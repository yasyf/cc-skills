package lanes

import (
	"context"
	"log/slog"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
)

func muteWatch(rows ...client.Record) (*MutePushback, *fakeBus) {
	bus := &fakeBus{tail: rows}
	return &MutePushback{Env: Env{Bus: bus, Config: config.Config{Drive: testDrive}}, Log: slog.Default()}, bus
}

func TestAMuteOrMonitorSkipOnAnUnfixedMechanismGetsOnePushbackToRoot(t *testing.T) {
	cases := []struct {
		lane   string
		text   string
		pushed bool
	}{
		{"run-history-fix", "MECHANISM journal refusals only on the smoke teams; ai-oncall's #47507 makes the monitor skip the smoke teams", true},
		{"desk-runner", "PAGE ai-oncall: Muted: smoke-team run-failure pages until 6:30pm", true},
		{"run-history-fix", "MECHANISM False alarm: Runner refusing runs whose journal cannot open on Zeta", true},
		{"run-history-fix", "MECHANISM sandsql-primary resets connections at each api roll", false},
		{"root", "INCIDENT That is a mute, not a fix", false},
		{"run-history-fix", "MECHANISM mute the smoke-team monitor until the roll ends", true},
		{"run-history-fix", "MECHANISM refusals come from the smoke teams only, so scope out the smoke teams from the run-failure monitor", true},
		{"datadog-mute-fix-1", "#47575 5e74a17 regrade please, head unchanged. (1) Caller trace now in the PR body: discoverRows -> irisSessionOpenRows (sole reference) -> programFor -> rowsProgram -> datadogType build(downtimeSchedules) -> one datadog.DowntimeSchedule datadog-iris-session-open-mute/first-week, which the next datadog deploy deletes; nothing else reads it.", false},
		{"datadog-mute-fix-1", "MECHANISM the fix deletes the expired mute on the smoke-team monitor", false},
		{"run-queue-fix", "MUTE run-queue-fix: monitors 339598935 339599887 until fix-live: every team's runs complete since 4:46 PM; rsw-cp logs backlog_not_draining from a pre-admission snapshot", false},
		{"run-history-fix", "MUTE run-history-fix: monitors 339000001 until fix-live: the smoke teams' journal refusals are a monitor defect", true},
		{"run-queue-fix", "mute monitors 339598935 and 339599887, they are noise", true},
		{"desk-runner", "SCALE-MISSED scale:R84898 zeta-sso-hop: The failing health check runs on its own schedule per saved login session, not once per customer run, so counting only Zeta runs leaves out the sessions (and any other teams' sessions) that go through the same blocked connection.", false},
		{"desk-runner", "SCALE-MISSED scale:R84898 zeta-sso-hop: counting only Zeta runs leaves out the sessions (and any other teams' sessions) the run-failure monitor sees.", false},
		{"zeta-sso-hop", "MECHANISM the N line leaves out the other teams' sessions, which stop counting toward the monitor", false},
		{"owner-relay", "462215's 5:30 AM is not a deadline a lane owes: it is when ai-oncall's mute on monitor 337843695 ends by itself. The incident behind it is linear-sync-slow.", false},
		{"owner-relay", "the mute on monitor 337843695 lapses at 5:30 AM", false},
	}
	for _, c := range cases {
		t.Run(c.text[:min(len(c.text), 40)], func(t *testing.T) {
			row := client.Record{Seq: 7, Lane: c.lane, Kind: "mechanism", At: t0, Text: c.text}
			if c.lane == Root {
				row.Kind = muteKind
			}
			watch, bus := muteWatch(row)

			if err := watch.Check(context.Background(), t0); err != nil {
				t.Fatal(err)
			}
			if err := watch.Check(context.Background(), t0.Add(muteEvery)); err != nil {
				t.Fatal(err)
			}

			if !c.pushed {
				equal(t, len(bus.posted), 0)
				return
			}
			equal(t, len(bus.posted), 1)
			equal(t, bus.posted[0].To, []string{Root})
			equal(t, bus.posted[0].Kind, muteKind)
			if !strings.HasPrefix(bus.posted[0].Text, "MUTE-PUSHBACK "+c.lane+": #7 proposes") || !strings.Contains(bus.posted[0].Text, "is never a close, and smoke teams are the canary") {
				t.Fatalf("text = %q", bus.posted[0].Text)
			}
		})
	}
}

func TestAMuteByARootOrderedFixLaneAfterItsFixLandedGetsNoPushback(t *testing.T) {
	const lane = "run-queue-fix"
	muted := "Step 2 muting monitor 339599887 with the ai-oncall mute verb until fix-live"
	cases := []struct {
		name    string
		goOrder bool
		answers bool
		landed  bool
		pushed  bool
	}{
		{"an order, its fix landed", false, false, true, false},
		{"an answer on the order, its fix landed", false, true, true, false},
		{"an order, no fix yet", false, false, false, true},
		{"a go is no incident order", true, false, true, true},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			order := client.Record{Seq: 1, Lane: Root, Kind: muteKind, At: t0, To: []string{lane}, Text: "INCIDENT run-queue-backlog: fix lane run-queue-fix with incident apply authority"}
			if c.goOrder {
				order.Kind = "go"
			}
			rows := []client.Record{order}
			if c.landed {
				rows = append(rows, client.Record{Seq: 2, Lane: lane, Kind: landedKind, At: t0, To: []string{Root}, Text: "MERGED #47601 as 3975fbea5ec, verified on fresh dev"})
			}
			mute := client.Record{Seq: 3, Lane: lane, Kind: muteKind, Status: "open", At: t0, To: []string{Root}, Text: muted}
			if c.answers {
				mute.Re = 1
			}
			watch, bus := muteWatch(append(rows, mute)...)

			if err := watch.Check(context.Background(), t0.Add(time.Minute)); err != nil {
				t.Fatal(err)
			}

			equal(t, len(bus.posted) == 1, c.pushed)
		})
	}
}

func TestAFalseAlarmVerdictGetsAPushbackOnlyWhenItsRecordIsTiedToAnIncidentOrMonitor(t *testing.T) {
	const verdict = "this LANDED-NOT-LIVE is a false alarm: api has run #48377 since release 12291"
	blocker := client.Record{Seq: 1, Lane: Self, Kind: blockerKind, Status: "open", At: t0, To: []string{Root}, Text: "LANDED-NOT-LIVE llm-gateway: #48377 landed, the oldest 90 minutes ago, and llm-gateway runs none of them"}
	incident := client.Record{Seq: 1, Lane: "alerts-desk", Kind: muteKind, Status: "open", At: t0, To: []string{Root}, Text: "dd-339598935 run failures on the smoke teams"}
	cases := []struct {
		name   string
		open   []client.Record
		tail   []client.Record
		re     int64
		attrs  map[string]string
		url    string
		pushed bool
	}{
		{"an answer on a supervisor blocker", nil, []client.Record{blocker}, 1, nil, "", false},
		{"an answer on nothing", nil, nil, 0, nil, "", false},
		{"an answer on an ask on an open incident", nil, []client.Record{incident, {Seq: 2, Lane: Root, Kind: "ask", At: t0, Re: 1, Text: "is the smoke-team failure real?"}}, 2, nil, "", true},
		{"an answer on an open incident older than the window", []client.Record{incident}, nil, 1, nil, "", true},
		{"an answer on a resolved incident", nil, []client.Record{{Seq: 1, Lane: "alerts-desk", Kind: muteKind, Status: "resolved", At: t0, Text: "dd-339598935 run failures on the smoke teams"}}, 1, nil, "", false},
		{"an answer naming a monitor", nil, nil, 0, map[string]string{"monitor": "339598935"}, "", true},
		{"an answer linking a monitor", nil, nil, 0, nil, "https://app.datadoghq.com/monitors/339598935", true},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			answer := client.Record{Seq: 3, Lane: "release-6", Kind: "answer", At: t0, To: []string{Root}, Re: c.re, Text: verdict}
			answer.Fields.Attrs, answer.Refs.URL = c.attrs, c.url
			bus := &fakeBus{open: c.open, tail: append(c.tail, answer)}
			watch := &MutePushback{Env: Env{Bus: bus, Config: config.Config{Drive: testDrive}}, Log: slog.Default()}

			if err := watch.Check(context.Background(), t0); err != nil {
				t.Fatal(err)
			}

			equal(t, len(bus.posted) == 1, c.pushed)
		})
	}
}
