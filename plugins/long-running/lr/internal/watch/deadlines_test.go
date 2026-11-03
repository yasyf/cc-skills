package watch

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/pass"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

type posted struct {
	to   []string
	body bus.Escalation
}

type recorder struct{ posts []posted }

func (r *recorder) post(to []string, body bus.Body) error {
	if escalation, raised := body.(bus.Escalation); raised {
		r.posts = append(r.posts, posted{to, escalation})
	}
	return nil
}

func memory(t *testing.T) *store.Store {
	t.Helper()
	s, err := store.Memory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s
}

func at(t *testing.T, stamp string) time.Time {
	t.Helper()
	parsed, err := time.Parse(time.RFC3339, stamp)
	if err != nil {
		t.Fatal(err)
	}
	return parsed
}

func release12192(t *testing.T) []client.Record {
	t.Helper()
	data, err := os.ReadFile("testdata/release-12192.json")
	if err != nil {
		t.Fatal(err)
	}
	var records []client.Record
	if err := json.Unmarshal(data, &records); err != nil {
		t.Fatal(err)
	}
	return records
}

func upTo(records []client.Record, now time.Time) []client.Record {
	return slices.DeleteFunc(slices.Clone(records), func(r client.Record) bool { return r.At.After(now) })
}

func TestARelease12192StyleGateIsABlockerToRootFiveMinutesAfterTheOwnerOrderNeverAWait(t *testing.T) {
	s, r := memory(t), &recorder{}
	records := release12192(t)
	gate := time.Date(2026, 11, 3, 2, 32, 10, 0, time.UTC)
	reads := 0
	waiting := func(int) (Gate, error) {
		reads++
		return Gate{GateWaiting, gate}, nil
	}
	sweep := func(now time.Time, status func(int) (Gate, error)) []pass.Escalated {
		t.Helper()
		escalated, err := Sweep(s, upTo(records, now), nil, status, now, r.post)
		if err != nil {
			t.Fatal(err)
		}
		return escalated
	}

	if got := sweep(at(t, "2026-11-03T02:36:00Z"), waiting); len(got) != 0 {
		t.Fatalf("before any order: %+v", got)
	}
	want(t, reads, 0)
	order := at(t, "2026-11-03T02:37:55.585Z")
	if got := sweep(order, waiting); len(got) != 0 {
		t.Fatalf("at the order: %+v", got)
	}
	if got := sweep(order.Add(5*time.Minute-time.Second), waiting); len(got) != 0 {
		t.Fatalf("one second short: %+v", got)
	}
	late := sweep(order.Add(5*time.Minute), waiting)

	want(t, len(late), 1)
	want(t, late[0].To, "root")
	want(t, late[0].Subject, "release#12192")
	for _, words := range []string{"release 12192", "6:32 PM", "owner order #343888", "release-5"} {
		if !strings.Contains(late[0].Cause, words) {
			t.Fatalf("cause %q lacks %q", late[0].Cause, words)
		}
	}
	if got := sweep(order.Add(20*time.Minute), waiting); len(got) != 0 {
		t.Fatalf("second post: %+v", got)
	}
	want(t, len(r.posts), 1)
	open, err := s.OpenItems(ReleaseNamespace, 12192)
	if err != nil || len(open) != 1 || open[0].Mover != machine.MoverLane {
		t.Fatalf("open = %+v, %v", open, err)
	}

	approved := at(t, "2026-11-03T02:59:30Z")
	sweep(approved, func(int) (Gate, error) { return Gate{State: GateSettled}, nil })
	open, err = s.OpenItems(ReleaseNamespace, 12192)
	if err != nil || len(open) != 0 {
		t.Fatalf("after approval: %+v, %v", open, err)
	}
	reads = 0
	sweep(approved.Add(time.Minute), waiting)
	want(t, reads, 0)
}

func TestAGateReachedAfterTheOrderCountsFromTheGate(t *testing.T) {
	s, r := memory(t), &recorder{}
	records := release12192(t)[:2]
	gate := at(t, "2026-11-03T02:50:00Z")
	waiting := func(int) (Gate, error) { return Gate{GateWaiting, gate}, nil }

	early, err := Sweep(s, records, nil, waiting, gate.Add(4*time.Minute), r.post)
	if err != nil {
		t.Fatal(err)
	}
	late, err := Sweep(s, records, nil, waiting, gate.Add(5*time.Minute), r.post)
	if err != nil {
		t.Fatal(err)
	}

	want(t, len(early), 0)
	want(t, len(late), 1)
}

func TestAReleaseNoOwnerOrderNamesNeverWaitsOnADeadline(t *testing.T) {
	s, r := memory(t), &recorder{}
	records := release12192(t)[:1]

	escalated, err := Sweep(s, records, nil, func(int) (Gate, error) {
		t.Fatal("no status read for a release no order names")
		return Gate{}, nil
	}, at(t, "2026-11-03T04:00:00Z"), r.post)
	if err != nil {
		t.Fatal(err)
	}

	want(t, len(escalated), 0)
}

func TestParseGateReadsReleaseStatusFirstLine(t *testing.T) {
	now := at(t, "2026-11-03T02:40:00Z")
	since := time.Date(2026, 11, 2, 18, 32, 10, 0, pacific)
	cases := []struct {
		line string
		want Gate
	}{
		{"turbulent-ucevic 12192 (manual) waits on approval since 6:32:10 PM", Gate{GateWaiting, since}},
		{"turbulent-ucevic 12192 (manual) was approved by root at 6:59:00 PM", Gate{State: GateSettled}},
		{"turbulent-ucevic 12192 (manual) has no approve gate", Gate{State: GateSettled}},
		{"turbulent-ucevic 12192 (manual) stopped without approval", Gate{State: GateSettled}},
		{"turbulent-ucevic 12192 (manual) decides its approve gate when its plans finish", Gate{State: GatePlanning}},
	}
	for _, c := range cases {
		got, err := ParseGate(c.line, now)
		if err != nil {
			t.Fatal(err)
		}
		if got.State != c.want.State || !got.Since.Equal(c.want.Since) {
			t.Fatalf("%q = %+v, want %+v", c.line, got, c.want)
		}
	}
}

func TestAGateClockPastMidnightIsYesterdays(t *testing.T) {
	got, err := ParseGate("x waits on approval since 11:58:00 PM", time.Date(2026, 11, 3, 0, 3, 0, 0, pacific))
	if err != nil {
		t.Fatal(err)
	}
	if !got.Since.Equal(time.Date(2026, 11, 2, 23, 58, 0, 0, pacific)) {
		t.Fatalf("since = %v", got.Since)
	}
}

func TestAnOpenAskToRootReachesRootAfterFifteenMinutesOnceAndClosesWhenAnswered(t *testing.T) {
	s, r := memory(t), &recorder{}
	asked := at(t, "2026-11-03T02:00:00Z")
	asks := []client.Record{
		{Seq: 100, Lane: "lane-a", Kind: "ask", At: asked, To: []string{"root"}, Text: "which head?"},
		{Seq: 101, Lane: "lane-b", Kind: "ask", At: asked, To: []string{"drive-think-1"}, Text: "not coordinator's"},
		{Seq: 102, Lane: "root", Kind: "ask", At: asked, To: []string{"root"}, Text: "the coordinator's own"},
		{Seq: 103, Lane: "lane-c", Kind: "ask", At: asked.Add(-25 * time.Hour), To: []string{"root"}, Text: "older than the window"},
	}
	sweep := func(now time.Time, records []client.Record) []pass.Escalated {
		t.Helper()
		escalated, err := Sweep(s, records, asks, nil, now, r.post)
		if err != nil {
			t.Fatal(err)
		}
		return escalated
	}

	if got := sweep(asked.Add(14*time.Minute), nil); len(got) != 0 {
		t.Fatalf("early: %+v", got)
	}
	late := sweep(asked.Add(15*time.Minute), nil)
	want(t, len(late), 1)
	want(t, late[0].To, "root")
	want(t, late[0].Subject, "ask#100")
	if got := sweep(asked.Add(30*time.Minute), nil); len(got) != 0 {
		t.Fatalf("again: %+v", got)
	}
	answer := []client.Record{{Seq: 200, Lane: "root", Kind: "answer", At: asked.Add(40 * time.Minute), Re: 100}}
	sweep(asked.Add(41*time.Minute), answer)
	open, err := s.OpenItems(AskNamespace, 100)
	if err != nil || len(open) != 0 {
		t.Fatalf("open = %+v, %v", open, err)
	}
}

func TestAWorkThatWaitedOnALandedPRGoesToItsOwnLaneNeverRoot(t *testing.T) {
	said := at(t, "2026-11-03T02:00:00Z")
	waited := client.Record{Seq: 10, Lane: "lane-a", Kind: "state", At: said, Text: "the port waits on #48721"}
	landed := client.Record{Seq: 11, Lane: "landing-desk-3", Kind: "landed", At: said.Add(5 * time.Minute), Text: "LANDED #48721 at abc1234"}
	cases := []struct {
		name    string
		records []client.Record
		waits   int
	}{
		{"it landed and the lane is quiet", []client.Record{waited, landed}, 1},
		{"it has not landed", []client.Record{waited}, 0},
		{"the lane opened the work", []client.Record{waited, landed, {Seq: 12, Lane: "lane-a", Kind: "opened", At: said.Add(6 * time.Minute)}}, 0},
		{"the lane no longer waits on it", []client.Record{waited, landed, {Seq: 12, Lane: "lane-a", Kind: "state", At: said.Add(6 * time.Minute), Text: "port paused for the owner"}}, 0},
		{"a desk's line opens none", []client.Record{{Seq: 10, Lane: "finder-desk", Kind: "state", At: said, Text: "waits on #48721"}, landed}, 0},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := LandedWaits(c.records)
			want(t, len(got), c.waits)
			if c.waits == 1 {
				want(t, got[0].To, "lane-a")
				want(t, got[0].Since, landed.At)
				want(t, got[0].Within, waitWithin)
			}
		})
	}
}

func TestAClassOfEscalationAcrossThreeSubjectsInTwoHoursReachesRootOnce(t *testing.T) {
	s, r := memory(t), &recorder{}
	base := at(t, "2026-11-03T02:00:00Z")
	for i, subject := range []string{"org/repo#1", "org/repo#2", "org/repo#3"} {
		raised := base.Add(time.Duration(i) * time.Minute)
		if _, err := s.Escalate(subject, "enqueue overdue; the queue said "+subject, "landing-desk-3", raised); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := s.Escalate("org/repo#9", "fix-red overdue", "lane-a", base); err != nil {
		t.Fatal(err)
	}

	first, err := Sweep(s, nil, nil, nil, base.Add(3*time.Minute), r.post)
	if err != nil {
		t.Fatal(err)
	}
	second, err := Sweep(s, nil, nil, nil, base.Add(4*time.Minute), r.post)
	if err != nil {
		t.Fatal(err)
	}

	want(t, len(first), 1)
	want(t, first[0].To, "root")
	if !strings.HasPrefix(first[0].Cause, "repeat: enqueue overdue reached 3") {
		t.Fatalf("cause = %q", first[0].Cause)
	}
	want(t, len(second), 0)
}

func TestDesksRouteToTheNewestLiveMemberOfTheFamilyAndRootWhenNoDeskOwnsIt(t *testing.T) {
	s := memory(t)
	now := at(t, "2026-11-03T02:00:00Z")
	for _, name := range []string{"landing-desk-2", "landing-desk-3", "landing-desk-extra"} {
		if _, err := s.WantLane(store.Lane{Name: name, Brief: "b", Model: "opus", Effort: "xhigh", Role: "desk", State: store.LaneDesired}, now); err != nil {
			t.Fatal(err)
		}
	}
	if err := s.SetLaneState("landing-desk-3", store.LaneRetired, now, "", "done"); err != nil {
		t.Fatal(err)
	}

	route, err := Desks(s)
	if err != nil {
		t.Fatal(err)
	}

	want(t, route(pass.LandingDesk), "landing-desk-2")
	want(t, route(pass.BoardDesk), "root")
}

type fixtureStep struct {
	Minutes     int                        `json:"minutes"`
	Facts       []json.RawMessage          `json:"facts"`
	Expect      map[string]machine.Verdict `json:"expect"`
	Acts        []int                      `json:"acts"`
	Escalations []pass.Escalated           `json:"escalations"`
}

type classFixture struct {
	Name  string          `json:"name"`
	Cites []string        `json:"cites"`
	Base  json.RawMessage `json:"base"`
	Steps []fixtureStep   `json:"steps"`
}

func TestTheNovember2StallClassesEachReachTheirOwnerOrNeverStall(t *testing.T) {
	paths, err := filepath.Glob("testdata/nov2/*.json")
	if err != nil {
		t.Fatal(err)
	}
	if len(paths) == 0 {
		t.Fatal("no fixtures")
	}
	t0 := at(t, "2026-11-02T20:48:00Z")
	all := []machine.Mover{machine.MoverSupervisor, machine.MoverPool, machine.MoverLane}
	route := func(desk string) string { return desk + "-3" }
	for _, path := range paths {
		data, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		var fixture classFixture
		if err := json.Unmarshal(data, &fixture); err != nil {
			t.Fatal(err)
		}
		t.Run(fixture.Name, func(t *testing.T) {
			if len(fixture.Cites) == 0 {
				t.Fatal("a stall class fixture cites its records")
			}
			s, r := memory(t), &recorder{}
			for _, step := range fixture.Steps {
				snapshot := make([]machine.Facts, 0, len(step.Facts))
				for _, raw := range step.Facts {
					var facts machine.Facts
					if err := json.Unmarshal(fixture.Base, &facts); err != nil {
						t.Fatal(err)
					}
					if err := json.Unmarshal(raw, &facts); err != nil {
						t.Fatal(err)
					}
					snapshot = append(snapshot, facts)
				}
				out, err := pass.Run(s, snapshot, t0.Add(time.Duration(step.Minutes)*time.Minute), r.post, all, route)
				if err != nil {
					t.Fatal(err)
				}
				for number, expected := range step.Expect {
					pr, err := strconv.Atoi(number)
					if err != nil {
						t.Fatal(err)
					}
					got := out.Verdicts[pr]
					if got.State != expected.State || got.Mover != expected.Mover || got.Action != expected.Action {
						t.Fatalf("minute %d #%d = %+v, want %+v", step.Minutes, pr, got, expected)
					}
				}
				acts := make([]int, 0, len(out.Acts))
				for _, act := range out.Acts {
					if act.Verdict.Action == machine.ActionEnqueue {
						acts = append(acts, act.Facts.Number)
					}
				}
				if !slices.Equal(acts, step.Acts) && (len(acts) != 0 || len(step.Acts) != 0) {
					t.Fatalf("minute %d enqueues = %v, want %v", step.Minutes, acts, step.Acts)
				}
				if !reflect.DeepEqual(out.Escalated, step.Escalations) && (len(out.Escalated) != 0 || len(step.Escalations) != 0) {
					t.Fatalf("minute %d escalated = %+v, want %+v", step.Minutes, out.Escalated, step.Escalations)
				}
			}
		})
	}
}

func want[T comparable](t *testing.T, got, expected T) {
	t.Helper()
	if got != expected {
		t.Fatalf("got %v, want %v", got, expected)
	}
}
