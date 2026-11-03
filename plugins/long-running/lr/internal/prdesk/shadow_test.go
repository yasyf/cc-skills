package prdesk

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
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
	if strings.Join(causes, ",") != "unqueued,landed-early" {
		t.Fatalf("posted %v, want one unqueued note for the overdue ready PR and one landed-early note for the held PR", causes)
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

func githubRecord(t *testing.T, seq int64, topic, pr, outcome, commit string, attrs map[string]string) client.Record {
	t.Helper()
	refs := map[string]any{}
	if pr != "" {
		refs["prs"] = []string{pr}
	}
	raw, err := json.Marshal(map[string]any{
		"seq": seq, "id": fmt.Sprintf("github:d%d", seq), "drive": "github", "kind": "github", "at": t0, "topic": topic,
		"refs": refs, "fields": map[string]any{"outcome": outcome, "commit": commit, "attrs": attrs},
	})
	if err != nil {
		t.Fatal(err)
	}
	var r client.Record
	if err := json.Unmarshal(raw, &r); err != nil {
		t.Fatal(err)
	}
	return r
}

func githubShadow(t *testing.T, posted *[]client.Draft) Shadow {
	s := shadowOf(t, posted)
	s.Author, s.Label = "lane-bot", "drive"
	return s
}

func pushed(author, labels string) map[string]string {
	return map[string]string{"author": author, "labels": labels, "base": landing, "branch": "lane/fix", "draft": "false"}
}

func TestOnlyTheDrivesPullRequestsOfThisRepoBecomeEvents(t *testing.T) {
	s := githubShadow(t, &[]client.Draft{})
	for _, tc := range []struct {
		name   string
		record client.Record
		want   []Event
	}{
		{
			"a push by the drive's author", githubRecord(t, 1, "pull_request", repo+"#7", "synchronize", h1, pushed("lane-bot", "")),
			[]Event{PullRequest{Meta{githubSource, 1, "github:d1", t0, repo}, 7, "lane/fix", landing, h1, true, false, ""}},
		},
		{
			"a merge carrying the drive's label", githubRecord(t, 2, "pull_request", repo+"#8", "merged", h2, pushed("someone", "infra,drive")),
			[]Event{PullRequest{Meta{githubSource, 2, "github:d2", t0, repo}, 8, "lane/fix", landing, h2, false, true, ""}},
		},
		{"someone else's unlabeled pull request", githubRecord(t, 3, "pull_request", repo+"#9", "opened", h1, pushed("someone", "")), nil},
		{"the drive's author in another repo", githubRecord(t, 4, "pull_request", "org/other#7", "opened", h1, pushed("lane-bot", "")), nil},
		{"a push to the default branch", githubRecord(t, 5, "push", "", "pushed", h2, map[string]string{"repo": repo, "branch": landing}), nil},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got, err := s.githubEvents(tc.record)
			if err != nil {
				t.Fatal(err)
			}
			if fmt.Sprint(got) != fmt.Sprint(tc.want) {
				t.Fatalf("events = %+v, want %+v", got, tc.want)
			}
		})
	}
}

func TestAReviewCountsOnceTheDeskTracksItsPullRequest(t *testing.T) {
	s := githubShadow(t, &[]client.Draft{})
	review := githubRecord(t, 2, "review", repo+"#7", "approved", h1, map[string]string{"reviewer": "owner"})
	if got, err := s.githubEvents(review); err != nil || len(got) != 0 {
		t.Fatalf("an untracked review made %v, %v", got, err)
	}
	if _, err := s.Desk.Apply(PullRequest{Meta{githubSource, 1, "github:d1", t0, repo}, 7, "lane/fix", landing, h1, true, false, ""}); err != nil {
		t.Fatal(err)
	}
	got, err := s.githubEvents(review)
	if err != nil || fmt.Sprint(got) != fmt.Sprint([]Event{Review{Meta{githubSource, 2, "github:d2", t0, repo}, 7, "owner", "approved", h1}}) {
		t.Fatalf("a tracked review made %+v, %v", got, err)
	}
}

func TestOnlyARequiredCheckOfThisRepoBecomesAnEvent(t *testing.T) {
	s := githubShadow(t, &[]client.Draft{})
	for _, tc := range []struct {
		name  string
		attrs map[string]string
		want  []Event
	}{
		{
			"a required check",
			map[string]string{"check": "ci", "id": "7001", "repo": repo},
			[]Event{Check{Meta{githubSource, 1, "github:d1", t0, repo}, h1, "ci", "failure", 7001}},
		},
		{"a check no rule requires", map[string]string{"check": "lint", "id": "7002", "repo": repo}, nil},
		{"a required check name in another repo", map[string]string{"check": "ci", "id": "7003", "repo": "org/other"}, nil},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got, err := s.githubEvents(githubRecord(t, 1, "check", "", "failure", h1, tc.attrs))
			if err != nil || fmt.Sprint(got) != fmt.Sprint(tc.want) {
				t.Fatalf("events = %+v, %v, want %+v", got, err, tc.want)
			}
		})
	}
}

func TestTheHourlyReadFilesOneDefectNamingEachPullRequestTheRecordsGotWrong(t *testing.T) {
	var posted []client.Draft
	s := githubShadow(t, &posted)
	for _, e := range []Event{
		PullRequest{Meta{githubSource, 1, "github:d1", t0, repo}, 7, "lane/fix", landing, h1, true, false, ""},
		PullRequest{Meta{githubSource, 2, "github:d2", t0, repo}, 8, "lane/next", landing, h1, true, false, ""},
	} {
		if _, err := s.Desk.Apply(e); err != nil {
			t.Fatal(err)
		}
	}
	read := Meta{readSource, t0.Unix(), "", t0.Add(time.Hour), repo}
	agreeing := []Event{PullRequest{read, 7, "lane/fix", landing, h1, true, false, ""}, PullRequest{read, 8, "lane/next", landing, h1, true, false, ""}}
	if err := s.audit(context.Background(), agreeing); err != nil || len(posted) != 0 {
		t.Fatalf("an agreeing read posted %v, %v", posted, err)
	}
	if err := s.audit(context.Background(), []Event{
		PullRequest{read, 7, "lane/fix", landing, h2, true, false, ""},
		PullRequest{read, 8, "lane/next", landing, h1, false, true, ""},
		PullRequest{read, 9, "lane/new", landing, h1, true, false, ""},
	}); err != nil {
		t.Fatal(err)
	}
	if len(posted) != 1 || posted[0].Kind != "defect" {
		t.Fatalf("posted %+v, want one defect", posted)
	}
	for _, want := range []string{"#7 records say open at 1111111111", "GitHub says open at 2222222222", "#8 records say open", "GitHub says shut", "#9 is open at 1111111111 with no record"} {
		if !strings.Contains(posted[0].Text, want) {
			t.Fatalf("defect %q does not name %q", posted[0].Text, want)
		}
	}
}
