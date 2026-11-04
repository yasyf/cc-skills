package board

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	readyRepo = "o/r"
	readyHead = "fa8217f33bf26d96b05780f47ebf5475dc90fae6"
	nextHead  = "1234567890abcdef1234567890abcdef12345678"
)

func tapRow(number int) store.ReviewRow {
	return store.ReviewRow{
		Repo: readyRepo, Number: number, Title: fmt.Sprintf("sand: fix %d", number), Lane: fmt.Sprintf("lane-%d", number), Head: readyHead,
		ForUsers: "Chats load.", PlanRow: "Checked.", Risk: "Low.", ReadyForTap: true, State: prrow.StateOpen, WaitsOn: "Only your approval.",
	}
}

func putRows(t *testing.T, f *linkFixture, rows ...store.ReviewRow) {
	t.Helper()
	if err := f.Cards.Store.PutReviewRows(rows); err != nil {
		t.Fatal(err)
	}
}

func readyPass(t *testing.T, f *linkFixture, named ...int) {
	t.Helper()
	cards := f.Cards
	cards.Session = "board-1"
	r := ReadyCards{Cards: cards, Repo: readyRepo, Lane: "supervisor", Named: func(context.Context) (map[int]bool, error) {
		found := map[int]bool{}
		for _, number := range named {
			found[number] = true
		}
		return found, nil
	}}
	if err := r.Pass(context.Background()); err != nil {
		t.Fatal(err)
	}
}

func askCalls(f *linkFixture) []string {
	var calls []string
	for _, call := range f.shell.seen() {
		if strings.HasPrefix(call, "cc-present ask ") {
			calls = append(calls, call)
		}
	}
	return calls
}

func TestARowTurningReadyDrawsOneCardAndASecondPassDrawsNothing(t *testing.T) {
	liveDrive(t)
	f := newLink(t)
	putRows(t, f, tapRow(50287))
	readyPass(t, f)
	f.run(t)
	if calls := askCalls(f); len(calls) != 1 || !strings.Contains(calls[0], "--lane supervisor") {
		t.Fatalf("asks = %v, want one card", calls)
	}
	var card struct {
		ID, Title string
		Children  []struct {
			Type, Head string
			Options    []struct{ Label string }
		}
	}
	if err := json.Unmarshal([]byte(f.shell.drawn["card-tap-50287-fa8217f"]), &card); err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(card.Title, "Tap: #50287") || card.Children[0].Options[0].Label != approveLabel || card.Children[1].Head != readyHead {
		t.Fatalf("card = %+v", card)
	}
	readyPass(t, f)
	f.run(t)
	if calls := askCalls(f); len(calls) != 1 {
		t.Fatalf("after a second pass, asks = %v, want still one", calls)
	}
}

func TestARowWithACardAtItsHeadDrawsNone(t *testing.T) {
	for name, block := range map[string]string{
		"a hand-drawn tap": fmt.Sprintf(`{"id":"card-rmw2","type":"card","children":[{"id":"c","type":"choice","options":[{"label":"Approve"}]},{"id":"p","type":"pr","number":50287,"head":%q}]}`, readyHead),
		"a merge card":     `{"id":"merge:50287:fa8217f","type":"card"}`,
	} {
		t.Run(name, func(t *testing.T) {
			liveDrive(t)
			f := newLink(t)
			f.shell.drawn = map[string]string{"existing": block}
			putRows(t, f, tapRow(50287))
			readyPass(t, f)
			if calls := askCalls(f); len(calls) != 0 {
				t.Fatalf("asks = %v, want none", calls)
			}
		})
	}
}

func TestAQuestionCardNamingTheRowIsNotItsTapCard(t *testing.T) {
	liveDrive(t)
	f := newLink(t)
	f.shell.drawn = map[string]string{"existing": fmt.Sprintf(`{"id":"card-q","type":"card","children":[{"id":"c","type":"choice","options":[{"label":"Wait for it"}]},{"id":"p","type":"pr","number":50287,"head":%q}]}`, readyHead)}
	putRows(t, f, tapRow(50287))
	readyPass(t, f)
	f.run(t)
	if calls := askCalls(f); len(calls) != 1 {
		t.Fatalf("asks = %v, want its own card", calls)
	}
}

func TestOnlyTenTapCardsAreLiveAndTheNextDrawsAsHeAnswersOne(t *testing.T) {
	liveDrive(t)
	f := newLink(t)
	var rows []store.ReviewRow
	for number := 100; number < 112; number++ {
		rows = append(rows, tapRow(number))
	}
	putRows(t, f, rows...)
	readyPass(t, f)
	f.run(t)
	if calls := askCalls(f); len(calls) != liveTapCards {
		t.Fatalf("asks = %d, want %d", len(calls), liveTapCards)
	}
	readyPass(t, f)
	f.run(t)
	if calls := askCalls(f); len(calls) != liveTapCards {
		t.Fatalf("with ten live, asks = %d, want still %d", len(calls), liveTapCards)
	}
	rows[0].Tapped = true
	putRows(t, f, rows[0])
	readyPass(t, f)
	f.run(t)
	if _, drawn := f.shell.drawn["card-tap-110-fa8217f"]; !drawn || len(askCalls(f)) != liveTapCards+1 {
		t.Fatalf("after one answer, asks = %d and #110 drawn %v", len(askCalls(f)), drawn)
	}
	if _, drawn := f.shell.drawn["card-tap-111-fa8217f"]; drawn {
		t.Fatal("#111 drew past the limit")
	}
}

func TestTheLimitDrawsAnOrderedPRFirstThenTheOneFreeingMostLanes(t *testing.T) {
	due := []store.ReviewRow{tapRow(100), tapRow(200), tapRow(300)}
	above := tapRow(201)
	above.Parent, above.ReadyForTap = "#200", false
	inTapOrder(due, append(slices.Clone(due), above), map[int]bool{300: true})
	got := []int{due[0].Number, due[1].Number, due[2].Number}
	if !slices.Equal(got, []int{300, 200, 100}) {
		t.Fatalf("order = %v", got)
	}
}

func TestARefusedDrawAlarmsOnceNamingThePRAndFreesItsPlace(t *testing.T) {
	liveDrive(t)
	f := newLink(t)
	f.shell.refuse = "the daemon is down"
	putRows(t, f, tapRow(50287))
	readyPass(t, f)
	f.run(t)
	alarms := addressedTo(t, linkRefusalKind, communicator)
	if len(alarms) != 1 || !strings.Contains(alarms[0].Text, readyRepo+"#50287") || !strings.Contains(alarms[0].Text, "the daemon is down") {
		t.Fatalf("alarms = %+v", alarms)
	}
	readyPass(t, f)
	f.run(t)
	if alarms := addressedTo(t, linkRefusalKind, communicator); len(alarms) != 1 {
		t.Fatalf("after a second pass, alarms = %d, want one", len(alarms))
	}
	facts, err := f.Cards.Store.FactsAtRowHeads(readyRepo)
	if err != nil {
		t.Fatal(err)
	}
	if facts[50287][FactTapCard] != tapCardRefused {
		t.Fatalf("fact = %q", facts[50287][FactTapCard])
	}
}

func TestANewHeadTakesTheOldCardDownAndDrawsItsOwn(t *testing.T) {
	liveDrive(t)
	f := newLink(t)
	row := tapRow(50287)
	putRows(t, f, row)
	readyPass(t, f)
	f.run(t)
	row.Head = nextHead
	putRows(t, f, row)
	readyPass(t, f)
	f.run(t)
	if _, old := f.shell.drawn["card-tap-50287-fa8217f"]; old {
		t.Fatal("the old head's card stayed on the board")
	}
	if _, drawn := f.shell.drawn["card-tap-50287-1234567"]; !drawn {
		t.Fatalf("drawn = %v, want the new head's card", f.shell.drawn)
	}
}

func TestACardDrawsInTheThreadARowAlreadyHas(t *testing.T) {
	liveDrive(t)
	f := newLink(t)
	replies := filepath.Join(f.state, repliesDir)
	if err := os.MkdirAll(replies, 0o700); err != nil {
		t.Fatal(err)
	}
	for name, sent := range map[string]string{
		"old":   `{"reply":{"prs":["https://github.com/o/r/pull/50287"],"thread":"1.1"},"at":"2026-11-04T10:00:00Z"}`,
		"new":   `{"reply":{"prs":["https://github.com/o/r/pull/50287"],"thread":"2.2"},"at":"2026-11-04T11:00:00Z"}`,
		"other": `{"reply":{"prs":["https://github.com/x/y/pull/50287"],"thread":"3.3"},"at":"2026-11-04T12:00:00Z"}`,
	} {
		if err := os.WriteFile(filepath.Join(replies, name+".json"), []byte(sent), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	putRows(t, f, tapRow(50287))
	readyPass(t, f)
	f.run(t)
	if calls := askCalls(f); len(calls) != 1 || !strings.Contains(calls[0], "--slack 2.2") {
		t.Fatalf("asks = %v, want one in thread 2.2", calls)
	}
}
