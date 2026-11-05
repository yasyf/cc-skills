package lanes

import (
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const promisedCard = "card-rmw2x0z3q"

func promised(by time.Time) []Promise {
	return []Promise{{Card: promisedCard, Lane: lane2, Question: "Wait for the root-cause lane's report first?", By: by}}
}

func TestALaneOwningACardWithAnUnmetPromiseIsHeldThroughItsOwnEnd(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.promises = append(promised(t0.Add(time.Hour)), Promise{Card: "card-other", Lane: heir, By: t0.Add(time.Hour)})
	f.feed = append(f.feed, rec(2, lane2, "stopped", t0, "stopping here", Root))
	f.peers = []Row{who(lane2, true, t0)}

	tick(t, s, f, t0.Add(mins(1)))

	equal(t, row(t, s, lane2).State, store.LaneHeld)
	defects := f.ofKind("defect")
	equal(t, len(defects), 1)
	contains(t, defects[0].Text, "its cards promise him something by a clock, with no delivered proof: "+promisedCard+".")
	contains(t, defects[0].Text, "delivered=card:<card>")
}

func TestALaneWhoseCardPromisesWereDeliveredRetiresOnItsDone(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.promises = []Promise{{Card: "card-other", Lane: heir, By: t0.Add(time.Hour)}}
	f.feed = append(f.feed, rec(2, lane2, "done", t0, "report delivered", Root))
	f.peers = []Row{who(lane2, false, t0)}

	tick(t, s, f, t0.Add(mins(1)))

	equal(t, row(t, s, lane2).RetiredBy, "done")
	equal(t, len(f.ofKind("defect")), 0)
}

func cardBlockers(f *fake) []Post {
	var told []Post
	for _, post := range f.ofKind("blocker") {
		if post.Attrs["card"] != "" {
			told = append(told, post)
		}
	}
	return told
}

func TestACardClockPassingWithNothingDeliveredTellsItsLaneAndTheCommunicatorOnce(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.peers = []Row{who(lane2, true, t0)}
	f.promises = promised(t0.Add(mins(5)))

	tick(t, s, f, t0.Add(mins(4)))
	equal(t, len(cardBlockers(f)), 0)

	tick(t, s, f, t0.Add(mins(6)))
	tick(t, s, f, t0.Add(mins(7)))

	told := cardBlockers(f)
	equal(t, len(told), 1)
	equal(t, told[0].To, []string{lane2, OwnerRelay})
	equal(t, told[0].Attrs, map[string]string{"card": promisedCard, "by": "2026-10-31T17:05:00Z"})
	contains(t, told[0].Text, `he waits on it to answer "Wait for the root-cause lane's report first?"`)
	contains(t, told[0].Text, "delivered=card:"+promisedCard)
}
