package orca

import (
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/incident"
)

const clockLane = "api-fix"

func clockedWorld(t *testing.T) *deskWorld {
	t.Helper()
	w := newDeskWorld(t)
	fixedLane(t, w, clockLane)
	w.live(clockLane, testDispatch, "term_fix")
	w.now = w.now.Add(-time.Hour)
	return w
}

func (w *deskWorld) clock() incidentClockState {
	w.t.Helper()
	clock, err := w.desk.clockOf(laneBook(clockLane))
	if err != nil {
		w.t.Fatal(err)
	}
	return clock
}

func (w *deskWorld) fallbacks() []incident.Action {
	w.t.Helper()
	launches, err := w.desk.store.Actions(laneBook(clockLane+clockBackup), kindLaunch)
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
	if len(lines) != 1 || !strings.HasPrefix(lines[0], "INCIDENT-CLOCK api-fix mechanism: no mechanism 11 min after its launch") || !strings.Contains(lines[0], "limit 10 min; launch to now 11 min | the Opus fallback api-fix-opus is launching on incident-opus xhigh") {
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
		"fix live":         {object{"lane": clockLane, "kind": "fix-live", "text": "live"}, "fix-live"},
		"a root hold":      {object{"kind": kindHold, "to": []string{clockLane}, "text": "wait"}, "held by #"},
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

func TestAStopThatScopesOutAFailingReleaseGateIsRefusedAndTheFallbackTakesIt(t *testing.T) {
	w := clockedWorld(t)
	w.now = w.now.Add(time.Minute)
	w.add(object{"lane": clockLane, "kind": kindStopped, "text": "smoke team red, no customer impact"})
	w.now = w.now.Add(time.Minute)

	w.step(w.desk.clockPass)

	if clock := w.clock(); !strings.HasSuffix(clock.Ended, "refused: smoke team is a release gate") {
		t.Fatal(clock)
	}
	if launches := w.fallbacks(); len(launches) != 1 {
		t.Fatal(launches)
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
