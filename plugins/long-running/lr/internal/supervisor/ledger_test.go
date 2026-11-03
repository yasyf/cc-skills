package supervisor

import (
	"context"
	"slices"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	ledgerTool = "/claude/long-running/current/bin/ledger.py"
	ledgerSHA  = "e718a7434fa01ee9835f992a9a647a825350732c"
)

func ledgerUnder(t *testing.T, runner *shelltest.Fake) (prLedger, *store.Store) {
	t.Helper()
	t.Setenv("CCI_HOME", t.TempDir())
	s := memory(t)
	if err := s.PutLedgerRows("L", map[string]map[string]string{"8": {"lane": "lane-8"}}, t0); err != nil {
		t.Fatal(err)
	}
	env := lanes.Env{Bus: lanes.CCI{}, Runner: runner, Config: config.Config{Repo: "org/repo", Drive: "d", Checkout: "/co"}, Claude: "/claude", Now: time.Now}
	return prLedger{env, s, "L"}, s
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
		out = append(out, call.Argv[3])
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
	if got := runner.Calls()[0].Argv; !slices.Contains(got, "lane-7") || got[len(got)-1] != "7" {
		t.Fatalf("register = %v", got)
	}

	if err := l.pass(context.Background()); err != nil {
		t.Fatal(err)
	}
	if got, want := verbs(runner)[3:], []string{"refresh", "reconcile"}; !slices.Equal(got, want) {
		t.Fatalf("second pass = %v, want %v, its cursor past the records", got, want)
	}
}

func TestARegisterTheLedgerRefusesStillRefreshesAndIsNotRetriedForever(t *testing.T) {
	runner := &shelltest.Fake{}
	runner.Fail(1, "no such pull request", ledgerTool, "-C", "/co", "register")
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
