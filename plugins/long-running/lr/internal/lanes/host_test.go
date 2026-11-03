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

func TestOneTickProbesEveryUnprovenSpriteInOneCallAtOneLaneAndAtAThousand(t *testing.T) {
	reads := func(n int) int {
		s := memory(t)
		f := adopted(t, s, writeBrief(t), "")
		f.peers = []Row{who(lane2, true, t0)}
		f.hosts = map[string]Host{}
		for i := range n {
			name := fmt.Sprintf("parked-%d", i)
			registered(t, s, store.Lane{Name: name, Role: "lane", Placement: placedOnSprite, PRs: []int{50000 + i}})
			f.peers = append(f.peers, Row{Lane: name, SeenAt: t0.Add(-time.Hour), Dispatch: fmt.Sprintf("ctx_%d", i), Unverified: "has an Orca agent that reads unverifiable (host_unavailable)"})
			f.hosts[name] = HostRunning
		}
		out := tick(t, s, f, t0)
		equal(t, len(out.Held), 0)
		return f.hostReads
	}
	equal(t, reads(1000), reads(1))
}

func TestASpriteReadsByWhatSpriteListAndItsWorkerProbeAnswer(t *testing.T) {
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
	env.Runner = scripted(func(argv []string) (shell.Result, error) {
		if argv[1] == "list" {
			return shell.Result{Stdout: "dar-parked\ndar-ended\ndar-hung\ndar-odd\ndar-down\n"}, nil
		}
		return answers[argv[3]](argv)
	})

	equal(t, env.hosts(context.Background(), []string{"parked", "ended", "hung", "odd", "down", "deleted"}), map[string]Host{
		"parked": HostRunning, "ended": HostEmpty, "hung": HostUnreachable, "down": HostUnreachable, "deleted": HostUnreachable,
	})

	env.Runner = scripted(func([]string) (shell.Result, error) {
		return shell.Result{}, errors.New("sprite: not logged in")
	})
	equal(t, len(env.hosts(context.Background(), []string{"parked"})), 0)
}

func lostReports(f *fake) []Post {
	var lost []Post
	for _, post := range f.ofKind("report") {
		if strings.HasPrefix(post.Text, "LANE-LOST ") {
			lost = append(lost, post)
		}
	}
	return lost
}

func unsettled(lane, dispatch string) Row {
	return Row{Lane: lane, Live: true, SeenAt: t0, Dispatch: dispatch}
}

func TestALaneReadingHeardNeverOnAColdSpriteIsLostAndRelaunchedOnceAsItsNextStem(t *testing.T) {
	s := memory(t)
	f := onSprite(t, s)
	registered(t, s, store.Lane{Name: "meta-desk-3", Role: RoleMeta})
	f.peers = []Row{unsettled(lane2, "ctx_1"), who("meta-desk-3", true, t0)}
	cold := t0.Add(-mins(22))
	f.stopped = map[string]Sprite{lane2: {"dar-conflicts-2", "cold", cold}}
	rows, err := LivenessRows(s)
	if err != nil {
		t.Fatal(err)
	}
	for _, one := range rows {
		if one.Lane == lane2 {
			equal(t, []any{one.State, one.Live, one.Heard}, []any{string(store.LaneDesired), true, (*string)(nil)})
		}
	}

	first := tick(t, s, f, t0)
	second := tick(t, s, f, t0.Add(mins(1)))

	why := "was lost: its Sprite dar-conflicts-2 is cold, last running " + store.Stamp(cold) + ", and its dispatch ctx_1 never settled"
	equal(t, first.Relaunched, []Relaunch{{lane2, "conflicts-3", why}})
	equal(t, []any{len(second.Relaunched), f.launches, f.hostReads}, []any{0, []string{"conflicts-3"}, 0})
	lost := lostReports(f)
	equal(t, len(lost), 1)
	equal(t, lost[0].To, []string{Root, "meta-desk-3"})
	contains(t, lost[0].Text, "LANE-LOST conflicts-2: its Sprite dar-conflicts-2 is cold, last running "+store.Stamp(cold))
	contains(t, lost[0].Text, "; relaunched as conflicts-3.")
	lead, _, _ := strings.Cut(f.briefs["conflicts-3"], "\n")
	contains(t, lead, "Partial work: conflicts-2 was lost mid-task")
	contains(t, lead, "`sprite exec -s dar-conflicts-2`")
}

func TestASecondLossOfARelaunchedLanePostsLaneLostAndHoldsItWithNoLaunch(t *testing.T) {
	s := memory(t)
	f := newFake(t, nil, []Row{unsettled("conflicts-3", "ctx_2")})
	if _, err := s.WantLane(store.Lane{Name: "conflicts-3", Brief: writeBrief(t), Model: "opus", Effort: "xhigh", Role: "lane", State: store.LaneDesired, Placement: placedOnSprite, Predecessor: lane2, Reason: "was lost", Launcher: Root}, t0.Add(-time.Hour)); err != nil {
		t.Fatal(err)
	}
	f.stopped = map[string]Sprite{"conflicts-3": {"dar-conflicts-3", "cold", t0.Add(-mins(10))}}

	tick(t, s, f, t0)
	tick(t, s, f, t0.Add(mins(1)))

	equal(t, []any{len(f.launches), row(t, s, "conflicts-3").State}, []any{0, store.LaneHeld})
	lost := lostReports(f)
	equal(t, len(lost), 1)
	contains(t, lost[0].Text, "; held, not relaunched, since the supervisor relaunches a lane once.")
}

func TestALaneTheLostCheckLeavesAlone(t *testing.T) {
	for name, setup := range map[string]func(*testing.T, *store.Store, *fake){
		"its dispatch settled": func(_ *testing.T, _ *store.Store, f *fake) {
			f.peers = []Row{{Lane: lane2, SeenAt: t0, Dispatch: "ctx_1", Settled: true}}
		},
		"it posted done": func(_ *testing.T, _ *store.Store, f *fake) {
			f.feed = append(f.feed, rec(2, lane2, "done", t0.Add(-mins(30)), "shipped", Root))
		},
		"it is parked on a recorded wait": func(_ *testing.T, _ *store.Store, f *fake) {
			f.feed = append(f.feed, rec(2, lane2, "blocked", t0.Add(-time.Hour), "lands after #48099").withRefs(prs(48099)))
		},
		"its Sprite stopped under five minutes ago": func(_ *testing.T, _ *store.Store, f *fake) {
			f.stopped[lane2] = Sprite{"dar-conflicts-2", "warm", t0.Add(-mins(4))}
		},
		"it runs on no Sprite": func(t *testing.T, s *store.Store, f *fake) {
			if err := s.SetLaneState(lane2, store.LaneRetired, t0, "test", ""); err != nil {
				t.Fatal(err)
			}
			f.peers = []Row{unsettled("plain-1", "ctx_9")}
			registered(t, s, store.Lane{Name: "plain-1", Role: "lane"})
			f.stopped["plain-1"] = Sprite{"dar-plain-1", "cold", t0.Add(-time.Hour)}
		},
	} {
		t.Run(name, func(t *testing.T) {
			s := memory(t)
			f := onSprite(t, s, 46001)
			f.peers = []Row{unsettled(lane2, "ctx_1")}
			f.stopped = map[string]Sprite{lane2: {"dar-conflicts-2", "cold", t0.Add(-time.Hour)}}
			setup(t, s, f)

			out := tick(t, s, f, t0)

			equal(t, []any{len(lostReports(f)), len(f.launches), len(out.Relaunched)}, []any{0, 0, 0})
		})
	}
}

func TestOneTickReadsTheSpritesListOnceAtOneLaneAndAtAThousand(t *testing.T) {
	reads := func(n int) int {
		s := memory(t)
		f := newFake(t, nil, nil)
		for i := range n {
			name := fmt.Sprintf("worker-%d", i)
			registered(t, s, store.Lane{Name: name, Role: "lane", Placement: placedOnSprite})
			f.peers = append(f.peers, unsettled(name, fmt.Sprintf("ctx_%d", i)))
		}
		tick(t, s, f, t0)
		return f.stoppedReads
	}
	equal(t, []int{reads(1), reads(1000)}, []int{1, 1})
}

func TestTheSpritesListNamesEachLanesStoppedSpriteInOnePagedReadAndFailsOpen(t *testing.T) {
	env := Env{Config: config.Config{OrcaRun: "run_1"}, Local: t.TempDir()}
	for _, lane := range []string{"cold", "warm", "up", "unlisted"} {
		writeJSON(t, filepath.Join(env.receipts(), lane+".json"), map[string]any{"result": map[string]any{"server": map[string]string{"name": "dar-" + lane}}})
	}
	pages := map[string]string{
		"/sprites":                       `{"sprites":[{"name":"dar-cold","status":"cold","last_running_at":"2026-10-31T16:38:00Z"},{"name":"dar-up","status":"running","last_running_at":"2026-10-31T17:00:00Z"}],"has_more":true,"next_continuation_token":"t2"}`,
		"/sprites?continuation_token=t2": `{"sprites":[{"name":"dar-warm","status":"warm","last_running_at":"2026-10-31T16:50:00Z"},{"name":"dar-pool-1","status":"warm","last_running_at":"2026-10-31T12:00:00Z"}],"has_more":false}`,
	}
	reads := 0
	env.Runner = scripted(func(argv []string) (shell.Result, error) {
		reads++
		return shell.Result{Stdout: pages[argv[2]]}, nil
	})

	equal(t, env.stopped(context.Background(), []string{"cold", "warm", "up", "unlisted"}), map[string]Sprite{
		"cold": {"dar-cold", "cold", time.Date(2026, 10, 31, 16, 38, 0, 0, time.UTC)},
		"warm": {"dar-warm", "warm", time.Date(2026, 10, 31, 16, 50, 0, 0, time.UTC)},
	})
	many := make([]string, 1000)
	for i := range many {
		many[i] = fmt.Sprintf("lane-%d", i)
	}
	env.stopped(context.Background(), many)
	equal(t, reads, 4)

	env.Runner = scripted(func([]string) (shell.Result, error) {
		return shell.Result{}, errors.New("sprite: not logged in")
	})
	equal(t, len(env.stopped(context.Background(), []string{"cold"})), 0)
}
