package awake

import (
	"context"
	"io"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const workURL = "https://github.com/acme/widgets"

type pushes struct {
	carried []string
}

func (p *pushes) Run(ctx context.Context, c shell.Command) (shell.Result, error) {
	result, err := shell.Exec{}.Run(ctx, c)
	if said := strings.TrimSpace(result.Stdout); err == nil && said != "" {
		p.carried = append(p.carried, said)
	}
	return result, err
}

func gitIn(t *testing.T, dir string, args ...string) string {
	t.Helper()
	out, err := exec.Command("git", append([]string{"-C", dir}, args...)...).CombinedOutput()
	if err != nil {
		t.Fatalf("git %v: %v\n%s", args, err, out)
	}
	return strings.TrimSpace(string(out))
}

func write(t *testing.T, path, text string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o600); err != nil {
		t.Fatal(err)
	}
}

type laneBox struct {
	home, origin, checkout string
	work                   Work
	runner                 *pushes
}

func newLaneBox(t *testing.T, lane string) *laneBox {
	t.Helper()
	root := t.TempDir()
	b := &laneBox{home: filepath.Join(root, "home"), origin: filepath.Join(root, "widgets.git"), runner: &pushes{}}
	b.checkout = filepath.Join(b.home, "work", "widgets")
	t.Setenv("GIT_CONFIG_GLOBAL", filepath.Join(root, "gitconfig"))
	write(t, filepath.Join(root, "gitconfig"), "[init]\n\tdefaultBranch = main\n[url \""+b.origin+"\"]\n\tinsteadOf = "+workURL+"\n")
	gitIn(t, root, "init", "-q", "--bare", b.origin)
	gitIn(t, root, "init", "-q", b.checkout)
	gitIn(t, b.checkout, "remote", "add", "origin", workURL)
	write(t, filepath.Join(b.checkout, ".gitignore"), "secrets.env\n")
	write(t, filepath.Join(b.checkout, "README"), "widgets\n")
	gitIn(t, b.checkout, "add", "-A")
	gitIn(t, b.checkout, "-c", "user.name=Seed", "-c", "user.email=seed@example.com", "commit", "-q", "-m", "seed")
	gitIn(t, b.checkout, "push", "-q", "origin", "main")
	gitIn(t, b.checkout, "fetch", "-q", "origin")
	agent := filepath.Join(root, "proc", "4242")
	write(t, filepath.Join(agent, "comm"), "claude\n")
	environ := []string{"PATH=/usr/bin", "GIT_CONFIG_COUNT=2", "GIT_CONFIG_KEY_0=user.name", "GIT_CONFIG_VALUE_0=Lane", "GIT_CONFIG_KEY_1=user.email", "GIT_CONFIG_VALUE_1=lane@example.com"}
	if lane != "" {
		environ = append(environ, laneVariable+"="+lane)
	}
	write(t, filepath.Join(agent, "environ"), strings.Join(environ, "\x00")+"\x00")
	b.work = Work{Runner: b.runner, Proc: filepath.Join(root, "proc"), Home: b.home, Every: WorkEvery, Log: slog.New(slog.NewTextHandler(io.Discard, nil))}
	return b
}

func (b *laneBox) remoteRef(t *testing.T, ref string) string {
	t.Helper()
	return gitIn(t, b.checkout, "ls-remote", "origin", ref)
}

func TestALanesUnpushedCommitsAndUncommittedEditsReachItsWorkRefAndNothingElse(t *testing.T) {
	b := newLaneBox(t, "widgets-1")
	gitIn(t, b.checkout, "switch", "-q", "-c", "lane/widgets")
	write(t, filepath.Join(b.checkout, "README"), "widgets, committed\n")
	gitIn(t, b.checkout, "-c", "user.name=Lane", "-c", "user.email=lane@example.com", "commit", "-q", "-am", "unpushed")
	head := gitIn(t, b.checkout, "rev-parse", "HEAD")
	write(t, filepath.Join(b.checkout, "README"), "widgets, edited\n")
	write(t, filepath.Join(b.checkout, "new.go"), "package widgets\n")
	write(t, filepath.Join(b.checkout, "secrets.env"), "TOKEN=local-only\n")

	b.work.carry(t.Context())

	if len(b.runner.carried) != 1 {
		t.Fatalf("carried %q, want one push", b.runner.carried)
	}
	carried := b.runner.carried[0]
	if said := b.remoteRef(t, WorkRefs+"widgets-1"); !strings.HasPrefix(said, carried) {
		t.Fatalf("origin's %swidgets-1 = %q, want %s", WorkRefs, said, carried)
	}
	if parents := gitIn(t, b.checkout, "log", "-1", "--format=%P", carried); parents != head {
		t.Errorf("the work commit's parents = %q, want the lane's unpushed HEAD %s", parents, head)
	}
	files := strings.Fields(gitIn(t, b.checkout, "ls-tree", "-r", "--name-only", carried))
	if !slices.Equal(files, []string{".gitignore", "README", "new.go"}) {
		t.Errorf("the work tree holds %q, want the edits and the new file but not the ignored secrets.env", files)
	}
	if readme := gitIn(t, b.checkout, "show", carried+":README"); readme != "widgets, edited" {
		t.Errorf("README in the work commit = %q, want the uncommitted edit", readme)
	}
	if gitIn(t, b.checkout, "rev-parse", "HEAD") != head || gitIn(t, b.checkout, "diff", "--cached", "--name-only") != "" {
		t.Error("carrying the work moved the lane's branch or staged its edits")
	}
	if heads := b.remoteRef(t, "refs/heads/*"); strings.Contains(heads, "lane/widgets") {
		t.Errorf("origin's branches = %q, want the lane's branch left unpushed", heads)
	}
	recorded, err := os.ReadFile(filepath.Join(b.home, CheckoutsFile))
	if err != nil || strings.TrimSpace(string(recorded)) != b.checkout {
		t.Errorf("recorded checkouts = %q, %v; want %s", recorded, err, b.checkout)
	}

	b.work.carry(t.Context())
	if len(b.runner.carried) != 1 {
		t.Errorf("a second pass over unchanged work carried %q, want no push", b.runner.carried[1:])
	}
}

func TestACleanCheckoutAtItsOriginPushesNothing(t *testing.T) {
	b := newLaneBox(t, "widgets-1")

	b.work.carry(t.Context())

	if len(b.runner.carried) != 0 {
		t.Errorf("a clean checkout carried %q, want no push", b.runner.carried)
	}
	if said := b.remoteRef(t, WorkRefs+"*"); said != "" {
		t.Errorf("origin's work refs = %q, want none", said)
	}
}

func TestASpriteWithNoLaneAgentCarriesNothing(t *testing.T) {
	b := newLaneBox(t, "")
	write(t, filepath.Join(b.checkout, "README"), "widgets, edited\n")

	b.work.carry(t.Context())

	if len(b.runner.carried) != 0 {
		t.Errorf("carried %q with no lane agent running, want nothing", b.runner.carried)
	}
	if _, err := os.Stat(filepath.Join(b.home, CheckoutsFile)); !os.IsNotExist(err) {
		t.Errorf("recorded checkouts with no lane agent: %v", err)
	}
}

func TestCheckoutsFindsEachCheckoutUnderHomeAndClonesInsideOneButNoneUnderADotDirectoryOrTooDeep(t *testing.T) {
	home := t.TempDir()
	for _, dir := range []string{"monorepo/.git", "monorepo/nested/.git", "work/cc-skills/.git", ".cache/clone/.git", "a/b/c/deep/.git"} {
		if err := os.MkdirAll(filepath.Join(home, dir), 0o700); err != nil {
			t.Fatal(err)
		}
	}
	write(t, filepath.Join(home, "work", "linked", ".git"), "gitdir: /elsewhere\n")

	found, err := Checkouts(home)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{filepath.Join(home, "monorepo"), filepath.Join(home, "monorepo", "nested"), filepath.Join(home, "work", "cc-skills"), filepath.Join(home, "work", "linked")}
	if !slices.Equal(found, want) {
		t.Errorf("Checkouts = %q, want %q", found, want)
	}
}
