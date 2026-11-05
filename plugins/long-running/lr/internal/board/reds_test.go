package board

import (
	"reflect"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
)

func TestARedHeadReadsItsNewestClassAtThatHeadAndAPushReadsUnclassedAgain(t *testing.T) {
	red := machine.Facts{Number: number, Head: head, Lane: "lane-7", CI: machine.CIFailure, Failing: []string{"lr-go", "pytest"}}
	since := time.Date(2026, 11, 3, 18, 0, 0, 0, time.UTC)
	at := func(machine.Facts) time.Time { return since }
	classes := []client.Record{
		{Text: "class=flake head=" + head[:7] + " owner=red-sweep rebuilt once"},
		{Text: "class=real head=" + head[:10] + " owner=lane-9 pushed a fix"},
		{Text: "class=real head=0000000 owner=lane-3 the older head"},
		{Text: "class=wrong head=" + head[:7] + " owner=lane-4 no such class"},
	}

	got := Reds(map[int]machine.Facts{number: red, number + 1: {Number: number + 1, Head: head, CI: machine.CISuccess}}, classes, nil, at)

	want := []Red{{Number: number, Head: head, Checks: "lr-go, pytest", Class: "real", Owner: "lane-9", Action: "pushed a fix", Since: since}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %+v, want %+v", got, want)
	}
	if line := got[0].Line(); line != "#45583 e718a74 | lr-go, pytest | real | owner lane-9 | red since 10:00 AM | pushed a fix" {
		t.Fatalf("line = %q", line)
	}

	pushed := red
	pushed.Head = "1234567" + head[7:]
	got = Reds(map[int]machine.Facts{number: pushed}, classes, nil, at)
	if len(got) != 1 || got[0].Class != unclassed || got[0].Owner != "lane-7" || got[0].Action != "" {
		t.Fatalf("after a push got %+v", got)
	}
}

func TestAnotherPersonsRedPRIsListedOnlyOnceTheOwnerTappedItAndThenNamesItsAuthor(t *testing.T) {
	carried := machine.Facts{Number: 47613, Head: head, Lane: "lane-7", ForeignAuthor: "teammate", CI: machine.CIFailure, Failing: []string{"lr-go"}}
	tapped := carried
	tapped.Number, tapped.Tap = 47615, machine.TapTapped
	at := func(machine.Facts) time.Time { return time.Time{} }

	got := Reds(map[int]machine.Facts{carried.Number: carried, tapped.Number: tapped}, nil, nil, at)

	if len(got) != 1 || got[0].Line() != "#47615 e718a74 | lr-go | unclassed | owner lane-7, by teammate | red since an unread time | no action recorded | on his tap list: he tapped it" {
		t.Fatalf("got %+v", got)
	}
}

func TestARedNamesWhetherHisTapOrHisWaitingCardCoversItAndARefusedCardDoesNot(t *testing.T) {
	red := func(n int) machine.Facts {
		return machine.Facts{Number: n, Head: head, Lane: "lane-7", CI: machine.CIFailure, Failing: []string{"lr-go"}}
	}
	tapped := red(49059)
	tapped.Tap = machine.TapTapped
	snapshot := map[int]machine.Facts{48159: red(48159), 49059: tapped, 50485: red(50485), 50486: red(50486)}
	cards := map[int]string{48159: tapCardDrawn, 50485: "tap-list-1", 50486: tapCardRefused}

	got := map[int]string{}
	for _, r := range Reds(snapshot, nil, cards, func(machine.Facts) time.Time { return time.Time{} }) {
		got[r.Number] = r.Tap
	}

	want := map[int]string{48159: cardWaitsOnHim, 49059: tappedByHim, 50485: cardWaitsOnHim, 50486: ""}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %+v, want %+v", got, want)
	}
}
