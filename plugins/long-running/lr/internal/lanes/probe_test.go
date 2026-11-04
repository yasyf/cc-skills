package lanes

import (
	"os"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const metaDesk = "meta-desk-3"

func unverifiableRow(name string, seen time.Time) Row {
	return Row{Lane: name, SeenAt: seen, Dispatch: "ctx_1", Unverified: "has an Orca agent that reads unverifiable (stale_status)"}
}

func quietOrcaLane(t *testing.T, s *store.Store, brief, handoff string, owned ...int) *fake {
	t.Helper()
	f := adopted(t, s, brief, handoff, owned...)
	registered(t, s, store.Lane{Name: metaDesk, Role: RoleMeta})
	f.feed = append(f.feed, rec(2, lane2, "state", t0.Add(-mins(41)), "watching CI"))
	f.peers = []Row{unverifiableRow(lane2, t0.Add(-mins(41))), who(metaDesk, true, t0)}
	return f
}

func stateRelays(f *fake) []Post {
	var relays []Post
	for _, post := range f.ofKind("go") {
		if strings.Contains(post.Text, "Post your state now") {
			relays = append(relays, post)
		}
	}
	return relays
}

func TestAnUnverifiableQuietLaneThatAnswersItsOneRelayGetsNoBlockerAndNoRelaunch(t *testing.T) {
	s := memory(t)
	f := quietOrcaLane(t, s, writeBrief(t), "", 46001)

	tick(t, s, f, t0)
	tick(t, s, f, t0.Add(mins(1)))

	relays := stateRelays(f)
	equal(t, len(relays), 1)
	equal(t, relays[0].To, []string{orcaDesk})
	contains(t, relays[0].Text, "orca-desk: relay to conflicts-2: This lane has an Orca agent that reads unverifiable (stale_status) and has been quiet for 41 min. Post your state now")

	f.feed = append(f.feed, rec(3, lane2, "state", t0.Add(mins(8)), "alive and slow"))
	out := tick(t, s, f, t0.Add(relayAnswer))

	equal(t, []any{out.Relaunched, out.Held, len(f.launches), len(f.ofKind("blocker")), row(t, s, lane2).State}, []any{[]Relaunch(nil), []string(nil), 0, 0, store.LaneDesired})
}

func TestAnAnsweredLaneThatGoesQuietAgainIsAskedAgainBeforeAnyRelaunch(t *testing.T) {
	s := memory(t)
	f := quietOrcaLane(t, s, writeBrief(t), "", 46001)
	tick(t, s, f, t0)
	f.feed = append(f.feed, rec(3, lane2, "state", t0.Add(mins(2)), "alive and slow"))
	tick(t, s, f, t0.Add(mins(3)))

	out := tick(t, s, f, t0.Add(mins(2)+StaleAfter))

	equal(t, []any{out.Relaunched, len(f.launches), len(stateRelays(f))}, []any{[]Relaunch(nil), 0, 2})
}

func TestAnUnverifiableLaneThatAnswersNoRelayRelaunchesFromItsBriefAndHandoffOnce(t *testing.T) {
	s := memory(t)
	f := quietOrcaLane(t, s, writeBrief(t), "9f3c2e1", 46001)

	tick(t, s, f, t0)
	out := tick(t, s, f, t0.Add(relayAnswer))
	tick(t, s, f, t0.Add(relayAnswer+time.Minute))

	equal(t, out.Relaunched, []Relaunch{{lane2, "conflicts-3", "has an Orca agent that reads unverifiable (stale_status) and has been quiet for 56 min, and posted no record in the 15 min after a relay asked its state"}})
	equal(t, []any{f.launches, row(t, s, lane2).State, row(t, s, "conflicts-3").PRs, len(f.ofKind("blocker"))}, []any{[]string{"conflicts-3"}, store.LaneRetired, []int{46001}, 0})
	contains(t, f.briefs["conflicts-3"], "Your handoff is")
	contains(t, f.briefs["conflicts-3"], "restack the five approved stacks")
}

func TestARelaunchedLaneThatAnswersNoRelayIsHeldWithOneBlockerNamingBothLosses(t *testing.T) {
	s := memory(t)
	f := newFake(t, nil, nil)
	registered(t, s, store.Lane{Name: metaDesk, Role: RoleMeta})
	registered(t, s, store.Lane{Name: "conflicts-3", Role: "lane", PRs: []int{46001}, Predecessor: lane2, Reason: "stopped, quiet for 20 min"})
	f.peers = []Row{unverifiableRow("conflicts-3", t0.Add(-mins(41))), who(metaDesk, true, t0)}

	tick(t, s, f, t0)
	out := tick(t, s, f, t0.Add(relayAnswer))
	tick(t, s, f, t0.Add(relayAnswer+time.Minute))

	equal(t, []any{out.Held, len(f.launches), row(t, s, "conflicts-3").State}, []any{[]string{"conflicts-3"}, 0, store.LaneHeld})
	blockers := f.ofKind("blocker")
	equal(t, len(blockers), 1)
	equal(t, blockers[0].To, []string{metaDesk})
	contains(t, blockers[0].Text, "held, not relaunched: the supervisor already relaunched it once, when conflicts-2 stopped, quiet for 20 min")
	contains(t, blockers[0].Text, "posted no record in the 15 min after a relay asked its state")
}

func TestAnUnansweredLaneWhoseBriefIsGoneIsHeldWithOneBlockerNamingTheBrief(t *testing.T) {
	s, brief := memory(t), writeBrief(t)
	f := quietOrcaLane(t, s, brief, "", 46001)
	tick(t, s, f, t0)
	if err := os.Remove(brief); err != nil {
		t.Fatal(err)
	}

	out := tick(t, s, f, t0.Add(relayAnswer))

	equal(t, []any{out.Held, len(f.launches)}, []any{[]string{lane2}, 0})
	blockers := f.ofKind("blocker")
	equal(t, len(blockers), 1)
	equal(t, blockers[0].To, []string{metaDesk})
	contains(t, blockers[0].Text, "its brief "+brief+" is gone")
}

func TestAnUnansweredLaneHoldingNothingRetiresPastTheThresholdAsAProvenDeadOneDoes(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.feed = append(f.feed, rec(2, lane2, "state", t0, "reading the logs", Root))
	f.peers = []Row{unverifiableRow(lane2, t0)}

	tick(t, s, f, t0.Add(mins(59)))
	equal(t, []any{row(t, s, lane2).State, len(f.posts)}, []any{store.LaneDesired, 0})

	tick(t, s, f, t0.Add(mins(60)))
	equal(t, []any{row(t, s, lane2).State, len(stateRelays(f))}, []any{store.LaneDesired, 1})

	tick(t, s, f, t0.Add(mins(60)+relayAnswer))

	lane := row(t, s, lane2)
	equal(t, []any{lane.State, lane.RetiredBy, len(f.launches), len(f.ofKind("blocker"))}, []any{store.LaneRetired, retiredSilent, 0, 0})
}

func TestALaneOrcaProvesLiveIsNeverAskedItsState(t *testing.T) {
	s := memory(t)
	f := quietOrcaLane(t, s, writeBrief(t), "", 46001)
	f.peers = []Row{{Lane: lane2, Live: true, SeenAt: t0, Dispatch: "ctx_1"}, who(metaDesk, true, t0)}

	out := tick(t, s, f, t0)

	equal(t, []any{len(stateRelays(f)), out.Relaunched, len(f.ofKind("blocker"))}, []any{0, []Relaunch(nil), 0})
}

func TestARelayOutstandingWhenOrcaProvesTheLaneLiveIsForgotten(t *testing.T) {
	s := memory(t)
	f := quietOrcaLane(t, s, writeBrief(t), "", 46001)
	tick(t, s, f, t0)
	quiet := f.peers
	f.peers = []Row{{Lane: lane2, Live: true, SeenAt: t0.Add(mins(1)), Dispatch: "ctx_1"}, who(metaDesk, true, t0)}
	tick(t, s, f, t0.Add(mins(1)))
	f.peers = quiet

	out := tick(t, s, f, t0.Add(relayAnswer+time.Minute))

	equal(t, []any{out.Relaunched, len(f.launches), len(stateRelays(f))}, []any{[]Relaunch(nil), 0, 2})
}

func TestAnUnverifiableLaneWithNoPlacementWhoseSpriteRunsItsWorkerIsLeftAlone(t *testing.T) {
	s := memory(t)
	f := quietOrcaLane(t, s, writeBrief(t), "", 46001)
	f.sprites = map[string]Sprite{lane2: {Name: "sprite-" + lane2, Status: spriteRunning, LastRunning: t0}}
	f.hosts = map[string]Host{lane2: HostRunning}

	first := tick(t, s, f, t0)
	later := tick(t, s, f, t0.Add(relayAnswer+time.Minute))

	equal(t, []any{first.Relaunched, later.Relaunched, len(stateRelays(f)), len(f.ofKind("blocker"))}, []any{[]Relaunch(nil), []Relaunch(nil), 0, 0})
}

func TestAFailedSpritesListNeverAsksOrRelaunchesASpriteLane(t *testing.T) {
	s := memory(t)
	f := onSprite(t, s, 46001)
	registered(t, s, store.Lane{Name: metaDesk, Role: RoleMeta})
	quietFor(f, mins(41), "host_unavailable")
	f.peers = append(f.peers, who(metaDesk, true, t0))
	f.spritesFail = os.ErrDeadlineExceeded

	tick(t, s, f, t0)
	out := tick(t, s, f, t0.Add(relayAnswer))

	equal(t, []any{out.Relaunched, len(f.launches), len(stateRelays(f)), len(f.ofKind("blocker"))}, []any{[]Relaunch(nil), 0, 0, 0})
}

func TestAnUnverifiableCoordinatorIsHeldForTheBridgeNeverRelaunchedOnASilence(t *testing.T) {
	s := memory(t)
	f := coordinator(t, s, writeBrief(t))
	f.peers = []Row{unverifiableRow(Root, t0.Add(-mins(41)))}

	out := tick(t, s, f, t0)

	equal(t, []any{out.Held, len(f.launches), len(stateRelays(f)), f.kinds()}, []any{[]string{Root}, 0, 0, []string{"blocker:" + OwnerRelay}})
}

func TestALaneOnARootOrderWhoseAgentIsUnverifiableIsAskedThenRelaunchedNeverRetired(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "", 46001)
	f.feed = append(f.feed, rec(2, Root, "decide", t0.Add(-mins(25)), "fix the race in your open PR", lane2))
	f.peers = []Row{unverifiableRow(lane2, t0.Add(-mins(20)))}

	tick(t, s, f, t0)
	equal(t, []any{row(t, s, lane2).State, len(stateRelays(f))}, []any{store.LaneDesired, 1})

	tick(t, s, f, t0.Add(relayAnswer))

	equal(t, []any{f.launches, row(t, s, lane2).RetiredBy}, []any{[]string{"conflicts-3"}, "conflicts-3"})
}
