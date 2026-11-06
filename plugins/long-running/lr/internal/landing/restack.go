package landing

import (
	"context"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"slices"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	movedValue   = "moved"
	movedLine    = "MOVED"
	baseLine     = "BASE"
	conflictLine = "CONFLICT"
	replayLine   = "REPLAY-FAILED"
	carriesLine  = "CARRIES"
	leftLine     = "LEFT"
	openLimit    = "10000"
	shortSHA     = 10
	detailBytes  = 400
	pullFields   = "number,headRefName,baseRefName,headRefOid"
)

//go:embed restack.sh
var restackScript string

var (
	ErrConflict = errors.New("only a lane can resolve this restack")
	ErrCarries  = errors.New("only a lane can put it back on its real base")
)

type Remote struct {
	Runner   shell.Runner
	PostHead func(ctx context.Context, number int, text string) error
	Repo     string
	Clone    string
	Sprite   string
	Here     string
}

type pull struct {
	Number int    `json:"number"`
	Head   string `json:"headRefName"`
	Base   string `json:"baseRefName"`
	Oid    string `json:"headRefOid"`
}

func (r Remote) Restack(ctx context.Context, number int, _, _ string) (string, error) {
	_, err := r.Run(ctx, number)
	if errors.Is(err, ErrConflict) {
		return "", nil
	}
	if err != nil {
		return "", err
	}
	return movedValue, nil
}

func (r Remote) Run(ctx context.Context, number int) (string, error) {
	var open []pull
	if err := r.read(ctx, &open, "gh", "pr", "list", "--repo", r.Repo, "--state", "open", "--limit", openLimit, "--json", pullFields); err != nil {
		return "", err
	}
	at := slices.IndexFunc(open, func(p pull) bool { return p.Number == number })
	if at < 0 {
		return "", fmt.Errorf("remote-restack: #%d is not an open pull request", number)
	}
	byBranch := make(map[string]pull, len(open))
	children := make(map[string][]pull, len(open))
	for _, p := range open {
		byBranch[p.Head] = p
		children[p.Base] = append(children[p.Base], p)
	}
	line := []pull{open[at]}
	for below, found := byBranch[line[0].Base]; found; below, found = byBranch[line[0].Base] {
		line = slices.Insert(line, 0, below)
	}
	credentials, err := r.credentials(ctx)
	if err != nil {
		return "", err
	}
	stdout, err := r.replay(ctx, number, credentials, open, line[0].Base, "", line)
	if err != nil {
		return stdout, err
	}
	posted := []error{r.postHeads(ctx, stdout, line[0].Base, line)}
	var out strings.Builder
	out.WriteString(stdout)
	for _, moved := range movedHeads(stdout) {
		for _, child := range children[moved.branch] {
			if slices.ContainsFunc(line, func(p pull) bool { return p.Number == child.Number }) {
				continue
			}
			subtree := []pull{child}
			for at := 0; at < len(subtree); at++ {
				subtree = append(subtree, children[subtree[at].Head]...)
			}
			replayed, err := r.replay(ctx, child.Number, credentials, open, moved.branch, moved.old, subtree)
			out.WriteString(replayed)
			if err == nil {
				posted = append(posted, r.postHeads(ctx, replayed, moved.branch, subtree))
				continue
			}
			why := firstLine(err.Error())
			if errors.Is(err, ErrConflict) {
				why = firstLine(replayed)
			}
			for _, p := range subtree {
				fmt.Fprintf(&out, "%s %s %s\n", leftLine, p.Head, why)
			}
		}
	}
	return out.String(), errors.Join(posted...)
}

type movedHead struct{ branch, old, moved string }

func movedHeads(stdout string) []movedHead {
	var heads []movedHead
	for line := range strings.Lines(stdout) {
		if fields := strings.Fields(line); len(fields) == 4 && fields[0] == movedLine {
			heads = append(heads, movedHead{branch: fields[1], old: fields[2], moved: fields[3]})
		}
	}
	return heads
}

func (r Remote) replay(ctx context.Context, number int, credentials string, open []pull, base, upstream string, pulls []pull) (string, error) {
	outside := make(map[string]int, len(open))
	for _, p := range open {
		outside[p.Oid] = p.Number
	}
	branches := make([]string, 0, len(pulls))
	for _, p := range pulls {
		delete(outside, p.Oid)
		branches = append(branches, p.Head)
	}
	var stdin strings.Builder
	stdin.WriteString(credentials)
	for oid := range outside {
		stdin.WriteString(oid + "\n")
	}
	done, err := r.Runner.Run(ctx, shell.Command{
		Argv:    slices.Concat(r.prefix(), []string{"sh", "-c", restackScript, "sh", r.Clone, base, upstream}, branches),
		Stdin:   stdin.String(),
		Timeout: passTimeout,
	})
	if err != nil {
		return done.Stdout, r.failure(number, done, outside)
	}
	return done.Stdout, nil
}

func (r Remote) read(ctx context.Context, into any, argv ...string) error {
	done, err := r.Runner.Run(ctx, shell.Command{Argv: argv, Timeout: readTimeout})
	if err != nil {
		return err
	}
	return json.Unmarshal([]byte(done.Stdout), into)
}

func (r Remote) credentials(ctx context.Context) (string, error) {
	var lines strings.Builder
	for _, argv := range [][]string{
		{"gh", "auth", "token", "--hostname", "github.com"},
		{"git", "config", "user.name"},
		{"git", "config", "user.email"},
	} {
		done, err := r.Runner.Run(ctx, shell.Command{Argv: argv, Timeout: readTimeout})
		if err != nil {
			return "", err
		}
		lines.WriteString(strings.TrimSpace(done.Stdout) + "\n")
	}
	return lines.String(), nil
}

func (r Remote) prefix() []string {
	if r.Sprite == "" {
		return nil
	}
	return []string{"sprite", "exec", "-s", r.Sprite, "--"}
}

func (r Remote) where() string {
	if r.Sprite == "" {
		return r.Here
	}
	return r.Sprite
}

func (r Remote) failure(number int, done shell.Result, outside map[string]int) error {
	summary := fmt.Sprintf("remote-restack: #%d on %s pushed nothing", number, r.where())
	var carried []int
	for line := range strings.Lines(done.Stdout) {
		if oid, found := strings.CutPrefix(strings.TrimSpace(line), carriesLine+" "); found {
			carried = append(carried, outside[oid])
		}
	}
	if len(carried) > 0 {
		slices.Sort(carried)
		return fmt.Errorf("%s: its branch holds the heads of %s, open PRs outside its stack, so %w", summary, numbered(slices.Compact(carried)), ErrCarries)
	}
	if strings.Contains("\n"+done.Stdout, "\n"+conflictLine+" ") || strings.Contains("\n"+done.Stdout, "\n"+replayLine+" ") {
		return fmt.Errorf("%s: %w", summary, ErrConflict)
	}
	detail := strings.TrimSpace(done.Stderr)
	if done.Code == shell.CodeTimedOut {
		detail = fmt.Sprintf("timed out after %ds", int(passTimeout.Seconds()))
	}
	if len(detail) > detailBytes {
		detail = detail[len(detail)-detailBytes:]
	}
	if detail == "" {
		detail = fmt.Sprintf("the sprite printed nothing and exited %d", done.Code)
	}
	return fmt.Errorf("%s: %s", summary, detail)
}

func (r Remote) postHeads(ctx context.Context, stdout, base string, pulls []pull) error {
	var tip string
	for line := range strings.Lines(stdout) {
		if fields := strings.Fields(line); len(fields) == 3 && fields[0] == baseLine {
			tip = fields[2]
		}
	}
	for _, moved := range movedHeads(stdout) {
		number := pulls[slices.IndexFunc(pulls, func(p pull) bool { return p.Head == moved.branch })].Number
		text := fmt.Sprintf("HEAD #%d %s: restacked onto %s %s on %s by remote-restack (was %s)", number, moved.moved, base, tip[:shortSHA], r.where(), moved.old[:shortSHA])
		if err := r.PostHead(ctx, number, text); err != nil {
			return err
		}
	}
	return nil
}
