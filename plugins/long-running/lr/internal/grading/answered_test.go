package grading

import (
	"context"
	"encoding/json"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/cardtext"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/grading/grader"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/scaletest"
)

const parkedCard = "card-rmw37abc"

func parkedOnACard(t *testing.T) (*poolFixture, map[int]machine.Facts) {
	t.Helper()
	f := newPool(t, map[string]any{"landing": map[string]any{"checkout": "/srv/main"}, "fidelity": map[string]any{}})
	post(t, client.Draft{Lane: grader.FidelityLane, Kind: kindReview, Text: "fidelity: missing " + head[:7] + " class owner-pick: the chapter title waits on his answer to card rmw37abc", PRs: []string{pull}})
	snapshot := map[int]machine.Facts{number: facts(head, func(f *machine.Facts) { f.Fidelity = machine.FidelityFail })}
	if started, _, err := f.pool.Reopen(context.Background(), snapshot); err != nil || len(started) != 0 {
		t.Fatalf("before any answer: started = %v, err = %v", started, err)
	}
	return f, snapshot
}

func answered(t *testing.T, f *poolFixture, label, id string, at time.Time, tags ...string) {
	t.Helper()
	rows := make([]map[string]any, 0, 1)
	if held := f.shell.labelled[label]; held != "" {
		if err := json.Unmarshal([]byte(held), &rows); err != nil {
			t.Fatal(err)
		}
	}
	rows = append(rows, map[string]any{"id": strings.Repeat(id, 40), "created_at": at.UTC().Format(time.RFC3339), "tags": append([]string{"from:owner", label}, tags...)})
	listed, err := json.Marshal(rows)
	if err != nil {
		t.Fatal(err)
	}
	if f.shell.labelled == nil {
		f.shell.labelled = map[string]string{}
	}
	f.shell.labelled[label] = string(listed)
}

func wakes(t *testing.T) []client.Record {
	t.Helper()
	found, err := client.Tail(context.Background(), client.Query{Drive: testDrive, Kinds: []string{kindReport}})
	if err != nil {
		t.Fatal(err)
	}
	return slices.DeleteFunc(found, func(r client.Record) bool { return !strings.HasPrefix(r.Text, "ANSWER-REGRADES") })
}

func TestHisAnswerRecordedOnTheCardRegradesItOnceAndWakesItsDesk(t *testing.T) {
	for name, answer := range map[string]struct {
		label string
		tags  []string
	}{
		"his tap on the card":                 {cardtext.BoardCardLabel + parkedCard, []string{"board-answer", "board-block:rmw37abc-choice", "proof:board-watch:board:d50e80e8:2227:d7375019"}},
		"his numbered reply in its thread":    {cardtext.BoardCardLabel + parkedCard, []string{"board-answer", "proof:slack-thread:1793985883.857779"}},
		"a relay's record of his typed words": {cardtext.CardLabel + parkedCard, nil},
	} {
		t.Run(name, func(t *testing.T) {
			f, snapshot := parkedOnACard(t)
			answered(t, f, answer.label, "6", time.Now().Add(time.Minute), answer.tags...)

			started, _, err := f.pool.Reopen(context.Background(), snapshot)
			if err != nil {
				t.Fatal(err)
			}
			again, _, err := f.pool.Reopen(context.Background(), snapshot)
			if err != nil {
				t.Fatal(err)
			}

			if len(started) != 1 || len(again) != 0 {
				t.Errorf("started = %v, then %v", started, again)
			}
			if spec := readJob[grader.FidelityJob](t, filepath.Join(f.pool.root, grader.FidelityLane, jobDir+regradeMark)); spec.Regrade != 1 {
				t.Errorf("regrade = %d", spec.Regrade)
			}
			posted := wakes(t)
			if len(posted) != 1 || !slices.Equal(posted[0].To, []string{oddDesk}) || !strings.Contains(posted[0].Text, "card rmw37abc") {
				t.Errorf("wakes = %+v", posted)
			}
		})
	}
}

func TestOnlyAnAnswerRecordedOnTheCardReopensIt(t *testing.T) {
	for name, record := range map[string]func(*testing.T, *poolFixture){
		"a relay's reading that names the card": func(t *testing.T, _ *poolFixture) {
			post(t, client.Draft{Lane: "owner-relay", Kind: "owner", To: []string{"root"}, Text: `Owner, 10:08 AM same thread, asked to be shown the open SandSQL picks. My reading: the three open SandSQL picks (card-rmw37abc, card-launch-parked-chats redrawn, card-replicator-behind redrawn) are drawn as cards in Slack thread 1793985883.857779. I draw them.`})
		},
		"his tap on another card": func(t *testing.T, f *poolFixture) {
			answered(t, f, cardtext.BoardCardLabel+"card-rmw99xyz", "6", time.Now().Add(time.Minute))
		},
		"his answer on the card before the grade": func(t *testing.T, f *poolFixture) {
			answered(t, f, cardtext.BoardCardLabel+parkedCard, "6", time.Now().Add(-time.Hour))
		},
	} {
		t.Run(name, func(t *testing.T) {
			f, snapshot := parkedOnACard(t)
			record(t, f)

			started, _, err := f.pool.Reopen(context.Background(), snapshot)
			if err != nil {
				t.Fatal(err)
			}

			if len(started) != 0 || len(wakes(t)) != 0 {
				t.Errorf("started = %v, wakes = %+v", started, wakes(t))
			}
		})
	}
}

func TestHisSecondAnswerWhileTheGradeStillWaitsIsCarriedToo(t *testing.T) {
	f, snapshot := parkedOnACard(t)
	answered(t, f, cardtext.BoardCardLabel+parkedCard, "6", time.Now().Add(time.Minute))
	if started, _, err := f.pool.Reopen(context.Background(), snapshot); err != nil || len(started) != 1 {
		t.Fatalf("his first answer: started = %v, err = %v", started, err)
	}
	answered(t, f, cardtext.CardLabel+parkedCard, "7", time.Now().Add(2*time.Minute))

	started, _, err := f.pool.Reopen(context.Background(), snapshot)
	if err != nil {
		t.Fatal(err)
	}
	again, _, err := f.pool.Reopen(context.Background(), snapshot)
	if err != nil {
		t.Fatal(err)
	}

	if len(started) != 1 || len(again) != 0 || len(wakes(t)) != 2 {
		t.Errorf("started = %v, then %v, wakes = %+v", started, again, wakes(t))
	}
}

func TestSuccessorLanesCostTheSameAtOneRecordAndMany(t *testing.T) {
	cost := func(records int) float64 {
		found := slices.Repeat([]client.Record{{Lane: "owner-relay-3"}}, records)
		return testing.AllocsPerRun(5, func() { withSuccessors([]string{grader.FidelityLane}, found) })
	}
	if one, many := cost(1), cost(scaletest.N()); many > 3*one {
		t.Fatalf("allocations at 1 record %v, at %d %v", one, scaletest.N(), many)
	}
}

func TestOneAnswerOnACardTwoPRsWaitOnRegradesBoth(t *testing.T) {
	f, snapshot := parkedOnACard(t)
	const other = number + 2
	otherHead := strings.Repeat("c", 40)
	otherFacts := facts(otherHead, func(f *machine.Facts) {
		f.Number = other
		f.Fidelity = machine.FidelityFail
	})
	snapshot[other] = otherFacts
	post(t, client.Draft{Lane: grader.FidelityLane, Kind: kindReview, Text: "fidelity: missing " + otherHead[:7] + " class owner-pick: the chapter title waits on his answer to card rmw37abc", PRs: []string{otherFacts.Repo + "#" + strconv.Itoa(other)}})
	answered(t, f, cardtext.BoardCardLabel+parkedCard, "6", time.Now().Add(time.Minute))

	started, _, err := f.pool.Reopen(context.Background(), snapshot)
	if err != nil {
		t.Fatal(err)
	}

	if len(started) != 2 || len(wakes(t)) != 2 {
		t.Errorf("started = %v, wakes = %+v", started, wakes(t))
	}
}

func TestAGradeOnACardTakenOffHisBoardSaysSoAndNothingElseDoes(t *testing.T) {
	f, snapshot := parkedOnACard(t)
	post(t, client.Draft{Lane: "root", Kind: kindWithdraw, Topic: "rmw37abc", Text: "its question has gone away"})

	_, waits, err := f.pool.Reopen(context.Background(), snapshot)
	if err != nil {
		t.Fatal(err)
	}

	if line := waits[number]; !strings.Contains(line, "card rmw37abc") || !strings.Contains(line, "taken off his board") {
		t.Errorf("waits = %v", waits)
	}
	answered(t, f, cardtext.BoardCardLabel+parkedCard, "6", time.Now().Add(time.Minute))
	if _, waits, err := f.pool.Reopen(context.Background(), snapshot); err != nil || len(waits) != 0 {
		t.Errorf("after his answer: waits = %v, err = %v", waits, err)
	}
}
