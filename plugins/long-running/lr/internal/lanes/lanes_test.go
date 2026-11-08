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
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/owner"
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
func (r Record) withCard(id string) Record {
	r.Fields.Attrs = map[string]string{CardAttr: id}
	return r
}

func (r Record) withResolves(seq int64) Record {
	r.Resolves = seq
	return r
}

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
	lastWords   string
	feed        []Record
	peers       []Row
	tokens      map[string]int
	posts       []Post
	launches    []string
	keys        []string
	costs       map[string]Cost
	briefs      map[string]string
	withdrawn   []string
	wakes       []string
	pages       []map[string]any
	states      map[int]string
	holds       map[int]bool
	desks       map[string]string
	work        []Record
	owed        []deletions.Owed
	notes       map[string]error
	published   []string
	gaps        []string
	orders      []owner.Row
	undelivered []owner.Row
	promises    []Promise
	branches    map[string][]int
	branchReads int
	foreign     map[int]bool
	oursReads   int
	hosts       map[string]Host
	hostReads   int
	sprites     map[string]Sprite
	spriteReads int
	spritesFail error
	carried     map[string]Carried
	deps        Deps
}

func newFake(t *testing.T, records []Record, peers []Row) *fake {
	t.Helper()
	f := &fake{feed: records, peers: peers, costs: map[string]Cost{}, tokens: map[string]int{}, states: map[int]string{}, holds: map[int]bool{}, briefs: map[string]string{}}
	f.deps = Deps{
		Drive: testDrive, Checkout: testCheckout, Briefs: filepath.Join(t.TempDir(), "lanes"), Bin: "/plugin/bin", HandoffBrief: "/plugin/handoff-subagent-brief.md",
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
		Launch: func(name, model, effort, brief, key string) error {
			f.keys = append(f.keys, key)
			f.launches = append(f.launches, name)
			f.costs[name] = Cost{model, effort}
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
		Held:         func() (map[int]bool, error) { return f.holds, nil },
		Desks:        func() (map[string]string, error) { return f.desks, nil },
		OpenWork:     func() ([]Record, error) { return f.work, nil },
		Owed:         func() ([]deletions.Owed, error) { return slices.Clone(f.owed), nil },
		Present:      func(rows []deletions.Owed) ([]deletions.Owed, error) { return rows, nil },
		PublishNotes: func(lane string) error { f.published = append(f.published, lane); return f.notes[lane] },
		Gaps:         func() ([]string, error) { return f.gaps, nil },
		Orders:       func(*store.Store) ([]owner.Row, error) { return f.orders, nil },
		Undelivered:  func() ([]owner.Row, error) { return f.undelivered, nil },
		Promises:     func() ([]Promise, error) { return f.promises, nil },
		RotateAt:     600_000,
		RetireAfter:  time.Hour,
		OpenBranches: func(*store.Store) (map[string][]int, error) { f.branchReads++; return f.branches, nil },
		Ours: func(_ *store.Store, prs []int) ([]int, error) {
			f.oursReads++
			return slices.DeleteFunc(slices.Clone(prs), func(n int) bool { return f.foreign[n] }), nil
		},
		Sprites: func(names []string) (map[string]Sprite, error) {
			f.spriteReads++
			out := map[string]Sprite{}
			for _, name := range names {
				if sprite, ok := f.sprites[name]; ok {
					out[name] = sprite
				}
			}
			return out, f.spritesFail
		},
		LastWords: func(string) string { return f.lastWords },
		Carried:   func(lane string) (Carried, error) { return f.carried[lane], nil },
		Hosts: func(names []string, _ map[string]Sprite) map[string]Host {
			f.hostReads++
			out := map[string]Host{}
			for _, name := range names {
				if host, ok := f.hosts[name]; ok {
					out[name] = host
				}
			}
			return out
		},
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

func TestADeadLaneRelaunchesWithItsHandoffAndIsToldItStoodDownAtOnce(t *testing.T) {
	s, brief := memory(t), writeBrief(t)
	f := adopted(t, s, brief, "9f3c2e1", 46001, 46002)
	workers, err := s.Workers()
	if err != nil || len(workers) != 1 || workers[0].Name != lane2 {
		t.Fatalf("workers %v err %v", workers, err)
	}
	f.peers = []Row{who(lane2, false, t0.Add(-mins(20))), who("conflicts-3", false, t0.Add(-9*time.Hour))}

	out := tick(t, s, f, t0)

	equal(t, out.Relaunched, []Relaunch{{lane2, "conflicts-4", "stopped, quiet for 20 min"}})
	equal(t, f.launches, []string{"conflicts-4"})
	contains(t, f.posts[0].Text, "RELAUNCHED conflicts-2 as conflicts-4: it stopped, quiet for 20 min; handoff 9f3c2e1; PRs #46001 #46002; brief ")
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
	equal(t, f.kinds(), []string{"report:root", "stand-down:" + lane2})
	standDown := f.posts[1]
	equal(t, []any{standDown.Successor, standDown.PRs}, []any{"conflicts-4", []int{46001, 46002}})
	equal(t, standDown.Text, "conflicts-2 stood down for conflicts-4; conflicts-2 stopped, quiet for 20 min.")
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

	equal(t, len(f.posts), 0)
	equal(t, row(t, s, "conflicts-4").State, store.LaneDesired)
}

func TestADeadLaneWhosePredecessorStillRunsGetsASuccessorAndNeverStandsDownForThePredecessor(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "", 46001)
	f.peers = []Row{who(lane2, false, t0.Add(-mins(30))), who("conflicts-1", true, t0)}

	tick(t, s, f, t0)

	equal(t, f.launches, []string{"conflicts-3"})
	equal(t, row(t, s, lane2).RetiredBy, "conflicts-3")
}

func TestARelaunchBriefKeepsTheLanesPlacementAndCarriesItsRegisterSlice(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "", 46001)
	f.peers = []Row{who(lane2, false, t0.Add(-mins(30)))}

	tick(t, s, f, t0)

	equal(t, strings.Split(f.briefs["conflicts-3"], "\n")[:3], []string{"Replaces: conflicts-2", "ccx: place=remote", "Register slice: lane=conflicts-3 step=tooling"})
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
		f := adopted(t, s, brief, "", 46001)
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
	f := adopted(t, s, writeBrief(t), "", 46001)
	f.peers = []Row{who(lane2, false, t0.Add(-mins(30)))}

	tick(t, s, f, t0)

	text := f.briefs["conflicts-3"]
	contains(t, text, "conflicts-2 left no handoff")
	contains(t, text, "named `conflicts-2-handoff`")
	contains(t, text, "/projects/x/d50e80e8/subagents/agent-aconflicts-2-0123456789abcdef.jsonl")
	contains(t, text, f.deps.HandoffBrief)
}

func TestASuccessorBriefRunsLaneStatusByPathAndStopsOnlyOnAPredecessorWithATurnRunning(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "", 46001)
	f.peers = []Row{who(lane2, false, t0.Add(-mins(30)))}

	tick(t, s, f, t0)

	text := f.briefs["conflicts-3"]
	contains(t, text, "run `/plugin/bin/drive.py lane-status conflicts-2 --held`")
	contains(t, text, "`cci who --drive "+testDrive+" --lane conflicts-2 --json --fields lane,live,status,status_at`")
	contains(t, text, "a live session whose lane is `conflicts-2` and whose status is not `idle`")
	contains(t, text, "An idle session or none writes nothing")
	for _, gone := range []string{"lsof", "pids"} {
		if strings.Contains(text, gone) {
			t.Fatalf("the brief still reads processes in the worktree (%q):\n%s", gone, text)
		}
	}
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
	contains(t, replaced.Relaunched[0].Why, "has made no tool call in the 30 min since it was woken because blocker #2")
	equal(t, f.launches, []string{"conflicts-3"})
	equal(t, owners(t, s, 46475)[46475], "conflicts-3")
}

func TestAWokenLaneParkedInABackgroundLrWaitIsReplacedOnlyAfterItsDeadline(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "", 46475)
	f.feed = append(f.feed, stall(2, t0.Add(-mins(30)), "STALL red #46475", 46475, "red:46475:89f46c3"))
	f.peers = []Row{onRoster(who(lane2, true, t0.Add(-2*time.Hour)))}
	transcript := filepath.Join(t.TempDir(), "lane.jsonl")
	parked := fmt.Sprintf(`{"type":"assistant","session_id":"p","timestamp":%q,"message":{"content":[{"type":"tool_use","id":"w1","name":"Bash","input":{"command":"lr wait 3600 cci:%s:%s -- wait-land.zsh 46475","run_in_background":true}}]}}`, t0.Add(-mins(10)).Format(time.RFC3339), testDrive, lane2)
	if err := os.WriteFile(transcript, []byte(parked+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	f.deps.Transcript = func(Row) string { return transcript }
	tick(t, s, f, t0)

	waiting := tick(t, s, f, t0.Add(PageAnswer))
	stillWaiting := tick(t, s, f, t0.Add(mins(49)))
	replaced := tick(t, s, f, t0.Add(mins(51)))

	equal(t, len(waiting.Relaunched)+len(stillWaiting.Relaunched), 0)
	equal(t, replaced.Relaunched[0].Old, lane2)
	equal(t, f.launches, []string{"conflicts-3"})
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
	f := adopted(t, s, writeBrief(t), "", 46001)
	f.peers = []Row{who(lane2, false, t0.Add(-mins(20))), who(Root, true, t0), who("p8a-step7", true, t0.Add(-2*time.Hour))}

	tick(t, s, f, t0)

	equal(t, f.kinds(), []string{"report:root", "stand-down:" + lane2})
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

func TestABuildLanesBlockedOrReadyRecordClaimsOnlyAPRNoLaneOwns(t *testing.T) {
	for _, kind := range []string{"blocked", "ready"} {
		t.Run(kind, func(t *testing.T) {
			s, brief := memory(t), writeBrief(t)
			build := map[string]string{"role": "build"}
			f := newFake(t, []Record{
				spawned(1, lane2, brief, t0.Add(-time.Hour), build),
				spawned(2, "replicator-1", brief, t0.Add(-time.Hour), build),
				rec(3, "replicator-1", "opened", t0, "").withRefs(prs(46002)),
				rec(4, lane2, kind, t0, "").withRefs(prs(46001, 46002)),
			}, []Row{who(lane2, true, t0), who("replicator-1", true, t0)})

			tick(t, s, f, t0)

			equal(t, owners(t, s, 46001, 46002), map[int]string{46001: lane2, 46002: "replicator-1"})
		})
	}
}

func TestADeskNamingAPRNoLaneOwnsLeavesItUnowned(t *testing.T) {
	s, brief := memory(t), writeBrief(t)
	f := newFake(t, []Record{
		spawned(1, "go-sweep-9", brief, t0.Add(-time.Hour), map[string]string{"role": "desk"}),
		rec(2, "go-sweep-9", "blocked", t0, "").withRefs(prs(46001)),
	}, []Row{who("go-sweep-9", true, t0)})

	tick(t, s, f, t0)

	equal(t, owners(t, s, 46001), map[int]string{46001: ""})
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
	f := adopted(t, s, writeBrief(t), "", 46001)
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
			f := adopted(t, s, writeBrief(t), "", 46001)
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

func TestASuccessorSpawnedAfterItsStandDownRecordsWhereAndHowItWasLaunched(t *testing.T) {
	s := memory(t)
	f := newFake(t, []Record{spawned(1, lane2, writeBrief(t), t0.Add(-time.Hour), map[string]string{"model": "sonnet", "effort": "low", "placement": placedOnSprite})}, []Row{who(lane2, true, t0.Add(-mins(1)))})
	tick(t, s, f, t0.Add(-mins(1)))
	f.feed = append(f.feed,
		ask(2, "stand-down", lane2, t0, nil, "", map[string]string{"successor": "conflicts-3", "last-read": "1"}),
		spawned(3, "conflicts-3", writeBrief(t), t0, map[string]string{"model": "sonnet", "effort": "high", "placement": "orca"}),
	)
	f.peers = []Row{who("conflicts-3", true, t0)}

	tick(t, s, f, t0)

	successor := row(t, s, "conflicts-3")
	equal(t, []string{successor.Model, successor.Effort, successor.Placement}, []string{"sonnet", "high", "orca"})
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

	equal(t, f.kinds(), []string{"stand-down:conflicts-2", "report:" + OwnerRelay})
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

func TestALaneThatRegistersWithNoRowIsAdoptedFromItsBriefUnlessItIsAHelper(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.deps.State = t.TempDir()
	briefs := map[string]string{"turn-error-91-fix-1": "ccx: role=fix place=remote\n", "turn-error-91-evidence-1": "ccx: role=evidence place=local why=datadog\n", "port-audit-1": "# port audit\n"}
	for lane, text := range briefs {
		path := filepath.Join(f.deps.State, lane, "brief.md")
		if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(text), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	onSprite := rec(2, "turn-error-91-fix-1", "state", t0, "turn-error-91-fix-1"+firstCommand)
	onSprite.Origin = "79af6f75210100fe"
	f.feed = append(f.feed, onSprite, rec(3, "turn-error-91-evidence-1", "state", t0, "turn-error-91-evidence-1"+firstCommand), rec(4, "port-audit-1", "state", t0, "port-audit-1"+firstCommand), rec(5, lane2, "state", t0, lane2+firstCommand), rec(6, "no-brief-1", "state", t0, "no-brief-1"+firstCommand))
	f.peers = append(f.peers, who("turn-error-91-fix-1", true, t0), who("turn-error-91-evidence-1", true, t0), who("port-audit-1", true, t0), who("no-brief-1", true, t0))

	out := tick(t, s, f, t0)

	equal(t, out.Adopted, []string{"turn-error-91-fix-1", "port-audit-1"})
	fix, audit := row(t, s, "turn-error-91-fix-1"), row(t, s, "port-audit-1")
	equal(t, []any{fix.State, fix.Brief, fix.Model, fix.Effort, fix.Role, fix.Placement}, []any{store.LaneDesired, filepath.Join(f.deps.State, "turn-error-91-fix-1", "brief.md"), "opus", "high", "fix", placedOnSprite})
	equal(t, []any{audit.State, audit.Role, audit.Placement}, []any{store.LaneDesired, "lane", ""})
	equal(t, row(t, s, lane2).Effort, "xhigh")
	for _, name := range []string{"turn-error-91-evidence-1", "no-brief-1"} {
		if _, found, _ := s.Lane(name); found {
			t.Fatalf("%s was adopted", name)
		}
	}
	equal(t, len(f.launches)+len(f.posts), 0)
}

func TestASpawnedRecordAfterARegistrationSetsTheLauncherRoleAndCostOfTheAdoptedRow(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.deps.State = t.TempDir()
	brief := filepath.Join(f.deps.State, "board-audit-1", "brief.md")
	if err := os.MkdirAll(filepath.Dir(brief), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(brief, []byte("# board audit\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	f.feed = append(f.feed, rec(2, "board-audit-1", "state", t0, "board-audit-1"+firstCommand))
	f.peers = append(f.peers, who("board-audit-1", true, t0))
	tick(t, s, f, t0)
	spawn := spawned(3, "board-audit-1", brief, t0.Add(mins(7)), map[string]string{"model": "sol", "effort": "high", "role": "read", "placement": "orca"})
	spawn.Lane = "meta-desk-3"
	f.feed = append(f.feed, spawn)

	tick(t, s, f, t0.Add(mins(8)))

	audit := row(t, s, "board-audit-1")
	equal(t, []any{audit.State, audit.Launcher, audit.Role, audit.Model, audit.Placement}, []any{store.LaneDesired, "meta-desk-3", "read", "sol", "orca"})
}

func TestASuccessorThatNeverComesUpEscalatesOnce(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "", 46001)
	f.peers = []Row{who(lane2, false, t0.Add(-mins(20)))}
	tick(t, s, f, t0)
	f.posts = nil

	for _, later := range []int{25, 26} {
		tick(t, s, f, t0.Add(mins(later)))
	}

	equal(t, f.kinds(), []string{"blocker:root"})
	equal(t, row(t, s, "conflicts-3").State, store.LaneHeld)
}

func TestALaneTheSupervisorRelaunchedOnceIsHeldWithADefectToRootWhenItDiesAgain(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "", 46001)
	life := []struct {
		lane     string
		live     bool
		seen, at int
	}{{lane2, false, -20, 0}, {"conflicts-3", true, 1, 1}, {"conflicts-3", false, 1, 90}}
	for _, step := range life {
		f.peers = []Row{who(step.lane, step.live, t0.Add(mins(step.seen)))}
		tick(t, s, f, t0.Add(mins(step.at)))
	}

	equal(t, f.launches, []string{"conflicts-3"})
	equal(t, row(t, s, "conflicts-3").State, store.LaneHeld)
	defects := f.ofKind("defect")
	equal(t, len(defects), 1)
	equal(t, defects[0].To, []string{Root})
	contains(t, defects[0].Text, "the supervisor already relaunched it once, when conflicts-2 stopped, quiet for 20 min")
}

func TestAMetaLanesSecondDeathRaisesItsDefectToItsLaunchersHead(t *testing.T) {
	s, brief := memory(t), writeBrief(t)
	registered(t, s, store.Lane{Name: "meta-desk-2", Role: RoleMeta})
	spawn := spawned(1, "liveness-1", brief, t0.Add(-time.Hour), nil)
	spawn.Lane = "meta-desk-2"
	f := newFake(t, []Record{spawn}, []Row{who("liveness-1", true, t0.Add(-mins(1))), who("meta-desk-2", true, t0.Add(-mins(1)))})
	tick(t, s, f, t0.Add(-mins(1)))
	if err := s.SetLanePRs("liveness-1", []int{46001}); err != nil {
		t.Fatal(err)
	}
	life := []struct {
		lane     string
		live     bool
		seen, at int
	}{{"liveness-1", false, -20, 0}, {"liveness-2", true, 1, 1}, {"liveness-2", false, 1, 90}}
	for _, step := range life {
		f.peers = []Row{who(step.lane, step.live, t0.Add(mins(step.seen))), who("meta-desk-2", true, t0.Add(mins(step.at)))}
		tick(t, s, f, t0.Add(mins(step.at)))
	}

	equal(t, row(t, s, "liveness-1").Launcher, "meta-desk-2")
	equal(t, f.launches, []string{"liveness-2"})
	equal(t, f.ofKind("report")[0].To, []string{"meta-desk-2"})
	defects := f.ofKind("defect")
	equal(t, len(defects), 1)
	equal(t, defects[0].To, []string{"meta-desk-2"})
}

func TestALaneThatClosesItsRotateAskWithAnAnswerOrAHandoffStaysLive(t *testing.T) {
	for _, kind := range []string{"answer", "handoff"} {
		s := memory(t)
		f := adopted(t, s, writeBrief(t), "")
		closed := rec(3, lane2, kind, t0, "Handoff written as cc-notes doc 5fc90b8 and posted; owner-relay keeps working.", Self)
		closed.Resolves = 2
		f.feed = append(f.feed, rec(2, Self, "ask", t0, "ROTATE: conflicts-2 is at 425,128 tokens.", lane2), closed)
		f.peers = []Row{onRoster(who(lane2, true, t0))}

		tick(t, s, f, t0.Add(mins(1)))
		tick(t, s, f, t0.Add(mins(2)))

		lane := row(t, s, lane2)
		equal(t, []any{lane.State, settledLane(lane), len(f.ofKind("stand-down"))}, []any{store.LaneDesired, false, 0})
	}
}

func TestALaneThatPostedDoneOrStoppedIsNeverRelaunchedByARecordAddressedToIt(t *testing.T) {
	for name, end := range map[string]Record{
		"done answering its ask":  rec(2, lane2, "done", t0, "done: #46001 merged", Root).withRe(1),
		"done resolving its ask":  rec(2, lane2, "done", t0, "done: #46001 merged", Root).withResolves(1),
		"stopped out of its work": rec(2, lane2, "stopped", t0, "STOPPED conflicts-2: out of scope", Root),
	} {
		t.Run(name, func(t *testing.T) {
			s := memory(t)
			f := adopted(t, s, writeBrief(t), "", 46001)
			f.feed = append(f.feed, end, rec(3, Root, "answer", t0.Add(mins(5)), "Post the notebook and STOPPED.", lane2).withRe(2))
			f.peers = []Row{{Lane: lane2, SeenAt: t0, Dispatch: "ctx_1"}}

			tick(t, s, f, t0.Add(mins(30)))
			tick(t, s, f, t0.Add(mins(31)))

			equal(t, []any{row(t, s, lane2).State, len(f.launches)}, []any{store.LaneRetired, 0})
		})
	}
}

func TestALaneThatEndedItselfStandsDownOnTheBusSoItsAliasPassesOn(t *testing.T) {
	for _, kind := range []string{"done", "stopped", "handoff"} {
		s := memory(t)
		f := adopted(t, s, writeBrief(t), "")
		f.feed = append(f.feed, rec(2, lane2, kind, t0, "finished", Root))
		f.peers = []Row{who(lane2, false, t0)}

		tick(t, s, f, t0.Add(mins(1)))
		tick(t, s, f, t0.Add(mins(2)))

		equal(t, row(t, s, lane2).State, store.LaneRetired)
		standDowns := f.ofKind("stand-down")
		equal(t, len(standDowns), 1)
		equal(t, standDowns[0].To, []string{lane2})
	}
}

func TestDoneAndARootStandDownRetireALane(t *testing.T) {
	s, brief := memory(t), writeBrief(t)
	f := adopted(t, s, brief, "")
	f.feed = append(f.feed, spawned(2, "red-sweep-3", brief, t0, nil), rec(3, lane2, "done", t0, "all five stacks restacked", Root), ask(4, "stand-down", "red-sweep-3", t0, nil, "", map[string]string{"last-read": "3"}))
	f.peers = []Row{who(lane2, false, t0), who("red-sweep-3", false, t0)}

	tick(t, s, f, t0.Add(mins(30)))

	equal(t, len(f.launches), 0)
	equal(t, f.kinds(), []string{"stand-down:red-sweep-3", "report:" + OwnerRelay, "stand-down:" + lane2})
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
	equal(t, blockers[0].Text, "The owed-deletions check did not run: rulings.py rows exited 1: no register")
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
		f := adopted(t, s, writeBrief(t), "", 46001)
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

func TestAnUnreadRequestFromAnotherLaneIsRefusedToThatLaneWithTheRetryCitingTheNewestRecord(t *testing.T) {
	const asker = "stopped-lanes-1"
	cases := []struct {
		request Record
		retry   string
	}{
		{ask(4, "owner", "restack-children-2", t0, []int{46001}, asker, nil), "lr request owner restack-children-2 --pr 46001 --last-read 3"},
		{ask(4, "stand-down", lane2, t0, nil, asker, map[string]string{"successor": "restack-children-2", "last-read": "2"}), "lr request stand-down conflicts-2 --successor restack-children-2 --last-read 3"},
	}
	for _, c := range cases {
		s, brief := memory(t), writeBrief(t)
		f := adopted(t, s, brief, "", 46001)
		f.feed = append(f.feed, spawned(2, "restack-children-2", brief, t0, nil), rec(3, lane2, "head", t0, "#46001 e590637").withRefs(prs(46001)), c.request)
		f.peers = append(f.peers, who("restack-children-2", true, t0))

		tick(t, s, f, t0)

		equal(t, row(t, s, lane2).PRs, []int{46001})
		equal(t, len(f.posts), 1)
		equal(t, f.posts[0].To, []string{Root, lane2, asker})
		contains(t, f.posts[0].Text, asker+": read conflicts-2 with `cci tail --lane conflicts-2 --since <its wake seq> --oldest` through #3, then run `"+c.retry+"`.")
	}
}

func TestAnOwnerDecideTakingSomeOfALiveLanesPRsMovesThemWithNoLastRead(t *testing.T) {
	s, brief := memory(t), writeBrief(t)
	f := adopted(t, s, brief, "", 46001, 46002)
	f.feed = append(f.feed, spawned(2, "restack-children-2", brief, t0, nil), rec(3, lane2, "head", t0, "#46001 e590637").withRefs(prs(46001)), ask(4, "owner", "restack-children-2", t0, []int{46001}, "", nil))
	f.peers = append(f.peers, who("restack-children-2", true, t0))

	tick(t, s, f, t0)

	lane := row(t, s, lane2)
	equal(t, []any{lane.State == store.LaneHeld, lane.PRs}, []any{false, []int{46002}})
	equal(t, row(t, s, "restack-children-2").PRs, []int{46001})
	equal(t, len(f.ofKind("defect")), 0)
}

func TestAnOwnerDecideInProseMovesNoPR(t *testing.T) {
	for _, text := range []string{"root: owner=restack-children-2 for #47245.", "root: #47245 moves to restack-children-2.", "root: #47245 with restack-children-2 as owner."} {
		s, brief := memory(t), writeBrief(t)
		f := adopted(t, s, brief, "", 47245)
		f.feed = append(f.feed, spawned(2, "restack-children-2", brief, t0, nil), rec(3, Root, "decide", t0, text, Self, "restack-children-2", lane2).withRefs(prs(47245)))
		f.peers = append(f.peers, who("restack-children-2", true, t0))

		tick(t, s, f, t0)

		equal(t, owners(t, s, 47245), map[int]string{47245: lane2})
		equal(t, len(f.ofKind("defect")), 0)
	}
}

func TestALaneAnUnreadStandDownLeftInPlaceIsNeverRelaunchedOnceItStops(t *testing.T) {
	for _, postsAgain := range []bool{false, true} {
		s := memory(t)
		f := adopted(t, s, writeBrief(t), "", 46001)
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

func TestALanesOwnStandDownForASuccessorPassesThoughItIsTheLanesNewestRecord(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.feed = append(f.feed, rec(2, lane2, "state", t0, "graded #50972"), ask(3, "stand-down", lane2, t0.Add(time.Minute), nil, lane2, map[string]string{"successor": "conflicts-3", "last-read": "2"}))
	f.peers = []Row{who(lane2, true, t0.Add(time.Minute))}

	tick(t, s, f, t0.Add(mins(2)))

	equal(t, []any{row(t, s, lane2).State, row(t, s, lane2).RetiredBy}, []any{store.LaneRetired, "conflicts-3"})
	equal(t, row(t, s, "conflicts-3").State, store.LaneLaunching)
	equal(t, len(f.ofKind("defect")), 0)
}

func TestARootStandDownCitingAReadAsNewAsTheLanesNewestRecordRetiresIt(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.feed = append(f.feed, rec(2, lane2, "state", t0, "restacked"), ask(3, "stand-down", lane2, t0, nil, "", map[string]string{"last-read": "2"}))

	tick(t, s, f, t0)

	equal(t, row(t, s, lane2).State, store.LaneRetired)
	equal(t, f.kinds(), []string{"stand-down:conflicts-2", "report:" + OwnerRelay})
}

func TestTheOwnerHearsOneLineAnHourAndOnlyWhenALaneRestarted(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "", 46001)
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
	equal(t, f.posts[0].Text, "In the past hour the lane supervisor restarted 1 lane with no coordinator action: conflicts-2 stopped, quiet for 16 min, so conflicts-3 took over.")
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
	cold := &Peer{Lane: "l-1", Seen: t0.Add(-mins(20)), Row: Row{Unverified: "has an Orca agent that reads unverifiable (host_unavailable)"}}
	equal(t, Classify(store.Lane{Name: "l-1", State: store.LaneDesired, Since: t0.Add(-mins(30))}, cold, t0, 0, 0), Verdict{Unproven, "has an Orca agent that reads unverifiable (host_unavailable) and has been quiet for 20 min, so nothing proves it died"})
}

func TestALaunchingLaneThatNeverComesUpIsANoShow(t *testing.T) {
	equal(t, Classify(store.Lane{State: store.LaneLaunching, Since: t0.Add(-mins(25))}, nil, t0, 0, 0).Kind, NoShow)
	equal(t, Classify(store.Lane{State: store.LaneLaunching, Since: t0.Add(-mins(20))}, nil, t0, 0, 0).Kind, Ok)
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
		equal(t, SuccessorName(c.name, taken), c.want)
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

func TestALaneParkedOnAnOwnerCardWakesOnceForRootsReplyAndOnceWithTheOwnersPick(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.peers = []Row{onRoster(who(lane2, true, t0.Add(-2*time.Hour)))}
	f.feed = append(f.feed,
		rec(2, lane2, "blocked", t0.Add(-2*time.Hour), "parked on the owner's pick on card-cleanup-daemon-restart", Root),
		rec(3, Root, "decide", t0.Add(-mins(90)), "conflicts-2 is held on the owner's card by design", lane2).withRe(2),
	)

	waiting := tick(t, s, f, t0)
	f.feed = append(f.feed, rec(4, "board-watch", "owner", t0.Add(mins(1)), "Owner on board d50e80e8, Restart the cleanup daemon? (cleanup-daemon-restart): picked \"yes, restart it\"", "all").withCard("card-cleanup-daemon-restart"))
	picked := tick(t, s, f, t0.Add(mins(2)))
	after := tick(t, s, f, t0.Add(mins(10)))

	equal(t, waiting.Woken, []string{lane2})
	contains(t, pageText(f.pages[0], "why"), "root's decide #3")
	equal(t, len(after.Woken), 0)
	equal(t, row(t, s, lane2).Awaits, "")
	equal(t, picked.Woken, []string{lane2})
	if why := pageText(f.pages[1], "why"); !strings.HasPrefix(why, "the owner answered card-cleanup-daemon-restart, the card you wait on: Owner on board") {
		t.Fatal(why)
	}
}

func TestHisAnswerOnAnyCardALaneWaitsOnWakesIt(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.peers = []Row{onRoster(who(lane2, true, t0.Add(-2*time.Hour)))}
	f.feed = append(f.feed, rec(2, lane2, "blocked", t0.Add(-2*time.Hour), "parked on two picks: card-phone-app-platform and card-phone-app-mesh", Root))

	tick(t, s, f, t0)
	equal(t, row(t, s, lane2).Awaits, "card-phone-app-platform card-phone-app-mesh")
	f.feed = append(f.feed, rec(3, "owner-relay", "owner", t0.Add(mins(1)), "go with the recommended option on card-phone-app-mesh", "all").withCard("card-phone-app-mesh"))
	ruled := tick(t, s, f, t0.Add(mins(2)))

	equal(t, ruled.Woken, []string{lane2})
	equal(t, row(t, s, lane2).Awaits, "")
	if why := pageText(f.pages[0], "why"); !strings.HasPrefix(why, "the owner answered card-phone-app-mesh, the card you wait on: go with the recommended option") {
		t.Fatal(why)
	}
}

func TestAnOwnerRecordThatOnlyNamesTheCardALaneWaitsOnLeavesItWaiting(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.peers = []Row{onRoster(who(lane2, true, t0.Add(-2*time.Hour)))}
	f.feed = append(f.feed, rec(2, lane2, "blocked", t0.Add(-2*time.Hour), "parked on his pick on card-49305-rollback-stale", Root))

	tick(t, s, f, t0)
	f.feed = append(f.feed, rec(3, "owner-relay", "owner", t0.Add(mins(1)), `Owner, 10:08 AM same thread, asked to be shown the open SandSQL picks. My reading: the three open SandSQL picks (card-49305-rollback-stale, card-launch-parked-chats, card-replicator-behind) are drawn as cards in Slack thread 1793985883.857779. I draw them.`, "root", "c-reconcile"))
	read := tick(t, s, f, t0.Add(mins(2)))

	equal(t, len(read.Woken), 0)
	equal(t, row(t, s, lane2).Awaits, "card-49305-rollback-stale")
}

func recommendedDrive(t *testing.T, parked string) (*store.Store, *fake, time.Time) {
	t.Helper()
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	if _, err := s.WantLane(store.Lane{Name: "meta-desk-3", Brief: "/briefs/meta-desk-3.md", Model: "opus", Effort: "xhigh", Role: RoleMeta, State: store.LaneDesired}, t0); err != nil {
		t.Fatal(err)
	}
	at := t0.Add(mins(61))
	f.peers = []Row{who(lane2, true, at), who("meta-desk-3", true, at)}
	f.feed = append(f.feed, rec(2, lane2, "blocked", t0.Add(mins(30)), parked, Root))
	return s, f, at
}

func reportsTo(f *fake, lane string) []string {
	var texts []string
	for _, p := range f.posts {
		if p.Kind == "report" && slices.Contains(p.To, lane) {
			texts = append(texts, p.Text)
		}
	}
	return texts
}

func TestWhileTheBuildRecommendedOrderStandsTheHourlyPassWakesEveryLaneParkedOnAPick(t *testing.T) {
	s, f, at := recommendedDrive(t, "parked on the owner's pick on card-phone-app-platform")
	f.deps.BuildRecommendedOrder = "owner:m104"

	tick(t, s, f, at)

	equal(t, row(t, s, lane2).Awaits, "")
	equal(t, reportsTo(f, "meta-desk-3"), []string{"owner:m104: woke these lanes from a pick to build its recommended option: conflicts-2 (card-phone-app-platform)."})
	if why := pageText(must(s.Pages())[lane2], "why"); !strings.HasPrefix(why, "owner:m104 stands, so no lane parks on a pick that recommends an option: build the recommended option of card-phone-app-platform as a branch, never a PR") {
		t.Fatal(why)
	}
}

func TestALaneParkedOnAPickIsWokenOnceForItAndNeverWhileItHasAPR(t *testing.T) {
	const parked = "parked on the owner's pick on card-phone-app-platform"
	for name, withPR := range map[string]bool{"woken once": false, "has a PR": true} {
		t.Run(name, func(t *testing.T) {
			s, f, at := recommendedDrive(t, parked)
			f.deps.BuildRecommendedOrder = "owner:m104"
			if withPR {
				if err := s.SetLanePRs(lane2, []int{48977}); err != nil {
					t.Fatal(err)
				}
			}
			tick(t, s, f, at)
			if err := s.SetLaneWaiting(lane2, 2, at, parked); err != nil {
				t.Fatal(err)
			}

			later := at.Add(2 * time.Hour)
			f.peers = []Row{who(lane2, true, later), who("meta-desk-3", true, later)}
			tick(t, s, f, later)

			equal(t, row(t, s, lane2).Awaits, parked)
			woke := 1
			if withPR {
				woke = 0
			}
			equal(t, len(slices.DeleteFunc(reportsTo(f, "meta-desk-3"), func(text string) bool { return !strings.Contains(text, "woke these lanes") })), woke)
		})
	}
}

func TestACardNamedInAnAskToAnotherLaneIsNoWaitOnHisPick(t *testing.T) {
	s, f, at := recommendedDrive(t, "parked on nothing of his")
	f.deps.BuildRecommendedOrder = "owner:m104"
	f.feed = append(f.feed, rec(3, lane2, "ask", t0.Add(mins(31)), "For card-agents-chat-table: replace the dead background link", "board-refresher"))

	tick(t, s, f, at)

	if strings.Contains(row(t, s, lane2).Awaits, "card-agents-chat-table") {
		t.Fatal(row(t, s, lane2).Awaits)
	}
	equal(t, len(reportsTo(f, "meta-desk-3")), 0)
	equal(t, len(must(s.BranchCards())), 0)
}

func TestACardWithNoRecommendationOrNoStandingOrderLeavesTheLaneParked(t *testing.T) {
	for name, c := range map[string]struct{ parked, order string }{
		"no recommendation": {"card-phone-app-platform recommends none: no-recommendation", "owner:m104"},
		"no order":          {"parked on the owner's pick on card-phone-app-platform", ""},
	} {
		t.Run(name, func(t *testing.T) {
			s, f, at := recommendedDrive(t, c.parked)
			f.deps.BuildRecommendedOrder = c.order

			tick(t, s, f, at)

			if !strings.HasPrefix(row(t, s, lane2).Awaits, "card-phone-app-platform") {
				t.Fatal(row(t, s, lane2).Awaits)
			}
			equal(t, len(reportsTo(f, "meta-desk-3")), 0)
		})
	}
}

func defectsTo(f *fake, lane string) []string {
	var texts []string
	for _, p := range f.posts {
		if p.Kind == "defect" && slices.Contains(p.To, lane) {
			texts = append(texts, p.Text)
		}
	}
	return texts
}

func answeredBranch(t *testing.T) (*store.Store, *fake, func(time.Duration) Outcome) {
	t.Helper()
	s, f, at := recommendedDrive(t, "parked on the owner's picks on card-signal-redesign-relay and card-signal-redesign-prs")
	f.deps.BuildRecommendedOrder = "owner:m104"
	tickAt := func(after time.Duration) Outcome {
		f.peers = []Row{who(lane2, true, at.Add(after)), who("meta-desk-3", true, at.Add(after))}
		return tick(t, s, f, at.Add(after))
	}
	tickAt(0)
	f.feed = append(f.feed, rec(3, "board-watch", "owner", at.Add(mins(1)), "Owner on board d50e80e8, Which transport? (signal-redesign-relay): picked \"direct-or-relay\"", "all").withCard("card-signal-redesign-relay"))
	tickAt(mins(2))
	if topic := pageText(must(s.Pages())[lane2], "topic"); topic != "owner:m104" {
		t.Fatalf("one card still open, yet the page is %q", topic)
	}
	f.feed = append(f.feed, rec(4, "owner-relay", "owner", at.Add(mins(3)), "go with the recommended option on card-signal-redesign-prs", "all").withCard("card-signal-redesign-prs"))
	tickAt(mins(4))
	return s, f, tickAt
}

func TestWhenTheOwnerAnswersTheLastCardOfABoardBuiltBranchItsLaneIsToldToOpenThePR(t *testing.T) {
	s, f, tickAt := answeredBranch(t)

	if why := pageText(must(s.Pages())[lane2], "why"); !strings.HasPrefix(why, "the owner answered every card your branch was built on (card-signal-redesign-prs, card-signal-redesign-relay), so nothing holds its PR") {
		t.Fatal(why)
	}
	if !slices.Contains(reportsTo(f, "meta-desk-3"), "Told these lanes to open the PR of a branch whose every card the owner answered: conflicts-2 (card-signal-redesign-prs, card-signal-redesign-relay).") {
		t.Fatal(reportsTo(f, "meta-desk-3"))
	}
	tickAt(mins(10))
	equal(t, len(defectsTo(f, "meta-desk-3")), 0)
	tickAt(mins(20))
	tickAt(mins(30))
	defects := defectsTo(f, "meta-desk-3")
	equal(t, len(defects), 1)
	contains(t, defects[0], "branch:conflicts-2: the owner answered every card its branch was built on")
}

func TestAnOwnerRecordThatOnlyNamesABranchsCardAnswersNoneOfIt(t *testing.T) {
	s, f, at := recommendedDrive(t, "parked on the owner's pick on card-49305-rollback-stale")
	f.deps.BuildRecommendedOrder = "owner:m104"
	tick(t, s, f, at)
	f.feed = append(f.feed, rec(3, "owner-relay", "owner", at.Add(mins(1)), `Owner, 10:08 AM same thread, asked to be shown the open SandSQL picks. My reading: the three open SandSQL picks (card-49305-rollback-stale, card-launch-parked-chats, card-replicator-behind) are drawn as cards.`, "root", "c-reconcile"))
	later := at.Add(mins(2))
	f.peers = []Row{who(lane2, true, later), who("meta-desk-3", true, later)}

	tick(t, s, f, later)

	built := must(s.BranchCards())
	equal(t, len(built), 1)
	if !built[0].Answered.IsZero() {
		t.Fatalf("branch card answered at %v by a record that only names it", built[0].Answered)
	}
	if slices.ContainsFunc(reportsTo(f, "meta-desk-3"), func(text string) bool { return strings.Contains(text, "open the PR") }) {
		t.Fatal(reportsTo(f, "meta-desk-3"))
	}
}

func TestALaneThatOpensItsAnsweredBranchsPRRaisesNoDefect(t *testing.T) {
	s, f, tickAt := answeredBranch(t)

	f.feed = append(f.feed, rec(5, lane2, "opened", t0.Add(mins(66)), "").withRefs(prs(2440)))
	tickAt(mins(6))
	tickAt(mins(20))

	equal(t, len(defectsTo(f, "meta-desk-3")), 0)
	equal(t, len(must(s.BranchCards())), 0)
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

func TestOnlyARootDecidesTypedFieldsChangeTheRegistry(t *testing.T) {
	prose := "Root task list: conflicts-2 (1985) is complete and closed; supervisor, stop waking it; conflicts-2 retired, park it. last-read=1"
	typed := ask(2, "park", lane2, t0, nil, "", map[string]string{"last-read": "1"})
	typed.Text = prose
	cases := []struct {
		name    string
		decide  Record
		waiting int64
		defects int
	}{
		{"prose naming lanes", rec(2, Root, "decide", t0, prose, Self, lane2), 0, 0},
		{"a typed request", ask(2, "park", lane2, t0, nil, "", nil), 2, 0},
		{"a typed request whose prose names other verbs", typed, 2, 0},
		{"a verb with no lane", ask(2, "park", "", t0, nil, "", nil), 0, 1},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			s := memory(t)
			f := adopted(t, s, writeBrief(t), "")
			f.feed = append(f.feed, c.decide)

			tick(t, s, f, t0)

			lane := row(t, s, lane2)
			equal(t, []any{lane.State, lane.Waiting}, []any{store.LaneDesired, c.waiting})
			equal(t, len(f.ofKind("defect")), c.defects)
		})
	}
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
	do(s.SetLaneAsked(lane2, t0.Add(-3*time.Hour)))
	f.feed = append(f.feed, rec(2, lane2, "handoff", t0.Add(-2*time.Hour), "flushed", Root).withRefs(Refs{CCN: "9a1b2c3"}).withRe(1), stall(3, t0.Add(-time.Hour), "STALL red #46475", 46475, ""))
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

func TestEverySupervisorPostIsForcedAndLeavesItsTimeToTheRecord(t *testing.T) {
	draft := Post{Kind: "report", To: []string{Root}, Text: "x", LaneRef: "l", PRs: []int{9}, Re: 4, Successor: "s"}.Draft(testDrive)

	equal(t, draft, client.Draft{Drive: testDrive, Lane: Self, Kind: "report", To: []string{Root}, Text: "x", Re: 4, Successor: "s", Force: true, LaneRefs: []string{"l"}, PRs: []string{"9"}})
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
		"decide": ask(3, "owner", Root, t0.Add(-mins(98)), []int{46107}, "", map[string]string{"last-read": "1"}),
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

func TestALaneARootDecideCallsRetiredOrDoneInProseStaysInTheRegistry(t *testing.T) {
	for _, text := range []string{"root: conflicts-2 retired; gos posted for the four (stack order on #48190).", "root: conflicts-2 is done.", "root: restack-children-2 replaces conflicts-2."} {
		s, brief := memory(t), writeBrief(t)
		f := adopted(t, s, brief, "")
		f.feed = append(f.feed, spawned(2, "restack-children-2", brief, t0, nil), rec(3, Root, "decide", t0, text, Self, lane2))
		f.peers = append(f.peers, who("restack-children-2", true, t0))

		tick(t, s, f, t0)

		equal(t, row(t, s, lane2).State, store.LaneDesired)
	}
}

func TestAHeldLaneThatPostsAgainIsLiveAndAPRRootOpenedForItIsTheLanes(t *testing.T) {
	s, brief := memory(t), writeBrief(t)
	f := adopted(t, s, brief, "")
	f.feed = append(f.feed, spawned(2, "ssql-sleep-truncation", brief, t0, nil))
	tick(t, s, f, t0)
	tick(t, s, f, t0.Add(mins(26)))
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

func TestALaunchersRecordTakingAPRRetiresOnlyALaunchThatNeverPosted(t *testing.T) {
	s, brief := memory(t), writeBrief(t)
	f := adopted(t, s, brief, "", 46001)
	f.feed = append(f.feed, spawned(2, "sprite-lane-gaps", brief, t0, nil), spawned(3, "never-up", brief, t0, nil))
	tick(t, s, f, t0.Add(time.Minute))
	if err := s.SetLanePRs("never-up", []int{121}); err != nil {
		t.Fatal(err)
	}
	f.feed = append(f.feed,
		rec(4, "sprite-lane-gaps", "opened", t0.Add(mins(8)), "opened #yasyf/cc-remote#120").withRefs(prs(120)),
		rec(5, lane2, "opened", t0.Add(mins(8)), "yasyf/cc-remote#120 on sprite-lane-gaps-gemini").withRefs(prs(120, 121)),
	)

	tick(t, s, f, t0.Add(mins(10)))

	equal(t, row(t, s, "sprite-lane-gaps").State, store.LaneDesired)
	equal(t, []any{row(t, s, "never-up").State, row(t, s, "never-up").RetiredBy}, []any{store.LaneRetired, lane2})
	downs := f.ofKind("stand-down")
	equal(t, len(downs), 1)
	equal(t, []any{downs[0].To, downs[0].Successor, downs[0].PRs, downs[0].Text}, []any{[]string{"never-up"}, lane2, []int{121}, "never-up stood down for conflicts-2."})
}

func TestAnOrcaWorkerThatFinishedRelaunchesItsLaneWithItsPRsWhileItIsStandingOrOwnsAnOpenPR(t *testing.T) {
	const duty, heir = "Standing: refresh the owner's board every pass\n", "board-refresher-7"
	for name, tc := range map[string]struct {
		spawnedWith, finishedWith string
		launches                  []string
		retiredBy, owner          string
	}{
		"a standing lane relaunches":                                              {duty, duty, []string{heir}, heir, heir},
		"a one-shot lane owning an open PR relaunches to finish it":               {"", "", []string{heir}, heir, heir},
		"a lane whose Standing line was deleted relaunches to finish its open PR": {duty, "", []string{heir}, heir, heir},
	} {
		t.Run(name, func(t *testing.T) {
			s := memory(t)
			path := filepath.Join(t.TempDir(), refresher+".brief.md")
			brief := func(standing string) {
				do(os.WriteFile(path, []byte("# "+refresher+"\nRegister slice: lane="+refresher+" step=tooling\n"+standing), 0o600))
			}
			brief(tc.spawnedWith)
			f := newFake(t, []Record{spawned(1, refresher, path, t0.Add(-2*time.Hour), nil)}, []Row{who(refresher, true, t0.Add(-time.Hour))})
			tick(t, s, f, t0.Add(-time.Hour))
			do(s.SetLanePRs(refresher, []int{46001}))
			f.peers = nil
			withUnblock(t, s, f, store.LaneDesired)
			brief(tc.finishedWith)
			f.feed = append(f.feed, ask(2, "done", refresher, t0, nil, runner, nil))

			tick(t, s, f, t0.Add(mins(1)))
			tick(t, s, f, t0.Add(mins(11)))

			equal(t, []any{f.launches, row(t, s, refresher).RetiredBy, owners(t, s, 46001)[46001]}, []any{tc.launches, tc.retiredBy, tc.owner})
		})
	}
}

func TestAOneShotLaneWhoseOrcaWorkerFinishedWhileItOwnsAnOpenPRIsRelaunchedThatTickAndOneWithNoneOpenRetires(t *testing.T) {
	cases := map[string]struct {
		open      string
		launches  int
		retiredBy string
	}{
		"owns an open PR": {open: Waiting, launches: 1},
		"owns none open":  {open: Closed, retiredBy: "done"},
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			s := memory(t)
			f := adopted(t, s, writeBrief(t), "", 52503, 52514)
			f.states = map[int]string{52503: c.open, 52514: Closed}
			f.feed = append(f.feed, ask(2, "done", lane2, t0, nil, runner, nil))

			tick(t, s, f, t0.Add(mins(1)))

			equal(t, []any{len(f.launches), len(f.ofKind("defect"))}, []any{c.launches, 0})
			if c.retiredBy != "" {
				equal(t, row(t, s, lane2).RetiredBy, c.retiredBy)
			}
			if c.launches == 1 {
				equal(t, row(t, s, lane2).State, store.LaneRetired)
			}
		})
	}
}

func TestALaneWhoseOrcaWorkerSucceededByItsOutcomeLineIsRetired(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.feed = append(f.feed, rec(2, lane2, "state", t0.Add(-mins(30)), "restacking", Root), rec(3, runner, "report", t0.Add(-mins(14)), "OUTCOME msg_b21ededd168d conflicts-2: worker_done succeeded dispatch=ctx_320e271b3112"))
	f.peers = []Row{who(lane2, false, t0.Add(-mins(20)))}

	out := tick(t, s, f, t0)

	equal(t, len(out.Relaunched), 0)
	equal(t, row(t, s, lane2).RetiredBy, "worker-done")
}

func TestALaneOnARootOrderWithNoProcessRowIsHeldNeverRetired(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "", 46001)
	f.feed = append(f.feed, rec(2, Root, "decide", t0.Add(-mins(25)), "fix the race in your open PR", lane2))
	f.peers = nil

	tick(t, s, f, t0)

	equal(t, row(t, s, lane2).State, store.LaneHeld)
	equal(t, row(t, s, lane2).RetiredBy, "")
	equal(t, len(f.ofKind("blocker")), 1)
}

func TestALaneThatStoppedOnAnEarlierDayNamesHowLongItWasQuietAndNoClock(t *testing.T) {
	at := t0.Add(-30 * time.Hour)

	verdict := Classify(store.Lane{Name: lane2, State: store.LaneDesired}, &Peer{Lane: lane2, Seen: at}, t0, 0, 0)

	equal(t, verdict.Why, "stopped, quiet for 1800 min")
}

func TestAnOrcaWorkerThatSucceededIsRetiredBeforeItsOutcomeLineArrives(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	finished := who(lane2, false, t0.Add(-mins(20)))
	finished.Succeeded = true
	f.feed = append(f.feed, rec(2, lane2, "state", t0.Add(-mins(30)), "restacking", Root))
	f.peers = []Row{finished}

	out := tick(t, s, f, t0)

	equal(t, len(out.Relaunched), 0)
	equal(t, row(t, s, lane2).RetiredBy, retiredWorkerDone)
}

func TestALaneWhoseOrcaWorkerFailedStillRelaunches(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "", 46001)
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

func TestARetiredStandingLaneStillPostingHasAnOwner(t *testing.T) {
	s := memory(t)
	f, _ := standingLane(t, s)
	f.feed = append(f.feed, rec(2, refresher, "stopped", t0, "retired"))
	f.peers = nil
	tick(t, s, f, t0.Add(mins(1)))

	f.feed = append(f.feed, rec(3, refresher, "report", t0.Add(mins(10)), "still working"))
	tick(t, s, f, t0.Add(mins(11)))

	equal(t, len(f.ofKind("blocker")), 0)
}

func TestASameStemSuccessorStaysStandingThroughItsOwnDoneOnlyWhenItsPredecessorStood(t *testing.T) {
	for name, tc := range map[string]struct {
		standing  bool
		retiredBy string
		duty      string
	}{
		"successor of a standing lane": {true, "", "refresh the owner's board every pass"},
		"successor of a one-shot lane": {false, "done", ""},
	} {
		t.Run(name, func(t *testing.T) {
			s := memory(t)
			old, f := lane2, (*fake)(nil)
			if tc.standing {
				f, _ = standingLane(t, s)
				old = refresher
			} else {
				f = adopted(t, s, writeBrief(t), "")
			}
			heir := SuccessorName(old, nil)
			f.feed = append(f.feed, spawned(10, heir, writeBrief(t), t0, nil), rec(11, heir, "done", t0.Add(mins(1)), "this pass is finished", Root))
			f.peers = []Row{who(heir, true, t0.Add(mins(1)))}

			tick(t, s, f, t0.Add(mins(2)))

			got := row(t, s, heir)
			equal(t, []any{got.RetiredBy, got.Standing}, []any{tc.retiredBy, tc.duty})
		})
	}
}

func TestACoordinatorsDoneEndsTheDutyASuccessorInheritedAndNothingRelaunchesIt(t *testing.T) {
	s := memory(t)
	f, _ := standingLane(t, s)
	heir := SuccessorName(refresher, nil)
	f.feed = append(f.feed, spawned(10, heir, writeBrief(t), t0, nil), rec(11, heir, "done", t0.Add(mins(1)), "this pass is finished", Root), ask(12, "done", heir, t0.Add(mins(2)), nil, "", nil))
	f.peers = nil

	tick(t, s, f, t0.Add(mins(3)))
	tick(t, s, f, t0.Add(mins(40)))

	got := row(t, s, heir)
	equal(t, []any{got.RetiredBy, got.Standing, len(f.launches), len(f.ofKind("blocker"))}, []any{retiredDutyEnded, "", 0, 0})
}

func TestARevivedLaneIsLiveAgainAndTakesTheDutyOfTheStandingLaneItSucceeds(t *testing.T) {
	s := memory(t)
	for _, lane := range []store.Lane{
		{Name: "go-desk-2", Brief: standingBrief(t, "go-desk-2"), Model: "opus", Effort: "high", Role: "lane", State: store.LaneRetired, RetiredBy: "go-desk-8"},
		{Name: "go-desk-8", Brief: writeBrief(t), Model: "opus", Effort: "high", Role: "lane", State: store.LaneRetired, RetiredBy: "done"},
	} {
		if _, err := s.WantLane(lane, t0.Add(-time.Hour)); err != nil {
			t.Fatal(err)
		}
	}
	f := newFake(t, []Record{ask(1, "revive", "go-desk-8", t0, nil, "", nil)}, []Row{who("go-desk-8", true, t0)})

	tick(t, s, f, t0.Add(mins(1)))

	got := row(t, s, "go-desk-8")
	equal(t, []any{got.State, got.RetiredBy, got.Standing}, []any{store.LaneDesired, "", "refresh the owner's board every pass"})
}

func TestAReviveOfALaneTheRegistryHoldsDesiredPostsOneRefusalToTheRequester(t *testing.T) {
	s := memory(t)
	const name = "cp-egress-reset-fix-2"
	heard := t0.Add(-33 * time.Minute)
	if _, err := s.WantLane(store.Lane{Name: name, Brief: writeBrief(t), Model: "opus", Effort: "high", Role: "lane", State: store.LaneDesired, Predecessor: "cp-egress-reset-fix-1"}, t0.Add(-time.Hour)); err != nil {
		t.Fatal(err)
	}
	do(s.LaneHeard(name, heard, "landed", 1))
	do(s.Advance(CursorName, 1))
	f := newFake(t, []Record{ask(2, "revive", name, t0, nil, "", nil)}, nil)

	tick(t, s, f, t0.Add(mins(1)))
	tick(t, s, f, t0.Add(mins(2)))

	var answers []Post
	for _, post := range f.posts {
		if post.Re == 2 {
			answers = append(answers, post)
		}
	}
	equal(t, answers, []Post{{Kind: "report", To: []string{Root}, LaneRef: name, Re: 2, Text: "REVIVE REFUSED: cp-egress-reset-fix-2 did not launch: it is desired, not retired, so the registry counts it running; it was last heard 9:27 AM (landed), and no live session answers for it, so the supervisor's liveness check relaunches or holds it, not a revive. A revive brings back only a retired lane."}})
	equal(t, f.launches, []string(nil))
}

func TestAReviveOfARetiredLaneWithNoSessionLaunchesItFromItsBrief(t *testing.T) {
	s := memory(t)
	const name = "lf-grant-fix-2"
	if _, err := s.WantLane(store.Lane{Name: name, Brief: writeBrief(t), Model: "opus", Effort: "high", Role: "lane", State: store.LaneRetired, RetiredBy: "done"}, t0.Add(-time.Hour)); err != nil {
		t.Fatal(err)
	}
	do(s.Advance(CursorName, 1))
	f := newFake(t, []Record{ask(2, "revive", name, t0, nil, "", nil)}, nil)

	tick(t, s, f, t0.Add(mins(1)))
	tick(t, s, f, t0.Add(mins(2)))

	answered := slices.ContainsFunc(f.posts, func(post Post) bool { return post.Re == 2 })
	equal(t, []any{f.launches, f.keys, row(t, s, name).State, answered}, []any{[]string{name}, []string{"supervisor-launch:lf-grant-fix-2:revive:2"}, store.LaneLaunching, false})
}

func TestAStandingLaneStoppedWithNoSuccessorRelaunchesOnceItIsNoLongerHeard(t *testing.T) {
	s := memory(t)
	f, _ := standingLane(t, s)
	f.feed = append(f.feed, rec(2, refresher, "stopped", t0, "retired"))
	f.peers = nil

	early := tick(t, s, f, t0.Add(mins(9)))
	equal(t, early, Outcome{})
	tick(t, s, f, t0.Add(mins(16)))
	tick(t, s, f, t0.Add(mins(40)))

	equal(t, []any{f.launches, len(f.ofKind("blocker")), row(t, s, refresher).RetiredBy}, []any{[]string{"board-refresher-7"}, 0, "board-refresher-7"})
}

func TestAStandingDeskWhoseBriefNamesNoMacNeedRelaunchesRemote(t *testing.T) {
	s := memory(t)
	path := filepath.Join(t.TempDir(), refresher+".brief.md")
	do(os.WriteFile(path, []byte("# "+refresher+"\nccx: role=desk place=local why=mac-inputs\nStanding: refresh the owner's board every pass\n"), 0o600))
	f := newFake(t, []Record{spawned(1, refresher, path, t0.Add(-2*time.Hour), nil)}, []Row{who(refresher, true, t0.Add(-time.Hour))})
	tick(t, s, f, t0.Add(-time.Hour))
	f.posts = nil
	f.feed = append(f.feed, rec(2, refresher, "done", t0, "finished"), ask(3, "done", refresher, t0, nil, "meta-desk-3", nil))
	f.peers = nil

	tick(t, s, f, t0.Add(mins(1)))
	tick(t, s, f, t0.Add(mins(40)))

	equal(t, []any{f.launches, len(f.ofKind("blocker"))}, []any{[]string{"board-refresher-7"}, 0})
	contains(t, f.briefs["board-refresher-7"], "Replaces: "+refresher+"\nccx: place=remote\n")
}

func TestAStandingLanesDoneClosesAnItemAndNeverTheLane(t *testing.T) {
	s := memory(t)
	f, _ := standingLane(t, s)
	f.feed = append(f.feed, rec(2, refresher, "done", t0, "item 157 proven"))
	f.peers = []Row{who(refresher, true, t0)}

	tick(t, s, f, t0.Add(mins(11)))

	equal(t, row(t, s, refresher).State, store.LaneDesired)
	equal(t, len(f.ofKind("blocker")), 0)
}

func TestALaneParkedOnAStandingLaneWakesOnItsStopNotOnADoneForAnItem(t *testing.T) {
	s := memory(t)
	f, _ := standingLane(t, s)
	f.feed = append(f.feed, spawned(2, lane2, writeBrief(t), t0.Add(-time.Hour), nil), rec(3, lane2, "blocked", t0.Add(-mins(30)), "waits on the desk").withRefs(Refs{Lanes: []string{refresher}}))
	f.peers = []Row{who(refresher, true, t0), who(lane2, true, t0)}
	tick(t, s, f, t0)
	equal(t, row(t, s, lane2).Waiting, int64(3))

	f.feed = append(f.feed, rec(4, refresher, "done", t0.Add(mins(1)), "item 157 proven"))
	tick(t, s, f, t0.Add(mins(2)))
	equal(t, row(t, s, lane2).Waiting, int64(3))

	f.feed = append(f.feed, rec(5, refresher, "stopped", t0.Add(mins(3)), "stopping"))
	tick(t, s, f, t0.Add(mins(4)))
	equal(t, row(t, s, lane2).Waiting, int64(0))
}

func TestOnlyAStandingLaneStaysUnsettledOnItsOwnDone(t *testing.T) {
	brief := standingBrief(t, refresher)
	equal(t, settledLane(store.Lane{Name: refresher, Brief: brief, Last: "done"}), false)
	equal(t, settledLane(store.Lane{Name: refresher, Brief: brief, Last: "stopped"}), true)
	equal(t, settledLane(store.Lane{Name: lane2, Last: "done"}), true)
}

func TestARootStandDownNamingNoSuccessorKeepsAStandingLaneAndAsksRootOnce(t *testing.T) {
	s := memory(t)
	f, _ := standingLane(t, s)
	f.feed = append(f.feed, rec(2, Root, "stand-down", t0, "stand down, last-read=1", refresher))
	f.peers = []Row{who(refresher, true, t0)}

	tick(t, s, f, t0.Add(mins(1)))
	tick(t, s, f, t0.Add(mins(2)))

	equal(t, row(t, s, refresher).State, store.LaneDesired)
	equal(t, f.kinds(), []string{"ask:" + Root})
	equal(t, f.posts[0].Re, int64(2))
	contains(t, f.posts[0].Text, "stand-down #2 names no successor, so the supervisor kept it live")
	contains(t, f.posts[0].Text, "`lr request done "+refresher+"`")
}

func TestEveryLaneARootStandDownRetiresIsReportedToTheCommunicator(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.feed = append(f.feed, rec(2, Root, "stand-down", t0, "stand down, last-read=1", lane2))
	f.peers = []Row{who(lane2, true, t0)}

	tick(t, s, f, t0.Add(mins(1)))

	equal(t, row(t, s, lane2).RetiredBy, Root)
	reports := f.ofKind("report")
	equal(t, len(reports), 1)
	equal(t, []any{reports[0].To, reports[0].Text}, []any{[]string{OwnerRelay}, "RETIRED conflicts-2 by stand-down #2, its PRs to the coordinator."})
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

func TestAStandDownChainEndsAtTheSuccessorThatPostsUnderAnyName(t *testing.T) {
	for _, tc := range []struct {
		name, poster string
		listed       bool
	}{
		{"registered by its lane name", "board-refresher-7", true},
		{"registered by its Orca workspace name", "dar-board-refresher-7", true},
		{"posting with no process row", "board-refresher-7", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			s := memory(t)
			path := standingBrief(t, "board-refresher-5")
			f := newFake(t, []Record{spawned(1, "board-refresher-5", path, t0.Add(-3*time.Hour), nil)}, []Row{who("board-refresher-5", true, t0.Add(-2*time.Hour))})
			f.deps.Drive = "durable-agents-rebuild"
			tick(t, s, f, t0.Add(-2*time.Hour))
			f.feed = append(f.feed, ask(2, "stand-down", "board-refresher-5", t0.Add(-time.Hour), nil, "", map[string]string{"successor": "board-refresher-6", "last-read": "1"}))
			f.peers = []Row{who("board-refresher-6", true, t0.Add(-time.Hour))}
			tick(t, s, f, t0.Add(-time.Hour))
			f.feed = append(f.feed, ask(3, "stand-down", "board-refresher-6", t0.Add(-mins(30)), nil, "", map[string]string{"successor": "board-refresher-7", "last-read": "2"}))
			for i, later := range []int{-29, -15, 0, 15, 30} {
				at := t0.Add(mins(later))
				f.feed = append(f.feed, rec(int64(4+i), tc.poster, "report", at, "board pass done", Root))
				f.peers = nil
				if tc.listed {
					f.peers = []Row{who(tc.poster, true, at)}
				}
				tick(t, s, f, at)
			}

			equal(t, row(t, s, "board-refresher-7").State, store.LaneDesired)
			equal(t, []string{row(t, s, "board-refresher-5").RetiredBy, row(t, s, "board-refresher-6").RetiredBy}, []string{"board-refresher-6", "board-refresher-7"})
			route, err := Route(s, "board-refresher-5")
			if err != nil {
				t.Fatal(err)
			}
			equal(t, route, "board-refresher-7")
			if _, found, err := s.Lane("dar-board-refresher-7"); err != nil || found {
				t.Fatalf("the Orca workspace name has its own registry row: found=%v err=%v", found, err)
			}
			equal(t, len(f.launches), 0)
			equal(t, len(f.ofKind("blocker")), 0)
		})
	}
}

func TestARootStandDownRetiresOnlyTheLaneItNamesNeverItsSuccessor(t *testing.T) {
	for _, text := range []string{"stand down conflicts-2, conflicts-3 holds the stacks now", "stand down conflicts-2 for conflicts-3"} {
		t.Run(text, func(t *testing.T) {
			s := memory(t)
			f := adopted(t, s, writeBrief(t), "")
			f.feed = append(f.feed, ask(2, "stand-down", lane2, t0, nil, "", map[string]string{"successor": "conflicts-3", "last-read": "1"}))
			f.peers = []Row{who("conflicts-3", true, t0)}
			tick(t, s, f, t0)
			f.feed = append(f.feed, rec(3, Root, "stand-down", t0.Add(mins(1)), text))

			tick(t, s, f, t0.Add(mins(2)))

			equal(t, row(t, s, "conflicts-3").State, store.LaneDesired)
			equal(t, len(f.ofKind("defect")), 0)
		})
	}
}

func TestAStandDownForASuccessorCarriesTheChainPastALaneThatAlreadyStopped(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.feed = append(f.feed, rec(2, lane2, "stopped", t0, "stopped on the coordinator's order"), ask(3, "stand-down", lane2, t0.Add(mins(1)), nil, "", map[string]string{"successor": "conflicts-3", "last-read": "2"}))
	f.peers = []Row{who("conflicts-3", true, t0.Add(mins(1)))}

	tick(t, s, f, t0.Add(mins(2)))

	equal(t, row(t, s, lane2).RetiredBy, "conflicts-3")
	route, err := Route(s, lane2)
	if err != nil {
		t.Fatal(err)
	}
	equal(t, route, "conflicts-3")
}

func TestAStandDownOfARelaunchedLaneKeepsTheSuccessorThatAlreadyPosted(t *testing.T) {
	s, brief := memory(t), writeBrief(t)
	f := adopted(t, s, brief, "7d102a9", 46001)
	f.peers = []Row{who(lane2, false, t0.Add(-mins(30)))}
	tick(t, s, f, t0)
	equal(t, row(t, s, "conflicts-3").State, store.LaneLaunching)
	f.feed = append(f.feed, rec(2, "conflicts-3", "report", t0.Add(mins(1)), "picked up the stacks from the handoff"), ask(3, "stand-down", lane2, t0.Add(mins(1)), nil, "", nil))

	tick(t, s, f, t0.Add(mins(2)))

	equal(t, row(t, s, "conflicts-3").State, store.LaneDesired)
	equal(t, len(f.withdrawn), 0)
}

func TestARetiredLaneThatStillPostsReadsAsPostingNotSilent(t *testing.T) {
	s := memory(t)
	if _, err := s.WantLane(store.Lane{Name: "board-refresher-7", Brief: "/briefs/board-refresher-7.md", State: store.LaneRetired, RetiredBy: Root}, t0.Add(-time.Hour)); err != nil {
		t.Fatal(err)
	}
	if err := s.LaneHeard("board-refresher-7", t0.Add(-mins(2)), "report", 360698); err != nil {
		t.Fatal(err)
	}

	lines, err := RegistryLines(s, []string{"board-refresher-7"}, t0)
	if err != nil {
		t.Fatal(err)
	}

	contains(t, lines[0], "board-refresher-7 retired posting heard ")
}

func TestARecordFromAnotherNodeShowsItsClockAndNodeAndALateOneReadsLaggingUntilItsLagPasses(t *testing.T) {
	s := memory(t)
	if _, err := s.WantLane(store.Lane{Name: "sprite-1", Brief: "/briefs/sprite-1.md", State: store.LaneRetired, RetiredBy: Root}, t0.Add(-3*time.Hour)); err != nil {
		t.Fatal(err)
	}
	node := "110f10f5abcdef01"
	records := []Record{
		{Seq: 1, Lane: "local-1", Kind: "state", At: t0},
		{Seq: 2, Origin: node, Lane: "sprite-1", Kind: "report", At: t0.Add(-mins(55))},
	}
	fold(s, records, nil, "", Deps{})

	line := func(now time.Time) string {
		t.Helper()
		lines, err := RegistryLines(s, []string{"sprite-1"}, now)
		if err != nil {
			t.Fatal(err)
		}
		return lines[0]
	}
	contains(t, line(t0), "sprite-1 retired lagging heard ")
	contains(t, line(t0), " on 110f10f5, arrived 55 min late ")
	contains(t, line(t0.Add(mins(10))), "sprite-1 retired silent heard ")
}

func TestARecordFromThisNodeNamesNoNode(t *testing.T) {
	s := memory(t)
	if _, err := s.WantLane(store.Lane{Name: "local-1", Brief: "/briefs/local-1.md", State: store.LaneRetired, RetiredBy: Root}, t0.Add(-time.Hour)); err != nil {
		t.Fatal(err)
	}
	fold(s, []Record{{Seq: 1, Lane: "local-1", Kind: "state", At: t0}}, nil, "", Deps{})

	lines, err := RegistryLines(s, []string{"local-1"}, t0)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(lines[0], " on ") {
		t.Fatalf("line = %q", lines[0])
	}
}

func TestAStandingLaneDeadWhileItWaitsOnRootIsRelaunchedFromItsBrief(t *testing.T) {
	s := memory(t)
	f, _ := standingLane(t, s)
	f.feed = append(f.feed, rec(2, refresher, "ask", t0.Add(-mins(30)), "which card first?", Root))
	f.peers = []Row{who(refresher, false, t0.Add(-mins(30)))}

	tick(t, s, f, t0)

	equal(t, len(f.ofKind("blocker")), 0)
	equal(t, f.launches, []string{"board-refresher-7"})
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

	lines, err := RegistryLines(s, []string{"gateway-gemini-1", "never-launched-1"}, t0)
	if err != nil {
		t.Fatal(err)
	}

	equal(t, len(lines), 2)
	if !strings.HasPrefix(lines[0], "gateway-gemini-1 desired live heard ") || !strings.Contains(lines[0], " head #342601 since ") {
		t.Fatal(lines[0])
	}
	equal(t, lines[1], "never-launched-1 unregistered")
}

func TestAnUnregisteredLaneThatPostsReadsAsPostingWithItsNewestRecord(t *testing.T) {
	s := memory(t)
	if err := s.LaneHeard("perf-sweep-1", t0.Add(-time.Minute), "report", 370803); err != nil {
		t.Fatal(err)
	}

	lines, err := RegistryLines(s, []string{"perf-sweep-1", "never-launched-1"}, t0)
	if err != nil {
		t.Fatal(err)
	}

	equal(t, lines, []string{"perf-sweep-1 unregistered posting heard " + pacific(t0.Add(-time.Minute)) + " #370803", "never-launched-1 unregistered"})
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

	for _, allowed := range []Request{{Verb: "stand-down", Lane: refresher, Successor: "board-refresher-7", Open: "none"}, {Verb: "done", Lane: refresher}, {Verb: "stand-down", Lane: "unknown"}} {
		refusal, err := StandingRefusal(s, allowed)
		if err != nil {
			t.Fatal(err)
		}
		equal(t, refusal, "")
	}
}

func TestADesksStandDownWithNoOpenItemListIsRefusedNamingWhatItMustCarry(t *testing.T) {
	s := memory(t)
	for _, lane := range []store.Lane{{Name: "meta-desk-2", Role: "desk"}, {Name: "fix-lane-1", Role: "lane"}} {
		lane.Model, lane.Effort, lane.State = "opus", "xhigh", store.LaneDesired
		if _, err := s.WantLane(lane, t0); err != nil {
			t.Fatal(err)
		}
	}

	refusal, err := StandingRefusal(s, Request{Verb: "stand-down", Lane: "meta-desk-2", Successor: "meta-desk-3"})
	if err != nil {
		t.Fatal(err)
	}
	contains(t, refusal, "meta-desk-2 is a desk, so its stand-down carries its open items for whoever takes them: every owner order it holds (`lr owner audit`), and every ask, PR and card it owes. Name them with --open '<item>; <item>', or pass --open none")

	for _, allowed := range []Request{{Verb: "stand-down", Lane: "meta-desk-2", Successor: "meta-desk-3", Open: "owner:m85 phone app; card-tap-hold"}, {Verb: "stand-down", Lane: "fix-lane-1"}} {
		refusal, err := StandingRefusal(s, allowed)
		if err != nil {
			t.Fatal(err)
		}
		equal(t, refusal, "")
	}
	draft := Request{Verb: "stand-down", Lane: "meta-desk-2", Successor: "meta-desk-3", Open: "owner:m85 phone app", By: Root}.Draft(testDrive)
	equal(t, []string{draft.Attrs["open"], draft.Text}, []string{"owner:m85 phone app", "registry request: stand-down meta-desk-2 for meta-desk-3 open: owner:m85 phone app"})
}

func TestADoneWhileTheLaneOwnsAnOpenPRIsRefusedNamingItAndTheBlockedForm(t *testing.T) {
	s := memory(t)
	if err := s.Own(lane2, []int{12, 13}); err != nil {
		t.Fatal(err)
	}
	states := func(states map[int]string) func([]int) (map[int]string, error) {
		return func([]int) (map[int]string, error) { return states, nil }
	}

	refusal, err := DoneRefusal(s, lane2, nil, "/Users/me", states(map[int]string{12: Closed, 13: NeedsWork}))
	if err != nil {
		t.Fatal(err)
	}
	contains(t, refusal, "open: #13\n")
	contains(t, refusal, "cci post --lane conflicts-2 --kind blocked --pr 13 --to <your desk>")

	for lane, found := range map[string]map[int]string{lane2: {12: Closed, 13: Closed}, "never-1": {}} {
		refusal, err := DoneRefusal(s, lane, nil, "/Users/me", states(found))
		if err != nil {
			t.Fatal(err)
		}
		equal(t, refusal, "")
	}
}

func TestASpriteDoneNamingAFileOnItsOwnDiskIsRefusedNamingTheDocFix(t *testing.T) {
	s := memory(t)
	registered(t, s, store.Lane{Name: "overdue-sweep-2", Role: "lane", Placement: placedOnSprite})
	registered(t, s, store.Lane{Name: "mac-sweep-1", Role: "lane", Placement: "orca"})
	none := func([]int) (map[int]string, error) { return nil, nil }
	done := func(lane string, said ...string) string {
		t.Helper()
		refusal, err := DoneRefusal(s, lane, said, "/Users/me", none)
		if err != nil {
			t.Fatal(err)
		}
		return refusal
	}

	for _, said := range [][]string{
		{"29 unmet. Unmet table with live lanes: ~/.vcache/scratch/overdue-sweep/unmet.md."},
		{"29 unmet", "table=$HOME/sweep/unmet.md"},
		{"29 unmet, table in (/home/sprite/sweep/unmet.md)"},
	} {
		refusal := done("overdue-sweep-2", said...)
		contains(t, refusal, "overdue-sweep-2 runs on a Sprite")
		contains(t, refusal, "`ccn doc add --body-file <path>` and cite the doc id")
	}
	contains(t, done("overdue-sweep-2", "table at ~/.vcache/scratch/overdue-sweep/unmet.md."), "read ~/.vcache/scratch/overdue-sweep/unmet.md once")

	for lane, said := range map[string][]string{
		"overdue-sweep-2": {"29 unmet. Unmet table with live lanes: ccn doc b50496a; merged yasyf/cc-skills#3713, https://github.com/yasyf/cc-skills/pull/3713, ran /codex", "brief=/Users/me/handoffs/plan.md", "file at /home/sprite/drive/carried/Users/me/.claude/scratch/overdue-picks-h/root-posts.zsh, which carry-back copies"},
		"mac-sweep-1":     {"29 unmet. Unmet table with live lanes: ~/.vcache/scratch/overdue-sweep/unmet.md"},
		"never-1":         {"table at /tmp/unmet.md"},
	} {
		equal(t, done(lane, said...), "")
	}
}

func TestADoneFromALaneStillStandingSaysToEndItsDutyFirst(t *testing.T) {
	s := memory(t)
	brief := filepath.Join(t.TempDir(), "brief.md")
	do(os.WriteFile(brief, []byte("# bk-reads-1\nStanding: read Buildkite logs until bk-desk is back\n"), 0o600))
	for _, lane := range []store.Lane{
		{Name: "bk-reads-1", Brief: brief},
		{Name: "port-desk-2", Brief: "/gone.md", Standing: "forward-port every old-tree PR"},
		{Name: "fix-lane-1", Brief: "/gone.md"},
	} {
		lane.Model, lane.Effort, lane.Role, lane.State = "opus", "high", "lane", store.LaneDesired
		if _, err := s.WantLane(lane, t0); err != nil {
			t.Fatal(err)
		}
	}
	done := func(lane string) string {
		t.Helper()
		refusal, err := DoneRefusal(s, lane, nil, "/Users/me", func([]int) (map[int]string, error) { return nil, nil })
		if err != nil {
			t.Fatal(err)
		}
		return refusal
	}

	equal(t, done("bk-reads-1"), "bk-reads-1 is standing (read Buildkite logs until bk-desk is back), and the supervisor relaunches a standing lane after its done. A desk keeps its duty and rotates instead of posting done. If the duty is over, delete the `Standing:` line in "+brief+", then post done.")
	contains(t, done("port-desk-2"), "If the duty is over, run `lr request done port-desk-2`, which ends the duty it took from the lane it succeeds, then post done.")
	equal(t, done("fix-lane-1"), "")
}

func TestARequestPostsOneTypedDecideToTheSupervisor(t *testing.T) {
	draft := Request{Verb: "stand-down", Lane: lane2, Successor: "conflicts-3", LastRead: "7", PRs: []int{1, 2}, By: Root}.Draft(testDrive)

	equal(t, draft, client.Draft{Drive: testDrive, Lane: Root, Kind: "decide", To: []string{Self}, Attrs: map[string]string{"verb": "stand-down", "lane": lane2, "successor": "conflicts-3", "last-read": "7"}, PRs: []string{"1", "2"}, Text: "registry request: stand-down conflicts-2 for conflicts-3 last-read=7 #1 #2"})
}

func TestBriefParsing(t *testing.T) {
	said := parseBrief("# x\nRegister slice: lane=x step=B13\nStanding: keep it up   \n\n**Deliverable:**\n  the thing\n")
	equal(t, said, Brief{Step: "B13", Standing: "keep it up", Deliverable: "the thing"})
	equal(t, parseBrief("nothing"), Brief{})
}

func TestAStandingLineOfNoneIsNoDuty(t *testing.T) {
	for text, duty := range map[string]string{
		"Standing: none; ends when the report lands\n":  "",
		"Standing: None.\n":                             "",
		"Standing: yes\n":                               "yes",
		"Standing: nonetheless keep the queue moving\n": "nonetheless keep the queue moving",
	} {
		equal(t, parseBrief("# x\n"+text).Standing, duty)
	}
}

func coordinator(t *testing.T, s *store.Store, brief string) *fake {
	t.Helper()
	f := newFake(t, []Record{spawned(1, Root, brief, t0.Add(-2*time.Hour), map[string]string{"model": "opus", "effort": "xhigh"})}, []Row{who(Root, true, t0.Add(-time.Hour))})
	tick(t, s, f, t0.Add(-time.Hour))
	f.posts = nil
	return f
}

func TestANumberedCoordinatorThatPostsWorkerDoneRelaunchesAsRoot(t *testing.T) {
	s := memory(t)
	f := newFake(t, []Record{spawned(1, "root-1", writeBrief(t), t0.Add(-2*time.Hour), map[string]string{"model": "opus", "effort": "xhigh"})}, []Row{who("root-1", true, t0.Add(-time.Hour))})
	tick(t, s, f, t0.Add(-time.Hour))
	finished := who("root-1", false, t0.Add(-mins(20)))
	finished.Succeeded = true
	f.peers = []Row{finished}

	tick(t, s, f, t0)

	equal(t, f.launches, []string{Root})
}

func TestADeadCoordinatorRelaunchesFromItsBriefUnderItsOwnNameAndTellsTheBridge(t *testing.T) {
	s, brief := memory(t), writeBrief(t)
	f := coordinator(t, s, brief)
	f.peers = []Row{who(Root, false, t0.Add(-mins(20)))}

	out := tick(t, s, f, t0)

	equal(t, f.launches, []string{Root})
	equal(t, out.Relaunched, []Relaunch{{Root, Root, "stopped, quiet for 20 min"}})
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
		{"the meta desk answers to the communicator", []store.Lane{{Name: desk, Role: RoleMeta}}, nil, OwnerRelay},
		{"a lane the meta desk launched answers to it with no launch line", []store.Lane{{Name: worker, Launcher: desk}, {Name: desk, Role: RoleMeta}}, nil, desk},
		{"a lane a meta lane's helper launched answers to that helper", []store.Lane{{Name: "board-mirror-1-finder", Launcher: worker}, {Name: worker, Launcher: desk}, {Name: desk, Role: RoleMeta}}, nil, worker},
		{"a meta lane whose launcher retired answers to the live meta desk", []store.Lane{{Name: worker, Launcher: "helper-1"}, {Name: "helper-1", Launcher: desk, State: store.LaneRetired, RetiredBy: "done"}, {Name: desk, Role: RoleMeta}}, nil, desk},
		{"a meta lane with no live meta desk answers to root", []store.Lane{{Name: worker, Launcher: desk}, {Name: desk, Role: RoleMeta, State: store.LaneRetired, RetiredBy: "done"}}, nil, Root},
		{"a desk a retired meta lane launched answers to root, never the communicator", []store.Lane{{Name: worker, Role: "desk", Launcher: "desks-move-1"}, {Name: "desks-move-1", Launcher: desk, State: store.LaneRetired, RetiredBy: "done"}, {Name: desk, Role: RoleMeta}}, nil, Root},
		{"a lane root launched answers to root", []store.Lane{{Name: worker, Launcher: Root}, {Name: Root}}, nil, Root},
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
			d := Deps{Desks: func() (map[string]string, error) { return c.desks, nil }}
			equal(t, owningDesk(s, d, c.lanes[0].Name), c.want)
		})
	}
}

func TestTheRegistryCountsTheMetaDeskAndEveryLaneLaunchedUnderItAsMeta(t *testing.T) {
	s, brief := memory(t), writeBrief(t)
	launch := func(seq int64, lane, by string, attrs map[string]string) Record {
		r := spawned(seq, lane, brief, t0.Add(-time.Hour), attrs)
		r.Lane = by
		return r
	}
	f := newFake(t, []Record{
		launch(1, "meta", OwnerRelay, map[string]string{"role": RoleMeta}),
		launch(2, "hook-gate-1", "meta", nil),
		launch(3, "hook-gate-1-finder", "hook-gate-1", nil),
		launch(4, "conflicts-2", Root, nil),
		launch(5, "meta", Self, map[string]string{"role": "desk"}),
	}, []Row{who("meta", true, t0), who("hook-gate-1", true, t0), who("hook-gate-1-finder", true, t0), who("conflicts-2", true, t0)})

	tick(t, s, f, t0)

	for lane, meta := range map[string]bool{"meta": true, "hook-gate-1": true, "hook-gate-1-finder": true, "conflicts-2": false, "unknown": false} {
		answer, err := AnswerRoute(s, lane)
		if err != nil {
			t.Fatal(err)
		}
		equal(t, answer.Meta, meta)
	}
	equal(t, row(t, s, "hook-gate-1").Launcher, "meta")
}

func TestAMetaLaneAdoptedBeforeItsSpawnRecordKeepsTheMetaRole(t *testing.T) {
	s, state := memory(t), t.TempDir()
	sessionBrief(t, state, "meta", "desk")
	f := newFake(t, nil, []Row{who("meta", true, t0)})
	f.deps.State = state

	tick(t, s, f, t0)

	equal(t, row(t, s, "meta").Role, RoleMeta)
}

func TestAMetaDesksSuccessorLaunchedByAnotherLaneStaysTheMetaDeskForItsHelpers(t *testing.T) {
	s, brief := memory(t), writeBrief(t)
	launch := func(seq int64, lane, by string, attrs map[string]string) Record {
		r := spawned(seq, lane, brief, t0.Add(-time.Hour), attrs)
		r.Lane = by
		return r
	}
	f := newFake(t, []Record{
		launch(1, "meta-desk-2", Root, map[string]string{"role": RoleMeta}),
		launch(2, "meta-desk-3", OwnerRelay, nil),
		launch(3, "sprite-pins-1", "meta-desk-3", nil),
	}, []Row{who("meta-desk-2", true, t0), who("meta-desk-3", true, t0), who("sprite-pins-1", true, t0)})

	tick(t, s, f, t0)

	equal(t, row(t, s, "meta-desk-3").Role, RoleMeta)
	for _, lane := range []string{"meta-desk-3", "sprite-pins-1"} {
		answer, err := AnswerRoute(s, lane)
		if err != nil {
			t.Fatal(err)
		}
		equal(t, answer.Meta, true)
	}
	equal(t, must(NoticeTo(s, "sprite-pins-1")), "meta-desk-3")
	equal(t, must(MetaDesk(s)), "meta-desk-3")
}

func TestASpawnThatTakesOverALiveLaneOfItsFamilyStandsItDownOnTheBus(t *testing.T) {
	s, brief := memory(t), writeBrief(t)
	f := adopted(t, s, brief, "")
	f.feed = append(f.feed, spawned(2, "conflicts-3", brief, t0, nil))
	f.peers = append(f.peers, who("conflicts-3", true, t0))

	tick(t, s, f, t0.Add(time.Minute))

	downs := f.ofKind("stand-down")
	equal(t, len(downs), 1)
	equal(t, []any{downs[0].To, downs[0].Successor}, []any{[]string{lane2}, "conflicts-3"})
	equal(t, row(t, s, lane2).RetiredBy, "conflicts-3")
}

func TestASuccessorKeepsTheRoleItsSpawnNames(t *testing.T) {
	s, brief := memory(t), writeBrief(t)
	f := newFake(t, []Record{
		spawned(1, "meta-desk-2", brief, t0.Add(-time.Hour), map[string]string{"role": RoleMeta}),
		spawned(2, "meta-desk-3", brief, t0.Add(-time.Hour), map[string]string{"role": "desk"}),
	}, []Row{who("meta-desk-2", true, t0), who("meta-desk-3", true, t0)})

	tick(t, s, f, t0)

	equal(t, row(t, s, "meta-desk-3").Role, "desk")
}

func TestAMetaLanesDefectsReachItsLauncherAndADriveLanesReachRoot(t *testing.T) {
	for _, tt := range []struct {
		name, launcher, want string
	}{
		{"launched by the meta desk", "meta-desk-2", "meta-desk-2"},
		{"launched by root", Root, Root},
	} {
		t.Run(tt.name, func(t *testing.T) {
			s := memory(t)
			registered(t, s, store.Lane{Name: "meta-desk-2", Role: RoleMeta})
			registered(t, s, store.Lane{Name: lane2, Launcher: tt.launcher})
			f := newFake(t, nil, nil)
			f.owed = []deletions.Owed{{ID: "a1b2c3d", Owner: lane2}}

			equal(t, refusedOwing(s, f.deps, lane2, t0), true)

			equal(t, f.posts[0].To, []string{tt.want, lane2})
		})
	}
}

func TestAHeldLanesBlockerReachesItsDeskAndNeverRoot(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "", 46001)
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
	f := adopted(t, s, writeBrief(t), "9f3c2e1", 46001)
	f.desks = map[string]string{lane2: "meta-desk-1"}
	registered(t, s, store.Lane{Name: "meta-desk-1"})
	f.peers = []Row{who(lane2, false, t0.Add(-mins(20))), who("meta-desk-1", true, t0)}

	out := tick(t, s, f, t0)

	equal(t, len(out.Relaunched), 1)
	equal(t, f.kinds(), []string{"report:meta-desk-1", "stand-down:" + lane2})
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
	f := adopted(t, s, writeBrief(t), "9f3c2e1", 46001)
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
	equal(t, f.posts[0].Text, "Open work by lane, so no queue grows unseen (retro row 133): tooling-1 2 defects, 1 ask (oldest #10); desk-3 1 blocker (oldest #13).")
}

func TestTheHourlyTickNamesEachUndeliveredOwnerOrderToTheCommunicatorOnce(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.orders = []owner.Row{{ID: "m2", Status: owner.Open}, {ID: "m85", Status: owner.Open}, {ID: "m38", Status: owner.InProgress, Lane: "rich-reply-1"}}
	at := t0.Add(mins(61))
	f.peers = []Row{who(lane2, true, at)}

	tick(t, s, f, at)

	equal(t, f.kinds(), []string{"report:" + OwnerRelay})
	equal(t, f.posts[0].Text, "2 owner asks newly open, orders with no live lane, no open PR and no landed or carried record and questions nobody answered: owner:m2, owner:m85. Answer each question and mark its row, restart each order, or mark a row that orders nothing no-order with `mcp__plugin_long-running_lr__owner_mark`; a lane delivering an order is launched with `lr launch lane --order owner:<row>`. 1 owner orders newly in progress, landed or carried with no live lane and no delivered proof: owner:m38. Post `cci post --kind report --attr delivered=owner:<row>` once he has what the row asked for, or restart the order. Each row is named once; `lr owner audit` lists them all.")

	f.posts = nil
	at = at.Add(mins(60))
	f.peers = []Row{who(lane2, true, at)}
	tick(t, s, f, at)
	equal(t, f.kinds(), []string{})

	f.orders = append(f.orders, owner.Row{ID: "m90", Status: owner.Open})
	at = at.Add(mins(60))
	f.peers = []Row{who(lane2, true, at)}
	tick(t, s, f, at)
	equal(t, f.kinds(), []string{"report:" + OwnerRelay})
	if text := f.posts[0].Text; !strings.Contains(text, "1 owner asks newly open") || !strings.Contains(text, "owner:m90") || strings.Contains(text, "owner:m2,") {
		t.Fatalf("report = %q, want only the new row", text)
	}
}

func TestEveryOtherHourTheParkedOrdersGoToTheProgressDeskAsOneNoteAndPageNobody(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	registered(t, s, store.Lane{Name: "progress-desk-2"})
	f.orders = []owner.Row{{ID: "m38", At: "Nov 3 4:34 PM", Text: "build the dashboard page for the reply tool", Status: owner.Parked, Lane: "rich-reply-1", Hold: "Left open: the dashboard page"}}

	for _, at := range []int{61, 121} {
		f.peers = []Row{who(lane2, true, t0.Add(mins(at))), who("progress-desk-2", true, t0.Add(mins(at)))}
		tick(t, s, f, t0.Add(mins(at)))
	}

	notes := f.ofKind("note")
	equal(t, len(notes), 1)
	equal(t, notes[0].To, []string{"progress-desk-2"})
	equal(t, notes[0].Text, "1 owner orders are parked behind a hold or a part left open, with no live lane and no open PR, for your update: m38 Nov 3 4:34 PM: build the dashboard page for the reply tool [parked: Left open: the dashboard page; last lane rich-reply]")
	for _, post := range f.posts {
		if post.Kind != "note" && strings.Contains(post.Text, "m38") {
			t.Fatalf("a parked order reached %v: %s", post.To, post.Text)
		}
	}
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

func TestOwnerOrdersWhoseLaneStoodDownTenMinutesAgoEscalateToTheRelayInOneBlockerOnce(t *testing.T) {
	s := memory(t)
	f := newFake(t, nil, nil)
	f.orders = []owner.Row{
		{ID: "m1747", Text: "something can't sit idle for an hour", Status: owner.Open, Lane: "item-93-presign-1"},
		{ID: "m1748", Text: "unfurl the platy link", Status: owner.Open, Lane: "slack-render-1"},
		{ID: "m2", Text: "a ruling no lane ever held", Status: owner.Open},
		{ID: "m38", Status: owner.InProgress, Lane: "rich-reply-1"},
	}

	escalated := func() []Post {
		return slices.DeleteFunc(slices.Clone(f.posts), func(p Post) bool { return !strings.HasPrefix(p.Text, "orders:") })
	}
	for sweep := range 6 {
		tick(t, s, f, t0.Add(time.Duration(sweep)*orphanSweep))
	}

	posts := escalated()
	if len(posts) != 1 || posts[0].Kind != "blocker" || !slices.Equal(posts[0].To, []string{OwnerRelay}) || !strings.HasPrefix(posts[0].Text, "orders:m1747,m1748: 2 of his open orders lost their lane") || strings.Contains(posts[0].Text, "m2,") || strings.Contains(posts[0].Text, "m38") {
		t.Fatalf("escalations = %+v, want one blocker to %s naming m1747 and m1748 alone", posts, OwnerRelay)
	}
}

func TestAQueueCitesItsOldestRecordBySeq(t *testing.T) {
	got := queueDepth([]Record{rec(11, Root, "ask", t0, "and this", "tooling-1"), rec(10, Root, "defect", t0.Add(-22*time.Hour), "fix the gate", "tooling-1")})
	equal(t, got, "tooling-1 1 defect, 1 ask (oldest #10)")
}

func TestAnInstallDeskRelaunchesOnTheMacWhateverItsBriefSays(t *testing.T) {
	s := memory(t)
	f := newFake(t, []Record{spawned(1, "install-desk-2", writeBrief(t), t0.Add(-time.Hour), map[string]string{"model": "opus", "effort": "medium"})}, []Row{who("install-desk-2", true, t0.Add(-mins(1)))})
	tick(t, s, f, t0.Add(-mins(1)))
	if err := s.SetLanePRs("install-desk-2", []int{46001}); err != nil {
		t.Fatal(err)
	}
	f.peers = []Row{who("install-desk-2", false, t0.Add(-mins(20)))}

	tick(t, s, f, t0)

	equal(t, f.launches, []string{"install-desk-3"})
	contains(t, f.briefs["install-desk-3"], "\nccx: place=local why=mac-build\n")
}
