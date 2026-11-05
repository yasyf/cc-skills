package subdrive

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	parentDrive = "brook"
	childDrive  = "brook--billing"
	childRun    = "run_child"
)

var t0 = time.Date(2026, 11, 2, 20, 0, 0, 0, time.UTC)

type fakeBus struct {
	posted []client.Draft
	open   []client.Record
	tailed []string
}

func (b *fakeBus) Post(_ context.Context, d client.Draft) (client.Record, error) {
	b.posted = append(b.posted, d)
	return client.Record{Seq: int64(len(b.posted))}, nil
}

func (b *fakeBus) Tail(_ context.Context, q client.Query) ([]client.Record, error) {
	b.tailed = append(b.tailed, q.Drive)
	return nil, nil
}

func (b *fakeBus) Open(context.Context, string, ...string) ([]client.Record, error) {
	return b.open, nil
}

func (b *fakeBus) Who(context.Context, string) ([]client.Peer, error) {
	return nil, nil
}

type fakeRunner struct {
	ran    []shell.Command
	ledger string
}

func (r *fakeRunner) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	r.ran = append(r.ran, c)
	line := strings.Join(c.Argv, " ")
	switch {
	case strings.Contains(line, "run-create"):
		return shell.Result{Stdout: `{"ok":true,"result":{"run":{"id":"` + childRun + `"}}}`}, nil
	case strings.Contains(line, "ledger.py") && strings.Contains(line, " init "):
		return shell.Result{Stdout: "child-ledger\n"}, nil
	case strings.Contains(line, "ledger.py") && strings.Contains(line, " list "):
		return shell.Result{Stdout: r.ledger}, nil
	case strings.HasPrefix(line, "git "):
		return shell.Result{Stdout: "/repo/.git\n"}, nil
	}
	return shell.Result{Stdout: "root ready\n"}, nil
}

func (r *fakeRunner) launch(t *testing.T) shell.Command {
	t.Helper()
	found := slices.IndexFunc(r.ran, func(c shell.Command) bool { return strings.HasSuffix(c.Argv[0], "orca-launch.sh") })
	if found < 0 {
		t.Fatalf("no launch in %v", r.ran)
	}
	return r.ran[found]
}

type harness struct {
	Manager
	bus          *fakeBus
	runner       *fakeRunner
	state        string
	supervised   []string
	unsupervised []string
}

func newHarness(t *testing.T) *harness {
	t.Helper()
	t.Setenv("CLAUDE_CONFIG_DIR", t.TempDir())
	t.Setenv(laneEnv, lanes.Root)
	h := &harness{bus: &fakeBus{}, runner: &fakeRunner{ledger: "[]"}, state: t.TempDir()}
	h.Manager = Manager{
		Env: lanes.Env{
			Bus: h.bus, Runner: h.runner, State: h.state, Claude: "/claude", Now: func() time.Time { return t0 },
			Config: config.Config{Repo: "o/r", Drive: parentDrive, Checkout: "/repo", Author: "a", Label: parentDrive, Movers: []string{"lanes"}, OrcaRun: "run_parent"},
		},
		Supervise:   func(path string) error { h.supervised = append(h.supervised, path); return nil },
		Unsupervise: func(path string) error { h.unsupervised = append(h.unsupervised, path); return nil },
	}
	return h
}

func complete() Request {
	request := Request{Name: "billing", Objective: "move billing to the new ledger", Reason: "nothing here touches billing", Facts: map[string]string{}}
	for _, f := range facts {
		request.Facts[f.flag] = "none: billing lives in its own service"
	}
	return request
}

func children(t *testing.T) []drive.Entry {
	t.Helper()
	found, err := drive.Children(parentDrive)
	if err != nil {
		t.Fatal(err)
	}
	return found
}

func TestStartRefusesWithoutAReasonAndEachUnrelatednessFact(t *testing.T) {
	cases := map[string]func(*Request){
		"--reason":    func(r *Request) { r.Reason = " " },
		"--objective": func(r *Request) { r.Objective = "" },
	}
	for _, f := range facts {
		cases["--"+f.flag] = func(r *Request) { delete(r.Facts, f.flag) }
	}
	for flag, strip := range cases {
		t.Run(flag, func(t *testing.T) {
			h := newHarness(t)
			request := complete()
			strip(&request)

			_, err := h.Start(context.Background(), request)

			if err == nil || !strings.Contains(err.Error(), flag) {
				t.Fatalf("got %v, want a refusal naming %s", err, flag)
			}
			if len(h.runner.ran) != 0 || len(h.bus.posted) != 0 || len(h.supervised) != 0 || len(children(t)) != 0 {
				t.Fatalf("a refused start acted: ran %v, posted %v", h.runner.ran, h.bus.posted)
			}
		})
	}
}

func TestTheStartVerbNamesEveryMissingFactAtOnce(t *testing.T) {
	t.Setenv("CLAUDE_CONFIG_DIR", t.TempDir())
	t.Setenv(laneEnv, lanes.Root)
	path := filepath.Join(t.TempDir(), configFile)
	if err := writeConfig(path, config.Config{Repo: "o/r", Drive: parentDrive, Checkout: "/repo", Author: "a", Label: parentDrive, Movers: []string{"lanes"}}); err != nil {
		t.Fatal(err)
	}
	var out bytes.Buffer
	cmd := Command(&app.App{ConfigPath: path, Runner: &fakeRunner{}, Now: func() time.Time { return t0 }})
	cmd.SetOut(&out)
	cmd.SetErr(&out)
	cmd.SetArgs([]string{"sub", "start", "billing", "--reason", "nothing here touches billing"})

	err := cmd.ExecuteContext(context.Background())

	if err == nil {
		t.Fatal("started with no facts")
	}
	for _, flag := range []string{"--objective", "--no-shared-prs", "--no-shared-files", "--no-shared-steps", "--no-shared-targets", "--own-lanes"} {
		if !strings.Contains(err.Error(), flag) {
			t.Errorf("%q does not name %s", err, flag)
		}
	}
	if strings.Contains(err.Error(), "--reason") {
		t.Errorf("%q names the reason it was given", err)
	}
}

func TestOnlyARootCoordinatorStartsASubDrive(t *testing.T) {
	t.Run("a sub-drive's own coordinator", func(t *testing.T) {
		h := newHarness(t)
		h.Env.Config.Parent = "reef"
		_, err := h.Start(context.Background(), complete())
		if err == nil || !strings.Contains(err.Error(), "root@reef") {
			t.Fatalf("got %v, want a refusal sending it to root@reef", err)
		}
	})
	t.Run("a window bound to another lane", func(t *testing.T) {
		h := newHarness(t)
		t.Setenv(laneEnv, "owner-relay")
		_, err := h.Start(context.Background(), complete())
		if err == nil || !strings.Contains(err.Error(), "owner-relay") {
			t.Fatalf("got %v, want a refusal naming the bound lane", err)
		}
	})
}

func TestAStartedCoordinatorIsBoundToTheChildDriveAndReportsToTheParent(t *testing.T) {
	h := newHarness(t)

	out, err := h.Start(context.Background(), complete())
	if err != nil {
		t.Fatal(err)
	}

	state := filepath.Join(h.state, "sub", "billing")
	configPath := filepath.Join(state, configFile)
	briefPath := filepath.Join(state, lanes.Root, "brief.md")
	launch := h.runner.launch(t)
	if !slices.Equal(launch.Argv[1:], []string{lanes.Root, "opus", "high", briefPath}) {
		t.Fatalf("launch argv = %v", launch.Argv)
	}
	for _, want := range []string{driveEnv + "=" + childDrive, "ORCA_LAUNCH_RUN=" + childRun, "ORCA_LAUNCH_PARENT=/repo"} {
		if !slices.Contains(launch.Env, want) {
			t.Errorf("launch env %v lacks %s", launch.Env, want)
		}
	}
	if !slices.Equal(h.bus.tailed, []string{childDrive}) {
		t.Errorf("the launch read the runner on %v, want the child drive", h.bus.tailed)
	}
	if !slices.Equal(h.supervised, []string{configPath}) {
		t.Errorf("supervised %v", h.supervised)
	}

	cfg, err := config.Load(configPath, &bytes.Buffer{})
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Drive != childDrive || cfg.Parent != parentDrive || cfg.OrcaRun != childRun || cfg.Label != childDrive {
		t.Errorf("child config = %+v", cfg)
	}

	found := children(t)
	if len(found) != 1 {
		t.Fatalf("children = %+v", found)
	}
	entry := found[0]
	if entry.Bus() != childDrive || entry.StateDir != state || entry.Ledger != "child-ledger" || entry.OrcaRun != childRun || entry.GitCommonDir != "/repo/.git" || entry.Reason == "" || len(entry.Facts) != len(facts) {
		t.Errorf("registry entry = %+v", entry)
	}

	written, err := os.ReadFile(briefPath)
	if err != nil {
		t.Fatal(err)
	}
	text := string(written)
	for _, want := range []string{
		"Register slice: lane=root step=all", "`long-running:coordinator`", "CLAUDE_LONG_RUNNING_DRIVE=" + childDrive,
		"drive.py start --drive " + childDrive + " --ledger child-ledger", "--to root@" + parentDrive, "move billing to the new ledger", "- PRs: none:",
	} {
		if !strings.Contains(text, want) {
			t.Errorf("brief lacks %q:\n%s", want, text)
		}
	}
	if clock := regexp.MustCompile(`--stamp|\b\d{1,2}:\d{2}\s?[AP]M\b`).FindString(text); clock != "" {
		t.Errorf("brief asks for a time with %q:\n%s", clock, text)
	}

	if len(h.bus.posted) != 1 {
		t.Fatalf("posted %+v", h.bus.posted)
	}
	report := h.bus.posted[0]
	if report.Drive != parentDrive || report.Lane != lanes.Root || !slices.Equal(report.To, []string{lanes.OwnerRelay}) || !strings.Contains(report.Text, "nothing here touches billing") {
		t.Errorf("the parent's record of the start = %+v", report)
	}
	if !strings.Contains(out, childRun) {
		t.Errorf("output %q does not name the Run", out)
	}
}

func TestASecondStartOfAnOpenNameIsRefused(t *testing.T) {
	h := newHarness(t)
	if _, err := h.Start(context.Background(), complete()); err != nil {
		t.Fatal(err)
	}
	ran := len(h.runner.ran)

	_, err := h.Start(context.Background(), complete())

	if err == nil || !strings.Contains(err.Error(), "already open") {
		t.Fatalf("got %v", err)
	}
	if len(h.runner.ran) != ran {
		t.Fatal("the refused start ran a command")
	}
}

func seedOpenWork(t *testing.T, h *harness, state string) {
	t.Helper()
	s, err := store.Open(filepath.Join(state, lanes.StoreFile))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = s.Close() }()
	for _, lane := range []store.Lane{
		{Name: lanes.Root, Brief: "/b/root.md", Model: "opus", Effort: "xhigh", State: store.LaneDesired},
		{Name: "invoices", Brief: "/b/invoices.md", Model: "opus", Effort: "high", State: store.LaneDesired},
		{Name: "finished", Brief: "/b/finished.md", Model: "opus", Effort: "high", State: store.LaneRetired},
	} {
		if _, err := s.WantLane(lane, t0); err != nil {
			t.Fatal(err)
		}
	}
	h.bus.open = []client.Record{{Seq: 77, Lane: "invoices", Kind: "blocker", Text: "the ledger schema is unsettled"}}
	h.runner.ledger = `[{"pr":"4801","head":"abc123","lane":"invoices"}]`
}

func TestEndHandsEveryOpenItemBackToTheParentAndClosesTheChild(t *testing.T) {
	h := newHarness(t)
	if _, err := h.Start(context.Background(), complete()); err != nil {
		t.Fatal(err)
	}
	state := filepath.Join(h.state, "sub", "billing")
	seedOpenWork(t, h, state)
	h.bus.posted = nil

	if _, err := h.End(context.Background(), "billing"); err != nil {
		t.Fatal(err)
	}

	if len(h.bus.posted) != 2 {
		t.Fatalf("posted %+v", h.bus.posted)
	}
	back, down := h.bus.posted[0], h.bus.posted[1]
	if back.Drive != parentDrive || back.Kind != "ask" || !slices.Equal(back.To, []string{lanes.Root}) || back.Lane == lanes.Root {
		t.Errorf("hand-back = %+v", back)
	}
	for _, want := range []string{"3 open items", "lane invoices (desired)", "#77 blocker from invoices", "PR #4801 of invoices", "lr gc --run " + childRun} {
		if !strings.Contains(back.Text, want) {
			t.Errorf("hand-back %q lacks %q", back.Text, want)
		}
	}
	if strings.Contains(back.Text, "finished") {
		t.Errorf("hand-back %q names a retired lane", back.Text)
	}
	details, err := os.ReadFile(filepath.Join(state, "handback.md"))
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"/b/invoices.md", "the ledger schema is unsettled", "abc123"} {
		if !strings.Contains(string(details), want) {
			t.Errorf("hand-back file lacks %q:\n%s", want, details)
		}
	}
	if down.Drive != childDrive || down.Kind != "stand-down" || !slices.Equal(slices.Sorted(slices.Values(down.To)), []string{"invoices", lanes.Root}) {
		t.Errorf("stand-down = %+v", down)
	}
	if !slices.Equal(h.unsupervised, []string{filepath.Join(state, configFile)}) {
		t.Errorf("unsupervised %v", h.unsupervised)
	}
	if len(children(t)) != 0 {
		t.Error("the ended sub-drive is still registered")
	}
}

func TestASubDriveStartedAgainUnderAnEndedNameRelaunchesNoneOfItsOldLanes(t *testing.T) {
	h := newHarness(t)
	if _, err := h.Start(context.Background(), complete()); err != nil {
		t.Fatal(err)
	}
	state := filepath.Join(h.state, "sub", "billing")
	seedOpenWork(t, h, state)

	if _, err := h.End(context.Background(), "billing"); err != nil {
		t.Fatal(err)
	}

	s, err := store.Open(filepath.Join(state, lanes.StoreFile))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = s.Close() }()
	live, err := s.Lanes(store.LaneLaunching, store.LaneDesired, store.LaneHeld)
	if err != nil {
		t.Fatal(err)
	}
	if len(live) != 0 {
		t.Fatalf("the new supervisor would relaunch %+v", live)
	}
}

func TestEndingWithNothingOpenOnlyReports(t *testing.T) {
	h := newHarness(t)
	if _, err := h.Start(context.Background(), complete()); err != nil {
		t.Fatal(err)
	}
	h.bus.posted = nil

	if _, err := h.End(context.Background(), "billing"); err != nil {
		t.Fatal(err)
	}

	if len(h.bus.posted) != 1 || h.bus.posted[0].Kind != "report" || !strings.Contains(h.bus.posted[0].Text, "nothing open") {
		t.Fatalf("posted %+v", h.bus.posted)
	}
	if _, err := os.Stat(filepath.Join(h.state, "sub", "billing", "handback.md")); !os.IsNotExist(err) {
		t.Errorf("a hand-back file with nothing to hand back: %v", err)
	}
}

func TestEndingAnUnknownSubDriveActsOnNothing(t *testing.T) {
	h := newHarness(t)

	_, err := h.End(context.Background(), "billing")

	if err == nil || !strings.Contains(err.Error(), "no open sub-drive billing") {
		t.Fatalf("got %v", err)
	}
	if len(h.bus.posted) != 0 || len(h.unsupervised) != 0 {
		t.Fatal("an unknown sub-drive was acted on")
	}
}

func TestListShowsEachOpenSubDriveWithWhatItStillHolds(t *testing.T) {
	h := newHarness(t)
	if _, err := h.Start(context.Background(), complete()); err != nil {
		t.Fatal(err)
	}
	seedOpenWork(t, h, filepath.Join(h.state, "sub", "billing"))

	lines, err := h.List(context.Background())
	if err != nil {
		t.Fatal(err)
	}

	if len(lines) != 1 {
		t.Fatalf("lines = %q", lines)
	}
	for _, want := range []string{"billing: move billing", "Coordinator desired", "3 open items", childRun, "nothing here touches billing"} {
		if !strings.Contains(lines[0], want) {
			t.Errorf("%q lacks %q", lines[0], want)
		}
	}
}

func TestTheChildsRegistryEntryKeepsThePythonRegistrysFields(t *testing.T) {
	h := newHarness(t)
	if _, err := h.Start(context.Background(), complete()); err != nil {
		t.Fatal(err)
	}
	dir, err := drive.ConfigDir()
	if err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(filepath.Join(dir, "long-running", "drives", childDrive+".json"))
	if err != nil {
		t.Fatal(err)
	}
	var raw map[string]any
	if err := json.Unmarshal(data, &raw); err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{"drive", "sessions", "orca_run", "started_at", "ledger", "repo", "git_common_dir", "checkout", "state_dir", "program", "cci_drive", "updated_at", "parent"} {
		if _, found := raw[key]; !found {
			t.Errorf("registry entry lacks %s, which drive.py reads: %s", key, data)
		}
	}
	if sessions, ok := raw["sessions"].([]any); !ok || len(sessions) != 0 {
		t.Errorf("sessions = %v, want an empty list drive.py can append to", raw["sessions"])
	}
}
