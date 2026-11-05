package landing_test

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/landing"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const sprite = "vet4"

const chain = `[{"number":1,"headRefName":"a","baseRefName":"dev","headRefOid":"a1"},{"number":2,"headRefName":"b","baseRefName":"a","headRefOid":"b2"}]`

type host struct {
	open        string
	sprited     int
	spriteFails shell.Result
	sawStdin    string
}

func (h *host) Run(ctx context.Context, c shell.Command) (shell.Result, error) {
	switch {
	case slices.Equal(c.Argv[:2], []string{"gh", "auth"}):
		return shell.Result{Stdout: "token\n"}, nil
	case slices.Equal(c.Argv[:3], []string{"gh", "pr", "list"}):
		if h.open == "" {
			return shell.Result{Stdout: chain}, nil
		}
		return shell.Result{Stdout: h.open}, nil
	case slices.Equal(c.Argv[:2], []string{"git", "config"}):
		return shell.Result{Stdout: map[string]string{"user.name": "Lane\n", "user.email": "lane@example.com\n"}[c.Argv[2]]}, nil
	case c.Argv[0] == "sprite":
		h.sprited++
		if h.spriteFails.Code != 0 {
			return h.spriteFails, &shell.ExitError{Argv: c.Argv, Code: h.spriteFails.Code, Stderr: h.spriteFails.Stderr}
		}
		c.Argv = c.Argv[slices.Index(c.Argv, "--")+1:]
	}
	h.sawStdin = c.Stdin
	return shell.Exec{}.Run(ctx, c)
}

func git(t *testing.T, repo string, args ...string) string {
	t.Helper()
	out, err := exec.Command("git", append([]string{"-C", repo}, args...)...).CombinedOutput()
	if err != nil {
		t.Fatalf("git %v: %v\n%s", args, err, out)
	}
	return strings.TrimSpace(string(out))
}

func commit(t *testing.T, repo, name, text string) {
	t.Helper()
	if err := os.WriteFile(filepath.Join(repo, name), []byte(text), 0o600); err != nil {
		t.Fatal(err)
	}
	git(t, repo, "add", name)
	git(t, repo, "commit", "-q", "-m", name+": "+strings.TrimSpace(text))
}

func stacked(t *testing.T, conflicting bool) (origin, clone string) {
	t.Helper()
	root := t.TempDir()
	config := filepath.Join(root, "gitconfig")
	if err := os.WriteFile(config, []byte("[user]\n\tname = Lane\n\temail = lane@example.com\n[init]\n\tdefaultBranch = dev\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("GIT_CONFIG_GLOBAL", config)
	t.Setenv("GIT_CONFIG_NOSYSTEM", "1")
	origin, clone = filepath.Join(root, "origin.git"), filepath.Join(root, "clone")
	work := filepath.Join(root, "work")
	git(t, root, "init", "-q", "--bare", origin)
	git(t, root, "clone", "-q", origin, work)
	commit(t, work, "dev.txt", "one\n")
	git(t, work, "push", "-q", "origin", "HEAD:dev")
	git(t, work, "checkout", "-q", "-b", "a")
	name := "a.txt"
	if conflicting {
		name = "dev.txt"
	}
	commit(t, work, name, "a\n")
	git(t, work, "checkout", "-q", "-b", "b")
	commit(t, work, "b.txt", "b\n")
	git(t, work, "push", "-q", "origin", "a", "b")
	git(t, work, "checkout", "-q", "dev")
	commit(t, work, "dev.txt", "two\n")
	git(t, work, "push", "-q", "origin", "dev")
	git(t, root, "clone", "-q", origin, clone)
	return origin, clone
}

type heads struct{ texts []string }

func (h *heads) post(_ context.Context, number int, text string) error {
	h.texts = append(h.texts, fmt.Sprintf("%d %s", number, text))
	return nil
}

func TestARemoteRestackReplaysTheChainOntoItsBaseOnTheSpriteAndPostsEachNewHead(t *testing.T) {
	origin, clone := stacked(t, false)
	wasA, wasB := git(t, origin, "rev-parse", "a"), git(t, origin, "rev-parse", "b")
	posted, machine := &heads{}, &host{}
	remote := landing.Remote{Runner: machine, PostHead: posted.post, Repo: "o/r", Clone: clone, Sprite: sprite}

	moved, err := remote.Restack(context.Background(), 2, wasB, "a")
	if err != nil {
		t.Fatal(err)
	}

	dev, a, b := git(t, origin, "rev-parse", "dev"), git(t, origin, "rev-parse", "a"), git(t, origin, "rev-parse", "b")
	if moved != "moved" || machine.sprited != 1 {
		t.Fatalf("moved = %q, sprited = %d", moved, machine.sprited)
	}
	if git(t, origin, "rev-parse", "b~1") != a || git(t, origin, "rev-parse", "a~1") != dev {
		t.Fatalf("the chain did not move onto dev: dev=%s a=%s b=%s", dev, a, b)
	}
	want := []string{
		fmt.Sprintf("1 HEAD #1 %s: restacked onto dev %s on vet4 by remote-restack (was %s)", a, dev[:10], wasA[:10]),
		fmt.Sprintf("2 HEAD #2 %s: restacked onto dev %s on vet4 by remote-restack (was %s)", b, dev[:10], wasB[:10]),
	}
	if !slices.Equal(posted.texts, want) {
		t.Fatalf("posted = %q, want %q", posted.texts, want)
	}
	if !strings.HasPrefix(machine.sawStdin, "token\nLane\nlane@example.com\n") {
		t.Fatalf("stdin = %q", machine.sawStdin)
	}
}

func TestAConflictPushesNothingAndNamesTheBranchAndPaths(t *testing.T) {
	origin, clone := stacked(t, true)
	wasA, wasB := git(t, origin, "rev-parse", "a"), git(t, origin, "rev-parse", "b")
	posted := &heads{}
	remote := landing.Remote{Runner: &host{}, PostHead: posted.post, Repo: "o/r", Clone: clone, Sprite: sprite}

	stdout, err := remote.Run(context.Background(), 2)

	if !errors.Is(err, landing.ErrConflict) {
		t.Fatalf("err = %v", err)
	}
	if !strings.Contains(stdout, "CONFLICT replaying a onto dev at ") || !strings.Contains(stdout, "\n  dev.txt\n") || !strings.Contains(stdout, "CONFLICT (content): Merge conflict in dev.txt") {
		t.Fatalf("stdout = %q", stdout)
	}
	if git(t, origin, "rev-parse", "a") != wasA || git(t, origin, "rev-parse", "b") != wasB || len(posted.texts) != 0 {
		t.Fatal("a conflict moved a head or posted one")
	}
}

func TestABranchOnDevHoldingAnOpenParentsHeadPushesNothingAndNamesTheParent(t *testing.T) {
	origin, clone := stacked(t, false)
	wasA, wasB := git(t, origin, "rev-parse", "a"), git(t, origin, "rev-parse", "b")
	posted := &heads{}
	machine := &host{open: fmt.Sprintf(`[{"number":1,"headRefName":"a","baseRefName":"dev","headRefOid":%q},{"number":2,"headRefName":"b","baseRefName":"dev","headRefOid":%q}]`, wasA, wasB)}
	remote := landing.Remote{Runner: machine, PostHead: posted.post, Repo: "o/r", Clone: clone, Sprite: sprite}

	moved, err := remote.Restack(context.Background(), 2, wasB, "dev")

	if !errors.Is(err, landing.ErrCarries) || moved != "" || !strings.Contains(err.Error(), "its branch holds the heads of #1, open PRs outside its stack") {
		t.Fatalf("moved = %q, err = %v", moved, err)
	}
	if git(t, origin, "rev-parse", "a") != wasA || git(t, origin, "rev-parse", "b") != wasB || len(posted.texts) != 0 {
		t.Fatal("a branch carrying an open parent was moved onto dev")
	}
}

func TestAConflictTellsTheMoverOnlyALaneCanResolveIt(t *testing.T) {
	_, clone := stacked(t, true)
	remote := landing.Remote{Runner: &host{}, PostHead: (&heads{}).post, Repo: "o/r", Clone: clone, Sprite: sprite}

	moved, err := remote.Restack(context.Background(), 2, head, "a")

	if err != nil || moved != "" {
		t.Fatalf("moved = %q, err = %v", moved, err)
	}
}

func TestASpriteThatCannotRunIsAFailureTheNextPassRetriesNotAConflict(t *testing.T) {
	machine := &host{spriteFails: shell.Result{Code: 1, Stderr: "Error: no organizations configured\n"}}
	remote := landing.Remote{Runner: machine, PostHead: (&heads{}).post, Repo: "o/r", Clone: "/clone", Sprite: sprite}

	moved, err := remote.Restack(context.Background(), 2, head, "a")

	if err == nil || errors.Is(err, landing.ErrConflict) || moved != "" {
		t.Fatalf("moved = %q, err = %v", moved, err)
	}
	if !strings.Contains(err.Error(), "#2 on vet4 pushed nothing: Error: no organizations configured") {
		t.Fatalf("err = %v", err)
	}
}

func TestARestackOnASpriteHostRunsInItsOwnCloneWithNoSpriteExec(t *testing.T) {
	origin, clone := stacked(t, false)
	wasB := git(t, origin, "rev-parse", "b")
	posted, machine := &heads{}, &host{}
	remote := landing.Remote{Runner: machine, PostHead: posted.post, Repo: "o/r", Clone: clone, Here: "dar-desk-1"}

	if _, err := remote.Run(context.Background(), 2); err != nil {
		t.Fatal(err)
	}

	if machine.sprited != 0 || git(t, origin, "rev-parse", "b") == wasB {
		t.Fatalf("sprited = %d", machine.sprited)
	}
	if len(posted.texts) != 2 || !strings.Contains(posted.texts[0], "on dar-desk-1 by remote-restack") {
		t.Fatalf("posted = %q", posted.texts)
	}
}
