package orca

import (
	"bytes"
	"context"
	"errors"
	"io"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const graderMesh = `[{"mesh":"default","url":"https://relay.example.test/","routes":["drive-g"]}]`

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
	switch {
	case strings.HasPrefix(line, "ccx vcs auth token Org/repo"):
		return shell.Result{Stdout: "token-x"}, nil
	case strings.HasPrefix(line, "cci relay export --drive drive-g"):
		_, err := io.WriteString(c.Stdout, "rows")
		return shell.Result{}, err
	case line == "cci relay status --json":
		return shell.Result{Stdout: graderMesh}, nil
	case line == "cci node --json":
		return shell.Result{Stdout: `{"public_key":"owner-pub"}`}, nil
	case strings.HasSuffix(line, "cat "+sponsorKey):
		return shell.Result{Stdout: "key of " + c.Argv[3]}, nil
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
	maker := &GraderMaker{Runner: w, Store: w.store, Home: "/Users/me", Sleep: func(time.Duration) {}, Now: func() time.Time { return time.Date(2026, 11, 5, 9, 0, 0, 0, time.UTC) }, Out: &w.out}
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

func TestGradersJoinWithTheSponsorRunnerJSONNamesAndShareOneExport(t *testing.T) {
	w := newGraderWorld(t, "sponsor-7")

	if refused := w.make("grader-a", "grader-b"); refused != "" {
		t.Fatal(refused)
	}

	if exports := w.ran("cci", "relay", "export"); len(exports) != 1 {
		t.Fatal("each grader exported the store again", exports)
	}
	keys := w.ran("sprite", "exec", "-s", "sponsor-7", "--no-stdin", "--", "cat", sponsorKey)
	if len(keys) != 2 {
		t.Fatal("the service key did not come from WORKER_RELAY_SPONSOR", w.calls)
	}
	for _, box := range []string{"grader-a", "grader-b"} {
		if !slices.Contains(w.stdins[box], "key of sponsor-7") {
			t.Fatal(box, "joined without the sponsor's key", w.stdins[box])
		}
		if !slices.Contains(w.stdins[box], "token-x\n") {
			t.Fatal(box, "fetched its history without one token line", w.stdins[box])
		}
		install := w.ran("sprite", "exec", "-s", box, "--no-stdin", "--", "sh", "-c", installTools)
		if len(install) != 1 || slices.Contains(install[0], "long-running@skills") || !slices.Contains(install[0], "ccx") {
			t.Fatal(box, "was not offered only the grader pins", install)
		}
		if census := w.census()[box]; census["ccx"] != rollCCX {
			t.Fatal(box, "has no recorded ccx for a roll to compare", census)
		}
	}
	if len(w.ran("sprite", "exec", "-s", "grader-a", "--no-stdin", "--file")) != 1 {
		t.Fatal("grader-a was not seeded once", w.calls)
	}
}

func TestAGraderLinksEachGradingCheckoutOnce(t *testing.T) {
	w := newGraderWorld(t, "sponsor-7")

	if refused := w.make("grader-a"); refused != "" {
		t.Fatal(refused)
	}

	links := slices.IndexFunc(w.calls, func(argv []string) bool { return slices.Contains(argv, linkGrader) })
	if got := w.calls[links][slices.Index(w.calls[links], linkGrader)+2:]; !slices.Equal(got, []string{"/Users/me", "/Users/me/runner"}) {
		t.Fatal(got)
	}
}

func TestNoSponsorRefusesBeforeAnySpriteIsTouched(t *testing.T) {
	w := newGraderWorld(t, "")

	refused := w.make("grader-a")

	if !strings.Contains(refused, "set orca.sprite.env.WORKER_RELAY_SPONSOR") {
		t.Fatal(refused)
	}
	if len(w.calls) != 0 {
		t.Fatal("a grader with no sponsor ran", w.calls)
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
	if !slices.Contains(w.stdins["grader-b"], "key of sponsor-7") || len(w.ran("sprite", "exec", "-s", "grader-b", "--no-stdin", "--file")) != 1 {
		t.Fatal("grader-b stopped with grader-a", w.calls)
	}
	if census := w.census()["grader-a"]; census["cci"] != "0.49.36" {
		t.Fatal("the partial install was not recorded", census)
	}
}
