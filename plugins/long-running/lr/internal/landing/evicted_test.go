package landing_test

import (
	"context"
	"fmt"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	prfacts "github.com/yasyf/cc-skills/plugins/long-running/lr/internal/facts"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/landing"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/scaletest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const evictionDrive = "d"

var (
	batchFailure = landing.Failure{State: "failed", At: "t1", Reason: "TEST_RUN_FAILED_BY_OPTIMIZATION", Check: "buildkite/test", URL: "https://b/1", MatesFailed: []int{8}}
	infraFailure = landing.Failure{State: "failed", At: "t1", Reason: "TEST_RUN_REQUIRED_STATUS_FAILED", Check: "buildkite/test", URL: "https://b/1", Job: "go-test", Infra: "no space left on device"}
	ownFailure   = landing.Failure{State: "failed", At: "t1", Reason: "TEST_RUN_REQUIRED_STATUS_FAILED", Check: "buildkite/test", URL: "https://b/1", Job: "sand-next-tests", Test: "TestRead"}
)

func fellOut(f *machine.Facts) { f.Queue = machine.QueueEvicted }

func evictions(s *store.Store, q *queue) landing.Evictions {
	return landing.Evictions{Store: s, Queue: q, Drive: evictionDrive, Now: func() time.Time { return t0 }}
}

func settle(t *testing.T, s *store.Store, q *queue, snapshot ...machine.Facts) []machine.Facts {
	t.Helper()
	read, err := evictions(s, q).Pass(context.Background(), snapshot)
	if err != nil {
		t.Fatal(err)
	}
	return read
}

func told(t *testing.T) []client.Record {
	t.Helper()
	records, err := client.Tail(context.Background(), client.Query{Drive: evictionDrive, Lanes: []string{"supervisor"}})
	if err != nil {
		t.Fatal(err)
	}
	return records
}

func TestAnEvictedHeadPostsOneRecordAndMovesItsRowOnce(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	s, q := storeWith(t, "lane-7"), &queue{failure: ownFailure}

	first := settle(t, s, q, facts(45711, fellOut))[0]
	second := settle(t, s, q, first)[0]

	records := told(t)
	if len(records) != 1 || q.failureReads != 1 {
		t.Fatalf("records = %+v, failure reads = %d, want one of each over two passes", records, q.failureReads)
	}
	for _, want := range []string{"EVICTED #45711", "TEST_RUN_REQUIRED_STATUS_FAILED", "buildkite/test https://b/1", "FAIL TestRead"} {
		if !strings.Contains(records[0].Text, want) {
			t.Errorf("record %q does not name %q", records[0].Text, want)
		}
	}
	if !slices.Contains(records[0].To, "lane-7") || !slices.Contains(records[0].To, "owner-relay") {
		t.Errorf("record goes to %v, want its lane and owner-relay", records[0].To)
	}
	if got := machine.Classify(second).State; got != machine.StateEvicted {
		t.Errorf("state = %q", got)
	}
	if got := fact(t, s, 45711, machine.EvictionFact); !strings.Contains(got, "buildkite/test") {
		t.Errorf("row's reason = %q", got)
	}
}

func TestAnInfrastructureFailureReentersOnceAndASecondEvictionStaysOut(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	s, q := storeWith(t, "lane-7"), &queue{failure: infraFailure}

	read := settle(t, s, q, facts(45711, fellOut))
	if read[0].EvictionOwn || !strings.Contains(told(t)[0].Text, "no space left on device") {
		t.Fatalf("facts = %+v, record = %q", read[0], told(t)[0].Text)
	}
	act(t, s, q, &restacker{}, t0, read...)
	if !slices.Equal(q.reentered, []int{45711}) || fact(t, s, 45711, machine.EvictionFact) != "" {
		t.Fatalf("reentered = %v, eviction fact = %q", q.reentered, fact(t, s, 45711, machine.EvictionFact))
	}

	q.failure.At = "t2"
	again := settle(t, s, q, facts(45711, func(f *machine.Facts) {
		f.Queue, f.Resubmitted = machine.QueueEvicted, 1
	}))
	act(t, s, q, &restacker{}, t0, again...)

	if len(q.reentered) != 1 {
		t.Fatalf("reentered = %v, want one re-entry at this head", q.reentered)
	}
	if !again[0].EvictionOwn || len(told(t)) != 2 {
		t.Fatalf("facts = %+v, records = %+v", again[0], told(t))
	}
}

func TestItsOwnRedCheckGoesToItsLaneAndNeverReenters(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	s, q := storeWith(t, "lane-7"), &queue{failure: ownFailure}

	read := settle(t, s, q, facts(45711, fellOut))
	done := act(t, s, q, &restacker{}, t0, read...)

	if len(q.reentered) != 0 || len(done.Resubmitted) != 0 {
		t.Fatalf("reentered = %v, done = %+v", q.reentered, done)
	}
	if got := machine.Classify(read[0]); got.Mover != machine.MoverLane || got.Action != machine.ActionFixRed {
		t.Fatalf("verdict = %+v", got)
	}
	if records := told(t); string(records[0].Kind) != "blocker" {
		t.Fatalf("kind = %q", records[0].Kind)
	}
}

func TestOnlyABuildWhoseFailedJobsRanNoFailingTestReentersAndSaysWhy(t *testing.T) {
	const reentry = "for eviction #%d, which counted as infrastructure since no failed job ran a failing test, it failed on infrastructure; the queue said exit 0"
	for name, c := range map[string]struct {
		build, log, kind, next string
		reenters               bool
	}{
		"build 118033, the render step failed alone": {
			build:    `{"state":"failed","jobs":[{"id":"job-1","type":"script","name":":pipeline: Render and upload the pipeline","step_key":"bootstrap","state":"failed"}]}`,
			kind:     "report",
			next:     "job bootstrap: no test job ran, since the build held no other step. Not its own change: no failed job ran a failing test, it failed on infrastructure. It re-enters the queue once at this head",
			reenters: true,
		},
		"a test job's checkout failed before its commands ran": {
			build:    `{"state":"failed","jobs":[{"id":"job-0","type":"script","step_key":"bootstrap","state":"passed"},` + failedJob + `]}`,
			log:      `{"content":"~~~ Preparing working directory\nBUG: builtin/pack-objects.c:4453: should_include_obj should only be called on existing objects\nfatal: index-pack failed\n"}`,
			kind:     "report",
			next:     "job go-test: it failed in checkout or a hook, before its commands ran. Not its own change",
			reenters: true,
		},
		"build 118164, a node test failed beside a page's 403": {
			build: `{"state":"failed","jobs":[{"id":"job-0","type":"script","step_key":"bootstrap","state":"passed"},{"id":"job-1","type":"script","step_key":"product-dashboard-serve-next","state":"failed"}]}`,
			log:   `{"content":"~~~ Preparing working directory\n~~~ Running commands\npage| error Failed to load resource: the server responded with a status of 403 (Forbidden)\n\u2716 failing tests:\n\u2716 a plain chat's published plan shows in its Outline tab (70896.900971ms)\n"}`,
			kind:  "blocker",
			next:  "job product-dashboard-serve-next: FAIL a plain chat's published plan shows in its Outline tab. It stays out of the queue until a new head; read the check, then push a fix.",
		},
		"a page's 403 in a test job whose failing test no parser names": {
			build: `{"state":"failed","jobs":[{"id":"job-0","type":"script","step_key":"bootstrap","state":"passed"},{"id":"job-1","type":"script","step_key":"product-dashboard-serve-next","state":"failed"}]}`,
			log:   `{"content":"~~~ Preparing working directory\n~~~ Running commands\npage| error Failed to load resource: the server responded with a status of 403 (Forbidden)\n"}`,
			kind:  "blocker",
			next:  "job product-dashboard-serve-next, no failing test named in its log. It stays out of the queue until a new head",
		},
		"a test job failed after the render step passed": {
			build: `{"state":"failed","jobs":[{"id":"job-0","type":"script","step_key":"bootstrap","state":"passed"},` + failedJob + `]}`,
			log:   `{"content":"--- FAIL: TestRead (0.1s)\n"}`,
			kind:  "blocker",
			next:  "job go-test: FAIL TestRead. It stays out of the queue until a new head; read the check, then push a fix.",
		},
	} {
		t.Run(name, func(t *testing.T) {
			t.Setenv("CCI_HOME", t.TempDir())
			fake := &shelltest.Fake{}
			bkReply(fake, buildPath+"105354", c.build)
			bkReply(fake, buildPath+"105354/jobs/job-1/log", c.log)
			fake.Reply("read-token\n", tokenTool)
			watch := landing.TrunkWatch{Runner: fake, Checkout: "/co", Config: landing.TrunkConfig{CIBuild: "tools/ci bk api /pipelines/{pipeline}/builds/{number}", TokenCommand: []string{tokenTool}}}
			job, test, infra := watch.FailedJob(context.Background(), redBuild)
			s := storeWith(t, "lane-7")
			q := &queue{failure: landing.Failure{State: "failed", At: "t1", Reason: "TEST_RUN_REQUIRED_STATUS_FAILED", Check: "buildkite/test", URL: redBuild, Job: job, Test: test, Infra: infra}}

			done := act(t, s, q, &restacker{}, t0, settle(t, s, q, facts(45711, fellOut))...)
			if err := landing.Settle(context.Background(), s, evictionDrive, done.Said); err != nil {
				t.Fatal(err)
			}

			records := told(t)
			if len(records) == 0 || string(records[0].Kind) != c.kind || !strings.Contains(records[0].Text, c.next) {
				t.Fatalf("records = %+v, want a %s saying %q", records, c.kind, c.next)
			}
			if !c.reenters {
				if len(q.reentered) != 0 || len(records) != 1 {
					t.Fatalf("reentered = %v, records = %+v, want no re-entry and only the eviction", q.reentered, records)
				}
				return
			}
			if !slices.Equal(q.reentered, []int{45711}) || len(records) != 2 {
				t.Fatalf("reentered = %v, records = %+v, want one re-entry and its record", q.reentered, records)
			}
			if said := records[1]; said.Re != records[0].Seq || !strings.HasPrefix(said.Text, "REENTERED #45711 at ") || !strings.Contains(said.Text, fmt.Sprintf(reentry, records[0].Seq)) {
				t.Fatalf("re-entry record = %+v, want it to answer eviction #%d and say why", said, records[0].Seq)
			}
		})
	}
}

func TestOnlyAFailureWithNoFailedTestIsNotItsOwn(t *testing.T) {
	for name, c := range map[string]struct {
		failure landing.Failure
		own     bool
	}{
		"a lone run's red check":       {ownFailure, true},
		"optimized away, no evidence":  {landing.Failure{State: "failed", At: "t", Reason: "TEST_RUN_FAILED_BY_OPTIMIZATION"}, true},
		"a mate fell in the same run":  {batchFailure, true},
		"trunk's own step failed":      {landing.Failure{State: "failed", At: "t", Reason: "GITHUB_API_MERGE_PR_FAILED"}, false},
		"the log reads infrastructure": {landing.Failure{State: "failed", At: "t", Job: "j", Infra: "no space left on device"}, false},
		"canceled, not failed":         {landing.Failure{State: "cancelled", At: "t", Reason: "USER_REQUESTED"}, false},
	} {
		t.Run(name, func(t *testing.T) {
			t.Setenv("CCI_HOME", t.TempDir())
			s, q := storeWith(t, "lane-7"), &queue{failure: c.failure}

			if got := settle(t, s, q, facts(45711, fellOut))[0].EvictionOwn; got != c.own {
				t.Fatalf("own = %v, want %v", got, c.own)
			}
		})
	}
}

func TestARedTestAMateOrAnOpenFlakeSharesStaysWithItsLaneAndSaysSo(t *testing.T) {
	for name, c := range map[string]struct {
		failure landing.Failure
		says    string
	}{
		"a mate fell in the same run":  {batchFailure, "The same test run also failed #8, yet a failed test never re-enters at its head."},
		"an open flake names its test": {ownFailure, "Open flake "},
	} {
		t.Run(name, func(t *testing.T) {
			t.Setenv("CCI_HOME", t.TempDir())
			if _, err := client.Post(context.Background(), client.Draft{Drive: evictionDrive, Lane: "supervisor", Kind: "defect", To: []string{"flakes"}, Topic: "dev-red", ID: "flake:one", Force: true, Attrs: map[string]string{"flake": "TestRead"}, Text: "flake TestRead"}); err != nil {
				t.Fatal(err)
			}
			s, q := storeWith(t, "lane-7"), &queue{failure: c.failure}

			read := settle(t, s, q, facts(45711, fellOut))
			act(t, s, q, &restacker{}, t0, read...)

			evicted := slices.DeleteFunc(told(t), func(r client.Record) bool { return !strings.Contains(r.Text, "EVICTED #45711") })
			if !read[0].EvictionOwn || len(q.reentered) != 0 || len(evicted) != 1 || string(evicted[0].Kind) != "blocker" || !strings.Contains(evicted[0].Text, c.says) {
				t.Fatalf("facts = %+v, reentered = %v, records = %+v, want a blocker saying %q", read[0], q.reentered, evicted, c.says)
			}
		})
	}
}

func TestARowReadingQueuedThatTrunkFailedReadsEvicted(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	s := storeWith(t, "lane-7")
	q := &queue{failure: batchFailure, states: map[int]prfacts.QueueEntry{45711: {State: "failed", Head: head}}}
	queued := facts(45711, func(f *machine.Facts) { f.Queue, f.QueuedHead = machine.QueueQueued, head })

	read := settle(t, s, q, queued)

	if read[0].Queue != machine.QueueEvicted || read[0].Eviction == "" || len(told(t)) != 1 {
		t.Fatalf("facts = %+v, records = %+v", read[0], told(t))
	}
}

func TestANotReadyEntryPastTheQueueClockIsToldOnce(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	s := storeWith(t, "lane-7")
	stale := t0.Add(-machine.QueueClock - time.Minute).Format(time.RFC3339)
	fresh := t0.Add(-time.Minute).Format(time.RFC3339)
	q := &queue{states: map[int]prfacts.QueueEntry{
		1: {State: "notReady", Head: head, ChangedAt: stale, Readiness: `{"mergeability":"conflict"}`},
		2: {State: "notReady", Head: head, ChangedAt: fresh},
	}}
	queued := func(f *machine.Facts) { f.Queue, f.QueuedHead = machine.QueueQueued, head }

	settle(t, s, q, facts(1, queued), facts(2, queued))
	settle(t, s, q, facts(1, queued), facts(2, queued))

	records := told(t)
	if len(records) != 1 || !strings.Contains(records[0].Text, "NOT-READY #1") || !strings.Contains(records[0].Text, "conflict") {
		t.Fatalf("records = %+v", records)
	}
}

func TestAReenteredRowForgetsItsEviction(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	s, q := storeWith(t, "lane-7"), &queue{failure: ownFailure}
	read := settle(t, s, q, facts(45711, fellOut))
	read[0].Queue, read[0].QueuedHead = machine.QueueQueued, head

	again := settle(t, s, q, read...)

	if again[0].Eviction != "" || fact(t, s, 45711, machine.EvictionFact) != "" || fact(t, s, 45711, machine.EvictionOwnFact) != "" {
		t.Fatalf("facts = %+v", again[0])
	}
}

func TestQueueReadsStayFlatWithTheRowCount(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	reads := func(rows int) (status, failure int) {
		s, q := storeWith(t, "lane-7"), &queue{failure: ownFailure}
		snapshot := make([]machine.Facts, rows)
		for at := range snapshot {
			snapshot[at] = facts(at+1, func(f *machine.Facts) { f.Queue, f.QueuedHead = machine.QueueQueued, head })
		}
		settle(t, s, q, snapshot...)
		return q.statusReads, q.failureReads
	}

	oneStatus, oneFailure := reads(1)
	manyStatus, manyFailure := reads(scaletest.N())

	if oneStatus != 1 || manyStatus != 1 || oneFailure != 0 || manyFailure != 0 {
		t.Fatalf("N=1 read %d, %d; N=%d read %d, %d; want one status call and no failure read at each", oneStatus, oneFailure, scaletest.N(), manyStatus, manyFailure)
	}
}

func TestADequeueAlarmReachesOwnerRelayAndNamesHisRefusedTap(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	s, q := storeWith(t, "lane-7"), &queue{}
	queued := facts(50160, func(f *machine.Facts) {
		f.Queue, f.QueuedHead, f.Tap, f.TapRefused = machine.QueueQueued, head, "", "a grade needs a verdict"
	})

	done := act(t, s, q, &restacker{}, t0, queued)
	if err := landing.Settle(context.Background(), s, evictionDrive, done.Said); err != nil {
		t.Fatal(err)
	}

	records := told(t)
	if len(records) != 1 || !slices.Contains(records[0].To, "owner-relay") || !strings.Contains(records[0].Text, "his tap was refused: a grade needs a verdict") {
		t.Fatalf("records = %+v", records)
	}
}

func TestAFellLabelThatOutlivedItsHeadTellsNobodyAndMovesNothing(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	s, q := storeWith(t, "lane-7"), &queue{}
	arrived := t0.Add(-time.Minute)
	if err := s.PutReviewRows([]store.ReviewRow{{Repo: repo, Number: 50525, Head: head, HeadAt: store.Stamp(arrived)}}); err != nil {
		t.Fatal(err)
	}

	read := settle(t, s, q, facts(50525, fellOut))

	if !q.since.Equal(arrived) {
		t.Fatalf("the failure read from %v, want the head's arrival %v", q.since, arrived)
	}
	if read[0].Queue != machine.QueueNone || read[0].Eviction != "" || len(told(t)) != 0 {
		t.Fatalf("facts = %+v, records = %+v", read[0], told(t))
	}
	if got := machine.Classify(read[0]).State; got == machine.StateEvicted {
		t.Fatalf("state = %q", got)
	}
}

func TestARowNoLongerEvictedForgetsItsEviction(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	s, q := storeWith(t, "lane-7"), &queue{failure: ownFailure}
	read := settle(t, s, q, facts(45711, fellOut))
	read[0].Queue = machine.QueueNone

	again := settle(t, s, q, read...)

	if again[0].Eviction != "" || fact(t, s, 45711, machine.EvictionFact) != "" {
		t.Fatalf("facts = %+v", again[0])
	}
}

func TestAHeldRowTheQueueEvictedIsNotReenteredWhenItsSnapshotShowsNoHold(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	s, q := storeWith(t, "lane-7"), &queue{failure: infraFailure}
	guard := landing.HoldGuard{Queue: q, Held: func(context.Context) (map[int]bool, error) { return map[int]bool{45711: true}, nil }}
	read := settle(t, s, q, facts(45711, fellOut))

	done, err := landing.Act(context.Background(), s, guard, &restacker{}, acts(t, s, t0, read...), t0)
	if err != nil {
		t.Fatal(err)
	}

	if len(q.reentered) != 0 || len(q.enqueued) != 0 || len(done.Resubmitted) != 0 {
		t.Fatalf("reentered = %v, enqueued = %+v, resubmitted = %+v", q.reentered, q.enqueued, done.Resubmitted)
	}
	if len(done.Said) != 1 || !strings.Contains(done.Said[0].Text, "an open hold names it") {
		t.Fatalf("said = %+v", done.Said)
	}
}
