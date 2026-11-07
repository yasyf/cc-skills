package prompts

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	rootTerm      = "term_26c4ee5e-61f3-4fac-86aa-40bbdbc87c4e"
	rootSession   = "900424b6-7393-480c-a26a-f1bd21da6e57"
	supTerm       = "term_7f2d70f5-ca5c-4260-ab60-129bfd2e6ec2"
	local         = "ctx_25f8965ea0fb"
	localTerm     = "term_cfce7ff3-0976-4032-9d55-22697f68d6ee"
	remote        = "ctx_1e2e970fc485"
	remoteTerm    = "term_5cfcad9a-6928-47d6-88e3-f04749533661"
	gone          = "ctx_726698aaa803"
	environment   = "codex-hooks-proof"
	run           = "run_4104a86f0a03"
	hookTitle     = "Bash command · from the startup-speed agent"
	asked         = "Bash command · from the startup-speed agent / Time the current single-stream fetch with its SHA-256 check on the measurement Sprite as a same-host control / This shell -c script runs rm and could not be checked / Do you want to proceed? / ❯ 1. Yes / 2. No"
	supDesk       = "codex-supervisor"
	pollInterval  = 30 * time.Second
	instead       = "Leave the fetch alone and report what you measured so far."
	taskListKey   = "orchestration task-list --run " + run + " --status dispatched --brief"
	runCurrentKey = "orchestration run-current"
	boundListKey  = "orchestration worker-list --include-remote"
)

func fixture(t *testing.T, name string) string {
	t.Helper()
	data, err := os.ReadFile(filepath.Join("testdata", name+".json"))
	if err != nil {
		t.Fatal(err)
	}
	return string(data)
}

func edited(t *testing.T, name string, edit func(map[string]any)) string {
	t.Helper()
	var doc map[string]any
	if err := json.Unmarshal([]byte(fixture(t, name)), &doc); err != nil {
		t.Fatal(err)
	}
	edit(doc)
	data, err := json.Marshal(doc)
	if err != nil {
		t.Fatal(err)
	}
	return string(data)
}

func terminalOf(doc map[string]any) map[string]any {
	return doc["result"].(map[string]any)["terminal"].(map[string]any)
}

func tailOf(doc map[string]any) []any {
	return terminalOf(doc)["tail"].([]any)
}

func withTail(t *testing.T, name string, tail ...string) string {
	t.Helper()
	return edited(t, name, func(doc map[string]any) {
		lines := make([]any, len(tail))
		for i, line := range tail {
			lines[i] = line
		}
		terminalOf(doc)["tail"] = lines
	})
}

type fake struct {
	t         *testing.T
	mu        sync.Mutex
	clock     time.Time
	orca      map[string]string
	envs      map[string][]string
	calls     [][]string
	notices   []Notice
	seqs      []int64
	refuse    bool
	refuseCds bool
	cards     []map[string]string
	sends     [][]string
	sendReply string
	keys      [][]string
	onKeys    map[string]string
	mail      []map[string]string
}

func newFake(t *testing.T) *fake {
	return &fake{
		t:         t,
		clock:     time.Date(2026, 10, 10, 10, 24, 0, 0, time.UTC),
		orca:      map[string]string{},
		envs:      map[string][]string{},
		sendReply: fixture(t, "terminal-send.turn-started"),
	}
}

func (f *fake) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.calls = append(f.calls, c.Argv)
	switch c.Argv[0] {
	case "cc-slack":
		if f.refuseCds {
			return shell.Result{Code: 1}, errors.New("cc-slack: no Slack identity")
		}
		var card map[string]string
		if err := json.Unmarshal([]byte(c.Argv[5]), &card); err != nil {
			f.t.Fatal(err)
		}
		if c.Argv[3] != rootSession {
			f.t.Fatalf("card session = %q", c.Argv[3])
		}
		f.cards = append(f.cards, card)
		return shell.Result{}, nil
	case "orca":
	default:
		f.t.Fatalf("unexpected command %q", c.Argv)
	}
	args := slices.DeleteFunc(slices.Clone(c.Argv[1:len(c.Argv)-1]), func(word string) bool { return word == "--full" })
	if args[0] == "terminal" && args[1] == "send" {
		if slices.Contains(args, "--enter") {
			f.sends = append(f.sends, args)
			return f.answer(f.sendReply)
		}
		f.keys = append(f.keys, args[2:])
		for key, value := range f.onKeys {
			f.orca[key] = value
		}
		f.onKeys = nil
		return f.answer(fixture(f.t, "terminal-send.accepted"))
	}
	if args[0] == "orchestration" && args[1] == "send" {
		fields := map[string]string{}
		for i := 2; i+1 < len(args); i += 2 {
			fields[args[i]] = args[i+1]
		}
		f.mail = append(f.mail, fields)
		return f.answer(`{"ok":true,"result":{"message":{"id":"msg"}}}`)
	}
	key := strings.Join(args, " ")
	f.envs[key] = c.Env
	reply, known := f.orca[key]
	if !known {
		f.t.Fatalf("no reply scripted for orca %q", key)
	}
	return f.answer(reply)
}

func (f *fake) answer(reply string) (shell.Result, error) {
	var parsed struct {
		OK bool `json:"ok"`
	}
	if err := json.Unmarshal([]byte(reply), &parsed); err != nil {
		f.t.Fatal(err)
	}
	if parsed.OK {
		return shell.Result{Stdout: reply}, nil
	}
	return shell.Result{Code: 1, Stdout: reply}, &shell.ExitError{Code: 1}
}

func (f *fake) post(_ context.Context, n Notice) (int64, error) {
	if f.refuse {
		return 0, errors.New("cci: store locked")
	}
	f.notices = append(f.notices, n)
	seq := int64(45600 + len(f.seqs))
	f.seqs = append(f.seqs, seq)
	return seq, nil
}

func (f *fake) watch() *Watch {
	return &Watch{Runner: f, Post: f.post, Now: func() time.Time { return f.clock }, Sleep: func(d time.Duration) { f.clock = f.clock.Add(d) }}
}

func (f *fake) to(lane string) []Notice {
	return slices.DeleteFunc(slices.Clone(f.notices), func(n Notice) bool { return n.To != lane })
}

func (f *fake) kinds(topic string) []string {
	kinds := []string{}
	for _, n := range f.notices {
		if n.Topic == "prompt:"+topic {
			kinds = append(kinds, n.Kind)
		}
	}
	return kinds
}

func (f *fake) reads() []string {
	terminals := []string{}
	for _, call := range f.calls {
		if len(call) > 4 && call[0] == "orca" && call[1] == "terminal" && call[2] == "read" {
			terminals = append(terminals, call[4])
		}
	}
	return terminals
}

func showKey(terminal string) string { return "terminal show --terminal " + terminal }

func readKey(terminal, env string) string {
	key := "terminal read --terminal " + terminal + " --screen"
	if env != "" {
		key += " --environment " + env
	}
	return key
}

func workerKey(dispatch string) string { return "orchestration worker-show --dispatch " + dispatch }

func tuiKey() string {
	return "terminal wait --terminal " + supTerm + " --for tui-idle --timeout-ms 1000"
}

func unbound(f *fake) {
	f.orca[runCurrentKey] = `{"ok":true,"result":{"run":null}}`
}

func coordinatorAt(f *fake, state, screen string) {
	f.orca[showKey(rootTerm)] = fixture(f.t, "terminal-show.coordinator."+state)
	f.orca[readKey(rootTerm, "")] = fixture(f.t, "screen."+screen)
}

func supervisorAt(f *fake, screen string, idle bool) {
	f.orca[showKey(supTerm)] = fixture(f.t, "terminal-show.coordinator.clear")
	f.orca[readKey(supTerm, "")] = fixture(f.t, "screen."+screen)
	f.orca[tuiKey()] = fixture(f.t, "error.timeout")
	if idle {
		f.orca[tuiKey()] = fixture(f.t, "terminal-wait.idle")
	}
}

func register(t *testing.T, change func(*Entry)) Entry {
	t.Helper()
	dir := t.TempDir()
	t.Setenv("XDG_STATE_HOME", filepath.Join(dir, "state"))
	t.Setenv("CLAUDE_CONFIG_DIR", filepath.Join(dir, "config"))
	entry := Entry{Drive: "900424b6", StateDir: filepath.Join(dir, "scratch", "release-v3"), RootTerminal: rootTerm, SlackCards: true, Sessions: []string{"2a1f0c9e", rootSession}}
	if change != nil {
		change(&entry)
	}
	return entry
}

func supervisedEntry(t *testing.T) Entry {
	t.Helper()
	return register(t, func(e *Entry) {
		e.Desks = map[string]Desk{supDesk: {Terminal: supTerm, Supervisor: true}}
	})
}

func workersUp(t *testing.T, f *fake, localState string) Entry {
	t.Helper()
	entry := register(t, func(e *Entry) { e.OrcaRun = run })
	coordinatorAt(f, "clear", "coordinator.answered")
	f.orca[taskListKey] = fixture(t, "task-list")
	f.orca[workerKey(local)] = fixture(t, "worker-show.local."+localState)
	f.orca[workerKey(remote)] = fixture(t, "worker-show.remote")
	f.orca[readKey(remoteTerm, environment)] = fixture(t, "screen.managed-server.working")
	f.orca[workerKey(gone)] = fixture(t, "error.remote-runtime-unavailable")
	receipts, err := launchReceipts(run)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(receipts, 0o750); err != nil {
		t.Fatal(err)
	}
	receipt := fmt.Sprintf(`{"ok":true,"result":{"taskId":"task_8c1d2e3f4a5b","dispatchId":%q}}`, local)
	if err := os.WriteFile(filepath.Join(receipts, "bake.json"), []byte(receipt), 0o600); err != nil {
		t.Fatal(err)
	}
	return entry
}

func poll(t *testing.T, f *fake, e Entry, times int) map[string]*Row {
	t.Helper()
	for range times {
		if err := f.watch().Check(context.Background(), e); err != nil {
			t.Fatal(err)
		}
		f.clock = f.clock.Add(pollInterval)
	}
	return rowsOf(t, e)
}

func rowsOf(t *testing.T, e Entry) map[string]*Row {
	t.Helper()
	state, err := loadState(e.statePath())
	if err != nil {
		t.Fatal(err)
	}
	return state.Subjects
}

func whole(t *testing.T, n Notice) string {
	t.Helper()
	if len([]rune(n.Text)) > textLimit {
		t.Fatalf("%d runes over cci's limit: %q", len([]rune(n.Text)), n.Text)
	}
	if _, path, cut := strings.Cut(n.Text, " Whole line: "); cut {
		data, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		return strings.TrimRight(string(data), "\n")
	}
	return n.Text
}

func notice(name, role, waits, title, capture, read, tail string) string {
	return fmt.Sprintf("PROMPT %s (%s) waits on %s (%s). %s Screen: %s. Read: %s.", name, role, waits, title, tail, capture, read)
}

func answerLine(target, episode, by string) string {
	return fmt.Sprintf("Answer: lr watch prompts answer %s --episode %s --by %s yes|no.", target, episode, by)
}

func want[T comparable](t *testing.T, got, expected T) {
	t.Helper()
	if got != expected {
		t.Fatalf("got %v, want %v", got, expected)
	}
}

func wantSlice[T comparable](t *testing.T, got []T, expected ...T) {
	t.Helper()
	if !slices.Equal(got, expected) && (len(got) != 0 || len(expected) != 0) {
		t.Fatalf("got %v, want %v", got, expected)
	}
}

func recipients(f *fake) []string {
	to := make([]string, 0, len(f.notices))
	for _, n := range f.notices {
		to = append(to, n.To)
	}
	return to
}

func TestAHookApprovalOnTheCoordinatorGoesOnceToTheSupervisorAndNeverToTheOwnersCard(t *testing.T) {
	f := newFake(t)
	e := supervisedEntry(t)
	unbound(f)
	coordinatorAt(f, "waiting", "coordinator.approval")
	supervisorAt(f, "supervisor.codex-busy", false)

	rows := poll(t, f, e, 3)

	row := rows[rootTerm]
	want(t, row.State, Approval)
	want(t, *row.Since, 1791627768779)
	wantSlice(t, recipients(f), supDesk)
	n := f.notices[0]
	want(t, n.Kind, "blocker")
	want(t, n.Topic, "prompt:coordinator")
	want(t, whole(t, n), notice("coordinator", "coordinator", "an approval dialog", hookTitle, row.Capture, "orca terminal read --terminal "+rootTerm+" --screen",
		"Answer only within what the owner already authorized, else ask the owner. "+answerLine("--terminal "+rootTerm, row.Episode, supDesk)))
	want(t, row.Detail, "screen saved at "+row.Capture+"; supervisor is busy")
	want(t, len(f.sends), 0)
	want(t, len(f.cards), 0)
	if row.Wake != nil {
		t.Fatalf("wake = %+v", row.Wake)
	}
}

func TestTheDialogsTextStaysInAnOwnerOnlyFileUnderTheDrivesStateDirectory(t *testing.T) {
	f := newFake(t)
	e := supervisedEntry(t)
	unbound(f)
	coordinatorAt(f, "waiting", "coordinator.approval")
	supervisorAt(f, "managed-server.working", true)

	rows := poll(t, f, e, 11)

	capture := rows[rootTerm].Capture
	want(t, filepath.Dir(capture), filepath.Join(e.StateDir, "prompt-watch", "screens"))
	info, err := os.Stat(capture)
	if err != nil {
		t.Fatal(err)
	}
	want(t, info.Mode().Perm(), os.FileMode(0o600))
	dir, err := os.Stat(filepath.Dir(capture))
	if err != nil {
		t.Fatal(err)
	}
	want(t, dir.Mode().Perm(), os.FileMode(0o700))
	data, err := os.ReadFile(capture)
	if err != nil {
		t.Fatal(err)
	}
	var saved struct {
		Asked    string   `json:"asked"`
		Tail     []string `json:"tail"`
		Terminal string   `json:"terminal"`
		State    State    `json:"state"`
	}
	if err := json.Unmarshal(data, &saved); err != nil {
		t.Fatal(err)
	}
	want(t, saved.Asked, asked)
	want(t, saved.Terminal, rootTerm)
	want(t, saved.State, Approval)
	if !slices.Contains(saved.Tail, " ❯ 1. Yes") {
		t.Fatalf("tail = %q", saved.Tail)
	}
	wantSlice(t, recipients(f), supDesk)
	want(t, len(f.cards), 1)
	want(t, len(f.sends), 1)
	shown, err := View(e, f.clock)
	if err != nil {
		t.Fatal(err)
	}
	rowsJSON, err := json.Marshal(rows)
	if err != nil {
		t.Fatal(err)
	}
	cardsJSON, err := json.Marshal(f.cards)
	if err != nil {
		t.Fatal(err)
	}
	elsewhere := []string{whole(t, f.notices[0]), f.sends[0][5], string(cardsJSON), shown.Text(), string(rowsJSON)}
	for _, text := range elsewhere {
		for _, words := range []string{"runs rm", "Do you want to proceed", "Kick it off", "Would you like to run", "rm -rf"} {
			if strings.Contains(text, words) {
				t.Fatalf("%q leaked into %q", words, text)
			}
		}
	}
}

func TestADriveStartedWithoutSlackCardsPostsNoCardForTheCoordinatorsPrompt(t *testing.T) {
	f := newFake(t)
	e := register(t, func(e *Entry) { e.SlackCards = false })
	unbound(f)
	coordinatorAt(f, "waiting", "coordinator.approval")

	rows := poll(t, f, e, 2)

	want(t, len(f.cards), 0)
	want(t, len(f.notices), 0)
	want(t, len(rows[rootTerm].Alerts), 0)
}

func TestWithNoSupervisorTheCoordinatorsPromptIsOneNeedsYouCardInTheNewestRootSessionsStatusThread(t *testing.T) {
	f := newFake(t)
	e := register(t, nil)
	unbound(f)
	coordinatorAt(f, "waiting", "coordinator.approval")

	rows := poll(t, f, e, 2)

	want(t, len(f.notices), 0)
	want(t, len(f.cards), 1)
	card := f.cards[0]
	want(t, card["status"], "needs_you")
	want(t, card["title"], "The coordinator terminal waits on an approval dialog")
	want(t, card["drive"], "release-v3")
	want(t, card["body"], notice("coordinator", "coordinator", "an approval dialog", hookTitle, rows[rootTerm].Capture, "orca terminal read --terminal "+rootTerm+" --screen", "It froze the coordinator; no supervisor desk is registered. Answer it in that terminal."))
}

func TestTheOwnersCardIsFollowedByOneInfoCardOnceThePromptClears(t *testing.T) {
	f := newFake(t)
	e := register(t, nil)
	unbound(f)
	coordinatorAt(f, "waiting", "coordinator.approval")
	poll(t, f, e, 1)
	f.orca[readKey(rootTerm, "")] = fixture(t, "screen.coordinator.answered")

	rows := poll(t, f, e, 3)

	want(t, len(f.notices), 0)
	want(t, len(rows[rootTerm].Alerts), 0)
	want(t, len(f.cards), 2)
	want(t, f.cards[0]["status"], "needs_you")
	want(t, f.cards[1]["status"], "info")
	want(t, f.cards[1]["title"], "The coordinator terminal no longer waits on a prompt")
	want(t, f.cards[1]["body"], "CLEARED coordinator (coordinator) terminal="+rootTerm+": the prompt left the screen")
}

func TestASupervisorThatCannotActSendsTheCoordinatorsPromptToTheOwner(t *testing.T) {
	cases := []struct{ show, read, why string }{
		{"terminal-show.coordinator.waiting", "screen.coordinator.question", "supervisor codex-supervisor is question"},
		{"error.remote-runtime-unavailable", "", "supervisor codex-supervisor is unreachable"},
		{"error.terminal-handle-stale", "", "supervisor codex-supervisor is unknown"},
	}
	for _, c := range cases {
		t.Run(c.why, func(t *testing.T) {
			f := newFake(t)
			e := supervisedEntry(t)
			unbound(f)
			coordinatorAt(f, "waiting", "coordinator.approval")
			f.orca[showKey(supTerm)] = fixture(t, c.show)
			if c.read != "" {
				f.orca[readKey(supTerm, "")] = fixture(t, c.read)
			}

			poll(t, f, e, 1)

			want(t, len(f.cards), 1)
			if !strings.Contains(f.cards[0]["body"], " It froze the coordinator; "+c.why+". Answer it in that terminal. Screen: ") {
				t.Fatalf("body = %q", f.cards[0]["body"])
			}
			want(t, len(f.to(supDesk)), 0)
		})
	}
}

func TestAPromptStillOpenFiveMinutesAfterTheSupervisorHeardBecomesOneOwnerCard(t *testing.T) {
	f := newFake(t)
	e := supervisedEntry(t)
	unbound(f)
	coordinatorAt(f, "waiting", "coordinator.approval")
	supervisorAt(f, "supervisor.codex-busy", false)

	poll(t, f, e, 9)
	wantSlice(t, recipients(f), supDesk)
	want(t, len(f.cards), 0)
	poll(t, f, e, 4)

	wantSlice(t, recipients(f), supDesk)
	want(t, len(f.cards), 1)
	if !strings.Contains(f.cards[0]["body"], " It froze the coordinator; supervisor codex-supervisor stayed busy for 5m. Answer it in that terminal. Screen: ") {
		t.Fatalf("body = %q", f.cards[0]["body"])
	}
	want(t, len(f.sends), 0)
}

func TestAnIdleSupervisorGetsTheRecordsLineTypedOnceIntoItsOwnTerminal(t *testing.T) {
	cases := []struct{ screen, receipt, request string }{
		{"managed-server.working", "terminal-send.codex-pilot", "82f8014f-c164-4856-b77b-bac9e2338ae4"},
		{"supervisor.claude-idle", "terminal-send.turn-started", "02c7edf3-c3d1-4ec3-b3ed-c1d125c8be82"},
	}
	for _, c := range cases {
		t.Run(c.screen, func(t *testing.T) {
			f := newFake(t)
			e := supervisedEntry(t)
			unbound(f)
			coordinatorAt(f, "waiting", "coordinator.approval")
			supervisorAt(f, c.screen, true)
			f.sendReply = fixture(t, c.receipt)

			rows := poll(t, f, e, 3)

			want(t, len(f.notices), 1)
			want(t, len(f.sends), 1)
			want(t, f.sends[0][5], whole(t, f.notices[0]))
			wake := rows[rootTerm].Wake
			want(t, wake.Request, c.request)
			wantSlice(t, wake.Stages, "input_accepted", "turn_started")
			want(t, wake.Reached, "turn_started")
			if !strings.HasSuffix(rows[rootTerm].Detail, "; supervisor got one line and its turn started") {
				t.Fatalf("detail = %q", rows[rootTerm].Detail)
			}
			poll(t, f, e, 8)
			wantSlice(t, recipients(f), supDesk)
			want(t, len(f.cards), 1)
			want(t, len(f.sends), 1)
			if !strings.Contains(f.cards[0]["body"], "supervisor codex-supervisor got the line 5m ago, its turn started, and it is still open") {
				t.Fatalf("body = %q", f.cards[0]["body"])
			}
		})
	}
}

func TestALineOrcaAcceptedWithNoTurnStartIsKeptAsAcceptedAndNeverTypedAgain(t *testing.T) {
	f := newFake(t)
	e := supervisedEntry(t)
	unbound(f)
	coordinatorAt(f, "waiting", "coordinator.approval")
	supervisorAt(f, "managed-server.working", true)
	f.sendReply = fixture(t, "terminal-send.accepted")

	rows := poll(t, f, e, 4)

	want(t, rows[rootTerm].Wake.Reached, "input_accepted")
	wantSlice(t, rows[rootTerm].Wake.Stages, "input_accepted")
	want(t, len(f.sends), 1)
	if !strings.HasSuffix(rows[rootTerm].Detail, "; supervisor got one line and Orca accepted it and saw no turn start") {
		t.Fatalf("detail = %q", rows[rootTerm].Detail)
	}
	wantSlice(t, recipients(f), supDesk)
}

func TestAWorkingClaudeSupervisorReadsBusyAndGetsNoLineEvenWhenOrcaCallsItIdle(t *testing.T) {
	f := newFake(t)
	e := supervisedEntry(t)
	unbound(f)
	coordinatorAt(f, "waiting", "coordinator.approval")
	supervisorAt(f, "coordinator.answered", true)

	rows := poll(t, f, e, 2)

	want(t, len(f.sends), 0)
	want(t, rows[rootTerm].SupervisorInput, "busy")
}

func TestABusySupervisorIsWokenOnTheFirstPollThatFindsItIdle(t *testing.T) {
	f := newFake(t)
	e := supervisedEntry(t)
	unbound(f)
	coordinatorAt(f, "waiting", "coordinator.approval")
	supervisorAt(f, "supervisor.codex-busy", false)
	poll(t, f, e, 2)
	want(t, len(f.sends), 0)
	f.orca[tuiKey()] = fixture(t, "terminal-wait.idle")
	poll(t, f, e, 1)
	want(t, len(f.sends), 0)
	f.orca[readKey(supTerm, "")] = fixture(t, "screen.managed-server.working")

	rows := poll(t, f, e, 2)

	want(t, len(f.sends), 1)
	want(t, len(f.notices), 1)
	want(t, rows[rootTerm].SupervisorInput, "")
}

func TestAWakeOrcaRefusesIsAnOwnerCardAtOnceAndIsNotTypedAgain(t *testing.T) {
	cases := []struct{ reply, failure string }{
		{"error.terminal-handle-stale", "terminal_handle_stale: terminal_handle_stale"},
		{"terminal-send.refused", "input_refused: Orca gave no reason"},
	}
	for _, c := range cases {
		t.Run(c.reply, func(t *testing.T) {
			f := newFake(t)
			e := supervisedEntry(t)
			unbound(f)
			coordinatorAt(f, "waiting", "coordinator.approval")
			supervisorAt(f, "managed-server.working", true)
			f.sendReply = fixture(t, c.reply)

			rows := poll(t, f, e, 3)

			want(t, rows[rootTerm].Wake.Error, c.failure)
			wantSlice(t, recipients(f), supDesk)
			want(t, len(f.cards), 1)
			want(t, len(f.sends), 1)
			if !strings.Contains(f.cards[0]["body"], "the wake to supervisor codex-supervisor failed with "+c.failure) {
				t.Fatalf("body = %q", f.cards[0]["body"])
			}
		})
	}
}

func TestNoLineIsEverTypedIntoATerminalThatWaitsOrForAWorkersPrompt(t *testing.T) {
	f := newFake(t)
	e := workersUp(t, f, "waiting")
	f.orca[readKey(localTerm, "")] = fixture(t, "screen.coordinator.approval")
	f.orca[showKey(rootTerm)] = fixture(t, "terminal-show.coordinator.waiting")
	f.orca[readKey(rootTerm, "")] = fixture(t, "screen.coordinator.approval")

	poll(t, f, e, 3)

	want(t, len(f.sends), 0)
	want(t, len(f.keys), 0)
}

func shownCount(f *fake, dispatch string) int {
	count := 0
	for _, call := range f.calls {
		if strings.Contains(strings.Join(call, " "), workerKey(dispatch)) {
			count++
		}
	}
	return count
}

func TestAClearWorkerIsReadAgainOnlyAfterTheWindow(t *testing.T) {
	f := newFake(t)
	e := workersUp(t, f, "clear")
	w := f.watch()
	w.Reshow = reshowEvery
	check := func(shown int) {
		t.Helper()
		if err := w.Check(context.Background(), e); err != nil {
			t.Fatal(err)
		}
		f.clock = f.clock.Add(pollInterval)
		want(t, shownCount(f, local), shown)
	}

	check(1)
	check(1)

	f.clock = f.clock.Add(reshowEvery * 5 / 4)
	check(2)
}

func TestAPollCostsTheSameOrcaCallsAtAnyRunHistory(t *testing.T) {
	calls := func(retained int) int {
		f := newFake(t)
		e := workersUp(t, f, "clear")
		rows := make([]string, retained)
		for i := range rows {
			rows[i] = fmt.Sprintf(`{"dispatchId":"ctx_old%d","dispatchStatus":"completed","projection":{"outcome":"succeeded"}}`, i)
		}
		for page := 0; page*100 < retained; page++ {
			key := "orchestration worker-list --run " + run + " --include-remote"
			if page > 0 {
				key += fmt.Sprintf(" --cursor c%d", page)
			}
			more := (page+1)*100 < retained
			f.orca[key] = fmt.Sprintf(`{"ok":true,"result":{"workers":[%s],"page":{"hasMore":%t,"nextCursor":"c%d"},"scope":{"run":%q,"source":"flag"}}}`, strings.Join(rows[page*100:min(retained, (page+1)*100)], ","), more, page+1, run)
		}
		poll(t, f, e, 1)
		count := 0
		for _, call := range f.calls {
			if call[0] == "orca" {
				count++
			}
		}
		return count
	}
	if few, many := calls(2000), calls(20000); few != many {
		t.Fatalf("a poll made %d Orca calls with 2,000 retained dispatches and %d with 20,000", few, many)
	}
}

func TestReshowWindowsStayWithinAQuarterOfTheSettingAndDiffer(t *testing.T) {
	w := &Watch{Reshow: reshowEvery}
	seen := map[time.Duration]bool{}
	for _, key := range []string{"dispatch:a", "dispatch:b", "dispatch:c", "dispatch:d"} {
		window := w.reshowOf(key)
		want(t, window == w.reshowOf(key), true)
		want(t, window >= reshowEvery*3/4 && window < reshowEvery*5/4, true)
		seen[window] = true
	}
	want(t, len(seen) > 1, true)
}

func TestAWaitingWorkerIsReadEveryPoll(t *testing.T) {
	f := newFake(t)
	e := workersUp(t, f, "waiting")
	f.orca[readKey(localTerm, "")] = fixture(t, "screen.coordinator.approval")
	w := f.watch()
	w.Reshow = reshowEvery

	for shown := range 3 {
		if err := w.Check(context.Background(), e); err != nil {
			t.Fatal(err)
		}
		f.clock = f.clock.Add(pollInterval)
		want(t, shownCount(f, local), shown+1)
	}
}

func TestAnAnsweredPromptReadsStaleAndResolvesWhatItRaised(t *testing.T) {
	f := newFake(t)
	e := supervisedEntry(t)
	unbound(f)
	coordinatorAt(f, "waiting", "coordinator.approval")
	supervisorAt(f, "supervisor.codex-busy", false)
	poll(t, f, e, 1)
	f.orca[readKey(rootTerm, "")] = fixture(t, "screen.coordinator.answered")

	rows := poll(t, f, e, 2)

	want(t, rows[rootTerm].State, Stale)
	want(t, len(rows[rootTerm].Alerts), 0)
	want(t, len(f.notices), 2)
	cleared := f.notices[1]
	want(t, cleared.Kind, "unblock")
	want(t, cleared.Resolves, f.seqs[0])
	want(t, cleared.To, "")
	want(t, cleared.Text, "CLEARED coordinator (coordinator) terminal="+rootTerm+": the prompt left the screen")
	shown, err := View(e, f.clock)
	if err != nil {
		t.Fatal(err)
	}
	want(t, shown.Rows[0].Line, "PROMPT-STALE coordinator coordinator 1m terminal="+rootTerm+": agentWait via hook is set and the input box is back")
}

func TestAStaleWaitAloneNeverRaisesARecord(t *testing.T) {
	f := newFake(t)
	e := register(t, nil)
	unbound(f)
	coordinatorAt(f, "waiting", "coordinator.answered")

	want(t, poll(t, f, e, 4)[rootTerm].State, Stale)
	want(t, len(f.notices), 0)
}

func TestOneDialogWhoseSinceOrcaKeepsRewritingStaysOneRecordOneLineAndOneOwnerCard(t *testing.T) {
	f := newFake(t)
	e := supervisedEntry(t)
	unbound(f)
	coordinatorAt(f, "waiting", "coordinator.approval")
	supervisorAt(f, "managed-server.working", true)
	captures := map[string]bool{}
	for since := int64(1791631247002); since < 1791631247002+12*14000; since += 14000 {
		f.orca[showKey(rootTerm)] = edited(t, "terminal-show.coordinator.waiting", func(doc map[string]any) {
			terminalOf(doc)["agentWait"].(map[string]any)["since"] = since
		})
		row := poll(t, f, e, 1)[rootTerm]
		captures[row.Capture] = true
		want(t, *row.Since, since)
	}
	wantSlice(t, recipients(f), supDesk)
	want(t, f.notices[0].Kind, "blocker")
	want(t, len(f.cards), 1)
	want(t, len(f.sends), 1)
	want(t, len(captures), 1)
	if !strings.Contains(f.cards[0]["body"], "supervisor codex-supervisor got the line 5m ago, its turn started, and it is still open") {
		t.Fatalf("body = %q", f.cards[0]["body"])
	}
}

func anotherCommand(t *testing.T) string {
	t.Helper()
	return edited(t, "screen.coordinator.approval", func(doc map[string]any) {
		tail := tailOf(doc)
		for i, line := range tail {
			tail[i] = strings.ReplaceAll(line.(string), "single-stream fetch", "eight-stream fetch")
		}
	})
}

func TestASecondPromptOnTheSameTerminalIsASecondCard(t *testing.T) {
	f := newFake(t)
	e := register(t, nil)
	unbound(f)
	coordinatorAt(f, "waiting", "coordinator.approval")
	earlier := poll(t, f, e, 1)[rootTerm].Capture
	f.orca[readKey(rootTerm, "")] = anotherCommand(t)

	row := poll(t, f, e, 1)[rootTerm]

	want(t, len(f.cards), 3)
	want(t, f.cards[0]["status"], "needs_you")
	want(t, f.cards[1]["status"], "info")
	want(t, f.cards[2]["status"], "needs_you")
	want(t, len(f.notices), 0)
	if row.Capture == earlier || !strings.Contains(f.cards[2]["body"], row.Capture) {
		t.Fatalf("capture %q earlier %q body %q", row.Capture, earlier, f.cards[2]["body"])
	}
}

func TestTheCoordinatorsOwnQuestionWaitsOnTheOwnerInItsTerminalAndRaisesNoRecord(t *testing.T) {
	for _, screen := range []string{"coordinator.question", "coordinator.preview-question"} {
		t.Run(screen, func(t *testing.T) {
			f := newFake(t)
			e := supervisedEntry(t)
			unbound(f)
			coordinatorAt(f, "waiting", screen)
			supervisorAt(f, "managed-server.working", true)

			row := poll(t, f, e, 12)[rootTerm]

			want(t, row.State, Question)
			want(t, len(row.Alerts), 0)
			if row.Capture == "" {
				t.Fatal("no capture")
			}
			want(t, len(f.notices), 0)
			want(t, len(f.sends), 0)
		})
	}
}

func TestAnApprovalThatGivesWayToTheCoordinatorsQuestionIsResolved(t *testing.T) {
	f := newFake(t)
	e := register(t, nil)
	unbound(f)
	coordinatorAt(f, "waiting", "coordinator.approval")
	poll(t, f, e, 1)
	f.orca[readKey(rootTerm, "")] = fixture(t, "screen.coordinator.question")

	row := poll(t, f, e, 3)[rootTerm]

	want(t, len(f.cards), 2)
	want(t, f.cards[1]["status"], "info")
	want(t, row.State, Question)
	want(t, len(row.Alerts), 0)
}

func TestTwoTerminalsAtTheSameDialogKeepAScreenEach(t *testing.T) {
	f := newFake(t)
	e := workersUp(t, f, "waiting")
	coordinatorAt(f, "waiting", "coordinator.approval")
	f.orca[readKey(localTerm, "")] = fixture(t, "screen.coordinator.approval")

	rows := poll(t, f, e, 1)

	if rows[rootTerm].Capture == rows["dispatch:"+local].Capture {
		t.Fatalf("one capture for two terminals: %q", rows[rootTerm].Capture)
	}
}

func TestAWaitWithAScreenThatShowsNoKnownPromptIsUnknownAndReportedOnceAfterThreePolls(t *testing.T) {
	f := newFake(t)
	e := register(t, nil)
	unbound(f)
	coordinatorAt(f, "waiting", "coordinator.unrecognised")

	poll(t, f, e, 2)
	want(t, len(f.notices), 0)
	row := poll(t, f, e, 3)[rootTerm]

	want(t, row.State, Unknown)
	want(t, len(f.notices), 1)
	report := f.notices[0]
	want(t, report.To, "desk-runner")
	want(t, report.Kind, "report")
	want(t, whole(t, report), "UNKNOWN coordinator (coordinator) terminal="+rootTerm+": agentWait via hook is set and the screen shows neither a known dialog nor an input box; screen saved at "+row.UnreadCapture+"; unread for 3 polls, so whether it waits on a prompt is not known")
	info, err := os.Stat(row.UnreadCapture)
	if err != nil {
		t.Fatal(err)
	}
	want(t, info.Mode().Perm(), os.FileMode(0o600))
	shown, err := View(e, f.clock)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasSuffix(shown.Header, "0 approval, 0 question, 0 stale, 1 unknown, 0 unreachable, 0 clear") {
		t.Fatalf("header = %q", shown.Header)
	}
}

func TestAScreenOrcaCannotRenderIsUnknownWhateverItsTextHolds(t *testing.T) {
	f := newFake(t)
	e := register(t, nil)
	unbound(f)
	coordinatorAt(f, "waiting", "unavailable")

	row := poll(t, f, e, 1)[rootTerm]

	want(t, row.State, Unknown)
	want(t, row.Detail, "no rendered screen (source screen-unavailable)")
	want(t, *row.Since, 1791627768779)
	want(t, row.Unseen, 1)
	want(t, row.UnseenState, Unknown)
}

func firstWords(f *fake) []string {
	words := make([]string, 0, len(f.notices))
	for _, n := range f.notices {
		words = append(words, strings.Fields(n.Text)[0])
	}
	return words
}

func TestAnUnreadTerminalReportsOnceAndReadsAgainOnlyAfterThreePollsReadIt(t *testing.T) {
	f := newFake(t)
	e := register(t, nil)
	unbound(f)
	coordinatorAt(f, "waiting", "unavailable")
	poll(t, f, e, 3)
	coordinatorAt(f, "clear", "coordinator.answered")
	poll(t, f, e, 1)
	coordinatorAt(f, "waiting", "unavailable")
	poll(t, f, e, 3)
	coordinatorAt(f, "clear", "coordinator.answered")
	poll(t, f, e, 2)
	wantSlice(t, firstWords(f), "UNKNOWN")

	row := poll(t, f, e, 1)[rootTerm]

	want(t, len(f.notices), 2)
	readable := f.notices[1]
	want(t, readable.To, "desk-runner")
	want(t, readable.Topic, "prompt:coordinator")
	want(t, whole(t, readable), "READABLE coordinator (coordinator) terminal="+rootTerm+": read for 3 polls in a row since the watch reported it unknown")
	want(t, row.State, Clear)
	want(t, row.Reported, State(""))
	want(t, row.Readable, 0)
	poll(t, f, e, 2)
	coordinatorAt(f, "waiting", "unavailable")
	poll(t, f, e, 3)
	wantSlice(t, firstWords(f), "UNKNOWN", "READABLE", "UNKNOWN")
}

func TestAWorkersQuestionWakesTheCoordinatorOnce(t *testing.T) {
	f := newFake(t)
	e := workersUp(t, f, "waiting")
	f.orca[readKey(localTerm, "")] = fixture(t, "screen.coordinator.question")

	rows := poll(t, f, e, 3)

	row := rows["dispatch:"+local]
	want(t, row.State, Question)
	want(t, row.Name, "bake")
	wantSlice(t, f.kinds("bake"), "blocker")
	want(t, whole(t, f.to("root")[0]), notice("bake", "worker", "a question dialog", "Test page", row.Capture, "orca terminal read --terminal "+localTerm+" --screen", "The watch answers nothing."))
	want(t, len(f.cards), 0)
	wantSlice(t, f.kinds(gone), "report")
}

func ticking(t *testing.T, remaining string) string {
	t.Helper()
	return edited(t, "screen.coordinator.approval", func(doc map[string]any) {
		tail := tailOf(doc)
		at := slices.IndexFunc(tail, func(line any) bool { return strings.Contains(line.(string), "Do you want to proceed?") })
		warned := fmt.Sprintf(" ⚠ Claude Code will automatically deny this request in %s, to avoid blocking progress on an unattended session", remaining)
		terminalOf(doc)["tail"] = slices.Insert(tail, at, any(warned))
	})
}

func TestAWorkersDialogWhoseAutoDenyCountdownTicksIsOneBlockerOneStillOpenReportAndOneClear(t *testing.T) {
	f := newFake(t)
	e := workersUp(t, f, "waiting")
	for _, remaining := range []string{"1:58", "1:26", "0:55"} {
		f.orca[readKey(localTerm, "")] = ticking(t, remaining)
		poll(t, f, e, 1)
	}
	wantSlice(t, f.kinds("bake"), "blocker")
	episode := rowsOf(t, e)["dispatch:"+local].Episode
	blocker := f.to("root")[0]
	if !strings.Contains(whole(t, blocker), "waits on an approval dialog ("+hookTitle+"; auto-denies in 1:58). "+answerLine("--dispatch "+local, episode, "root")+" Screen: ") {
		t.Fatalf("blocker = %q", whole(t, blocker))
	}

	poll(t, f, e, 20)

	wantSlice(t, f.kinds("bake"), "blocker", "report")
	reminder := f.to("root")[1]
	if !strings.Contains(whole(t, reminder), fmt.Sprintf("Still open 10m after #%d. %s Screen: ", f.seqs[0], answerLine("--dispatch "+local, episode, "root"))) {
		t.Fatalf("reminder = %q", whole(t, reminder))
	}

	f.orca[workerKey(local)] = fixture(t, "worker-show.local.clear")
	poll(t, f, e, 1)

	wantSlice(t, f.kinds("bake"), "blocker", "report", "unblock")
}

func TestALocalWorkerWithNoWaitCostsNoScreenReadAndASettledOneNoShow(t *testing.T) {
	f := newFake(t)
	e := workersUp(t, f, "clear")

	row := poll(t, f, e, 1)["dispatch:"+local]

	want(t, row.State, Clear)
	reads := f.reads()
	slices.Sort(reads)
	wantSlice(t, reads, rootTerm, remoteTerm)
	for _, call := range f.calls {
		if slices.Contains(call, "ctx_b4e069ef5d36") {
			t.Fatalf("settled worker was read: %q", call)
		}
	}
}

func TestAManagedServerWorkerSetsNoWaitSoItsScreenIsReadThroughItsEnvironment(t *testing.T) {
	f := newFake(t)
	e := workersUp(t, f, "clear")
	row := poll(t, f, e, 1)["dispatch:"+remote]
	want(t, row.State, Clear)
	want(t, row.Environment, environment)
	want(t, row.Terminal, remoteTerm)
	if row.Since != nil {
		t.Fatalf("since = %d", *row.Since)
	}
	f.orca[readKey(remoteTerm, environment)] = fixture(t, "screen.managed-server.approval")

	row = poll(t, f, e, 2)["dispatch:"+remote]

	want(t, row.State, Approval)
	wantSlice(t, f.kinds(remote), "blocker")
	want(t, whole(t, f.to("root")[0]), notice(remote, "worker", "an approval dialog", "• I need to clear the stale build directory before the bake can rerun.", row.Capture, "orca terminal read --terminal "+remoteTerm+" --screen --environment "+environment, answerLine("--dispatch "+remote, row.Episode, "root")))
}

func TestAnUnreachableRemoteHostIsItsOwnStateReportedOnceAndNeverAPrompt(t *testing.T) {
	f := newFake(t)
	e := workersUp(t, f, "clear")

	poll(t, f, e, 2)
	want(t, len(f.notices), 0)
	row := poll(t, f, e, 3)["dispatch:"+gone]

	want(t, row.State, Unreachable)
	want(t, len(f.notices), 1)
	report := f.notices[0]
	want(t, report.To, "desk-runner")
	want(t, report.Text, "UNREACHABLE "+gone+" (worker) terminal=unresolved: remote_runtime_unavailable: Could not connect to the remote Orca runtime.; unread for 3 polls, so whether it waits on a prompt is not known")
	shown, err := View(e, f.clock)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasSuffix(shown.Header, "0 approval, 0 question, 0 stale, 0 unknown, 1 unreachable, 3 clear") {
		t.Fatalf("header = %q", shown.Header)
	}
	want(t, shown.Rows[0].Line, "PROMPT-UNREACHABLE "+gone+" worker 2m terminal=unresolved: remote_runtime_unavailable: Could not connect to the remote Orca runtime.")
}

func TestARemoteScreenThatStopsAnsweringKeepsTheOpenPromptAndRaisesNothingNew(t *testing.T) {
	f := newFake(t)
	e := workersUp(t, f, "clear")
	f.orca[readKey(remoteTerm, environment)] = fixture(t, "screen.managed-server.approval")
	poll(t, f, e, 1)
	f.orca[readKey(remoteTerm, environment)] = fixture(t, "error.remote-runtime-unavailable")
	poll(t, f, e, 1)
	f.orca[readKey(remoteTerm, environment)] = fixture(t, "screen.managed-server.approval")

	row := poll(t, f, e, 1)["dispatch:"+remote]

	want(t, row.State, Approval)
	wantSlice(t, f.kinds(remote), "blocker")
}

func TestWithNoRunInTheRegistryTheWorkersComeFromTheRunBoundToTheCoordinatorsTerminal(t *testing.T) {
	f := newFake(t)
	e := register(t, nil)
	coordinatorAt(f, "clear", "coordinator.answered")
	f.orca[runCurrentKey] = fmt.Sprintf(`{"ok":true,"result":{"run":{"id":%q}}}`, run)
	f.orca[taskListKey] = fixture(t, "task-list")
	f.orca[workerKey(local)] = fixture(t, "worker-show.local.clear")
	f.orca[workerKey(remote)] = fixture(t, "worker-show.remote")
	f.orca[readKey(remoteTerm, environment)] = fixture(t, "screen.managed-server.working")
	f.orca[workerKey(gone)] = fixture(t, "error.remote-runtime-unavailable")

	rows := poll(t, f, e, 1)

	wantSlice(t, f.envs[runCurrentKey], terminalEnv+"="+rootTerm)
	for _, key := range []string{rootTerm, "dispatch:" + local, "dispatch:" + remote, "dispatch:" + gone} {
		if rows[key] == nil {
			t.Fatalf("no row %q", key)
		}
	}
	want(t, len(rows), 4)
}

func TestAnUnboundCoordinatorTerminalWatchesNoWorker(t *testing.T) {
	f := newFake(t)
	e := register(t, nil)
	coordinatorAt(f, "clear", "coordinator.answered")
	unbound(f)

	rows := poll(t, f, e, 1)

	want(t, len(rows), 1)
	if rows[rootTerm] == nil {
		t.Fatal("no coordinator row")
	}
}

func TestAWorkerThatSettlesWhileAtAPromptHasItsRecordResolved(t *testing.T) {
	f := newFake(t)
	e := workersUp(t, f, "waiting")
	f.orca[readKey(localTerm, "")] = fixture(t, "screen.coordinator.approval")
	poll(t, f, e, 1)
	f.orca[taskListKey] = edited(t, "task-list", func(doc map[string]any) {
		result := doc["result"].(map[string]any)
		result["tasks"] = result["tasks"].([]any)[1:]
	})

	rows := poll(t, f, e, 1)

	if rows["dispatch:"+local] != nil {
		t.Fatal("settled worker kept its row")
	}
	wantSlice(t, f.kinds("bake"), "blocker", "unblock")
	unblock := f.notices[len(f.notices)-1]
	want(t, unblock.Resolves, f.seqs[0])
	want(t, unblock.Text, "CLEARED bake (worker) terminal="+localTerm+": the terminal left the watch")
}

func TestAWorkerListThatFailsClosesNoPromptAndTheSamePromptIsNotRaisedAgain(t *testing.T) {
	cases := []struct {
		page  string
		state State
	}{
		{"error.remote-runtime-unavailable", Unreachable},
		{"error.terminal-handle-stale", Unknown},
	}
	for _, c := range cases {
		t.Run(c.page, func(t *testing.T) {
			f := newFake(t)
			e := workersUp(t, f, "waiting")
			f.orca[readKey(localTerm, "")] = fixture(t, "screen.coordinator.approval")
			poll(t, f, e, 1)
			f.orca[taskListKey] = fixture(t, c.page)

			rows := poll(t, f, e, 2)

			row := rows["dispatch:"+local]
			want(t, row.State, c.state)
			if !strings.HasPrefix(row.Detail, "last read approval; task-list failed with ") {
				t.Fatalf("detail = %q", row.Detail)
			}
			want(t, row.Alerts["root"].Seq, f.seqs[0])
			if !strings.HasPrefix(rows["dispatch:"+remote].Detail, "last read clear; ") {
				t.Fatalf("remote detail = %q", rows["dispatch:"+remote].Detail)
			}
			want(t, rows["workers"].State, c.state)
			wantSlice(t, f.kinds("bake"), "blocker")
			f.orca[taskListKey] = fixture(t, "task-list")

			rows = poll(t, f, e, 1)

			want(t, rows["dispatch:"+local].State, Approval)
			if rows["workers"] != nil {
				t.Fatal("workers row kept")
			}
			wantSlice(t, f.kinds("bake"), "blocker")
		})
	}
}

func TestAWorkerListThatFlapsReportsOnceAndReadsAgainAfterThreeAnsweredPolls(t *testing.T) {
	f := newFake(t)
	e := workersUp(t, f, "clear")
	for _, step := range []struct {
		page  string
		times int
	}{{"error.timeout", 3}, {"task-list", 1}, {"error.timeout", 3}} {
		f.orca[taskListKey] = fixture(t, step.page)
		poll(t, f, e, step.times)
	}
	f.orca[taskListKey] = fixture(t, "task-list")
	rows := poll(t, f, e, 2)
	want(t, rows["workers"].State, Clear)
	want(t, rows["workers"].Reported, Unknown)

	rows = poll(t, f, e, 1)

	if rows["workers"] != nil {
		t.Fatal("workers row kept")
	}
	lines := []string{}
	for _, n := range f.notices {
		if n.Topic == "prompt:task-list" {
			lines = append(lines, whole(t, n))
		}
	}
	wantSlice(t, lines,
		"UNKNOWN task-list (workers) terminal=unresolved: timeout: timeout; unread for 3 polls, so whether it waits on a prompt is not known",
		"READABLE task-list (workers) terminal=unresolved: read for 3 polls in a row since the watch reported it unknown",
	)
}

func orcaDown(f *fake, listing string) {
	f.orca[showKey(rootTerm)] = fixture(f.t, "error.runtime-unavailable")
	f.orca[listing] = fixture(f.t, "error.timeout")
}

func TestAnOrcaAppThatFlapsWithNoWorkerInProgressPostsNothing(t *testing.T) {
	f := newFake(t)
	e := register(t, nil)
	for range 2 {
		orcaDown(f, runCurrentKey)
		rows := poll(t, f, e, 4)
		want(t, rows[rootTerm].State, Unknown)
		want(t, rows["workers"].State, Unknown)
		if rows["runtime"] != nil {
			t.Fatal("runtime row with no worker in progress")
		}
		unbound(f)
		coordinatorAt(f, "clear", "coordinator.answered")
		rows = poll(t, f, e, 4)
		want(t, len(rows), 1)
		want(t, rows[rootTerm].State, Clear)
	}
	want(t, len(f.notices), 0)
}

func TestAnOrcaAppDownWhileWorkersAreInProgressIsOneReportAndOneReadable(t *testing.T) {
	f := newFake(t)
	e := workersUp(t, f, "clear")
	f.orca[workerKey(gone)] = fixture(t, "worker-show.local.clear")
	poll(t, f, e, 1)
	orcaDown(f, taskListKey)
	poll(t, f, e, 5)
	f.orca[taskListKey] = fixture(t, "task-list")
	coordinatorAt(f, "clear", "coordinator.answered")

	rows := poll(t, f, e, 3)

	if rows["runtime"] != nil {
		t.Fatal("runtime row kept")
	}
	want(t, rows["dispatch:"+local].State, Clear)
	want(t, len(f.notices), 2)
	want(t, f.notices[0].To, "desk-runner")
	want(t, f.notices[0].Topic, "prompt:orca")
	want(t, whole(t, f.notices[0]), "UNKNOWN orca (runtime) terminal=unresolved: Orca answers no watched terminal (runtime_unavailable, timeout); bake, "+remote+", "+gone+" were in progress at the last task-list and may wait on a prompt; unread for 3 polls, so whether it waits on a prompt is not known")
	want(t, whole(t, f.notices[1]), "READABLE orca (runtime) terminal=unresolved: read for 3 polls in a row since the watch reported it unknown")
}

func TestARecordCciRefusesIsPostedOnTheNextPoll(t *testing.T) {
	f := newFake(t)
	e := supervisedEntry(t)
	unbound(f)
	coordinatorAt(f, "waiting", "coordinator.approval")
	supervisorAt(f, "supervisor.codex-busy", false)
	f.refuse = true
	poll(t, f, e, 1)
	f.refuse = false

	poll(t, f, e, 2)

	wantSlice(t, recipients(f), supDesk)
}

func TestACardCcSlackRefusesIsPostedOnTheNextPoll(t *testing.T) {
	f := newFake(t)
	e := register(t, nil)
	unbound(f)
	coordinatorAt(f, "waiting", "coordinator.approval")
	f.refuseCds = true
	want(t, len(poll(t, f, e, 1)[rootTerm].Alerts), 0)
	f.refuseCds = false

	poll(t, f, e, 2)

	want(t, len(f.cards), 1)
	want(t, f.cards[0]["status"], "needs_you")
	want(t, len(f.notices), 0)
}

func TestShowNamesAWatchThatNeverPolledAndOneThatStopped(t *testing.T) {
	f := newFake(t)
	e := register(t, nil)
	shown, err := View(e, f.clock)
	if err != nil {
		t.Fatal(err)
	}
	want(t, shown.Header, "prompt-watch "+e.Drive+" has never polled")
	want(t, shown.Rows[0].State, down)
	unbound(f)
	coordinatorAt(f, "waiting", "coordinator.approval")
	row := poll(t, f, e, 1)[rootTerm]
	f.clock = f.clock.Add(240 * time.Second)

	shown, err = View(e, f.clock)
	if err != nil {
		t.Fatal(err)
	}

	want(t, shown.Header, "prompt-watch "+e.Drive+" polled 270s ago: 1 approval, 0 question, 0 stale, 0 unknown, 0 unreachable, 0 clear")
	if !strings.HasPrefix(shown.Rows[0].Line, "PROMPT-WATCH-DOWN "+e.Drive+": last poll 4m ago, so the states below are old") {
		t.Fatalf("line = %q", shown.Rows[0].Line)
	}
	want(t, shown.Rows[1].Line, "WAITING-ON-PROMPT coordinator coordinator approval 4m terminal="+rootTerm+": screen saved at "+row.Capture)
}

func TestFindReadsADriveByItsRegistryIdOrItsBusName(t *testing.T) {
	e := register(t, nil)
	dir := filepath.Join(os.Getenv("CLAUDE_CONFIG_DIR"), "long-running", "drives")
	if err := os.MkdirAll(dir, 0o750); err != nil {
		t.Fatal(err)
	}
	data, err := json.Marshal(map[string]any{"drive": e.Drive, "state_dir": e.StateDir, "orca_run": nil, "cci_drive": nil, "root_terminal": rootTerm, "desks": map[string]any{supDesk: map[string]any{"terminal": supTerm, "environment": nil, "supervisor": true}}})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, e.Drive+".json"), data, 0o600); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{e.Drive, "release-v3"} {
		found, err := Find(name)
		if err != nil {
			t.Fatal(err)
		}
		want(t, found.Drive, e.Drive)
		want(t, found.Desks[supDesk].Supervisor, true)
	}
	if _, err := Find("some-other-drive"); !errors.Is(err, ErrNoDrive) {
		t.Fatalf("err = %v", err)
	}
}

func TestAReplyInAShapeTheWatchDoesNotKnowMakesThatTerminalUnknownAndTheRestStillRead(t *testing.T) {
	f := newFake(t)
	e := workersUp(t, f, "clear")
	f.orca[workerKey(local)] = fmt.Sprintf(`{"ok":true,"result":{"terminal":{"handle":%q}}}`, localTerm)

	rows := poll(t, f, e, 1)

	want(t, rows["dispatch:"+local].State, Unknown)
	want(t, rows["dispatch:"+local].Detail, "unreadable Orca reply (no observation)")
	want(t, rows[rootTerm].State, Clear)
	want(t, rows["dispatch:"+remote].State, Clear)
}

func TestAStateFileThatDoesNotParseFailsTheCheckAndTheNextCheckRunsOnceItIsGone(t *testing.T) {
	f := newFake(t)
	e := register(t, nil)
	unbound(f)
	coordinatorAt(f, "clear", "coordinator.answered")
	if err := os.MkdirAll(e.dir(), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(e.statePath(), []byte("{"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := f.watch().Check(context.Background(), e); err == nil {
		t.Fatal("no error from an unparseable state file")
	}
	if err := os.Remove(e.statePath()); err != nil {
		t.Fatal(err)
	}

	want(t, poll(t, f, e, 1)[rootTerm].State, Clear)
}

func TestACheckWhileAnotherWatchHoldsTheLockReadsNothing(t *testing.T) {
	f := newFake(t)
	e := register(t, nil)
	if err := os.MkdirAll(e.dir(), 0o750); err != nil {
		t.Fatal(err)
	}
	held, err := os.OpenFile(filepath.Join(e.dir(), "lock"), os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	defer held.Close()
	if err := flock(held); err != nil {
		t.Fatal(err)
	}

	if err := f.watch().Check(context.Background(), e); !errors.Is(err, ErrHeld) {
		t.Fatalf("err = %v", err)
	}
	want(t, len(f.calls), 0)
}

func TestBindSetsWhatItNamesAndKeepsTheRestOfTheEntry(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("CLAUDE_CONFIG_DIR", dir)
	path := filepath.Join(dir, "long-running", "drives", "d1.json")
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(`{"drive":"d1","repo":"/r","count":12345678901234567,"root_terminal":"term_old","state_dir":"/s"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	read := func() map[string]json.RawMessage {
		data, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		var fields map[string]json.RawMessage
		if err := json.Unmarshal(data, &fields); err != nil {
			t.Fatal(err)
		}
		return fields
	}

	if err := Bind("d1", "term_new", false); err != nil {
		t.Fatal(err)
	}
	fields := read()
	want(t, string(fields["root_terminal"]), `"term_new"`)
	want(t, string(fields["repo"]), `"/r"`)
	want(t, string(fields["count"]), "12345678901234567")
	if _, set := fields["slack_cards"]; set {
		t.Fatal(fields)
	}

	if err := Bind("d1", "", true); err != nil {
		t.Fatal(err)
	}
	fields = read()
	want(t, string(fields["slack_cards"]), "true")
	want(t, string(fields["root_terminal"]), `"term_new"`)
}

func TestACheckQueuesAsSuccessorUntilTheHolderLetsGoThenPolls(t *testing.T) {
	f := newFake(t)
	e := workersUp(t, f, "clear")
	if err := os.MkdirAll(e.dir(), 0o750); err != nil {
		t.Fatal(err)
	}
	held, err := os.OpenFile(filepath.Join(e.dir(), "lock"), os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	if err := flock(held); err != nil {
		t.Fatal(err)
	}
	queued := func() bool {
		queue, err := os.OpenFile(filepath.Join(e.dir(), "successor"), os.O_CREATE|os.O_RDWR, 0o600)
		if err != nil {
			t.Fatal(err)
		}
		defer queue.Close()
		return flock(queue) != nil
	}
	watch := f.watch()

	for range 2 {
		if err := watch.Check(context.Background(), e); !errors.Is(err, ErrHeld) {
			t.Fatalf("err = %v", err)
		}
		want(t, queued(), true)
	}
	want(t, len(f.calls), 0)

	held.Close()
	if err := watch.Check(context.Background(), e); err != nil {
		t.Fatal(err)
	}
	want(t, len(f.calls) > 0, true)
	want(t, queued(), false)
}

func TestReceiptsAreReadOnlyForAWorkerStillWithoutALaneName(t *testing.T) {
	f := newFake(t)
	e := workersUp(t, f, "clear")
	poll(t, f, e, 1)
	receipts, err := launchReceipts(run)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(filepath.Join(receipts, "bake.json")); err != nil {
		t.Fatal(err)
	}

	want(t, poll(t, f, e, 1)["dispatch:"+local].Name, "bake")
}

func atDialog(f *fake, after string) {
	f.orca[workerKey(local)] = fixture(f.t, "worker-show.local.waiting")
	f.orca[readKey(localTerm, "")] = fixture(f.t, "screen.coordinator.approval")
	f.orca[workerKey(remote)] = fixture(f.t, "worker-show.remote")
	f.orca[readKey(remoteTerm, environment)] = fixture(f.t, "screen.managed-server.approval")
	f.orca[showKey(localTerm)] = fixture(f.t, "terminal-show.coordinator.waiting")
	if after == "" {
		after = fixture(f.t, "screen.supervisor.claude-idle")
	}
	f.onKeys = map[string]string{
		readKey(localTerm, ""):           after,
		readKey(remoteTerm, environment): withTail(f.t, "screen.managed-server.working", "• Left build/out in place.", "", "› "),
	}
}

func keysOf(terminal, text, env string) []string {
	keys := []string{"--terminal", terminal, "--text", text}
	if env != "" {
		keys = append(keys, "--environment", env)
	}
	return keys
}

func listedScope(t *testing.T, source, scopeRun string, workers bool) string {
	t.Helper()
	return edited(t, "worker-list", func(doc map[string]any) {
		result := doc["result"].(map[string]any)
		result["scope"] = map[string]any{"run": scopeRun, "source": source}
		if !workers {
			result["workers"] = []any{}
		}
	})
}

func TestYesTypesTheYesOptionsNumberAsRawKeysAndTheWorkersRunMailNamesWhoChoseWhat(t *testing.T) {
	f := newFake(t)
	atDialog(f, "")

	answered, err := f.watch().Answer(context.Background(), AnswerArgs{Dispatch: local, By: "root", Choice: "yes"})
	if err != nil {
		t.Fatal(err)
	}

	want(t, answered.OK, true)
	want(t, len(f.keys), 1)
	wantSlice(t, f.keys[0], keysOf(localTerm, "1", "")...)
	chose := fmt.Sprintf(`root chose "1. Yes" for %s on %s; the dialog closed`, local, localTerm)
	want(t, len(f.mail), 1)
	mail := f.mail[0]
	want(t, mail["--to"], "run:"+run)
	want(t, mail["--dispatch-id"], local)
	want(t, mail["--type"], "status")
	want(t, mail["--subject"], "root answered yes: "+hookTitle)
	if !strings.HasPrefix(mail["--body"], chose+". Prompt episode ") || strings.Contains(mail["--body"], "single-stream fetch") {
		t.Fatalf("body = %q", mail["--body"])
	}
	want(t, answered.Line, "ANSWERED "+chose+"; recorded in run:"+run)
}

func TestNoTypesNoThenSubmitsWhatToDoInsteadSoOrcaTakesPromptsAgain(t *testing.T) {
	f := newFake(t)
	atDialog(f, "")

	answered, err := f.watch().Answer(context.Background(), AnswerArgs{Dispatch: local, By: "root", Choice: "no", Instead: instead})
	if err != nil {
		t.Fatal(err)
	}

	want(t, answered.OK, true)
	want(t, len(f.keys), 3)
	wantSlice(t, f.keys[0], keysOf(localTerm, "2", "")...)
	wantSlice(t, f.keys[1], keysOf(localTerm, instead, "")...)
	wantSlice(t, f.keys[2], keysOf(localTerm, "\r", "")...)
	if !strings.Contains(f.mail[0]["--body"], `the dialog closed; told it instead: "`+instead+`". Prompt episode `) {
		t.Fatalf("body = %q", f.mail[0]["--body"])
	}
}

func TestNumberedLinesInTheCommandAboveTheOptionsNeverChooseTheAnswer(t *testing.T) {
	for _, c := range []struct{ choice, typed, instead string }{{"yes", "1", ""}, {"no", "2", instead}} {
		t.Run(c.choice, func(t *testing.T) {
			f := newFake(t)
			atDialog(f, "")
			f.orca[readKey(localTerm, "")] = edited(t, "screen.coordinator.approval", func(doc map[string]any) {
				tail := tailOf(doc)
				at := slices.IndexFunc(tail, func(line any) bool { return strings.Contains(line.(string), "Do you want to proceed?") })
				terminalOf(doc)["tail"] = slices.Insert(tail, at, any(" │ 1. No"), any(" │ 2. Yes"))
			})

			if _, err := f.watch().Answer(context.Background(), AnswerArgs{Dispatch: local, By: "root", Choice: c.choice, Instead: c.instead}); err != nil {
				t.Fatal(err)
			}

			wantSlice(t, f.keys[0], keysOf(localTerm, c.typed, "")...)
		})
	}
}

func TestARemoteCodexDialogIsAnsweredThroughItsServerWithItsOwnNumbering(t *testing.T) {
	for _, c := range []struct{ choice, typed, instead string }{{"yes", "1", ""}, {"no", "3", instead}} {
		t.Run(c.choice, func(t *testing.T) {
			f := newFake(t)
			atDialog(f, "")

			answered, err := f.watch().Answer(context.Background(), AnswerArgs{Dispatch: remote, By: "root", Choice: c.choice, Instead: c.instead})
			if err != nil {
				t.Fatal(err)
			}

			want(t, answered.OK, true)
			wantSlice(t, f.keys[0], keysOf(remoteTerm, c.typed, environment)...)
			want(t, f.mail[0]["--to"], "run:"+run)
			want(t, f.mail[0]["--dispatch-id"], remote)
		})
	}
}

func TestATerminalHandleFindsTheRunWhoseMailRecordsTheAnswerBeforeReadingTheDialog(t *testing.T) {
	cases := []struct {
		name     string
		scope    string
		dispatch string
	}{
		{"a worker's terminal names its dispatch", listedScope(t, "all", "", true), local},
		{"a worker that also coordinates keeps its dispatch", listedScope(t, "bound", run, true), local},
		{"a coordinator's terminal names its bound Run", listedScope(t, "bound", run, false), ""},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			f := newFake(t)
			atDialog(f, "")
			f.orca[boundListKey] = c.scope

			answered, err := f.watch().Answer(context.Background(), AnswerArgs{Terminal: localTerm, By: "root", Choice: "yes"})
			if err != nil {
				t.Fatal(err)
			}

			want(t, answered.OK, true)
			wantSlice(t, f.envs[boundListKey], terminalEnv+"="+localTerm)
			listIndex := slices.IndexFunc(f.calls, func(call []string) bool { return slices.Contains(call, "worker-list") })
			readIndex := slices.IndexFunc(f.calls, func(call []string) bool { return slices.Contains(call, "read") })
			if listIndex < 0 || readIndex < listIndex {
				t.Fatalf("list at %d, read at %d", listIndex, readIndex)
			}
			want(t, f.mail[0]["--to"], "run:"+run)
			want(t, f.mail[0]["--dispatch-id"], c.dispatch)
		})
	}
}

func trustDialog(t *testing.T) string {
	t.Helper()
	return withTail(t, "screen.coordinator.approval", strings.Repeat("─", 80), " Quick safety check: Is this a project you created or one you trust?", " ❯ No, exit", "   Yes, I trust this folder", " Enter to confirm · Esc to cancel")
}

func TestATerminalThatShowsNoAnswerableDialogGetsNoKeysAndNoMail(t *testing.T) {
	cases := []struct {
		name    string
		screen  string
		args    AnswerArgs
		refusal string
	}{
		{"no dialog", fixture(t, "screen.coordinator.answered"), AnswerArgs{Choice: "yes"}, "shows no approval dialog: it reads stale"},
		{"a question picker", fixture(t, "screen.coordinator.question"), AnswerArgs{Choice: "yes"}, "shows no approval dialog: it reads question"},
		{"another dialog than the record's", fixture(t, "screen.coordinator.approval"), AnswerArgs{Choice: "yes", Episode: "000000000000"}, "not 000000000000"},
		{"a no that says nothing to do instead", fixture(t, "screen.coordinator.approval"), AnswerArgs{Choice: "no"}, "--instead goes with no and only with no"},
		{"a yes with text to type", fixture(t, "screen.coordinator.approval"), AnswerArgs{Choice: "yes", Instead: instead}, "--instead goes with no and only with no"},
		{"options without numbers", trustDialog(t), AnswerArgs{Choice: "yes"}, "no numbered option"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			f := newFake(t)
			atDialog(f, "")
			f.orca[readKey(localTerm, "")] = c.screen
			c.args.Dispatch, c.args.By = local, "root"

			_, err := f.watch().Answer(context.Background(), c.args)

			if err == nil || !strings.Contains(err.Error(), c.refusal) {
				t.Fatalf("err = %v, want %q", err, c.refusal)
			}
			want(t, len(f.keys), 0)
			want(t, len(f.mail), 0)
		})
	}
}

func TestATerminalInNoRunAndOwnedByNoWorkerGetsNoKeys(t *testing.T) {
	f := newFake(t)
	atDialog(f, "")
	f.orca[boundListKey] = listedScope(t, "all", "", false)

	_, err := f.watch().Answer(context.Background(), AnswerArgs{Terminal: localTerm, By: "root", Choice: "yes"})

	if err == nil || !strings.Contains(err.Error(), "coordinates no Run") {
		t.Fatalf("err = %v", err)
	}
	want(t, len(f.keys), 0)
	want(t, len(f.mail), 0)
}

func TestKeysThatLeaveNoIdleInputBoxAreRecordedAndTypeNothingMore(t *testing.T) {
	cases := []struct {
		name, after string
		reads       State
	}{
		{"the same dialog", fixture(t, "screen.coordinator.approval"), Approval},
		{"another dialog", anotherCommand(t), Approval},
		{"an unreadable screen", fixture(t, "screen.unavailable"), Unknown},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			f := newFake(t)
			atDialog(f, c.after)

			answered, err := f.watch().Answer(context.Background(), AnswerArgs{Dispatch: local, By: "root", Choice: "no", Instead: instead})
			if err != nil {
				t.Fatal(err)
			}

			want(t, answered.OK, false)
			want(t, len(f.keys), 1)
			wantSlice(t, f.keys[0], keysOf(localTerm, "2", "")...)
			prefix := fmt.Sprintf(`root chose "2. No" for %s on %s; after the keys the terminal reads %s`, local, localTerm, c.reads)
			if !strings.HasPrefix(f.mail[0]["--body"], prefix) {
				t.Fatalf("body = %q", f.mail[0]["--body"])
			}
		})
	}
}

func TestAClosedDialogOverABusyInputBoxGetsNoTextToDoInstead(t *testing.T) {
	f := newFake(t)
	atDialog(f, fixture(t, "screen.coordinator.answered"))

	answered, err := f.watch().Answer(context.Background(), AnswerArgs{Dispatch: local, By: "root", Choice: "no", Instead: instead})
	if err != nil {
		t.Fatal(err)
	}

	want(t, answered.OK, false)
	want(t, len(f.keys), 1)
	if !strings.Contains(f.mail[0]["--body"], "its input box is not empty and idle, so nothing was typed instead") {
		t.Fatalf("body = %q", f.mail[0]["--body"])
	}
}

func TestTheCommandInAWorkersBlockerAnswersThatDialog(t *testing.T) {
	f := newFake(t)
	e := workersUp(t, f, "waiting")
	f.orca[readKey(localTerm, "")] = fixture(t, "screen.coordinator.approval")
	poll(t, f, e, 1)
	_, after, _ := strings.Cut(whole(t, f.to("root")[0]), "Answer: ")
	command, _, _ := strings.Cut(after, " yes|no.")
	words := strings.Fields(command)
	wantSlice(t, words[:4], "lr", "watch", "prompts", "answer")
	args := AnswerArgs{Dispatch: words[5], Episode: words[7], By: words[9], Choice: "yes"}
	atDialog(f, "")

	answered, err := f.watch().Answer(context.Background(), args)
	if err != nil {
		t.Fatal(err)
	}

	want(t, answered.OK, true)
	wantSlice(t, f.keys[0], keysOf(localTerm, "1", "")...)
	if !strings.HasPrefix(f.mail[0]["--subject"], "root answered yes") {
		t.Fatalf("subject = %q", f.mail[0]["--subject"])
	}
}
