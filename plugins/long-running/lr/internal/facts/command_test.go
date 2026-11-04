package facts_test

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"testing"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/facts"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func lrFacts(t *testing.T, args ...string) []machine.Facts {
	t.Helper()
	dir := t.TempDir()
	cfg := filepath.Join(dir, "lr.json")
	if err := os.WriteFile(cfg, []byte(`{"repo":"org/repo","drive":"d","checkout":"/checkouts/repo","author":"drive-bot","label":"drive","movers":["lanes"]}`), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, draft := range []client.Draft{
		{Drive: "d", Lane: "lane-7", Kind: "opened", Text: "opened", PRs: []string{"org/repo#1"}, Attrs: map[string]string{"repo": "org/repo", "head": head}},
		{Drive: "d", Lane: "supervisor", Kind: "github", Text: "the title", PRs: []string{"org/repo#1"}, Attrs: map[string]string{"record": "pr.read", "repo": "org/repo", "head": head, "state": "open", "ci": "green"}},
	} {
		if _, err := client.Post(context.Background(), draft); err != nil {
			t.Fatal(err)
		}
	}
	runner := &shelltest.Fake{}
	a := app.New(io.Discard)
	a.Runner, a.ConfigPath = runner, cfg
	var out bytes.Buffer
	cmd := facts.Command(a)
	cmd.SetOut(&out)
	cmd.SetArgs(args)
	if err := cmd.Execute(); err != nil {
		t.Fatal(err)
	}
	var rows []machine.Facts
	lines := bufio.NewScanner(&out)
	for lines.Scan() {
		var row machine.Facts
		if err := json.Unmarshal(lines.Bytes(), &row); err != nil {
			t.Fatal(err)
		}
		rows = append(rows, row)
	}
	return rows
}

func TestLrFactsPrintsOneLinePerPullRequest(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	post(t, "finder-desk", bus.Grade{PR: 1, Head: head, Grader: "finder", Verdict: "clear"}, "supervisor")

	rows := lrFacts(t)

	if len(rows) != 1 || rows[0].Number != 1 || rows[0].Finder != machine.FinderClear || rows[0].CI != machine.CISuccess {
		t.Fatalf("rows = %+v", rows)
	}
}

func TestLrFactsWritesOnlyTheStoreItIsNamed(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	post(t, "finder-desk", bus.Grade{PR: 1, Head: head, Grader: "finder", Verdict: "clear"}, "supervisor")
	path := filepath.Join(t.TempDir(), "lr.sqlite")

	lrFacts(t, "--store", path)

	s, err := store.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = s.Close() }()
	stored, err := s.Facts(repo, 1, head)
	if err != nil || stored["finder"] != "clear" {
		t.Fatalf("stored = %+v, %v", stored, err)
	}
}
