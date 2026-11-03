package orca

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/incident"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/wait"
)

const (
	testRun      = "run_1"
	testDrive    = "brook"
	testLane     = "lane-a"
	testBrief    = "Register slice: lane=lane-a step=all\nScale: no N\n"
	testDispatch = "ctx_a"
	testTerminal = "term_a"
)

var idleTail = object{"terminal": object{"source": "screen", "tail": []string{"────────────────────", "❯", "────────────────────"}}}

type fakeDispatch struct {
	status, terminal, observation string
	revoked                       bool
}

type spawned struct {
	argv []string
	log  string
	env  []string
}

type fakeProcess struct{ exited bool }

func (p *fakeProcess) Exited() bool { return p.exited }

type deskWorld struct {
	t          *testing.T
	mu         sync.Mutex
	home       string
	desk       *Desk
	now        time.Time
	records    []client.Record
	posts      []client.Draft
	calls      [][]string
	dispatches map[string]*fakeDispatch
	runInbox   []orcaMessage
	handed     []orcaMessage
	delivery   string
	mailboxes  map[string][]orcaMessage
	mail       []orcaMessage
	unread     map[string][]orcaMessage
	refusals   map[string]object
	postError  error
	coordinate string
	spawns     []spawned
	launchOut  map[string]string
	process    *fakeProcess
	fired      []lanes.Fired
	fireErr    error
	sentID     int
	busy       string
	ccnFails   bool
	parent     int
	release    string
	refuseTime bool
}

func newDeskWorld(t *testing.T) *deskWorld {
	t.Helper()
	home := t.TempDir()
	w := &deskWorld{
		t: t, home: home, now: time.Date(2026, 11, 3, 8, 0, 0, 0, time.UTC),
		dispatches: map[string]*fakeDispatch{}, mailboxes: map[string][]orcaMessage{}, unread: map[string][]orcaMessage{}, refusals: map[string]object{},
		coordinate: testRun, launchOut: map[string]string{}, process: &fakeProcess{exited: true}, busy: "95.0",
	}
	config := DeskConfig{
		Source: filepath.Join(home, "state", "runner.json"), Store: filepath.Join(home, "store", "incidents"), Drive: testDrive, DeskInbox: filepath.Join(home, "state", "inbox", "orca-desk.md"), Run: testRun,
		Receipts: filepath.Join(home, "receipts"), BriefsRepo: filepath.Join(home, "repo"), BriefsLog: "log_1", StartMinutes: 10, LaunchMinutes: 15, HoldMinutes: 15, CaseLanes: 2,
	}
	w.write(config.Source, `{"drive":"brook","orca":{"run":"run_1"}}`)
	registry, err := store.Memory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = registry.Close() })
	w.desk = &Desk{
		Config: config, Bus: w, Runner: w, Env: func(string) string { return "" }, Now: func() time.Time { return w.now }, Sleep: func(d time.Duration) { w.now = w.now.Add(d) }, Parent: func() int { return w.parent }, Release: func() string { return w.release },
		Plugin: filepath.Join(home, "plugin"), Stderr: io.Discard,
		Spawn: func(argv []string, log string, env []string) (Process, error) {
			w.mu.Lock()
			defer w.mu.Unlock()
			w.spawns = append(w.spawns, spawned{argv: argv, log: log, env: env})
			if out, found := w.launchOut[argv[len(argv)-4]]; found {
				w.write(log, out)
			}
			return w.process, nil
		},
		FireIncident: func(_ context.Context, fired lanes.Fired) error {
			w.mu.Lock()
			defer w.mu.Unlock()
			w.fired = append(w.fired, fired)
			return w.fireErr
		},
		Registry: registry,
	}
	w.desk.open()
	w.desk.passSince = w.now
	if err := w.desk.ensure(deskRunner, deskRunner); err != nil {
		t.Fatal(err)
	}
	return w
}

func (w *deskWorld) write(path, text string) {
	w.t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		w.t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o600); err != nil {
		w.t.Fatal(err)
	}
}

func (w *deskWorld) register(lane store.Lane) {
	w.t.Helper()
	lane.Brief, lane.Model, lane.Effort, lane.State = "/brief.md", "opus", "xhigh", store.LaneDesired
	if _, err := w.desk.Registry.WantLane(lane, w.now); err != nil {
		w.t.Fatal(err)
	}
}

func (w *deskWorld) postOpening(prefix string) client.Draft {
	w.t.Helper()
	for _, post := range w.posts {
		if strings.HasPrefix(post.Text, prefix) {
			return post
		}
	}
	w.t.Fatalf("no post opens %q", prefix)
	return client.Draft{}
}

func (w *deskWorld) brief(lane, text string) string {
	path := filepath.Join(w.home, "briefs", lane, "brief.md")
	w.write(path, text)
	return path
}

func (w *deskWorld) live(lane, dispatch, terminal string) {
	w.write(filepath.Join(w.desk.Config.Receipts, lane+".json"), fmt.Sprintf(`{"result":{"dispatchId":%q}}`, dispatch))
	w.write(filepath.Join(w.desk.Config.Receipts, lane+".terminal"), terminal+"\n")
	w.dispatches[dispatch] = &fakeDispatch{status: "running", terminal: terminal, observation: "live"}
}

func recordFrom(fields object) client.Record {
	raw, err := json.Marshal(fields)
	if err != nil {
		panic(err)
	}
	var record client.Record
	if err := json.Unmarshal(raw, &record); err != nil {
		panic(err)
	}
	return record
}

func (w *deskWorld) add(fields object) client.Record {
	w.mu.Lock()
	defer w.mu.Unlock()
	seq := int64(len(w.records) + 1)
	base := object{"seq": seq, "drive": testDrive, "lane": coordinatorLane, "kind": kindGo, "at": store.Stamp(w.now), "text": "", "source": "post", "refs": object{}, "fields": object{}}
	for key, value := range fields {
		base[key] = value
	}
	record := recordFrom(base)
	w.records = append(w.records, record)
	return record
}

func (w *deskWorld) line(text string) client.Record {
	return w.add(object{"text": text, "to": []string{deskLane}})
}

func (w *deskWorld) Post(_ context.Context, d client.Draft) (client.Record, error) {
	w.mu.Lock()
	if w.postError != nil {
		w.mu.Unlock()
		return client.Record{}, w.postError
	}
	if w.refuseTime && quotedClock.MatchString(d.Text) {
		w.mu.Unlock()
		return client.Record{}, errors.New("time 7:45 AM in the text is 100 minutes after the post time 6:05 AM, so it names something that has not happened yet: write the time it happened")
	}
	w.posts = append(w.posts, d)
	w.mu.Unlock()
	return w.add(object{"lane": d.Lane, "kind": d.Kind, "text": d.Text, "to": d.To, "topic": d.Topic}), nil
}

func (w *deskWorld) Tail(_ context.Context, q client.Query) ([]client.Record, error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	var found []client.Record
	for _, record := range w.records {
		if record.Seq <= q.After || (!q.Since.IsZero() && record.At.Before(q.Since)) {
			continue
		}
		if len(q.Lanes) > 0 && !slices.Contains(q.Lanes, record.Lane) {
			continue
		}
		if len(q.Kinds) > 0 && !slices.Contains(q.Kinds, string(record.Kind)) {
			continue
		}
		if q.PR != "" && !prNamed(record, q.PR) {
			continue
		}
		found = append(found, record)
	}
	return found, nil
}

func (w *deskWorld) Among(_ context.Context, _ string, seqs []int64) ([]client.Record, error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	var found []client.Record
	for _, record := range w.records {
		if slices.Contains(seqs, record.Seq) {
			found = append(found, record)
		}
	}
	return found, nil
}

func orcaOK(result any) shell.Result {
	raw, err := json.Marshal(object{"ok": true, "result": result, "_meta": object{}})
	if err != nil {
		panic(err)
	}
	return shell.Result{Stdout: string(raw)}
}

func orcaRefused(argv []string, refusal object) (shell.Result, error) {
	raw, err := json.Marshal(object{"ok": false, "error": refusal})
	if err != nil {
		panic(err)
	}
	return shell.Result{Code: 1, Stdout: string(raw)}, &shell.ExitError{Argv: argv, Code: 1}
}

func (w *deskWorld) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	w.calls = append(w.calls, c.Argv)
	argv := c.Argv
	switch {
	case argv[0] == "orca" && argv[1] == "terminal":
		return w.terminalReply(argv)
	case argv[0] == "orca":
		key := argv[1] + " " + argv[2]
		if refusal, refused := w.refusals[key]; refused {
			return orcaRefused(argv, refusal)
		}
		return w.orchestrationReply(argv)
	case argv[0] == "top":
		return shell.Result{Stdout: "CPU usage: 1% user, 1% sys, " + w.busy + "% idle\n"}, nil
	case argv[0] == "cci":
		return shell.Result{}, nil
	case argv[0] == "ccn":
		if w.ccnFails {
			return shell.Result{Code: 1, Stderr: "no log"}, &shell.ExitError{Argv: argv, Code: 1}
		}
		return shell.Result{Stdout: "[]"}, nil
	case argv[0] == "git":
		return shell.Result{Stdout: "dev\n"}, nil
	case filepath.Base(argv[0]) == "prompt_watch.py":
		return shell.Result{Stdout: `{"rows":[]}`}, nil
	}
	return shell.Result{}, nil
}

func (w *deskWorld) terminalReply(argv []string) (shell.Result, error) {
	if argv[2] == "read" {
		return orcaOK(idleTail), nil
	}
	return orcaOK(object{}), nil
}

func (w *deskWorld) orchestrationReply(argv []string) (shell.Result, error) {
	switch argv[2] {
	case "run-show":
		return orcaOK(object{"run": object{"coordinator_handle": "term_root", "consumer_generation": 3}}), nil
	case "run-current":
		return orcaOK(object{"run": object{"id": w.coordinate}}), nil
	case "run-use":
		return orcaOK(object{}), nil
	case "worker-show":
		return w.workerShow(argv)
	case "worker-stop", "worker-abandon":
		if entry := w.dispatches[flag(argv, "--dispatch")]; entry != nil {
			entry.status = statusCompleted
			entry.observation = "gone"
		}
		return orcaOK(object{"state": "stopped"}), nil
	case "worker-list":
		rows := make([]object, 0, len(w.dispatches))
		for id, entry := range w.dispatches {
			rows = append(rows, object{"dispatchId": id, "dispatchStatus": entry.status, "agentTerminalHandle": entry.terminal})
		}
		return orcaOK(object{"workers": rows}), nil
	case "check":
		return w.checkReply(argv)
	case "inbox":
		return w.inboxReply(argv)
	case "send", "reply":
		w.sentID++
		return orcaOK(object{"message": object{"id": fmt.Sprintf("msg_sent%d", w.sentID)}}), nil
	case "request-show":
		return orcaOK(object{"state": "absent"}), nil
	}
	return orcaOK(object{}), nil
}

func (w *deskWorld) workerShow(argv []string) (shell.Result, error) {
	entry := w.dispatches[flag(argv, "--dispatch")]
	if entry == nil {
		return orcaRefused(argv, object{"code": "selector_not_found", "message": "gone"})
	}
	dispatch := object{"status": entry.status}
	if entry.revoked {
		dispatch["capabilityRevokedAt"] = "2026-11-03T00:00:00Z"
	}
	return orcaOK(object{"dispatch": dispatch, "terminal": object{"handle": entry.terminal}, "observation": object{"status": entry.observation}}), nil
}

func (w *deskWorld) checkReply(argv []string) (shell.Result, error) {
	if slices.Contains(argv, "--run") {
		if slices.Contains(argv, "--ack") {
			return orcaOK(object{}), nil
		}
		return orcaOK(object{"messages": w.handed, "deliveryId": w.delivery}), nil
	}
	terminal := flag(argv, "--terminal")
	if slices.Contains(argv, "--peek") {
		return orcaOK(object{"messages": w.unread[terminal]}), nil
	}
	if terminal == "run:"+testRun {
		return orcaOK(object{"messages": w.runInbox}), nil
	}
	return orcaOK(object{"messages": w.mailboxes[terminal]}), nil
}

func (w *deskWorld) inboxReply(argv []string) (shell.Result, error) {
	messages := w.mail
	if slices.Contains(argv, "--terminal") {
		messages = w.runInbox
	}
	newestFirst := slices.Clone(messages)
	slices.SortFunc(newestFirst, func(a, b orcaMessage) int { return int(b.Sequence - a.Sequence) })
	return orcaOK(object{"messages": newestFirst}), nil
}

func (w *deskWorld) step(run func(context.Context) error) {
	w.t.Helper()
	if err := run(context.Background()); err != nil {
		w.t.Fatal(err)
	}
}

func (w *deskWorld) lines() {
	w.t.Helper()
	w.step(w.desk.readLines)
}

func (w *deskWorld) escalations() []string {
	w.t.Helper()
	actions, err := w.desk.store.Actions(deskRunner, kindEscalation)
	if err != nil {
		w.t.Fatal(err)
	}
	found := make([]string, 0, len(actions))
	for _, action := range actions {
		found = append(found, action.Target)
	}
	return found
}

func (w *deskWorld) wantEscalation(prefix string) string {
	w.t.Helper()
	for _, line := range w.escalations() {
		if strings.HasPrefix(line, prefix) {
			return line
		}
	}
	w.t.Fatalf("no escalation opens %q in %q", prefix, w.escalations())
	return ""
}

func (w *deskWorld) noEscalation(prefix string) {
	w.t.Helper()
	for _, line := range w.escalations() {
		if strings.HasPrefix(line, prefix) {
			w.t.Fatalf("unexpected escalation %q", line)
		}
	}
}

func (w *deskWorld) action(lane, key string) incident.Action {
	w.t.Helper()
	action, err := w.desk.store.Action(laneBook(lane), key)
	if err != nil {
		w.t.Fatal(err)
	}
	return action
}

func (w *deskWorld) orcaCalls(verb string) [][]string {
	var found [][]string
	for _, call := range w.calls {
		if call[0] == "orca" && len(call) > 2 && call[1]+" "+call[2] == verb {
			found = append(found, call)
		}
	}
	return found
}

func wantContains(t *testing.T, text, part string) {
	t.Helper()
	if !strings.Contains(text, part) {
		t.Fatalf("%q lacks %q", text, part)
	}
}

func TestLoadDeskConfigReadsTheRunnerFileWithItsDefaults(t *testing.T) {
	home := t.TempDir()
	path := filepath.Join(home, "runner.json")
	raw := `{"drive":"brook","store":"~/store","orca":{"desk_inbox":"~/inbox/orca-desk.md","run":"run_9","briefs":{"repo":"~/repo","log":"log_1"},"launch_env":{"A":"b"},"gc":"gc"}}`
	if err := os.WriteFile(path, []byte(raw), 0o600); err != nil {
		t.Fatal(err)
	}
	config, err := LoadDeskConfig(path, func(name string) string {
		if name == "HOME" {
			return home
		}
		return ""
	})
	if err != nil {
		t.Fatal(err)
	}
	if config.Store != filepath.Join(home, "store") || config.BriefsRepo != filepath.Join(home, "repo") || config.Run != "run_9" || config.GC != "gc" {
		t.Fatalf("%+v", config)
	}
	if config.StartMinutes != 10 || config.LaunchMinutes != 15 || config.CaseLanes != 2 {
		t.Fatalf("%+v", config)
	}
	if config.Receipts != filepath.Join(home, ".local", "state", "long-running", "orca-launch", "run_9") {
		t.Fatal(config.Receipts)
	}
}

func TestTheFirstReadSavesTheNewestRecordAndActsOnNothing(t *testing.T) {
	w := newDeskWorld(t)
	w.live(testLane, testDispatch, testTerminal)
	w.line("R1 (5:00 PM) orca-desk: relay to lane-a: old")
	w.lines()
	if exists, _ := w.desk.store.Exists(laneBook(testLane)); exists {
		t.Fatal("the first read relayed an old line")
	}
	w.line("R2 (5:01 PM) orca-desk: relay to lane-a: new")
	w.lines()
	if action := w.action(testLane, "R2"); action.Kind != kindRelay {
		t.Fatal(action)
	}
}

func TestARelayToALiveLaneIsSentOnItsThreadAndWakesTheLane(t *testing.T) {
	w := newDeskWorld(t)
	w.live(testLane, testDispatch, testTerminal)
	w.lines()
	w.line("R2 (5:01 PM) orca-desk: relay to lane-a: restack onto dev")
	w.lines()
	wantContains(t, w.wantEscalation("RELAYED R2 lane-a"), "relay to dispatch ctx_a")
	w.step(w.desk.deliver)
	sends := w.orcaCalls("orchestration send")
	if len(sends) != 1 {
		t.Fatal(w.calls)
	}
	if flag(sends[0], "--to") != "dispatch:ctx_a" || flag(sends[0], "--subject") != "R2: act R2" || flag(sends[0], "--thread-id") != "desk-lane-lane-a/R2" {
		t.Fatal(sends[0])
	}
	wantContains(t, flag(sends[0], "--body"), "restack onto dev\n\nReply on thread desk-lane-lane-a/R2: subject `started R2` before acting, then `done R2: <result>`.")
	if sent := w.action(testLane, "send:R2:ctx_a#1"); sent.Status != incident.ActionCompleted {
		t.Fatal(sent)
	}
	typed := false
	for _, call := range w.calls {
		if call[0] == "orca" && call[1] == "terminal" && call[2] == "send" {
			typed = flag(call, "--terminal") == testTerminal
		}
	}
	if !typed {
		t.Fatal("the lane was not woken")
	}
}

func TestARelayToSeveralLanesAcceptsOneActionForEach(t *testing.T) {
	w := newDeskWorld(t)
	w.lines()
	w.line("R2 (5:01 PM) orca-desk: relay to lane-a, lane-b and lane-c: go")
	w.lines()
	for _, lane := range []string{"lane-a", "lane-b", "lane-c"} {
		if action := w.action(lane, "R2"); action.Kind != kindRelay {
			t.Fatal(lane, action)
		}
	}
}

func TestAMalformedRelayIsRefusedAndRelaysNothing(t *testing.T) {
	w := newDeskWorld(t)
	w.lines()
	w.line("R2 (5:01 PM) orca-desk: relay lane-a go")
	w.lines()
	wantContains(t, w.wantEscalation("RELAY-FAILED R2 inbox"), relayGrammar)
}

func TestTwoRelaysOnOneLineAreRefused(t *testing.T) {
	w := newDeskWorld(t)
	w.lines()
	w.line("R2 (5:01 PM) orca-desk: relay to lane-a: go; relay to lane-b: stop")
	w.lines()
	w.wantEscalation("RELAY-FAILED R2 inbox")
}

func TestAClippedRelayCarriesTheWholeFile(t *testing.T) {
	w := newDeskWorld(t)
	w.lines()
	whole := "R2 (5:01 PM) orca-desk: relay to lane-a: " + strings.Repeat("word ", 200) + "end"
	path := filepath.Join(w.home, "whole.txt")
	w.write(path, whole)
	w.add(object{"text": clipped(whole, textLimit), "to": []string{deskLane}, "refs": object{"path": path}})
	w.lines()
	held, err := relayOf(w.action(testLane, "R2"))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasSuffix(held.Text, "end") {
		t.Fatal(held.Text)
	}
}

func TestARelayWhoseFileIsGoneFails(t *testing.T) {
	w := newDeskWorld(t)
	w.lines()
	w.add(object{"text": "R2 orca-desk: relay to lane-a: x…", "to": []string{deskLane}, "refs": object{"path": filepath.Join(w.home, "gone.txt")}})
	w.lines()
	w.wantEscalation("RELAY-FAILED R2 inbox: the record's full text")
}

func TestARecordToTheDeskThatIsNoLineIsAnswered(t *testing.T) {
	w := newDeskWorld(t)
	w.lines()
	w.line("please launch something")
	w.lines()
	wantContains(t, w.wantEscalation("DIRECTIVE-FAILED cci#1 inbox"), "it opens `please launch something`")
}

func TestARecordNotToTheDeskDoesNothing(t *testing.T) {
	w := newDeskWorld(t)
	w.lines()
	w.add(object{"text": "orca-desk: relay to lane-a: quoted", "to": []string{"lane-b"}})
	w.lines()
	if got := w.escalations(); len(got) != 0 {
		t.Fatal(got)
	}
	if exists, _ := w.desk.store.Exists(laneBook(testLane)); exists {
		t.Fatal("a quoted line relayed")
	}
}

func launchText(lane, brief string) string {
	return "orca-desk: launch " + lane + " opus xhigh brief=" + brief
}

func (w *deskWorld) launched(lane, dispatch, terminal string) string {
	return fmt.Sprintf("%s ready task=task_a dispatch=%s terminal=%s worktree=/w/%s\n", lane, dispatch, terminal, lane)
}

func TestALaunchVerdictReachesTheMetaLaneThatLaunchedTheLaneAndADriveLanesReachesRoot(t *testing.T) {
	for _, tt := range []struct {
		name, by, want string
	}{
		{"launched by the meta desk", "meta-desk-2", "meta-desk-2"},
		{"launched by root", coordinatorLane, coordinatorLane},
	} {
		t.Run(tt.name, func(t *testing.T) {
			w := newDeskWorld(t)
			w.register(store.Lane{Name: "meta-desk-2", Role: lanes.RoleMeta, Launcher: lanes.OwnerRelay})
			w.register(store.Lane{Name: testLane, Role: "lane", Launcher: tt.by})
			brief := w.brief(testLane, testBrief+"ccx: place=local\n")
			w.lines()
			line := w.add(object{"lane": tt.by, "text": launchText(testLane, brief), "to": []string{deskLane}})
			key := "R" + strings.TrimPrefix(line.Name(), "#")
			w.launchOut[testLane] = w.launched(testLane, testDispatch, testTerminal)
			w.lines()
			w.step(w.desk.deliver)
			w.step(w.desk.reap)
			w.step(w.desk.flush)

			if launched := w.postOpening("LAUNCHED " + key + " " + testLane); !slices.Equal(launched.To, []string{tt.want}) {
				t.Fatal(launched)
			}
		})
	}
}

func TestALaunchIsStartedThroughLrLaunchStartAndSettlesFromItsLine(t *testing.T) {
	w := newDeskWorld(t)
	brief := w.brief(testLane, testBrief+"ccx: place=local\n")
	w.lines()
	line := w.add(object{"lane": "meta-desk-1", "text": launchText(testLane, brief), "to": []string{deskLane}})
	key := "R" + strings.TrimPrefix(line.Name(), "#")
	w.launchOut[testLane] = w.launched(testLane, testDispatch, testTerminal)
	w.lines()
	w.step(w.desk.deliver)
	if len(w.spawns) != 1 {
		t.Fatal(w.spawns)
	}
	spawn := w.spawns[0]
	want := []string{w.desk.tool("lr"), "launch", "start", testLane, "opus", "xhigh", brief}
	if !slices.Equal(spawn.argv, want) {
		t.Fatal(spawn.argv)
	}
	for _, pair := range []string{"ORCA_LAUNCH_STATE=" + w.desk.Config.Receipts, "ORCA_TERMINAL_HANDLE=term_root", "ORCA_PANE_KEY=", "CLAUDE_LONG_RUNNING_LANE=meta-desk-1"} {
		if !slices.Contains(spawn.env, pair) {
			t.Fatalf("%q lacks %q", spawn.env, pair)
		}
	}
	if action := w.action(testLane, key); action.Status != incident.ActionStarted {
		t.Fatal(action)
	}
	w.step(w.desk.reap)
	action := w.action(testLane, key)
	if action.Status != incident.ActionVerified || string(action.DispatchID) != testDispatch {
		t.Fatal(action)
	}
	wantContains(t, w.wantEscalation("LAUNCHED "+key+" lane-a"), "dispatch ctx_a terminal term_a")
	header, err := w.desk.store.Header(laneBook(testLane))
	if err != nil {
		t.Fatal(err)
	}
	if header.Owner != unlaunched {
		t.Fatal(header)
	}
}

func TestALaunchThatPrintsAFailureFailsWithItsLine(t *testing.T) {
	w := newDeskWorld(t)
	brief := w.brief(testLane, testBrief+"ccx: place=local\n")
	w.lines()
	line := w.line(launchText(testLane, brief))
	key := "R" + strings.TrimPrefix(line.Name(), "#")
	w.launchOut[testLane] = "booting\nlane-a failed worker-start terminal=: refused\n"
	w.lines()
	w.step(w.desk.deliver)
	w.step(w.desk.reap)
	if action := w.action(testLane, key); action.Status != incident.ActionFailed {
		t.Fatal(action)
	}
	wantContains(t, w.wantEscalation("LAUNCH-FAILED "), "lane-a failed worker-start terminal=: refused")
}

func TestALaunchWithNoLineAndNoReceiptIsUnverifiableAndNeverRelaunched(t *testing.T) {
	w := newDeskWorld(t)
	brief := w.brief(testLane, testBrief+"ccx: place=local\n")
	w.lines()
	line := w.line(launchText(testLane, brief))
	key := "R" + strings.TrimPrefix(line.Name(), "#")
	w.lines()
	w.step(w.desk.deliver)
	w.step(w.desk.reap)
	if action := w.action(testLane, key); action.Status != incident.ActionUnverifiable {
		t.Fatal(action)
	}
	w.wantEscalation("UNVERIFIABLE ")
	w.step(w.desk.deliver)
	if len(w.spawns) != 1 {
		t.Fatal(w.spawns)
	}
}

func TestALaunchStillRunningIsNotSettled(t *testing.T) {
	w := newDeskWorld(t)
	brief := w.brief(testLane, testBrief+"ccx: place=local\n")
	w.process = &fakeProcess{}
	w.lines()
	line := w.line(launchText(testLane, brief))
	key := "R" + strings.TrimPrefix(line.Name(), "#")
	w.lines()
	w.step(w.desk.deliver)
	w.step(w.desk.reap)
	if action := w.action(testLane, key); action.Status != incident.ActionStarted {
		t.Fatal(action)
	}
}

func TestAnUnknownModelOrEffortRefusesTheLaunch(t *testing.T) {
	w := newDeskWorld(t)
	brief := w.brief(testLane, testBrief)
	w.lines()
	w.line("orca-desk: launch lane-a gemini extreme brief=" + brief)
	w.lines()
	line := w.wantEscalation("LAUNCH-FAILED ")
	wantContains(t, line, "gemini is not a model")
	wantContains(t, line, "extreme is not an effort")
	wantContains(t, line, "nothing was launched")
}

func TestALaunchOfALiveLaneIsRefused(t *testing.T) {
	w := newDeskWorld(t)
	brief := w.brief(testLane, testBrief)
	w.live(testLane, testDispatch, testTerminal)
	w.lines()
	w.line(launchText(testLane, brief))
	w.lines()
	wantContains(t, w.wantEscalation("LAUNCH-FAILED "), "dispatch ctx_a is running; relay to it instead")
}

func TestALaunchOnAnotherLanesBriefIsRefused(t *testing.T) {
	w := newDeskWorld(t)
	brief := w.brief("other", testBrief)
	w.lines()
	w.line(launchText(testLane, brief))
	w.lines()
	wantContains(t, w.wantEscalation("LAUNCH-FAILED "), "is lane other's")
}

func TestALaunchLinePastItsWindowExpires(t *testing.T) {
	w := newDeskWorld(t)
	brief := w.brief(testLane, testBrief)
	w.lines()
	w.line(launchText(testLane, brief))
	w.now = w.now.Add(20 * time.Minute)
	w.desk.passSince = w.now
	w.lines()
	line := w.wantEscalation("LAUNCH-EXPIRED ")
	wantContains(t, line, "20 minutes before this read")
	wantContains(t, line, "a launch line is valid for 15")
}

func TestASlowPassWidensTheLaunchWindow(t *testing.T) {
	w := newDeskWorld(t)
	brief := w.brief(testLane, testBrief+"ccx: place=local\n")
	w.lines()
	w.line(launchText(testLane, brief))
	w.now = w.now.Add(20 * time.Minute)
	w.desk.passTook = []time.Duration{25 * time.Minute}
	w.lines()
	w.noEscalation("LAUNCH-EXPIRED ")
}

func TestALaunchIsRefusedWhileTheRunnerIsUnbound(t *testing.T) {
	w := newDeskWorld(t)
	brief := w.brief(testLane, testBrief+"ccx: place=local\n")
	w.coordinate = "run_other"
	w.lines()
	w.line(launchText(testLane, brief))
	w.lines()
	w.step(w.desk.deliver)
	if len(w.spawns) != 0 {
		t.Fatal(w.spawns)
	}
	wantContains(t, w.wantEscalation("UNBOUND "), "coordinates run_other, not run_1")
	wantContains(t, w.wantEscalation("LAUNCH-FAILED "), "an unbound runner")
}

func TestABriefWithNoSliceOwesOneAndAScaleLessBriefStillLaunches(t *testing.T) {
	w := newDeskWorld(t)
	brief := w.brief(testLane, "Register slice: lane=lane-a step=all\nccx: place=local\n")
	w.lines()
	w.line(launchText(testLane, brief))
	w.lines()
	wantContains(t, w.wantEscalation("SCALE-MISSING "), "lane-a launches anyway")
	w.step(w.desk.deliver)
	if len(w.spawns) != 1 {
		t.Fatal(w.spawns)
	}
}

func TestASliceThatDoesNotRefreshLogsOneAndNeverHoldsTheLaunch(t *testing.T) {
	w := newDeskWorld(t)
	brief := w.brief(testLane, testBrief+"ccx: place=local\n")
	w.desk.Runner = runnerFunc(func(ctx context.Context, c shell.Command) (shell.Result, error) {
		if filepath.Base(c.Argv[0]) == "rulings.py" {
			return shell.Result{Code: 2, Stderr: "no register\n"}, &shell.ExitError{Argv: c.Argv, Code: 2}
		}
		return w.Run(ctx, c)
	})
	w.lines()
	w.line(launchText(testLane, brief))
	w.lines()
	wantContains(t, w.wantEscalation("SLICE-UNREAD "), "exited 2: no register")
	w.step(w.desk.deliver)
	if len(w.spawns) != 1 {
		t.Fatal(w.spawns)
	}
}

type runnerFunc func(context.Context, shell.Command) (shell.Result, error)

func (f runnerFunc) Run(ctx context.Context, c shell.Command) (shell.Result, error) {
	return f(ctx, c)
}

func TestWithdrawingALaunchNotYetStartedFailsIt(t *testing.T) {
	w := newDeskWorld(t)
	brief := w.brief(testLane, testBrief+"ccx: place=local\n")
	w.lines()
	line := w.line(launchText(testLane, brief))
	key := "R" + strings.TrimPrefix(line.Name(), "#")
	w.lines()
	w.line("R9 (5:02 PM) orca-desk: withdraw " + key)
	w.lines()
	if action := w.action(testLane, key); action.Status != incident.ActionFailed {
		t.Fatal(action)
	}
	w.wantEscalation("WITHDRAWN " + key + " lane-a: by R9; nothing was launched")
	w.step(w.desk.deliver)
	if len(w.spawns) != 0 {
		t.Fatal(w.spawns)
	}
}

func TestALaunchWithdrawnWhileStartingIsStoppedOnceItLogsLaunched(t *testing.T) {
	w := newDeskWorld(t)
	brief := w.brief(testLane, testBrief+"ccx: place=local\n")
	w.process = &fakeProcess{}
	w.lines()
	line := w.line(launchText(testLane, brief))
	key := "R" + strings.TrimPrefix(line.Name(), "#")
	w.launchOut[testLane] = w.launched(testLane, testDispatch, testTerminal)
	w.lines()
	w.step(w.desk.deliver)
	w.line("R9 (5:02 PM) orca-desk: withdraw " + key)
	w.lines()
	w.wantEscalation("WITHDRAW-PENDING " + key)
	w.process.exited = true
	w.dispatches[testDispatch] = &fakeDispatch{status: "running", terminal: testTerminal, observation: "live"}
	w.step(w.desk.reap)
	if len(w.orcaCalls("orchestration worker-stop")) != 1 {
		t.Fatal(w.calls)
	}
	w.wantEscalation("WITHDRAWN " + key + " lane-a: by R9; dispatch ctx_a stopped (stopped)")
	by, err := w.desk.withdrawnBy(testLane)
	if err != nil {
		t.Fatal(err)
	}
	if by != "" {
		t.Fatal("no receipt names ctx_a yet, so the mark reads as another dispatch's", by)
	}
}

func TestWithdrawingALaneStopsItsDispatchAndMarksItWithdrawn(t *testing.T) {
	w := newDeskWorld(t)
	w.live(testLane, testDispatch, testTerminal)
	w.lines()
	w.line("R9 (5:02 PM) orca-desk: withdraw lane-a")
	w.lines()
	w.wantEscalation("WITHDRAWN lane-a lane-a: by R9; dispatch ctx_a stopped")
	by, err := w.desk.withdrawnBy(testLane)
	if err != nil || by != "R9" {
		t.Fatal(by, err)
	}
}

func TestWithdrawingWhatNobodyHoldsFails(t *testing.T) {
	w := newDeskWorld(t)
	w.lines()
	w.line("R9 (5:02 PM) orca-desk: withdraw nobody")
	w.lines()
	w.wantEscalation("WITHDRAW-FAILED R9 nobody")
}

func TestAStopOrcaNeverConfirmsIsReported(t *testing.T) {
	w := newDeskWorld(t)
	w.live(testLane, testDispatch, testTerminal)
	w.desk.Runner = runnerFunc(func(ctx context.Context, c shell.Command) (shell.Result, error) {
		if c.Argv[0] == "orca" && c.Argv[2] == "worker-stop" {
			return orcaOK(object{"state": "stopping"}), nil
		}
		return w.Run(ctx, c)
	})
	w.lines()
	w.line("R9 (5:02 PM) orca-desk: withdraw lane-a")
	w.lines()
	wantContains(t, w.wantEscalation("WITHDRAW-UNCONFIRMED R9 lane-a"), "dispatch ctx_a still running on terminal term_a")
}

func TestAHoldIsKeptOnceAndAnUnholdLiftsIt(t *testing.T) {
	w := newDeskWorld(t)
	w.lines()
	w.line("R3 (5:02 PM) orca-desk: hold sandsql-5177 owner=root :: sand releases")
	w.lines()
	w.line("R4 (5:02 PM) orca-desk: hold sandsql-5177 owner=root :: again")
	w.lines()
	held, err := w.desk.store.Actions(deskRunner, kindHold)
	if err != nil || len(held) != 1 {
		t.Fatal(held, err)
	}
	w.line("R5 (5:02 PM) orca-desk: unhold sandsql-5177")
	w.lines()
	held, err = w.desk.store.Actions(deskRunner, kindHold, incident.ActionAccepted)
	if err != nil || len(held) != 0 {
		t.Fatal(held, err)
	}
}

func TestAMalformedHoldStartsNoClock(t *testing.T) {
	w := newDeskWorld(t)
	w.lines()
	w.line("R3 (5:02 PM) orca-desk: hold Bad Slug")
	w.lines()
	wantContains(t, w.wantEscalation("HOLD-FAILED R3 inbox"), holdGrammar)
}

func TestAnAlertLaunchesNothingAndARepeatGoesToTheFixLane(t *testing.T) {
	w := newDeskWorld(t)
	w.lines()
	w.line("R3 (5:02 PM) orca-desk: alert api-500 https://dd/1 :: errors")
	w.lines()
	w.wantEscalation("ALERT api-500: errors https://dd/1 | no lane launched")
	w.live("api-500-fix", "ctx_fix", "term_fix")
	w.line("R4 (5:03 PM) orca-desk: alert api-500 https://dd/1 :: errors")
	w.lines()
	held, err := relayOf(w.action("api-500-fix", "R4:again"))
	if err != nil {
		t.Fatal(err)
	}
	wantContains(t, held.Text, "The alert fired again: errors https://dd/1")
}

func TestAnIncidentLineCallsTheIncidentOwner(t *testing.T) {
	w := newDeskWorld(t)
	w.lines()
	w.line("orca-desk: incident api-500 https://dd/1 reported=5:02 PM :: errors")
	w.lines()
	w.desk.background.Wait()
	if len(w.fired) != 1 || w.fired[0].Slug != "api-500" || w.fired[0].Link != "https://dd/1" || w.fired[0].What != "errors" {
		t.Fatal(w.fired)
	}
	w.wantEscalation("INCIDENT api-500")
}

func TestAnIncidentLineNamingNoReportTimeLaunchesAndPostsItsIncidentWithNoClock(t *testing.T) {
	w := newDeskWorld(t)
	w.lines()
	w.line("orca-desk: incident api-500 https://dd/1 :: errors")
	w.lines()
	w.desk.background.Wait()
	if len(w.fired) != 1 || w.fired[0].Slug != "api-500" || w.fired[0].Link != "https://dd/1" || w.fired[0].What != "errors" {
		t.Fatal(w.fired)
	}
	w.step(w.desk.flush)
	var posted []client.Draft
	for _, post := range w.posts {
		if post.Kind == "incident" {
			posted = append(posted, post)
		}
	}
	if len(posted) != 1 || posted[0].Topic != "api-500" || !strings.HasPrefix(posted[0].Text, "INCIDENT api-500: errors https://dd/1 | fix lane api-500-fix launching") {
		t.Fatal(w.posts)
	}
}

func TestAnIncidentLaunchThatFailsIsReported(t *testing.T) {
	w := newDeskWorld(t)
	w.fireErr = errors.New("no brief")
	w.lines()
	w.line("orca-desk: incident api-500 https://dd/1 reported=17:02 :: errors")
	w.lines()
	w.desk.background.Wait()
	wantContains(t, strings.Join(w.escalations(), "\n"), "api-500-fix not launched: no brief")
}

func TestAnIncidentLineOnALiveFixLaneRelaysTheRepeat(t *testing.T) {
	w := newDeskWorld(t)
	w.live("api-500-fix", "ctx_fix", "term_fix")
	w.lines()
	w.line("orca-desk: incident api-500 https://dd/1 reported=17:02 :: errors")
	w.lines()
	w.desk.background.Wait()
	if len(w.fired) != 0 {
		t.Fatal(w.fired)
	}
}

func TestAnIncidentAnotherLaneHoldsOnItsMonitorIsSkipped(t *testing.T) {
	w := newDeskWorld(t)
	other := w.brief("other-fix", testBrief+"ccx: incident=338724738\n")
	if _, _, err := w.desk.acceptLaunch("R1", "other-fix", launchTarget{Model: "opus", Effort: "xhigh", Brief: other}); err != nil {
		t.Fatal(err)
	}
	w.lines()
	w.line("orca-desk: incident api-500 https://x/monitors/338724738 reported=17:02 :: errors")
	w.lines()
	w.desk.background.Wait()
	if len(w.fired) != 0 {
		t.Fatal(w.fired)
	}
	w.wantEscalation("LAUNCH-SKIPPED ")
}

func TestAnIncidentTheRootClosedIsOwned(t *testing.T) {
	w := newDeskWorld(t)
	w.lines()
	opened := w.add(object{"kind": "incident", "topic": "api-500", "text": "api-500 opened", "lane": "alerts-desk"})
	w.add(object{"kind": "done", "text": "handled", "resolves": opened.Seq})
	w.line("orca-desk: incident api-500 https://dd/1 reported=17:02 :: errors")
	w.lines()
	w.desk.background.Wait()
	if len(w.fired) != 0 {
		t.Fatal(w.fired)
	}
	w.wantEscalation("INCIDENT-OWNED ")
}

func question(id, from string) orcaMessage {
	return orcaMessage{ID: id, Type: typeQuestion, FromHandle: from, Subject: "which way", Body: "left or right", CreatedAt: "2026-11-03T08:00:00Z"}
}

func TestAPlainRelayToALaneWithAnOpenQuestionAnswersIt(t *testing.T) {
	w := newDeskWorld(t)
	w.live(testLane, testDispatch, testTerminal)
	w.runInbox = []orcaMessage{question("msg_q", testTerminal)}
	w.lines()
	w.line("R2 (5:01 PM) orca-desk: relay to lane-a: left")
	w.lines()
	action := w.action(testLane, "R2")
	if action.Kind != kindReply {
		t.Fatal(action)
	}
	w.step(w.desk.deliver)
	replies := w.orcaCalls("orchestration reply")
	if len(replies) != 1 || flag(replies[0], "--id") != "msg_q" || flag(replies[0], "--body") != "left\n\n(R2)" {
		t.Fatal(w.calls)
	}
	if action := w.action(testLane, "R2"); action.Status != incident.ActionCompleted {
		t.Fatal(action)
	}
}

func TestARootReplyToALanesRecordAnswersItsOpenQuestion(t *testing.T) {
	w := newDeskWorld(t)
	w.live(testLane, testDispatch, testTerminal)
	w.runInbox = []orcaMessage{question("msg_q", testTerminal)}
	asked := w.add(object{"lane": testLane, "kind": "ask", "text": "which way"})
	w.lines()
	reply := w.add(object{"kind": "answer", "text": "left", "re": asked.Seq})
	w.lines()
	action := w.action(testLane, fmt.Sprintf("cci#%d", reply.Seq))
	held, err := relayOf(action)
	if err != nil {
		t.Fatal(err)
	}
	if action.Kind != kindReply || held.ReplyTo != "msg_q" {
		t.Fatal(action)
	}
	wantContains(t, held.Text, fmt.Sprintf("The root answered on cci #%d (re #%d)", reply.Seq, asked.Seq))
}

func TestARootResolveOfTheRunnersDecideAnswersThatQuestion(t *testing.T) {
	w := newDeskWorld(t)
	w.live(testLane, testDispatch, testTerminal)
	decide := w.add(object{"lane": deskRunner, "kind": "decide", "text": "DECIDE msg_q lane-a: which way"})
	w.lines()
	reply := w.add(object{"kind": "answer", "text": "left", "resolves": decide.Seq})
	w.lines()
	held, err := relayOf(w.action(testLane, fmt.Sprintf("cci#%d", reply.Seq)))
	if err != nil {
		t.Fatal(err)
	}
	if held.ReplyTo != "msg_q" || held.Text != "left" {
		t.Fatal(held)
	}
}

func TestARootWithdrawOfALaunchRecordWithdrawsIt(t *testing.T) {
	w := newDeskWorld(t)
	brief := w.brief(testLane, testBrief+"ccx: place=local\n")
	w.lines()
	line := w.line(launchText(testLane, brief))
	key := "R" + strings.TrimPrefix(line.Name(), "#")
	w.lines()
	w.add(object{"kind": "withdraw", "text": "no", "re": line.Seq})
	w.lines()
	if action := w.action(testLane, key); action.Status != incident.ActionFailed {
		t.Fatal(action)
	}
}

func TestAReplyToTheDeskThatIsNoLineNorAnAnswerIsRefused(t *testing.T) {
	w := newDeskWorld(t)
	other := w.add(object{"lane": "lane-b", "kind": "note", "text": "hi"})
	w.lines()
	w.add(object{"kind": "answer", "text": "thanks", "re": other.Seq, "to": []string{deskLane}})
	w.lines()
	w.wantEscalation("DIRECTIVE-FAILED ")
}

func TestARelayToALaneWithNoDispatchGoesOnCCI(t *testing.T) {
	w := newDeskWorld(t)
	w.lines()
	w.line("R2 (5:01 PM) orca-desk: relay to lane-a: go")
	w.lines()
	w.step(w.desk.deliver)
	var note *client.Draft
	for i := range w.posts {
		if w.posts[i].Lane == deskRunner && w.posts[i].Kind == kindNote {
			note = &w.posts[i]
		}
	}
	if note == nil || note.Text != "go" || !slices.Equal(note.To, []string{testLane}) {
		t.Fatal(w.posts)
	}
	wantContains(t, w.wantEscalation("RELAYED R2 lane-a"), "no live dispatch, so it went to the lane on cci as #")
	if action := w.action(testLane, "R2"); action.Status != incident.ActionCompleted {
		t.Fatal(action)
	}
}

func TestARelayAboutAPRToALaneWithNoDispatchIsABlockerAndLogsNoRelayed(t *testing.T) {
	w := newDeskWorld(t)
	container, err := w.desk.lane(testLane)
	if err != nil {
		t.Fatal(err)
	}
	target, err := compactJSON(relayTarget{Text: "#12 is red", PR: "12"})
	if err != nil {
		t.Fatal(err)
	}
	if _, _, err := w.desk.accept(container, "blocker:12:abc", kindRelay, target, "blocker:12:abc", zeroTime); err != nil {
		t.Fatal(err)
	}
	w.step(w.desk.deliver)
	last := w.posts[len(w.posts)-1]
	if last.Kind != kindBlocker || !slices.Equal(last.PRs, []string{"12"}) || last.Topic != "12" {
		t.Fatal(last)
	}
	w.noEscalation("RELAYED ")
}

func TestARelayToAStoodDownLaneFailsQuietly(t *testing.T) {
	w := newDeskWorld(t)
	w.lines()
	w.line("R2 (5:01 PM) orca-desk: relay to lane-a: go")
	w.lines()
	stop := w.add(object{"lane": supervisorLane, "kind": kindStandDown, "text": "retired", "to": []string{testLane}})
	w.step(w.desk.deliver)
	action := w.action(testLane, "R2")
	if action.Status != incident.ActionFailed || string(action.Reason) != fmt.Sprintf("stood down by #%d", stop.Seq) {
		t.Fatal(action)
	}
	told, err := w.desk.store.Action(deskRunner, "escalation:relay:"+laneBook(testLane)+"/R2")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(told.Target, fmt.Sprintf("RELAY-FAILED R2 %s: stood down by #%d", testLane, stop.Seq)) {
		t.Fatal(told)
	}
}

func TestALaneCopiedOnAnotherLanesStandDownStillTakesItsRelay(t *testing.T) {
	w := newDeskWorld(t)
	w.lines()
	w.line("R2 (5:01 PM) orca-desk: relay to lane-a: go")
	w.lines()
	w.add(object{"lane": supervisorLane, "kind": kindStandDown, "text": "retired", "to": []string{"another-lane", testLane}})
	w.step(w.desk.deliver)
	if action := w.action(testLane, "R2"); action.Status == incident.ActionFailed {
		t.Fatal(action)
	}
}

func TestARelayWaitsForALaunchInFlight(t *testing.T) {
	w := newDeskWorld(t)
	brief := w.brief(testLane, testBrief+"ccx: place=local\n")
	w.process = &fakeProcess{}
	w.lines()
	w.line(launchText(testLane, brief))
	w.line("R2 (5:01 PM) orca-desk: relay to lane-a: go")
	w.lines()
	w.step(w.desk.deliver)
	if action := w.action(testLane, "R2"); action.Status != incident.ActionAccepted {
		t.Fatal(action)
	}
}

func TestANewerRelayAboutOnePRFoldsIntoTheUnansweredOne(t *testing.T) {
	w := newDeskWorld(t)
	w.live(testLane, testDispatch, testTerminal)
	container, err := w.desk.lane(testLane)
	if err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{"blocker:12:abc", "blocker:12:def"} {
		target, err := compactJSON(relayTarget{Text: key})
		if err != nil {
			t.Fatal(err)
		}
		if _, _, err := w.desk.accept(container, key, kindRelay, target, key, zeroTime); err != nil {
			t.Fatal(err)
		}
		w.step(w.desk.deliver)
	}
	if sends := w.orcaCalls("orchestration send"); len(sends) != 1 {
		t.Fatal(sends)
	}
	action := w.action(testLane, "blocker:12:def")
	if action.Status != incident.ActionCompleted {
		t.Fatal(action)
	}
	wantContains(t, fmt.Sprint(action.Response["text"]), "folded into blocker:12:abc")
}

func (w *deskWorld) acceptedRelay(key string) string {
	w.t.Helper()
	container, err := w.desk.lane(testLane)
	if err != nil {
		w.t.Fatal(err)
	}
	if _, _, err := w.desk.acceptRelay(key, testLane, "go", "", 10); err != nil {
		w.t.Fatal(err)
	}
	return container
}

func TestASendOrcaRefusesIsRetriedThenEscalated(t *testing.T) {
	w := newDeskWorld(t)
	w.live(testLane, testDispatch, testTerminal)
	w.acceptedRelay("R2")
	w.refusals["orchestration send"] = object{"code": "bad_request", "message": "no"}
	for range sendAttempts + 1 {
		w.step(w.desk.deliver)
	}
	if sends := w.orcaCalls("orchestration send"); len(sends) != sendAttempts {
		t.Fatal(sends)
	}
	wantContains(t, w.wantEscalation("SEND-FAILED desk-lane-lane-a/send:R2:ctx_a"), "failed 3 times")
}

func TestASendFencedAsInactiveFallsBackToCCI(t *testing.T) {
	w := newDeskWorld(t)
	w.live(testLane, testDispatch, testTerminal)
	w.acceptedRelay("R2")
	w.refusals["orchestration send"] = object{"code": CodeFenced, "message": "fenced"}
	w.step(w.desk.deliver)
	wantContains(t, w.wantEscalation("RELAYED R2 lane-a"), "no live dispatch (ctx_a is not active), so it went to the lane on cci")
}

func TestAReplyWhoseQuestionAlreadyHoldsAnAnswerGoesAsAMessage(t *testing.T) {
	w := newDeskWorld(t)
	w.live(testLane, testDispatch, testTerminal)
	if _, _, err := w.desk.acceptRelay("R2", testLane, "left", "msg_q", 10); err != nil {
		t.Fatal(err)
	}
	w.refusals["orchestration reply"] = object{"code": codeAnswerConflict, "message": "answered"}
	w.step(w.desk.deliver)
	sends := w.orcaCalls("orchestration send")
	if len(sends) != 1 || flag(sends[0], "--subject") != "R2: on answered question msg_q" {
		t.Fatal(w.calls)
	}
	if action := w.action(testLane, "R2"); action.Status != incident.ActionCompleted {
		t.Fatal(action)
	}
}

func TestALostSendResendsOnTheSameThreadUpToTheLimitThenReportsUnacked(t *testing.T) {
	w := newDeskWorld(t)
	w.live(testLane, testDispatch, testTerminal)
	w.acceptedRelay("R2")
	w.refusals["orchestration send"] = object{"code": "runtime_timeout", "message": "lost"}
	w.step(w.desk.deliver)
	if sent := w.action(testLane, "send:R2:ctx_a#1"); sent.Status != incident.ActionUnverifiable {
		t.Fatal(sent)
	}
	for range resendLimit + 1 {
		w.now = w.now.Add(resendAfter)
		w.step(w.desk.deliver)
	}
	if sends := w.orcaCalls("orchestration send"); len(sends) != resendLimit+1 {
		t.Fatal(sends)
	}
	line := w.wantEscalation("UNACKED R2 lane-a")
	wantContains(t, line, "2 resends lost their response")
	if kept := readBrief(w.desk.messageFile("R2")); !strings.Contains(kept, "go") {
		t.Fatal(kept)
	}
}

func TestALostSendFoundInTheMailboxIsSettled(t *testing.T) {
	w := newDeskWorld(t)
	w.live(testLane, testDispatch, testTerminal)
	w.acceptedRelay("R2")
	w.refusals["orchestration send"] = object{"code": "runtime_timeout", "message": "lost"}
	w.step(w.desk.deliver)
	delete(w.refusals, "orchestration send")
	w.mailboxes[testTerminal] = []orcaMessage{{ID: "msg_found", ThreadID: "desk-lane-lane-a/R2", CreatedAt: "2026-11-03T08:00:00Z"}}
	w.step(w.desk.deliver)
	sent := w.action(testLane, "send:R2:ctx_a#1")
	if sent.Status != incident.ActionCompleted || sent.Response["message"] != "msg_found" {
		t.Fatal(sent)
	}
}

func TestALostSendToAGoneDispatchIsUnverifiableAndNotResent(t *testing.T) {
	w := newDeskWorld(t)
	w.live(testLane, testDispatch, testTerminal)
	w.acceptedRelay("R2")
	w.refusals["orchestration send"] = object{"code": "runtime_timeout", "message": "lost"}
	w.step(w.desk.deliver)
	delete(w.dispatches, testDispatch)
	w.step(w.desk.deliver)
	w.wantEscalation("UNVERIFIABLE desk-lane-lane-a/send:R2:ctx_a#1")
}

func TestASendStartedAndOrphanedIsLost(t *testing.T) {
	w := newDeskWorld(t)
	w.live(testLane, testDispatch, testTerminal)
	container := w.acceptedRelay("R2")
	if _, _, err := w.desk.accept(container, "send:R2:ctx_a#1", kindSend, testDispatch, "R2", zeroTime); err != nil {
		t.Fatal(err)
	}
	if err := w.desk.store.Edit(container, func(tx *incident.Tx) error {
		_, err := tx.StartBy("send:R2:ctx_a#1", w.now, zeroTime, testDispatch)
		return err
	}); err != nil {
		t.Fatal(err)
	}
	w.now = w.now.Add(orphanedSend + time.Second)
	w.step(func(ctx context.Context) error { return w.desk.reconcileSends(ctx, container, testLane) })
	if sent := w.action(testLane, "send:R2:ctx_a#1"); sent.Status == incident.ActionStarted {
		t.Fatal(sent)
	}
}

const deskToolPin = `{"kind":"binary","name":"ccx","url":"https://example.test/ccx.tgz","sha256":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","version":"0.91.68"}`

func spriteConfig(w *deskWorld) string {
	launcher := filepath.Join(w.home, "repo", "worker-launch.sh")
	w.write(w.desk.Config.Source, fmt.Sprintf(`{"drive":"brook","orca":{"run":"run_1","sprite":{"launcher":%q,"env":{"WORKER_RELAY_SPONSOR":"sponsor-1"},"tools":[%s]}}}`, launcher, deskToolPin))
	return launcher
}

func TestALaunchThatMayLeaveTheMacStartsOnASpriteAndSettlesFromItsReceipt(t *testing.T) {
	w := newDeskWorld(t)
	launcher := spriteConfig(w)
	brief := w.brief(testLane, testBrief)
	w.lines()
	line := w.line(launchText(testLane, brief))
	key := "R" + strings.TrimPrefix(line.Name(), "#")
	ready := `{"task":"t","dispatch":"ctx_s","terminal":"term_s","worktree":"/box/w","workspace":"pool-1"}` + "\n"
	w.launchOut[testLane] = ready
	w.lines()
	w.step(w.desk.deliver)
	if len(w.spawns) != 1 || w.spawns[0].argv[0] != launcher || w.spawns[0].argv[2] != "claude-opus-5-5" {
		t.Fatal(w.spawns)
	}
	if !slices.Contains(w.spawns[0].env, "WORKER_RELAY_SPONSOR=sponsor-1") || !slices.Contains(w.spawns[0].env, "WORKER_RUN="+testRun) || !slices.Contains(w.spawns[0].env, "WORKER_LOCAL=") || !slices.Contains(w.spawns[0].env, "WORKER_TOOLS=["+deskToolPin+"]") {
		t.Fatal(w.spawns[0].env)
	}
	attempt := spriteAttempt(brief)
	w.write(filepath.Join(attempt, spriteReceipt), `{"result":{"dispatchId":"ctx_s"}}`)
	w.write(filepath.Join(attempt, spriteMade), `{"workspace":"pool-1"}`)
	w.step(w.desk.reap)
	action := w.action(testLane, key)
	if action.Status != incident.ActionVerified || string(action.DispatchID) != "ctx_s" {
		t.Fatal(action)
	}
	wantContains(t, w.wantEscalation("LAUNCHED "+key), "on Sprite pool-1")
	if w.desk.receipt(testLane) != "ctx_s" || w.desk.terminal(testLane) != "term_s" {
		t.Fatal("the receipts were not written")
	}
}

const twoDrifts = `{"workspace":"pool-1","captainHookDrift":[{"hook":"hooks.merge_guard","error":"ImportError: cannot import name 'gate'"},{"hook":"hooks.slack_guard","error":"ModuleNotFoundError: No module named 'captain_hook.llm'"}]}`

func (w *deskWorld) settleOnSprite(lane, allocation string) string {
	w.t.Helper()
	brief := w.brief(lane, "Register slice: lane="+lane+" step=all\nScale: no N\n")
	w.lines()
	line := w.line(launchText(lane, brief))
	w.launchOut[lane] = `{"task":"t","dispatch":"ctx_` + lane + `","terminal":"term_` + lane + `","worktree":"/box/w","workspace":"pool-1"}` + "\n"
	w.lines()
	w.step(w.desk.deliver)
	attempt := spriteAttempt(brief)
	w.write(filepath.Join(attempt, spriteReceipt), `{"result":{"dispatchId":"ctx_`+lane+`"}}`)
	w.write(filepath.Join(attempt, spriteMade), allocation)
	w.step(w.desk.reap)
	return "R" + strings.TrimPrefix(line.Name(), "#")
}

func (w *deskWorld) driftBlockers() []client.Draft {
	var found []client.Draft
	for _, post := range w.posts {
		if strings.HasPrefix(post.ID, "escalation:hook-drift:") {
			found = append(found, post)
		}
	}
	return found
}

func TestEachHookASpritesCaptainHookCannotLoadReachesTheMetaDeskAsOneBlockerAcrossLaunches(t *testing.T) {
	for _, tt := range []struct {
		name, desk, want string
	}{
		{"the drive has a meta desk", "meta-desk-2", "meta-desk-2"},
		{"the drive has none", "", coordinatorLane},
	} {
		t.Run(tt.name, func(t *testing.T) {
			w := newDeskWorld(t)
			spriteConfig(w)
			if tt.desk != "" {
				w.register(store.Lane{Name: tt.desk, Role: lanes.RoleMeta})
			}
			if action := w.action("lane-a", w.settleOnSprite("lane-a", twoDrifts)); action.Status != incident.ActionVerified {
				t.Fatal(action)
			}
			w.step(w.desk.flush)
			blockers := w.driftBlockers()
			if len(blockers) != 2 || blockers[0].ID == blockers[1].ID {
				t.Fatal(w.posts)
			}
			for i, want := range []string{"hooks.merge_guard", "hooks.slack_guard"} {
				if blockers[i].Kind != kindBlocker || !slices.Equal(blockers[i].To, []string{tt.want}) {
					t.Fatal(blockers[i])
				}
				wantContains(t, blockers[i].Text, "hook "+want+" ")
				wantContains(t, blockers[i].Text, "tools/cc-remote/inventory.yaml")
			}
			wantContains(t, blockers[0].Text, "ImportError: cannot import name 'gate'")
			if action := w.action("lane-b", w.settleOnSprite("lane-b", twoDrifts)); action.Status != incident.ActionVerified {
				t.Fatal(action)
			}
			w.step(w.desk.flush)
			if again := w.driftBlockers(); len(again) != 2 {
				t.Fatal(again)
			}
		})
	}
}

func TestASpriteWhoseHooksAllLoadPostsNoBlocker(t *testing.T) {
	w := newDeskWorld(t)
	spriteConfig(w)
	w.settleOnSprite(testLane, `{"workspace":"pool-1"}`)
	w.step(w.desk.flush)
	w.noEscalation("HOOK-DRIFT ")
	if blockers := w.driftBlockers(); len(blockers) != 0 {
		t.Fatal(blockers)
	}
}

func TestARefusedDriftPostLeavesTheSpriteLaunchSettled(t *testing.T) {
	w := newDeskWorld(t)
	spriteConfig(w)
	key := w.settleOnSprite(testLane, twoDrifts)
	w.postError = errors.New("relay down")
	w.step(w.desk.flush)
	action := w.action(testLane, key)
	if action.Status != incident.ActionVerified || string(action.DispatchID) != "ctx_lane-a" {
		t.Fatal(action)
	}
	drifts, err := w.desk.store.Actions(deskRunner, kindEscalation, incident.ActionAccepted)
	if err != nil {
		t.Fatal(err)
	}
	pending := 0
	for _, drift := range drifts {
		if strings.HasPrefix(drift.Target, "HOOK-DRIFT ") {
			pending++
		}
	}
	if pending != 2 {
		t.Fatal(drifts)
	}
}

func TestASpriteThatNeverStartedAWorkerIsRefusedAndOneThatMayRunIsUnverifiable(t *testing.T) {
	for _, tt := range []struct {
		name, prepare, receipt, want string
		status                       incident.ActionStatus
	}{
		{"prepare failed", "1\n", "", "LAUNCH-FAILED ", incident.ActionFailed},
		{"worker-start opened", "0\n", `{"result":{"taskId":"t1","dispatchId":"ctx_s"}}`, "SPRITE-UNVERIFIABLE ", incident.ActionUnverifiable},
	} {
		t.Run(tt.name, func(t *testing.T) {
			w := newDeskWorld(t)
			spriteConfig(w)
			brief := w.brief(testLane, testBrief)
			w.lines()
			line := w.line(launchText(testLane, brief))
			key := "R" + strings.TrimPrefix(line.Name(), "#")
			w.launchOut[testLane] = "lost the box\n"
			w.lines()
			w.step(w.desk.deliver)
			attempt := spriteAttempt(brief)
			w.write(filepath.Join(attempt, spritePrepare), tt.prepare)
			if tt.receipt != "" {
				w.write(filepath.Join(attempt, spriteReceipt), tt.receipt)
			}
			w.step(w.desk.reap)
			if action := w.action(testLane, key); action.Status != tt.status {
				t.Fatal(action)
			}
			w.wantEscalation(tt.want)
		})
	}
}

func TestARunnerWithNoSpriteRouteRefusesALaunchThatMustLeaveTheMac(t *testing.T) {
	w := newDeskWorld(t)
	brief := w.brief(testLane, testBrief)
	w.lines()
	w.line(launchText(testLane, brief))
	w.lines()
	w.step(w.desk.deliver)
	wantContains(t, w.wantEscalation("LAUNCH-FAILED "), "a runner config with no orca.sprite")
}

func TestAStandingDeskFallsBackToTheMacWithNoSpriteRoute(t *testing.T) {
	w := newDeskWorld(t)
	brief := w.brief(testLane, testBrief+"ccx: role=desk\n")
	w.lines()
	w.line(launchText(testLane, brief))
	w.lines()
	w.step(w.desk.deliver)
	if len(w.spawns) != 1 || w.spawns[0].argv[1] != "launch" {
		t.Fatal(w.spawns)
	}
}

func TestTheCoordinatorLaunchesOnTheMacWhileALaneWithNoPlaceTakesASprite(t *testing.T) {
	coordinatorBrief := "Register slice: lane=root step=all\nScale: no N\nccx: incident=none\n"
	for _, tt := range []struct {
		lane, model, brief, want string
	}{
		{coordinatorLane, "fable", coordinatorBrief, "start"},
		{coordinatorLane, "opus", coordinatorBrief, "start"},
		{testLane, "opus", testBrief + "ccx: role=build\n", "sprite"},
	} {
		t.Run(tt.lane+"/"+tt.model, func(t *testing.T) {
			w := newDeskWorld(t)
			launcher := spriteConfig(w)
			brief := w.brief(tt.lane, tt.brief)
			w.lines()
			w.line("orca-desk: launch " + tt.lane + " " + tt.model + " xhigh brief=" + brief)
			w.lines()
			w.step(w.desk.deliver)
			if len(w.spawns) != 1 {
				t.Fatal(w.spawns)
			}
			got := "sprite"
			if w.spawns[0].argv[0] != launcher {
				got = w.spawns[0].argv[2]
			}
			if got != tt.want || got == "start" && w.spawns[0].argv[4] != tt.model {
				t.Fatal(w.spawns)
			}
		})
	}
}

func TestAnIncidentLaunchOverLoadStartsAnywayAndTellsTheWatchdog(t *testing.T) {
	w := newDeskWorld(t)
	w.busy = "3.0"
	brief := w.brief(testLane, testBrief)
	w.lines()
	w.line("orca-desk: launch lane-a NOW incident xhigh brief=" + brief)
	w.lines()
	w.step(w.desk.deliver)
	if len(w.spawns) != 1 {
		t.Fatal(w.spawns)
	}
	kinds := make([]string, 0, len(w.posts))
	for _, post := range w.posts {
		kinds = append(kinds, strings.Fields(post.Text)[0])
	}
	if !slices.Contains(kinds, "LOAD-HIGH") || !slices.Contains(kinds, "LOAD-INCIDENT") {
		t.Fatal(kinds)
	}
}

func TestALaunchStillAcceptedAfterAMinuteLogsOneStall(t *testing.T) {
	w := newDeskWorld(t)
	brief := w.brief(testLane, testBrief)
	if _, _, err := w.desk.acceptLaunch("R1", testLane, launchTarget{Model: "opus", Effort: "xhigh", Brief: brief}); err != nil {
		t.Fatal(err)
	}
	w.now = w.now.Add(launchStall)
	w.step(w.desk.stalledLaunches)
	line := w.wantEscalation("LAUNCH-STALLED stalled:desk-lane-lane-a/R1 lane-a")
	wantContains(t, line, "withdraw it with `orca-desk: withdraw R1`")
	wantContains(t, line, "`lr launch start lane-a opus xhigh "+brief+"`")
}

func runMessage(id, kind, from string, sequence int64) orcaMessage {
	return orcaMessage{ID: id, Type: kind, FromHandle: from, Sequence: sequence, Subject: "subject", Body: "body", CreatedAt: "2026-11-03T08:00:00Z"}
}

func (w *deskWorld) checked(messages ...orcaMessage) {
	w.t.Helper()
	w.runInbox = nil
	ready, err := w.desk.check(context.Background())
	if err != nil || !ready {
		w.t.Fatal(ready, err)
	}
	w.runInbox = messages
	ready, err = w.desk.check(context.Background())
	if err != nil || !ready {
		w.t.Fatal(ready, err)
	}
}

func TestAWorkerQuestionBecomesADecideWithItsWordsKept(t *testing.T) {
	w := newDeskWorld(t)
	w.live(testLane, testDispatch, testTerminal)
	w.checked(runMessage("msg_q", typeQuestion, testTerminal, 1))
	if line := w.wantEscalation("DECIDE msg_q lane-a: subject: body"); line == "" {
		t.Fatal("no decide")
	}
	if kept := readBrief(w.desk.messageFile("msg_q")); kept != "subject\n\nbody\n" {
		t.Fatalf("%q", kept)
	}
}

func TestAQuestionFromALaneAnotherLaneLaunchedGoesToThatLane(t *testing.T) {
	w := newDeskWorld(t)
	w.live(testLane, testDispatch, testTerminal)
	brief := w.brief(testLane, testBrief)
	if _, _, err := w.desk.acceptLaunch("R1", testLane, launchTarget{Model: "opus", Effort: "xhigh", Brief: brief, By: "meta-desk-1"}); err != nil {
		t.Fatal(err)
	}
	w.checked(runMessage("msg_q", typeQuestion, testTerminal, 1))
	last := w.posts[len(w.posts)-1]
	if !slices.Equal(last.To, []string{"meta-desk-1"}) || last.Kind != "decide" || last.Path == "" {
		t.Fatal(last)
	}
}

func (w *deskWorld) pooledLane(name, role string, prs ...int) {
	w.t.Helper()
	w.live(name, "ctx_"+name, "term_"+name)
	if _, err := w.desk.Registry.WantLane(store.Lane{Name: name, Brief: "/b", Model: "opus", Effort: "high", Role: role, State: store.LaneDesired}, w.now); err != nil {
		w.t.Fatal(err)
	}
	if err := w.desk.Registry.SetLanePRs(name, prs); err != nil {
		w.t.Fatal(err)
	}
}

func (w *deskWorld) saidStarted(lane, key string) {
	w.t.Helper()
	container, err := w.desk.lane(lane)
	if err != nil {
		w.t.Fatal(err)
	}
	if err := w.desk.store.Edit(container, func(tx *incident.Tx) error {
		if _, _, err := tx.AcceptDue(key, kindRelay, `{"text":"first"}`, key, w.now, w.now.Add(time.Hour)); err != nil {
			return err
		}
		_, err := tx.StartBy(key, w.now, time.Time{}, "")
		return err
	}); err != nil {
		w.t.Fatal(err)
	}
}

func (w *deskWorld) holds(lane, key string) bool {
	w.t.Helper()
	held, err := w.desk.known(laneBook(lane), key)
	if err != nil {
		w.t.Fatal(err)
	}
	return held != nil
}

func TestASecondOrderToALaneThatSaidStartedGoesToAFreeLaneOfItsRole(t *testing.T) {
	w := newDeskWorld(t)
	w.pooledLane("lane-a", "pool-triage")
	w.pooledLane("lane-parked", "pool-triage")
	w.pooledLane("lane-busy", "pool-triage")
	w.pooledLane("lane-items", "pool-triage", 7)
	w.pooledLane("lane-other", "pool-audit")
	w.pooledLane("lane-free", "pool-triage")
	w.saidStarted("lane-a", "R1")
	w.saidStarted("lane-busy", "R0")
	if err := w.desk.Registry.SetLaneWaiting("lane-parked", 9, time.Time{}, ""); err != nil {
		t.Fatal(err)
	}
	if _, err := w.desk.Registry.OpenItem(store.NewItem{Kind: "fix-red", Repo: "o/r", Number: 7, Head: "h", Mover: machine.MoverLane}, w.now); err != nil {
		t.Fatal(err)
	}
	w.lines()

	w.line("R2 (5:01 PM) orca-desk: relay to lane-a: the second order")
	w.lines()

	w.wantEscalation("ROUTED R2 lane-a -> lane-free: lane-a is busy with R1")
	if w.holds("lane-a", "R2") || !w.holds("lane-free", "R2") {
		t.Fatal("the order did not move to the free lane")
	}
}

func TestAnOrderStaysWithItsLane(t *testing.T) {
	cases := []struct {
		name, role, text string
		started          bool
		prs              []int
	}{
		{name: "a delivered relay it has not started is not busy", role: "pool-triage", text: "go on"},
		{name: "a lane launched with no pool role has its own brief", role: "build", text: "drop item 2 of my last relay", started: true},
		{name: "nor does a release lane stand in for one", role: "release", text: "go on", started: true},
		{name: "a desk keeps its own orders", role: "desk", text: "go on", started: true},
				{name: "a meta desk keeps its own orders", role: "meta", text: "go on", started: true},
		{name: "an order about a PR it owns", role: "pool-triage", text: "fix #48 now", started: true, prs: []int{48}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			w := newDeskWorld(t)
			w.pooledLane("lane-a", c.role, c.prs...)
			w.pooledLane("lane-free", c.role)
			if c.started {
				w.saidStarted("lane-a", "R1")
			}
			w.lines()

			w.line("R2 (5:01 PM) orca-desk: relay to lane-a: " + c.text)
			w.lines()

			if !w.holds("lane-a", "R2") || w.holds("lane-free", "R2") {
				t.Fatal("the order moved")
			}
		})
	}
}

func TestABusyLaneWithNoFreeLaneOfItsRoleAsksRootToSpawnAndTellsTheSender(t *testing.T) {
	w := newDeskWorld(t)
	w.pooledLane("lane-a", "pool-triage")
	w.saidStarted("lane-a", "R1")
	w.lines()

	w.add(object{"lane": "meta-desk-2", "text": "R2 (5:01 PM) orca-desk: relay to lane-a: the second order", "to": []string{deskLane}})
	w.lines()

	if w.holds("lane-a", "R2") {
		t.Fatal("the order waits behind the busy lane")
	}
	asked := w.postOpening("spawn pool-triage lane for R2:")
	if !slices.Equal(asked.To, []string{coordinatorLane}) || asked.Kind != kindAsk || readBrief(asked.Path) == "" {
		t.Fatalf("%+v", asked)
	}
	if !strings.Contains(readBrief(asked.Path), "the second order") {
		t.Fatal(readBrief(asked.Path))
	}
	told := w.postOpening("POOL-NONE R2 lane-a:")
	if !slices.Equal(told.To, []string{"meta-desk-2"}) {
		t.Fatalf("%+v", told)
	}
}

func TestRunMailOrcaSendsWithItsNumericReadFlagBecomesRecords(t *testing.T) {
	raw, err := os.ReadFile("testdata/run-home-flip.json")
	if err != nil {
		t.Fatal(err)
	}
	messages, err := messagesOf(Reply{Result: raw})
	if err != nil {
		t.Fatal(err)
	}
	if len(messages) != 45 {
		t.Fatalf("read %d of the 45 messages the flip missed", len(messages))
	}
	w := newDeskWorld(t)
	w.checked(messages...)
	for _, message := range messages {
		held, err := w.desk.known(deskRunner, escalationPref+message.ID)
		if err != nil || held == nil {
			t.Fatalf("%s %s reached no record: %v", message.Type, message.ID, err)
		}
	}
}

func TestAnUnreadableRunMailRowRaisesInsteadOfReadingAsEmpty(t *testing.T) {
	if _, err := messagesOf(Reply{Result: json.RawMessage(`{"messages":[{"id":"m","sequence":"not a number"}]}`)}); err == nil {
		t.Fatal("an unreadable row read as no mail")
	}
}

func TestAWithdrawnLanesQuestionNeedsNoAnswer(t *testing.T) {
	w := newDeskWorld(t)
	w.live(testLane, testDispatch, testTerminal)
	container, err := w.desk.lane(testLane)
	if err != nil {
		t.Fatal(err)
	}
	if err := w.desk.setFact(container, withdrawnFact, object{"dispatch": testDispatch, "by": "R9"}); err != nil {
		t.Fatal(err)
	}
	w.checked(runMessage("msg_q", typeQuestion, testTerminal, 1))
	w.wantEscalation("WITHDRAWN msg_q lane-a: withdrawn by R9")
	w.noEscalation("DECIDE ")
}

func TestASuccessfulWorkerDoneIsReportedAndAsksTheRegistryToRetireTheLane(t *testing.T) {
	w := newDeskWorld(t)
	w.live(testLane, testDispatch, testTerminal)
	message := runMessage("msg_d", typeWorkerDone, testTerminal, 1)
	message.Payload = `{"outcome":"succeeded","dispatchId":"ctx_a"}`
	w.checked(message)
	w.wantEscalation("OUTCOME msg_d lane-a: worker_done succeeded dispatch=ctx_a: subject")
	last := w.posts[len(w.posts)-1]
	if last.ID != "done:msg_d" || last.Attrs["verb"] != "done" || last.Attrs["lane"] != testLane || !slices.Equal(last.To, []string{supervisorLane}) {
		t.Fatal(last)
	}
}

func TestAFailedWorkerDoneAsksNothingOfTheRegistry(t *testing.T) {
	w := newDeskWorld(t)
	w.live(testLane, testDispatch, testTerminal)
	message := runMessage("msg_d", typeWorkerDone, testTerminal, 1)
	message.Payload = `{"outcome":"failed"}`
	w.checked(message)
	w.wantEscalation("OUTCOME msg_d lane-a: worker_done failed")
	if len(w.posts) != 0 {
		t.Fatal(w.posts)
	}
}

func TestAStartedAndDoneReplyMoveTheRelayFromItsOwner(t *testing.T) {
	w := newDeskWorld(t)
	w.live(testLane, testDispatch, testTerminal)
	container := w.acceptedRelay("R2")
	w.step(w.desk.deliver)
	started := runMessage("msg_s", typeStatus, testTerminal, 1)
	started.Subject, started.ThreadID = "started R2", threadOf(container, "R2")
	done := runMessage("msg_e", typeStatus, testTerminal, 2)
	done.Subject, done.ThreadID = "done R2: restacked", threadOf(container, "R2")
	w.checked(started)
	action := w.action(testLane, "R2")
	if action.Status != incident.ActionStarted || string(action.DispatchID) != testDispatch || action.Deadline != "" {
		t.Fatal(action)
	}
	w.runInbox = []orcaMessage{done, started}
	if _, err := w.desk.check(context.Background()); err != nil {
		t.Fatal(err)
	}
	action = w.action(testLane, "R2")
	if action.Status != incident.ActionCompleted || action.Response["text"] != "restacked" {
		t.Fatal(action)
	}
}

func TestAReplyFromAnotherDispatchIsToldToStandDown(t *testing.T) {
	w := newDeskWorld(t)
	w.live(testLane, testDispatch, testTerminal)
	container := w.acceptedRelay("R2")
	message := runMessage("msg_s", typeStatus, "term_old", 1)
	message.Subject, message.ThreadID, message.Payload = "started R2", threadOf(container, "R2"), `{"dispatchId":"ctx_old"}`
	w.checked(message)
	if action := w.action(testLane, "R2"); action.Status != incident.ActionAccepted {
		t.Fatal(action)
	}
	notice := w.action(testLane, "stand-down:R2:ctx_old")
	wantContains(t, notice.Target, "Do not execute R2: dispatch ctx_a owns desk-lane-lane-a")
}

func TestAStatusNamingARelayAcknowledgesIt(t *testing.T) {
	w := newDeskWorld(t)
	w.live(testLane, testDispatch, testTerminal)
	w.acceptedRelay("R77")
	w.step(w.desk.deliver)
	message := runMessage("msg_s", typeStatus, testTerminal, 1)
	message.Subject, message.Body = "handled R77", "all set"
	w.checked(message)
	if action := w.action(testLane, "R77"); action.Status != incident.ActionCompleted {
		t.Fatal(action)
	}
}

func (w *deskWorld) postsOn(topic string) []client.Draft {
	var found []client.Draft
	for _, post := range w.posts {
		if post.Topic == topic {
			found = append(found, post)
		}
	}
	return found
}

func (w *deskWorld) passed() {
	w.t.Helper()
	w.step(func(ctx context.Context) error {
		_, err := w.desk.check(ctx)
		return err
	})
	w.step(w.desk.flush)
}

func TestAnAskAndAStatusNamingNoRelayEachReachTheBusOnce(t *testing.T) {
	for _, c := range []struct{ kind, subject, posted string }{
		{typeStatus, "merge_ready", "report"},
		{typeQuestion, "Question", "decide"},
	} {
		t.Run(c.kind, func(t *testing.T) {
			w := newDeskWorld(t)
			w.live(testLane, testDispatch, testTerminal)
			message := runMessage("msg_a", c.kind, testTerminal, 1)
			message.Subject = c.subject
			w.checked(message)
			w.step(w.desk.flush)
			w.passed()
			posted := w.postsOn("msg_a")
			if len(posted) != 1 || posted[0].Kind != c.posted || !slices.Equal(posted[0].To, []string{lanes.Root}) {
				t.Fatal(posted)
			}
		})
	}
}

func TestADeskPostCCIRefusesStillReachesTheBus(t *testing.T) {
	w := newDeskWorld(t)
	w.live(testLane, testDispatch, testTerminal)
	brief := w.brief(testLane, testBrief)
	if _, _, err := w.desk.acceptLaunch("R1", testLane, launchTarget{Model: "opus", Effort: "xhigh", Brief: brief, By: "meta-desk-1"}); err != nil {
		t.Fatal(err)
	}
	w.postError = errors.New("no lane meta-desk-1")
	w.checked(runMessage("msg_q", typeQuestion, testTerminal, 1))
	w.postError = nil
	w.passed()
	if posted := w.postsOn("msg_q"); len(posted) != 1 || posted[0].Kind != "decide" || !slices.Equal(posted[0].To, []string{lanes.Root}) {
		t.Fatal(posted)
	}
}

func TestTheReplayRelaysDroppedMailOnceAndMailAlreadyRelayedNever(t *testing.T) {
	w := newDeskWorld(t)
	w.live(testLane, testDispatch, testTerminal)
	asked := runMessage("msg_q", typeQuestion, testTerminal, 1)
	w.checked(asked)
	w.step(w.desk.flush)
	dropped := runMessage("msg_s", typeStatus, testTerminal, 2)
	dropped.Subject = "merge_ready"
	unreplayed := func() {
		if err := w.desk.store.Edit(deskRunner, func(tx *incident.Tx) error {
			if err := tx.SetFact("inbox:"+testRun, 2); err != nil {
				return err
			}
			if err := tx.SetFact("relayed:"+testRun, []string{"msg_q", "msg_s"}); err != nil {
				return err
			}
			return tx.DropFact("replayed:" + testRun)
		}); err != nil {
			t.Fatal(err)
		}
	}
	unreplayed()
	w.runInbox = []orcaMessage{dropped, asked}
	w.passed()
	w.passed()
	unreplayed()
	w.passed()
	if asks, reports := w.postsOn("msg_q"), w.postsOn("msg_s"); len(asks) != 1 || len(reports) != 1 || reports[0].Kind != "report" {
		t.Fatal(asks, reports)
	}
	wantContains(t, w.wantEscalation("REPLAYED replayed:run_1"), "1 asks and reports")
	if posted := w.postsOn("replayed:" + testRun); len(posted) != 1 {
		t.Fatal(posted)
	}
}

func TestTheRelayReadsRunHomeAsOftenAtAThousandLanesAsAtOne(t *testing.T) {
	reads := func(count int) int {
		w := newDeskWorld(t)
		for i := range count {
			w.live(fmt.Sprintf("lane-%d", i), fmt.Sprintf("ctx_%d", i), fmt.Sprintf("term_%d", i))
		}
		message := runMessage("msg_s", typeStatus, "term_0", 1)
		message.Subject = "merge_ready"
		w.checked(message)
		return len(w.calls)
	}
	if one, thousand := reads(1), reads(1000); one != thousand {
		t.Fatal(one, thousand)
	}
}

func TestAHeartbeatIsKeptOnTheLaneAndRelaysNothing(t *testing.T) {
	w := newDeskWorld(t)
	w.live(testLane, testDispatch, testTerminal)
	w.acceptedRelay("R2")
	w.checked(runMessage("msg_h", typeHeartbeat, testTerminal, 1))
	value, found, err := w.desk.factOf(laneBook(testLane), heartbeatFact)
	if err != nil || !found || value != "2026-11-03T08:00:00Z" {
		t.Fatal(value, found, err)
	}
	if got := w.escalations(); len(got) != 0 {
		t.Fatal(got)
	}
}

func TestAMessageBehindTheCursorOfNoRelayedTypeIsReportedOnce(t *testing.T) {
	w := newDeskWorld(t)
	w.live(testLane, testDispatch, testTerminal)
	old := runMessage("msg_old", "note", testTerminal, 1)
	w.runInbox = []orcaMessage{old, runMessage("msg_new", "note", testTerminal, 2)}
	if _, err := w.desk.check(context.Background()); err != nil {
		t.Fatal(err)
	}
	w.handed, w.delivery = []orcaMessage{old}, "delivery_1"
	if _, err := w.desk.check(context.Background()); err != nil {
		t.Fatal(err)
	}
	wantContains(t, w.wantEscalation("UNRELAYED msg_old lane-a"), "behind cursor")
	acks := 0
	for _, call := range w.orcaCalls("orchestration check") {
		if slices.Contains(call, "--ack") {
			acks++
		}
	}
	if acks != 1 {
		t.Fatal(w.calls)
	}
}

func fixedLane(t *testing.T, w *deskWorld, lane string) {
	t.Helper()
	brief := w.brief(lane, testBrief)
	if _, _, err := w.desk.acceptLaunch("R1", lane, launchTarget{Model: agentIncident, Effort: "xhigh", Brief: brief}); err != nil {
		t.Fatal(err)
	}
	if err := w.desk.store.Edit(laneBook(lane), func(tx *incident.Tx) error {
		if _, err := tx.StartBy("R1", w.now, zeroTime, ""); err != nil {
			return err
		}
		if _, err := tx.Complete("R1", object{}, testDispatch); err != nil {
			return err
		}
		_, err := tx.Verify("R1", object{"at": store.Stamp(w.now.Add(-time.Hour))})
		return err
	}); err != nil {
		t.Fatal(err)
	}
	w.write(w.desk.reference("unfixed-case-brief.md"), "ccx: lane={lane} role=incident\n{parent} {case} {prs} {log} {facts}\nRegister slice: lane={lane} step=incident\n")
}

func TestAReadyReportNamingUnfixedCasesLaunchesCaseLanesUpToTheCap(t *testing.T) {
	w := newDeskWorld(t)
	lane := "api-fix"
	fixedLane(t, w, lane)
	message := runMessage("msg_r", typeStatus, "term_fix", 1)
	message.Subject = "ready: fixed"
	message.Body = "UNFIXED api-fix: case one\n`UNFIXED api-fix: case two`\nUNFIXED api-fix: case three\nUNFIXED other: not ours\n"
	message.FromHandle = "term_fix"
	w.write(filepath.Join(w.desk.Config.Receipts, lane+".terminal"), "term_fix\n")
	w.write(filepath.Join(w.desk.Config.Receipts, lane+".json"), `{"result":{"dispatchId":"ctx_fix"}}`)
	w.checked(message)
	w.wantEscalation("READY msg_r api-fix: ready: fixed")
	for _, name := range []string{"api-fix-case1", "api-fix-case2"} {
		launches, err := w.desk.store.Actions(laneBook(name), kindLaunch)
		if err != nil || len(launches) != 1 {
			t.Fatal(name, launches, err)
		}
		spec, err := launchOf(launches[0])
		if err != nil {
			t.Fatal(err)
		}
		if spec.Model != agentIncident {
			t.Fatal(spec)
		}
		wantContains(t, readBrief(spec.Brief), "api-fix ")
	}
	wantContains(t, w.wantEscalation("DECIDE unfixed:msg_r api-fix"), "past the cap of 2 case lanes per incident")
	wantContains(t, w.wantEscalation("DECIDE unfixed:msg_r api-fix"), "case three | to launch its lane, post `orca-desk: launch api-fix-case3 NOW incident xhigh brief=")
	value, _, err := w.desk.factOf(laneBook(lane), unfixedCases)
	if err != nil {
		t.Fatal(err)
	}
	if taken := stringList(value); !slices.Equal(taken, []string{"case one", "case two"}) {
		t.Fatal(taken)
	}
}

func TestAReportThatHandsTheIncidentBackLaunchesNoCaseLane(t *testing.T) {
	w := newDeskWorld(t)
	lane := "api-fix"
	fixedLane(t, w, lane)
	message := runMessage("msg_r", typeStatus, "term_fix", 1)
	message.Subject = "ready: NOT-OURS"
	message.Body = "NOT-OURS after all\nUNFIXED api-fix: case one\n"
	w.write(filepath.Join(w.desk.Config.Receipts, lane+".terminal"), "term_fix\n")
	w.checked(message)
	if exists, _ := w.desk.store.Exists(laneBook("api-fix-case1")); exists {
		t.Fatal("a case lane launched")
	}
}

func TestAnUnknownCauseOffersTheCasesInsteadOfLaunching(t *testing.T) {
	w := newDeskWorld(t)
	lane := "api-fix"
	fixedLane(t, w, lane)
	message := runMessage("msg_r", typeStatus, "term_fix", 1)
	message.Subject = "ready: mitigated"
	message.Body = "cause UNKNOWN\nUNFIXED api-fix: case one\n"
	w.write(filepath.Join(w.desk.Config.Receipts, lane+".terminal"), "term_fix\n")
	w.checked(message)
	wantContains(t, w.wantEscalation("DECIDE unfixed:msg_r api-fix"), "says the cause is UNKNOWN, so no case lane launches until the root ratifies it")
	if exists, _ := w.desk.store.Exists(laneBook("api-fix-case1")); exists {
		t.Fatal("a case lane launched")
	}
}

func TestACaseLaunchPendingWhenItsIncidentEndsNeverStarts(t *testing.T) {
	w := newDeskWorld(t)
	brief := w.brief("api-fix-case1", testBrief)
	if _, _, err := w.desk.acceptLaunch("unfixed:msg:1", "api-fix-case1", launchTarget{Model: agentIncident, Effort: "xhigh", Brief: brief}); err != nil {
		t.Fatal(err)
	}
	w.add(object{"lane": "api-fix", "kind": kindNotOurs, "text": "not ours"})
	w.step(w.desk.deliver)
	if len(w.spawns) != 0 {
		t.Fatal(w.spawns)
	}
	if action := w.action("api-fix-case1", "unfixed:msg:1"); action.Status != incident.ActionFailed {
		t.Fatal(action)
	}
	w.wantEscalation("WITHDRAWN unfixed:msg:1 api-fix-case1: the incident ended with #")
}

func TestAnIncidentLaunchAnotherOwnerHoldsFails(t *testing.T) {
	w := newDeskWorld(t)
	brief := w.brief(testLane, testBrief)
	if _, _, err := w.desk.acceptLaunch("R1", testLane, launchTarget{Model: agentIncident, Effort: "xhigh", Brief: brief, Incident: "api-500"}); err != nil {
		t.Fatal(err)
	}
	w.add(object{"kind": kindStopped, "text": "stop", "to": []string{testLane}})
	w.step(w.desk.deliver)
	if action := w.action(testLane, "R1"); action.Status != incident.ActionFailed {
		t.Fatal(action)
	}
	w.wantEscalation("INCIDENT-OWNED ")
}

func (w *deskWorld) cutLaunch(key string, clock object, silent bool) {
	w.t.Helper()
	brief := w.brief("manual-release-api", testBrief+"ccx: place=local\n")
	if _, _, err := w.desk.acceptLaunch("cut:"+key, "manual-release-api", launchTarget{Model: "opus", Effort: "xhigh", Brief: brief}); err != nil {
		w.t.Fatal(err)
	}
	if err := w.desk.setFact(deskRunner, cutClocksFact, object{key: clock}); err != nil {
		w.t.Fatal(err)
	}
	if err := w.desk.record("escalation:cut:"+key, "CUT-SILENT release-1 api: no CUT"); err != nil {
		w.t.Fatal(err)
	}
	if !silent {
		return
	}
	posted := w.add(object{"lane": deskRunner, "kind": kindBlocker, "text": "CUT-SILENT"})
	if err := w.desk.store.Edit(deskRunner, func(tx *incident.Tx) error {
		if _, err := tx.StartBy("escalation:cut:"+key, w.now, zeroTime, ""); err != nil {
			return err
		}
		_, err := tx.Complete("escalation:cut:"+key, object{"at": store.Stamp(w.now), "posted": seqReceipt(posted.Seq)}, "")
		return err
	}); err != nil {
		w.t.Fatal(err)
	}
}

func testClock(at time.Time) object {
	return object{"lane": "release-1", "pr": "", "commit": "abcdef012", "components": []string{"api"}, "since": store.Stamp(at), "ended": ""}
}

func TestAManualReleaseWaitsOutTheGraceThenLaunches(t *testing.T) {
	w := newDeskWorld(t)
	w.cutLaunch("go:9", testClock(w.now), false)
	w.step(w.desk.deliver)
	if len(w.spawns) != 0 {
		t.Fatal("it launched before CUT-SILENT posted")
	}
	w.step(w.desk.flush)
	w.step(w.desk.deliver)
	if len(w.spawns) != 0 {
		t.Fatal("it launched inside the grace")
	}
	w.now = w.now.Add(cutGrace)
	w.step(w.desk.deliver)
	if len(w.spawns) != 1 {
		t.Fatal(w.spawns)
	}
}

func TestAReleaseAnswerWithdrawsTheManualRelease(t *testing.T) {
	w := newDeskWorld(t)
	w.cutLaunch("go:9", testClock(w.now), true)
	answer := w.add(object{"lane": "release-1", "kind": "refuse", "text": "no"})
	w.now = w.now.Add(cutGrace)
	w.step(w.desk.deliver)
	if len(w.spawns) != 0 {
		t.Fatal(w.spawns)
	}
	action := w.action("manual-release-api", "cut:go:9")
	if action.Status != incident.ActionFailed || string(action.Reason) != fmt.Sprintf("#%d refuse", answer.Seq) {
		t.Fatal(action)
	}
	value, _, err := w.desk.factOf(deskRunner, cutClocksFact)
	if err != nil {
		t.Fatal(err)
	}
	wantContains(t, fmt.Sprint(value), "withdrawn: ")
}

func TestARootResolveOfCutSilentWithdrawsTheManualRelease(t *testing.T) {
	w := newDeskWorld(t)
	w.cutLaunch("go:9", testClock(w.now), true)
	silent, err := w.desk.store.Action(deskRunner, "escalation:cut:go:9")
	if err != nil {
		t.Fatal(err)
	}
	seq := receiptSeq(fmt.Sprint(silent.Response["posted"]))
	w.add(object{"kind": "decide", "text": "no", "resolves": seq})
	w.now = w.now.Add(cutGrace)
	w.step(w.desk.deliver)
	if action := w.action("manual-release-api", "cut:go:9"); action.Status != incident.ActionFailed {
		t.Fatal(action)
	}
}

func TestUnreadMailGetsOneWakeAndASettledDispatchsMailEscalates(t *testing.T) {
	w := newDeskWorld(t)
	w.live(testLane, testDispatch, testTerminal)
	w.step(w.desk.sweep)
	message := orcaMessage{ID: "msg_u", Sequence: 5, ToHandle: testTerminal, CreatedAt: store.Stamp(w.now)}
	w.mail = []orcaMessage{message}
	w.unread[testTerminal] = []orcaMessage{message}
	w.now = w.now.Add(staleMail)
	w.step(w.desk.sweep)
	woken := func() int {
		count := 0
		for _, call := range w.calls {
			if call[0] == "orca" && call[1] == "terminal" && call[2] == "send" {
				count++
			}
		}
		return count
	}
	if woken() != 1 {
		t.Fatal(w.calls)
	}
	w.step(w.desk.sweep)
	if woken() != 1 {
		t.Fatal("woken twice")
	}
	w.dispatches[testDispatch].status = statusFailed
	w.step(w.desk.sweep)
	w.wantEscalation("STALE-MAIL stale:msg_u lane-a: msg_u unread by a failed dispatch")
	wantContains(t, w.wantEscalation("RECLAIM reclaim:ctx_a runner"), "1 settled dispatch(es) still hold their terminal: lane-a=ctx_a:term_a")
}

func TestReclaimNamesEachSettledDispatchOnce(t *testing.T) {
	w := newDeskWorld(t)
	w.live(testLane, testDispatch, testTerminal)
	w.dispatches[testDispatch].status = statusCompleted
	w.step(w.desk.sweep)
	w.step(w.desk.sweep)
	count := 0
	for _, line := range w.escalations() {
		if strings.HasPrefix(line, "RECLAIM ") {
			count++
		}
	}
	if count != 1 {
		t.Fatal(w.escalations())
	}
}

func TestAPromptWatchDownEscalates(t *testing.T) {
	w := newDeskWorld(t)
	w.desk.Runner = runnerFunc(func(ctx context.Context, c shell.Command) (shell.Result, error) {
		if filepath.Base(c.Argv[0]) == "prompt_watch.py" {
			return shell.Result{Stdout: `{"rows":[{"state":"down","detail":"no poll for 5 min"}]}`}, nil
		}
		return w.Run(ctx, c)
	})
	w.step(w.desk.sweep)
	wantContains(t, w.wantEscalation("PROMPT-WATCH-DOWN "), "no poll for 5 min")
}

func TestAnOverdueRelayIsADeadlineUnlessTheLaneIsAliveOrMoved(t *testing.T) {
	w := newDeskWorld(t)
	w.acceptedRelay("R2")
	w.now = w.now.Add(11 * time.Minute)
	w.step(w.desk.deadlines)
	wantContains(t, w.wantEscalation("DEADLINE deadline:desk-lane-lane-a/R2:accepted lane-a"), "R2 relay: never delivered: no live dispatch; accepted 11 min ago")
}

func TestAHeartbeatAfterTheRelayExcusesItsDeadline(t *testing.T) {
	w := newDeskWorld(t)
	container := w.acceptedRelay("R2")
	w.now = w.now.Add(11 * time.Minute)
	if err := w.desk.setFact(container, heartbeatFact, store.Stamp(w.now)); err != nil {
		t.Fatal(err)
	}
	w.step(w.desk.deadlines)
	w.noEscalation("DEADLINE ")
}

func TestAHeadOnThePRExcusesARelayedBlockersDeadline(t *testing.T) {
	w := newDeskWorld(t)
	if _, _, err := w.desk.acceptRelay("blocker:12:abc", testLane, "#12 red", "", 10); err != nil {
		t.Fatal(err)
	}
	w.now = w.now.Add(11 * time.Minute)
	w.add(object{"lane": testLane, "kind": "head", "text": "12 def", "refs": object{"prs": []int{12}}})
	w.step(w.desk.deadlines)
	w.noEscalation("DEADLINE ")
}

func TestARedByDesignContractExcusesARelayedBlockersDeadline(t *testing.T) {
	w := newDeskWorld(t)
	if _, _, err := w.desk.acceptRelay("blocker:12:abc", testLane, "#12 red", "", 10); err != nil {
		t.Fatal(err)
	}
	w.add(object{"lane": testLane, "kind": "contract", "text": "#12 is red by design", "refs": object{"prs": []int{12}}})
	w.now = w.now.Add(11 * time.Minute)
	w.step(w.desk.deadlines)
	w.noEscalation("DEADLINE ")
}

func TestAnOverdueLaunchNamesItsStep(t *testing.T) {
	w := newDeskWorld(t)
	brief := w.brief(testLane, testBrief)
	if _, _, err := w.desk.acceptLaunch("R1", testLane, launchTarget{Model: "opus", Effort: "xhigh", Brief: brief}); err != nil {
		t.Fatal(err)
	}
	w.now = w.now.Add(16 * time.Minute)
	w.step(w.desk.deadlines)
	wantContains(t, w.wantEscalation("DEADLINE "), "R1 launch: launch never started")
}

func TestFlushPostsEachEscalationOnceWithItsKindAndWholeMessage(t *testing.T) {
	w := newDeskWorld(t)
	if err := w.desk.keep(orcaMessage{ID: "msg_q", Subject: "which", Body: "way"}); err != nil {
		t.Fatal(err)
	}
	if err := w.desk.escalate("msg_q", "DECIDE", testLane, "which: way"); err != nil {
		t.Fatal(err)
	}
	if err := w.desk.record("escalation:launch:cci#1", "LAUNCH-FAILED R1 lane-a: "+strings.Repeat("long ", 100)); err != nil {
		t.Fatal(err)
	}
	w.step(w.desk.flush)
	w.step(w.desk.flush)
	if len(w.posts) != 2 {
		t.Fatal(w.posts)
	}
	decide, failed := w.posts[0], w.posts[1]
	if decide.Kind != "decide" || decide.Topic != "msg_q" || decide.Path == "" || decide.ID != "escalation:msg_q" || !slices.Equal(decide.To, []string{coordinatorLane}) {
		t.Fatal(decide)
	}
	wantContains(t, readBrief(decide.Path), "which\n\nway")
	if failed.Kind != kindDefect || failed.Path == "" || len([]rune(failed.Text)) != textLimit || !strings.HasSuffix(failed.Text, "…") {
		t.Fatal(failed)
	}
	action, err := w.desk.store.Action(deskRunner, "escalation:msg_q")
	if err != nil || action.Status != incident.ActionCompleted {
		t.Fatal(action, err)
	}
}

func TestAnIncidentClockDefersOutsideNoiseUntilItEnds(t *testing.T) {
	w := newDeskWorld(t)
	fixedLane(t, w, "api-fix")
	if err := w.desk.setFact(laneBook("api-fix"), clockFact, object{"ended": ""}); err != nil {
		t.Fatal(err)
	}
	if err := w.desk.record("escalation:deadline:x", "DEADLINE deadline:x lane-b: late"); err != nil {
		t.Fatal(err)
	}
	if err := w.desk.record("escalation:deadline:y", "DEADLINE deadline:y api-fix: late"); err != nil {
		t.Fatal(err)
	}
	w.step(w.desk.flush)
	if len(w.posts) != 1 || !strings.Contains(w.posts[0].Text, "api-fix") {
		t.Fatal(w.posts)
	}
	if err := w.desk.setFact(laneBook("api-fix"), clockFact, object{"ended": "fix-live"}); err != nil {
		t.Fatal(err)
	}
	w.step(w.desk.flush)
	w.step(w.desk.flush)
	wantContains(t, w.posts[len(w.posts)-1].Text, "DEFERRED 1 escalations held during api-fix: DEADLINE deadline:x lane-b: late")
}

func TestAConfirmationWithNoActionStaysOnTheBusAndOffTheCoordinator(t *testing.T) {
	for label, to := range map[string]string{"RELAYED": watchdogLane, "ORCA-BACK": watchdogLane, "LAUNCHED": coordinatorLane, "RELAY-FAILED": coordinatorLane, "DECIDE": coordinatorLane, "UNKNOWN-LABEL": coordinatorLane} {
		w := newDeskWorld(t)
		if to == watchdogLane {
			to += "@" + w.desk.Config.Drive
		}
		if err := w.desk.escalate("k1", label, testLane, "text"); err != nil {
			t.Fatal(err)
		}
		w.step(w.desk.flush)
		if len(w.posts) != 1 || !slices.Equal(w.posts[0].To, []string{to}) || w.posts[0].Lane != deskRunner {
			t.Fatalf("%s: posts %+v, want one from the runner to %s", label, w.posts, to)
		}
	}
}

func TestAnEscalationToALaneIsRecordedOnlyOncePosted(t *testing.T) {
	w := newDeskWorld(t)
	if err := w.desk.escalateTo(context.Background(), "k1", "DECIDE", testLane, "text", "meta-desk-1"); err != nil {
		t.Fatal(err)
	}
	if err := w.desk.escalateTo(context.Background(), "k1", "DECIDE", testLane, "text", "meta-desk-1"); err != nil {
		t.Fatal(err)
	}
	if len(w.posts) != 1 {
		t.Fatal(w.posts)
	}
	action, err := w.desk.store.Action(deskRunner, "escalation:k1")
	if err != nil || action.Status != incident.ActionCompleted {
		t.Fatal(action, err)
	}
}

func TestAnUnreachableOrcaIsOneReportAndBackoffUntilItAnswers(t *testing.T) {
	w := newDeskWorld(t)
	err := w.desk.settlePass(context.Background(), &orcaUnreachable{Text: "runtime_timeout"})
	if err != nil {
		t.Fatal(err)
	}
	if err := w.desk.settlePass(context.Background(), &orcaUnreachable{Text: "runtime_timeout"}); err != nil {
		t.Fatal(err)
	}
	if w.desk.passWait() != 40*time.Second {
		t.Fatal(w.desk.passWait())
	}
	count := 0
	for _, line := range w.escalations() {
		if strings.HasPrefix(line, "ORCA-UNREACHABLE ") {
			count++
		}
	}
	if count != 1 {
		t.Fatal(w.escalations())
	}
	w.now = w.now.Add(5 * time.Minute)
	if err := w.desk.settlePass(context.Background(), nil); err != nil {
		t.Fatal(err)
	}
	wantContains(t, w.wantEscalation("ORCA-BACK "), "for 5 min (runtime_timeout)")
	if w.desk.passWait() != passEvery {
		t.Fatal(w.desk.passWait())
	}
}

func TestAWorkerShowOrcaCannotAnswerStopsThePassAsUnreachable(t *testing.T) {
	w := newDeskWorld(t)
	w.live(testLane, testDispatch, testTerminal)
	w.refusals["orchestration worker-show"] = object{"code": "runtime_unavailable", "message": "down"}
	_, err := w.desk.show(context.Background(), testLane, "")
	var down *orcaUnreachable
	if !errors.As(err, &down) {
		t.Fatal(err)
	}
}

func TestARemoteRuntimeThatDoesNotAnswerReadsAsLive(t *testing.T) {
	w := newDeskWorld(t)
	w.live(testLane, testDispatch, testTerminal)
	w.refusals["orchestration worker-show"] = object{"code": codeRemoteUnreachable, "message": "sprite"}
	shown, err := w.desk.show(context.Background(), testLane, "")
	if err != nil || !shown.Live || !shown.active() {
		t.Fatal(shown, err)
	}
}

func TestRunRefusesToStartUnboundAndExitsThree(t *testing.T) {
	w := newDeskWorld(t)
	w.coordinate = "run_other"
	err := w.desk.Run(context.Background(), true)
	var exit *wait.ExitError
	if !errors.As(err, &exit) || exit.Code != unboundExit {
		t.Fatal(err)
	}
	if len(w.posts) != 1 || !strings.HasPrefix(w.posts[0].Text, "UNBOUND ") {
		t.Fatal(w.posts)
	}
}

func TestOnePassReadsALineAndDeliversIt(t *testing.T) {
	w := newDeskWorld(t)
	w.live(testLane, testDispatch, testTerminal)
	if err := w.desk.Run(context.Background(), true); err != nil {
		t.Fatal(err)
	}
	w.line("R2 (5:01 PM) orca-desk: relay to lane-a: go")
	if err := w.desk.Run(context.Background(), true); err != nil {
		t.Fatal(err)
	}
	if sends := w.orcaCalls("orchestration send"); len(sends) != 1 {
		t.Fatal(w.calls)
	}
	alive := false
	for _, post := range w.posts {
		alive = alive || (post.Topic == runnerTopic && strings.HasPrefix(post.Text, runnerAlive))
	}
	if !alive {
		t.Fatal(w.posts)
	}
}

func TestASecondRelayOnOneStateDirLeavesTheFirstRunning(t *testing.T) {
	w := newDeskWorld(t)
	first, held, err := w.desk.holdRelayLock()
	if err != nil || !held {
		t.Fatal(held, err)
	}
	defer func() { _ = first.Close() }()
	if err := w.desk.Run(context.Background(), true); err != nil {
		t.Fatal(err)
	}
	if len(w.posts) != 0 || len(w.calls) != 0 {
		t.Fatal(w.posts, w.calls)
	}
}

func TestARelayOrphanedByItsParentStops(t *testing.T) {
	w := newDeskWorld(t)
	w.parent = 7
	w.desk.Sleep = func(time.Duration) { w.parent = 1 }
	if err := w.desk.Run(context.Background(), false); err != nil {
		t.Fatal(err)
	}
	if w.desk.passes != 1 {
		t.Fatal(w.desk.passes)
	}
}

func TestWordsNamingAClockTheBusRefusesStillReachTheReader(t *testing.T) {
	w := newDeskWorld(t)
	w.refuseTime = true
	line := "REPORT msg_1 term_1: the conflict named 7cbdeaf, which the 7:45 AM stack push replaced"

	if _, err := w.desk.postEscalation(context.Background(), line, "root", "escalation:msg_1"); err != nil {
		t.Fatal(err)
	}

	posted := w.postOpening("REPORT msg_1 term_1:")
	if strings.Contains(posted.Text, "7:45") || !strings.Contains(posted.Text, "the [clock] stack push") {
		t.Fatal(posted.Text)
	}
	if !strings.Contains(readBrief(posted.Path), "the 7:45 AM stack push") {
		t.Fatal(readBrief(posted.Path))
	}
}

func TestARefusalThatIsNotAboutAClockStaysARefusal(t *testing.T) {
	w := newDeskWorld(t)
	w.postError = errors.New("bus down")

	if _, err := w.desk.post(context.Background(), deskPost{Kind: kindReport, Text: "the 7:45 AM push", To: "root", Key: "k"}); err == nil {
		t.Fatal("no error")
	}
	if len(w.posts) != 0 {
		t.Fatalf("%+v", w.posts)
	}
}

func TestARelayStopsWhenTheSupervisorTakesARelease(t *testing.T) {
	w := newDeskWorld(t)
	w.release = "0.7.1"
	w.desk.Sleep = func(time.Duration) { w.release = "0.7.2" }
	if err := w.desk.Run(context.Background(), false); err != nil {
		t.Fatal(err)
	}
	if w.desk.passes != 1 {
		t.Fatal(w.desk.passes)
	}
}

func TestRebindRefusesOutsideAnOrcaTerminalAndBindsInsideOne(t *testing.T) {
	w := newDeskWorld(t)
	err := w.desk.Rebind(context.Background())
	var exit *wait.ExitError
	if !errors.As(err, &exit) || exit.Code != unboundExit {
		t.Fatal(err)
	}
	w.desk.Env = func(name string) string {
		if name == "ORCA_TERMINAL_HANDLE" {
			return "term_root"
		}
		return ""
	}
	if err := w.desk.Rebind(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(w.orcaCalls("orchestration run-use")) != 1 {
		t.Fatal(w.calls)
	}
}

func TestADirectiveParsesEveryForm(t *testing.T) {
	for _, tt := range []struct{ line, key, verb string }{
		{"orca-desk: relay to a: b", "", verbRelay},
		{"R12 orca-desk: hold x owner=y :: z", "R12", kindHold},
		{"R12 (5:01 PM) orca-desk: withdraw R9", "R12", verbWithdraw},
		{"5:01 PM R12 (5:01 PM) orca-desk: launch a opus xhigh brief=/b", "R12", verbLaunch},
		{"- 5:01PM orca-desk: unhold x", "", "unhold"},
	} {
		parsed := parseDirective(tt.line)
		if parsed == nil || parsed.Key != tt.key || parsed.Verb != tt.verb {
			t.Fatal(tt.line, parsed)
		}
	}
	for _, line := range []string{"orca-desk: launches", "say orca-desk: relay to a: b", "orca-desk: nothing"} {
		if parseDirective(line) != nil {
			t.Fatal(line)
		}
	}
}

func TestALaunchKeysByTheRecordsNumberAndOtherLinesByTheirOwnKey(t *testing.T) {
	record := recordFrom(object{"seq": 7, "num": "#7@node", "kind": kindGo, "at": "2026-11-03T08:00:00Z", "refs": object{}, "fields": object{}, "drive": testDrive, "lane": "root", "text": "", "source": "post"})
	if got := lineKey(&directive{Verb: verbLaunch, Key: "R1"}, record); got != "R7@node" {
		t.Fatal(got)
	}
	if got := lineKey(&directive{Verb: verbRelay, Key: "R1"}, record); got != "R1" {
		t.Fatal(got)
	}
	if got := lineKey(&directive{Verb: verbRelay}, record); got != "inbox@cci#7" {
		t.Fatal(got)
	}
}

func TestLaneNamesAndTheirLineage(t *testing.T) {
	for _, tt := range []struct{ lane, first, name string }{
		{"inc-laptop-fix", "inc-laptop-fix", "inc-laptop"},
		{"inc-laptop-fix-2-opus", "inc-laptop-fix-2", "inc-laptop"},
		{"api-fix", "api-fix", "api-fix"},
		{"api-fix-case2", "api-fix", "api-fix"},
	} {
		if firstLane(tt.lane) != tt.first || incidentName(tt.lane) != tt.name {
			t.Fatal(tt.lane, firstLane(tt.lane), incidentName(tt.lane))
		}
	}
	if !isCaseLane("api-fix-case2") || isCaseLane("api-fix") {
		t.Fatal("case lanes misread")
	}
}

func TestTheStoreKeepsWhatTheRelayWrites(t *testing.T) {
	w := newDeskWorld(t)
	container, err := w.desk.lane(testLane)
	if err != nil {
		t.Fatal(err)
	}
	deadline := w.now.Add(time.Hour)
	action, created, err := w.desk.accept(container, "R1", kindRelay, "{}", "R1", deadline)
	if err != nil || !created || string(action.Deadline) != store.Stamp(deadline) {
		t.Fatal(action, created, err)
	}
	if _, created, err := w.desk.accept(container, "R1", kindRelay, "other", "R1", deadline); err != nil || created {
		t.Fatal(created, err)
	}
	moved, err := w.desk.attempt(container, func(tx *incident.Tx) error {
		_, err := tx.Complete("R1", object{}, "")
		return err
	})
	if err != nil || moved {
		t.Fatal(moved, err)
	}
	if err := w.desk.setFact(container, "fact", object{"a": object{"b": 1}}); err != nil {
		t.Fatal(err)
	}
	value, found, err := w.desk.factOf(container, "fact")
	if err != nil || !found {
		t.Fatal(found, err)
	}
	if fmt.Sprint(value) != "map[a:map[b:1]]" {
		t.Fatal(value)
	}
	if err := w.desk.store.Edit(container, func(tx *incident.Tx) error { return tx.DropFact("fact") }); err != nil {
		t.Fatal(err)
	}
	if _, found, err := w.desk.factOf(container, "fact"); err != nil || found {
		t.Fatal(found, err)
	}
}
