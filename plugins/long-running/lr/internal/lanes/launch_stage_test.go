package lanes

import (
	"fmt"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func secs(n float64) time.Duration { return time.Duration(n * float64(time.Second)) }

func launchingRow(t *testing.T, s *store.Store, name string, since time.Time) {
	t.Helper()
	if _, err := s.WantLane(store.Lane{Name: name, Brief: "/briefs/" + name + ".brief.md", Model: "opus", Effort: "high", Role: "desk", State: store.LaneLaunching, Placement: placedOnSprite}, since); err != nil {
		t.Fatal(err)
	}
}

func verdictsWhileStaging(t *testing.T, name string, stages []time.Duration, until time.Duration) map[time.Duration]VerdictKind {
	t.Helper()
	s := memory(t)
	f := newFake(t, nil, nil)
	since := t0
	launchingRow(t, s, name, since)
	verdicts := map[time.Duration]VerdictKind{}
	next := 0
	for at := time.Duration(0); at <= until; at += 30 * time.Second {
		var batch []Record
		for ; next < len(stages) && stages[next] <= at; next++ {
			batch = append(batch, rec(int64(next+1), runner, "state", since.Add(stages[next]), fmt.Sprintf("STAGE R%d %s: stage %d", 500000+next, name, next)))
		}
		fold(s, batch, nil, f.deps.Briefs, f.deps)
		verdicts[at] = Classify(row(t, s, name), nil, since.Add(at), 0, 0).Kind
	}
	return verdicts
}

func TestTheNov5PrUnblockLaunchesThatTookOver15MinutesStayLaunchingUntilTheirWorkerCameUp(t *testing.T) {
	for name, launch := range map[string]struct {
		stages []time.Duration
		up     time.Duration
	}{
		"pr-unblock-7": {[]time.Duration{secs(28.5), secs(28.9), secs(36.4), secs(42), secs(101.5), secs(108.6), secs(969.7), secs(977.2)}, secs(998)},
		"pr-unblock-8": {[]time.Duration{secs(25.6), secs(26), secs(34.8), secs(40.9), secs(78.3), secs(84.7), secs(1298), secs(1332.5)}, secs(1391.2)},
	} {
		t.Run(name, func(t *testing.T) {
			up := launch.up
			for at, kind := range verdictsWhileStaging(t, name, launch.stages, up) {
				if kind == NoShow {
					t.Fatalf("%s was a no-show %s after its launch, before its worker came up at %s", name, at, up)
				}
			}
		})
	}
}

func TestALaunchStillPostingStagesPastItsWindowWaitsUntil15MinutesAfterItsNewestStage(t *testing.T) {
	stages := []time.Duration{secs(30), 12 * time.Minute, 24 * time.Minute, 30 * time.Minute}

	verdicts := verdictsWhileStaging(t, "retrying-1", stages, 46*time.Minute)

	equal(t, []VerdictKind{verdicts[26*time.Minute], verdicts[35*time.Minute], verdicts[44*time.Minute+30*time.Second], verdicts[45*time.Minute]}, []VerdictKind{Ok, Ok, Ok, NoShow})
}
