package orca

import (
	"bytes"
	"context"
	"errors"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

type graderWorld struct {
	t      *testing.T
	store  *store.Store
	runner string
	out    bytes.Buffer

	mu          sync.Mutex
	calls       [][]string
	stdins      map[string][]string
	failInstall string
}

func newGraderWorld(t *testing.T, sponsor string) *graderWorld {
	root := t.TempDir()
	s, err := store.Open(filepath.Join(root, "lr.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	f := &workerFixture{t: t}
	f.write(filepath.Join(root, "plugin", "bin", "lr.binrun"), lrPin("linux-x86_64"), 0o700)
	t.Setenv("BINRUN_PLUGIN_ROOT", filepath.Join(root, "plugin"))
	w := &graderWorld{t: t, store: s, runner: filepath.Join(root, "runner.json"), stdins: map[string][]string{}}
	raw := `{"drive":"drive-g","landing":{"repo":"Org/repo","checkout":"/Users/me/runner"},"fidelity":{"checkout":"/Users/me/runner"},"orca":{"sprite":{"env":{"WORKER_CCI_URL":"` + testCCIURL + `","WORKER_CCI_SHA256":"` + testCCISHA + `","WORKER_RELAY_SPONSOR":"` + sponsor + `"},"tools":` + rollTools + `}}}`
	f.write(w.runner, raw, 0o600)
	return w
}

func (w *graderWorld) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	w.calls = append(w.calls, c.Argv)
	line := strings.Join(c.Argv, " ")
	if strings.HasPrefix(line, "ccx vcs auth token Org/repo") {
		return shell.Result{Stdout: "token-x"}, nil
	}
	if len(c.Argv) < 4 || c.Argv[0] != "sprite" {
		w.t.Fatalf("a grader needed a CCI replica: %v", c.Argv)
	}
	box := c.Argv[3]
	w.stdins[box] = append(w.stdins[box], c.Stdin)
	if slices.Contains(c.Argv, installTools) {
		if box == w.failInstall {
			return shell.Result{Code: 1, Stdout: "installed cci 0.49.36\n", Stderr: "refused: the lr archive did not match\n"}, &shell.ExitError{Argv: c.Argv, Code: 1}
		}
		return shell.Result{Stdout: rollAll}, nil
	}
	return shell.Result{}, nil
}

func (w *graderWorld) make(boxes ...string) string {
	w.t.Helper()
	maker := &GraderMaker{Runner: w, Store: w.store, Home: "/Users/me", Now: func() time.Time { return time.Date(2026, 11, 5, 9, 0, 0, 0, time.UTC) }, Out: &w.out}
	err := maker.Make(context.Background(), w.runner, boxes)
	var failed *Failed
	if errors.As(err, &failed) {
		return failed.Line
	}
	if err != nil {
		w.t.Fatal(err)
	}
	return ""
}

func (w *graderWorld) census() map[string]map[string]string {
	return (&rollWorld{t: w.t, store: w.store}).census()
}

func (w *graderWorld) ran(prefix ...string) [][]string {
	var matched [][]string
	for _, argv := range w.calls {
		if len(argv) >= len(prefix) && slices.Equal(argv[:len(prefix)], prefix) {
			matched = append(matched, argv)
		}
	}
	return matched
}

func TestGradersNeedNoSponsorHistoryReplicaOrRelayService(t *testing.T) {
	w := newGraderWorld(t, "")

	if refused := w.make("grader-a", "grader-b"); refused != "" {
		t.Fatal(refused)
	}

	for _, box := range []string{"grader-a", "grader-b"} {
		if !slices.Contains(w.stdins[box], "token-x\n") {
			t.Fatal(box, "fetched its history without one token line", w.stdins[box])
		}
		install := w.ran("sprite", "exec", "-s", box, "--no-stdin", "--", "sh", "-c", installTools)
		if len(install) != 1 || slices.Contains(install[0], "long-running@skills") || !slices.Contains(install[0], "ccx") {
			t.Fatal(box, "was not offered only the grader pins", install)
		}
		if !slices.Contains(install[0], "no-cci-daemon") {
			t.Fatal(box, "was not told to stay without a cci daemon", install[0])
		}
		if census := w.census()[box]; census["ccx"] != rollCCX {
			t.Fatal(box, "has no recorded ccx for a roll to compare", census)
		}
		calls := w.ran("sprite", "exec", "-s", box)
		if len(calls) != 3 || !slices.Contains(calls[1], mirrorGrader) || !slices.Contains(calls[2], unshallowGrader) {
			t.Fatal(box, "needed more than install, mirror and Git history", calls)
		}
	}
}

func TestAGraderLinksEachGradingCheckoutOnce(t *testing.T) {
	w := newGraderWorld(t, "sponsor-7")

	if refused := w.make("grader-a"); refused != "" {
		t.Fatal(refused)
	}

	links := slices.IndexFunc(w.calls, func(argv []string) bool { return slices.Contains(argv, mirrorGrader) })
	if got := w.calls[links][slices.Index(w.calls[links], mirrorGrader)+2:]; !slices.Equal(got, []string{"/Users/me", graderShipped, graderCheckout, "/Users/me/runner"}) {
		t.Fatal(got)
	}
}

func TestAFailedInstallStopsOnlyItsOwnGrader(t *testing.T) {
	w := newGraderWorld(t, "sponsor-7")
	w.failInstall = "grader-a"

	refused := w.make("grader-a", "grader-b")

	if refused != "lr sprites grader: 1 of 2 Sprites were not made: grader-a" {
		t.Fatal(refused)
	}
	if !strings.Contains(w.out.String(), "grader-a stopped at install pins: refused: the lr archive did not match") {
		t.Fatal(w.out.String())
	}
	if len(w.stdins["grader-a"]) != 1 {
		t.Fatal("grader-a went on past its failed install", w.stdins["grader-a"])
	}
	if len(w.ran("sprite", "exec", "-s", "grader-b")) != 3 {
		t.Fatal("grader-b stopped with grader-a", w.calls)
	}
	if census := w.census()["grader-a"]; census["cci"] != "0.49.36" {
		t.Fatal("the partial install was not recorded", census)
	}
}

type graderDisk struct {
	t       *testing.T
	home    string
	mac     string
	shipped string
	clone   string
	runner  string
}

func newGraderDisk(t *testing.T) *graderDisk {
	root := t.TempDir()
	d := &graderDisk{t: t, home: filepath.Join(root, "home", "sprite"), mac: filepath.Join(root, "Users", "me"), shipped: filepath.Join(root, "lr-ship")}
	d.clone = filepath.Join(d.home, "monorepo")
	d.runner = filepath.Join(d.mac, ".claude", "worktrees", "monorepo", "runner")
	for _, dir := range []string{d.clone, filepath.Dir(d.mac), d.shipped} {
		if err := os.MkdirAll(dir, 0o750); err != nil {
			t.Fatal(err)
		}
	}
	return d
}

func (d *graderDisk) write(path, text string) {
	d.t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		d.t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o600); err != nil {
		d.t.Fatal(err)
	}
}

func (d *graderDisk) mirror() (string, error) {
	out, err := exec.Command("sh", "-c", mirrorGrader, "sh", d.mac, d.shipped, d.clone, d.runner).CombinedOutput()
	return string(out), err
}

func (d *graderDisk) homeIsALink() bool {
	d.t.Helper()
	info, err := os.Lstat(d.mac)
	if err != nil {
		d.t.Fatal(err)
	}
	return info.Mode()&fs.ModeSymlink != 0
}

func (d *graderDisk) runnerIsTheClone() {
	d.t.Helper()
	if target, err := os.Readlink(d.runner); err != nil || target != d.clone {
		d.t.Fatal("the grading checkout does not resolve to the Sprite's clone:", target, err)
	}
}

func TestAGraderWhoseMacHomeLinkedItsOwnHomeGetsARealMirrorSoAPassNeverReplacesALanesDriveEntry(t *testing.T) {
	d := newGraderDisk(t)
	lane := filepath.Join(d.home, ".claude", "long-running", "drives", "drive-g.json")
	d.write(lane, `{"host":"sprite"}`)
	if err := os.Symlink(d.home, d.mac); err != nil {
		t.Fatal(err)
	}

	if out, err := d.mirror(); err != nil {
		t.Fatal(err, out)
	}
	d.write(filepath.Join(d.mac, ".claude", "long-running", "drives", "drive-g.json"), `{"checkout":"/Users/me/.claude/worktrees/monorepo/runner"}`)

	if d.homeIsALink() {
		t.Fatal("the Mac home still links the Sprite's own home")
	}
	d.runnerIsTheClone()
	if got, err := os.ReadFile(lane); err != nil || string(got) != `{"host":"sprite"}` {
		t.Fatal("a pass's drive entry replaced the lane's:", string(got), err)
	}
}

func TestAGraderKeepsItsHomeLinkWhileAPassHoldsAShippedPluginCopy(t *testing.T) {
	d := newGraderDisk(t)
	if err := os.Symlink(d.home, d.mac); err != nil {
		t.Fatal(err)
	}
	held := filepath.Join(d.shipped, "3f2a", "9c41.lock")
	d.write(held, "")
	lock, err := os.Open(held)
	if err != nil {
		t.Fatal(err)
	}
	defer lock.Close()
	if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_SH); err != nil {
		t.Fatal(err)
	}

	out, err := d.mirror()

	if err == nil || !strings.Contains(out, "a pass holds "+held) {
		t.Fatal("the step went on under a running pass:", err, out)
	}
	if !d.homeIsALink() {
		t.Fatal("the link a running pass reads through was removed")
	}
	if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_UN); err != nil {
		t.Fatal(err)
	}
	if out, err := d.mirror(); err != nil {
		t.Fatal(err, out)
	}
	if d.homeIsALink() {
		t.Fatal("the link stayed once the pass ended")
	}
	d.runnerIsTheClone()
}

func TestAPassThatReachesAGraderBeforeItsSetupLeavesAMirrorTheSetupFinishes(t *testing.T) {
	d := newGraderDisk(t)
	d.write(filepath.Join(d.mac, ".local", "share", "claude-plugins", "cache", "long-running", "0.7.1865.digest"), "")

	if out, err := d.mirror(); err != nil {
		t.Fatal(err, out)
	}

	d.runnerIsTheClone()
	entries, err := os.ReadDir(d.mac)
	if err != nil {
		t.Fatal(err)
	}
	names := make([]string, 0, len(entries))
	for _, entry := range entries {
		names = append(names, entry.Name())
	}
	if !slices.Equal(names, []string{".claude", ".local"}) {
		t.Fatal("the setup left something beside the pass's copy and the checkout link:", names)
	}
}
