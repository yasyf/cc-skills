package lanes

import "testing"

func TestADeskNamingACardLeavesItWithTheLaneThatDrewIt(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.feed = append(f.feed,
		spawned(2, "meta-desk-3", writeBrief(t), t0, nil),
		rec(3, lane2, "report", t0.Add(mins(1)), "drew card-chat: which one?"),
		rec(4, "meta-desk-3", "note", t0.Add(mins(2)), "card-chat is with the design lane"),
	)
	f.peers = []Row{who(lane2, true, t0.Add(mins(2))), who("meta-desk-3", true, t0.Add(mins(2)))}
	tick(t, s, f, t0.Add(mins(2)))

	lane, err := CardLane(s, "card-chat")
	equal(t, []any{lane, err}, []any{lane2, error(nil)})
}

func TestACardOfARetiredLaneGoesToItsHeirAndToNoLaneWithoutOne(t *testing.T) {
	s := memory(t)
	f := stoppedOnACard(t, s)

	lane, err := CardLane(s, "card-chat")
	equal(t, []any{lane, err}, []any{"", error(nil)})

	f.feed = append(f.feed, cardWake(3, lane2, "picked Combined", t0.Add(mins(5))))
	tick(t, s, f, t0.Add(mins(5)))

	lane, err = CardLane(s, "card-chat")
	equal(t, []any{lane, err}, []any{heir, error(nil)})
}

func TestACardADeskTookBeforeDesksStoppedTakingGoesToNoLane(t *testing.T) {
	s := memory(t)
	if err := s.TakeCard("card-chat", OwnerRelay); err != nil {
		t.Fatal(err)
	}

	lane, err := CardLane(s, "card-chat")
	equal(t, []any{lane, err}, []any{"", error(nil)})
}

func TestAPullRequestGoesToTheLiveLaneThatOwnsItAndAnUnownedOneToNoLane(t *testing.T) {
	s := memory(t)
	adopted(t, s, writeBrief(t), "", 48159)

	owned, err := PRLane(s, 48159)
	equal(t, []any{owned, err}, []any{lane2, error(nil)})
	unowned, err := PRLane(s, 51995)
	equal(t, []any{unowned, err}, []any{"", error(nil)})
}
