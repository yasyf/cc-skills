package lanes

import "testing"

func TestALaneThatPostsPastItsOpenCardWakesOnceWhenHeAnswersIt(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "", 51907)
	f.peers = []Row{onRoster(who(lane2, true, t0.Add(mins(21))))}
	f.feed = append(f.feed,
		rec(2, lane2, "blocker", t0, "#51907 at 8dbd227 lands only on his pick on card-rmw537imn", Root),
		rec(3, lane2, "state", t0.Add(mins(20)), "Acknowledged: #51907 stays at 8dbd227 until his pick", Root),
	)

	tick(t, s, f, t0.Add(mins(21)))
	lane := row(t, s, lane2)
	equal(t, []any{lane.Waiting, lane.Awaits}, []any{int64(0), "pick:card-rmw537imn"})
	f.wakes = nil

	f.feed = append(f.feed, rec(4, boardWatch, "owner", t0.Add(mins(30)), `Owner on board d50e80e8, Raw SQL incident (rmw537imn): picked "Fix forward"`, "all").withCard("card-rmw537imn"))
	f.peers = []Row{onRoster(who(lane2, true, t0.Add(mins(31))))}
	picked := tick(t, s, f, t0.Add(mins(31)))
	f.peers = []Row{onRoster(who(lane2, true, t0.Add(mins(40))))}
	again := tick(t, s, f, t0.Add(mins(40)))

	equal(t, picked.Woken, []string{lane2})
	equal(t, len(again.Woken), 0)
	equal(t, row(t, s, lane2).Awaits, "")
	contains(t, pageText(f.pages[0], "why"), "the owner answered card-rmw537imn, the card you wait on")
}

func TestAnAnswerToALaterAskEndsThatWaitAndKeepsTheCardItCarries(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "", 51907)
	f.peers = []Row{onRoster(who(lane2, true, t0.Add(mins(21))))}
	f.feed = append(f.feed,
		rec(2, lane2, "blocker", t0, "#51907 lands only on his pick on card-rmw537imn", Root),
		rec(3, lane2, "state", t0.Add(mins(10)), "Acknowledged: #51907 stays at 8dbd227 until his pick", Root),
		rec(4, lane2, "ask", t0.Add(mins(15)), "Which runner image does the canary use?", "meta-desk-3"),
		rec(5, "meta-desk-3", "answer", t0.Add(mins(20)), "The one pinned in lr.json", lane2).withRe(4),
	)

	tick(t, s, f, t0.Add(mins(21)))

	lane := row(t, s, lane2)
	equal(t, []any{lane.Waiting, lane.Awaits}, []any{int64(0), "pick:card-rmw537imn"})
}
