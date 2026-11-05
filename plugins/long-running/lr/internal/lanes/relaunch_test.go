package lanes

import (
	"errors"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func deadWithMetaDesk(t *testing.T, s *store.Store) *fake {
	t.Helper()
	f := adopted(t, s, writeBrief(t), "", 46001)
	registered(t, s, store.Lane{Name: "meta-desk-3", Role: RoleMeta})
	f.peers = []Row{who(lane2, false, t0.Add(-mins(20))), who("meta-desk-3", true, t0)}
	return f
}

func refusing(f *fake, times int) {
	launch := f.deps.Launch
	f.deps.Launch = func(name, model, effort, brief, key string) error {
		if err := launch(name, model, effort, brief, key); err != nil {
			return err
		}
		if len(f.launches) > times {
			return nil
		}
		return errors.New("refused, nothing was posted: brief names what no Sprite can receive")
	}
}

func relaunchReports(f *fake) []Post {
	var out []Post
	for _, p := range f.ofKind("report") {
		if strings.HasPrefix(p.Text, "RELAUNCHED ") {
			out = append(out, p)
		}
	}
	return out
}

func TestARefusedRelaunchRetriesOnceThenHoldsWithOneBlockerToTheMetaDeskAndNeverReportsARelaunch(t *testing.T) {
	s := memory(t)
	f := deadWithMetaDesk(t, s)
	refusing(f, 2)

	out := tick(t, s, f, t0)
	tick(t, s, f, t0.Add(mins(1)))

	equal(t, []any{f.launches, out.Relaunched, out.Held, row(t, s, lane2).State, len(relaunchReports(f))}, []any{[]string{"conflicts-3", "conflicts-3"}, []Relaunch(nil), []string{lane2}, store.LaneHeld, 0})
	blockers := f.ofKind("blocker")
	equal(t, len(blockers), 1)
	equal(t, blockers[0].To, []string{"meta-desk-3"})
	contains(t, blockers[0].Text, "held, not relaunched: its relaunch as conflicts-3 was refused twice, last with refused, nothing was posted: brief names what no Sprite can receive")
}

func TestARelaunchRefusedOnceLaunchesOnItsRetry(t *testing.T) {
	s := memory(t)
	f := deadWithMetaDesk(t, s)
	refusing(f, 1)

	out := tick(t, s, f, t0)

	equal(t, []any{f.launches, out.Relaunched, row(t, s, "conflicts-3").State}, []any{[]string{"conflicts-3", "conflicts-3"}, []Relaunch{{lane2, "conflicts-3", "stopped, quiet for 20 min"}}, store.LaneLaunching})
	equal(t, []any{len(relaunchReports(f)), len(f.ofKind("blocker"))}, []any{1, 0})
}

func TestARelaunchTheRunnerRefusesRetriesOnceThenHoldsWithOneBlockerToTheMetaDesk(t *testing.T) {
	s := memory(t)
	f := deadWithMetaDesk(t, s)
	tick(t, s, f, t0)
	duplicate := func(seq int64, at time.Time) Record {
		return rec(seq, runner, "blocker", at, "LAUNCH-DUPLICATE R9 conflicts-3: fork-share-grant-2: /w/fork, 1 ahead of trunk already holds this work; nothing was launched", "meta-desk-3")
	}

	f.feed = append(f.feed, duplicate(10, t0.Add(mins(1))))
	retried := tick(t, s, f, t0.Add(mins(1)))

	equal(t, []any{f.launches, retried.Held, row(t, s, "conflicts-3").State, len(f.ofKind("blocker"))}, []any{[]string{"conflicts-3", "conflicts-3"}, []string(nil), store.LaneLaunching, 0})

	f.feed = append(f.feed, duplicate(11, t0.Add(mins(2))))
	held := tick(t, s, f, t0.Add(mins(2)))
	tick(t, s, f, t0.Add(mins(3)))

	equal(t, []any{f.launches, held.Held, row(t, s, "conflicts-3").State}, []any{[]string{"conflicts-3", "conflicts-3"}, []string{"conflicts-3"}, store.LaneHeld})
	blockers := f.ofKind("blocker")
	equal(t, len(blockers), 1)
	equal(t, blockers[0].To, []string{"meta-desk-3"})
	contains(t, blockers[0].Text, "the runner refused the supervisor's relaunch as conflicts-3 twice, last with LAUNCH-DUPLICATE: fork-share-grant-2")
}

func hostGone(f *fake, gone bool) {
	quietFor(f, mins(25), "host_unavailable")
	f.peers[0].HostGone = gone
	f.hosts = map[string]Host{lane2: HostUnreachable}
}

func TestASpriteOrcaCannotReachIsLeftAloneWhileItsWorkerRunsAndRelaunchedWhenItStops(t *testing.T) {
	s := memory(t)
	f := onSprite(t, s, 46001)
	hostGone(f, true)
	f.hosts = map[string]Host{lane2: HostRunning}

	tick(t, s, f, t0)
	out := tick(t, s, f, t0.Add(hostGoneAfter+mins(30)))
	equal(t, []any{out.Relaunched, len(f.launches), row(t, s, lane2).State}, []any{[]Relaunch(nil), 0, store.LaneDesired})

	f.hosts = map[string]Host{lane2: HostEmpty}
	stopped := tick(t, s, f, t0.Add(hostGoneAfter+mins(31)))

	equal(t, len(stopped.Relaunched), 1)
}

func TestASpriteLaneWhoseHostOrcaReportsUnavailableForTenMinutesIsRelaunchedOnceFromWhatTheBusHolds(t *testing.T) {
	s := memory(t)
	f := onSprite(t, s, 46001)
	registered(t, s, store.Lane{Name: "meta-desk-3", Role: RoleMeta})
	hostGone(f, true)
	f.peers = append(f.peers, who("meta-desk-3", true, t0))

	tick(t, s, f, t0)
	early := tick(t, s, f, t0.Add(hostGoneAfter-time.Minute))
	equal(t, []any{early.Relaunched, len(f.launches)}, []any{[]Relaunch(nil), 0})

	out := tick(t, s, f, t0.Add(hostGoneAfter))

	why := "has a Sprite host Orca has reported unavailable for 10 min"
	equal(t, out.Relaunched, []Relaunch{{lane2, "conflicts-3", why}})
	reports := lostReports(f)
	equal(t, len(reports), 1)
	equal(t, slices.Contains(reports[0].To, "meta-desk-3"), true)
	brief := f.briefs["conflicts-3"]
	contains(t, brief, "read conflicts-2's records with `cci tail --drive "+testDrive+" --lane conflicts-2 --oldest`")
	contains(t, brief, "conflicts-2's worktree and transcript stayed on its Sprite")
	contains(t, brief, "run `git fetch origin refs/lr/work/conflicts-2:refs/lr/work/conflicts-2`")
	for _, gone := range []string{testCheckout, "lane-status", "lsof", "handoff-subagent-brief"} {
		if strings.Contains(brief, gone) {
			t.Fatalf("a Sprite successor's brief names %q:\n%s", gone, brief)
		}
	}
}

func TestASpriteLaneThatRecoversIsNeverRelaunchedAndOneThatRecoversAfterItsRelaunchDoesNotDoubleUp(t *testing.T) {
	s := memory(t)
	f := onSprite(t, s, 46001)
	registered(t, s, store.Lane{Name: "meta-desk-3", Role: RoleMeta})
	meta := who("meta-desk-3", true, t0)
	at := func(minutes int, gone bool) Outcome {
		hostGone(f, gone)
		f.peers = append(f.peers, meta)
		return tick(t, s, f, t0.Add(mins(minutes)))
	}

	at(0, true)
	at(8, false)
	at(9, true)
	at(18, true)
	equal(t, len(f.launches), 0)

	at(19, true)
	equal(t, f.launches, []string{"conflicts-3"})

	f.peers = []Row{who(lane2, true, t0.Add(mins(20))), meta}
	tick(t, s, f, t0.Add(mins(20)))
	tick(t, s, f, t0.Add(mins(21)))
	equal(t, []any{f.launches, row(t, s, lane2).State}, []any{[]string{"conflicts-3"}, store.LaneRetired})
}

func TestAQuietSpriteLaneOrcaReportsLiveIsNeverRelaunchedForPostingNoRecord(t *testing.T) {
	s := memory(t)
	registered(t, s, store.Lane{Name: "meta-desk-3", Role: RoleMeta})
	spawn := spawned(1, lane2, writeBrief(t), t0.Add(-time.Hour), map[string]string{"model": "opus", "effort": "xhigh", "placement": placedOnSprite})
	spawn.Lane = "meta-desk-3"
	live := []Row{{Lane: lane2, Live: true, SeenAt: t0, Dispatch: "ctx_1"}, who("meta-desk-3", true, t0)}
	f := newFake(t, []Record{spawn}, live)
	tick(t, s, f, t0.Add(-time.Hour))

	tick(t, s, f, t0)
	equal(t, len(f.launches), 0)

	f.peers = []Row{{Lane: lane2, SeenAt: t0.Add(-time.Hour), Dispatch: "ctx_1", Unverified: "has an Orca agent that reads unverifiable (stale_status)"}, who("meta-desk-3", true, t0)}
	out := tick(t, s, f, t0.Add(mins(1)))

	equal(t, out.Relaunched, []Relaunch{{lane2, "conflicts-3", "has posted no record in the 61 min since its launch, and Orca does not report it live"}})
}

func TestAnOverdueItemsAskToAParkedLaneIsRelayedThroughTheDesk(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.feed = append(f.feed, rec(2, Self, "lr.ask", t0, "org/repo#1 restack overdue", lane2))
	f.peers = []Row{{Lane: lane2, Live: true, SeenAt: t0.Add(mins(11)), Dispatch: "ctx_1"}}

	tick(t, s, f, t0.Add(mins(11)))

	relays := f.ofKind("go")
	if len(relays) != 1 || !strings.HasPrefix(relays[0].Text, "orca-desk: relay to "+lane2+": Orders addressed to you are unread: supervisor's lr.ask #2") {
		t.Fatalf("relays = %+v", relays)
	}
}

func TestAFailedGradeIsAnOrderToItsLaneAndAPassIsNot(t *testing.T) {
	for text, relays := range map[string]int{
		"finder: major 187ae77 2 open over delta 035bd9e..187ae77": 1,
		"fidelity: missing 187ae77 the plan row":                   1,
		"finder: ok 187ae77 no majors":                             0,
		"fidelity: ok 187ae77 class owner-pick":                    0,
	} {
		t.Run(text, func(t *testing.T) {
			s := memory(t)
			f := adopted(t, s, writeBrief(t), "")
			f.feed = append(f.feed, rec(2, "finder-desk", "review", t0, text, lane2))
			f.peers = []Row{{Lane: lane2, Live: true, SeenAt: t0.Add(mins(11)), Dispatch: "ctx_1"}}

			tick(t, s, f, t0.Add(mins(11)))

			equal(t, len(f.ofKind("go")), relays)
		})
	}
}

func TestUnreadOrdersAreRelayedThroughTheDeskFromAMacLaneAndASpriteLaneAlike(t *testing.T) {
	for name, setup := range map[string]func(*testing.T, *store.Store) *fake{
		"a Sprite lane": func(t *testing.T, s *store.Store) *fake { return onSprite(t, s) },
		"a Mac lane":    func(t *testing.T, s *store.Store) *fake { return adopted(t, s, writeBrief(t), "") },
	} {
		t.Run(name, func(t *testing.T) {
			s := memory(t)
			f := setup(t, s)
			f.feed = append(f.feed, rec(2, Root, "ask", t0, "first", lane2))
			f.peers = []Row{{Lane: lane2, Live: true, SeenAt: t0.Add(mins(11)), Dispatch: "ctx_1"}}

			tick(t, s, f, t0.Add(mins(11)))

			sent := relays(f)
			equal(t, len(sent), 1)
			contains(t, sent[0].Text, "orca-desk: relay to "+lane2+": Orders addressed to you are unread: root's ask #2, the oldest for 11 min")
		})
	}
}

func stoodDownFor(t *testing.T, s *store.Store, brief string) *fake {
	t.Helper()
	f := adopted(t, s, brief, "9f3c2e1", 46001)
	f.feed = append(f.feed, ask(2, "stand-down", lane2, t0, nil, "", map[string]string{"successor": "conflicts-3", "last-read": "1"}))
	f.peers = nil
	return f
}

func runnerLines(f *fake) []string {
	return slices.Compact(slices.Sorted(slices.Values(f.keys)))
}

func TestAStandDownNamingASuccessorLaunchesItOnceFromTheBriefItsBlockerNames(t *testing.T) {
	s := memory(t)
	f := stoodDownFor(t, s, writeBrief(t))

	tick(t, s, f, t0)
	tick(t, s, f, t0.Add(mins(1)))

	equal(t, []any{f.launches, f.keys, row(t, s, "conflicts-3").State}, []any{[]string{"conflicts-3"}, []string{"supervisor-launch:conflicts-3"}, store.LaneLaunching})
	contains(t, f.briefs["conflicts-3"], "Replaces: conflicts-2")
	contains(t, f.briefs["conflicts-3"], "because conflicts-2 was stood down for it by record #2")
}

func TestASuccessorOwedWhenTheSupervisorRestartsBeforeItsLaunchLaunchesOnceAfterIt(t *testing.T) {
	s := memory(t)
	f := stoodDownFor(t, s, writeBrief(t))
	launch := f.deps.Launch
	f.deps.Launch = func(string, string, string, string, string) error {
		do(errors.New("the supervisor re-executed"))
		return nil
	}
	if _, err := Tick(s, f.deps, t0, 1); err == nil {
		t.Fatal("a tick whose launch died returned no error")
	}
	f.deps.Launch = launch

	tick(t, s, f, t0.Add(mins(1)))
	tick(t, s, f, t0.Add(mins(2)))

	equal(t, []any{f.launches, runnerLines(f)}, []any{[]string{"conflicts-3"}, []string{"supervisor-launch:conflicts-3"}})
}

func TestASuccessorWhoseLaunchPostedJustBeforeARestartReachesTheRunnerOnce(t *testing.T) {
	s := memory(t)
	f := stoodDownFor(t, s, writeBrief(t))
	launch := f.deps.Launch
	f.deps.Launch = func(name, model, effort, brief, key string) error {
		do(launch(name, model, effort, brief, key))
		do(errors.New("the supervisor re-executed"))
		return nil
	}
	if _, err := Tick(s, f.deps, t0, 1); err == nil {
		t.Fatal("a tick whose launch died returned no error")
	}
	f.deps.Launch = launch

	tick(t, s, f, t0.Add(mins(1)))
	tick(t, s, f, t0.Add(mins(2)))

	equal(t, []any{f.launches, runnerLines(f)}, []any{[]string{"conflicts-3", "conflicts-3"}, []string{"supervisor-launch:conflicts-3"}})
}

func TestASuccessorAlreadyListedIsNeverLaunchedAgain(t *testing.T) {
	s := memory(t)
	f := stoodDownFor(t, s, writeBrief(t))
	f.peers = []Row{who("conflicts-3", true, t0)}

	tick(t, s, f, t0)
	tick(t, s, f, t0.Add(mins(1)))

	equal(t, len(f.launches), 0)
}

func TestAStandingDesksRefusedSuccessorReachesRootByNameInTheSameTick(t *testing.T) {
	s := memory(t)
	brief := filepath.Join(t.TempDir(), "brief.md")
	do(os.WriteFile(brief, []byte("# conflicts-2: restack desk\nStanding: restack every approved stack\n"), 0o600))
	f := stoodDownFor(t, s, brief)
	refusing(f, 2)

	out := tick(t, s, f, t0)

	equal(t, []any{f.launches, out.Held, row(t, s, "conflicts-3").State}, []any{[]string{"conflicts-3", "conflicts-3"}, []string{"conflicts-3"}, store.LaneHeld})
	blockers := f.ofKind("blocker")
	equal(t, len(blockers), 1)
	equal(t, blockers[0].To, []string{Root})
	contains(t, blockers[0].Text, "lane:conflicts-3: held, not launched: conflicts-3 is standing in for conflicts-2")
}

func TestASuccessorOfADeskThatNeedsTheMacKeepsItsNeedSoItNeverLandsOnASprite(t *testing.T) {
	s := memory(t)
	brief := filepath.Join(t.TempDir(), "brief.md")
	do(os.WriteFile(brief, []byte("# conflicts-2: restack desk\nccx: role=desk place=local why=cc-present incident=none\n"), 0o600))
	f := stoodDownFor(t, s, brief)

	tick(t, s, f, t0)

	successor := f.briefs["conflicts-3"]
	contains(t, successor, "Replaces: conflicts-2\nccx: role=desk place=local why=cc-present incident=none\n")
	if strings.Contains(successor, "predecessor-worked-here") {
		t.Fatalf("a Mac desk's successor brief turned its need into a preference:\n%s", successor)
	}
}

func TestASuccessorOfALaneWhoseWhyNamesNoMacNeedOnlyPrefersTheMac(t *testing.T) {
	s := memory(t)
	brief := filepath.Join(t.TempDir(), "brief.md")
	do(os.WriteFile(brief, []byte("# conflicts-2: restack desk\nccx: role=build incident=none place=local why=mac-inputs\n"), 0o600))
	f := stoodDownFor(t, s, brief)

	tick(t, s, f, t0)

	contains(t, f.briefs["conflicts-3"], "Replaces: conflicts-2\nccx: place=local why=predecessor-worked-here\n")
}

func TestAStandDownNamingItsSuccessorByStemPointsAtTheStemsLiveLaneAndLaunchesNothing(t *testing.T) {
	s, brief := memory(t), writeBrief(t)
	f := adopted(t, s, brief, "", 46001)
	f.feed = append(f.feed, spawned(2, "restack-1", brief, t0, nil), ask(3, "stand-down", lane2, t0, nil, "", map[string]string{"successor": "restack", "last-read": "1"}))
	f.peers = []Row{who("restack-1", true, t0)}

	tick(t, s, f, t0)
	tick(t, s, f, t0.Add(mins(16)))

	equal(t, []any{len(f.launches), row(t, s, lane2).RetiredBy, len(f.ofKind("blocker"))}, []any{0, "restack-1", 0})
	if _, found := laneOf(s, "restack"); found {
		t.Fatal("a stand-down by stem registered a second row for a lane already live")
	}
}

func TestAStemSuccessorWhoseLaneOnlyCCIListsIsStartedWithNoLaunchAndNoAlarm(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "", 46001)
	f.feed = append(f.feed, ask(2, "stand-down", lane2, t0, nil, "", map[string]string{"successor": "restack", "last-read": "1"}))
	f.peers = []Row{who("restack-1", true, t0)}

	tick(t, s, f, t0)
	f.peers = []Row{who("restack-1", true, t0.Add(mins(16)))}
	tick(t, s, f, t0.Add(mins(16)))

	equal(t, []any{len(f.launches), row(t, s, "restack").State, row(t, s, "restack").RetiredBy, len(f.ofKind("blocker"))}, []any{0, store.LaneRetired, "restack-1", 0})
}
