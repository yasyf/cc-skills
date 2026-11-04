package wait

import (
	"bytes"
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx/ccxtest"
)

const (
	testCarried = "aaaaaaaa1111"
	testNewer   = "bbbbbbbb2222"
	testOlder   = "cccccccc3333"
	listRuns    = "run list --repo yasyf/cc-skills --workflow long-running lr release --branch main --limit 30 --json status,conclusion,headSha,databaseId,number,attempt"
)

func compareKey(commit, head string) string {
	return "api repos/yasyf/cc-skills/compare/" + commit + "..." + head + " --jq .status"
}

type releaseFixture struct {
	fake   *ccxtest.Fake
	out    bytes.Buffer
	now    time.Time
	onTick func()
}

func newReleaseFixture(runs string) *releaseFixture {
	return &releaseFixture{
		now: time.Date(2026, 11, 4, 12, 0, 0, 0, time.UTC),
		fake: &ccxtest.Fake{GHOut: map[string]string{
			compareKey(testCarried, "main"):      "ahead\n",
			compareKey(testCarried, testNewer):   "ahead\n",
			compareKey(testCarried, testCarried): "identical\n",
			compareKey(testCarried, testOlder):   "behind\n",
			listRuns:                             runs,
		}},
	}
}

func (f *releaseFixture) wait(ctx context.Context, timeout time.Duration) error {
	return ReleaseWait{
		Commit:  testCarried,
		Timeout: timeout,
		CCX:     f.fake,
		Out:     &f.out,
		Warn:    &f.out,
		Now:     func() time.Time { return f.now },
		Sleep: func(context.Context, time.Duration) error {
			f.now = f.now.Add(releasePoll)
			if f.onTick != nil {
				f.onTick()
			}
			return nil
		},
	}.Run(ctx)
}

func TestReleaseWaitExitsOnTheFirstSuccessfulRunCarryingTheCommit(t *testing.T) {
	f := newReleaseFixture(`[
		{"status":"completed","conclusion":"success","headSha":"` + testOlder + `","databaseId":1,"number":600,"attempt":1},
		{"status":"completed","conclusion":"cancelled","headSha":"` + testCarried + `","databaseId":2,"number":601,"attempt":1},
		{"status":"in_progress","conclusion":"","headSha":"` + testNewer + `","databaseId":3,"number":602,"attempt":1}
	]`)
	f.onTick = func() {
		f.fake.GHOut[listRuns] = `[
			{"status":"completed","conclusion":"success","headSha":"` + testOlder + `","databaseId":1,"number":600,"attempt":1},
			{"status":"completed","conclusion":"cancelled","headSha":"` + testCarried + `","databaseId":2,"number":601,"attempt":1},
			{"status":"completed","conclusion":"success","headSha":"` + testNewer + `","databaseId":3,"number":602,"attempt":2}
		]`
	}
	if err := f.wait(t.Context(), time.Hour); err != nil {
		t.Fatal(err)
	}
	if got, want := f.out.String(), "lr--v0.602.2 released at bbbbbbbb carries aaaaaaaa\n"; got != want {
		t.Errorf("output = %q, want %q: an older release and a cancelled one carry nothing", got, want)
	}
}

func TestReleaseWaitReportsAFailedRunOnceAndWaitsOn(t *testing.T) {
	failed := `{"status":"completed","conclusion":"failure","headSha":"` + testCarried + `","databaseId":5,"number":610,"attempt":1}`
	f := newReleaseFixture("[" + failed + "]")
	ticks := 0
	f.onTick = func() {
		if ticks++; ticks == 2 {
			f.fake.GHOut[listRuns] = "[" + failed + `,{"status":"completed","conclusion":"success","headSha":"` + testNewer + `","databaseId":6,"number":611,"attempt":1}]`
		}
	}
	if err := f.wait(t.Context(), time.Hour); err != nil {
		t.Fatal(err)
	}
	lines := strings.Split(strings.TrimSpace(f.out.String()), "\n")
	if len(lines) != 2 || !strings.HasPrefix(lines[0], "lr release failure at aaaaaaaa run 5") || !strings.HasPrefix(lines[1], "lr--v0.611.1 released") {
		t.Errorf("output = %q; want the failure once, then the release", lines)
	}
}

func TestReleaseWaitRefusesACommitNotOnMain(t *testing.T) {
	f := newReleaseFixture("[]")
	f.fake.GHOut[compareKey(testCarried, "main")] = "diverged\n"
	err := f.wait(t.Context(), time.Hour)
	if err == nil || !strings.Contains(err.Error(), "not on main") {
		t.Fatalf("err = %v, want the commit refused rather than a wait that never ends", err)
	}
	for _, call := range f.fake.Calls() {
		if strings.Contains(call, "run list") {
			t.Errorf("read runs after the refusal: %s", call)
		}
	}
}

func TestReleaseWaitPrintsQuietAtTheDeadline(t *testing.T) {
	f := newReleaseFixture("[]")
	err := f.wait(t.Context(), 2*releasePoll)
	var exit *ExitError
	if !errors.As(err, &exit) || exit.Code != CodeQuiet {
		t.Fatalf("err = %v, want exit %d", err, CodeQuiet)
	}
	if !strings.HasPrefix(f.out.String(), "QUIET ") {
		t.Errorf("output = %q, want QUIET", f.out.String())
	}
}

func TestReleaseWaitOutlivesAFailedRunListRead(t *testing.T) {
	f := newReleaseFixture(`[{"status":"completed","conclusion":"success","headSha":"` + testCarried + `","databaseId":7,"number":620,"attempt":1}]`)
	runs := f.fake.GHOut[listRuns]
	delete(f.fake.GHOut, listRuns)
	f.onTick = func() { f.fake.GHOut[listRuns] = runs }
	if err := f.wait(t.Context(), time.Hour); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(f.out.String(), "lr--v0.620.1 released") {
		t.Errorf("output = %q, want the release after the retried read", f.out.String())
	}
}
