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
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	clobberHead = "clobbers"
	clobberFact = "trunk-read"
	clobberKind = "report"
	commitMark  = "\x00"

	mergeConflicted = 1
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
	listed, err := c.git(ctx, gitLimit, "ls-remote", "--heads", "origin", "refs/heads/"+c.Trunk)
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
		c.Log.Warn("a squash's merges were not read against a clean merge", "squash", shortHead(squash), "err", err)
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
	merges, err := c.git(ctx, gitLimit, "rev-list", "--merges", head, "^"+parent)
	if err != nil || merges == "" {
		return nil, err
	}
	touched, err := c.git(ctx, gitLimit, "diff", "--name-only", "--no-renames", parent, squash)
	if err != nil {
		return nil, err
	}
	undid := map[int]*Undone{}
	for _, merge := range strings.Fields(merges) {
		if err := c.dropped(ctx, parent, merge, strings.Split(touched, "\n"), description, undid); err != nil {
			return nil, err
		}
	}
	if len(undid) == 0 {
		return nil, nil
	}
	clobber := Clobber{Number: number, Squash: squash}
	for _, undone := range undid {
		slices.Sort(undone.Files)
		clobber.Undid = append(clobber.Undid, *undone)
	}
	slices.SortFunc(clobber.Undid, func(a, b Undone) int { return a.Number - b.Number })
	return &clobber, nil
}

func (c *Clobbers) dropped(ctx context.Context, parent, merge string, touched []string, description string, undid map[int]*Undone) error {
	merged, err := c.Runner.Run(ctx, shell.Command{Argv: []string{"git", "-C", c.Checkout, "merge-tree", "--write-tree", "--name-only", "--no-messages", merge + "^1", merge + "^2"}, Timeout: gitLimit})
	if err != nil && merged.Code != mergeConflicted {
		return err
	}
	clean, conflicted, _ := strings.Cut(strings.TrimSpace(merged.Stdout), "\n")
	diff, err := c.git(ctx, gitLimit, slices.Concat([]string{"diff", "--no-renames", clean, merge, "--"}, touched)...)
	if err != nil || diff == "" {
		return err
	}
	restored, lost := parseDiff(diff)
	var files []string
	for file := range lost {
		if !slices.Contains(strings.Split(conflicted, "\n"), file) && !describes(description, file) {
			files = append(files, file)
		}
	}
	if len(files) == 0 {
		return nil
	}
	own, err := c.git(ctx, gitLimit, slices.Concat([]string{"log", "--no-merges", "--no-renames", "-p", "--format=", merge + "^1", "^" + parent, "--"}, files)...)
	if err != nil {
		return err
	}
	_, rewrote := parseDiff(own)
	for file, lines := range rewrote {
		for line := range lines {
			delete(lost[file], line)
		}
	}
	landed, err := c.git(ctx, gitLimit, slices.Concat([]string{"log", "--first-parent", "--no-renames", "-p", "--format=%x00%s", merge + "^2", "^" + merge + "^1", "--"}, files)...)
	if err != nil {
		return err
	}
	for _, landing := range strings.Split(landed, commitMark)[1:] {
		subject, landedDiff, _ := strings.Cut(landing, "\n")
		pr := squashedPR.FindStringSubmatch(subject)
		if pr == nil || regexp.MustCompile(`#`+pr[1]+`\b`).MatchString(description) {
			continue
		}
		number, _ := strconv.Atoi(pr[1])
		added, removed := parseDiff(landedDiff)
		for _, file := range files {
			count := shared(added[file], lost[file]) + shared(removed[file], restored[file])
			if count == 0 {
				continue
			}
			undone := undid[number]
			if undone == nil {
				undone = &Undone{Number: number}
				undid[number] = undone
			}
			if !slices.Contains(undone.Files, file) {
				undone.Files = append(undone.Files, file)
			}
			undone.Lines += count
		}
	}
	return nil
}

func shared(lines, of map[string]bool) int {
	count := 0
	for line := range lines {
		if of[line] {
			count++
		}
	}
	return count
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

func describes(description, file string) bool {
	return strings.Contains(description, file) || strings.Contains(description, file[strings.LastIndex(file, "/")+1:])
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
		undid = append(undid, fmt.Sprintf("#%d's %d lines in %s", undone.Number, undone.Lines, strings.Join(undone.Files, ", ")))
	}
	to := []string{rootLane}
	refs := make([]string, 0, len(numbers))
	for _, number := range numbers {
		refs = append(refs, fmt.Sprintf("%s#%d", c.Repo, number))
		holder, err := lanes.ChangeOwner(c.Store, c.Drive, laneOf[number], number)
		if err != nil {
			return err
		}
		if !slices.Contains(to, holder) {
			to = append(to, holder)
		}
	}
	text := fmt.Sprintf("CLOBBERED #%d at %s undid what landed since its base: a merge from trunk on its branch kept stale copies of files that merged cleanly, so its squash took out %s, and its description names none of it. Restore them forward", found.Number, shortHead(found.Squash), strings.Join(undid, "; "))
	_, err = client.Post(ctx, client.Draft{Drive: c.Drive, Lane: self, Kind: clobberKind, To: to, ID: "clobbered:" + found.Squash, PRs: refs, Text: text})
	return err
}

func (c *Clobbers) git(ctx context.Context, limit time.Duration, args ...string) (string, error) {
	result, err := c.Runner.Run(ctx, shell.Command{Argv: append([]string{"git", "-C", c.Checkout}, args...), Timeout: limit})
	return strings.TrimSpace(result.Stdout), err
}
