package lanes

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/owner"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func briefCarrying(t *testing.T, orders string) string {
	t.Helper()
	return briefSaying(t, "Assigned orders: "+orders)
}

func briefSaying(t *testing.T, lines string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "brief-conflicts-2.md")
	if err := os.WriteFile(path, []byte("# conflicts-2: run the four spinner options again\nRegister slice: lane=conflicts-2 step=all\n\n"+lines+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestALaneWhoseBriefCarriesAnUndeliveredOwnerOrderIsHeldThroughItsOwnEnd(t *testing.T) {
	for name, ending := range map[string]Record{
		"its done": rec(2, lane2, "done", t0, "0 of 4 spinner options delivered", Root),
		"its stop": rec(2, lane2, "stopped", t0, "stopping here", Root),
	} {
		t.Run(name, func(t *testing.T) {
			s := memory(t)
			f := adopted(t, s, briefCarrying(t, "owner:m876 owner:m877"), "")
			f.undelivered = []owner.Row{{ID: "m5"}, {ID: "m876"}, {ID: "m877"}}
			f.feed = append(f.feed, ending)
			f.peers = []Row{who(lane2, true, t0)}

			tick(t, s, f, t0.Add(mins(1)))

			got := row(t, s, lane2)
			equal(t, []any{got.State, got.RetiredBy}, []any{store.LaneHeld, ""})
			defects := f.ofKind("defect")
			equal(t, len(defects), 1)
			equal(t, defects[0].To, []string{Root, lane2})
			contains(t, defects[0].Text, "it was assigned 2 owner orders with no delivered proof: owner:m876, owner:m877")
			contains(t, defects[0].Text, "delivered=owner:<row>")
		})
	}
}

func TestALaneRetiresOnItsDoneWhenNoOrderItsBriefCarriesIsUndelivered(t *testing.T) {
	for name, brief := range map[string]func(*testing.T) string{
		"its orders delivered":  func(t *testing.T) string { return briefCarrying(t, "owner:m876") },
		"a brief with no order": writeBrief,
		"a brief only quoting rows": func(t *testing.T) string {
			return briefSaying(t, "His owner:m5 ruling is background.\n\nOwner orders: owner:m5")
		},
	} {
		t.Run(name, func(t *testing.T) {
			s := memory(t)
			f := adopted(t, s, brief(t), "")
			f.undelivered = []owner.Row{{ID: "m5"}}
			f.feed = append(f.feed, rec(2, lane2, "done", t0, "all four options ran", Root))
			f.peers = []Row{who(lane2, false, t0)}

			tick(t, s, f, t0.Add(mins(1)))

			equal(t, row(t, s, lane2).RetiredBy, "done")
			equal(t, len(f.ofKind("defect")), 0)
		})
	}
}

func TestAnOwnerRecordToARetiredLaneReachesTheLiveEndOfItsRouteInTheSamePass(t *testing.T) {
	words := "run all four options again as new posts"
	for name, tc := range map[string]struct {
		feed []Record
		to   string
	}{
		"retired a pass before":        {[]Record{rec(2, lane2, "done", t0, "spinner pictures posted", Root), rec(3, OwnerRelay, "owner", t0.Add(mins(1)), words, "conflicts")}, Root},
		"retired in the same pass":     {[]Record{rec(2, OwnerRelay, "owner", t0, words, "conflicts"), rec(3, lane2, "done", t0, "spinner pictures posted", Root)}, Root},
		"retired for a live successor": {[]Record{spawned(2, heir, writeBrief(t), t0, nil), rec(3, heir, "state", t0, "taking over", Root), rec(4, OwnerRelay, "owner", t0.Add(mins(1)), words, lane2)}, heir},
	} {
		t.Run(name, func(t *testing.T) {
			s := memory(t)
			f := adopted(t, s, writeBrief(t), "")
			f.peers = []Row{who(lane2, false, t0), who(heir, true, t0)}
			f.feed = append(f.feed, tc.feed[:len(tc.feed)-1]...)
			tick(t, s, f, t0)
			f.posts = nil
			f.feed = append(f.feed, tc.feed[len(tc.feed)-1])

			tick(t, s, f, t0.Add(mins(2)))

			asks := f.ofKind("ask")
			equal(t, len(asks), 1)
			equal(t, asks[0].To, []string{tc.to})
			contains(t, asks[0].Text, "owner-relay's owner record")
			contains(t, asks[0].Text, "after it retired")
			equal(t, row(t, s, lane2).State, store.LaneRetired)
		})
	}
}

func TestOnlyHisWordsToARetiredLaneAreForwarded(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.feed = append(f.feed, rec(2, lane2, "done", t0, "spinner pictures posted", Root))
	f.peers = []Row{who(lane2, false, t0)}
	tick(t, s, f, t0)
	f.posts = nil
	f.feed = append(f.feed, rec(3, "meta-desk-3", "report", t0.Add(mins(1)), "three tooling items noted", lane2))

	tick(t, s, f, t0.Add(mins(2)))

	equal(t, len(f.ofKind("ask")), 0)
}
