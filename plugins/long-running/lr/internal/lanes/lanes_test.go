package lanes

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/deletions"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

var t0 = time.Date(2026, 10, 31, 17, 0, 0, 0, time.UTC)

const (
	testDrive    = "d1"
	testCheckout = "/work/monorepo"
	lane2        = "conflicts-2"
	roster       = "/teams/session-d50e80e8"
)

func mins(n int) time.Duration { return time.Duration(n) * time.Minute }

func writeBrief(t *testing.T) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "brief-conflicts-2.md")
	if err := os.WriteFile(path, []byte("# conflicts-2: restack the five approved stacks\nRegister slice: lane=conflicts-2 step=tooling\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func spawned(seq int64, lane, brief string, at time.Time, attrs map[string]string) Record {
	merged := map[string]string{"brief": brief}
	for key, value := range attrs {
		merged[key] = value
	}
	return Record{Seq: seq, Lane: Root, Kind: "state", At: at, Text: fmt.Sprintf("SPAWNED %s %s", lane, brief), Fields: Fields{Attrs: merged}}
}

func rec(seq int64, lane, kind string, at time.Time, text string, to ...string) Record {
	return Record{Seq: seq, Lane: lane, Kind: kind, At: at, Text: text, To: to}
}

func prs(numbers ...int) Refs {
	refs := Refs{}
	for _, n := range numbers {
		refs.PRs = append(refs.PRs, PRRef(n))
	}
	return refs
}

func (r Record) withRefs(refs Refs) Record { r.Refs = refs; return r }
func (r Record) withRe(seq int64) Record   { r.Re = seq; return r }
func (r Record) withTopic(t string) Record { r.Topic = t; return r }

func ask(seq int64, verb, lane string, at time.Time, numbers []int, by string, attrs map[string]string) Record {
	typed := map[string]string{"verb": verb, "lane": lane}
	for key, value := range attrs {
		typed[key] = value
	}
	return Record{Seq: seq, Lane: or(by, Root), Kind: "decide", At: at, Text: fmt.Sprintf("registry request: %s %s", verb, lane), To: []string{Self}, Refs: prs(numbers...), Fields: Fields{Attrs: typed}}
}

func who(lane string, live bool, seen time.Time) Row {
	return Row{Lane: lane, Live: live, SeenAt: seen, Session: "d50e80e8", Agent: "a" + lane + "-0123456789abcdef"}
}

func onRoster(row Row) Row {
	row.Roster = roster
	return row
}

type fake struct {
	feed      []Record
	peers     []Row
	tokens    map[string]int
	posts     []Post
	launches  []string
	briefs    map[string]string
	withdrawn []string
	wakes     []string
	pages     []map[string]any
	states    map[int]string
	holds     map[int]bool
	desks     map[string]string
	work      []Record
	owed      []deletions.Owed
	gaps      []string
	deps      Deps
}

func newFake(t *testing.T, records []Record, peers []Row) *fake {
	t.Helper()
	f := &fake{feed: records, peers: peers, tokens: map[string]int{}, states: map[int]string{}, holds: map[int]bool{}, briefs: map[string]string{}}
	f.deps = Deps{
		Drive: testDrive, Checkout: testCheckout, Briefs: filepath.Join(t.TempDir(), "lanes"), HandoffBrief: "/plugin/handoff-subagent-brief.md",
		Records: func(since int64) ([]Record, error) {
			var after []Record
			for _, r := range f.feed {
				if r.Seq > since {
					after = append(after, r)
				}
			}
			return after, nil
		},
		Who:    func([]string) ([]Row, error) { return f.peers, nil },
		Tokens: func(row Row) int { return f.tokens[row.Lane] },
		Transcript: func(row Row) string {
			return fmt.Sprintf("/projects/x/%s/subagents/agent-%s.jsonl", row.Session, row.Agent)
		},
		Post: func(p Post) error { f.posts = append(f.posts, p); return nil },
		Launch: func(name, _, _, brief string) error {
			f.launches = append(f.launches, name)
			text, err := os.ReadFile(brief)
			f.briefs[name] = string(text)
			return err
		},
		Withdraw: func(name string) error { f.withdrawn = append(f.withdrawn, name); return nil },
		Wake: func(name string, _ Row, page map[string]any) (bool, error) {
			f.wakes = append(f.wakes, name)
			f.pages = append(f.pages, page)
			return true, nil
		},
		PRStates: func(numbers []int) (map[int]string, error) {
			out := map[int]string{}
			for _, n := range numbers {
				out[n] = Waiting
				if state, ok := f.states[n]; ok {
					out[n] = state
				}
			}
			return out, nil
		},
		Held:     func() (map[int]bool, error) { return f.holds, nil },
		Desk:     func(lane string) (string, error) { return f.desks[lane], nil },
		OpenWork: func() ([]Record, error) { return f.work, nil },
		Owed:     func() ([]deletions.Owed, error) { return slices.Clone(f.owed), nil },
		Present:  func(rows []deletions.Owed) ([]deletions.Owed, error) { return rows, nil },
		Gaps:     func() ([]string, error) { return f.gaps, nil },
		RotateAt: 600_000,
	}
	return f
}

func (f *fake) kinds() []string {
	out := make([]string, 0, len(f.posts))
	for _, p := range f.posts {
		out = append(out, p.Kind+":"+p.To[0])
	}
	return out
}

func (f *fake) ofKind(kind string) []Post {
	var out []Post
	for _, p := range f.posts {
		if p.Kind == kind {
			out = append(out, p)
		}
	}
	return out
}

func memory(t *testing.T) *store.Store {
	t.Helper()
	s, err := store.Memory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := s.Close(); err != nil {
			t.Fatal(err)
		}
	})
	return s
}

func tick(t *testing.T, s *store.Store, f *fake, at time.Time) Outcome {
	t.Helper()
	out, err := Tick(s, f.deps, at, 1)
	if err != nil {
		t.Fatal(err)
	}
	return out
}

func adopted(t *testing.T, s *store.Store, brief, handoff string, owned ...int) *fake {
	t.Helper()
	f := newFake(t, []Record{spawned(1, lane2, brief, t0.Add(-time.Hour), map[string]string{"model": "opus", "effort": "xhigh"})}, []Row{who(lane2, true, t0.Add(-mins(1)))})
	tick(t, s, f, t0.Add(-mins(1)))
	if handoff != "" {
		if err := s.SetLaneHandoff(lane2, handoff, time.Time{}); err != nil {
			t.Fatal(err)
		}
	}
	if err := s.SetLanePRs(lane2, owned); err != nil {
		t.Fatal(err)
	}
	f.posts = nil
	return f
}

func row(t *testing.T, s *store.Store, name string) store.Lane {
	t.Helper()
	lane, found, err := s.Lane(name)
	if err != nil || !found {
		t.Fatalf("lane %s: found=%v err=%v", name, found, err)
	}
	return lane
}

func equal(t *testing.T, got, want any) {
	t.Helper()
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %#v, want %#v", got, want)
	}
}

func contains(t *testing.T, text, want string) {
	t.Helper()
	if !strings.Contains(text, want) {
		t.Fatalf("%q does not contain %q", text, want)
	}
}

func owners(t *testing.T, s *store.Store, numbers ...int) map[int]string {
	t.Helper()
	found, err := s.OwnerOf(numbers)
	if err != nil {
		t.Fatal(err)
	}
	return found
}

func TestADeadLaneRelaunchesWithItsHandoffAndStandsDownOnceItsSuccessorIsUp(t *testing.T) {
	s, brief := memory(t), writeBrief(t)
	f := adopted(t, s, brief, "9f3c2e1", 46001, 46002)
	workers, err := s.Workers()
	if err != nil || len(workers) != 1 || workers[0].Name != lane2 {
		t.Fatalf("workers %v err %v", workers, err)
	}
	f.peers = []Row{who(lane2, false, t0.Add(-mins(20))), who("conflicts-3", false, t0.Add(-9*time.Hour))}

	out := tick(t, s, f, t0)

	equal(t, out.Relaunched, []Relaunch{{lane2, "conflicts-4", "stopped at 9:40 AM"}})
	equal(t, f.launches, []string{"conflicts-4"})
	text := f.briefs["conflicts-4"]
	if !strings.HasPrefix(text, "Replaces: conflicts-2\n") {
		t.Fatal(text)
	}
	contains(t, text, "ccn -R "+testCheckout+" doc show 9f3c2e1")
	contains(t, text, "#46001, #46002")
	body, _ := os.ReadFile(brief)
	if !strings.HasSuffix(text, string(body)) {
		t.Fatal("the brief must end with the old brief")
	}
	equal(t, f.kinds(), []string{"report:root"})
	if old := row(t, s, lane2); old.State != store.LaneRetired || old.RetiredBy != "conflicts-4" {
		t.Fatalf("%+v", old)
	}
	heir := row(t, s, "conflicts-4")
	equal(t, []any{heir.State, heir.Handoff, heir.PRs, heir.Predecessor}, []any{store.LaneLaunching, "9f3c2e1", []int{46001, 46002}, lane2})
	if workers, _ := s.Workers(); len(workers) != 0 {
		t.Fatalf("workers %v", workers)
	}
	f.posts = nil
	f.peers = []Row{who("conflicts-4", true, t0.Add(mins(2)))}

	tick(t, s, f, t0.Add(mins(2)))

	equal(t, len(f.posts), 1)
	standDown := f.posts[0]
	equal(t, []any{standDown.Kind, standDown.To, standDown.Successor, standDown.PRs}, []any{"stand-down", []string{lane2}, "conflicts-4", []int{46001, 46002}})
	equal(t, row(t, s, "conflicts-4").State, store.LaneDesired)
}

func TestARelaunchBriefKeepsTheLaneOnTheMacAndCarriesItsRegisterSlice(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.peers = []Row{who(lane2, false, t0.Add(-mins(30)))}

	tick(t, s, f, t0)

	equal(t, strings.Split(f.briefs["conflicts-3"], "\n")[:3], []string{"Replaces: conflicts-2", "ccx: place=local", "Register slice: lane=conflicts-3 step=tooling"})
}

func TestADeadLaneWhoseSuccessorLaunchNamedItIsRetiredNotRelaunched(t *testing.T) {
	s, brief := memory(t), writeBrief(t)
	f := adopted(t, s, brief, "")
	f.feed = append(f.feed, spawned(2, "restack-desk", brief, t0.Add(-mins(1)), map[string]string{"successor-of": lane2}))
	f.peers = []Row{who(lane2, false, t0.Add(-mins(30))), who("restack-desk", true, t0)}

	out := tick(t, s, f, t0)

	equal(t, len(out.Relaunched)+len(f.launches), 0)
	equal(t, row(t, s, lane2).RetiredBy, "restack-desk")
}

func TestALaneSpawnedByHandUnderAPendingRelaunchsNameWithdrawsTheRelaunch(t *testing.T) {
	for _, byHand := range []bool{true, false} {
		s, brief := memory(t), writeBrief(t)
		f := adopted(t, s, brief, "")
		f.peers = []Row{who(lane2, false, t0.Add(-mins(30)))}
		tick(t, s, f, t0)
		from := filepath.Join(f.deps.Briefs, "conflicts-3.brief.md")
		var withdrawn []string
		if byHand {
			from = filepath.Join(t.TempDir(), "root", "conflicts-3.md")
			withdrawn = []string{"conflicts-3"}
		}
		f.feed = append(f.feed, spawned(2, "conflicts-3", from, t0.Add(mins(1)), nil))

		out := tick(t, s, f, t0.Add(mins(2)))

		equal(t, f.withdrawn, withdrawn)
		equal(t, out.Withdrawn, withdrawn)
	}
}

func TestADeadLaneWithNoHandoffGetsASuccessorThatWritesOneFromTheTranscript(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.peers = []Row{who(lane2, false, t0.Add(-mins(30)))}

	tick(t, s, f, t0)

	text := f.briefs["conflicts-3"]
	contains(t, text, "conflicts-2 left no handoff")
	contains(t, text, "named `conflicts-2-handoff`")
	contains(t, text, "/projects/x/d50e80e8/subagents/agent-aconflicts-2-0123456789abcdef.jsonl")
	contains(t, text, f.deps.HandoffBrief)
}

func TestAFlushedLaneKeepsWorkingInPlace(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.peers = []Row{who(lane2, true, t0)}
	f.tokens[lane2] = 640_000
	f.feed = append(f.feed, rec(2, lane2, "state", t0.Add(-mins(2)), "restacking #46053"))

	tick(t, s, f, t0)

	equal(t, f.kinds(), []string{"ask:conflicts-2"})
	contains(t, f.posts[0].Text, "ROTATE")
	f.posts = nil
	f.feed = append(f.feed, rec(3, lane2, "handoff", t0.Add(mins(4)), "flushed 9a1b2c3", Root).withRefs(Refs{CCN: "9a1b2c3"}))
	out := tick(t, s, f, t0.Add(mins(5)))

	equal(t, len(out.Relaunched)+len(f.launches)+len(f.posts), 0)
	lane := row(t, s, lane2)
	equal(t, []any{lane.State, lane.Handoff}, []any{store.LaneDesired, "9a1b2c3"})
}

func TestAnIdleLanePastItsLineIsNeverAskedToRotate(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.peers = []Row{who(lane2, true, t0.Add(-5*time.Hour))}
	f.tokens[lane2] = 640_000

	out := tick(t, s, f, t0)

	equal(t, len(out.Asked)+len(f.posts), 0)
	if !row(t, s, lane2).Asked.IsZero() {
		t.Fatal("asked")
	}
}

func TestSilenceAloneNeverWakesALane(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.feed = append(f.feed, rec(2, lane2, "state", t0.Add(-2*time.Hour), "restacking"))
	f.peers = []Row{onRoster(who(lane2, true, t0))}

	out := tick(t, s, f, t0)

	equal(t, len(f.wakes)+len(out.Woken)+len(out.Relaunched)+len(f.launches)+len(f.posts), 0)
}

func TestALaneRootParkedIsNeverWokenUntilRootLiftsThePark(t *testing.T) {
	for _, lift := range []bool{false, true} {
		s := memory(t)
		f := adopted(t, s, writeBrief(t), "")
		f.feed = append(f.feed, rec(2, lane2, "state", t0.Add(-3*time.Hour), "restacking"), ask(3, "park", lane2, t0.Add(-2*time.Hour), nil, "", nil))
		var woken []string
		if lift {
			f.feed = append(f.feed, ask(4, "unpark", lane2, t0.Add(-time.Hour), nil, "", nil))
			woken = []string{lane2}
		}
		f.peers = []Row{onRoster(who(lane2, true, t0))}

		out := tick(t, s, f, t0)

		equal(t, f.wakes, woken)
		equal(t, out.Woken, woken)
	}
}

func stall(seq int64, at time.Time, text string, pr int, topic string) Record {
	return rec(seq, "stall-watch", "blocker", at, text, "conflicts-2@d1").withRefs(prs(pr)).withTopic(topic)
}

func TestABlockerOnAnIdleLanesPRWakesItOnceThenItGetsASuccessorWithoutAnyoneAsked(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "", 46475)
	f.feed = append(f.feed, stall(2, t0.Add(-mins(30)), "STALL red #46475 at 89f46c3, 659 min", 46475, "red:46475:89f46c3"), stall(3, t0.Add(-mins(20)), "STALL red #46475 at 89f46c3, 669 min", 46475, "red:46475:89f46c3"))
	f.peers = []Row{onRoster(who(lane2, true, t0.Add(-2*time.Hour)))}

	woken := tick(t, s, f, t0)
	f.feed = append(f.feed, stall(4, t0.Add(mins(5)), "STALL red #46475 at 89f46c3, 689 min", 46475, "red:46475:89f46c3"))
	quiet := tick(t, s, f, t0.Add(mins(10)))
	replaced := tick(t, s, f, t0.Add(PageAnswer))

	equal(t, woken.Woken, []string{lane2})
	equal(t, f.pages[0]["text"], "STALL red #46475 at 89f46c3, 659 min")
	equal(t, len(quiet.Woken)+len(replaced.Woken), 0)
	equal(t, f.wakes, []string{lane2})
	equal(t, len(f.ofKind("ask")), 0)
	equal(t, replaced.Relaunched[0].Old, lane2)
	contains(t, replaced.Relaunched[0].Why, "has made no tool call since it was woken")
	equal(t, f.launches, []string{"conflicts-3"})
	equal(t, owners(t, s, 46475)[46475], "conflicts-3")
}

func TestAWokenLaneStillMakingToolCallsIsNeverReplaced(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "", 46475)
	f.feed = append(f.feed, stall(2, t0.Add(-mins(30)), "STALL red #46475", 46475, "red:46475:89f46c3"))
	f.peers = []Row{onRoster(who(lane2, true, t0.Add(-2*time.Hour)))}
	tick(t, s, f, t0)
	f.peers = []Row{onRoster(who(lane2, true, t0.Add(PageAnswer-mins(1))))}

	out := tick(t, s, f, t0.Add(PageAnswer))

	equal(t, len(out.Relaunched)+len(f.launches), 0)
	equal(t, row(t, s, lane2).State, store.LaneDesired)
}

func TestALaneThatAnswersItsPageIsNeverAskedAbout(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "", 46475)
	f.feed = append(f.feed, stall(2, t0.Add(-mins(30)), "STALL red #46475", 46475, "red:46475:89f46c3"))
	f.peers = []Row{onRoster(who(lane2, true, t0.Add(-2*time.Hour)))}
	tick(t, s, f, t0)
	f.feed = append(f.feed, rec(3, lane2, "state", t0.Add(mins(5)), "fixing the red build of #46475"))

	out := tick(t, s, f, t0.Add(PageAnswer))

	if row(t, s, lane2).Page != nil {
		t.Fatal("page kept")
	}
	equal(t, len(out.Relaunched)+len(f.posts), 0)
}

func TestALiveLaneThatIgnoresRotateIsLeftWorking(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.peers = []Row{who(lane2, true, t0)}
	f.tokens[lane2] = 640_000
	tick(t, s, f, t0)
	f.peers = []Row{who(lane2, true, t0.Add(mins(15)))}

	out := tick(t, s, f, t0.Add(mins(15)))

	equal(t, len(out.Relaunched)+len(f.launches), 0)
}

func TestAProtectedSessionIsNeverTouched(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.peers = []Row{who(lane2, false, t0.Add(-mins(20))), who(Root, true, t0), who("p8a-step7", true, t0.Add(-2*time.Hour))}

	tick(t, s, f, t0)

	equal(t, f.kinds(), []string{"report:root"})
	equal(t, f.launches, []string{"conflicts-3"})
	names, err := s.Names()
	if err != nil {
		t.Fatal(err)
	}
	equal(t, names, []string{lane2, "conflicts-3"})
}

func TestTheRegistrySurvivesARootCompaction(t *testing.T) {
	brief := writeBrief(t)
	path := filepath.Join(t.TempDir(), "lr.sqlite")
	f := newFake(t, []Record{spawned(1, lane2, brief, t0.Add(-time.Hour), nil), rec(2, lane2, "opened", t0, "").withRefs(prs(46001)), rec(3, lane2, "handoff", t0, "").withRefs(Refs{CCN: "9f3c2e1"})}, []Row{who(lane2, true, t0)})
	opened, err := store.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	tick(t, opened, f, t0)
	if err := opened.Close(); err != nil {
		t.Fatal(err)
	}
	reopened, err := store.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer func() {
		if err := reopened.Close(); err != nil {
			t.Fatal(err)
		}
	}()
	rebuilt := memory(t)
	tick(t, rebuilt, f, t0)

	lane := row(t, reopened, lane2)
	equal(t, []any{lane.State, lane.Brief, lane.Handoff, lane.PRs}, []any{store.LaneDesired, brief, "9f3c2e1", []int{46001}})
	equal(t, row(t, rebuilt, lane2), lane)
}

func TestAnOpenedRecordNamingItsPRByBareNumberOrByRepoAddsBoth(t *testing.T) {
	s := memory(t)
	opened := Record{}
	if err := jsonInto(`{"seq":2,"lane":"conflicts-2","kind":"opened","at":"2026-10-31T17:00:00.000Z","text":"","refs":{"prs":[46001,"Forge-AI/monorepo#46002"]}}`, &opened); err != nil {
		t.Fatal(err)
	}
	f := newFake(t, []Record{spawned(1, lane2, writeBrief(t), t0.Add(-time.Hour), nil), opened}, []Row{who(lane2, true, t0)})

	tick(t, s, f, t0)

	equal(t, row(t, s, lane2).PRs, []int{46001, 46002})
}

func TestALaneThatEndedItsTurnOnAnAskToRootWaitsAndStopsOnRootsAnswer(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.feed = append(f.feed, rec(2, lane2, "ask", t0, "pick A or B?", Root))
	f.peers = []Row{who(lane2, false, t0)}
	for _, later := range []time.Duration{mins(1), 3 * time.Hour} {
		tick(t, s, f, t0.Add(later))
	}
	f.feed = append(f.feed, rec(3, Root, "answer", t0.Add(3*time.Hour), "A", lane2).withRe(2))
	tick(t, s, f, t0.Add(3*time.Hour+mins(10)))

	equal(t, len(f.launches)+len(f.posts), 0)

	out := tick(t, s, f, t0.Add(3*time.Hour+mins(16)))

	equal(t, len(out.Relaunched), 0)
	equal(t, row(t, s, lane2).RetiredBy, "stand-down")
}

func TestALaneNoProcessRowListsIsHeldForRootAndNeverRelaunched(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.feed = append(f.feed, rec(2, lane2, "state", t0, "restacking", Root))
	f.peers = nil

	out := tick(t, s, f, t0.Add(mins(16)))

	equal(t, len(out.Relaunched)+len(f.launches), 0)
	equal(t, out.Held, []string{lane2})
	equal(t, f.kinds(), []string{"blocker:root"})
	contains(t, f.posts[0].Text, "nothing proves it died")
}

func TestALaneWhoseProcessRowCCIMarksStoodDownRetiresWithoutASuccessor(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	peer := who(lane2, false, t0.Add(-mins(20)))
	peer.StoodDown = true
	f.peers = []Row{peer}

	out := tick(t, s, f, t0)

	equal(t, len(out.Relaunched)+len(f.launches), 0)
	equal(t, row(t, s, lane2).State, store.LaneRetired)
}

func TestALaneThatStoppedOnARootOrderOfAnyKindStandsDownAndIsNeverRelaunched(t *testing.T) {
	cases := map[string]struct {
		then       []Record
		relaunched bool
	}{
		"root-incident-to-it":    {[]Record{rec(2, Root, "incident", t0.Add(-mins(25)), "tenant-api: FIX-LIVE; conflicts-2 not needed, exit", lane2)}, false},
		"answered-root":          {[]Record{rec(2, Root, "incident", t0.Add(-mins(25)), "tenant-api: FIX-LIVE", "tenant-api-fix"), rec(3, lane2, "state", t0.Add(-mins(24)), "STOPPED conflicts-2: stood down per root #2").withRe(2)}, false},
		"worked-on":              {[]Record{rec(2, Root, "decide", t0.Add(-mins(25)), "keep going", lane2), rec(3, lane2, "state", t0.Add(-mins(24)), "conflicts-2: rebased #46001")}, true},
		"handoff-naming-nothing": {[]Record{rec(2, Root, "decide", t0.Add(-mins(25)), "conflicts-2 died mid-cancel. Successors, no relaunch.", "release-5"), rec(3, lane2, "handoff", t0.Add(-mins(24)), "conflicts-2: cancel settled; standing down per 2.", Root)}, false},
		"rotation-flush":         {[]Record{rec(2, lane2, "handoff", t0.Add(-mins(24)), "flushed 9a1b2c3", Root).withRe(1)}, true},
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			s := memory(t)
			f := adopted(t, s, writeBrief(t), "")
			f.feed = append(f.feed, c.then...)
			f.peers = []Row{who(lane2, false, t0.Add(-mins(20)))}

			out := tick(t, s, f, t0)

			equal(t, len(out.Relaunched) == 1, c.relaunched)
			want := "stand-down"
			if c.relaunched {
				want = "conflicts-3"
			}
			equal(t, row(t, s, lane2).RetiredBy, want)
		})
	}
}

func TestAStandingDeskWhoseLastRecordIsABlockerToAnotherLaneIsNotWaiting(t *testing.T) {
	s := memory(t)
	f, _ := standingLane(t, s)
	f.feed = append(f.feed, rec(2, refresher, "blocker", t0, "#46001 red", "p8-lane"))
	f.peers = []Row{who(refresher, false, t0)}

	out := tick(t, s, f, t0.Add(mins(16)))

	equal(t, out.Relaunched[0].Old, refresher)
}

func TestAFreshStoreAdoptsALiveLaneSilentForHoursAndNeverRelaunchesIt(t *testing.T) {
	s, brief := memory(t), writeBrief(t)
	f := newFake(t, []Record{spawned(1, lane2, brief, t0.Add(-time.Hour), nil), spawned(2, "conflicts-9", brief, t0.Add(-time.Hour), nil)}, []Row{who(lane2, true, t0.Add(-7*time.Hour)), who("conflicts-9", false, t0.Add(-7*time.Hour))})

	out := tick(t, s, f, t0)

	equal(t, out.Adopted, []string{lane2})
	equal(t, len(f.launches), 0)
	if _, found, _ := s.Lane("conflicts-9"); found {
		t.Fatal("conflicts-9 adopted")
	}
}

func TestANumberedSpawnThatNamesNoPredecessorStillTakesItsFamilysOlderLane(t *testing.T) {
	s, brief := memory(t), writeBrief(t)
	f := adopted(t, s, brief, "", 46001)
	f.feed = append(f.feed, spawned(2, "conflicts-7", brief, t0.Add(-mins(1)), nil))
	f.peers = []Row{who(lane2, true, t0), who("conflicts-7", true, t0)}

	tick(t, s, f, t0)

	equal(t, row(t, s, lane2).RetiredBy, "conflicts-7")
	equal(t, owners(t, s, 46001), map[int]string{46001: "conflicts-7"})
}

func TestAHandoffWritersRecordIsTheNewestHandoffOfTheLaneHoldingTheWork(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.feed = append(f.feed, ask(2, "stand-down", lane2, t0, nil, "", map[string]string{"successor": "conflicts-3", "last-read": "1"}), rec(3, "conflicts-2-handoff", "state", t0, "11:08 AM handoff conflicts-2: doc 7d102a9", Root))
	f.peers = []Row{who("conflicts-3", true, t0)}

	tick(t, s, f, t0)

	equal(t, row(t, s, "conflicts-3").Handoff, "7d102a9")
}

func TestRootProseSayingSomethingReplacesALaneRetiresNothing(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.feed = append(f.feed, rec(2, Root, "decide", t0, "12:30 PM the new hook replaces conflicts-2's poll loop"))
	f.peers = []Row{who(lane2, true, t0)}

	tick(t, s, f, t0)

	equal(t, row(t, s, lane2).State, store.LaneDesired)
}

func TestALaneWhoseHeadsTakeEveryPROfASuccessorThatNeverLaunchedRetiresIt(t *testing.T) {
	s, brief := memory(t), writeBrief(t)
	f := adopted(t, s, brief, "", 46612, 46615)
	f.feed = append(f.feed,
		spawned(2, "n-enum11-restack", brief, t0, nil),
		ask(3, "stand-down", lane2, t0, nil, "", map[string]string{"successor": "conflicts-3", "last-read": "2"}),
		rec(4, "n-enum11-restack", "head", t0, "").withRefs(prs(46612)),
		rec(5, "n-enum11-restack", "head", t0, "").withRefs(prs(46615)),
	)
	f.peers = []Row{who("n-enum11-restack", true, t0.Add(mins(20)))}

	tick(t, s, f, t0.Add(mins(20)))

	equal(t, f.kinds(), []string{"stand-down:conflicts-2"})
	equal(t, len(f.launches), 0)
	heir := row(t, s, "conflicts-3")
	equal(t, []any{heir.State, heir.RetiredBy}, []any{store.LaneRetired, "n-enum11-restack"})
	equal(t, row(t, s, "n-enum11-restack").PRs, []int{46612, 46615})
}

func TestAFreshStoreAdoptsOnlyTheLanesLiveNow(t *testing.T) {
	s, brief := memory(t), writeBrief(t)
	at := t0.Add(-time.Hour)
	f := newFake(t, []Record{spawned(1, lane2, brief, at, nil), spawned(2, "placement-build-4", brief, at, nil), spawned(3, "slack-read-1", brief, at, map[string]string{"role": "reader"}), spawned(4, "x-handoff", brief, at, nil)}, []Row{who(lane2, true, t0), who("placement-build-4", false, t0.Add(-3*time.Hour)), who("slack-read-1", true, t0), who("x-handoff", true, t0)})

	out := tick(t, s, f, t0)

	equal(t, out.Adopted, []string{lane2})
	equal(t, len(f.launches)+len(f.posts), 0)
}

func TestALaneSpawnedAfterAdoptionIsWatchedFromItsLaunch(t *testing.T) {
	s, brief := memory(t), writeBrief(t)
	f := adopted(t, s, brief, "")
	f.feed = append(f.feed, spawned(5, "fidelity-desk-14", brief, t0, nil))
	f.peers = append(f.peers, who("fidelity-desk-14", true, t0))

	tick(t, s, f, t0)

	equal(t, row(t, s, "fidelity-desk-14").State, store.LaneDesired)
}

func TestASuccessorThatNeverComesUpEscalatesOnce(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.peers = []Row{who(lane2, false, t0.Add(-mins(20)))}
	tick(t, s, f, t0)
	f.posts = nil

	for _, later := range []int{15, 16} {
		tick(t, s, f, t0.Add(mins(later)))
	}

	equal(t, f.kinds(), []string{"blocker:root"})
	equal(t, row(t, s, "conflicts-3").State, store.LaneHeld)
}

func TestASuccessorThatDiesWithinAnIntervalTwiceIsHeld(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	life := []struct {
		lane     string
		live     bool
		seen, at int
	}{{lane2, false, -20, 0}, {"conflicts-3", true, 1, 1}, {"conflicts-3", false, 1, 17}, {"conflicts-4", true, 18, 18}, {"conflicts-4", false, 18, 34}}
	for _, step := range life {
		f.peers = []Row{who(step.lane, step.live, t0.Add(mins(step.seen)))}
		tick(t, s, f, t0.Add(mins(step.at)))
	}

	equal(t, f.launches, []string{"conflicts-3", "conflicts-4"})
	equal(t, row(t, s, "conflicts-4").State, store.LaneHeld)
	last := f.posts[len(f.posts)-1]
	equal(t, last.Kind+":"+last.To[0], "blocker:root")
	contains(t, last.Text, "died within 30 minutes of launch 2 times")
}

func TestDoneAndARootStandDownRetireALane(t *testing.T) {
	s, brief := memory(t), writeBrief(t)
	f := adopted(t, s, brief, "")
	f.feed = append(f.feed, spawned(2, "red-sweep-3", brief, t0, nil), rec(3, lane2, "done", t0, "all five stacks restacked", Root), ask(4, "stand-down", "red-sweep-3", t0, nil, "", map[string]string{"last-read": "3"}))
	f.peers = []Row{who(lane2, false, t0), who("red-sweep-3", false, t0)}

	tick(t, s, f, t0.Add(mins(30)))

	equal(t, len(f.launches), 0)
	equal(t, f.kinds(), []string{"stand-down:red-sweep-3"})
	equal(t, row(t, s, lane2).RetiredBy, "done")
	equal(t, row(t, s, "red-sweep-3").RetiredBy, Root)
}

func TestALaneOwingADeletionWhoseCodeIsStillThereIsHeldNotRetired(t *testing.T) {
	for name, ending := range map[string]Record{
		"its done":        rec(2, lane2, "done", t0, "all five stacks restacked", Root),
		"its stop":        rec(2, lane2, "stopped", t0, "stopping at root's word", Root),
		"a root standown": ask(2, "stand-down", lane2, t0, nil, "", map[string]string{"last-read": "1"}),
	} {
		t.Run(name, func(t *testing.T) {
			s := memory(t)
			f := adopted(t, s, writeBrief(t), "")
			f.owed = []deletions.Owed{{ID: "4ec66f9bf94aa8352ab8c2229b2c9e4d3ad0d17b", Title: "Owed deletion: epoch fields left in share and journal positions", Owner: lane2, Paths: []string{"go/sand/next/cmd/iris_serve_share.go"}, Symbols: []string{"Epoch"}, Due: time.Date(2026, 11, 9, 0, 0, 0, 0, time.UTC)}}
			f.feed = append(f.feed, ending)
			f.peers = []Row{who(lane2, true, t0)}

			tick(t, s, f, t0.Add(mins(1)))

			equal(t, row(t, s, lane2).State, store.LaneHeld)
			defects := f.ofKind("defect")
			equal(t, len(defects), 1)
			equal(t, defects[0].To, []string{Root, lane2})
			contains(t, defects[0].Text, `task 4ec66f9 "Owed deletion: epoch fields left in share and journal positions" (go/sand/next/cmd/iris_serve_share.go, #Epoch; owner conflicts-2, due 2026-11-09)`)
		})
	}
}

func TestALaneRetiresWhenEveryOwedDeletionIsAnotherLanes(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.owed = []deletions.Owed{{ID: "9c99a5d50bf0b9c7fb141090e0bb6acd9798de24", Title: "Owed deletion: Go client of the server's sync and upload watch", Owner: "replicator-reads-disk-1", Dirs: []string{"go/sandsql-follower"}}}
	f.feed = append(f.feed, rec(2, lane2, "done", t0, "all five stacks restacked", Root))
	f.peers = []Row{who(lane2, false, t0)}

	tick(t, s, f, t0.Add(mins(1)))

	equal(t, row(t, s, lane2).RetiredBy, "done")
	equal(t, len(f.ofKind("defect")), 0)
}

func TestTheHourlyTickPostsOneOwedDeletionDigestPerChangedSetOfGaps(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	incarnation := "Incarnation, removed by design/one-reader, order/incarnation"
	client := "go/sand/next/internal/journal/client.go, removed by order/journal-client"
	sets := [][]string{{incarnation, client}, {incarnation, client}, {incarnation}}

	for i, gaps := range sets {
		at := t0.Add(mins(61 + 60*i))
		f.gaps = gaps
		f.peers = []Row{who(lane2, true, at)}
		tick(t, s, f, at)
	}

	blockers := f.ofKind("blocker")
	equal(t, len(blockers), 2)
	equal(t, blockers[0].To, []string{Root})
	contains(t, blockers[0].Text, "2 removes targets have no open owed-deletion task and no exception row: "+incarnation+"; "+client)
	contains(t, blockers[1].Text, "1 removes targets have no open owed-deletion task and no exception row: "+incarnation+".")
	if blockers[0].Attrs["digest"] == blockers[1].Attrs["digest"] {
		t.Fatalf("two different sets of gaps share digest %s", blockers[0].Attrs["digest"])
	}
}

func TestAnOwedDeletionPastItsDueDayDrawsOneBlockerToItsOwnerLane(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.owed = []deletions.Owed{
		{ID: "025443e4e324ee52", Title: "Owed deletion: Iris stops reading uploaded from journal positions", Owner: lane2, Paths: []string{"go/sand/next/internal/journal/client.go"}, Symbols: []string{"Uploaded"}, Due: t0.AddDate(0, 0, -3)},
		{ID: "c4015d76839d8b1c", Title: "Owed deletion: opening a Mac's own evicted chat holds the request on the cloud", Paths: []string{"go/sand/next/cmd/iris_serve_journal.go"}, Due: t0.AddDate(0, 0, -3)},
		{ID: "4ec66f9bf94aa835", Title: "Owed deletion: epoch fields left in share and journal positions", Owner: lane2, Due: t0.AddDate(0, 0, 7)},
	}

	for _, at := range []time.Time{t0.Add(mins(61)), t0.Add(mins(121))} {
		f.peers = []Row{who(lane2, true, at)}
		tick(t, s, f, at)
	}

	blockers := f.ofKind("blocker")
	equal(t, len(blockers), 2)
	equal(t, []any{blockers[0].To, blockers[1].To}, []any{[]string{lane2}, []string{Root}})
	contains(t, blockers[0].Text, `task 025443e "Owed deletion: Iris stops reading uploaded from journal positions"`)
	contains(t, blockers[0].Text, "is past due with its code still on trunk")
	contains(t, blockers[1].Text, "task c4015d7")
}

func TestAnOwedDeletionCheckThatCannotReadTellsRoot(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.deps.Gaps = func() ([]string, error) { return nil, errors.New("rulings.py rows exited 1: no register") }
	at := t0.Add(mins(61))
	f.peers = []Row{who(lane2, true, at)}

	tick(t, s, f, at)

	blockers := f.ofKind("blocker")
	equal(t, len(blockers), 1)
	contains(t, blockers[0].Text, "the owed-deletions check did not run: rulings.py rows exited 1: no register")
}

func TestALaneThatStopsAfterItsLastPRLandedIsRetiredNotRelaunched(t *testing.T) {
	for _, worked := range []bool{false, true} {
		s := memory(t)
		f := adopted(t, s, writeBrief(t), "", 47214)
		f.feed = append(f.feed, rec(2, runner, "landed", t0.Add(-mins(20)), "LANDED #47214 as ef104ad", Root).withRefs(prs(47214)))
		if worked {
			f.feed = append(f.feed, rec(3, lane2, "head", t0.Add(-mins(19)), "HEAD #47300 abc1234", Root).withRefs(prs(47300)))
		}
		f.peers = []Row{who(lane2, false, t0.Add(-mins(19)))}

		out := tick(t, s, f, t0)

		equal(t, len(out.Relaunched) > 0, worked)
		if !worked {
			lane := row(t, s, lane2)
			equal(t, []any{lane.State, lane.RetiredBy, len(f.launches)}, []any{store.LaneRetired, kindLanded, 0})
		}
	}
}

func TestALaneThatRecordsItsOwnStandDownIsRetiredNotRelaunched(t *testing.T) {
	for kind, text := range map[string]string{"stand-down": "2:10 PM conflicts-2: standing down, the coordinator owns it", "stopped": "stopping at root's word"} {
		s := memory(t)
		f := adopted(t, s, writeBrief(t), "")
		f.feed = append(f.feed, rec(2, lane2, kind, t0, text, Root))
		f.peers = []Row{who(lane2, false, t0)}

		out := tick(t, s, f, t0.Add(mins(16)))

		equal(t, len(out.Relaunched)+len(f.launches), 0)
		lane := row(t, s, lane2)
		equal(t, []any{lane.State, lane.RetiredBy}, []any{store.LaneRetired, "stand-down"})
	}
}

func TestALaneReportingAnotherLanesStandDownStaysWatched(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.feed = append(f.feed, rec(2, lane2, "state", t0, "p8-lane STAND-DOWN seen; conflicts-2 keeps restacking", Root))
	f.peers = []Row{who(lane2, true, t0)}

	tick(t, s, f, t0)

	equal(t, row(t, s, lane2).State, store.LaneDesired)
}

func TestProseNeverRetiresALaneSoOneThatDiesMidTaskGetsASuccessor(t *testing.T) {
	for _, text := range []string{"2:24 PM conflicts-2: finish #1478 and the release, then stand down; your successor is spawned from 99c1291", "conflicts-2 STAND-DOWN per #265653. Thread names 2 releases"} {
		s := memory(t)
		f := adopted(t, s, writeBrief(t), "")
		f.feed = append(f.feed, rec(2, Root, "decide", t0, text, lane2, "mq-health-6"), rec(3, lane2, "state", t0, "conflicts-2: on #1478"))
		f.peers = []Row{who(lane2, false, t0)}

		out := tick(t, s, f, t0.Add(mins(16)))

		equal(t, out.Relaunched[0].New, "conflicts-3")
		equal(t, row(t, s, lane2).RetiredBy, "conflicts-3")
	}
}

func TestARootDecideMovesPROwnership(t *testing.T) {
	s, brief := memory(t), writeBrief(t)
	f := adopted(t, s, brief, "", 46001)
	f.feed = append(f.feed, spawned(2, "restack-children-2", brief, t0, nil), ask(3, "owner", "restack-children-2", t0, []int{46001}, "", map[string]string{"last-read": "2"}))
	f.peers = append(f.peers, who("restack-children-2", true, t0))

	tick(t, s, f, t0)

	equal(t, len(row(t, s, lane2).PRs), 0)
	equal(t, row(t, s, "restack-children-2").PRs, []int{46001})
}

func TestARootRecordStandingDownALaneItHasNotReadSinceItsNewestRecordIsRefusedAndTheLaneStays(t *testing.T) {
	cases := []struct {
		request Record
		cited   string
	}{
		{ask(4, "stand-down", lane2, t0, nil, "", nil), "cites no last-read=<seq>"},
		{ask(4, "stand-down", lane2, t0, nil, "", map[string]string{"last-read": "2"}), "cites last-read=2, older than conflicts-2's newest record #3"},
		{ask(4, "owner", "restack-children-2", t0, []int{46001}, "", nil), "cites no last-read=<seq>"},
	}
	for _, c := range cases {
		s, brief := memory(t), writeBrief(t)
		f := adopted(t, s, brief, "", 46001)
		f.feed = append(f.feed, spawned(2, "restack-children-2", brief, t0, nil), rec(3, lane2, "head", t0, "#46001 e590637").withRefs(prs(46001)), c.request)
		f.peers = append(f.peers, who("restack-children-2", true, t0))

		tick(t, s, f, t0)

		lane := row(t, s, lane2)
		equal(t, []any{lane.State, lane.PRs}, []any{store.LaneHeld, []int{46001}})
		equal(t, len(f.posts), 1)
		equal(t, []any{f.posts[0].Kind, f.posts[0].To}, []any{"defect", []string{Root, lane2}})
		contains(t, f.posts[0].Text, "the coordinator's decide #4 would stand conflicts-2 down or hand its PRs on, but it "+c.cited+", so the supervisor refused it and holds conflicts-2 with its PRs")
	}
}

func TestAnOwnerDecideMovesOnlyThePRsItRefsAndStandsNoLaneItMentionsDown(t *testing.T) {
	s, brief := memory(t), writeBrief(t)
	f := adopted(t, s, brief, "", 47245)
	f.feed = append(f.feed, spawned(2, "restack-children-2", brief, t0, nil), rec(3, lane2, "head", t0, "#47245 7071204").withRefs(prs(47245)),
		rec(4, Root, "decide", t0, "root: owner=restack-children-2 for #48037. Its red is #47245 7071204: conflicts-2 fixes that on #47245 now and posts HEAD.", "supervisor-registry", "restack-children-2", lane2).withRefs(prs(48037)))
	f.peers = append(f.peers, who("restack-children-2", true, t0))

	tick(t, s, f, t0)

	if row(t, s, lane2).State == store.LaneHeld {
		t.Fatal("held")
	}
	equal(t, owners(t, s, 48037, 47245), map[int]string{48037: "restack-children-2", 47245: lane2})
	equal(t, len(f.ofKind("defect")), 0)
}

func TestAnOwnerDecideMovesItsPRFromALiveLaneAndHoldsNoLane(t *testing.T) {
	s, brief := memory(t), writeBrief(t)
	f := adopted(t, s, brief, "", 48257)
	f.feed = append(f.feed, spawned(2, "restack-children-2", brief, t0, nil), rec(3, lane2, "head", t0, "#48257 6322d2f").withRefs(prs(48257)),
		rec(4, Root, "decide", t0, "root: #48257 at 6322d2f: owner=restack-children-2 again for this one item; conflicts-2 restacked it.", "restack-children-2", "supervisor-registry").withRefs(prs(48257)))
	f.peers = append(f.peers, who("restack-children-2", true, t0))

	tick(t, s, f, t0)

	if row(t, s, lane2).State == store.LaneHeld {
		t.Fatal("held")
	}
	equal(t, owners(t, s, 48257), map[int]string{48257: "restack-children-2"})
	equal(t, len(f.ofKind("defect")), 0)
}

func TestALaneAnUnreadStandDownLeftInPlaceIsNeverRelaunchedOnceItStops(t *testing.T) {
	for _, postsAgain := range []bool{false, true} {
		s := memory(t)
		f := adopted(t, s, writeBrief(t), "")
		f.feed = append(f.feed, rec(2, lane2, "state", t0, "all five stacks restacked"), ask(3, "stand-down", lane2, t0, nil, "", nil))
		tick(t, s, f, t0)
		equal(t, row(t, s, lane2).State, store.LaneHeld)
		if postsAgain {
			f.feed = append(f.feed, rec(4, lane2, "state", t0.Add(mins(1)), "LIVE conflicts-1: nothing to take over"))
			tick(t, s, f, t0.Add(mins(2)))
			equal(t, row(t, s, lane2).State, store.LaneDesired)
		}
		f.peers = []Row{who(lane2, false, t0)}
		f.posts = nil

		out := tick(t, s, f, t0.Add(time.Hour))

		equal(t, row(t, s, lane2).State, store.LaneHeld)
		equal(t, len(f.launches)+len(out.Relaunched), 0)
		told := 0
		for _, p := range f.posts {
			if strings.Contains(p.Text, "the coordinator's decide #3 would stand conflicts-2 down or hand its PRs on, but it cites no last-read=<seq>, so the supervisor refused it, and the lane has since stopped") {
				told++
			}
		}
		equal(t, told == 1, postsAgain)
	}
}

func TestARootStandDownCitingAReadAsNewAsTheLanesNewestRecordRetiresIt(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.feed = append(f.feed, rec(2, lane2, "state", t0, "restacked"), ask(3, "stand-down", lane2, t0, nil, "", map[string]string{"last-read": "2"}))

	tick(t, s, f, t0)

	equal(t, row(t, s, lane2).State, store.LaneRetired)
	equal(t, f.kinds(), []string{"stand-down:conflicts-2"})
}

func TestTheOwnerHearsOneLineAnHourAndOnlyWhenALaneRestarted(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.peers = []Row{who(lane2, false, t0)}
	tick(t, s, f, t0.Add(mins(16)))
	f.peers = []Row{who("conflicts-3", true, t0.Add(mins(50)))}
	tick(t, s, f, t0.Add(mins(50)))
	f.posts = nil
	f.peers = []Row{who("conflicts-3", true, t0.Add(2*time.Hour+mins(1)))}

	tick(t, s, f, t0.Add(time.Hour+mins(1)))
	tick(t, s, f, t0.Add(time.Hour+mins(2)))
	tick(t, s, f, t0.Add(2*time.Hour+mins(1)))

	equal(t, len(f.posts), 1)
	equal(t, f.posts[0].Attrs, map[string]string{"audience": "owner"})
	equal(t, f.posts[0].Text, "Between 10:00 AM and 11:00 AM the lane supervisor restarted 1 lane with no coordinator action: conflicts-2 stopped at 10:00 AM, so conflicts-3 took over.")
}

func TestClassify(t *testing.T) {
	type peerAt struct {
		live bool
		mins int
	}
	cases := []struct {
		state  store.LaneState
		peer   *peerAt
		heard  int
		tokens int
		want   VerdictKind
	}{
		{store.LaneDesired, &peerAt{true, 1}, -1, 0, Ok},
		{store.LaneDesired, &peerAt{true, 20}, -1, 0, Ok},
		{store.LaneDesired, &peerAt{true, 20}, 2, 0, Ok},
		{store.LaneDesired, &peerAt{false, 1}, -1, 0, Ok},
		{store.LaneDesired, &peerAt{false, 20}, -1, 0, Dead},
		{store.LaneDesired, &peerAt{false, 20}, 2, 0, Ok},
		{store.LaneDesired, nil, -1, 0, Unproven},
		{store.LaneDesired, &peerAt{true, 1}, -1, 700_000, AskRotate},
		{store.LaneLaunching, nil, -1, 0, Ok},
		{store.LaneLaunching, &peerAt{true, 1}, -1, 0, CameUp},
	}
	for _, c := range cases {
		lane := store.Lane{Name: "l-1", State: c.state, Since: t0.Add(-mins(10))}
		if c.heard >= 0 {
			lane.Heard = t0.Add(-mins(c.heard))
		}
		var seen *Peer
		if c.peer != nil {
			seen = &Peer{Lane: "l-1", Live: c.peer.live, Seen: t0.Add(-mins(c.peer.mins))}
		}
		equal(t, Classify(lane, seen, t0, c.tokens, 600_000).Kind, c.want)
	}
}

func TestALaunchingLaneThatNeverComesUpIsANoShow(t *testing.T) {
	equal(t, Classify(store.Lane{State: store.LaneLaunching, Since: t0.Add(-mins(15))}, nil, t0, 0, 0).Kind, NoShow)
}

func TestSuccessorName(t *testing.T) {
	cases := []struct {
		name  string
		taken []string
		want  string
	}{
		{lane2, nil, "conflicts-3"},
		{lane2, []string{"conflicts-3", "conflicts-4"}, "conflicts-5"},
		{"mq-health", nil, "mq-health-2"},
		{"tooling-misc-3-ccx-stack", nil, "tooling-misc-3-ccx-stack-2"},
		{"n-enum11-46068", nil, "n-enum11-46068-2"},
	}
	for _, c := range cases {
		taken := map[string]bool{}
		for _, name := range c.taken {
			taken[name] = true
		}
		equal(t, successorName(c.name, taken), c.want)
	}
}

func TestATickWhoseLivenessSourceDoesNotAnswerActsOnNothing(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.deps.Who = func([]string) ([]Row, error) { return nil, fmt.Errorf("%w: orca: connection refused", ErrUnread) }

	out, err := Tick(s, f.deps, t0.Add(time.Hour), 1)

	if !errors.Is(err, ErrUnread) {
		t.Fatalf("err %v", err)
	}
	equal(t, out, Outcome{})
	equal(t, len(f.posts), 0)
	equal(t, row(t, s, lane2).State, store.LaneDesired)
}

func TestALaneReadsLiveUntilItsRowEnds(t *testing.T) {
	s := memory(t)
	for name, heard := range map[string]time.Time{"fresh-1": t0.Add(-mins(50)), "stale-1": t0.Add(-7 * time.Hour), "gone-1": t0.Add(-mins(5))} {
		lane := store.Lane{Name: name, Brief: "/briefs/x.md", Model: "opus", Effort: "xhigh", Role: "lane", State: store.LaneDesired}
		if name == "fresh-1" {
			lane.PRs = []int{46475}
		}
		if _, err := s.WantLane(lane, t0.Add(-8*time.Hour)); err != nil {
			t.Fatal(err)
		}
		if err := s.LaneHeard(name, heard, "state", 0); err != nil {
			t.Fatal(err)
		}
	}
	retire(s, "gone-1", "gone-2", t0)

	rows, err := LivenessRows(s)
	if err != nil {
		t.Fatal(err)
	}
	byName := map[string]Liveness{}
	for _, one := range rows {
		byName[one.Lane] = one
	}

	equal(t, []bool{byName["fresh-1"].Live, byName["stale-1"].Live, byName["gone-1"].Live}, []bool{true, true, false})
	equal(t, byName["fresh-1"].PRs, []int{46475})
	equal(t, *byName["gone-1"].RetiredBy, "gone-2")
}

func TestEveryLaneThatPostsIsInTheLivenessAnswerWithThePRsItLastCarried(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "", 46446)
	f.feed = append(f.feed,
		rec(2, runner, "state", t0.Add(-mins(10)), "tick"),
		rec(3, "p8c-conversion-ship-6", "head", t0.Add(-mins(8)), "#46446 3f2a1bc").withRefs(prs(46446)),
		ask(4, "owner", "signal-valkey-2", t0.Add(-mins(6)), []int{46731}, "", nil),
		rec(5, "landing-desk-3", kindLanded, t0.Add(-mins(4)), "#46999 landed").withRefs(prs(46999)),
	)
	if err := s.Own("orphan-restack-1", []int{46999}); err != nil {
		t.Fatal(err)
	}

	tick(t, s, f, t0)
	rows, err := LivenessRows(s)
	if err != nil {
		t.Fatal(err)
	}
	byName := map[string]Liveness{}
	for _, one := range rows {
		byName[one.Lane] = one
	}

	equal(t, []any{byName[runner].State, byName[runner].Live}, []any{Unregistered, true})
	equal(t, byName["p8c-conversion-ship-6"].PRs, []int{46446})
	equal(t, byName[lane2].PRs, []int{})
	equal(t, byName["signal-valkey-2"].PRs, []int{46731})
	if _, found := byName["orphan-restack-1"]; found {
		t.Fatal("orphan-restack-1 listed")
	}
}

func TestALaneParkedOnAnOwnerCardWaitsThroughRootsReplyAndWakesOnceWithTheOwnersPick(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.peers = []Row{onRoster(who(lane2, true, t0.Add(-2*time.Hour)))}
	f.feed = append(f.feed,
		rec(2, lane2, "blocked", t0.Add(-2*time.Hour), "parked on the owner's pick on card-cleanup-daemon-restart", Root),
		rec(3, Root, "decide", t0.Add(-mins(90)), "conflicts-2 is held on the owner's card by design", lane2).withRe(2),
	)

	waiting := tick(t, s, f, t0)
	f.feed = append(f.feed, rec(4, "board-watch", "owner", t0.Add(mins(1)), "Owner on board d50e80e8, Restart the cleanup daemon? (cleanup-daemon-restart): picked \"yes, restart it\"", "all"))
	picked := tick(t, s, f, t0.Add(mins(2)))
	after := tick(t, s, f, t0.Add(mins(10)))

	equal(t, len(waiting.Woken)+len(after.Woken), 0)
	equal(t, row(t, s, lane2).Awaits, "")
	equal(t, picked.Woken, []string{lane2})
	if why := pageText(f.pages[0], "why"); !strings.HasPrefix(why, "the owner answered card-cleanup-daemon-restart, the card you wait on: Owner on board") {
		t.Fatal(why)
	}
}

func TestAHeadRelayedToThePRsOwnerLeavesTheOwnerInPlace(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "", 46888)
	f.feed = append(f.feed,
		rec(2, runner, "head", t0.Add(-mins(5)), "HEAD #46888 9de73e2: the landing runner rebased it", lane2, "fidelity-desk-3").withRefs(prs(46888)),
		rec(3, runner, "head", t0.Add(-mins(4)), "HEAD #46889 1a2b3c4: rebased", "fidelity-desk-3").withRefs(prs(46889)),
	)

	tick(t, s, f, t0)

	equal(t, owners(t, s, 46888, 46889), map[int]string{46888: lane2, 46889: runner})
}

func TestRotateReadsActivityFromTheLanesOwnRecordsNeverItsCCIWhoRow(t *testing.T) {
	for name, last := range map[string]Record{
		"in-process-row-fresh-but-records-old": rec(2, lane2, "state", t0.Add(-2*time.Hour), "restacking"),
		"last-record-a-handoff":                rec(2, lane2, "handoff", t0.Add(-mins(3)), "flushed", Root).withRefs(Refs{CCN: "9a1b2c3"}),
	} {
		t.Run(name, func(t *testing.T) {
			s := memory(t)
			f := adopted(t, s, writeBrief(t), "")
			f.feed = append(f.feed, last)
			f.peers = []Row{who(lane2, true, t0)}
			f.tokens[lane2] = 640_000

			out := tick(t, s, f, t0)

			equal(t, len(out.Asked)+len(f.posts), 0)
		})
	}
}

func TestAPageWhoseBlockerResolvedOrWhosePRLeftTheLaneNeverWakesIt(t *testing.T) {
	resolved := rec(3, "flake-dash-1", "answer", t0.Add(-mins(20)), "nothing to fix")
	resolved.Resolves, resolved.AlsoResolves = 9, []int64{2}
	cases := map[string]Record{
		"blocker-resolved": resolved,
		"pr-landed":        rec(3, "landing-desk-3", kindLanded, t0.Add(-mins(20)), "#46475 landed").withRefs(prs(46475)),
		"pr-moved":         rec(3, "conflicts-8", "head", t0.Add(-mins(20)), "#46475 1a2b3c4", Root).withRefs(prs(46475)),
		"pr-at-a-new-head": rec(3, runner, "head", t0.Add(-mins(20)), "HEAD #46475 5d6e7f8: the landing runner rebased it", lane2).withRefs(prs(46475)),
	}
	for name, after := range cases {
		t.Run(name, func(t *testing.T) {
			s := memory(t)
			f := adopted(t, s, writeBrief(t), "", 46475)
			f.feed = append(f.feed, stall(2, t0.Add(-mins(30)), "STALL red #46475", 46475, "red:46475:89f46c3"), after)
			f.peers = []Row{onRoster(who(lane2, true, t0))}

			tick(t, s, f, t0)

			for _, page := range f.pages {
				if page != nil {
					t.Fatalf("paged %v", page)
				}
			}
			if row(t, s, lane2).Page != nil {
				t.Fatal("page kept")
			}
		})
	}
}

func TestAFreshStorePagesNoLaneFromTheBlockersItRefolds(t *testing.T) {
	s := memory(t)
	f := newFake(t, []Record{spawned(1, lane2, writeBrief(t), t0.Add(-4*time.Hour), nil), rec(2, lane2, "head", t0.Add(-3*time.Hour), "#46475 1a2b3c4", Root).withRefs(prs(46475)), stall(3, t0.Add(-2*time.Hour), "STALL red #46475", 46475, "")}, []Row{onRoster(who(lane2, true, t0))})

	tick(t, s, f, t0)

	if row(t, s, lane2).Page != nil {
		t.Fatal("page kept")
	}
	for _, page := range f.pages {
		if page != nil {
			t.Fatalf("paged %v", page)
		}
	}
}

func TestARootDecideToTheSupervisorWithNoRegistryRequestIsAnsweredWithTheRequestToPost(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.feed = append(f.feed, rec(2, Root, "decide", t0, "Root task list: conflicts-2 (1985) is complete and closed; supervisor, stop waking it. last-read=1", Self, lane2), ask(3, "done", lane2, t0, nil, "", nil))

	tick(t, s, f, t0)

	defects := f.ofKind("defect")
	equal(t, len(defects), 1)
	equal(t, defects[0].Re, int64(2))
	contains(t, defects[0].Text, "post `lr request <done|owner|park|stand-down|unpark> <lane>`")
	equal(t, row(t, s, lane2).State, store.LaneRetired)
}

func TestARootDecideToTheSupervisorNamingNoRegistryVerbRaisesNoDefect(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.feed = append(f.feed, rec(2, Root, "decide", t0, "root: #48348 c0035cd: no answer to the 12:07 chase and fidelity still missing since 11:47. conflicts-2, post by 12:30 or the coordinator names a successor from your handoff c93936e.", Self, lane2))

	tick(t, s, f, t0)

	equal(t, len(f.ofKind("defect")), 0)
}

func TestALaneWhoseNewestRecordSettlesItIsNeverWoken(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "", 46475)
	f.feed = append(f.feed, rec(2, lane2, "handoff", t0.Add(-2*time.Hour), "flushed", Root).withRefs(Refs{CCN: "9a1b2c3"}), stall(3, t0.Add(-time.Hour), "STALL red #46475", 46475, ""))
	f.peers = []Row{onRoster(who(lane2, true, t0))}

	out := tick(t, s, f, t0)

	equal(t, len(out.Woken)+len(f.wakes), 0)
}

func TestARootAskNamingASilentLanesPRPagesItOnce(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "", 46869)
	f.feed = append(f.feed, rec(2, Root, "ask", t0.Add(-mins(30)), "agent-construct-build-6: where is #46869's red fix?", lane2))
	f.peers = []Row{onRoster(who(lane2, true, t0))}

	out := tick(t, s, f, t0)
	again := tick(t, s, f, t0.Add(mins(5)))

	equal(t, out.Woken, []string{lane2})
	equal(t, len(again.Woken), 0)
	if why := pageText(f.pages[0], "why"); !strings.HasPrefix(why, "coordinator ask #2 on your PR #46869") {
		t.Fatal(why)
	}
}

func TestAQuietOwnerOfAPRThatNeedsWorkHearsTheFactAndRootHearsNothing(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "", 46869, 47079, 46001)
	if err := s.LaneHeard(lane2, t0.Add(-mins(90)), "state", 0); err != nil {
		t.Fatal(err)
	}
	f.states = map[int]string{46869: NeedsWork, 47079: NeedsWork, 46001: Waiting}
	f.peers = []Row{onRoster(who(lane2, true, t0.Add(-mins(90))))}

	out := tick(t, s, f, t0)
	again := tick(t, s, f, t0.Add(mins(5)))

	equal(t, out.Woken, []string{lane2})
	equal(t, len(again.Woken), 0)
	contains(t, pageText(f.pages[0], "why"), "you own #46869 #47079, which still need work")
	equal(t, len(f.posts), 0)
}

func TestAQuietOwnerPastItsWakeGetsASuccessorThatTakesItsPRs(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "", 46869)
	if err := s.LaneHeard(lane2, t0.Add(-mins(90)), "state", 0); err != nil {
		t.Fatal(err)
	}
	f.states = map[int]string{46869: NeedsWork}
	f.peers = []Row{onRoster(who(lane2, true, t0.Add(-mins(90))))}

	tick(t, s, f, t0)
	out := tick(t, s, f, t0.Add(PageAnswer))

	equal(t, out.Relaunched[0].Old, lane2)
	equal(t, owners(t, s, 46869)[46869], "conflicts-3")
	equal(t, len(f.ofKind("blocker"))+len(f.ofKind("ask")), 0)
}

func TestAClosedPRLeavesItsQuietOwner(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "", 46869)
	if err := s.LaneHeard(lane2, t0.Add(-mins(90)), "state", 0); err != nil {
		t.Fatal(err)
	}
	f.states = map[int]string{46869: Closed}

	tick(t, s, f, t0)

	equal(t, owners(t, s, 46869)[46869], "")
	equal(t, len(f.wakes)+len(f.posts), 0)
}

func TestASuccessorLaunchTakesItsPredecessorsPRsAndBecomesTheEndOfItsChain(t *testing.T) {
	s, brief := memory(t), writeBrief(t)
	f := adopted(t, s, brief, "")
	if _, err := s.WantLane(store.Lane{Name: "conflicts-3", Brief: brief, Model: "opus", Effort: "xhigh", Role: "lane", State: store.LaneDesired, PRs: []int{46932, 47000, 46869}}, t0.Add(-2*time.Hour)); err != nil {
		t.Fatal(err)
	}
	retire(s, lane2, "conflicts-3", t0.Add(-2*time.Hour))
	f.feed = append(f.feed, rec(2, "conflicts-3", "done", t0.Add(-time.Hour), "stack handed on"), spawned(3, "conflicts-4", brief, t0.Add(-mins(30)), map[string]string{"successor-of": "conflicts-3"}))

	tick(t, s, f, t0)

	equal(t, owners(t, s, 46932, 47000, 46869), map[int]string{46932: "conflicts-4", 47000: "conflicts-4", 46869: "conflicts-4"})
	equal(t, row(t, s, "conflicts-3").RetiredBy, "conflicts-4")
	equal(t, row(t, s, lane2).RetiredBy, "conflicts-3")
	heir, _ := newest(s, lane2)
	equal(t, heir.Name, "conflicts-4")
}

func TestAQuietLaneWhosePRsOnlyWaitOnTheOwnersTapIsNeverAsked(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "", 46788, 47021)
	if err := s.LaneHeard(lane2, t0.Add(-3*time.Hour), "state", 0); err != nil {
		t.Fatal(err)
	}
	f.states = map[int]string{46788: Waiting, 47021: Waiting}

	tick(t, s, f, t0)
	tick(t, s, f, t0.Add(StaleAfter))

	equal(t, len(f.posts), 0)
	probes, _ := s.Probes()
	equal(t, len(probes), 0)
}

func TestAStandDownForALaneThatNeverBoundIsAddressedAsWritten(t *testing.T) {
	draft := Post{Kind: "stand-down", To: []string{"scrub-1"}, Text: "x", Successor: "scrub-2"}.Draft(testDrive)

	equal(t, draft.Force, true)
}

func TestEverySupervisorPostLetsCCIStampItsTimeSoASlowTickIsNeverRefused(t *testing.T) {
	draft := Post{Kind: "report", To: []string{Root}, Text: "x", LaneRef: "l", PRs: []int{9}, Re: 4, Successor: "s"}.Draft(testDrive)

	equal(t, draft, client.Draft{Drive: testDrive, Lane: Self, Kind: "report", To: []string{Root}, Text: "x", Re: 4, Successor: "s", Force: true, LaneRefs: []string{"l"}, PRs: []string{"9"}, Stamp: true})
}

func TestARootStandDownNamingNoSuccessorUnownsTheLanesPRsAndNamesToRootOnceOnlyThoseNeedingWork(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "", 45492, 46512)
	f.feed = append(f.feed, ask(2, "stand-down", lane2, t0.Add(-mins(5)), nil, "", map[string]string{"last-read": "1"}))
	f.states = map[int]string{45492: NeedsWork, 46512: Waiting}

	tick(t, s, f, t0.Add(time.Hour))
	tick(t, s, f, t0.Add(2*time.Hour))

	stranded := []Post{}
	for _, p := range f.posts {
		if p.To[0] == lane2 && p.Kind != "stand-down" {
			t.Fatalf("asked the lane: %+v", p)
		}
		if strings.Contains(p.Text, "no lane owns") {
			stranded = append(stranded, p)
		}
	}
	equal(t, len(stranded), 1)
	equal(t, stranded[0].Kind+":"+stranded[0].To[0], "blocker:root")
	contains(t, stranded[0].Text, "#45492 needs work and no lane owns it")
	equal(t, owners(t, s, 45492, 46512), map[int]string{45492: Root, 46512: Root})
}

func TestARootStandDownAfterTheLanesOwnDoneStillHandsItsPRsToRoot(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "", 45492, 46512)
	f.feed = append(f.feed, rec(2, lane2, "done", t0.Add(-mins(6)), "done: all PRs restacked"), ask(3, "stand-down", lane2, t0.Add(-mins(5)), nil, "", map[string]string{"last-read": "2"}))
	f.states = map[int]string{45492: NeedsWork, 46512: Waiting}

	tick(t, s, f, t0.Add(time.Hour))

	for _, p := range f.posts {
		if p.To[0] == lane2 && p.Kind != "stand-down" {
			t.Fatalf("asked the lane: %+v", p)
		}
	}
	equal(t, owners(t, s, 45492, 46512), map[int]string{45492: Root, 46512: Root})
}

func TestANeedsWorkPageSkipsAHeldPRAndAPRRootMoved(t *testing.T) {
	said := map[string]Record{
		"hold":   rec(2, Root, "hold", t0.Add(-mins(99)), "hold #46107: held per its earlier root decide; no work owed by any lane until the hold lifts", Self).withRefs(prs(46107)),
		"decide": rec(3, Root, "decide", t0.Add(-mins(98)), "root: #46107 moves to root under its existing typed hold", Self).withRefs(prs(46107)),
	}
	for _, shown := range [][]string{{}, {"hold"}, {"decide"}} {
		s := memory(t)
		f := adopted(t, s, writeBrief(t), "", 46107)
		for _, kind := range shown {
			f.feed = append(f.feed, said[kind])
		}
		if slices.Contains(shown, "hold") {
			f.holds = map[int]bool{46107: true}
		}
		f.states = map[int]string{46107: NeedsWork}
		if err := s.LaneHeard(lane2, t0.Add(-mins(90)), "state", 0); err != nil {
			t.Fatal(err)
		}
		f.peers = []Row{onRoster(who(lane2, true, t0))}

		tick(t, s, f, t0)

		equal(t, slices.Contains(f.wakes, lane2), len(shown) == 0)
	}
}

func TestALaneARootDecideCallsRetiredOrDoneIsRetiredAndNeverAsked(t *testing.T) {
	for _, text := range []string{"root: conflicts-2 retired; gos posted for the four (stack order on #48190).", "root: #47491 moves to p8a-step7; conflicts-2 is done (registry request filed)."} {
		s := memory(t)
		f := adopted(t, s, writeBrief(t), "", 48283)
		f.feed = append(f.feed, rec(2, Root, "decide", t0.Add(-mins(95)), text, lane2))
		f.states = map[int]string{48283: NeedsWork}
		if err := s.LaneHeard(lane2, t0.Add(-mins(90)), "state", 0); err != nil {
			t.Fatal(err)
		}
		f.peers = []Row{onRoster(who(lane2, true, t0))}

		tick(t, s, f, t0)

		equal(t, row(t, s, lane2).State, store.LaneRetired)
		for _, p := range f.ofKind("ask") {
			if p.To[0] == lane2 {
				t.Fatalf("asked %+v", p)
			}
		}
	}
}

func TestAHeldLaneThatPostsAgainIsLiveAndAPRRootOpenedForItIsTheLanes(t *testing.T) {
	s, brief := memory(t), writeBrief(t)
	f := adopted(t, s, brief, "")
	f.feed = append(f.feed, spawned(2, "ssql-sleep-truncation", brief, t0, nil))
	tick(t, s, f, t0)
	tick(t, s, f, t0.Add(mins(16)))
	equal(t, row(t, s, "ssql-sleep-truncation").State, store.LaneHeld)
	f.posts = nil
	f.states = map[int]string{47353: NeedsWork}
	f.feed = append(f.feed,
		rec(3, Root, "opened", t0.Add(mins(28)), "root opened #Forge-AI/monorepo#47353").withRefs(prs(47353)),
		rec(4, "ssql-sleep-truncation", "head", t0.Add(mins(28)), "HEAD #47353 at 3c97d53", Root).withRefs(prs(47353)),
	)

	tick(t, s, f, t0.Add(mins(29)))

	equal(t, row(t, s, "ssql-sleep-truncation").State, store.LaneDesired)
	equal(t, owners(t, s, 47353), map[int]string{47353: "ssql-sleep-truncation"})
	for _, p := range f.posts {
		if strings.Contains(p.Text, "no lane owns") {
			t.Fatalf("%+v", p)
		}
	}
}

func TestALaneWhoseOrcaWorkerFinishedIsRetiredAndNeverRelaunched(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.feed = append(f.feed, ask(2, "done", lane2, t0.Add(-mins(14)), nil, runner, nil))
	f.peers = []Row{who(lane2, false, t0.Add(-mins(20)))}

	out := tick(t, s, f, t0)

	equal(t, len(out.Relaunched)+len(f.launches), 0)
	lane := row(t, s, lane2)
	equal(t, []any{lane.State, lane.RetiredBy}, []any{store.LaneRetired, "done"})
}

func TestALaneWhoseOrcaWorkerSucceededByItsOutcomeLineIsRetired(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.feed = append(f.feed, rec(2, runner, "report", t0.Add(-mins(14)), "OUTCOME msg_b21ededd168d conflicts-2: worker_done succeeded dispatch=ctx_320e271b3112"))
	f.peers = []Row{who(lane2, false, t0.Add(-mins(20)))}

	out := tick(t, s, f, t0)

	equal(t, len(out.Relaunched), 0)
	equal(t, row(t, s, lane2).RetiredBy, "worker-done")
}

func TestALaneThatStoppedOnAnEarlierDayNamesTheDateSoCciNeverReadsTheClockAsAhead(t *testing.T) {
	at := t0.Add(-30 * time.Hour)

	verdict := Classify(store.Lane{Name: lane2, State: store.LaneDesired}, &Peer{Lane: lane2, Seen: at}, t0, 0, 0)

	equal(t, verdict.Why, "stopped at "+at.In(pacificZone).Format("3:04 PM Jan 2"))
}

func TestAnOrcaWorkerThatSucceededIsRetiredBeforeItsOutcomeLineArrives(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	finished := who(lane2, false, t0.Add(-mins(20)))
	finished.Succeeded = true
	f.peers = []Row{finished}

	out := tick(t, s, f, t0)

	equal(t, len(out.Relaunched), 0)
	equal(t, row(t, s, lane2).RetiredBy, retiredWorkerDone)
}

func TestALaneWhoseOrcaWorkerFailedStillRelaunches(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.feed = append(f.feed, rec(2, runner, "report", t0.Add(-mins(14)), "OUTCOME msg_b21ededd168d conflicts-2: worker_done failed dispatch=ctx_320e271b3112: codex exited 1"))
	f.peers = []Row{who(lane2, false, t0.Add(-mins(20)))}

	out := tick(t, s, f, t0)

	equal(t, out.Relaunched[0].Old, lane2)
}

func standingBrief(t *testing.T, name string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), name+".brief.md")
	text := fmt.Sprintf("# %s\nRegister slice: lane=%s step=tooling\nStanding: refresh the owner's board every pass\n\nDeliverable: every board row current at its head\n", name, name)
	if err := os.WriteFile(path, []byte(text), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

const refresher = "board-refresher-6"

func standingLane(t *testing.T, s *store.Store) (*fake, string) {
	t.Helper()
	path := standingBrief(t, refresher)
	f := newFake(t, []Record{spawned(1, refresher, path, t0.Add(-2*time.Hour), nil)}, []Row{who(refresher, true, t0.Add(-time.Hour))})
	tick(t, s, f, t0.Add(-time.Hour))
	f.posts = nil
	return f, path
}

func TestAStandingLaneDoneWithNoSuccessorReachesRootOnceAfterTenMinutes(t *testing.T) {
	s := memory(t)
	f, path := standingLane(t, s)
	f.feed = append(f.feed, rec(2, refresher, "done", t0, "retired"))
	f.peers = nil

	early := tick(t, s, f, t0.Add(mins(9)))
	equal(t, early, Outcome{})
	equal(t, len(f.ofKind("blocker")), 0)
	tick(t, s, f, t0.Add(mins(11)))
	tick(t, s, f, t0.Add(mins(40)))

	blockers := f.ofKind("blocker")
	equal(t, len(blockers), 1)
	contains(t, blockers[0].Text, "standing lane board-refresher-6 (refresh the owner's board every pass) has had no live owner since 10:00 AM")
	contains(t, blockers[0].Text, "retired by done")
	contains(t, blockers[0].Text, path)
}

func TestAStandingLaneStoodDownForALiveSuccessorRaisesNothing(t *testing.T) {
	s := memory(t)
	f, _ := standingLane(t, s)
	f.feed = append(f.feed, ask(2, "stand-down", refresher, t0, nil, "", map[string]string{"successor": "board-refresher-7", "last-read": "1"}))
	f.peers = []Row{who("board-refresher-7", true, t0.Add(mins(1)))}

	for _, later := range []int{1, 15, 30} {
		tick(t, s, f, t0.Add(mins(later)))
		f.peers = []Row{who("board-refresher-7", true, t0.Add(mins(later)))}
	}

	equal(t, len(f.ofKind("blocker")), 0)
	equal(t, row(t, s, "board-refresher-7").State, store.LaneDesired)
}

func TestAStandingLaneDeadWhileItWaitsOnRootReachesRootWithItsBrief(t *testing.T) {
	s := memory(t)
	f, _ := standingLane(t, s)
	f.feed = append(f.feed, rec(2, refresher, "ask", t0.Add(-mins(30)), "which card first?", Root))
	f.peers = []Row{who(refresher, false, t0.Add(-mins(30)))}

	tick(t, s, f, t0)

	blockers := f.ofKind("blocker")
	equal(t, len(blockers), 1)
	contains(t, blockers[0].Text, "is neither live nor heard since 9:30 AM")
	equal(t, len(f.launches), 0)
}

func TestABriefWithoutAStandingLineIsNeverADuty(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.feed = append(f.feed, rec(2, lane2, "done", t0, "finished"))
	f.peers = nil

	tick(t, s, f, t0.Add(time.Hour))

	equal(t, len(f.ofKind("blocker")), 0)
}

func TestTheLivenessAnswerCarriesEachLanesStepDutyAndDeliverableFromItsBrief(t *testing.T) {
	s := memory(t)
	path, plain := standingBrief(t, refresher), writeBrief(t)
	for name, brief := range map[string]string{refresher: path, lane2: plain} {
		if _, err := s.WantLane(store.Lane{Name: name, Brief: brief, Model: "opus", Effort: "xhigh", Role: "lane", State: store.LaneDesired}, t0); err != nil {
			t.Fatal(err)
		}
	}

	rows, err := LivenessRows(s)
	if err != nil {
		t.Fatal(err)
	}

	byName := map[string]Liveness{}
	for _, one := range rows {
		byName[one.Lane] = one
	}
	one := byName[refresher]
	equal(t, []string{one.Step, one.Standing, one.Deliverable, *one.Brief}, []string{"tooling", "refresh the owner's board every pass", "every board row current at its head", path})
	equal(t, []string{byName[lane2].Standing, byName[lane2].Deliverable}, []string{"", ""})
}

func TestTheLanesVerbAnswersForTheNamedLanesWithTheirNewestRecord(t *testing.T) {
	s := memory(t)
	at := time.Date(2026, 11, 2, 23, 0, 0, 0, time.UTC)
	for _, name := range []string{"gateway-gemini-1", "work-system-build-1"} {
		if _, err := s.WantLane(store.Lane{Name: name, Brief: "/briefs/" + name + ".md", Model: "opus", Effort: "xhigh", Role: "lane", State: store.LaneDesired}, at); err != nil {
			t.Fatal(err)
		}
	}
	if err := s.LaneHeard("gateway-gemini-1", at, "head", 342601); err != nil {
		t.Fatal(err)
	}

	lines, err := RegistryLines(s, []string{"gateway-gemini-1", "never-launched-1"})
	if err != nil {
		t.Fatal(err)
	}

	equal(t, len(lines), 2)
	if !strings.HasPrefix(lines[0], "gateway-gemini-1 desired live heard ") || !strings.Contains(lines[0], " head #342601 since ") {
		t.Fatal(lines[0])
	}
	equal(t, lines[1], "never-launched-1 unregistered")
}

func TestRouteFollowsTheSuccessorChainToItsLiveEndOrRoot(t *testing.T) {
	s, brief := memory(t), writeBrief(t)
	for name, state := range map[string]store.LaneState{"a-1": store.LaneRetired, "a-2": store.LaneDesired, "b-1": store.LaneRetired} {
		if _, err := s.WantLane(store.Lane{Name: name, Brief: brief, Model: "opus", Effort: "xhigh", Role: "lane", State: state}, t0); err != nil {
			t.Fatal(err)
		}
	}
	if err := s.SetLaneState("a-1", store.LaneRetired, t0, "a-2", ""); err != nil {
		t.Fatal(err)
	}

	for name, want := range map[string]string{"a-1": "a-2", "a-2": "a-2", "b-1": Root, "unknown": "unknown"} {
		route, err := Route(s, name)
		if err != nil {
			t.Fatal(err)
		}
		equal(t, route, want)
	}
}

func TestAStandDownOfAStandingLaneWithNoSuccessorIsRefused(t *testing.T) {
	s := memory(t)
	path := standingBrief(t, refresher)
	if _, err := s.WantLane(store.Lane{Name: refresher, Brief: path, Model: "opus", Effort: "xhigh", Role: "lane", State: store.LaneDesired}, t0); err != nil {
		t.Fatal(err)
	}

	refusal, err := StandingRefusal(s, Request{Verb: "stand-down", Lane: refresher})
	if err != nil {
		t.Fatal(err)
	}
	contains(t, refusal, "it stands down only for a successor")

	for _, allowed := range []Request{{Verb: "stand-down", Lane: refresher, Successor: "board-refresher-7"}, {Verb: "done", Lane: refresher}, {Verb: "stand-down", Lane: "unknown"}} {
		refusal, err := StandingRefusal(s, allowed)
		if err != nil {
			t.Fatal(err)
		}
		equal(t, refusal, "")
	}
}

func TestADoneWhileTheLaneOwnsAnOpenPRIsRefusedNamingItAndTheBlockedForm(t *testing.T) {
	s := memory(t)
	if err := s.Own(lane2, []int{12, 13}); err != nil {
		t.Fatal(err)
	}
	states := func(states map[int]string) func([]int) (map[int]string, error) {
		return func([]int) (map[int]string, error) { return states, nil }
	}

	refusal, err := DoneRefusal(s, lane2, states(map[int]string{12: Closed, 13: NeedsWork}))
	if err != nil {
		t.Fatal(err)
	}
	contains(t, refusal, "open: #13\n")
	contains(t, refusal, "cci post --lane conflicts-2 --kind blocked --pr 13 --to <your desk>")

	for lane, found := range map[string]map[int]string{lane2: {12: Closed, 13: Closed}, "never-1": {}} {
		refusal, err := DoneRefusal(s, lane, states(found))
		if err != nil {
			t.Fatal(err)
		}
		equal(t, refusal, "")
	}
}

func TestARequestPostsOneTypedDecideToTheSupervisor(t *testing.T) {
	draft := Request{Verb: "stand-down", Lane: lane2, Successor: "conflicts-3", LastRead: "7", PRs: []int{1, 2}, By: Root}.Draft(testDrive)

	equal(t, draft, client.Draft{Drive: testDrive, Lane: Root, Kind: "decide", To: []string{Self}, Attrs: map[string]string{"verb": "stand-down", "lane": lane2, "successor": "conflicts-3", "last-read": "7"}, PRs: []string{"1", "2"}, Stamp: true, Text: "registry request: stand-down conflicts-2 for conflicts-3 last-read=7 #1 #2"})
}

func TestBriefParsing(t *testing.T) {
	said := parseBrief("# x\nRegister slice: lane=x step=B13\nStanding: keep it up   \n\n**Deliverable:**\n  the thing\n")
	equal(t, said, Brief{Step: "B13", Standing: "keep it up", Deliverable: "the thing"})
	equal(t, parseBrief("nothing"), Brief{})
}

func TestNamesVerbReadsAStandDownOnlyWhenNoWordFollowsIt(t *testing.T) {
	cases := map[string]bool{"stand-down": true, "stand-down.": true, "stand-down conflicts-2": false, "closed": true, "the chase": false}
	for text, want := range cases {
		equal(t, namesVerb(text), want)
	}
}

func coordinator(t *testing.T, s *store.Store, brief string) *fake {
	t.Helper()
	f := newFake(t, []Record{spawned(1, Root, brief, t0.Add(-2*time.Hour), map[string]string{"model": "opus", "effort": "xhigh"})}, []Row{who(Root, true, t0.Add(-time.Hour))})
	tick(t, s, f, t0.Add(-time.Hour))
	f.posts = nil
	return f
}

func TestADeadCoordinatorRelaunchesFromItsBriefUnderItsOwnNameAndTellsTheBridge(t *testing.T) {
	s, brief := memory(t), writeBrief(t)
	f := coordinator(t, s, brief)
	f.peers = []Row{who(Root, false, t0.Add(-mins(20)))}

	out := tick(t, s, f, t0)

	equal(t, f.launches, []string{Root})
	equal(t, out.Relaunched, []Relaunch{{Root, Root, "stopped at 9:40 AM"}})
	equal(t, f.kinds(), []string{"report:" + OwnerRelay})
	equal(t, row(t, s, Root).State, store.LaneLaunching)
	if _, found, _ := s.Lane("root-2"); found {
		t.Fatal("a numbered coordinator")
	}
	text := f.briefs[Root]
	contains(t, text, "You are root, this drive's coordinator, in a new session.")
	contains(t, text, "root left no handoff")
	body, _ := os.ReadFile(brief)
	if !strings.HasSuffix(text, string(body)) {
		t.Fatal("the brief must end with the coordinator's brief")
	}
	f.posts = nil
	f.peers = []Row{who(Root, true, t0.Add(mins(2)))}

	tick(t, s, f, t0.Add(mins(2)))

	equal(t, row(t, s, Root).State, store.LaneDesired)
	equal(t, len(f.posts), 0)
}

func TestACoordinatorThatDiesAgainSoonAfterItsRelaunchIsHeldForTheBridge(t *testing.T) {
	s := memory(t)
	f := coordinator(t, s, writeBrief(t))
	f.peers = []Row{who(Root, false, t0.Add(-mins(20)))}
	tick(t, s, f, t0)
	f.peers = []Row{who(Root, true, t0.Add(mins(2)))}
	tick(t, s, f, t0.Add(mins(2)))
	f.posts = nil
	f.peers = []Row{who(Root, false, t0.Add(mins(3)))}

	out := tick(t, s, f, t0.Add(mins(19)))

	equal(t, out.Held, []string{Root})
	equal(t, f.launches, []string{Root})
	equal(t, f.kinds(), []string{"blocker:" + OwnerRelay})
	contains(t, f.posts[0].Text, "it died within 30 minutes of its relaunch")
}

func TestACoordinatorWithNoProcessRowIsHeldForTheBridge(t *testing.T) {
	s := memory(t)
	f := coordinator(t, s, writeBrief(t))
	f.peers = nil

	out := tick(t, s, f, t0)

	equal(t, out.Held, []string{Root})
	equal(t, f.kinds(), []string{"blocker:" + OwnerRelay})
}

func TestASubDrivesCoordinatorTellsItsParentAndNeverTheCommunicator(t *testing.T) {
	t.Run("relaunched", func(t *testing.T) {
		s, brief := memory(t), writeBrief(t)
		f := coordinator(t, s, brief)
		f.deps.Parent = "brook"
		f.peers = []Row{who(Root, false, t0.Add(-mins(20)))}

		tick(t, s, f, t0)

		equal(t, f.launches, []string{Root})
		equal(t, f.kinds(), []string{"report:root@brook"})
	})
	t.Run("held", func(t *testing.T) {
		s := memory(t)
		f := coordinator(t, s, writeBrief(t))
		f.deps.Parent = "brook"
		f.peers = nil

		out := tick(t, s, f, t0)

		equal(t, out.Held, []string{Root})
		equal(t, f.kinds(), []string{"blocker:root@brook"})
	})
}

func TestACoordinatorsOwnStandDownRecordAboutALaneNeverRetiresTheCoordinator(t *testing.T) {
	for _, kind := range []string{"stand-down", "stopped", "done", "handoff"} {
		s, brief := memory(t), writeBrief(t)
		f := coordinator(t, s, brief)
		f.feed = append(f.feed, rec(2, Root, kind, t0, "root: other-lane is finished", "other-lane"))
		f.peers = []Row{who(Root, true, t0)}

		tick(t, s, f, t0)

		equal(t, row(t, s, Root).State, store.LaneDesired)
	}
}

func TestACoordinatorHandoffRecordIsTheHandoffItsRelaunchReads(t *testing.T) {
	s := memory(t)
	f := coordinator(t, s, writeBrief(t))
	f.feed = append(f.feed, rec(2, Root, "handoff", t0.Add(-mins(30)), "flushed", OwnerRelay).withRefs(Refs{CCN: "9a1b2c3"}))
	f.peers = []Row{who(Root, false, t0.Add(-mins(20)))}

	tick(t, s, f, t0)

	contains(t, f.briefs[Root], "doc show 9a1b2c3")
}

func registered(t *testing.T, s *store.Store, lane store.Lane) {
	t.Helper()
	lane.Brief, lane.Model, lane.Effort = "/brief.md", "opus", "xhigh"
	if lane.State == "" {
		lane.State = store.LaneDesired
	}
	if _, err := s.WantLane(lane, t0); err != nil {
		t.Fatal(err)
	}
}

func TestTheOwningDeskIsTheNewestLiveLaneOfWhoeverLaunchedTheLane(t *testing.T) {
	const worker, desk, nextDesk = "board-mirror-1", "meta-desk-1", "meta-desk-2"
	launchedBy := map[string]string{worker: desk}
	cases := []struct {
		name  string
		lanes []store.Lane
		desks map[string]string
		want  string
	}{
		{"a live launcher owns it", []store.Lane{{Name: worker}, {Name: desk}}, launchedBy, desk},
		{"its successor takes the lane over", []store.Lane{{Name: worker}, {Name: desk, State: store.LaneRetired, RetiredBy: nextDesk}, {Name: nextDesk}}, launchedBy, nextDesk},
		{"a relaunch keeps its predecessor's desk", []store.Lane{{Name: "board-mirror-2", Predecessor: worker}, {Name: worker, State: store.LaneRetired}, {Name: desk}}, launchedBy, desk},
		{"root launched it", []store.Lane{{Name: worker}, {Name: Root}}, map[string]string{worker: Root}, Root},
		{"nobody launched it", []store.Lane{{Name: worker}}, nil, Root},
		{"its launcher is no registered lane", []store.Lane{{Name: worker}}, launchedBy, Root},
		{"its launcher retired with no successor", []store.Lane{{Name: worker}, {Name: desk, State: store.LaneRetired, RetiredBy: "done"}}, launchedBy, Root},
		{"its launcher is held", []store.Lane{{Name: worker}, {Name: desk, State: store.LaneHeld}}, launchedBy, Root},
		{"a lane never owns itself", []store.Lane{{Name: worker}}, map[string]string{worker: worker}, Root},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			s := memory(t)
			for _, lane := range c.lanes {
				registered(t, s, lane)
				if lane.State == store.LaneRetired {
					retire(s, lane.Name, lane.RetiredBy, t0)
				}
			}
			d := Deps{Desk: func(lane string) (string, error) { return c.desks[lane], nil }}
			equal(t, owningDesk(s, d, c.lanes[0].Name), c.want)
		})
	}
}

func TestAHeldLanesBlockerReachesItsDeskAndNeverRoot(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.desks = map[string]string{lane2: "meta-desk-1"}
	registered(t, s, store.Lane{Name: "meta-desk-1"})
	f.feed = append(f.feed, rec(2, lane2, "state", t0, "restacking", Root))
	f.peers = []Row{who("meta-desk-1", true, t0.Add(mins(16)))}

	tick(t, s, f, t0.Add(mins(16)))

	equal(t, f.kinds(), []string{"blocker:meta-desk-1"})
	contains(t, f.posts[0].Text, "nothing proves it died")
}

func TestARelaunchIsReportedToTheDeskThatOwnsTheLane(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "9f3c2e1")
	f.desks = map[string]string{lane2: "meta-desk-1"}
	registered(t, s, store.Lane{Name: "meta-desk-1"})
	f.peers = []Row{who(lane2, false, t0.Add(-mins(20))), who("meta-desk-1", true, t0)}

	out := tick(t, s, f, t0)

	equal(t, len(out.Relaunched), 1)
	equal(t, f.kinds(), []string{"report:meta-desk-1"})
}

func TestALaneThatPostsStoppedAsAStateIsRetiredAndNeverGetsASuccessor(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.feed = append(f.feed, rec(2, lane2, "state", t0, "7:26 PM STOPPED conflicts-2: out of scope, no customer failures", Root))
	f.peers = []Row{who(lane2, false, t0)}

	out := tick(t, s, f, t0.Add(mins(16)))

	equal(t, len(out.Relaunched)+len(f.launches), 0)
	equal(t, row(t, s, lane2).State, store.LaneRetired)
	equal(t, row(t, s, lane2).RetiredBy, "stand-down")
}

func TestAStateThatOnlyMentionsStoppedLeavesTheLaneWorking(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.feed = append(f.feed, rec(2, lane2, "state", t0, "7:26 PM the runner STOPPED at the gate, retrying", Root))
	f.peers = []Row{who(lane2, true, t0)}

	tick(t, s, f, t0.Add(mins(1)))

	equal(t, row(t, s, lane2).State, store.LaneDesired)
}

func TestAStandDownAfterARelaunchWasQueuedDropsTheQueuedLaunchAndGivesRootThePRs(t *testing.T) {
	cases := map[string]func(heir string, at time.Time) Record{
		"a registry request names the old lane": func(_ string, at time.Time) Record { return ask(2, "stand-down", lane2, at, nil, "", nil) },
		"a registry request names the heir": func(heir string, at time.Time) Record {
			return ask(2, "stand-down", heir, at, nil, "", map[string]string{"last-read": "0"})
		},
		"a root stand-down names the old lane": func(_ string, at time.Time) Record { return rec(2, Root, "stand-down", at, "stand down", lane2) },
		"a root stand-down names the heir": func(heir string, at time.Time) Record {
			return rec(2, Root, "stand-down", at, "stand down, last-read=0", heir)
		},
	}
	for name, standDown := range cases {
		t.Run(name, func(t *testing.T) {
			s := memory(t)
			f := adopted(t, s, writeBrief(t), "9f3c2e1", 46001)
			f.peers = []Row{who(lane2, false, t0.Add(-mins(20)))}
			heir := tick(t, s, f, t0).Relaunched[0].New
			f.feed = append(f.feed, standDown(heir, t0.Add(mins(1))))

			out := tick(t, s, f, t0.Add(mins(2)))

			equal(t, out.Withdrawn, []string{heir})
			equal(t, row(t, s, heir).State, store.LaneRetired)
			owners, err := s.Owners()
			if err != nil {
				t.Fatal(err)
			}
			equal(t, owners[Root], []int{46001})
		})
	}
}

func TestAStandDownNamingASuccessorKeepsTheQueuedLaunch(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "9f3c2e1")
	f.peers = []Row{who(lane2, false, t0.Add(-mins(20)))}
	heir := tick(t, s, f, t0).Relaunched[0].New
	f.feed = append(f.feed, ask(2, "stand-down", lane2, t0.Add(mins(1)), nil, "", map[string]string{"successor": "conflicts-9"}))

	out := tick(t, s, f, t0.Add(mins(2)))

	equal(t, len(out.Withdrawn), 0)
	equal(t, row(t, s, heir).State, store.LaneLaunching)
}

func TestAWithdrawnLaunchRetiresItsRowButTheSupervisorsOwnWithdrawalDoesNot(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	registered(t, s, store.Lane{Name: "release-recut-2", State: store.LaneLaunching})
	registered(t, s, store.Lane{Name: "handed-1", State: store.LaneLaunching})
	f.feed = append(f.feed,
		rec(2, Root, "go", t0, "R9 (5:00 PM) orca-desk: withdraw release-recut-2", "orca-desk"),
		rec(3, Self, "go", t0, "R10 (5:00 PM) orca-desk: withdraw handed-1", "orca-desk"),
	)
	f.peers = []Row{who(lane2, true, t0)}

	tick(t, s, f, t0.Add(mins(1)))

	equal(t, row(t, s, "release-recut-2").State, store.LaneRetired)
	equal(t, row(t, s, "handed-1").State, store.LaneLaunching)
}

func TestTheHourlyReportNamesEachLanesOpenWorkDeepestFirst(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.work = []Record{
		rec(10, Root, "defect", t0.Add(-3*time.Hour), "fix the gate", "tooling-1"),
		rec(11, Root, "ask", t0.Add(-time.Hour), "due time?", "tooling-1@drive"),
		rec(12, Root, "defect", t0.Add(-mins(30)), "and this", "tooling-1", Root),
		rec(13, "desk-runner", "blocker", t0.Add(-2*time.Hour), "stuck", "desk-3"),
		rec(14, "lane-x", "ask", t0, "the coordinator only", Root),
	}
	at := t0.Add(mins(61))
	f.peers = []Row{who(lane2, true, at)}

	tick(t, s, f, at)

	equal(t, f.kinds(), []string{"report:root"})
	equal(t, f.posts[0].Text, fmt.Sprintf("%s open work by lane, so no queue grows unseen (retro row 133): tooling-1 2 defects, 1 ask (oldest %s); desk-3 1 blocker (oldest %s).", pacific(at), pacific(t0.Add(-3*time.Hour)), pacific(t0.Add(-2*time.Hour))))
}

func TestAnUnreadOpenWorkQueryLeavesTheHourlyTickRunning(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.deps.OpenWork = func() ([]Record, error) { return nil, fmt.Errorf("%w: cci tail", ErrUnread) }
	at := t0.Add(mins(61))
	f.peers = []Row{who(lane2, true, at)}

	tick(t, s, f, at)

	equal(t, len(f.posts), 0)
}
