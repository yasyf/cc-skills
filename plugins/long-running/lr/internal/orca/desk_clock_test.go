package orca

import (
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/incident"
)

const clockLane = "api-fix"

func clockedWorld(t *testing.T) *deskWorld {
	t.Helper()
	return clockedLane(t, clockLane, "")
}

func clockedLane(t *testing.T, lane, header string) *deskWorld {
	t.Helper()
	w := newDeskWorld(t)
	w.desk.Config.IncidentMechanismMinutes, w.desk.Config.IncidentPRMinutes, w.desk.Config.IncidentReadyMinutes = 10, 15, 20
	fixedLane(t, w, lane)
	if header != "" {
		w.brief(lane, header+"\n"+testBrief)
	}
	w.live(lane, testDispatch, "term_fix")
	w.now = w.now.Add(-time.Hour)
	return w
}

func (w *deskWorld) clock() incidentClockState {
	w.t.Helper()
	clock, err := w.desk.clockOf(clockLane)
	if err != nil {
		w.t.Fatal(err)
	}
	return clock
}

func (w *deskWorld) fallbacks() []incident.Action {
	w.t.Helper()
	return w.fallbacksOf(clockLane)
}

func (w *deskWorld) fallbacksOf(lane string) []incident.Action {
	w.t.Helper()
	books, err := w.desk.containers(lanePrefix)
	if err != nil {
		w.t.Fatal(err)
	}
	if !slices.Contains(books, laneBook(lane+clockBackup)) {
		return nil
	}
	launches, err := w.desk.store.Actions(laneBook(lane+clockBackup), kindLaunch)
	if err != nil {
		w.t.Fatal(err)
	}
	return launches
}

func (w *deskWorld) clockLines() []string {
	w.t.Helper()
	var lines []string
	for _, line := range w.escalations() {
		if strings.HasPrefix(line, "INCIDENT-CLOCK") {
			lines = append(lines, line)
		}
	}
	return lines
}

func TestAFixLanePastItsMechanismLimitDrawsOneLineAndOneFallback(t *testing.T) {
	w := clockedWorld(t)
	w.now = w.now.Add(11 * time.Minute)

	w.step(w.desk.clockPass)
	w.step(w.desk.clockPass)

	lines := w.clockLines()
	if len(lines) != 1 || !strings.HasPrefix(lines[0], "INCIDENT-CLOCK api-fix mechanism: no mechanism 11 min after its launch") || !strings.Contains(lines[0], "limit 10 min; launch to now 11 min | the Opus fallback api-fix-opus is launching on incident-opus high") {
		t.Fatal(lines)
	}
	launches := w.fallbacks()
	if len(launches) != 1 {
		t.Fatal(launches)
	}
	spec, err := launchOf(launches[0])
	if err != nil {
		t.Fatal(err)
	}
	if spec.Model != modelIncidentOpus || !strings.Contains(readBrief(spec.Brief), "Opus fallback for `api-fix`") {
		t.Fatal(spec, readBrief(spec.Brief))
	}
	running, err := w.desk.liveClocks()
	if err != nil || len(running) != 1 || running[0] != clockLane {
		t.Fatal(running, err)
	}
}

func TestALaneUnderItsLimitDrawsNothing(t *testing.T) {
	w := clockedWorld(t)
	w.now = w.now.Add(9 * time.Minute)

	w.step(w.desk.clockPass)

	if lines := w.clockLines(); len(lines) != 0 {
		t.Fatal(lines)
	}
	if launches := w.fallbacks(); len(launches) != 0 {
		t.Fatal(launches)
	}
}

func TestAMechanismMovesTheClockToItsPRLimit(t *testing.T) {
	w := clockedWorld(t)
	w.now = w.now.Add(5 * time.Minute)
	w.add(object{"lane": clockLane, "kind": "mechanism", "text": "the pool leaks"})
	w.now = w.now.Add(11 * time.Minute)

	w.step(w.desk.clockPass)

	lines := w.clockLines()
	if len(lines) != 1 || !strings.HasPrefix(lines[0], "INCIDENT-CLOCK api-fix pr: no PR 16 min") || !strings.Contains(lines[0], "launch to mechanism 5 min, mechanism to now 11 min") {
		t.Fatal(lines)
	}
}

func TestWhatEndsAClockEndsItWithNoLine(t *testing.T) {
	for name, c := range map[string]struct {
		record object
		ended  string
	}{
		"fix live":          {object{"lane": clockLane, "kind": "fix-live", "text": "live"}, "fix-live"},
		"a root hold":       {object{"kind": kindHold, "to": []string{clockLane}, "text": "wait"}, "held by #"},
		"a closed incident": {object{"kind": "decide", "to": []string{clockLane}, "text": "INCIDENT api CLOSED"}, "incident closed by #"},
	} {
		t.Run(name, func(t *testing.T) {
			w := clockedWorld(t)
			w.now = w.now.Add(time.Minute)
			w.add(c.record)
			w.now = w.now.Add(40 * time.Minute)

			w.step(w.desk.clockPass)

			if lines := w.clockLines(); len(lines) != 0 {
				t.Fatal(lines)
			}
			if clock := w.clock(); !strings.HasPrefix(clock.Ended, c.ended) {
				t.Fatal(clock)
			}
			running, err := w.desk.liveClocks()
			if err != nil || len(running) != 0 {
				t.Fatal(running, err)
			}
		})
	}
}

const devRedHeader = "ccx: lane=api-fix role=incident effort=high incident=dev-red-ab12cd34ef"

func TestADevRedLanesOwnScopeOutIsRefusedAndTheFallbackTakesIt(t *testing.T) {
	w := clockedLane(t, clockLane, devRedHeader)
	w.now = w.now.Add(time.Minute)
	w.add(object{"kind": clockIncident, "to": []string{clockLane}, "text": "INCIDENT dev-red-ab12cd34ef SCOPE: active yes: dev red is the release gate, no customer runs failing yet; plan keeps path yes"})
	w.add(object{"lane": clockLane, "kind": kindStopped, "text": "STOPPED api-fix: out of scope, no customer impact, stopping"})
	w.now = w.now.Add(time.Minute)

	w.step(w.desk.clockPass)

	if clock := w.clock(); !strings.HasSuffix(clock.Ended, "refused: dev-red-ab12cd34ef is a release gate") {
		t.Fatal(clock)
	}
	wantContains(t, w.wantEscalation("REFUSED-STOP"), "dev-red-ab12cd34ef is a release gate")
	if launches := w.fallbacks(); len(launches) != 1 {
		t.Fatal(launches)
	}
}

func TestASentryLanesStopIsHonoredThoughItsWordsNameADevRed(t *testing.T) {
	lane := "sentry-7832068463-fix-case1"
	w := clockedLane(t, lane, "ccx: lane="+lane+" role=incident effort=high")
	w.now = w.now.Add(time.Minute)
	w.add(object{"kind": "answer", "to": []string{"sentry-7832068463-fix", "store-desk", "owner-relay"}, "text": "#51766 lands on his tap: the only failures were 3 calls from one user's curl script, and no customer run is failing, so it is not a forward-fix. It also waits behind dev-red (#51688)."})
	w.add(object{"lane": lane, "kind": kindStopped, "to": []string{coordinatorLane}, "text": "STOPPED sentry-7832068463-fix-case1: out of scope, active no under root524587: only three calls from one user curl script, no customer run failing. #51766 remains an ordinary approved green PR at b88af2eff7633593886238986ac076787bb6cd42 awaiting owner tap and dev-red #51688 under root524627; production recovery is unobserved, and no new code, merge or release ran."})
	w.now = w.now.Add(time.Minute)

	w.step(w.desk.clockPass)

	clock, err := w.desk.clockOf(lane)
	if err != nil || clock.Ended != kindStopped {
		t.Fatal(clock, err)
	}
	w.noEscalation("REFUSED-STOP")
	if launches := w.fallbacksOf(lane); len(launches) != 0 {
		t.Fatal(launches)
	}
}

func TestADevRedStopUnderRootsActiveNoIsHonored(t *testing.T) {
	for name, c := range map[string]struct {
		ruling  object
		answers bool
	}{
		"a root scope naming the lane":   {object{"kind": clockIncident, "to": []string{clockLane}, "text": "INCIDENT dev-red-ab12cd34ef SCOPE: active no: the red job tests a path the plan deletes; plan keeps path no"}, false},
		"a root record the stop answers": {object{"kind": "answer", "to": []string{"landing-desk"}, "text": "that red build is on the deleted tree: active no"}, true},
	} {
		t.Run(name, func(t *testing.T) {
			w := clockedLane(t, clockLane, devRedHeader)
			w.now = w.now.Add(time.Minute)
			root := w.add(c.ruling)
			stop := object{"lane": clockLane, "kind": kindStopped, "text": "STOPPED api-fix: out of scope, active no, no customer impact"}
			if c.answers {
				stop["re"] = root.Seq
			}
			w.add(stop)
			w.now = w.now.Add(time.Minute)

			w.step(w.desk.clockPass)

			if clock := w.clock(); clock.Ended != kindStopped {
				t.Fatal(clock)
			}
			w.noEscalation("REFUSED-STOP")
			if launches := w.fallbacks(); len(launches) != 0 {
				t.Fatal(launches)
			}
		})
	}
}

func TestAnOpenIncidentNamingTheLaneStandsTheFallbackAside(t *testing.T) {
	w := clockedWorld(t)
	w.add(object{"kind": clockIncident, "to": []string{clockLane}, "text": "api down", "status": clockOpen})
	w.now = w.now.Add(11 * time.Minute)

	w.step(w.desk.clockPass)

	lines := w.clockLines()
	if len(lines) != 1 || !strings.Contains(lines[0], "no fallback launched for api-fix-opus: open incident #") {
		t.Fatal(lines)
	}
	if launches := w.fallbacks(); len(launches) != 0 {
		t.Fatal(launches)
	}
}

func TestAFailedFallbackLaunchRetriesOnceThenAsksRootToStartItInProcess(t *testing.T) {
	w := clockedWorld(t)
	w.now = w.now.Add(11 * time.Minute)
	w.step(w.desk.clockPass)
	failFirst := func(key string) {
		t.Helper()
		if err := w.desk.store.Edit(laneBook(clockLane+clockBackup), func(tx *incident.Tx) error {
			_, err := tx.Fail(key, "orca refused")
			return err
		}); err != nil {
			t.Fatal(err)
		}
	}
	key := w.fallbacks()[0].ActionID
	failFirst(key)
	w.step(w.desk.clockPass)
	if launches := w.fallbacks(); len(launches) != 2 || launches[1].ActionID != key+clockRetry {
		t.Fatal(launches)
	}
	failFirst(key + clockRetry)
	w.step(w.desk.clockPass)

	if launches := w.fallbacks(); len(launches) != 2 {
		t.Fatal(launches)
	}
	wantContains(t, w.wantEscalation("INCIDENT-CLOCK api-fix in-process"), "failed twice: orca refused | orca refused | coordinator: start api-fix-opus in-process now")
}

func TestALaneIsNamedOnlyByItsWholeName(t *testing.T) {
	for text, want := range map[string]bool{
		"api-fix is stuck":      true,
		"see api-fix.":          true,
		"hold api-fix, please":  true,
		"my-api-fix is stuck":   false,
		"api-fix-2 is stuck":    false,
		"read api-fix.md first": false,
	} {
		if got := mentionsLane(text, "api-fix"); got != want {
			t.Errorf("%q = %v", text, got)
		}
	}
}

func TestAStandingLaneOnTheIncidentRouteDrawsNoClock(t *testing.T) {
	w := clockedWorld(t)
	w.brief(clockLane, testBrief+"\nStanding: follow up every bug report\n")
	w.now = w.now.Add(11 * time.Minute)

	w.step(w.desk.clockPass)

	if lines := w.clockLines(); len(lines) != 0 {
		t.Fatal(lines)
	}
	if launches := w.fallbacks(); len(launches) != 0 {
		t.Fatal(launches)
	}
}
