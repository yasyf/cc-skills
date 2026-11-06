package facts

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func TestLandingAndTheLanesSupervisorReadTheSameBranchsDiffWhileTheOtherHoldsItsRef(t *testing.T) {
	root := t.TempDir()
	gitconfig := filepath.Join(root, "gitconfig")
	if err := os.WriteFile(gitconfig, []byte("[user]\n\tname = Lane\n\temail = lane@example.com\n[init]\n\tdefaultBranch = dev\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("GIT_CONFIG_GLOBAL", gitconfig)
	t.Setenv("GIT_CONFIG_NOSYSTEM", "1")
	origin, work, shared := filepath.Join(root, "origin.git"), filepath.Join(root, "work"), filepath.Join(root, "shared")
	git(t, root, "init", "-q", "--bare", origin)
	git(t, root, "clone", "-q", origin, work)
	for _, step := range []struct{ file, branch string }{{"base", "dev"}, {"below", "feature"}, {"change", "pr"}} {
		if err := os.WriteFile(filepath.Join(work, step.file), []byte(step.file+"\n"), 0o600); err != nil {
			t.Fatal(err)
		}
		git(t, work, "add", step.file)
		git(t, work, "commit", "-q", "-m", step.file)
		git(t, work, "push", "-q", "origin", "HEAD:"+step.branch)
	}
	head := git(t, work, "rev-parse", "HEAD")
	want := gates.DiffHash(git(t, work, "diff", "--no-color", "--no-ext-diff", "HEAD~1", head) + "\n")
	git(t, root, "clone", "-q", origin, shared)
	landing := config.Config{Movers: []string{config.MoverLanding}}
	lanes := config.Config{Movers: []string{config.MoverLanes}}

	for _, writers := range []struct{ reader, holder config.Config }{{landing, lanes}, {lanes, landing}} {
		var held []string
		for _, branch := range []string{"feature", "dev"} {
			lock := filepath.Join(shared, ".git", writers.holder.Refs()+"diff", branch+".lock")
			if err := os.MkdirAll(filepath.Dir(lock), 0o750); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(lock, nil, 0o600); err != nil {
				t.Fatal(err)
			}
			held = append(held, lock)
		}
		s, err := store.Memory()
		if err != nil {
			t.Fatal(err)
		}
		source := Source{Repo: "o/r", Trunk: "dev", Refs: writers.reader.Refs(), Runner: fromCheckout{shell.Exec{}, shared, "o/r"}, Store: s, Now: time.Now}
		stored := map[string]string{}

		err = source.hashDiff(context.Background(), machine.Facts{Repo: "o/r", Number: 7, Head: head, Base: "feature"}, stored)

		if err != nil || stored[gates.DiffFact] != want {
			t.Fatalf("a %s reader beside a %s writer mid-fetch on the same branches: err = %v, diff = %q, want %q", writers.reader.Movers[0], writers.holder.Movers[0], err, stored[gates.DiffFact], want)
		}
		for _, lock := range held {
			if err := os.Remove(lock); err != nil {
				t.Fatal(err)
			}
		}
		if err := s.Close(); err != nil {
			t.Fatal(err)
		}
	}
}

func git(t *testing.T, dir string, args ...string) string {
	t.Helper()
	out, err := exec.Command("git", append([]string{"-C", dir}, args...)...).CombinedOutput()
	if err != nil {
		t.Fatalf("git %v: %v\n%s", args, err, out)
	}
	return strings.TrimSpace(string(out))
}
