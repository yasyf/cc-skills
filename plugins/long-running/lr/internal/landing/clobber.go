package landing

import (
	"context"
	"fmt"
	"log/slog"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"
	"unicode"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	clobberHead = "clobbers"
	clobberFact = "trunk-read"
	clobberKind = "report"
	commitMark  = "\x00"
)

type Clobbers struct {
	Runner   shell.Runner
	Store    *store.Store
	Drive    string
	Repo     string
	Trunk    string
	Checkout string
	Now      func() time.Time
	Log      *slog.Logger
}

type Clobber struct {
	Number int
	Squash string
	Undid  []Undone
}

type Undone struct {
	Number int
	Files  []string
	Lines  int
}

type fileLines map[string]map[string]bool

func (c *Clobbers) Pass(ctx context.Context) error {
	facts, err := c.Store.Facts(c.Repo, 0, clobberHead)
	if err != nil {
		return err
	}
	tip, landed, err := c.landed(ctx, facts[clobberFact])
	if err != nil {
		c.Log.Warn("the trunk was not read for squashes that undid a landing", "err", err)
		return nil
	}
	for _, squash := range landed {
		c.replay(ctx, squash)
	}
	_, err = c.Store.PutFact(c.Repo, 0, clobberHead, clobberFact, tip, c.Now())
	return err
}

func (c *Clobbers) landed(ctx context.Context, read string) (string, []string, error) {
	listed, err := c.git(ctx, gitLimit, "ls-remote", "origin", "refs/heads/"+c.Trunk)
	if err != nil {
		return "", nil, err
	}
	tip, _, _ := strings.Cut(listed, "\t")
	if read == "" || read == tip {
		return tip, nil, nil
	}
	if _, err := c.git(ctx, trunkReadLimit, "fetch", "-q", "--no-tags", "origin", tip, "--no-write-fetch-head"); err != nil {
		return "", nil, err
	}
	landed, err := c.git(ctx, gitLimit, "rev-list", "--reverse", "--first-parent", read+".."+tip)
	return tip, strings.Fields(landed), err
}

func (c *Clobbers) replay(ctx context.Context, squash string) []Undone {
	found, err := c.check(ctx, squash)
	if err == nil && found != nil {
		err = c.tell(ctx, *found)
	}
	if err != nil {
		c.Log.Warn("a squash was not read against the PRs landed since its base", "squash", shortHead(squash), "err", err)
	}
	if found == nil {
		return nil
	}
	return found.Undid
}

func (c *Clobbers) check(ctx context.Context, squash string) (*Clobber, error) {
	message, err := c.git(ctx, gitLimit, "log", "-1", "--format=%P%n%B", squash)
	if err != nil {
		return nil, err
	}
	parent, message, _ := strings.Cut(message, "\n")
	subject, description, _ := strings.Cut(message, "\n")
	named := squashedPR.FindStringSubmatch(subject)
	if named == nil {
		return nil, nil
	}
	number, _ := strconv.Atoi(named[1])
	listed, err := c.git(ctx, gitLimit, "ls-remote", "origin", fmt.Sprintf("refs/pull/%d/head", number))
	if err != nil {
		return nil, err
	}
	head, _, _ := strings.Cut(listed, "\t")
	if head == "" {
		return nil, fmt.Errorf("origin has no head for #%d", number)
	}
	if _, err := c.git(ctx, trunkReadLimit, "fetch", "-q", "--no-tags", "origin", head, "--no-write-fetch-head"); err != nil {
		return nil, err
	}
	own, err := c.git(ctx, gitLimit, "rev-list", "--first-parent", head, "^"+parent)
	if err != nil {
		return nil, err
	}
	chain := strings.Fields(own)
	if len(chain) == 0 {
		return nil, nil
	}
	base, err := c.git(ctx, gitLimit, "rev-parse", chain[len(chain)-1]+"^1")
	if err != nil {
		return nil, err
	}
	diff, err := c.git(ctx, gitLimit, "diff", "--no-renames", parent, squash)
	if err != nil {
		return nil, err
	}
	added, removed := parseDiff(diff)
	if len(removed) == 0 {
		return nil, nil
	}
	files := make([]string, 0, len(removed))
	for file := range removed {
		files = append(files, file)
	}
	since, err := c.git(ctx, trunkReadLimit, slices.Concat([]string{"log", "--first-parent", "--no-renames", "-p", "--format=%x00%s", base + ".." + parent, "--"}, files)...)
	if err != nil {
		return nil, err
	}
	clobber := Clobber{Number: number, Squash: squash}
	for _, landing := range strings.Split(since, commitMark)[1:] {
		landedSubject, landedDiff, _ := strings.Cut(landing, "\n")
		landedNumber := squashedPR.FindStringSubmatch(landedSubject)
		if landedNumber == nil || regexp.MustCompile(`#`+landedNumber[1]+`\b`).MatchString(description) {
			continue
		}
		undone := Undone{}
		undone.Number, _ = strconv.Atoi(landedNumber[1])
		landedAdded, landedRemoved := parseDiff(landedDiff)
		for file, lines := range landedAdded {
			if within(lines, removed[file]) && within(landedRemoved[file], added[file]) && !describes(description, file) {
				undone.Files = append(undone.Files, file)
				undone.Lines += len(lines)
			}
		}
		if len(undone.Files) > 0 {
			slices.Sort(undone.Files)
			clobber.Undid = append(clobber.Undid, undone)
		}
	}
	if len(clobber.Undid) == 0 {
		return nil, nil
	}
	return &clobber, nil
}

func within(lines, of map[string]bool) bool {
	for line := range lines {
		if !of[line] {
			return false
		}
	}
	return true
}

func describes(description, file string) bool {
	return strings.Contains(description, file) || strings.Contains(description, file[strings.LastIndex(file, "/")+1:])
}

func parseDiff(diff string) (added, removed fileLines) {
	added, removed = fileLines{}, fileLines{}
	var file string
	inHunk := false
	for line := range strings.SplitSeq(diff, "\n") {
		switch {
		case strings.HasPrefix(line, "diff --git "):
			_, file, _ = strings.Cut(line, " b/")
			inHunk = false
		case strings.HasPrefix(line, "@@"):
			inHunk = true
		case !inHunk || line == "":
		case line[0] == '+':
			added.mark(file, line[1:])
		case line[0] == '-':
			removed.mark(file, line[1:])
		}
	}
	return added, removed
}

func (f fileLines) mark(file, line string) {
	line = strings.TrimSpace(line)
	if !strings.ContainsFunc(line, func(r rune) bool { return unicode.IsLetter(r) || unicode.IsDigit(r) }) {
		return
	}
	if f[file] == nil {
		f[file] = map[string]bool{}
	}
	f[file][line] = true
}

func (c *Clobbers) tell(ctx context.Context, found Clobber) error {
	prs, err := c.Store.PRs(c.Repo)
	if err != nil {
		return err
	}
	laneOf := make(map[int]string, len(prs))
	for _, pr := range prs {
		laneOf[pr.Number] = pr.Lane
	}
	numbers := []int{found.Number}
	undid := make([]string, 0, len(found.Undid))
	for _, undone := range found.Undid {
		numbers = append(numbers, undone.Number)
		undid = append(undid, fmt.Sprintf("%d lines #%d added, in %s", undone.Lines, undone.Number, strings.Join(undone.Files, ", ")))
	}
	to := []string{rootLane}
	refs := make([]string, 0, len(numbers))
	for _, number := range numbers {
		refs = append(refs, fmt.Sprintf("%s#%d", c.Repo, number))
		holder, err := laneHolder(c.Store, c.Drive, laneOf[number])
		if err != nil {
			return err
		}
		if !slices.Contains(to, holder) {
			to = append(to, holder)
		}
	}
	text := fmt.Sprintf("CLOBBERED #%d at %s removed what PRs landed since its base added, and its description names none of it: %s. The squash put those lines back as they were before those PRs; restore them forward", found.Number, shortHead(found.Squash), strings.Join(undid, "; "))
	_, err = client.Post(ctx, client.Draft{Drive: c.Drive, Lane: self, Kind: clobberKind, To: to, ID: "clobbered:" + found.Squash, PRs: refs, Text: text})
	return err
}

func (c *Clobbers) git(ctx context.Context, limit time.Duration, args ...string) (string, error) {
	result, err := c.Runner.Run(ctx, shell.Command{Argv: append([]string{"git", "-C", c.Checkout}, args...), Timeout: limit})
	return strings.TrimSpace(result.Stdout), err
}
