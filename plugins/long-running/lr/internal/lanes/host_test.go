package lanes

import (
	"context"
	"errors"
	"fmt"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func onSprite(t *testing.T, s *store.Store, owned ...int) *fake {
	t.Helper()
	f := newFake(t, []Record{spawned(1, lane2, writeBrief(t), t0.Add(-2*time.Hour), map[string]string{"model": "opus", "effort": "xhigh", "placement": placedOnSprite})}, []Row{who(lane2, true, t0.Add(-2*time.Hour))})
	tick(t, s, f, t0.Add(-2*time.Hour))
	if err := s.SetLanePRs(lane2, owned); err != nil {
		t.Fatal(err)
	}
	f.posts = nil
	return f
}

func quietFor(f *fake, quiet time.Duration, reason string) {
	f.feed = append(f.feed, rec(2, lane2, "state", t0.Add(-quiet), "watching CI"))
	f.peers = []Row{{Lane: lane2, SeenAt: t0.Add(-quiet), Dispatch: "ctx_1", Unverified: "has an Orca agent that reads unverifiable (" + reason + ")"}}
}

func TestADownSpriteWithAPRWaitsOutTheBoundThenRelaunchesWithNoBlockerToRoot(t *testing.T) {
	s := memory(t)
	f := onSprite(t, s, 46001)
	quietFor(f, mins(25), "host_unavailable")
	f.hosts = map[string]Host{lane2: HostUnreachable}

	early := tick(t, s, f, t0)
	late := tick(t, s, f, t0.Add(goneAfter-time.Minute))

	equal(t, []any{early.Held, late.Held, len(f.launches), len(f.posts), row(t, s, lane2).State}, []any{[]string(nil), []string(nil), 0, 0, store.LaneDesired})

	gone := tick(t, s, f, t0.Add(goneAfter))

	equal(t, gone.Relaunched, []Relaunch{{lane2, "conflicts-3", "has a Sprite that has not answered for 30 min"}})
	equal(t, f.launches, []string{"conflicts-3"})
	equal(t, row(t, s, "conflicts-3").PRs, []int{46001})
	equal(t, len(f.ofKind("blocker")), 0)
}

func TestAnUpSpriteWithNoWorkerRetiresALaneHoldingNothingWithNoBlocker(t *testing.T) {
	s := memory(t)
	f := onSprite(t, s)
	quietFor(f, mins(61), "stale_status")
	f.hosts = map[string]Host{lane2: HostEmpty}

	out := tick(t, s, f, t0)

	lane := row(t, s, lane2)
	equal(t, []any{lane.State, lane.RetiredBy, len(out.Held), len(f.launches), len(f.ofKind("blocker"))}, []any{store.LaneRetired, retiredSilent, 0, 0, 0})
	contains(t, f.ofKind("report")[0].Text, "conflicts-2 (it has a Sprite that answers with no claude or codex running)")
}

func TestAQuietLaneWhoseSpriteRunsItsWorkerIsNotHeldAndItsSpriteIsLeftToSleep(t *testing.T) {
	s := memory(t)
	f := onSprite(t, s, 46001)
	quietFor(f, mins(41), "host_unavailable")
	f.hosts = map[string]Host{lane2: HostRunning}

	for _, at := range []time.Duration{0, mins(5), reprobeAfter - time.Minute} {
		out := tick(t, s, f, t0.Add(at))
		equal(t, []any{out.Held, out.Relaunched}, []any{[]string(nil), []Relaunch(nil)})
	}

	equal(t, []any{f.hostReads, len(f.posts), row(t, s, lane2).State}, []any{1, 0, store.LaneDesired})
	tick(t, s, f, t0.Add(reprobeAfter))
	equal(t, f.hostReads, 2)
}

func TestAQuietLaneNoProbeSettlesIsHeldWithItsBlockerToTheMetaDeskNotRoot(t *testing.T) {
	for name, setup := range map[string]func(*testing.T, *store.Store) *fake{
		"a lane on no Sprite": func(t *testing.T, s *store.Store) *fake { return adopted(t, s, writeBrief(t), "", 46001) },
		"a Sprite whose probe never answered": func(t *testing.T, s *store.Store) *fake {
			f := onSprite(t, s, 46001)
			f.hosts = map[string]Host{}
			return f
		},
	} {
		t.Run(name, func(t *testing.T) {
			s := memory(t)
			f := setup(t, s)
			registered(t, s, store.Lane{Name: "meta-desk-3", Role: RoleMeta})
			quietFor(f, mins(41), "host_unavailable")
			f.peers = append(f.peers, who("meta-desk-3", true, t0))

			out := tick(t, s, f, t0)

			equal(t, []any{out.Held, row(t, s, lane2).State}, []any{[]string{lane2}, store.LaneHeld})
			blockers := f.ofKind("blocker")
			equal(t, len(blockers), 1)
			equal(t, blockers[0].To, []string{"meta-desk-3"})
			contains(t, blockers[0].Text, "and it runs on no Sprite whose probe answered")
		})
	}
}

func TestOneTickReadsTheSpritesListOnceAndProbesInOneCallAtOneLaneAndAtAThousand(t *testing.T) {
	reads := func(n int) []int {
		s := memory(t)
		f := adopted(t, s, writeBrief(t), "")
		f.peers = []Row{who(lane2, true, t0)}
		f.hosts = map[string]Host{}
		f.sprites = map[string]Sprite{}
		for i := range n {
			name := fmt.Sprintf("parked-%d", i)
			registered(t, s, store.Lane{Name: name, Role: "lane", Placement: placedOnSprite, PRs: []int{50000 + i}})
			f.peers = append(f.peers, Row{Lane: name, SeenAt: t0.Add(-time.Hour), Dispatch: fmt.Sprintf("ctx_%d", i), Unverified: "has an Orca agent that reads unverifiable (host_unavailable)"})
			f.hosts[name] = HostRunning
			f.sprites[name] = Sprite{Name: name, Status: spriteRunning, LastRunning: t0}
		}
		out := tick(t, s, f, t0)
		equal(t, len(out.Held), 0)
		return []int{f.spriteReads, f.hostReads}
	}
	equal(t, reads(1000), reads(1))
	equal(t, reads(1), []int{1, 1})
}

func lostOnSprite(t *testing.T, s *store.Store, f *fake, name string, ran time.Time) {
	t.Helper()
	registered(t, s, store.Lane{Name: "meta-desk-3", Role: RoleMeta})
	f.peers = append(f.peers, who("meta-desk-3", true, t0), Row{Lane: name, SeenAt: t0.Add(-mins(20)), Dispatch: "ctx_1", Unverified: "has an Orca agent that reads unverifiable (sprite_runtime)"})
	f.sprites = map[string]Sprite{name: {Name: "sprite-" + name, Status: "cold", LastRunning: ran}}
}

func lostReports(f *fake) []Post {
	var out []Post
	for _, p := range f.ofKind("report") {
		if strings.HasPrefix(p.Text, "LANE-LOST ") {
			out = append(out, p)
		}
	}
	return out
}

func TestALaneHeardNeverWhoseSpriteWentColdIsReportedLostAndRelaunchedFromItsBriefOnce(t *testing.T) {
	s := memory(t)
	registered(t, s, store.Lane{Name: "build-desk", Role: "lane"})
	spawn := spawned(1, lane2, writeBrief(t), t0.Add(-mins(30)), map[string]string{"model": "opus", "effort": "xhigh", "placement": placedOnSprite})
	spawn.Lane = "build-desk"
	f := newFake(t, []Record{spawn}, []Row{who(lane2, true, t0.Add(-mins(30))), who("build-desk", true, t0.Add(-mins(30)))})
	tick(t, s, f, t0.Add(-mins(30)))
	f.peers = []Row{who("build-desk", true, t0)}
	ran := t0.Add(-mins(4))
	lostOnSprite(t, s, f, lane2, ran)
	f.posts = nil
	lines, err := RegistryLines(s, []string{lane2}, t0)
	if err != nil {
		t.Fatal(err)
	}
	contains(t, lines[0], "heard never")

	early := tick(t, s, f, t0)

	equal(t, []any{early.Relaunched, early.Held, len(f.launches), len(lostReports(f)), row(t, s, lane2).State}, []any{[]Relaunch(nil), []string(nil), 0, 0, store.LaneDesired})

	out := tick(t, s, f, t0.Add(mins(1)))
	tick(t, s, f, t0.Add(mins(2)))

	why := "has a Sprite sprite-conflicts-2 that is cold and last ran at " + pacific(ran)
	equal(t, out.Relaunched, []Relaunch{{lane2, "conflicts-3", why}})
	equal(t, []any{f.launches, f.hostReads, row(t, s, lane2).State}, []any{[]string{"conflicts-3"}, 0, store.LaneRetired})
	brief := strings.Split(f.briefs["conflicts-3"], "\n")
	contains(t, brief[0], "Partial work: conflicts-2 was lost mid-task, since it "+why)
	equal(t, brief[1], "Replaces: conflicts-2")
	reports := lostReports(f)
	equal(t, len(reports), 1)
	equal(t, reports[0].To, []string{"build-desk", "meta-desk-3"})
	equal(t, reports[0].Text, "LANE-LOST conflicts-2: it "+why+", and its dispatch never settled; relaunched as conflicts-3 from its brief.")
}

func TestASecondLossOfARelaunchedLaneIsReportedAndHeldNeverRelaunchedAgain(t *testing.T) {
	s := memory(t)
	f := newFake(t, nil, nil)
	registered(t, s, store.Lane{Name: "conflicts-3", Role: "lane", Placement: placedOnSprite, Predecessor: lane2, Reason: "has a Sprite sprite-conflicts-2 that is cold and last ran at 1:40 PM"})
	lostOnSprite(t, s, f, "conflicts-3", t0.Add(-mins(10)))

	first := tick(t, s, f, t0)
	tick(t, s, f, t0.Add(mins(1)))

	equal(t, []any{first.Held, len(f.launches), row(t, s, "conflicts-3").State}, []any{[]string{"conflicts-3"}, 0, store.LaneHeld})
	reports := lostReports(f)
	equal(t, len(reports), 1)
	equal(t, reports[0].To, []string{"meta-desk-3"})
	contains(t, reports[0].Text, "held, not relaunched: the supervisor already relaunched it once, when conflicts-2 has a Sprite sprite-conflicts-2")
}

func TestAColdSpriteTheAPIGivesNoLastRunTimeIsReportedWithoutATime(t *testing.T) {
	s := memory(t)
	f := newFake(t, nil, nil)
	registered(t, s, store.Lane{Name: "conflicts-3", Role: "lane", Placement: placedOnSprite, Predecessor: lane2, Reason: "was lost"})
	lostOnSprite(t, s, f, "conflicts-3", time.Time{})

	tick(t, s, f, t0)

	reports := lostReports(f)
	equal(t, len(reports), 1)
	contains(t, reports[0].Text, "LANE-LOST conflicts-3: it has a Sprite sprite-conflicts-3 that is cold, and its dispatch never settled")
}

func TestASettledLaneOrAFailedSpritesListLeavesAColdSpriteLaneAlone(t *testing.T) {
	for name, setup := range map[string]func(*fake){
		"a settled lane": func(f *fake) {
			f.feed = append(f.feed, rec(2, lane2, "done", t0.Add(-mins(20)), "shipped", Root))
		},
		"a failed list read": func(f *fake) { f.spritesFail = errors.New("sprite api /sprites: 502") },
	} {
		t.Run(name, func(t *testing.T) {
			s := memory(t)
			f := onSprite(t, s)
			f.peers = nil
			lostOnSprite(t, s, f, lane2, t0.Add(-mins(30)))
			setup(f)

			out := tick(t, s, f, t0)

			equal(t, []any{out.Relaunched, len(f.launches), len(lostReports(f))}, []any{[]Relaunch(nil), 0, 0})
		})
	}
}

func TestASpriteReadsByTheSpritesListAndItsWorkerProbeAnswer(t *testing.T) {
	env := Env{Config: config.Config{OrcaRun: "run_1"}, Local: t.TempDir()}
	for lane, box := range map[string]string{"parked": "dar-parked", "ended": "dar-ended", "hung": "dar-hung", "odd": "dar-odd", "down": "dar-down"} {
		writeJSON(t, filepath.Join(env.receipts(), lane+".json"), map[string]any{"result": map[string]any{"server": map[string]string{"name": box}}})
	}
	answers := map[string]func([]string) (shell.Result, error){
		"dar-parked": func([]string) (shell.Result, error) { return shell.Result{Stdout: "running\n"}, nil },
		"dar-ended":  func([]string) (shell.Result, error) { return shell.Result{Stdout: "none\n"}, nil },
		"dar-hung": func([]string) (shell.Result, error) {
			return shell.Result{}, fmt.Errorf("sprite exec: %w", context.DeadlineExceeded)
		},
		"dar-odd": func([]string) (shell.Result, error) { return shell.Result{Stdout: "Waking sprite\nrunning\n"}, nil },
		"dar-down": func(argv []string) (shell.Result, error) {
			return shell.Result{Code: 1}, &shell.ExitError{Argv: argv, Code: 1, Stderr: "connection refused"}
		},
	}
	pages := []string{
		`{"sprites":[{"name":"dar-parked","status":"running"},{"name":"dar-ended","status":"running"},{"name":"dar-hung","status":"running"},{"name":"warm-pool-1","status":"warm"}],"has_more":true,"next_continuation_token":"page 2"}`,
		`{"sprites":[{"name":"dar-odd","status":"running"},{"name":"dar-down","status":"cold","last_running_at":"2026-11-03T20:58:00Z"}],"has_more":false}`,
	}
	var listed []string
	env.Runner = scripted(func(argv []string) (shell.Result, error) {
		if argv[1] == "api" {
			listed = append(listed, argv[2])
			return shell.Result{Stdout: pages[len(listed)-1]}, nil
		}
		return answers[argv[3]](argv)
	})
	lanes := []string{"parked", "ended", "hung", "odd", "down", "deleted"}

	sprites, err := env.laneSprites(context.Background(), lanes)
	if err != nil {
		t.Fatal(err)
	}

	equal(t, listed, []string{"/sprites", "/sprites?continuation_token=page+2"})
	equal(t, []any{len(sprites), sprites["down"]}, []any{5, Sprite{Name: "dar-down", Status: "cold", LastRunning: time.Date(2026, 11, 3, 20, 58, 0, 0, time.UTC)}})
	equal(t, env.hosts(context.Background(), lanes, sprites), map[string]Host{
		"parked": HostRunning, "ended": HostEmpty, "hung": HostUnreachable, "down": HostUnreachable, "deleted": HostUnreachable,
	})

	env.Runner = scripted(func([]string) (shell.Result, error) {
		return shell.Result{}, errors.New("sprite: not logged in")
	})
	if _, err := env.laneSprites(context.Background(), []string{"parked"}); err == nil {
		t.Fatal("a failed Sprites list read returned no error")
	}
}

func spriteDesk(t *testing.T, s *store.Store, host Host) *fake {
	t.Helper()
	f := newFake(t, []Record{spawned(1, "go-desk-1", standingBrief(t, "go-desk-1"), t0.Add(-2*time.Hour), map[string]string{"placement": placedOnSprite}), rec(2, "go-desk-1", "report", t0.Add(-time.Hour), "swept")}, []Row{who("go-desk-1", true, t0.Add(-time.Hour))})
	registered(t, s, store.Lane{Name: "meta-desk-3", Role: RoleMeta})
	tick(t, s, f, t0.Add(-time.Hour))
	f.posts = nil
	f.peers = []Row{who("meta-desk-3", true, t0)}
	f.sprites = map[string]Sprite{"go-desk-1": {Name: "sprite-go-desk-1", Status: spriteRunning, LastRunning: t0}}
	f.hosts = map[string]Host{"go-desk-1": host}
	return f
}

func TestAQuietStandingDeskWhoseSpriteRunsItsWorkerRaisesNothingAndIsNeverReplaced(t *testing.T) {
	s := memory(t)
	f := spriteDesk(t, s, HostRunning)

	tick(t, s, f, t0)
	tick(t, s, f, t0.Add(mins(40)))

	equal(t, []any{len(f.launches), len(f.ofKind("blocker")), row(t, s, "go-desk-1").State}, []any{0, 0, store.LaneDesired})
}

func TestAStandingDeskWhoseSpriteHasNoWorkerRelaunchesAndLogsOneRecordToTheMetaDesk(t *testing.T) {
	s := memory(t)
	f := spriteDesk(t, s, HostEmpty)

	out := tick(t, s, f, t0)

	equal(t, []any{f.launches, len(out.Relaunched), len(f.ofKind("blocker"))}, []any{[]string{"go-desk-2"}, 1, 0})
	equal(t, relaunchReports(f)[0].To, []string{"meta-desk-3"})
}

func TestAStandingDeskGoneTenMinutesRelaunchesWithoutWaitingOutTheFifteenAPlainLaneGets(t *testing.T) {
	s := memory(t)
	f := spriteDesk(t, s, HostEmpty)
	f.feed = append(f.feed, rec(3, "go-desk-1", "report", t0.Add(-mins(11)), "swept"))

	tick(t, s, f, t0)

	equal(t, f.launches, []string{"go-desk-2"})
}

func TestAStandingDeskRelaunchedAndDeadThreeTimesInAnHourStopsWithOneBlockerNamingTheCause(t *testing.T) {
	s := memory(t)
	f := spriteDesk(t, s, HostEmpty)
	at := t0
	tick(t, s, f, at)
	var out Outcome
	for n := 2; n <= 4; n++ {
		name := fmt.Sprintf("go-desk-%d", n)
		at = at.Add(mins(1))
		f.feed = append(f.feed, rec(int64(10+n), name, "report", at, "up"))
		f.peers = []Row{who("meta-desk-3", true, at), who(name, true, at)}
		tick(t, s, f, at)
		at = at.Add(StaleAfter + mins(1))
		f.peers = []Row{who("meta-desk-3", true, at)}
		f.sprites[name] = Sprite{Name: "sprite-" + name, Status: spriteRunning, LastRunning: at}
		f.hosts[name] = HostEmpty
		out = tick(t, s, f, at)
	}
	tick(t, s, f, at.Add(mins(30)))

	equal(t, []any{f.launches, out.Held, row(t, s, "go-desk-4").State}, []any{[]string{"go-desk-2", "go-desk-3", "go-desk-4"}, []string{"go-desk-4"}, store.LaneHeld})
	blockers := f.ofKind("blocker")
	equal(t, len(blockers), 1)
	equal(t, blockers[0].To, []string{"meta-desk-3"})
	contains(t, blockers[0].Text, "died 3 times within 60 min")
}

func TestAStandingLaneRetiredByItsFinishedWorkerRelaunchesAfterTenMinutesAndItsSuccessorsDoneEndsIt(t *testing.T) {
	s := memory(t)
	f, _ := standingLane(t, s)
	f.feed = append(f.feed, ask(2, "done", refresher, t0, nil, runner, nil))
	f.peers = nil

	tick(t, s, f, t0.Add(mins(1)))
	equal(t, len(f.launches), 0)
	tick(t, s, f, t0.Add(mins(12)))
	equal(t, f.launches, []string{"board-refresher-7"})

	f.feed = append(f.feed, ask(3, "done", "board-refresher-7", t0.Add(mins(13)), nil, runner, nil))
	tick(t, s, f, t0.Add(mins(13)))
	tick(t, s, f, t0.Add(mins(30)))

	equal(t, f.launches, []string{"board-refresher-7"})
	contains(t, f.ofKind("blocker")[0].Text, "read its duty as finished")
}

func TestARetiredLaneWhoseBriefHasNoStandingLineIsNotADutyEvenAfterAStandingPredecessor(t *testing.T) {
	s := memory(t)
	f, _ := standingLane(t, s)
	registered(t, s, store.Lane{Name: Root, Brief: writeBrief(t), State: store.LaneRetired, RetiredBy: "done"})
	do(s.SetLaneState(refresher, store.LaneRetired, t0, Root, ""))
	f.peers = nil

	tick(t, s, f, t0.Add(mins(30)))

	equal(t, []any{len(f.launches), len(f.ofKind("blocker"))}, []any{0, 0})
}
