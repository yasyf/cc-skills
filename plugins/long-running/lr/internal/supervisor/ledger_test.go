package supervisor

import (
	"context"
	"maps"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

var co = filepath.Join(os.TempDir(), "lr-ledger-co")

const (
	ledgerTool = "/claude/long-running/current/bin/ledger.py"
	ledgerSHA  = "e718a7434fa01ee9835f992a9a647a825350732c"
)

func ledgerUnder(t *testing.T, runner *shelltest.Fake) (*prLedger, *store.Store) {
	t.Helper()
	t.Setenv("CCI_HOME", t.TempDir())
	s := memory(t)
	if err := s.PutLedgerRows("L", map[string]map[string]string{"8": {"lane": "lane-8"}}, t0); err != nil {
		t.Fatal(err)
	}
	runner.Reply("", "git", "-C", co)
	env := lanes.Env{Bus: lanes.CCI{}, Runner: runner, Config: config.Config{Repo: "org/repo", Drive: "d", Checkout: co, Trunk: "dev"}, Claude: "/claude", Now: time.Now}
	return &prLedger{env: env, store: s, id: "L"}, s
}

func postHead(t *testing.T, lane, pr string) {
	t.Helper()
	if _, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: lane, Kind: "head", Text: ledgerSHA, PRs: []string{pr}}); err != nil {
		t.Fatal(err)
	}
}

func verbs(runner *shelltest.Fake) []string {
	calls := runner.Calls()
	out := make([]string, 0, len(calls))
	for _, call := range calls {
		if call.Argv[0] == ledgerTool {
			out = append(out, call.Argv[3])
		}
	}
	return out
}

func TestALedgerPassRegistersEachNewPRTheHeadRecordsNameThenRefreshesAndReconciles(t *testing.T) {
	runner := &shelltest.Fake{}
	runner.Reply("", ledgerTool)
	l, _ := ledgerUnder(t, runner)
	postHead(t, "lane-7", "org/repo#7")
	postHead(t, "lane-7", "org/repo#7")
	postHead(t, "lane-8", "org/repo#8")
	postHead(t, "lane-9", "other/repo#9")

	if err := l.pass(context.Background()); err != nil {
		t.Fatal(err)
	}

	if got, want := verbs(runner), []string{"register", "refresh", "reconcile"}; !slices.Equal(got, want) {
		t.Fatalf("verbs = %v, want %v", got, want)
	}
	calls := runner.Calls()
	if got := calls[0].Argv; got[3] != "fetch" || got[len(got)-1] != "+refs/heads/dev:refs/lr/ledger/dev" {
		t.Fatalf("first call = %v, want the checkout fetching the trunk", got)
	}
	if got := calls[1].Argv; got[3] != "merge" || !slices.Contains(got, "--ff-only") || got[len(got)-1] != "refs/lr/ledger/dev" {
		t.Fatalf("second call = %v, want a fast-forward to the ledger's own fetched ref, which no other fetch in the checkout can move", got)
	}
	if got := calls[2].Argv; !slices.Contains(got, "lane-7") || got[len(got)-1] != "7" {
		t.Fatalf("register = %v", got)
	}

	if err := l.pass(context.Background()); err != nil {
		t.Fatal(err)
	}
	if got, want := verbs(runner)[3:], []string{"refresh", "reconcile"}; !slices.Equal(got, want) {
		t.Fatalf("second pass = %v, want %v, its cursor past the records", got, want)
	}
}

func TestACheckoutThatWillNotFastForwardReachesRootAndTheLedgerStillRuns(t *testing.T) {
	runner := &shelltest.Fake{}
	runner.Fail(1, "fatal: Not possible to fast-forward", "git", "-C", co, "merge")
	runner.Reply("", ledgerTool)
	l, _ := ledgerUnder(t, runner)

	if err := l.pass(context.Background()); err == nil {
		t.Fatal("a stale checkout must reach root")
	}
	if got, want := verbs(runner), []string{"refresh", "reconcile"}; !slices.Equal(got, want) {
		t.Fatalf("verbs = %v, want %v", got, want)
	}
}

func TestARegisterTheLedgerRefusesStillRefreshesAndIsNotRetriedForever(t *testing.T) {
	runner := &shelltest.Fake{}
	runner.Fail(1, "no such pull request", ledgerTool, "-C", co, "register")
	runner.Reply("", ledgerTool)
	l, _ := ledgerUnder(t, runner)
	postHead(t, "lane-7", "org/repo#7")

	if err := l.pass(context.Background()); err == nil {
		t.Fatal("the refusal must reach root")
	}
	if got, want := verbs(runner), []string{"register", "refresh", "reconcile"}; !slices.Equal(got, want) {
		t.Fatalf("verbs = %v, want %v", got, want)
	}
	if err := l.pass(context.Background()); err != nil {
		t.Fatalf("second pass = %v, want the refused record behind its cursor", err)
	}
}

func wantLane(t *testing.T, s *store.Store, name string, state store.LaneState) {
	t.Helper()
	if _, err := s.WantLane(store.Lane{Name: name, Brief: "/briefs/x.md", Model: "opus", Effort: "xhigh", Role: "lane", State: state}, t0); err != nil {
		t.Fatal(err)
	}
}

func droppedRows(runner *shelltest.Fake) []string {
	var dropped []string
	for _, call := range runner.Calls() {
		if call.Argv[0] == "ccn" {
			dropped = append(dropped, call.Argv[len(call.Argv)-1])
		}
	}
	slices.Sort(dropped)
	return dropped
}

func TestAPassDropsARetiredLaneWithNoOpenPRAndKeepsEveryLaneWithOne(t *testing.T) {
	runner := &shelltest.Fake{}
	runner.Reply("", ledgerTool)
	runner.Reply("", "ccn")
	l, s := ledgerUnder(t, runner)
	l.env.Now = func() time.Time { return t0 }
	old, recent := t0.Add(-72*time.Hour).Format(time.RFC3339), t0.Add(-time.Hour).Format(time.RFC3339)
	wantLane(t, s, "retired-done", store.LaneRetired)
	wantLane(t, s, "retired-open", store.LaneRetired)
	wantLane(t, s, "retired-by-branch", store.LaneRetired)
	wantLane(t, s, "live", store.LaneDesired)
	if err := s.PutLedgerRows("L", map[string]map[string]string{
		"lane/retired-done":      {"lane": "retired-done", "branch_prefix": "retired-done-", "registered_at": old},
		"lane/retired-open":      {"lane": "retired-open", "branch_prefix": "retired-open-", "registered_at": old},
		"lane/retired-by-branch": {"lane": "retired-by-branch", "branch_prefix": "by-branch-", "registered_at": old},
		"lane/live":              {"lane": "live", "branch_prefix": "live-", "registered_at": old},
		"lane/gone-unknown":      {"lane": "gone-unknown", "branch_prefix": "gone-unknown-", "registered_at": old},
		"lane/new-unknown":       {"lane": "new-unknown", "branch_prefix": "new-unknown-", "registered_at": recent},
		"100":                    {"state": "landed", "lane": "retired-done", "branch": "retired-done-a"},
		"101":                    {"state": "open", "lane": "retired-open", "branch": "retired-open-a"},
		"102":                    {"state": "open", "lane": "moved-to-another-lane", "branch": "by-branch-a"},
		"ask/000001":             {"state": "open", "lane": "gone-unknown"},
	}, t0); err != nil {
		t.Fatal(err)
	}

	if err := l.pass(context.Background()); err != nil {
		t.Fatal(err)
	}

	if got, want := droppedRows(runner), []string{"lane/gone-unknown", "lane/retired-done"}; !slices.Equal(got, want) {
		t.Fatalf("dropped = %v, want %v", got, want)
	}
	rows, err := s.LedgerRows("L")
	if err != nil {
		t.Fatal(err)
	}
	got := slices.Sorted(maps.Keys(rows))
	want := []string{"100", "101", "102", "ask/000001", "lane/live", "lane/new-unknown", "lane/retired-by-branch", "lane/retired-open"}
	if !slices.Equal(got, want) {
		t.Fatalf("rows = %v, want %v, every PR row kept", got, want)
	}
}

func limitedRunner() *shelltest.Fake {
	runner := &shelltest.Fake{}
	runner.Fail(ledgerLimited, "forge rate limited, wrote nothing", ledgerTool, "-C", co, "refresh")
	runner.Reply("", ledgerTool)
	runner.Reply("", "git", "-C", co)
	runner.Reply("", "ccn")
	return runner
}

func limitedLedger(t *testing.T) (*prLedger, *shelltest.Fake, *time.Time) {
	t.Helper()
	runner := limitedRunner()
	l, s := ledgerUnder(t, runner)
	now := t0
	l.env.Now = func() time.Time { return now }
	wantLane(t, s, "retired-done", store.LaneRetired)
	if err := s.PutLedgerRows("L", map[string]map[string]string{"lane/retired-done": {"lane": "retired-done", "branch_prefix": "retired-done-"}}, t0); err != nil {
		t.Fatal(err)
	}
	return l, runner, &now
}

func TestARateLimitedRefreshIsASkippedPassThatStillReconcilesAndDropsNothing(t *testing.T) {
	l, runner, _ := limitedLedger(t)

	if err := l.pass(context.Background()); err != nil {
		t.Fatalf("pass = %v, want a limit to skip silently", err)
	}

	if got, want := verbs(runner), []string{"refresh", "reconcile"}; !slices.Equal(got, want) {
		t.Fatalf("verbs = %v, want %v", got, want)
	}
	if got := droppedRows(runner); len(got) != 0 {
		t.Fatalf("dropped = %v, want nothing judged on a read that did not happen", got)
	}
}

func TestALimitThatOutlastsTheOutageWindowReachesRootOnceUntilItRecovers(t *testing.T) {
	l, _, now := limitedLedger(t)
	pass := func(at time.Duration) error {
		*now = t0.Add(at)
		return l.pass(context.Background())
	}

	for _, at := range []time.Duration{0, 9 * time.Minute} {
		if err := pass(at); err != nil {
			t.Fatalf("at %s pass = %v, want silence inside the window", at, err)
		}
	}
	err := pass(ledgerOutage)
	if err == nil || !strings.Contains(err.Error(), "forge rate limited") {
		t.Fatalf("at the window's end pass = %v, want the limit", err)
	}
	if err := pass(ledgerOutage + ledgerEvery); err != nil {
		t.Fatalf("after telling root pass = %v, want one report per outage", err)
	}

	recovered := &shelltest.Fake{}
	recovered.Reply("", ledgerTool)
	recovered.Reply("", "git", "-C", co)
	recovered.Reply("", "ccn")
	l.env.Runner = recovered
	if err := pass(15 * time.Minute); err != nil {
		t.Fatal(err)
	}

	l.env.Runner = limitedRunner()
	if err := pass(16 * time.Minute); err != nil {
		t.Fatalf("a new limit's first pass = %v, want silence", err)
	}
	if err := pass(16*time.Minute + ledgerOutage); err == nil {
		t.Fatal("a new outage must reach root again")
	}
}

func TestARefreshFailingForAnotherReasonReachesRootAtOnce(t *testing.T) {
	runner := &shelltest.Fake{}
	runner.Fail(1, "forge unreachable, wrote nothing: shallow checkout", ledgerTool, "-C", co, "refresh")
	runner.Reply("", ledgerTool)
	l, _ := ledgerUnder(t, runner)

	if err := l.pass(context.Background()); err == nil {
		t.Fatal("a refusal that is no limit must reach root")
	}
}
