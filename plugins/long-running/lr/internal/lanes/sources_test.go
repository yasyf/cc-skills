package lanes

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

type scripted func(argv []string) (shell.Result, error)

func (s scripted) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	return s(c.Argv)
}

func writeJSON(t *testing.T, path string, value any) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		t.Fatal(err)
	}
	data, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, data, 0o600); err != nil {
		t.Fatal(err)
	}
}

func assistant(tokens int) string {
	return fmt.Sprintf(`{"type":"assistant","message":{"usage":{"input_tokens":10,"cache_creation_input_tokens":90,"cache_read_input_tokens":%d}}}`, tokens-100)
}

func TestContextSizeIsTheNewestAssistantTurn(t *testing.T) {
	path := filepath.Join(t.TempDir(), "agent.jsonl")
	text := strings.Join([]string{assistant(200_000), `{"type":"user","message":{"content":"go"}}`, assistant(640_000), `{"type":"user","message":{"content":"ok"}}`}, "\n") + "\n"
	if err := os.WriteFile(path, []byte(text), 0o600); err != nil {
		t.Fatal(err)
	}

	tokens, err := lastTokens(path)
	if err != nil {
		t.Fatal(err)
	}
	equal(t, tokens, 640_000)
}

func TestACompactionAfterTheLastTurnMeansNoReading(t *testing.T) {
	path := filepath.Join(t.TempDir(), "agent.jsonl")
	if err := os.WriteFile(path, []byte(assistant(900_000)+"\n"+`{"type":"system","subtype":"compact_boundary"}`+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	tokens, err := lastTokens(path)
	if err != nil {
		t.Fatal(err)
	}
	equal(t, tokens, 0)
}

func TestEveryTeammateOfALiveLeadSessionReadsLiveAndCarriesItsTeam(t *testing.T) {
	teams := t.TempDir()
	writeJSON(t, filepath.Join(teams, "session-d50e80e8", "config.json"), map[string]any{"members": []map[string]string{{"name": "team-lead"}, {"name": "flock-race-1"}, {"name": "ci-github-403-1"}}})
	rows := []Row{
		{Lane: Root, Session: "d50e80e8-7ba3", Live: true},
		{Lane: "flock-race-1", Session: "d50e80e8-7ba3", Agent: "aflock-race-1-01"},
		{Lane: "ci-github-403-1", Session: "d50e80e8-7ba3", Agent: "aci-github-403-1-01", Live: true},
		{Lane: "gone-lane", Session: "d50e80e8-7ba3", Agent: "agone-lane-01"},
		{Lane: "orphan", Session: "0badc0de-1111", Agent: "aorphan-01"},
	}

	out := Rostered(rows, teams)

	team := filepath.Join(teams, "session-d50e80e8")
	live := make([]bool, 0, len(out))
	rosters := make([]string, 0, len(out))
	for _, row := range out {
		live = append(live, row.Live)
		rosters = append(rosters, row.Roster)
	}
	equal(t, live, []bool{true, true, true, false, false})
	equal(t, rosters, []string{"", team, team, "", ""})
}

func TestAWakeReachesARosterLanesInboxAndAnOrcaLanesDispatch(t *testing.T) {
	team := filepath.Join(t.TempDir(), "teams", "session-d50e80e8")
	var sent []string
	refuse := false
	waker := &Waker{Drive: testDrive, Clock: func() time.Time { return t0 }, Send: func(dispatch, subject, body string) error {
		if refuse {
			return errors.New("orca refused")
		}
		sent = append(sent, dispatch+" "+subject+" "+body)
		return nil
	}, Unread: func(string, string) (bool, error) { return false, nil }}
	page := map[string]any{"why": "this blocker on your PR #46475 arrived after your last record: STALL red #46475 at 89f46c3", "prs": []int{46475}}

	woken, err := waker.Wake("done-lane", Row{Session: "d50e80e8-7ba3", Roster: team}, page)
	if err != nil || !woken {
		t.Fatal(woken, err)
	}
	var inbox []map[string]any
	data, err := os.ReadFile(filepath.Join(team, "inboxes", "done-lane.json"))
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(data, &inbox); err != nil {
		t.Fatal(err)
	}
	equal(t, len(inbox), 1)
	equal(t, inbox[0]["from"], wakeSender)
	contains(t, inbox[0]["text"].(string), "WAKE done-lane: this blocker on your PR #46475 arrived after your last record")
	contains(t, inbox[0]["text"].(string), "do not restart your brief")
	if _, err := os.Stat(filepath.Join(team, "inboxes", "done-lane.json.lock")); err == nil {
		t.Fatal("lock left behind")
	}

	woken, err = waker.Wake("orca-lane", Row{Dispatch: "ctx_af31953664f5"}, page)
	if err != nil || !woken {
		t.Fatal(woken, err)
	}
	equal(t, len(sent), 1)
	contains(t, sent[0], "ctx_af31953664f5 WAKE orca-lane: continue the work in hand WAKE orca-lane: this blocker")

	refuse = true
	woken, err = waker.Wake("orca-lane", Row{Dispatch: "ctx_af31953664f5"}, page)
	if err != nil || woken {
		t.Fatalf("a refused send = %v, %v; want it unwoken, for the next tick to retry, with the tick unharmed", woken, err)
	}
}

func TestAnOrcaWakeFromLaunchdSendsAsTheRunsCoordinator(t *testing.T) {
	var sent string
	env := Env{Config: config.Config{OrcaRun: "run_1"}, Now: func() time.Time { return t0 }, Runner: scripted(func(argv []string) (shell.Result, error) {
		if strings.Contains(strings.Join(argv, " "), "run-show") {
			return shell.Result{Stdout: `{"ok":true,"result":{"run":{"id":"run_1","coordinator_handle":"term_coordinator"}}}`}, nil
		}
		sent = strings.Join(argv, " ")
		return shell.Result{Stdout: `{"ok":true}`}, nil
	})}

	if err := env.sendDispatch(context.Background(), "ctx_1", "WAKE a", "continue"); err != nil {
		t.Fatal(err)
	}

	contains(t, sent, "--from term_coordinator --to dispatch:ctx_1 --run run_1 --type dispatch")
}

func TestAnOrcaLaneHoldingAnUnreadWakeGetsNoSecondOneUntilItReadsIt(t *testing.T) {
	local := t.TempDir()
	env := Env{Config: config.Config{OrcaRun: "run_1"}, Local: local, Now: func() time.Time { return t0 }}
	if err := os.MkdirAll(env.receipts(), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(env.receipts(), "go-sweep.terminal"), []byte("term_lane\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	var inbox []string
	sends := 0
	env.Runner = scripted(func(argv []string) (shell.Result, error) {
		line := strings.Join(argv, " ")
		switch {
		case strings.Contains(line, "run-show"):
			return shell.Result{Stdout: `{"ok":true,"result":{"run":{"coordinator_handle":"term_coordinator"}}}`}, nil
		case strings.Contains(line, "check --terminal term_lane --run run_1 --peek"):
			return shell.Result{Stdout: `{"ok":true,"result":{"messages":[` + strings.Join(inbox, ",") + `]}}`}, nil
		case strings.Contains(line, "orchestration send"):
			sends++
			inbox = append(inbox, `{"id":"msg_`+strconv.Itoa(sends)+`","subject":"WAKE go-sweep: continue the work in hand"}`)
			return shell.Result{Stdout: `{"ok":true}`}, nil
		}
		t.Fatalf("unexpected orca call: %s", line)
		return shell.Result{}, nil
	})
	ctx := context.Background()
	waker := &Waker{Drive: testDrive, Clock: env.Now, Send: func(dispatch, subject, body string) error {
		return env.sendDispatch(ctx, dispatch, subject, body)
	}, Unread: func(lane, subject string) (bool, error) {
		return env.dispatchHoldsUnread(ctx, lane, subject)
	}}
	row := Row{Dispatch: "ctx_1"}

	for _, why := range []string{"orders addressed to you are unread: root's decide #1.", "orders addressed to you are unread: root's decide #2.", "orders addressed to you are unread: root's decide #3."} {
		woken, err := waker.Wake("go-sweep", row, map[string]any{"why": why})
		if err != nil || !woken {
			t.Fatalf("wake for %q = %v, %v; want the lane counted as woken", why, woken, err)
		}
	}
	equal(t, sends, 1)

	inbox = nil
	woken, err := waker.Wake("go-sweep", row, map[string]any{"why": "orders addressed to you are unread: root's decide #4."})
	if err != nil || !woken {
		t.Fatal(woken, err)
	}
	equal(t, sends, 2)
}

func TestAnUnrelatedUnreadMessageDoesNotHoldAWake(t *testing.T) {
	local := t.TempDir()
	env := Env{Config: config.Config{OrcaRun: "run_1"}, Local: local, Now: func() time.Time { return t0 }}
	if err := os.MkdirAll(env.receipts(), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(env.receipts(), "go-sweep.terminal"), []byte("term_lane\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	env.Runner = scripted(func([]string) (shell.Result, error) {
		return shell.Result{Stdout: `{"ok":true,"result":{"messages":[{"subject":"WAKE other-lane: continue the work in hand"},{"subject":"relay #12"}]}}`}, nil
	})

	pending, err := env.dispatchHoldsUnread(context.Background(), "go-sweep", "WAKE go-sweep: continue the work in hand")
	if err != nil || pending {
		t.Fatalf("pending = %v, %v; want no wake pending", pending, err)
	}
}

func TestAnUnreadablePeekStillSendsTheWake(t *testing.T) {
	sends := 0
	waker := &Waker{Drive: testDrive, Clock: func() time.Time { return t0 }, Send: func(string, string, string) error {
		sends++
		return nil
	}, Unread: func(string, string) (bool, error) { return false, errors.New("no terminal receipt") }}

	woke, err := waker.Wake("go-sweep", Row{Dispatch: "ctx_1"}, nil)
	if err != nil || !woke || sends != 1 {
		t.Fatalf("woke %v, sends %d, err %v", woke, sends, err)
	}
}

func TestARosterLaneHoldingAnUnreadWakeGetsNoSecondOneUntilItReadsIt(t *testing.T) {
	team := filepath.Join(t.TempDir(), "teams", "session-d50e80e8")
	waker := &Waker{Drive: testDrive, Clock: func() time.Time { return t0 }}
	row := Row{Session: "d50e80e8-7ba3", Roster: team}
	path := filepath.Join(team, "inboxes", "go-sweep.json")
	inboxOf := func() []map[string]any {
		t.Helper()
		data, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		var messages []map[string]any
		if err := json.Unmarshal(data, &messages); err != nil {
			t.Fatal(err)
		}
		return messages
	}

	for range 3 {
		woken, err := waker.Wake("go-sweep", row, map[string]any{"why": "orders"})
		if err != nil || !woken {
			t.Fatal(woken, err)
		}
	}
	messages := inboxOf()
	equal(t, len(messages), 1)

	messages[0]["read"] = true
	writeJSON(t, path, messages)
	woken, err := waker.Wake("go-sweep", row, map[string]any{"why": "orders"})
	if err != nil || !woken {
		t.Fatal(woken, err)
	}
	equal(t, len(inboxOf()), 2)
}

func TestAnEndedOrcaDispatchIsAProcessRowThatReadsNotLiveSinceItsLaunch(t *testing.T) {
	local := t.TempDir()
	env := Env{Config: config.Config{OrcaRun: "run_1"}, Local: local, Now: func() time.Time { return t0 }}
	receipts := env.receipts()
	launched := t0.Add(-2 * time.Hour)
	for lane, dispatch := range map[string]string{"sol-fix": "ctx_1", "sol-done": "ctx_2", "sol-gone": "ctx_3"} {
		path := filepath.Join(receipts, lane+".json")
		writeJSON(t, path, map[string]any{"result": map[string]string{"dispatchId": dispatch}})
		if err := os.WriteFile(filepath.Join(receipts, lane+".terminal"), []byte("term_1\n"), 0o600); err != nil {
			t.Fatal(err)
		}
		if err := os.Chtimes(path, launched, launched); err != nil {
			t.Fatal(err)
		}
	}
	calls := 0
	env.Runner = scripted(func([]string) (shell.Result, error) {
		calls++
		return shell.Result{Stdout: `{"ok":true,"result":{"workers":[{"dispatchId":"ctx_1","dispatchStatus":"dispatched","projection":{"liveness":{"verdict":"live"}}},{"dispatchId":"ctx_2","dispatchStatus":"completed","workerState":"succeeded"}],"page":{}}}`}, nil
	})

	rows, err := env.orcaRows(context.Background(), nil)
	if err != nil {
		t.Fatal(err)
	}

	equal(t, []any{rows[0].Lane, rows[0].Live, rows[0].Dispatch, rows[1].Lane, rows[1].Live, rows[1].Dispatch}, []any{"sol-done", false, "ctx_2", "sol-fix", true, "ctx_1"})
	equal(t, []bool{rows[0].Succeeded, rows[1].Succeeded}, []bool{true, false})
	if !rows[0].SeenAt.Equal(launched) || !rows[1].SeenAt.Equal(t0) || len(rows) != 2 {
		t.Fatalf("%+v", rows)
	}
	equal(t, calls, 1)
	gone, err := env.orcaRows(context.Background(), []string{"sol-gone"})
	if err != nil {
		t.Fatal(err)
	}
	equal(t, len(gone), 0)
}

func orcaReceipts(t *testing.T, env Env, launched time.Time, dispatches map[string]string) {
	t.Helper()
	for lane, dispatch := range dispatches {
		path := filepath.Join(env.receipts(), lane+".json")
		writeJSON(t, path, map[string]any{"result": map[string]string{"dispatchId": dispatch}})
		if err := os.Chtimes(path, launched, launched); err != nil {
			t.Fatal(err)
		}
	}
}

func TestAnOrcaLaneReadsLiveOnlyWhileItsAgentDoes(t *testing.T) {
	env := Env{Config: config.Config{OrcaRun: "run_1"}, Local: t.TempDir(), Now: func() time.Time { return t0 }}
	launched := t0.Add(-2 * time.Hour)
	orcaReceipts(t, env, launched, map[string]string{"working": "ctx_1", "exited": "ctx_2", "cold": "ctx_3"})
	env.Runner = scripted(func([]string) (shell.Result, error) {
		return shell.Result{Stdout: `{"ok":true,"result":{"workers":[
			{"dispatchId":"ctx_1","dispatchStatus":"dispatched","projection":{"liveness":{"verdict":"live"}}},
			{"dispatchId":"ctx_2","dispatchStatus":"dispatched","projection":{"liveness":{"verdict":"exited"}}},
			{"dispatchId":"ctx_3","dispatchStatus":"dispatched","projection":{"liveness":{"verdict":"unverifiable","reason":"host_unavailable"}}}
		],"page":{}}}`}, nil
	})

	rows, err := env.orcaRows(context.Background(), []string{"working", "exited", "cold"})
	if err != nil {
		t.Fatal(err)
	}

	equal(t, rows, []Row{
		{Lane: "cold", SeenAt: launched, Dispatch: "ctx_3", Unverified: "has an Orca agent that reads unverifiable (host_unavailable)", HostGone: true},
		{Lane: "exited", SeenAt: launched, Dispatch: "ctx_2"},
		{Lane: "working", Live: true, SeenAt: t0, Dispatch: "ctx_1"},
	})
}

func TestAnUnsettledDispatchWithNoAgentVerdictIsAnUnreadSource(t *testing.T) {
	env := Env{Config: config.Config{OrcaRun: "run_1"}, Local: t.TempDir(), Now: func() time.Time { return t0 }}
	orcaReceipts(t, env, t0, map[string]string{"lane-1": "ctx_1"})
	env.Runner = scripted(func([]string) (shell.Result, error) {
		return shell.Result{Stdout: `{"ok":true,"result":{"workers":[{"dispatchId":"ctx_1","dispatchStatus":"dispatched"}],"page":{}}}`}, nil
	})

	_, err := env.orcaRows(context.Background(), []string{"lane-1"})

	if !errors.Is(err, ErrUnread) {
		t.Fatalf("err %v", err)
	}
}

func TestOrcaLivenessIsOneWorkerListReadAtOneLaneAndAtAThousand(t *testing.T) {
	reads := func(n int) int {
		env := Env{Config: config.Config{OrcaRun: "run_1"}, Local: t.TempDir(), Now: func() time.Time { return t0 }}
		dispatches := map[string]string{}
		workers := make([]string, 0, n)
		names := make([]string, 0, n)
		for i := range n {
			lane := fmt.Sprintf("lane-%d", i)
			dispatches[lane] = fmt.Sprintf("ctx_%d", i)
			names = append(names, lane)
			workers = append(workers, fmt.Sprintf(`{"dispatchId":"ctx_%d","dispatchStatus":"dispatched","projection":{"liveness":{"verdict":"exited"}}}`, i))
		}
		orcaReceipts(t, env, t0, dispatches)
		calls := 0
		env.Runner = scripted(func([]string) (shell.Result, error) {
			calls++
			return shell.Result{Stdout: `{"ok":true,"result":{"workers":[` + strings.Join(workers, ",") + `],"page":{}}}`}, nil
		})
		rows, err := env.orcaRows(context.Background(), names)
		if err != nil {
			t.Fatal(err)
		}
		equal(t, len(rows), n)
		return calls
	}
	equal(t, reads(1000), reads(1))
}

func prNode(t *testing.T, text string) *PRNode {
	t.Helper()
	var node PRNode
	if err := json.Unmarshal([]byte(text), &node); err != nil {
		t.Fatal(err)
	}
	return &node
}

func TestAConflictingOrRedOpenPRIsBrokenAndAnUnapprovedOneNeedsWork(t *testing.T) {
	node := func(state, mergeable, review, rollup string) string {
		return fmt.Sprintf(`{"state":%q,"mergeable":%q,"reviewDecision":%q,"commits":{"nodes":[{"commit":{"statusCheckRollup":{"state":%q}}}]}}`, state, mergeable, review, rollup)
	}
	cases := map[string]string{
		node("OPEN", "MERGEABLE", "APPROVED", "SUCCESS"):        Waiting,
		node("OPEN", "CONFLICTING", "APPROVED", "SUCCESS"):      Broken,
		node("OPEN", "MERGEABLE", "APPROVED", "FAILURE"):        Broken,
		node("OPEN", "MERGEABLE", "REVIEW_REQUIRED", "SUCCESS"): NeedsWork,
		node("OPEN", "MERGEABLE", "APPROVED", "PENDING"):        Waiting,
		node("MERGED", "MERGEABLE", "APPROVED", "SUCCESS"):      Closed,
	}
	for text, want := range cases {
		equal(t, PRStateOf(prNode(t, text)), want)
	}
	equal(t, PRStateOf(nil), Closed)
}

func TestAPRReadIsOneQueryPerBatchAndStaysFreshForItsInterval(t *testing.T) {
	calls := 0
	now := t0
	env := Env{Config: config.Config{Repo: "o/r"}, Now: func() time.Time { return now }, Runner: scripted(func(argv []string) (shell.Result, error) {
		calls++
		var nodes []string
		for n := 1; n <= 1000; n++ {
			if strings.Contains(argv[len(argv)-1], fmt.Sprintf("p%d: ", n)) {
				nodes = append(nodes, fmt.Sprintf(`"p%d":{"state":"OPEN","mergeable":"MERGEABLE","reviewDecision":"APPROVED","commits":{"nodes":[]}}`, n))
			}
		}
		return shell.Result{Stdout: `{"data":{"repository":{` + strings.Join(nodes, ",") + `}}}`}, nil
	})}
	reader := &prReader{env: env, ctx: context.Background(), read: map[int]prRead{}}
	numbers := make([]int, 0, 120)
	for n := 1; n <= 120; n++ {
		numbers = append(numbers, n)
	}

	states, err := reader.states(numbers)
	if err != nil {
		t.Fatal(err)
	}
	equal(t, states[120], Waiting)
	equal(t, calls, 3)
	if _, err := reader.states(numbers); err != nil {
		t.Fatal(err)
	}
	equal(t, calls, 3)
	now = t0.Add(openEvery)
	if _, err := reader.states(numbers[:1]); err != nil {
		t.Fatal(err)
	}
	equal(t, calls, 4)
}

func TestAPartialGraphQLAnswerStillReadsAndAFailedOneIsUnread(t *testing.T) {
	failing := &shell.ExitError{Argv: []string{"gh"}, Code: 1, Stderr: "boom"}
	partial := Env{Config: config.Config{Repo: "o/r"}, Now: func() time.Time { return t0 }, Runner: scripted(func([]string) (shell.Result, error) {
		return shell.Result{Code: 1, Stdout: `{"data":{"repository":{"p1":null,"p2":{"state":"MERGED"}}}}`}, failing
	})}
	states, err := (&prReader{env: partial, ctx: context.Background(), read: map[int]prRead{}}).states([]int{1, 2})
	if err != nil {
		t.Fatal(err)
	}
	equal(t, states, map[int]string{1: Closed, 2: Closed})

	dead := Env{Config: config.Config{Repo: "o/r"}, Now: func() time.Time { return t0 }, Runner: scripted(func([]string) (shell.Result, error) {
		return shell.Result{Code: 1}, failing
	})}
	if _, err := (&prReader{env: dead, ctx: context.Background(), read: map[int]prRead{}}).states([]int{1}); !errors.Is(err, ErrUnread) {
		t.Fatalf("err %v", err)
	}
}

type fakeBus struct {
	open   []client.Record
	tail   []client.Record
	peers  []client.Peer
	posted []client.Draft
	query  client.Query
	tails  int
}

func (b *fakeBus) Post(_ context.Context, d client.Draft) (client.Record, error) {
	b.posted = append(b.posted, d)
	return client.Record{Seq: int64(len(b.posted))}, nil
}

func (b *fakeBus) Tail(_ context.Context, q client.Query) ([]client.Record, error) {
	b.query = q
	b.tails++
	return b.tail, nil
}

func (b *fakeBus) Open(context.Context, string, ...string) ([]client.Record, error) {
	return b.open, nil
}

func (b *fakeBus) Who(context.Context, string) ([]client.Peer, error) {
	return b.peers, nil
}

func TestAPRItsLaneReportedHeldAtItsCurrentHeadOwesNoWork(t *testing.T) {
	var typed client.Record
	if err := json.Unmarshal([]byte(`{"seq":4,"lane":"root","kind":"hold","at":"2026-10-31T17:00:00Z","text":"typed","refs":{"prs":["o/r#46109"]}}`), &typed); err != nil {
		t.Fatal(err)
	}
	var cites client.Record
	if err := json.Unmarshal([]byte(`{"seq":1,"lane":"root","kind":"hold","text":"hold #9: no work owed #9","refs":{"prs":["o/r#46107"]}}`), &cites); err != nil {
		t.Fatal(err)
	}
	bus := &fakeBus{open: []client.Record{cites, typed}}
	env := Env{Bus: bus, Config: config.Config{Drive: "durable", Checkout: "/checkout"}, Claude: t.TempDir(), Runner: scripted(func(argv []string) (shell.Result, error) {
		contains(t, strings.Join(argv, " "), "ledger.py -C /checkout list --drive durable --open --json")
		return shell.Result{Stdout: `[{"pr":"47794","head":"847e655aaaa","reported_head":"847e655","reported_verdict":"held"},{"pr":"47708","head":"70284afbbbb","reported_head":"39c21ff","reported_verdict":"held"},{"pr":"47851","head":"1234567cccc","reported_head":"1234567","reported_verdict":"clean"}]`}, nil
	})}

	holds, err := env.held(context.Background())
	if err != nil {
		t.Fatal(err)
	}

	equal(t, holds, map[int]bool{46107: true, 46109: true, 47794: true})
}

func TestTheRotateLineFollowsTheAutoCompactWindow(t *testing.T) {
	path := filepath.Join(t.TempDir(), "settings.json")
	at, err := rotateAt(path)
	if err != nil {
		t.Fatal(err)
	}
	equal(t, at, (1_000_000-33_000)*70/100)
	writeJSON(t, path, map[string]int{"autoCompactWindow": 200_000})
	at, err = rotateAt(path)
	if err != nil {
		t.Fatal(err)
	}
	equal(t, at, (200_000-33_000)*70/100)
}

func TestAFirstReadAdoptsTheLastThreeDaysAndALaterOneResumesFromItsCursor(t *testing.T) {
	var stored client.Record
	if err := json.Unmarshal([]byte(`{"seq":7,"lane":"a","kind":"state","at":"2026-10-31T17:00:00.000Z","text":"one","refs":{"prs":[1,"o/r#2"]},"fields":{"attrs":{"brief":"/b"}}}`), &stored); err != nil {
		t.Fatal(err)
	}
	bus := &fakeBus{tail: []client.Record{stored}}
	env := Env{Bus: bus, Config: config.Config{Drive: testDrive}, Now: func() time.Time { return t0 }}

	rows, err := env.Records(context.Background())(0)
	if err != nil {
		t.Fatal(err)
	}

	equal(t, bus.query.Since.Equal(t0.Add(-72*time.Hour)), true)
	equal(t, rows[0].PRs(), []int{1, 2})
	equal(t, rows[0].Attr("brief"), "/b")
	if _, err := env.Records(context.Background())(7); err != nil {
		t.Fatal(err)
	}
	equal(t, bus.query.After, int64(7))
	equal(t, bus.query.Since.IsZero(), true)
}

func TestWhoRowsCarryTheRosterOfALiveLeadsTeam(t *testing.T) {
	claude := t.TempDir()
	writeJSON(t, filepath.Join(claude, "teams", "session-d50e80e8", "config.json"), map[string]any{"members": []map[string]string{{"name": "mate"}}})
	bus := &fakeBus{peers: []client.Peer{{Lane: Root, Session: "d50e80e8-7", Live: true, SeenAt: t0}, {Lane: "mate", Session: "d50e80e8-7", Agent: "amate", SeenAt: t0}}}
	env := Env{Bus: bus, Config: config.Config{Drive: testDrive}, Claude: claude}

	rows, err := env.Who(context.Background())(nil)
	if err != nil {
		t.Fatal(err)
	}

	equal(t, []bool{rows[0].Idle, rows[1].Live, rows[1].Idle}, []bool{false, true, true})
	equal(t, rows[1].Roster, filepath.Join(claude, "teams", "session-d50e80e8"))
}

func TestAnInboxKeepsItsEarlierMessagesVerbatim(t *testing.T) {
	inbox := filepath.Join(t.TempDir(), "inbox.json")
	if err := os.WriteFile(inbox, []byte(`[{"from":"lead","extra":{"keep":[1,2]},"text":"<hello>"}]`), 0o600); err != nil {
		t.Fatal(err)
	}

	written, err := appendInbox(inbox, map[string]any{"text": "a & b"})
	if err != nil || !written {
		t.Fatalf("written %v err %v", written, err)
	}

	data, err := os.ReadFile(inbox)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Contains(data, []byte(`"keep"`)) || !bytes.Contains(data, []byte(`<hello>`)) || !bytes.Contains(data, []byte(`a & b`)) {
		t.Fatal(string(data))
	}
}

func TestALaunchOrderOrAnOwnedByDecideNamesTheDeskThatOwnsALane(t *testing.T) {
	var records []client.Record
	feed := `[
		{"seq":7,"lane":"meta-desk-1","kind":"go","at":"2026-11-02T19:59:00Z","text":"R85032 (7:59 PM) orca-desk: launch board-mirror-1 opus xhigh brief=/b.md","to":["orca-desk"]},
		{"seq":8,"lane":"root","kind":"go","at":"2026-11-02T19:59:00Z","text":"R85033 (7:59 PM) orca-desk: launch alerts-desk-3 sonnet xhigh brief=/a.md","to":["orca-desk"]},
		{"seq":9,"lane":"root","kind":"decide","at":"2026-11-02T19:59:00Z","text":"orca-desk: launch quoted-1 opus xhigh","to":["meta-desk-1"]},
		{"seq":10,"lane":"root","kind":"decide","at":"2026-11-02T19:59:00Z","text":"meta-desk-1 maintains it","to":["stall-watch"],"fields":{"attrs":{"owned-by":"meta-desk-1"}}}
	]`
	if err := json.Unmarshal([]byte(feed), &records); err != nil {
		t.Fatal(err)
	}
	bus := &fakeBus{tail: records}
	reader := &deskReader{env: Env{Bus: bus, Config: config.Config{Drive: "durable"}}, ctx: context.Background(), desks: map[string]string{}}

	desks, err := reader.all()
	if err != nil {
		t.Fatal(err)
	}
	if _, err := reader.all(); err != nil {
		t.Fatal(err)
	}

	equal(t, desks, map[string]string{"board-mirror-1": "meta-desk-1", "alerts-desk-3": "root", "stall-watch": "meta-desk-1"})
	equal(t, bus.query.After, int64(10))
	equal(t, bus.query.Kinds, []string{"go", "decide"})
}

func TestOpenWorkReadsOnlyTheLastDaysOpenRecords(t *testing.T) {
	var records []client.Record
	feed := `[
		{"seq":1,"lane":"root","kind":"defect","at":"2026-11-02T19:59:00Z","text":"open one","to":["tooling-1"],"status":"open"},
		{"seq":2,"lane":"root","kind":"ask","at":"2026-11-02T19:59:00Z","text":"answered","to":["tooling-1"],"status":"resolved"}
	]`
	if err := json.Unmarshal([]byte(feed), &records); err != nil {
		t.Fatal(err)
	}
	bus := &fakeBus{tail: records}
	now := time.Date(2026, 11, 3, 4, 0, 0, 0, time.UTC)
	env := Env{Bus: bus, Config: config.Config{Drive: "durable"}, Now: func() time.Time { return now }}

	open, err := env.openWork(context.Background())
	if err != nil {
		t.Fatal(err)
	}

	equal(t, len(open), 1)
	equal(t, open[0].Text, "open one")
	equal(t, bus.query.Since, now.Add(-24*time.Hour))
	equal(t, bus.query.Kinds, []string{"defect", "ask", "blocker"})
}
