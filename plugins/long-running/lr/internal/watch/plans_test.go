package watch

import (
	"slices"
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
		{"a later step waiting on an answer", map[string]string{"owner": "memoryschema-1", "step:1": "2:30 AM open the PR", "step:2": "after his answer merge on green"}, 0},
		{"no timed step", map[string]string{"owner": "memoryschema-1", "step:1": "open the PR"}, 1},
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

func TestAClockIsADeadlineOnlyWhileStillAheadOfItsRecord(t *testing.T) {
	writtenAt := func(month time.Month, day, hour, minute int) time.Time {
		return time.Date(2026, month, day, hour, minute, 0, 0, pacific)
	}
	cases := []struct {
		name    string
		written time.Time
		text    string
		names   string
	}{
		{"a past date in a defect report", writtenAt(11, 4, 11, 3), "lr supervisor deadline check misfire: record 423101 read 'her page ... was on 9e3f296c by 10:18 AM Nov 3' in report 422988 as a deadline with no plan. A past-tense time in evidence is not a deadline; a date-qualified past time should not trip it.", ""},
		{"a past date in the evidence it quoted", writtenAt(11, 4, 10, 58), "her page (Sentry dashboard project) was on 9e3f296c by 10:18 AM Nov 3, which carries #46339.", ""},
		{"a past date with a later clock of day", writtenAt(11, 4, 9, 0), "it was on the page by 10:18 AM Nov 3", ""},
		{"an undated clock behind the record", writtenAt(11, 4, 3, 0), "the routing update was owed by 1:55 AM", ""},
		{"not a clock", writtenAt(11, 4, 3, 0), "padded before 13 PM", ""},
		{"a clock the record reports as done", writtenAt(11, 4, 13, 53), "By 1:55 PM I posted 10 grades on five PRs", ""},
		{"a clock it reports as landed", writtenAt(11, 4, 13, 53), "by 1:55 PM it landed", ""},
		{"the same clock as a promise", writtenAt(11, 4, 13, 53), "By 1:55 PM I post 10 grades", "1:55 PM"},
		{"an undated clock ahead", writtenAt(11, 4, 10, 45), "lands by 11:20 AM", "11:20 AM"},
		{"a later date", writtenAt(11, 4, 11, 0), "lands by 9 AM Nov 5", "9 AM"},
		{"tomorrow", writtenAt(11, 4, 11, 0), "lands by 9 AM tomorrow", "9 AM"},
		{"a weekday", writtenAt(11, 4, 11, 0), "lands by 9 AM on Friday", "9 AM"},
		{"evidence before a clock ahead", writtenAt(11, 4, 11, 0), "it was live by 10:18 AM Nov 3 and lands by 3 PM", "3 PM"},
		{"a date in the next year", writtenAt(12, 30, 11, 0), "lands by 9 AM Jan 2", "9 AM"},
		{"just after midnight, a clock late that day", writtenAt(11, 4, 0, 5), "lands by 11:50 PM", "11:50 PM"},
		{"just after midnight, a clock early that day", writtenAt(11, 4, 0, 5), "lands by 3 AM", "3 AM"},
		{"before midnight, a clock just past it", writtenAt(11, 3, 23, 50), "lands by 12:30 AM", "12:30 AM"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			record := client.Record{Seq: 40, Lane: "lane-a", Kind: "report", At: c.written, Text: c.text}
			waits := Unplanned([]client.Record{record}, c.written.Add(15*time.Minute))
			if c.names == "" {
				want(t, len(waits), 0)
				return
			}
			want(t, len(waits), 1)
			if !strings.Contains(waits[0].Cause, "a deadline by "+c.names+" ") {
				t.Fatalf("cause %q", waits[0].Cause)
			}
		})
	}
}

func TestARecordThatIsThePlanOwesNoOtherAndClearsTheRecordItAnswers(t *testing.T) {
	written := time.Date(2026, 11, 4, 11, 12, 0, 0, pacific)
	parent := client.Record{Seq: 39, Lane: "lane-a", Kind: "answer", At: written.Add(-time.Minute), Text: "Fold PR opens by 6 PM Nov 6."}
	steps := "(1) By 1 PM today: read the stream. (2) By 6 PM today: one wrapper. (3) By 6 PM Nov 5: one client."
	inWords := client.Record{Seq: 40, Lane: "lane-a", Kind: "state", Re: 39, At: written, Text: "Fold PR plan. Owner: lane-a (task 67613d5). " + steps}
	inAttrs := withAttrs(client.Record{Seq: 40, Lane: "lane-a", Kind: "state", Re: 39, At: written, Text: steps}, map[string]string{"owner": "lane-a", "step:1": "1 PM read the stream"})
	noOwner := client.Record{Seq: 40, Lane: "lane-a", Kind: "state", Re: 39, At: written, Text: steps}
	ownerOfSomethingElse := client.Record{Seq: 40, Lane: "lane-a", Kind: "state", Re: 39, At: written, Text: "the owner: asked. " + steps}
	namedNoClock := client.Record{Seq: 41, Lane: "lane-a", Kind: "state", Re: 39, At: written, Text: "Owner: lane-a, steps later"}
	ownerByLane := client.Record{Seq: 40, Lane: "lane-a", Kind: "note", Re: 39, At: written, Text: "Plan for the 3:00 PM landing, owner port-grades-usage: grades by 2:30 PM, ready by 2:40 PM."}
	ownerOfNoLane := client.Record{Seq: 40, Lane: "lane-a", Kind: "note", Re: 39, At: written, Text: "the owner asked for grades by 2:30 PM."}

	for _, tt := range []struct {
		name    string
		records []client.Record
		pages   []int
	}{
		{"an owner and clocks in words", []client.Record{parent, inWords}, nil},
		{"an owner and timed steps in attrs", []client.Record{parent, inAttrs}, nil},
		{"clocks with no owner", []client.Record{noOwner}, []int{40}},
		{"an owner named by lane in words", []client.Record{parent, ownerByLane}, nil},
		{"the word owner before no lane name", []client.Record{ownerOfNoLane}, []int{40}},
		{"the word owner naming nobody", []client.Record{ownerOfSomethingElse}, []int{40}},
		{"an owner with no clock clears nothing", []client.Record{parent, namedNoClock}, []int{39}},
	} {
		t.Run(tt.name, func(t *testing.T) {
			var pages []int
			for _, wait := range Unplanned(tt.records, written.Add(time.Minute)) {
				pages = append(pages, wait.Number)
			}
			if !slices.Equal(pages, tt.pages) {
				t.Fatalf("pages = %v, want %v", pages, tt.pages)
			}
		})
	}
}

func TestAReplyRestatingAPostedPlansClockIsNoNewDeadline(t *testing.T) {
	written := time.Date(2026, 11, 4, 11, 0, 0, 0, pacific)
	plan := withAttrs(client.Record{Seq: 40, Lane: "lane-a", Kind: "state", Re: 39, At: written, Text: "the plan"}, map[string]string{"owner": "lane-a", "step:1": "3:00 PM merge on green"})
	reply := func(re int64, text string) client.Record {
		return client.Record{Seq: 41, Lane: "lane-a", Kind: "report", Re: re, At: written.Add(time.Minute), Text: text}
	}
	for _, tt := range []struct {
		name    string
		records []client.Record
		names   string
	}{
		{"a reply to the plan", []client.Record{plan, reply(40, "still lands by 3 PM")}, ""},
		{"a reply beside the plan", []client.Record{plan, reply(39, "still lands by 3 PM")}, ""},
		{"a typed deadline restating it", []client.Record{plan, withAttrs(reply(40, "still on track"), map[string]string{"deadline": "3:00 PM"})}, ""},
		{"a new clock beside the restated one", []client.Record{plan, reply(40, "lands by 3 PM, and the docs by 5 PM")}, "5 PM"},
		{"a reply to another record", []client.Record{plan, reply(38, "lands by 3 PM")}, "3 PM"},
		{"a reply with no plan posted", []client.Record{reply(39, "lands by 3 PM")}, "3 PM"},
		{"a plan posted after the reply", []client.Record{reply(39, "lands by 3 PM"), withAttrs(client.Record{Seq: 42, Lane: "lane-a", Kind: "state", Re: 39, At: written.Add(2 * time.Minute), Text: "the plan"}, map[string]string{"owner": "lane-a", "step:1": "3:00 PM merge"})}, "3 PM"},
	} {
		t.Run(tt.name, func(t *testing.T) {
			waits := Unplanned(tt.records, written.Add(5*time.Minute))
			if tt.names == "" {
				want(t, len(waits), 0)
				return
			}
			want(t, len(waits), 1)
			want(t, waits[0].Number, 41)
			if !strings.Contains(waits[0].Cause, "a deadline by "+tt.names+" ") {
				t.Fatalf("cause %q", waits[0].Cause)
			}
		})
	}
}

func TestARecordCitingAnotherForItsClockRestatesItAndANewClockStillPages(t *testing.T) {
	written := time.Date(2026, 11, 4, 11, 0, 0, 0, pacific)
	plan := withAttrs(client.Record{Seq: 425066, Lane: "root", Kind: "answer", At: written, Text: "the override lands first"}, map[string]string{"owner": "one-reader", "step:1": "6 PM Nov 5 stacked fold PR"})
	noClock := client.Record{Seq: 425066, Lane: "root", Kind: "answer", At: written, Text: "the fold PR opens"}
	unplanned := client.Record{Seq: 425066, Lane: "root", Kind: "answer", At: written, Text: "the fold PR opens by 6 PM Nov 5"}
	order := client.Record{Seq: 425066, Lane: "root", Kind: "hold", At: written, Text: "hold the fold until 6 PM Nov 5"}
	later := written.Add(time.Minute)
	for _, tt := range []struct {
		name  string
		first client.Record
		text  string
		names string
	}{
		{"named by bare number", plan, "Your 425066 took A; the fold opens by 6 PM Nov 5", ""},
		{"named with a hash", plan, "per #425066 the fold opens by 6 PM Nov 5", ""},
		{"a new clock beside the named one", plan, "per 425066 the fold opens by 6 PM Nov 5, the docs by 8 PM", "8 PM"},
		{"citing a record with no plan for its clock", unplanned, "per 425066 the fold opens by 6 PM Nov 5", ""},
		{"citing a record that never named the clock", noClock, "per 425066 the fold opens by 6 PM Nov 5", "6 PM"},
		{"taking on a clock from an order, which owes no plan itself", order, "per 425066 the fold opens by 6 PM Nov 5", "6 PM"},
		{"a pull request number is no record", plan, "#50107 lands by 6 PM Nov 5", "6 PM"},
		{"a finder id ending in the number", plan, "finder 9@425066 lands by 6 PM Nov 5", "6 PM"},
	} {
		t.Run(tt.name, func(t *testing.T) {
			restating := client.Record{Seq: 425610, Lane: "grader-appeals", Kind: "decide", At: later, Text: tt.text}
			waits := slices.DeleteFunc(Unplanned([]client.Record{tt.first, restating}, written.Add(5*time.Minute)), func(wait Wait) bool { return wait.Number != 425610 })
			if tt.names == "" {
				want(t, len(waits), 0)
				return
			}
			want(t, len(waits), 1)
			if !strings.Contains(waits[0].Cause, "a deadline by "+tt.names+" ") {
				t.Fatalf("cause %q", waits[0].Cause)
			}
		})
	}
}

func TestAReplyToARecordWithNoPlanRestatesItsClockAndOnlyTheFirstPages(t *testing.T) {
	written := time.Date(2026, 11, 4, 11, 0, 0, 0, pacific)
	first := client.Record{Seq: 40, Lane: "root", Kind: "report", At: written, Text: "the review lands by 3 PM"}
	reply := client.Record{Seq: 41, Lane: "lane-a", Kind: "report", Re: 40, At: written.Add(time.Minute), Text: "still lands by 3 PM"}

	waits := Unplanned([]client.Record{first, reply}, written.Add(5*time.Minute))

	want(t, len(waits), 1)
	want(t, waits[0].Number, 40)
}

func TestHisOwnWordsNamingAClockOweNoPlan(t *testing.T) {
	said := at(t, "2026-11-03T10:00:00Z")
	row := client.Record{Seq: 40, Lane: "owner-relay", Kind: "owner", At: said, Text: "get this done by 3 PM"}

	want(t, len(Unplanned([]client.Record{row}, said)), 0)
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

func liveLanes(t *testing.T, s *store.Store, now time.Time, lanes ...store.Lane) {
	t.Helper()
	for _, lane := range lanes {
		lane.Brief, lane.Model, lane.Effort, lane.Role, lane.State = "b", "opus", "xhigh", "desk", store.LaneDesired
		if _, err := s.WantLane(lane, now); err != nil {
			t.Fatal(err)
		}
	}
}

func retireLane(t *testing.T, s *store.Store, name, by string, now time.Time) {
	t.Helper()
	if err := s.SetLaneState(name, store.LaneRetired, now, by, ""); err != nil {
		t.Fatal(err)
	}
}

func TestAStepClockPassingWithNothingFromItsOwnerTellsThatLaneAndTheCommunicatorOnce(t *testing.T) {
	s, r := memory(t), &recorder{}
	said := at(t, "2026-11-05T05:00:00Z")
	liveLanes(t, s, said, store.Lane{Name: "tap-rows"})
	row := withAttrs(client.Record{Seq: 912, Lane: "owner-board", Kind: "posted", At: said, Text: "Received, with the coordinator"}, map[string]string{"row": "m912", "owner": "tap-rows", "step:1": "9:15 PM answer his question"})

	early := planSweep(t, s, r, said.Add(14*time.Minute), row)
	first := planSweep(t, s, r, said.Add(15*time.Minute), row)
	second := planSweep(t, s, r, said.Add(20*time.Minute), row)

	want(t, len(early), 0)
	want(t, len(first), 2)
	want(t, first[0].To, "tap-rows")
	want(t, first[1].To, "owner-relay")
	for _, words := range []string{"step clock passed", "#912", "row m912", "step:1=9:15 PM answer his question", "tap-rows"} {
		if !strings.Contains(first[1].Cause, words) {
			t.Fatalf("cause %q lacks %q", first[1].Cause, words)
		}
	}
	want(t, len(r.posts), 1)
	want(t, len(second), 0)
}

func TestOnlyTheStepNoRecordFromItsOwnerAnswersGoesOverdue(t *testing.T) {
	s, r := memory(t), &recorder{}
	said := at(t, "2026-11-05T05:00:00Z")
	liveLanes(t, s, said, store.Lane{Name: "tap-rows-1"})
	plan := withAttrs(client.Record{Seq: 50, Lane: "tap-rows-1", Kind: "report", At: said, Text: "the rows land tonight"}, map[string]string{"owner": "tap-rows", "step:1": "9:30 PM open the PR", "step:2": "10:00 PM land it"})
	stranger := client.Record{Seq: 51, Lane: "meta-desk-3", Kind: "note", At: said.Add(10 * time.Minute), Re: 50, Text: "seen"}
	opened := client.Record{Seq: 52, Lane: "tap-rows-1", Kind: "report", At: said.Add(25 * time.Minute), Re: 50, Text: "opened"}
	records := []client.Record{plan, stranger, opened}

	want(t, NextStep(records, said).Equal(said.Add(30*time.Minute)), true)
	atFirst := planSweep(t, s, r, said.Add(31*time.Minute), records...)
	atSecond := planSweep(t, s, r, said.Add(time.Hour), records...)

	want(t, len(atFirst), 0)
	want(t, len(atSecond), 1)
	want(t, atSecond[0].To, "tap-rows")
	if !strings.Contains(atSecond[0].Cause, "step:2=10:00 PM land it") {
		t.Fatalf("cause %q", atSecond[0].Cause)
	}
}

func TestAResolvedOrDeliveredStepRecordStaysSilent(t *testing.T) {
	said := at(t, "2026-11-05T05:00:00Z")
	row := withAttrs(client.Record{Seq: 864, Lane: "owner-board", Kind: "posted", At: said}, map[string]string{"row": "m864", "owner": "owner-relay", "step:1": "9:15 PM answer his question"})
	delivered := withAttrs(client.Record{Seq: 870, Lane: "owner-relay-2", Kind: "report", At: said.Add(5 * time.Minute)}, map[string]string{"delivered": "owner:m864"})
	resolved := client.Record{Seq: 871, Lane: "root", Kind: "answer", At: said.Add(5 * time.Minute), Resolves: 864}

	relay := []store.Lane{{Name: "owner-relay-2", State: store.LaneDesired}}

	want(t, len(MissedSteps([]client.Record{row, delivered}, relay, said.Add(time.Hour))), 0)
	want(t, len(MissedSteps([]client.Record{row, resolved}, relay, said.Add(time.Hour))), 0)
	want(t, len(MissedSteps([]client.Record{row}, relay, said.Add(time.Hour))), 1)
}

func TestAStepOwnedByARetiredLaneWakesNobody(t *testing.T) {
	s, r := memory(t), &recorder{}
	said := at(t, "2026-11-05T05:00:00Z")
	liveLanes(t, s, said, store.Lane{Name: "tap-rows-1"})
	retireLane(t, s, "tap-rows-1", "root", said.Add(5*time.Minute))
	row := withAttrs(client.Record{Seq: 912, Lane: "owner-board", Kind: "posted", At: said}, map[string]string{"row": "m912", "owner": "tap-rows", "step:1": "9:15 PM answer his question"})

	want(t, len(planSweep(t, s, r, said.Add(20*time.Minute), row)), 0)
	want(t, len(r.posts), 0)
}

func TestAStepOnARecordOlderThanItsOwnersRelaunchWakesNobodyWhileANewerOneWakesTheSuccessor(t *testing.T) {
	s, r := memory(t), &recorder{}
	said := at(t, "2026-11-05T05:00:00Z")
	liveLanes(t, s, said, store.Lane{Name: "tap-rows-1"})
	relaunched := said.Add(5 * time.Minute)
	retireLane(t, s, "tap-rows-1", "tap-rows-2", relaunched)
	liveLanes(t, s, relaunched, store.Lane{Name: "tap-rows-2", Predecessor: "tap-rows-1"})
	before := withAttrs(client.Record{Seq: 60, Lane: "root", Kind: "report", At: said}, map[string]string{"owner": "tap-rows", "step:1": "9:15 PM open the PR"})
	after := withAttrs(client.Record{Seq: 61, Lane: "root", Kind: "report", At: relaunched.Add(time.Minute)}, map[string]string{"owner": "tap-rows", "step:1": "9:15 PM land it"})

	escalated := planSweep(t, s, r, said.Add(20*time.Minute), before, after)

	want(t, len(escalated), 1)
	want(t, escalated[0].Subject, "step#61")
	want(t, escalated[0].To, "tap-rows")
}

func TestAStepWhoseClockPassedBeforeThePlanWindowIsHistoryAndWakesNobody(t *testing.T) {
	said := at(t, "2026-11-05T05:00:00Z")
	plan := withAttrs(client.Record{Seq: 70, Lane: "root", Kind: "report", At: said}, map[string]string{"owner": "tap-rows", "step:1": "9:15 PM open the PR"})
	live := []store.Lane{{Name: "tap-rows", State: store.LaneDesired}}

	want(t, len(MissedSteps([]client.Record{plan}, live, said.Add(15*time.Minute+planWindow))), 1)
	want(t, len(MissedSteps([]client.Record{plan}, live, said.Add(16*time.Minute+planWindow))), 0)
}

func TestAStepClockJustBehindItsRecordFiresOnItsOwnDayAndNeverOnTheNext(t *testing.T) {
	written := at(t, "2026-11-05T17:39:00Z")
	start := withAttrs(client.Record{Seq: 484932, Lane: "release-6", Kind: "release", At: written}, map[string]string{"owner": "release-6", "step:1": "9:38 AM cut 13295 started"})
	live := []store.Lane{{Name: "release-6", State: store.LaneDesired}}

	want(t, len(MissedSteps([]client.Record{start}, live, written.Add(time.Minute))), 1)
	want(t, len(MissedSteps([]client.Record{start}, live, written.Add(24*time.Hour-time.Minute))), 0)
	want(t, len(MissedSteps([]client.Record{start}, live, written.Add(24*time.Hour+5*time.Minute))), 0)
	want(t, NextStep([]client.Record{start}, written).IsZero(), true)
}

func TestAStepClockPastMidnightIsDueTheNextMorningAndFiresOnce(t *testing.T) {
	written := at(t, "2026-11-06T07:00:00Z")
	morning := at(t, "2026-11-06T09:00:00Z")
	plan := withAttrs(client.Record{Seq: 90, Lane: "root", Kind: "report", At: written}, map[string]string{"owner": "tap-rows", "step:1": "1:00 AM land it"})
	live := []store.Lane{{Name: "tap-rows", State: store.LaneDesired}}

	want(t, NextStep([]client.Record{plan}, written).Equal(morning), true)
	want(t, len(MissedSteps([]client.Record{plan}, live, morning.Add(-time.Minute))), 0)
	want(t, len(MissedSteps([]client.Record{plan}, live, morning.Add(5*time.Minute))), 1)
	want(t, len(MissedSteps([]client.Record{plan}, live, morning.Add(24*time.Hour+5*time.Minute))), 0)
}

func TestAStepNamingADateIsDueOnThatDate(t *testing.T) {
	written := at(t, "2026-11-05T17:39:00Z")
	plan := withAttrs(client.Record{Seq: 91, Lane: "root", Kind: "report", At: written}, map[string]string{"owner": "tap-rows", "step:1": "9:00 AM Nov 7 cut the release"})

	want(t, NextStep([]client.Record{plan}, written).Equal(at(t, "2026-11-07T17:00:00Z")), true)
}

func TestAStepOnTheOwnersApprovedOrderReachesTheCommunicatorThoughOnlyTheSenderIsAddressed(t *testing.T) {
	said := at(t, "2026-11-05T22:37:48Z")
	approved := withAttrs(client.Record{Seq: 504273, Lane: "owner-relay", Kind: "owner", At: said, To: []string{"handover-option-a", "root"}, Text: "Owner tapped \"Send it\": send exactly this DM"}, map[string]string{"owner": "handover-option-a", "step:1": "2:50 PM DM sent, permalink to owner-relay"})
	live := []store.Lane{{Name: "handover-option-a", State: store.LaneDesired}}

	waits := MissedSteps([]client.Record{approved}, live, said.Add(15*time.Minute))

	want(t, len(waits), 2)
	want(t, waits[0].To, "handover-option-a")
	want(t, waits[1].To, "owner-relay")
}

func TestTheCommunicatorHearsOnlyOfStepsAddressedToItOrOnOwnerRowsInOneLine(t *testing.T) {
	s, r := memory(t), &recorder{}
	said := at(t, "2026-11-05T05:00:00Z")
	liveLanes(t, s, said, store.Lane{Name: "tap-rows"}, store.Lane{Name: "landing-desk-3"})
	rowed := withAttrs(client.Record{Seq: 80, Lane: "owner-board", Kind: "posted", At: said}, map[string]string{"row": "m80", "owner": "tap-rows", "step:1": "9:15 PM answer his question"})
	addressed := withAttrs(client.Record{Seq: 81, Lane: "root", Kind: "report", At: said, To: []string{"owner-relay", "landing-desk"}}, map[string]string{"owner": "landing-desk", "step:1": "9:15 PM land it"})
	asides := withAttrs(client.Record{Seq: 82, Lane: "root", Kind: "report", At: said, To: []string{"tap-rows"}}, map[string]string{"owner": "tap-rows", "step:1": "9:15 PM open the PR"})

	escalated := planSweep(t, s, r, said.Add(20*time.Minute), rowed, addressed, asides)

	relayed := slices.DeleteFunc(slices.Clone(escalated), func(each pass.Escalated) bool { return each.To != "owner-relay" })
	want(t, len(escalated), 5)
	want(t, len(relayed), 2)
	i := slices.IndexFunc(r.posts, func(p posted) bool { return slices.Contains(p.to, "owner-relay") })
	want(t, r.posts[i].body.Cause, "2 step clocks passed with no answer from their owners, each told: landing-desk 1, tap-rows 1")
	want(t, r.posts[i].body.Detail, "")
}
