package bus_test

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"regexp"
	"slices"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
)

const head = "e718a7434fa01ee9835f992a9a647a825350732c"

func wantRefused(t *testing.T, err error, pattern string) {
	t.Helper()
	var refused *bus.Refused
	if !errors.As(err, &refused) {
		t.Fatalf("err = %v, want a refusal matching %q", err, pattern)
	}
	if !regexp.MustCompile(pattern).MatchString(refused.Message) {
		t.Fatalf("refused %q, want a match for %q", refused.Message, pattern)
	}
}

func bound(t *testing.T, lane string) bus.Bus {
	t.Helper()
	t.Setenv("CCI_HOME", t.TempDir())
	return bus.Bus{Drive: "d", Lane: lane}
}

func TestAnUnknownKindListsTheTwelve(t *testing.T) {
	wantRefused(t, bus.Check("evidence", json.RawMessage(`{"text":"x"}`), nil),
		`unknown kind 'evidence'; the kinds are head, grade, tap, verdict, item, done, escalation, decision, ask, answer, note, state`)
}

func TestABodyOfTheWrongShapeIsRefusedBeforeAnythingIsStored(t *testing.T) {
	b := bound(t, "finder-desk")

	_, err := b.Post(context.Background(), nil, bus.Grade{PR: 1, Head: "e718a74", Grader: "finder", Verdict: "clear"})

	wantRefused(t, err, `grade\.head must be a full 40-character sha`)
	rows, err := client.Tail(context.Background(), client.Query{Drive: "d"})
	if err != nil || len(rows) != 0 {
		t.Fatalf("Tail = %+v, %v", rows, err)
	}
}

func TestRootHearsNoTypedStateOrGrade(t *testing.T) {
	b := bound(t, "lane-7")

	_, err := b.Post(context.Background(), []string{"root"}, bus.State{Lane: "lane-7"})

	wantRefused(t, err, `a state record never reaches root; .*post an escalation, an ask or a decision`)
}

func TestAPostStoresATypedBodyWithItsIdentityKeyAndSummary(t *testing.T) {
	b := bound(t, "finder-desk")
	grade := bus.Grade{PR: 45821, Head: head, Grader: "finder", Verdict: "clear"}

	record, err := b.Post(context.Background(), []string{"supervisor"}, grade)
	if err != nil {
		t.Fatal(err)
	}

	if record.Body != bus.Body(grade) || record.Lane != "finder-desk" || !slices.Equal(record.To, []string{"supervisor"}) || record.Seq == 0 {
		t.Fatalf("record = %+v", record)
	}
	rows, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{"lr.grade"}})
	if err != nil || len(rows) != 1 {
		t.Fatalf("Tail = %+v, %v", rows, err)
	}
	if rows[0].ID != "lr.grade/45821/"+head+"/finder" {
		t.Errorf("id = %q", rows[0].ID)
	}
	if rows[0].Text != "grade #45821 e718a7434fa0 finder clear" {
		t.Errorf("text = %q", rows[0].Text)
	}
	if len(rows[0].Refs.PRs) != 1 || rows[0].Refs.PRs[0].Number != 45821 {
		t.Errorf("prs = %+v", rows[0].Refs.PRs)
	}
}

func TestAHeadRecordSummarizesAsItsPRHeadAndLane(t *testing.T) {
	b := bound(t, "lane-7")
	body := bus.Head{PR: 1, Head: head, Lane: "lane-7", Title: "the title"}

	record, err := b.Post(context.Background(), []string{"supervisor"}, body)
	if err != nil {
		t.Fatal(err)
	}

	if record.Body != bus.Body(body) {
		t.Fatalf("record = %+v", record)
	}
	rows, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{"lr.head"}})
	if err != nil || len(rows) != 1 || rows[0].Text != "head #1 e718a7434fa0 lane-7" {
		t.Fatalf("Tail = %+v, %v", rows, err)
	}
}

func TestATextKindSummarizesAsItsText(t *testing.T) {
	b := bound(t, "lane-7")

	if _, err := b.Post(context.Background(), []string{"lane-8"}, bus.Note{Text: "landing is slow"}); err != nil {
		t.Fatal(err)
	}

	rows, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{"lr.note"}})
	if err != nil || len(rows) != 1 || rows[0].Text != "note: landing is slow" || rows[0].ID != "" {
		t.Fatalf("Tail = %+v, %v", rows, err)
	}
}

func TestAnItemReachesALaneByName(t *testing.T) {
	b := bound(t, "supervisor")
	item := bus.Item{Item: 3, Action: "verdict", PR: 45470, Head: head, Mover: "lane", Deadline: "2026-10-31T06:20:00Z"}

	record, err := b.Post(context.Background(), []string{"lane-7"}, item)
	if err != nil {
		t.Fatal(err)
	}

	if record.Body != bus.Body(item) {
		t.Fatalf("record = %+v", record)
	}
	_, err = b.Post(context.Background(), []string{"root"}, item)
	wantRefused(t, err, `never reaches root`)
}

func TestAClassOfOverdueWaitsKeepsItsBodyInAFileTheRecordNames(t *testing.T) {
	b := bound(t, "supervisor")
	b.Bodies = t.TempDir()
	detail := "pick/1\nunfinished: cut at 6:30 AM?\n\npick/2\nunfinished: second\n"

	if _, err := b.Post(context.Background(), []string{"root"}, bus.OverdueClass("root", "unfinished/2/ab12", "2 overdue at once", detail)); err != nil {
		t.Fatal(err)
	}

	rows, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{"lr.escalation"}})
	if err != nil || len(rows) != 1 {
		t.Fatalf("Tail = %+v, %v", rows, err)
	}
	if rows[0].Text != "escalation unfinished/2/ab12 2 overdue at once" {
		t.Errorf("text = %q", rows[0].Text)
	}
	body, err := os.ReadFile(rows[0].Refs.Path)
	if err != nil || string(body) != detail {
		t.Fatalf("body = %q, %v", body, err)
	}
	var stored map[string]string
	if err := json.Unmarshal(rows[0].Body, &stored); err != nil {
		t.Fatal(err)
	}
	if len(stored) != 2 || stored["subject"] != "unfinished/2/ab12" {
		t.Errorf("stored = %v", stored)
	}
}

func TestAnOverdueClassToALaneIsOneNoteCarryingItsLines(t *testing.T) {
	got := bus.OverdueClass("lane-a", "gate/2/ab12", "2 overdue at once", "org/repo#1\nlate\n")
	if got != bus.Body(bus.Note{Text: "gate/2/ab12 2 overdue at once\n\norg/repo#1\nlate\n"}) {
		t.Fatalf("got = %+v", got)
	}
}

func TestAnOverdueReachesARoleAsAnEscalationAndALaneAsANote(t *testing.T) {
	b := bound(t, "supervisor")

	if _, err := b.Post(context.Background(), []string{"grader-appeals-1"}, bus.Overdue("grader-appeals-1", "org/repo#1", "grade overdue")); err != nil {
		t.Fatal(err)
	}
	if _, err := b.Post(context.Background(), []string{"root"}, bus.Overdue("root", "org/repo#1", "grade overdue")); err != nil {
		t.Fatal(err)
	}

	if got := bus.Overdue("grader-appeals-1", "org/repo#1", "grade overdue"); got != bus.Body(bus.Note{Text: "org/repo#1 grade overdue"}) {
		t.Fatalf("to a lane = %+v", got)
	}
	if got := bus.Overdue("root", "org/repo#1", "grade overdue"); got != bus.Body(bus.Escalation{Subject: "org/repo#1", Cause: "grade overdue"}) {
		t.Fatalf("to root = %+v", got)
	}
}

func TestASupervisorsOwnRecordIsStoredForItsReaders(t *testing.T) {
	b := bound(t, "supervisor")
	tap := bus.Tap{PR: 44989, Head: head, By: "rule", Value: "tapped", Rule: "test-only"}

	if _, err := b.Post(context.Background(), []string{"supervisor"}, tap); err != nil {
		t.Fatal(err)
	}

	records, rejected, err := b.Read(context.Background(), 0, "tap")
	if err != nil || rejected != nil || len(records) != 1 || records[0].Body != bus.Body(tap) {
		t.Fatalf("Read = %+v, %+v, %v", records, rejected, err)
	}
}

func TestPostingTheSameRecordTwiceIsOneRecordButAWithdrawalIsAnother(t *testing.T) {
	b := bound(t, "owner-board")
	tapped := bus.Tap{PR: 1, Head: head, By: "owner", Value: "tapped"}

	first, err := b.Post(context.Background(), []string{"supervisor"}, tapped)
	if err != nil {
		t.Fatal(err)
	}
	second, err := b.Post(context.Background(), []string{"supervisor"}, tapped)
	if err != nil {
		t.Fatal(err)
	}
	withdrawn, err := b.Post(context.Background(), []string{"supervisor"}, bus.Tap{PR: 1, Head: head, By: "owner", Value: "withdrawn"})
	if err != nil {
		t.Fatal(err)
	}

	if first.Seq != second.Seq || withdrawn.Seq == first.Seq {
		t.Fatalf("seqs %d, %d, %d", first.Seq, second.Seq, withdrawn.Seq)
	}
}

func TestATapCarriesItsConditionAndRuleAsTypedFields(t *testing.T) {
	b := bound(t, "owner-board")
	body := bus.Tap{PR: 45470, Head: head, By: "rule", Value: "tapped", Rule: "test-only", Condition: "only if the bot finding is superseded"}

	record, err := b.Post(context.Background(), []string{"supervisor"}, body)
	if err != nil {
		t.Fatal(err)
	}

	got, ok := record.Body.(bus.Tap)
	if !ok || got != body {
		t.Fatalf("body = %+v", record.Body)
	}
}

func TestAnAnswerRepliesToTheRecordItNames(t *testing.T) {
	b := bound(t, "root")
	ask, err := b.Post(context.Background(), []string{"lane-7"}, bus.Ask{Text: "which base?"})
	if err != nil {
		t.Fatal(err)
	}

	answer, err := b.Post(context.Background(), []string{"lane-7"}, bus.Answer{Re: ask.Seq, Text: "dev"})
	if err != nil {
		t.Fatal(err)
	}

	rows, err := client.Tail(context.Background(), client.Query{Drive: "d", After: ask.Seq, Kinds: []string{"lr.answer"}})
	if err != nil || len(rows) != 1 || rows[0].Seq != answer.Seq || rows[0].Re != ask.Seq {
		t.Fatalf("Tail = %+v, %v", rows, err)
	}
}

func TestReadReturnsTypedRecordsFromTheCursorOldestFirst(t *testing.T) {
	b := bound(t, "lane-7")
	first, err := b.Post(context.Background(), []string{"supervisor"}, bus.Head{PR: 1, Head: head, Lane: "lane-7"})
	if err != nil {
		t.Fatal(err)
	}
	second, err := b.Post(context.Background(), []string{"supervisor"}, bus.Verdict{PR: 46228, Head: head, Verdict: "accept"})
	if err != nil {
		t.Fatal(err)
	}

	records, rejected, err := b.Read(context.Background(), 0)
	if err != nil || rejected != nil {
		t.Fatalf("Read = %+v, %+v, %v", records, rejected, err)
	}
	if len(records) != 2 || records[0].Seq != first.Seq || records[1].Kind() != "verdict" {
		t.Fatalf("records = %+v", records)
	}
	later, _, err := b.Read(context.Background(), first.Seq)
	if err != nil || len(later) != 1 || later[0].Seq != second.Seq {
		t.Fatalf("Read after %d = %+v, %v", first.Seq, later, err)
	}
	filtered, _, err := b.Read(context.Background(), 0, "head")
	if err != nil || len(filtered) != 1 || filtered[0].Seq != first.Seq {
		t.Fatalf("Read of head = %+v, %v", filtered, err)
	}
	if records[0].At.Location() != time.UTC {
		t.Errorf("at = %v, want UTC", records[0].At)
	}
}

func TestEveryKindHasABodyWithExactlyItsFields(t *testing.T) {
	bodies := []bus.Body{
		bus.Head{PR: 1, Head: head, Lane: "l"},
		bus.Grade{PR: 1, Head: head, Grader: "finder", Verdict: "clear"},
		bus.Tap{PR: 1, Head: head, By: "rule", Value: "tapped", Rule: "r", Condition: "c"},
		bus.Verdict{PR: 1, Head: head, Verdict: "accept"},
		bus.Item{Item: 1, Action: "tap", PR: 1, Head: head, Mover: "lane", Deadline: "2026-10-31T06:20:00Z"},
		bus.Done{Item: 1},
		bus.Escalation{Subject: "s", Cause: "c"},
		bus.Decision{Text: "t"},
		bus.Ask{Text: "t"},
		bus.Answer{Re: 1, Text: "t"},
		bus.Note{Text: "t"},
		bus.State{Lane: "l", PR: 1, Head: head, Text: "t"},
	}
	kinds := make([]string, 0, len(bodies))
	for _, body := range bodies {
		raw, err := json.Marshal(body)
		if err != nil {
			t.Fatal(err)
		}
		if err := bus.Check(body.Kind(), raw, nil); err != nil {
			t.Errorf("%T does not fit its kind: %v", body, err)
		}
		kinds = append(kinds, body.Kind())
	}
	if want := client.TypedKinds(); !slices.Equal(kinds, want) {
		t.Fatalf("kinds = %v, want the table's %v", kinds, want)
	}
}
