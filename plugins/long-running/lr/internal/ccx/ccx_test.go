package ccx

import (
	"context"
	"errors"
	"reflect"
	"slices"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

const statusOutput = `[{"head": "754b02c186e7dd15bb6a1e3a1f7a4518acd79c7c", "number": 1921, "queue": "not queued", "state": "OPEN", "base": "main", "ci": {"state": "green"}, "approval": {"state": "none"}, "mergeability": "notQueued", "downstack": 1917, "verdict": "blocked:unapproved"}]`

func TestPRStatusReadsOneCallForEveryNumber(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply(statusOutput, "ccx", "vcs", "pr", "status")
	rows, err := CLI{Runner: fake}.PRStatus(context.Background(), "org/repo", []int{1921, 1922})
	if err != nil {
		t.Fatal(err)
	}
	want := PRStatus{Number: 1921, Head: "754b02c186e7dd15bb6a1e3a1f7a4518acd79c7c", Base: "main", State: "OPEN", Queue: "not queued", Mergeability: "notQueued", Downstack: 1917, Verdict: "blocked:unapproved", CI: Check{"green"}, Approval: Check{"none"}}
	if !reflect.DeepEqual(rows, []PRStatus{want}) {
		t.Fatalf("got %+v", rows)
	}
	calls := fake.Calls()
	if wantArgv := []string{"ccx", "vcs", "pr", "status", "--repo", "org/repo", "--json", "1921", "1922"}; len(calls) != 1 || !slices.Equal(calls[0].Argv, wantArgv) {
		t.Fatalf("calls %+v", calls)
	}
}

func TestNoNumbersMakeNoCall(t *testing.T) {
	fake := &shelltest.Fake{}
	if rows, err := (CLI{Runner: fake}).PRStatus(context.Background(), "org/repo", nil); err != nil || rows != nil {
		t.Fatalf("rows %v, err %v", rows, err)
	}
	if state, err := (CLI{Runner: fake}).PRState(context.Background(), "org/repo", MaxAgeCached, nil, nil); err != nil || state.Repo != "org/repo" {
		t.Fatalf("state %+v, err %v", state, err)
	}
	if calls := fake.Calls(); len(calls) != 0 {
		t.Fatalf("calls %+v", calls)
	}
}

func TestPRStateNamesItsNumbersAndLanePrefixes(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply(`{"repo":"org/repo","trunk":"main","lanes":{},"prs":{"7":{"number":7}}}`, "ccx", "vcs", "pr", "state")
	state, err := CLI{Runner: fake}.PRState(context.Background(), "org/repo", "2m", []int{7}, []string{"lr-", "go-"})
	if err != nil {
		t.Fatal(err)
	}
	if state.Trunk != "main" || string(state.PRs["7"]) != `{"number":7}` {
		t.Fatalf("state %+v", state)
	}
	want := []string{"ccx", "vcs", "pr", "state", "--repo", "org/repo", "--max-age", "2m", "7", "--lane-prefix", "lr-", "--lane-prefix", "go-"}
	if got := fake.Calls()[0].Argv; !slices.Equal(got, want) {
		t.Fatalf("argv %v", got)
	}
}

func TestGHGoesThroughTheGateway(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply("out", "ccx", "vcs", "gh")
	out, err := CLI{Runner: fake}.GH(context.Background(), MaxAgeFresh, "pr", "list")
	if err != nil || out != "out" {
		t.Fatalf("out %q, err %v", out, err)
	}
	if got, want := fake.Calls()[0].Argv, []string{"ccx", "vcs", "gh", "--max-age", "0s", "--", "pr", "list"}; !slices.Equal(got, want) {
		t.Fatalf("argv %v", got)
	}
}

func TestStackListRunsInTheWorktreeItReads(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply(`{"root":"/w","branches":[{"branch":"b","path":"/w","current":true,"needs_restack":true,"needs_submit":false,"state":""}]}`, "ccx", "vcs", "stack", "list")
	stack, err := CLI{Runner: fake}.StackList(context.Background(), "/w")
	if err != nil {
		t.Fatal(err)
	}
	if len(stack.Branches) != 1 || !stack.Branches[0].NeedsRestack || fake.Calls()[0].Dir != "/w" {
		t.Fatalf("stack %+v, calls %+v", stack, fake.Calls())
	}
}

func TestARefusalReachesTheCaller(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Fail(1, "rate limit", "ccx")
	_, err := CLI{Runner: fake}.PRStatus(context.Background(), "org/repo", []int{1})
	var exit *shell.ExitError
	if !errors.As(err, &exit) || exit.Stderr != "rate limit" {
		t.Fatalf("got %v", err)
	}
}

func TestPRReadsRunFromTheCheckoutTheClientHolds(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply(`[]`, "ccx", "vcs", "pr", "status")
	fake.Reply(`{}`, "ccx", "vcs", "pr", "state")
	from := CLI{Runner: fake, Dir: "/co"}
	if _, err := from.PRStatus(context.Background(), "org/repo", []int{1}); err != nil {
		t.Fatal(err)
	}
	if _, err := from.PRState(context.Background(), "org/repo", MaxAgeCached, []int{1}, nil); err != nil {
		t.Fatal(err)
	}
	for _, call := range fake.Calls() {
		if call.Dir != "/co" {
			t.Fatalf("%v ran from %q, want /co", call.Argv, call.Dir)
		}
	}
}

func TestCallerNamesTheVerbUnlessAParentAlreadyNamedOne(t *testing.T) {
	t.Setenv("CCX_CALLER", "")
	Caller = "lr-board-rows"
	t.Cleanup(func() { Caller = "" })
	fake := &shelltest.Fake{}
	fake.Reply("out", "ccx", "vcs", "gh")
	if _, err := (CLI{Runner: fake}).GH(context.Background(), MaxAgeFresh, "pr", "list"); err != nil {
		t.Fatal(err)
	}
	if got := fake.Calls()[0].Env; !slices.Contains(got, "CCX_CALLER=lr-board-rows") {
		t.Fatalf("env = %v", got)
	}
	t.Setenv("CCX_CALLER", "progress-desk-4")
	if _, err := (CLI{Runner: fake}).GH(context.Background(), MaxAgeFresh, "pr", "list"); err != nil {
		t.Fatal(err)
	}
	if got := fake.Calls()[1].Env; len(got) != 0 {
		t.Fatalf("env = %v, want the parent's caller left alone", got)
	}
}
