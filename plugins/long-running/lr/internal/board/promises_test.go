package board

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
)

const promiseQuestion = "Which report do you wait for?"

func promiseCard(t *testing.T, key, by, wait string) {
	t.Helper()
	block, err := askedCard(strings.NewReader(""), Ask{
		Title: promiseQuestion, Background: "The grading root cause is half proven.",
		Options: []string{wait + " :: Nothing changes until it lands.", "Go now :: The fix rolls on what is proven."}, NothingToBuild: "a choice of timing",
	}, "", "yasyf/cc-skills", everyPR)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := DrawCard(context.Background(), linkDrive, asker, key, cardBody{Block: block, Until: "at:3d", By: by}); err != nil {
		t.Fatal(err)
	}
}

const promiseID = "card-which-report-do-you-wait-for"

func TestACardDrawnWithAClockRecordsItsLaneAndClockForTheSupervisor(t *testing.T) {
	liveDrive(t)
	f := newLink(t)
	by := cardsNow.Add(2 * time.Hour)
	promiseCard(t, "promise", stamp(by), "Wait for the report")
	f.run(t)

	said, err := awaitLink(context.Background(), linkDrive, asker, "promise", busStart)
	if err != nil || !strings.Contains(said, asker+" cannot retire until a record carries delivered=card:"+promiseID) {
		t.Fatalf("awaitLink = %q, %v; want the lane told the card holds it", said, err)
	}
	promised, err := f.Cards.Promises(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	want := lanes.Promise{Card: promiseID, Lane: asker, Question: promiseQuestion, By: by}
	if len(promised) != 1 || promised[0].Card != want.Card || promised[0].Lane != want.Lane || promised[0].Question != want.Question || !promised[0].By.Equal(by) {
		t.Fatalf("Promises = %+v, want [%+v]", promised, want)
	}
}

func TestADeliveredRecordMeetsACardsPromiseForGood(t *testing.T) {
	liveDrive(t)
	f := newLink(t)
	promiseCard(t, "promise", stamp(cardsNow.Add(time.Hour)), "Wait for the report")
	f.run(t)
	if _, err := client.Post(context.Background(), client.Draft{Drive: linkDrive, Lane: asker, Kind: "report", Text: "the root-cause report is in his DM", Attrs: map[string]string{deliveredAttr: "owner:m11,card:" + promiseID}}); err != nil {
		t.Fatal(err)
	}

	for range 2 {
		promised, err := f.Cards.Promises(context.Background())
		if err != nil || len(promised) != 0 {
			t.Fatalf("Promises = %+v, %v; want none once a record delivered it", promised, err)
		}
	}
	_, rows, err := f.Cards.rows(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if rows[promiseID][metField] == "" {
		t.Fatalf("card row %v, want the delivering record's seq as met", rows[promiseID])
	}
}

func TestACardNamingAClockWithoutByDrawsWithAWarning(t *testing.T) {
	for wait, warns := range map[string]bool{
		"Wait for the 6:40 PM report": true,
		"Wait for the report":         false,
	} {
		t.Run(wait, func(t *testing.T) {
			liveDrive(t)
			f := newLink(t)
			promiseCard(t, "clock", "", wait)
			f.run(t)

			said, err := awaitLink(context.Background(), linkDrive, asker, "clock", busStart)
			if err != nil {
				t.Fatal(err)
			}
			if got := strings.Contains(said, `WARNING it names the clock "6:40 PM" and no --by`); got != warns {
				t.Fatalf("awaitLink = %q; want the clock warning %v", said, warns)
			}
			if promised, err := f.Cards.Promises(context.Background()); err != nil || len(promised) != 0 {
				t.Fatalf("Promises = %+v, %v; want none without --by", promised, err)
			}
		})
	}
}

func TestAskOwnerByTakesAClockStillAhead(t *testing.T) {
	now := cardsNow
	for given, want := range map[string]string{
		"90m":                       "2026-11-02T09:30:00Z",
		"2026-11-02T10:40:00-08:00": "2026-11-02T18:40:00Z",
		"":                          "",
	} {
		got, err := promiseClock(given, now)
		if err != nil || got != want {
			t.Errorf("promiseClock(%q) = %q, %v; want %q", given, got, err, want)
		}
	}
	if _, err := promiseClock("2026-11-01T18:40:00Z", now); err == nil || !strings.Contains(err.Error(), "already passed") {
		t.Errorf("promiseClock of a past clock = %v, want refused as passed", err)
	}
}
