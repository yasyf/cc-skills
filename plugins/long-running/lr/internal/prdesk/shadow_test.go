package prdesk

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const board = "144ffadb3d797d00"

func shadowOf(t *testing.T, posted *[]client.Draft) Shadow {
	return Shadow{
		Desk: memory(t), Repo: repo, Drive: "drive", To: []string{"meta-desk-3"}, Board: boardLane, Origin: board,
		Graders: []string{"finder-desk", "fidelity-pipeline"},
		Post: func(_ context.Context, d client.Draft) (client.Record, error) {
			*posted = append(*posted, d)
			return client.Record{}, nil
		},
		Now: func() time.Time { return t0.Add(time.Hour) },
	}
}

func typed(kind, lane, origin string, body map[string]any) client.Record {
	raw, _ := json.Marshal(body)
	r := client.Record{Seq: 9, Lane: lane, Origin: origin, At: t0, Body: raw, Kind: "lr.grade"}
	if kind == "tap" {
		r.Kind = "lr.tap"
	}
	return r
}

func TestOnlyTheBoardsTapAndAGradersGradeCount(t *testing.T) {
	s := shadowOf(t, &[]client.Draft{})
	tap := map[string]any{"pr": 7, "head": h1, "by": "owner", "value": "tapped"}
	grade := map[string]any{"pr": 7, "head": h1, "grader": "finder", "verdict": "clear"}
	for _, tc := range []struct {
		name   string
		record client.Record
		counts bool
	}{
		{"a tap from the board on its own node", typed("tap", boardLane, board, tap), true},
		{"a tap a lane posted", typed("tap", "landing-desk-3", board, tap), false},
		{"a tap posted as the board from another node", typed("tap", boardLane, "76609ca622323ef9", tap), false},
		{"a grade from the finder desk", typed("grade", "finder-desk-2", "76609ca622323ef9", grade), true},
		{"a grade a lane posted", typed("grade", "m17-build-1", board, grade), false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if got := len(s.events(tc.record)) == 1; got != tc.counts {
				t.Fatalf("counts = %v, want %v", got, tc.counts)
			}
		})
	}
}

func TestTheShadowPostsOnceWhereItDisagreesWithTheRunner(t *testing.T) {
	var posted []client.Draft
	s := shadowOf(t, &posted)
	row := func(state machine.State, deadline time.Time) store.PRState {
		return store.PRState{Repo: repo, Number: 7, Head: h1, State: state, Reason: "its reason", Deadline: deadline}
	}
	moved := []Moved{
		{row(machine.StateAwaitingTap, time.Time{}), row(machine.StateReady, t0.Add(10*time.Minute))},
		{row(machine.StateReady, t0.Add(10*time.Minute)), row(machine.StateReady, t0.Add(2*time.Hour))},
		{row(machine.StateHeld, time.Time{}), row(machine.StateQueued, time.Time{})},
		{row(machine.StateReady, time.Time{}), row(machine.StateQueued, time.Time{})},
	}
	if err := s.disagree(context.Background(), moved); err != nil {
		t.Fatal(err)
	}
	causes := make([]string, 0, len(posted))
	for _, d := range posted {
		causes = append(causes, d.ID[strings.LastIndex(d.ID, ":")+1:])
	}
	if strings.Join(causes, ",") != "unqueued,merged-outside-queue" {
		t.Fatalf("posted %v, want one unqueued note for the overdue ready PR and one merged-outside-queue note for the held PR Trunk never held", causes)
	}
	if err := s.Desk.Store.Update(func(tx *store.Tx) error {
		return tx.PutEntry(store.Entry{Repo: repo, Number: 7, ID: "e1", Head: h1, Attempt: 1, Outcome: QueueIn})
	}); err != nil {
		t.Fatal(err)
	}
	posted = nil
	if err := s.disagree(context.Background(), moved[2:3]); err != nil || len(posted) != 1 || !strings.HasSuffix(posted[0].ID, ":landed-early") {
		t.Fatalf("a held PR Trunk took posted %+v, %v, want landed-early", posted, err)
	}
}

type pages struct {
	ccx.Client
	open  int
	calls int
}

func (p *pages) GH(context.Context, string, ...string) (string, error) {
	p.calls++
	var out strings.Builder
	for start := 0; start < p.open; start += 100 {
		nodes := make([]map[string]any, 0, 100)
		for n := start; n < min(start+100, p.open); n++ {
			nodes = append(nodes, map[string]any{"number": n + 1, "headRefName": fmt.Sprintf("lane/%d", n), "baseRefName": landing, "headRefOid": h1, "updatedAt": t0, "author": map[string]any{"login": "yasyf"}})
		}
		page, _ := json.Marshal(map[string]any{"data": map[string]any{"repository": map[string]any{"pullRequests": map[string]any{"nodes": nodes}}}})
		out.Write(page)
	}
	return out.String(), nil
}

func TestTheMinuteReadMakesOneGitHubCallAtAnyNumberOfOpenPRs(t *testing.T) {
	for _, open := range []int{1, 1000} {
		gh := &pages{open: open}
		read := GitHubRead{CCX: gh, Store: memory(t).Store, Repo: repo, Author: "yasyf"}
		events, err := read.Read(context.Background(), t0)
		if err != nil {
			t.Fatal(err)
		}
		if gh.calls != 1 || len(events) != open {
			t.Fatalf("%d open PRs took %d GitHub calls for %d events, want 1 call and %d events", open, gh.calls, len(events), open)
		}
	}
}

func TestAHoldRecordHoldsItsPRsUntilItsOwnLifterResolvesIt(t *testing.T) {
	s, c := shadowOf(t, &[]client.Draft{}), &clock{}
	apply(t, s.Desk, append(clean(c, 0, 7, "lane/a", landing, h1), Tap{c.at(1), 7, h1, machine.TapTapped})...)
	hold := client.Record{Seq: 40, Lane: "root", Kind: "hold", At: t0.Add(2 * time.Minute), Text: "readers first"}
	hold.Refs.PRs = append(hold.Refs.PRs, struct {
		Repo   string
		Number int
	}{repo, 7})
	resolve := func(seq int64, lane string) client.Record {
		return client.Record{Seq: seq, Lane: lane, Kind: "answer", At: t0.Add(3 * time.Minute), Resolves: 40}
	}
	for _, step := range []struct {
		record client.Record
		want   machine.State
	}{
		{hold, machine.StateHeld},
		{resolve(41, "landing-desk-3"), machine.StateHeld},
		{resolve(42, "root"), machine.StateReady},
	} {
		apply(t, s.Desk, s.events(step.record)...)
		expect(t, s.Desk, 7, step.want)
	}
}

func TestAFailedDiffHashSkipsThatReadAndHashesOnlyIntoTheDesksOwnRefs(t *testing.T) {
	s := shadowOf(t, &[]client.Draft{})
	s.Log = slog.New(slog.DiscardHandler)
	s.Hash = func(context.Context, string, string) (string, error) {
		return "", errors.New("cannot lock ref 'refs/remotes/origin/dev'")
	}
	e, err := s.changed(context.Background(), PullRequest{readMeta(), 7, "lane/a", landing, h1, true, false, ""})
	if e != nil || err != nil {
		t.Fatalf("a failed hash gave %+v, %v, want the read skipped", e, err)
	}
	fake := &shelltest.Fake{}
	fake.Reply("", "git", "fetch")
	fake.Reply("diff --git a/x b/x\n", "git", "diff")
	if _, err := hashDiff(context.Background(), fake, "/checkout", landing, h1); err != nil {
		t.Fatal(err)
	}
	for _, call := range fake.Calls() {
		if strings.Contains(strings.Join(call.Argv, " "), "refs/remotes/") {
			t.Fatalf("the hash wrote a shared tracking ref: %q", call.Argv)
		}
	}
	if !slices.Contains(fake.Calls()[0].Argv, "+refs/heads/"+landing+":refs/pr-desk/"+landing) {
		t.Fatalf("fetch ran as %q", fake.Calls()[0].Argv)
	}
}

func readMeta() Meta { return Meta{Source: readSource, Seq: 1, Key: "pr:7", At: t0, Repo: repo} }
