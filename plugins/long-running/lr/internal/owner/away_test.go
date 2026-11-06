package owner

import (
	"bytes"
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

const (
	awayIncident = "inc-2026-11-03-eu7ayk"
	awayCI       = "/repo/tools/ci"
	bus          = "brook-bus"
	noneRuns     = "ci oncall connect refuses: incident inc-2026-11-03-eu7ayk has no running incident commander to connect to"
	unowned      = `[{"incident":"inc-2026-11-03-eu7ayk","phase":"triage","tookOver":false,"reach":"ci oncall connect"}]`
)

type awayWorld struct {
	state string
	fake  *shelltest.Fake
	now   time.Time
}

func newAwayWorld(t *testing.T, rowAge time.Duration, offset time.Duration) *awayWorld {
	t.Helper()
	config, state := t.TempDir(), t.TempDir()
	t.Setenv("CLAUDE_CONFIG_DIR", config)
	t.Setenv("CCI_HOME", t.TempDir())
	now := time.Now().Add(offset)
	entry := filepath.Join(config, "long-running", "drives", "brook.json")
	if err := os.MkdirAll(filepath.Dir(entry), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(entry, []byte(`{"drive":"brook","cci_drive":"`+bus+`","checkout":"/repo","state_dir":"`+state+`","sessions":["s1"]}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := Add(Ledger(state), now.Add(-rowAge), "merge the billing stack", opened, 0); err != nil {
		t.Fatal(err)
	}
	return &awayWorld{state: state, fake: &shelltest.Fake{}, now: now}
}

func (w *awayWorld) run(t *testing.T, args ...string) (string, error) {
	t.Helper()
	var out bytes.Buffer
	cmd := Command(&app.App{Runner: w.fake, Now: func() time.Time { return w.now }}, noLane)
	cmd.SetOut(&out)
	cmd.SetErr(&bytes.Buffer{})
	cmd.SetArgs(append(args, "--drive", "brook"))
	err := cmd.Execute()
	return out.String(), err
}

func (w *awayWorld) away(t *testing.T) (string, error) {
	t.Helper()
	return w.run(t, "away", awayIncident)
}

func post(t *testing.T, lane, kind, text string, attrs map[string]string) {
	t.Helper()
	if _, err := client.Post(context.Background(), client.Draft{Drive: bus, Lane: lane, Kind: kind, Text: text, Attrs: attrs}); err != nil {
		t.Fatal(err)
	}
}

func wantAway(t *testing.T, out string, err error) {
	t.Helper()
	if err != nil || !strings.HasPrefix(out, "away: ") {
		t.Fatalf("err = %v, out = %q, want away", err, out)
	}
}

func wantPresent(t *testing.T, out string, err error, says string) {
	t.Helper()
	if err == nil || !strings.HasPrefix(out, "present: ") || !strings.Contains(out, says) {
		t.Fatalf("err = %v, out = %q, want present saying %q", err, out, says)
	}
}

func TestATapMinutesBeforeATakeoverReadsPresentWithoutAskingAiOncall(t *testing.T) {
	world := newAwayWorld(t, 3*time.Hour, 9*time.Minute)
	post(t, "board-watch", ownerKind, "Owner on board b1, Who may write?: picked one", nil)
	out, err := world.away(t)
	wantPresent(t, out, err, "inside 30m0s")
	if !strings.Contains(out, "from board-watch") {
		t.Fatalf("out = %q, want the record it read", out)
	}
	if calls := world.fake.Calls(); len(calls) != 0 {
		t.Fatalf("present read ai-oncall: %v", calls)
	}
}

func TestAChatRowMinutesOldReadsPresent(t *testing.T) {
	world := newAwayWorld(t, 5*time.Minute, 0)
	out, err := world.away(t)
	wantPresent(t, out, err, "inside 30m0s")
}

func TestSilencePastTheClockWithNoCommanderReadsAway(t *testing.T) {
	world := newAwayWorld(t, time.Hour, 0)
	world.fake.Reply(unowned, awayCI, "oncall", "incidents", "--json")
	world.fake.Fail(1, noneRuns, awayCI, "oncall", "connect", awayIncident)
	out, err := world.away(t)
	wantAway(t, out, err)
	if !strings.Contains(out, "ledger m1") || !strings.Contains(out, noneRuns) {
		t.Fatalf("out = %q, want both reads quoted", out)
	}
}

func TestAMissingLedgerReadsPresent(t *testing.T) {
	world := newAwayWorld(t, time.Hour, 0)
	if err := os.Remove(Ledger(world.state)); err != nil {
		t.Fatal(err)
	}
	out, err := world.away(t)
	wantPresent(t, out, err, "his ledger did not read")
}

func TestAnUnreadableIncidentListReadsPresent(t *testing.T) {
	world := newAwayWorld(t, time.Hour, 0)
	world.fake.Fail(1, "sso expired", awayCI, "oncall", "incidents", "--json")
	out, err := world.away(t)
	wantPresent(t, out, err, "who holds the incident did not read")
}

func TestAnIncidentTheListLacksReadsPresent(t *testing.T) {
	world := newAwayWorld(t, time.Hour, 0)
	world.fake.Reply(`[]`, awayCI, "oncall", "incidents", "--json")
	out, err := world.away(t)
	wantPresent(t, out, err, "lists no open")
}

func TestAPersonHoldingTheIncidentReadsPresent(t *testing.T) {
	world := newAwayWorld(t, time.Hour, 0)
	world.fake.Reply(`[{"incident":"`+awayIncident+`","phase":"bot-owned","tookOver":true,"owner":{"name":"Matt"}}]`, awayCI, "oncall", "incidents", "--json")
	out, err := world.away(t)
	wantPresent(t, out, err, "Matt holds "+awayIncident)
}

func TestALiveCommanderReadsPresent(t *testing.T) {
	world := newAwayWorld(t, time.Hour, 0)
	world.fake.Reply(unowned, awayCI, "oncall", "incidents", "--json")
	world.fake.Reply("connected as conn-1", awayCI, "oncall", "connect", awayIncident)
	out, err := world.away(t)
	wantPresent(t, out, err, "commander")
}

func TestAWaitingOrOtherwiseRefusedConnectReadsPresent(t *testing.T) {
	for _, said := range []string{
		"incident x's incident commander is starting in build; ci oncall connect waits for it and connects once it is up",
		"ci oncall connect refuses: incident x's incident commander is closing it or handing it to a person",
		"ci oncall connect refuses: incident x's ai-oncall runtime predates connect",
	} {
		world := newAwayWorld(t, time.Hour, 0)
		world.fake.Reply(unowned, awayCI, "oncall", "incidents", "--json")
		world.fake.Fail(1, said, awayCI, "oncall", "connect", awayIncident)
		out, err := world.away(t)
		wantPresent(t, out, err, "commander")
	}
}

func TestBackfilledAndLaneCarriedOwnerRecordsAreNotHim(t *testing.T) {
	world := newAwayWorld(t, time.Hour, 0)
	post(t, "board-watch", ownerKind, "Backfilled from board b1 (merge-prs): 45494 approved", nil)
	post(t, "tap-orders", ownerKind, "Owner in #1: #2 approved at abc", nil)
	world.fake.Reply(unowned, awayCI, "oncall", "incidents", "--json")
	world.fake.Fail(1, noneRuns, awayCI, "oncall", "connect", awayIncident)
	out, err := world.away(t)
	wantAway(t, out, err)
}

func TestHisWordHereWinsOverSilence(t *testing.T) {
	world := newAwayWorld(t, time.Hour, 0)
	if _, err := world.run(t, "word", "here", "--row", "m1"); err != nil {
		t.Fatal(err)
	}
	out, err := world.away(t)
	wantPresent(t, out, err, "his word says he is here")
}

func TestHisWordAwayWinsOverTheClockUntilHeNextSpeaks(t *testing.T) {
	world := newAwayWorld(t, time.Minute, time.Minute)
	if _, err := world.run(t, "word", "away", "--row", "m1"); err != nil {
		t.Fatal(err)
	}
	world.fake.Reply(unowned, awayCI, "oncall", "incidents", "--json")
	world.fake.Fail(1, noneRuns, awayCI, "oncall", "connect", awayIncident)
	out, err := world.away(t)
	wantAway(t, out, err)

	post(t, communicator, ownerKind, "back, what happened", nil)
	world.now = time.Now().Add(time.Minute)
	out, err = world.away(t)
	wantPresent(t, out, err, "inside 30m0s")
}

func TestHisWordAwayStillLeavesALiveCommanderHolding(t *testing.T) {
	world := newAwayWorld(t, time.Minute, time.Minute)
	if _, err := world.run(t, "word", "away", "--row", "m1"); err != nil {
		t.Fatal(err)
	}
	world.fake.Reply(unowned, awayCI, "oncall", "incidents", "--json")
	world.fake.Reply("connected as conn-1", awayCI, "oncall", "connect", awayIncident)
	out, err := world.away(t)
	wantPresent(t, out, err, "commander")
}

func TestHisWordOnOneIncidentOrdersThatTakeoverAndNoOther(t *testing.T) {
	world := newAwayWorld(t, time.Minute, time.Minute)
	if _, err := world.run(t, "word", "away", "--row", "m1", "--incident", awayIncident); err != nil {
		t.Fatal(err)
	}
	out, err := world.away(t)
	wantAway(t, out, err)
	if calls := world.fake.Calls(); len(calls) != 0 {
		t.Fatalf("his order read ai-oncall: %v", calls)
	}

	out, err = world.run(t, "away", "inc-other")
	wantPresent(t, out, err, "inside 30m0s")
}

func TestAWordNeedsARealLedgerRow(t *testing.T) {
	world := newAwayWorld(t, time.Minute, 0)
	if _, err := world.run(t, "word", "away", "--row", "m999"); err == nil || !strings.Contains(err.Error(), "no row") {
		t.Fatalf("err = %v, want a refusal naming the row", err)
	}
	if _, err := world.run(t, "word", "gone", "--row", "m1"); err == nil || !strings.Contains(err.Error(), "away or here") {
		t.Fatalf("err = %v, want a refusal naming the words", err)
	}
}

func TestARowFromLastDecemberReadsAsLastYear(t *testing.T) {
	now := time.Date(2027, 1, 1, 0, 5, 0, 0, pacific)
	at, err := rowAt("Dec 31 11:50 PM", now)
	if err != nil {
		t.Fatal(err)
	}
	if want := time.Date(2026, 12, 31, 23, 50, 0, 0, pacific); !at.Equal(want) {
		t.Fatalf("at = %v, want %v", at, want)
	}
}
