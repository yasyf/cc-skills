package watch

import (
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/pass"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func withAttrs(record client.Record, attrs map[string]string) client.Record {
	record.Fields.Attrs = attrs
	return record
}

func planSweep(t *testing.T, s *store.Store, r *recorder, now time.Time, records ...client.Record) []pass.Escalated {
	t.Helper()
	escalated, err := Sweep(s, records, nil, nil, now, r.post)
	if err != nil {
		t.Fatal(err)
	}
	return escalated
}

func TestAPlanlessPageSaysByBeforeItsClockSoTheBusTakesAClockStillAhead(t *testing.T) {
	said := at(t, "2026-11-03T10:00:00Z")
	deadline := client.Record{Seq: 41, Lane: "lane-a", Kind: "report", At: said, Text: "the plan"}
	deadline.Fields.Attrs = map[string]string{"deadline": "6:00 AM"}

	waits := Unplanned([]client.Record{deadline}, said)

	want(t, len(waits), 1)
	if !strings.Contains(waits[0].Cause, "a deadline by 6:00 AM") {
		t.Fatalf("cause %q", waits[0].Cause)
	}
}

func TestADeadlineWithNoPlanPagesItsOwnLaneOnceNamingTheRecordDeadlineAndLane(t *testing.T) {
	s, r := memory(t), &recorder{}
	said := at(t, "2026-11-03T10:00:00Z")
	deadline := client.Record{Seq: 40, Lane: "memoryschema-1", Kind: "report", At: said, Text: "schema migration lands by 3:10 AM"}

	first := planSweep(t, s, r, said.Add(time.Minute), deadline)
	second := planSweep(t, s, r, said.Add(6*time.Minute), deadline)

	want(t, len(first), 1)
	want(t, first[0].Subject, "deadline#40")
	want(t, first[0].To, "memoryschema-1")
	for _, words := range []string{"deadline with no plan", "#40", "memoryschema-1", "3:10 AM"} {
		if !strings.Contains(first[0].Cause, words) {
			t.Fatalf("cause %q lacks %q", first[0].Cause, words)
		}
	}
	want(t, len(second), 0)
}

func TestADeadlineCarryingAnOwnerAndATimedStepEachIsSilent(t *testing.T) {
	said := at(t, "2026-11-03T10:00:00Z")
	cases := []struct {
		name  string
		attrs map[string]string
		pages int
	}{
		{"owner and timed steps", map[string]string{"owner": "memoryschema-1", "step:1": "2:30 AM open the PR", "step:2": "3:00 AM merge on green"}, 0},
		{"no owner", map[string]string{"step:1": "2:30 AM open the PR"}, 1},
		{"no steps", map[string]string{"owner": "memoryschema-1"}, 1},
		{"a step without a time", map[string]string{"owner": "memoryschema-1", "step:1": "2:30 AM open the PR", "step:2": "merge on green"}, 1},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			s, r := memory(t), &recorder{}
			deadline := withAttrs(client.Record{Seq: 40, Lane: "memoryschema-1", Kind: "report", At: said, Text: "schema migration lands by 3:10 AM"}, c.attrs)
			want(t, len(planSweep(t, s, r, said.Add(time.Minute), deadline)), c.pages)
		})
	}
}

func TestALaterPlanRecordClearsTheDeadlinePage(t *testing.T) {
	s, r := memory(t), &recorder{}
	said := at(t, "2026-11-03T10:00:00Z")
	deadline := client.Record{Seq: 40, Lane: "memoryschema-1", Kind: "report", At: said, Text: "schema migration lands by 3:10 AM"}
	plan := withAttrs(client.Record{Seq: 41, Lane: "memoryschema-1", Kind: "report", At: said.Add(10 * time.Minute), Re: 40, Text: "plan for the migration"},
		map[string]string{"owner": "memoryschema-1", "step:1": "2:30 AM open the PR", "step:2": "3:00 AM merge on green"})

	want(t, len(planSweep(t, s, r, said.Add(time.Minute), deadline)), 1)
	want(t, len(planSweep(t, s, r, said.Add(11*time.Minute), deadline, plan)), 0)

	open, err := s.OpenItems(PlanNamespace, 40)
	if err != nil || len(open) != 0 {
		t.Fatalf("open = %+v, %v", open, err)
	}
}

func TestAClockTimeWithoutByBeforeUntilOrDueIsNoDeadline(t *testing.T) {
	said := at(t, "2026-11-03T10:00:00Z")
	for _, text := range []string{"2:47 AM slack-bugs-1: quiet since 9:45 PM", "relaunched at 3:10 AM", "overdue 3 PM row closed", "owed to root since before 5 AM", "output below, ran before\n 03:42:54 up 1 day, 12:34", "lease held until 03:42:54 UTC"} {
		t.Run(text, func(t *testing.T) {
			s, r := memory(t), &recorder{}
			want(t, len(planSweep(t, s, r, said.Add(time.Minute), client.Record{Seq: 40, Lane: "lane-a", Kind: "state", At: said, Text: text})), 0)
		})
	}
	for _, text := range []string{"lands before 4 PM", "holds until 21:30", "due 9:00 a.m. Nov 4", "quiet since before 5 AM, lands by 9 AM"} {
		t.Run(text, func(t *testing.T) {
			s, r := memory(t), &recorder{}
			want(t, len(planSweep(t, s, r, said.Add(time.Minute), client.Record{Seq: 40, Lane: "lane-a", Kind: "state", At: said, Text: text})), 1)
		})
	}
}

func TestAnOrderNamingAClockOwesNoPlanAndAReportNamingOneStillDoes(t *testing.T) {
	said := at(t, "2026-11-03T10:00:00Z")
	text := "orca-desk: relay to iris-open-fix: land it by 11:10 AM"
	for _, tt := range []struct {
		record client.Record
		pages  int
	}{
		{client.Record{Seq: 40, Lane: "root", Kind: "hold", At: said, Text: text}, 0},
		{client.Record{Seq: 40, Lane: "root", Kind: "go", At: said, Text: text}, 0},
		{client.Record{Seq: 40, Lane: "root", Kind: "lift", At: said, Text: text}, 0},
		{client.Record{Seq: 40, Lane: "root", Kind: "report", At: said, Text: text}, 1},
	} {
		t.Run(string(tt.record.Kind), func(t *testing.T) {
			s, r := memory(t), &recorder{}
			want(t, len(planSweep(t, s, r, said.Add(time.Minute), tt.record)), tt.pages)
		})
	}
}

func TestTheDeskRunnersRelayOfAnOrderPagesNothing(t *testing.T) {
	s, r := memory(t), &recorder{}
	said := at(t, "2026-11-03T10:00:00Z")
	relay := client.Record{Seq: 40, Lane: "desk-runner", Kind: "note", At: said, Text: "relay: land it by 5:00 AM"}

	want(t, len(planSweep(t, s, r, said.Add(time.Minute), relay)), 0)
}

func TestTheSupervisorsOwnEscalationQuotingADeadlinePagesNothing(t *testing.T) {
	s, r := memory(t), &recorder{}
	said := at(t, "2026-11-03T10:00:00Z")
	escalation := client.Record{Seq: 40, Lane: "supervisor", Kind: "lr.escalation", At: said, Text: "escalation deadline#39 deadline with no plan: #39 names a deadline by 10:51 AM"}

	want(t, len(planSweep(t, s, r, said.Add(time.Minute), escalation)), 0)
}

func TestARestartPagesNothingForADeadlineOlderThanThePlanWindow(t *testing.T) {
	s, r := memory(t), &recorder{}
	said := at(t, "2026-11-03T10:00:00Z")
	stale := client.Record{Seq: 40, Lane: "lane-a", Kind: "state", At: said, Text: "watching; next state by 10:02 AM"}

	want(t, len(planSweep(t, s, r, said.Add(planWindow+time.Minute), stale)), 0)
	want(t, len(r.posts), 0)
}

func TestAReplyToTheOwnerOwesNoPlanForTheDeadlineItRepeats(t *testing.T) {
	said := at(t, "2026-11-03T10:00:00Z")
	reply := client.Record{Seq: 42, Lane: "owner-relay", Kind: "note", At: said, To: []string{"owner"}, Text: "the fix lands by 6:00 AM"}

	want(t, len(Unplanned([]client.Record{reply}, said)), 0)
}
