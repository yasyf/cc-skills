package landing_test

import (
	"context"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/landing"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

const (
	queueScript = ".agents/skills/submit-pr/scripts/stack-enqueue"
	queueKey    = "queue-key-value"
)

func trunk(t *testing.T, fake *shelltest.Fake) (landing.Trunk, string) {
	t.Helper()
	checkout := filepath.Join(t.TempDir(), "lr-landing")
	fake.Reply("", "git")
	fake.Reply(queueKey+"\n", "sh", "-c")
	return landing.Trunk{
		Runner: fake, Checkout: checkout, Branch: "dev",
		Script: queueScript, KeyCommand: "print-key --name queue",
	}, filepath.Join(checkout, queueScript)
}

func TestEnqueueRunsTheQueueScriptAtFreshTrunkWithTheKeyInItsEnvironmentOnly(t *testing.T) {
	fake := &shelltest.Fake{}
	queue, script := trunk(t, fake)
	fake.Reply("working\n#45583 QUEUED\n", script)

	said, err := queue.Enqueue(context.Background(), 45583, head)
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
	wantFetch := []string{"git", "-C", queue.Checkout, "fetch", "-q", "--no-tags", "--no-write-fetch-head", "--refmap=", "origin", "+refs/heads/dev:refs/lr/queue/dev"}
	wantDetach := []string{"git", "-C", queue.Checkout, "checkout", "-q", "--detach", "refs/lr/queue/dev"}
	if !slices.Equal(calls[0].Argv, wantFetch) || !slices.Equal(calls[1].Argv, wantDetach) {
		t.Fatalf("git calls = %+v, want trunk fetched into the queue's own ref, which no other fetch in the checkout writes, and that ref checked out", calls[:2])
	}
	call := calls[3]
	if !slices.Equal(call.Argv, []string{script, "45583"}) || call.Dir != queue.Checkout {
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
	queue, script := trunk(t, fake)
	fake.Fail(1, "#45583 BLOCKED required check pending\n", script)

	said, err := queue.Enqueue(context.Background(), 45583, head)
	if err != nil {
		t.Fatal(err)
	}

	if said != "exit 1: #45583 BLOCKED required check pending" {
		t.Fatalf("said = %q", said)
	}
}

func TestAHungQueueScriptIsReportedAsWhatTheQueueSaid(t *testing.T) {
	fake := &shelltest.Fake{}
	queue, script := trunk(t, fake)
	fake.Fail(shell.CodeTimedOut, "", script)

	said, err := queue.Enqueue(context.Background(), 45583, head)
	if err != nil {
		t.Fatal(err)
	}

	if said != "timed out after 300s" {
		t.Fatalf("said = %q", said)
	}
}

func TestDequeuePassesTheFlag(t *testing.T) {
	fake := &shelltest.Fake{}
	queue, script := trunk(t, fake)
	fake.Reply("", script)

	if _, err := queue.Dequeue(context.Background(), 45744); err != nil {
		t.Fatal(err)
	}

	calls := fake.Calls()
	if got := calls[len(calls)-1].Argv; !slices.Equal(got, []string{script, "45744", "--dequeue"}) {
		t.Fatalf("argv = %v", got)
	}
}

func TestLastFailureReadsTheAttemptsCheckAndWhichBatchMatesFellWithIt(t *testing.T) {
	fake := &shelltest.Fake{}
	queue, _ := trunk(t, fake)
	ci := queue.Checkout + "/tools/ci"
	fake.Reply(`[
		{"state":"failed","changedAt":"old","reason":"TEST_RUN_TIMEOUT","failed":{"failureCausingChecks":[{"name":"stale","url":"stale-url"}]}},
		{"state":"notReady","changedAt":"t0","reason":"API_REQUESTED"},
		{"state":"testing","changedAt":"t1","verificationRunId":"run-1"},
		{"state":"pendingFailure","changedAt":"t2","verificationRunId":"run-1","pendingFailure":{"failureCausingChecks":[{"name":"buildkite/test","url":"https://buildkite.com/forge/test/builds/113284"}]}},
		{"state":"failed","changedAt":"t3","reason":"TEST_RUN_FAILED_BY_OPTIMIZATION","failed":{"failureCausingChecks":[]}}
	]`, ci, "trunk", "history", "7")
	fake.Reply(`{"id":"run-1","testedPullRequests":[{"number":7},{"number":8},{"number":9}]}`, ci, "trunk", "verification", "7", "--run", "run-1")
	fake.Reply(`{"8":{"sha":"a","queueEntry":{"state":"failed","stateChangedAt":"t3"}},"9":{"sha":"b","queueEntry":{"state":"merged","stateChangedAt":"t3"}}}`, ci, "trunk", "status", "8", "9")
	queue.Logs = func(context.Context, string) (string, string, string) { return "sand-next-tests", "TestRead", "" }

	got, err := queue.LastFailure(context.Background(), 7)
	if err != nil {
		t.Fatal(err)
	}
	if got.At != "t3" || got.Reason != "TEST_RUN_FAILED_BY_OPTIMIZATION" || got.Check != "buildkite/test" || got.Run != "run-1" || got.Test != "TestRead" {
		t.Fatalf("failure = %+v", got)
	}
	if !slices.Equal(got.MatesFailed, []int{8}) {
		t.Fatalf("mates failed = %v, want only the one Trunk reads failed", got.MatesFailed)
	}
}

func TestACanceledEntryIsTheNewestEndAndReadsNoLog(t *testing.T) {
	fake := &shelltest.Fake{}
	queue, _ := trunk(t, fake)
	fake.Reply(`[{"state":"failed","changedAt":"t0","reason":"TEST_RUN_TIMEOUT"},{"state":"cancelled","changedAt":"t1","reason":"USER_REQUESTED"}]`, queue.Checkout+"/tools/ci", "trunk", "history", "7")
	queue.Logs = func(context.Context, string) (string, string, string) {
		t.Fatal("a canceled attempt has no failed job to read")
		return "", "", ""
	}

	got, err := queue.LastFailure(context.Background(), 7)
	if err != nil {
		t.Fatal(err)
	}
	if got.State != "cancelled" || got.Reason != "USER_REQUESTED" {
		t.Fatalf("failure = %+v", got)
	}
}

func TestAnUnreadHistoryIsAnErrorNotAnEmptyFailure(t *testing.T) {
	fake := &shelltest.Fake{}
	queue, _ := trunk(t, fake)
	fake.Fail(1, "429 Too Many Requests", queue.Checkout+"/tools/ci")

	if got, err := queue.LastFailure(context.Background(), 7); err == nil {
		t.Fatalf("failure = %+v, want the read's error", got)
	}
}

func TestStatesReadsEveryNumberInOneCallAndSkipsAPullWithNoEntry(t *testing.T) {
	fake := &shelltest.Fake{}
	queue, _ := trunk(t, fake)
	fake.Reply(`{"7":{"sha":"abc","queueEntry":{"state":"notReady","stateChangedAt":"t0","readiness":{"mergeability":"conflict"}}},"8":{"sha":"def","queueEntry":null}}`, queue.Checkout+"/tools/ci", "trunk", "status", "7", "8")

	got, err := queue.States(context.Background(), []int{7, 8})
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 || got[7].State != "notReady" || got[7].Head != "abc" || !strings.Contains(got[7].Readiness, "conflict") {
		t.Fatalf("states = %+v", got)
	}
}

func TestAFailedFetchStopsTheMoversAndNeverRunsTheScript(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Fail(128, "could not read from remote", "git")

	_, err := landing.Trunk{Runner: fake, Checkout: filepath.Join(t.TempDir(), "lr-landing"), Branch: "dev", Script: "s"}.Enqueue(context.Background(), 1, head)

	if err == nil || len(fake.Calls()) != 1 {
		t.Fatalf("err = %v, calls = %+v", err, fake.Calls())
	}
}

func TestNoKeyCommandRunsTheScriptWithTheInheritedEnvironment(t *testing.T) {
	fake := &shelltest.Fake{}
	checkout := filepath.Join(t.TempDir(), "c")
	fake.Reply("", "git")
	fake.Reply("#7 QUEUED\n", checkout+"/s")

	said, err := landing.Trunk{Runner: fake, Checkout: checkout, Branch: "dev", Script: "s"}.Enqueue(context.Background(), 7, head)
	if err != nil {
		t.Fatal(err)
	}

	calls := fake.Calls()
	if said != "exit 0: #7 QUEUED" || len(calls) != 3 || calls[2].Env != nil {
		t.Fatalf("said = %q, calls = %+v", said, calls)
	}
}

func TestTheQueueTouchesItsCheckoutOnlyOnceNoOtherWriterHoldsTheTree(t *testing.T) {
	fake := &shelltest.Fake{}
	queue, script := trunk(t, fake)
	fake.Reply("#7 QUEUED\n", script)
	ledger, err := landing.LockTree(queue.Checkout)
	if err != nil {
		t.Fatal(err)
	}

	done := make(chan error, 1)
	go func() {
		_, err := queue.Enqueue(context.Background(), 7, head)
		done <- err
	}()
	select {
	case err := <-done:
		t.Fatalf("the queue ran (%v) while another writer held the checkout's tree", err)
	case <-time.After(200 * time.Millisecond):
	}
	if calls := fake.Calls(); len(calls) != 0 {
		t.Fatalf("calls = %+v while the tree was held", calls)
	}
	if err := ledger.Close(); err != nil {
		t.Fatal(err)
	}

	if err := <-done; err != nil {
		t.Fatal(err)
	}
	if calls := fake.Calls(); len(calls) != 4 {
		t.Fatalf("calls = %+v, want the fetch, checkout, key and script once the tree was free", calls)
	}
}
