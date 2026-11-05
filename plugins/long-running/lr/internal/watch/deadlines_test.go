package watch

import (
	"encoding/json"
	"errors"
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
	if _, err := s.Escalate("org/repo#4", "enqueue overdue; the queue said org/repo#4", "landing-desk-3", base.Add(3*time.Minute)); err != nil {
		t.Fatal(err)
	}
	second, err := Sweep(s, nil, nil, nil, base.Add(4*time.Minute), r.post)
	if err != nil {
		t.Fatal(err)
	}

	want(t, len(first), 1)
	want(t, first[0].To, "root")
	if !strings.HasPrefix(first[0].Cause, "repeat: enqueue overdue reached 3") || !strings.Contains(first[0].Cause, ": org/repo#1, org/repo#2, org/repo#3;") {
		t.Fatalf("cause = %q", first[0].Cause)
	}
	want(t, len(second), 0)
}

func TestAClassOverdueAtOnceIsOneRecordNamingEveryIDAndAloneKeepsItsOwnRecord(t *testing.T) {
	s, r := memory(t), &recorder{}
	now := at(t, "2026-11-03T12:28:00Z")
	standing := make([]Wait, 0, 36)
	for i := range 35 {
		key := "pick/" + strconv.Itoa(313000+i)
		standing = append(standing, Wait{Kind: unfinishedKind, Repo: UnfinishedNamespace, Head: key, Mover: machine.MoverRoot, Since: now.Add(-time.Hour), To: root, Cause: "unfinished: " + key + " still standing"})
	}
	standing = append(standing, Wait{Kind: gateKind, Repo: "org/repo", Number: 9, Mover: machine.MoverLane, Since: now.Add(-time.Hour), To: root, Cause: "gate overdue"})

	escalated, err := Apply(s, nil, standing, now, r.post)
	if err != nil {
		t.Fatal(err)
	}
	again, err := Apply(s, nil, standing, now.Add(time.Minute), r.post)
	if err != nil {
		t.Fatal(err)
	}

	want(t, len(escalated), 36)
	want(t, len(again), 0)
	want(t, len(r.posts), 2)
	class, alone := r.posts[0].body, r.posts[1].body
	if !strings.HasPrefix(class.Subject, "unfinished/35/") {
		t.Fatalf("subject = %q", class.Subject)
	}
	for _, wait := range standing[:35] {
		if !strings.Contains(class.Detail, wait.Head+"\n"+wait.Cause) {
			t.Fatalf("body lacks %s: %q", wait.Head, class.Detail)
		}
	}
	want(t, alone.Subject, "org/repo#9")
	want(t, alone.Detail, "")
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

	want(t, route("landing-desk"), "landing-desk-2")
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
				out, err := pass.Run(s, snapshot, t0.Add(time.Duration(step.Minutes)*time.Minute), r.post, all, route, func(lane string, _ int) (string, error) { return lane, nil }, func(string, string) error { return nil })
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

func TestOneRefusedPostLeavesTheRestOfTheSweepPosting(t *testing.T) {
	s, r := memory(t), &recorder{}
	asked := at(t, "2026-11-03T05:00:00Z")
	asks := []client.Record{{Seq: 201, Lane: "lane-b", Kind: "ask", At: asked, To: []string{"root"}, Text: "which head?"}}
	unfinished := []Wait{{Kind: unfinishedKind, Repo: UnfinishedNamespace, Head: "pick/1", Mover: machine.MoverRoot, Since: asked, To: root, Cause: "unfinished: cut at 6:30 AM?"}}
	refused := false
	post := func(to []string, body bus.Body) error {
		if !refused {
			refused = true
			return errors.New("cci: time 6:30 AM in the text is 71 minutes after the post time")
		}
		return r.post(to, body)
	}

	escalated, err := Sweep(s, nil, asks, unfinished, asked.Add(15*time.Minute), post)

	if err == nil || !strings.Contains(err.Error(), "71 minutes after the post time") {
		t.Fatalf("err = %v, want the refusal", err)
	}
	want(t, len(escalated), 2)
	want(t, len(r.posts), 1)
}

func TestAPortDeskThatStopsWalkingTellsRootOnceUntilItEndsOrWalksAgain(t *testing.T) {
	walked := at(t, "2026-11-03T21:54:00Z")
	census := func(seq int, when time.Time, attrs string) client.Record {
		t.Helper()
		var record client.Record
		line := `{"seq":` + strconv.Itoa(seq) + `,"lane":"old-tree-port-desk-1","kind":"state","topic":"port-census","at":"` + when.Format(time.RFC3339) + `","text":"ledger 793","fields":{"attrs":{` + attrs + `}}}`
		if err := json.Unmarshal([]byte(line), &record); err != nil {
			t.Fatal(err)
		}
		return record
	}
	s, r := memory(t), &recorder{}
	sweep := func(now time.Time, records ...client.Record) []pass.Escalated {
		t.Helper()
		escalated, err := Sweep(s, records, nil, nil, now, r.post)
		if err != nil {
			t.Fatal(err)
		}
		return escalated
	}
	if got := sweep(walked.Add(portCensusQuiet-time.Minute), census(1, walked, "")); len(got) != 0 {
		t.Fatalf("a walk inside its quiet window escalated: %+v", got)
	}
	late := sweep(walked.Add(portCensusQuiet), census(1, walked, ""))
	want(t, len(late), 1)
	want(t, late[0].To, "root")
	want(t, late[0].Subject, PortCensusTopic)
	if got := sweep(walked.Add(portCensusQuiet+time.Hour), census(1, walked, "")); len(got) != 0 {
		t.Fatalf("the same stop told root twice: %+v", got)
	}
	walkedAgain := walked.Add(8 * time.Hour)
	if got := sweep(walkedAgain.Add(time.Minute), census(1, walked, ""), census(2, walkedAgain, "")); len(got) != 0 {
		t.Fatalf("a new walk still escalated: %+v", got)
	}
	if got := sweep(walkedAgain.Add(portCensusQuiet), census(2, walkedAgain, "")); len(got) != 1 {
		t.Fatalf("a second stop told root %d times, want once", len(got))
	}
	if got := PortCensusWaits([]client.Record{census(3, walked, `"ended":"cutover"`)}, walked.Add(time.Hour*20)); len(got) != 0 {
		t.Fatalf("a census that says ports have ended still waits: %+v", got)
	}
	if got := PortCensusWaits([]client.Record{{Seq: 4, Lane: "lane-a", Kind: "state", At: walked, Text: "port-census"}}, walked.Add(time.Hour*20)); len(got) != 0 {
		t.Fatalf("a drive with no port census waits: %+v", got)
	}
}
