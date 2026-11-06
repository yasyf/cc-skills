package dashboard

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const liveURL = "http://100.113.56.52:61210/"

func write(t *testing.T, path, body string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
}

func TestTreeIsGitsTreeOfTheServedFilesWithoutBytecodeOrEmptyDirectories(t *testing.T) {
	dir := t.TempDir()
	write(t, filepath.Join(dir, "app", "server.py"), "a\n")
	write(t, filepath.Join(dir, "app", "__pycache__", "s.pyc"), "x")
	write(t, filepath.Join(dir, "app.css"), "body{}\n")
	write(t, filepath.Join(dir, "app.js"), "b\n")
	if err := os.Chmod(filepath.Join(dir, "app.js"), 0o700); err != nil { //nolint:gosec // the tree has to hold an executable file
		t.Fatal(err)
	}
	if err := os.Mkdir(filepath.Join(dir, "empty"), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink("app/server.py", filepath.Join(dir, "link")); err != nil {
		t.Fatal(err)
	}

	got, err := Tree(dir)
	if err != nil {
		t.Fatal(err)
	}
	if got != "c52b93f32966ffe3850905d27c738251ac1b786f" {
		t.Fatalf("tree %s", got)
	}
}

type proofCase struct {
	t     *testing.T
	s     Service
	tree  string
	shots string
}

func newProofCase(t *testing.T) proofCase {
	shots := t.TempDir()
	for _, name := range []string{"overview-desktop.png", "overview-phone.png"} {
		write(t, filepath.Join(shots, name), "png")
	}
	return proofCase{t: t, s: service(t), tree: strings.Repeat("ab", 20), shots: shots}
}

func (c proofCase) proof(edit func(*Proof)) string {
	p := Proof{
		Commit: strings.Repeat("c", 40),
		Tree:   c.tree,
		Served: "http://127.0.0.1:61299/",
		Pages: []Page{{
			Page:    "Review",
			Desktop: filepath.Join(c.shots, "overview-desktop.png"),
			Phone:   filepath.Join(c.shots, "overview-phone.png"),
			Answers: []Answer{{ID: "m420", Shows: "PRs only, with no section of questions"}},
		}},
	}
	edit(&p)
	body, err := json.Marshal(p)
	if err != nil {
		c.t.Fatal(err)
	}
	path := filepath.Join(c.t.TempDir(), "proof.json")
	write(c.t, path, string(body))
	return path
}

func TestARestartOfTheServedDashboardOrOfADownOneNeedsNoProof(t *testing.T) {
	c := newProofCase(t)
	write(t, c.s.served(), c.tree)

	if err := c.s.Clear(c.tree, liveURL, true, "", nil); err != nil {
		t.Fatalf("unchanged: %v", err)
	}
	if err := c.s.Clear("ff"+c.tree[2:], liveURL, false, "", nil); err != nil {
		t.Fatalf("down: %v", err)
	}
}

func TestAChangedDashboardWithNoProofIsRefusedNamingTheTreeAndTheFlag(t *testing.T) {
	c := newProofCase(t)
	write(t, c.s.served(), "ff"+c.tree[2:])

	err := c.s.Clear(c.tree, liveURL, true, "", nil)

	if err == nil || !strings.Contains(err.Error(), c.tree) || !strings.Contains(err.Error(), "--proof") {
		t.Fatalf("err %v", err)
	}
}

func TestTheOwnersOrderDecidesARestartOfAChangedDashboard(t *testing.T) {
	c := newProofCase(t)
	write(t, c.s.served(), "ff"+c.tree[2:])
	stale := errors.New("REFUSED #416: past the pass that read it")

	if err := c.s.Clear(c.tree, liveURL, true, "", func() error { return nil }); err != nil {
		t.Fatalf("a standing order: %v", err)
	}
	if err := c.s.Clear(c.tree, liveURL, true, "", func() error { return stale }); !errors.Is(err, stale) {
		t.Fatalf("a stale order: %v", err)
	}
}

func TestAProofClearsTheRestartOnlyWhenItProvesThatTree(t *testing.T) {
	c := newProofCase(t)
	cases := map[string]struct {
		edit  func(*Proof)
		fault string
	}{
		"complete":       {func(*Proof) {}, ""},
		"text only":      {func(p *Proof) { p.TextOnly, p.Pages[0].Phone = true, "" }, ""},
		"other tree":     {func(p *Proof) { p.Tree = "ff" + c.tree[2:] }, "the dashboard going live"},
		"short commit":   {func(p *Proof) { p.Commit = "ccccccc" }, "no full commit sha"},
		"live port":      {func(p *Proof) { p.Served = "http://127.0.0.1:61210/" }, "is the live port"},
		"no phone shot":  {func(p *Proof) { p.Pages[0].Phone = "" }, "lacks a desktop or phone screenshot"},
		"missing shot":   {func(p *Proof) { p.Pages[0].Desktop = filepath.Join(c.shots, "gone.png") }, "is missing or empty"},
		"no answers":     {func(p *Proof) { p.Pages[0].Answers = nil }, "names no owner answer or ruling"},
		"answer unshown": {func(p *Proof) { p.Pages[0].Answers[0].Shows = " " }, "does not say what the page shows"},
		"no pages":       {func(p *Proof) { p.Pages = nil }, "shows no page"},
	}
	for name, tc := range cases {
		err := c.s.Clear(c.tree, liveURL, true, c.proof(tc.edit), nil)
		if tc.fault == "" && err != nil {
			t.Errorf("%s: %v", name, err)
		}
		if tc.fault != "" && (err == nil || !strings.Contains(err.Error(), tc.fault)) {
			t.Errorf("%s: err %v", name, err)
		}
	}
}
