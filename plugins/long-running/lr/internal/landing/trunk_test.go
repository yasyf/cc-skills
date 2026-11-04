package landing_test

import (
	"context"
	"slices"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/landing"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

const (
	checkout = "/repos/lr-landing"
	script   = "/repos/lr-landing/.agents/skills/submit-pr/scripts/stack-enqueue"
	queueKey = "queue-key-value"
)

func trunk(fake *shelltest.Fake) landing.Trunk {
	fake.Reply("", "git")
	fake.Reply(queueKey+"\n", "sh", "-c")
	return landing.Trunk{
		Runner: fake, Checkout: checkout, Branch: "dev",
		Script: ".agents/skills/submit-pr/scripts/stack-enqueue", KeyCommand: "print-key --name queue",
	}
}

func TestEnqueueRunsTheQueueScriptAtFreshTrunkWithTheKeyInItsEnvironmentOnly(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply("working\n#45583 QUEUED\n", script)

	said, err := trunk(fake).Enqueue(context.Background(), 45583, head)
	if err != nil {
		t.Fatal(err)
	}

	if said != "exit 0: #45583 QUEUED" {
		t.Fatalf("said = %q", said)
	}
	calls := fake.Calls()
	if len(calls) != 4 {
		t.Fatalf("calls = %+v", calls)
	}
	wantFetch := []string{"git", "-C", checkout, "fetch", "-q", "origin", "dev"}
	wantDetach := []string{"git", "-C", checkout, "checkout", "-q", "--detach", "FETCH_HEAD"}
	if !slices.Equal(calls[0].Argv, wantFetch) || !slices.Equal(calls[1].Argv, wantDetach) {
		t.Fatalf("git calls = %+v", calls[:2])
	}
	call := calls[3]
	if !slices.Equal(call.Argv, []string{script, "45583"}) || call.Dir != checkout {
		t.Fatalf("queue call = %+v", call)
	}
	if !slices.Contains(call.Env, "TRUNK_MERGE_API_KEY="+queueKey) {
		t.Fatalf("env = %v", call.Env)
	}
	if strings.Contains(strings.Join(call.Argv, " "), queueKey) || strings.Contains(said, queueKey) {
		t.Fatal("the key left the environment")
	}
}

func TestAQueueRefusalIsWhatTheQueueSaidNotAFailure(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Fail(1, "#45583 BLOCKED required check pending\n", script)

	said, err := trunk(fake).Enqueue(context.Background(), 45583, head)
	if err != nil {
		t.Fatal(err)
	}

	if said != "exit 1: #45583 BLOCKED required check pending" {
		t.Fatalf("said = %q", said)
	}
}

func TestAHungQueueScriptIsReportedAsWhatTheQueueSaid(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Fail(shell.CodeTimedOut, "", script)

	said, err := trunk(fake).Enqueue(context.Background(), 45583, head)
	if err != nil {
		t.Fatal(err)
	}

	if said != "timed out after 300s" {
		t.Fatalf("said = %q", said)
	}
}

func TestDequeuePassesTheFlag(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply("", script)

	if _, err := trunk(fake).Dequeue(context.Background(), 45744); err != nil {
		t.Fatal(err)
	}

	calls := fake.Calls()
	if got := calls[len(calls)-1].Argv; !slices.Equal(got, []string{script, "45744", "--dequeue"}) {
		t.Fatalf("argv = %v", got)
	}
}

func TestLastFailureIsTheNewestFailedEntrysReason(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply(`[{"state":"failed","reason":"TEST_RUN_REQUIRED_STATUS_FAILED"},{"state":"testsPassed","reason":"TEST_RUN_PASSED"},{"state":"failed","reason":"GITHUB_API_MERGE_PR_FAILED"},{"state":"notReady","reason":"API_REQUESTED"}]`, checkout+"/tools/ci")

	if got := trunk(fake).LastFailure(context.Background(), 7); got != "GITHUB_API_MERGE_PR_FAILED" {
		t.Fatalf("failure = %q", got)
	}
}

func TestAnUnreadHistoryNamesNoFailure(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Fail(1, "429 Too Many Requests", checkout+"/tools/ci")

	if got := trunk(fake).LastFailure(context.Background(), 7); got != "" {
		t.Fatalf("failure = %q", got)
	}
}

func TestAFailedFetchStopsTheMoversAndNeverRunsTheScript(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Fail(128, "could not read from remote", "git")

	_, err := landing.Trunk{Runner: fake, Checkout: checkout, Branch: "dev", Script: "s"}.Enqueue(context.Background(), 1, head)

	if err == nil || len(fake.Calls()) != 1 {
		t.Fatalf("err = %v, calls = %+v", err, fake.Calls())
	}
}

func TestNoKeyCommandRunsTheScriptWithTheInheritedEnvironment(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply("", "git")
	fake.Reply("#7 QUEUED\n", "/c/s")

	said, err := landing.Trunk{Runner: fake, Checkout: "/c", Branch: "dev", Script: "s"}.Enqueue(context.Background(), 7, head)
	if err != nil {
		t.Fatal(err)
	}

	calls := fake.Calls()
	if said != "exit 0: #7 QUEUED" || len(calls) != 3 || calls[2].Env != nil {
		t.Fatalf("said = %q, calls = %+v", said, calls)
	}
}
