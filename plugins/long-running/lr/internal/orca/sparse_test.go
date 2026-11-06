package orca

import (
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

func sparseRepo(t *testing.T, files map[string]string) string {
	t.Helper()
	repo, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	for name, text := range files {
		path := filepath.Join(repo, name)
		if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(text), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	for _, args := range [][]string{
		{"init", "-q"},
		{"add", "-A"},
		{"-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "commit", "-qm", "base"},
		{"sparse-checkout", "set", "--cone", "tools"},
	} {
		cmd := exec.Command("git", args...)
		cmd.Dir = repo
		if out, err := cmd.CombinedOutput(); err != nil {
			t.Fatalf("git %v: %v: %s", args, err, out)
		}
	}
	return repo
}

func TestSparseAddChecksOutAModuleAndEveryLocalReplaceTargetItReaches(t *testing.T) {
	repo := sparseRepo(t, map[string]string{
		"tools/t.sh":             "t\n",
		"go/app/cmd/x/main.go":   "package main\n",
		"go/app/internal/y/y.go": "package y\n",
		"go/app/go.mod":          "module app\n\nreplace lib => ../lib\n\nreplace gone => ../../../outside\n\nreplace pinned => example.com/pinned v1.0.0\n\nreplace fixed => /abs/fixed\n",
		"go/lib/go.mod":          "module lib\n\nreplace deep => ../deep\n",
		"go/deep/go.mod":         "module deep\n",
		"go/other/go.mod":        "module other\n",
	})
	t.Chdir(repo)
	var out bytes.Buffer
	cmd := SparseCommand(&app.App{Runner: shell.Exec{}})
	cmd.SetOut(&out)
	cmd.SetArgs([]string{"add", "go/app/cmd/x/"})
	if err := cmd.Execute(); err != nil {
		t.Fatal(err)
	}
	for _, module := range []string{"app", "lib", "deep"} {
		if !exists(filepath.Join(repo, "go", module, "go.mod")) {
			t.Fatalf("go/%s is not checked out", module)
		}
	}
	if !exists(filepath.Join(repo, "go", "app", "internal", "y", "y.go")) {
		t.Fatal("a package beside the named directory is not checked out")
	}
	if exists(filepath.Join(repo, "go", "other")) {
		t.Fatal("a module nothing replaces was checked out")
	}
	if got, want := strings.TrimSpace(out.String()), "sparse: added go/app go/lib go/deep"; got != want {
		t.Fatalf("got %q, want %q", got, want)
	}
}

func TestSparseAddOfADirectoryWithNoGoModuleAddsOnlyThatDirectory(t *testing.T) {
	repo := sparseRepo(t, map[string]string{"tools/t.sh": "t\n", "docs/a.md": "a\n", "api/b.md": "b\n"})
	t.Chdir(filepath.Join(repo, "tools"))
	cmd := SparseCommand(&app.App{Runner: shell.Exec{}})
	cmd.SetOut(&bytes.Buffer{})
	cmd.SetArgs([]string{"add", "docs"})
	if err := cmd.Execute(); err != nil {
		t.Fatal(err)
	}
	if !exists(filepath.Join(repo, "docs", "a.md")) || exists(filepath.Join(repo, "api")) {
		t.Fatal("the sparse set is wrong")
	}
}

func TestSparseAddOnAFullCheckoutSucceedsAndSaysTheCheckoutHoldsIt(t *testing.T) {
	repo := sparseRepo(t, map[string]string{"tools/t.sh": "t\n", "infra/main.tf": "x\n"})
	disable := exec.Command("git", "sparse-checkout", "disable")
	disable.Dir = repo
	if out, err := disable.CombinedOutput(); err != nil {
		t.Fatalf("git sparse-checkout disable: %v: %s", err, out)
	}
	t.Chdir(repo)
	var out bytes.Buffer
	cmd := SparseCommand(&app.App{Runner: shell.Exec{}})
	cmd.SetOut(&out)
	cmd.SetArgs([]string{"add", "infra"})
	if err := cmd.Execute(); err != nil {
		t.Fatal(err)
	}
	if got, want := strings.TrimSpace(out.String()), "sparse: a full checkout already holds infra"; got != want {
		t.Fatalf("got %q, want %q", got, want)
	}
	if !exists(filepath.Join(repo, "infra", "main.tf")) {
		t.Fatal("the full checkout lost infra")
	}
}

func TestSparseAddRestoresTheLaneDirectoriesAHandMadeConeLeftOut(t *testing.T) {
	repo := sparseRepo(t, map[string]string{
		"tools/t.sh": "t\n",
		"docs/a.md":  "a\n",
		".agents/skills/submit-pr/scripts/pr-reviews": "r\n",
		".claude/settings.json":                       "{}\n",
		"infra/ci/hooks/h.sh":                         "h\n",
		"infra/rows/r.json":                           "{}\n",
	})
	include := exec.Command("git", "config", "--add", "ccx.sparseInclude", "infra/ci/hooks")
	include.Dir = repo
	if out, err := include.CombinedOutput(); err != nil {
		t.Fatalf("git config: %v: %s", err, out)
	}
	t.Chdir(repo)
	for _, want := range []string{"sparse: added docs and the lane directories .agents .claude infra/ci/hooks", "sparse: added docs"} {
		var out bytes.Buffer
		cmd := SparseCommand(&app.App{Runner: shell.Exec{}})
		cmd.SetOut(&out)
		cmd.SetArgs([]string{"add", "docs"})
		if err := cmd.Execute(); err != nil {
			t.Fatal(err)
		}
		if got := strings.TrimSpace(out.String()); got != want {
			t.Fatalf("got %q, want %q", got, want)
		}
	}
	for _, path := range []string{".agents/skills/submit-pr/scripts/pr-reviews", ".claude/settings.json", "infra/ci/hooks/h.sh", "docs/a.md"} {
		if !exists(filepath.Join(repo, path)) {
			t.Fatalf("%s is not checked out", path)
		}
	}
	if exists(filepath.Join(repo, "infra", "rows")) {
		t.Fatal("a directory outside the lane set was checked out")
	}
}
