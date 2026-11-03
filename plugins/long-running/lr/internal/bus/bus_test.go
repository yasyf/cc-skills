package bus_test

import (
	"context"
	"encoding/json"
	"errors"
	"reflect"
	"regexp"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
)

const head = "e718a7434fa01ee9835f992a9a647a825350732c"

func gradeFields() map[string]any {
	return map[string]any{"pr": json.Number("45821"), "head": head, "grader": "finder", "verdict": "clear"}
}

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

func TestAClearPostedAsTheWrongKindIsRefusedWithTheRightCommand(t *testing.T) {
	wantRefused(t, bus.Check("note", gradeFields(), []string{"supervisor"}),
		`these fields are a grade record, not note; run: lr post grade --pr 45821 --head `+head+` --grader finder --verdict clear --to supervisor`)
}

func TestAGradeNamingManyPRsIsRefusedWithOneCommandPerPR(t *testing.T) {
	fields := gradeFields()
	fields["pr"] = []any{json.Number("1"), json.Number("2"), json.Number("3")}

	wantRefused(t, bus.Check("grade", fields, nil),
		`a grade names one PR; post one record per PR: lr post grade --pr 1 .* && lr post grade --pr 2 .* && lr post grade --pr 3 `)
}

func TestRootHearsOnlyEscalationsAsksAndDecisions(t *testing.T) {
	for kind, fields := range map[string]map[string]any{
		"state": {"lane": "lane-7"},
		"done":  {"item": json.Number("3")},
		"grade": gradeFields(),
		"item":  {"item": json.Number("3"), "action": "restack", "pr": json.Number("1"), "head": head, "mover": "lane", "deadline": "2026-10-31T06:20:00Z"},
		"note":  {"text": "landing is slow"},
	} {
		wantRefused(t, bus.Check(kind, fields, []string{"root"}),
			`a `+kind+` record never reaches root; .*post an escalation, an ask or a decision`)
	}
	for kind, fields := range map[string]map[string]any{
		"escalation": {"subject": "org/repo#45470", "cause": "verdict overdue"},
		"ask":        {"text": "which base?"},
		"decision":   {"text": "land the bottom"},
	} {
		if err := bus.Check(kind, fields, []string{"root"}); err != nil {
			t.Errorf("%s to root: %v", kind, err)
		}
	}
}

func TestAGoByRootIsNotATap(t *testing.T) {
	wantRefused(t, bus.Check("tap", map[string]any{"pr": json.Number("45911"), "head": head, "by": "root", "value": "tapped"}, nil),
		`tap\.by must be one of owner, rule, got 'root'`)
}

func TestAFieldOfTheWrongShapeIsRefused(t *testing.T) {
	for _, tt := range []struct {
		field   string
		value   any
		message string
	}{
		{"head", "e718a74", "a full 40-character sha"},
		{"pr", "45821", "an integer"},
		{"pr", json.Number("1.5"), "an integer"},
		{"verdict", "lgtm", "one of clear, ask, pass, fail"},
		{"grader", "codex", "one of finder, fidelity"},
	} {
		fields := gradeFields()
		fields[tt.field] = tt.value
		wantRefused(t, bus.Check("grade", fields, nil), `grade\.`+tt.field+` must be `+tt.message)
	}
}

func TestAMissingFieldIsNamed(t *testing.T) {
	fields := gradeFields()
	delete(fields, "verdict")

	wantRefused(t, bus.Check("grade", fields, nil), `grade takes pr, head, grader, verdict; missing verdict`)
}

func TestAnUnknownFieldIsNamed(t *testing.T) {
	fields := gradeFields()
	fields["reviewer"] = "sol"

	wantRefused(t, bus.Check("grade", fields, nil), `grade takes pr, head, grader, verdict; unexpected reviewer`)
}

func TestAnUnknownKindListsTheTwelve(t *testing.T) {
	wantRefused(t, bus.Check("evidence", map[string]any{"text": "x"}, nil),
		`unknown kind 'evidence'; the kinds are head, grade, tap, verdict, item, done, escalation, decision, ask, answer, note, state`)
}

func TestABlankConditionIsRefused(t *testing.T) {
	wantRefused(t, bus.Check("tap", map[string]any{"pr": json.Number("45470"), "head": head, "by": "owner", "value": "tapped", "condition": "  "}, nil),
		`tap\.condition must be non-empty text`)
}

func TestAnItemReachesALaneByNameButNeverARole(t *testing.T) {
	item := map[string]any{"item": json.Number("3"), "action": "verdict", "pr": json.Number("45470"), "head": head, "mover": "lane", "deadline": "2026-10-31T06:20:00Z"}

	if err := bus.Check("item", item, []string{"lane-7"}); err != nil {
		t.Fatal(err)
	}
	wantRefused(t, bus.Check("item", item, []string{"root"}), `never reaches root`)
	wantRefused(t, bus.Check("state", map[string]any{"lane": "lane-7"}, []string{"lane-7"}), `never reaches lane-7`)
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

func TestReadSetsAsideABodyTheBusWouldHaveRefused(t *testing.T) {
	b := bound(t, "lane-7")
	good, err := b.Post(context.Background(), []string{"supervisor"}, bus.Grade{PR: 1, Head: head, Grader: "finder", Verdict: "clear"})
	if err != nil {
		t.Fatal(err)
	}
	manyPRs, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: "lane-7", Kind: "lr.grade", Text: "many PRs", To: []string{"supervisor"}, Body: json.RawMessage(`{"pr":[1,2],"head":"` + head + `","grader":"finder","verdict":"clear"}`)})
	if err != nil {
		t.Fatal(err)
	}
	toRoot, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: "lane-7", Kind: "lr.state", Text: "to root", To: []string{"root"}, Body: json.RawMessage(`{"lane":"lane-7"}`)})
	if err != nil {
		t.Fatal(err)
	}

	records, rejected, err := b.Read(context.Background(), 0)
	if err != nil {
		t.Fatal(err)
	}

	if len(records) != 1 || records[0].Seq != good.Seq {
		t.Fatalf("records = %+v", records)
	}
	if len(rejected) != 2 || rejected[0].Seq != manyPRs.Seq || rejected[1].Seq != toRoot.Seq {
		t.Fatalf("rejected = %+v", rejected)
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
		dec := json.NewDecoder(strings.NewReader(string(raw)))
		dec.UseNumber()
		var fields map[string]any
		if err := dec.Decode(&fields); err != nil {
			t.Fatal(err)
		}
		if err := bus.Check(body.Kind(), fields, nil); err != nil {
			t.Errorf("%T does not fit its kind: %v", body, err)
		}
		kinds = append(kinds, body.Kind())
	}
	want := []string{"head", "grade", "tap", "verdict", "item", "done", "escalation", "decision", "ask", "answer", "note", "state"}
	if !reflect.DeepEqual(kinds, want) {
		t.Fatalf("kinds = %v, want %v", kinds, want)
	}
}
