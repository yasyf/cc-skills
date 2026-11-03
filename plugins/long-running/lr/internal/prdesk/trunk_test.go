package prdesk

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

type queueKey struct{}

func (queueKey) Run(context.Context, shell.Command) (shell.Result, error) {
	return shell.Result{Stdout: "k\n"}, nil
}

func TestATrunkReadAsksForThePRsTimelineWithTheQueueKey(t *testing.T) {
	var asked map[string]any
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("x-api-token") != "k" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		_ = json.NewDecoder(r.Body).Decode(&asked)
		_, _ = io.WriteString(w, `{"id":"e1","state":"testing","prSha":"`+h1+`"}`)
	}))
	t.Cleanup(server.Close)
	read := TrunkRead{Runner: queueKey{}, KeyCommand: "print-key", URL: server.URL, HTTP: server.Client(), Repo: repo, Landing: landing}
	got, err := read.Read(context.Background(), 7)
	if err != nil || got.ID != "e1" || got.Head != h1 || got.State != "testing" {
		t.Fatalf("read %+v, %v", got, err)
	}
	pr, _ := asked["pr"].(map[string]any)
	if asked["includeTimeline"] != true || pr["number"] != float64(7) || asked["targetBranch"] != landing {
		t.Fatalf("asked %v", asked)
	}
}

func TestATrunkEntryNamesWhyTheQueueDroppedIt(t *testing.T) {
	for _, tc := range []struct {
		answer  string
		outcome string
		reason  machine.Eviction
	}{
		{`{"state":"testing"}`, QueueIn, ""},
		{`{"state":"failed","timeline":[{"failure":{"reason":"start_test_run_merge_conflict"}}]}`, QueueOut, machine.EvictionConflict},
		{`{"state":"failed","timeline":[{"failure":{"reason":"test_run_required_status_failed"}}]}`, QueueOut, machine.EvictionOwnRed},
		{`{"state":"failed","timeline":[{"failure":{"reason":"test_run_timeout"}}]}`, QueueOut, machine.EvictionFlaky},
		{`{"state":"cancelled","timeline":[{"cancellation":{"reason":"pr_pushed_to"}}]}`, QueueOut, machine.EvictionOurs},
		{`{"state":"cancelled"}`, QueueOut, machine.EvictionFlaky},
		{`{"state":"merged"}`, "", ""},
	} {
		var entry Submitted
		if err := json.Unmarshal([]byte(tc.answer), &entry); err != nil {
			t.Fatal(err)
		}
		if outcome, reason := entry.outcome(); outcome != tc.outcome || reason != tc.reason {
			t.Errorf("%s reads as %q %q, want %q %q", tc.answer, outcome, reason, tc.outcome, tc.reason)
		}
	}
}

func TestAFlakyEvictionReentersOnceThroughTheTrunkRecordsThenGoesToTheLane(t *testing.T) {
	s, c := shadowOf(t, &[]client.Draft{}), &clock{}
	apply(t, s.Desk, append(clean(c, 0, 7, "lane/a", landing, h1), Tap{c.at(1), 7, h1, machine.TapTapped})...)
	answers := []string{
		`{"id":"e1","state":"testing","prSha":"` + h1 + `"}`,
		`{"id":"e1","state":"failed","prSha":"` + h1 + `","timeline":[{"failure":{"reason":"test_run_timeout"}}]}`,
		`{"id":"e2","state":"testing","prSha":"` + h1 + `"}`,
		`{"id":"e2","state":"cancelled","prSha":"` + h1 + `"}`,
		`{"id":"e2","state":"cancelled","prSha":"` + h1 + `"}`,
	}
	var next int
	s.Trunk = func(context.Context, int) (Submitted, error) {
		var entry Submitted
		err := json.Unmarshal([]byte(answers[next]), &entry)
		next++
		return entry, err
	}
	run := func(e Event) error {
		_, err := s.Desk.Apply(e)
		return err
	}
	for i, want := range []machine.State{machine.StateQueued, machine.StateEvicted, machine.StateQueued, machine.StateNeedsFix, machine.StateNeedsFix} {
		record := client.Record{Seq: int64(50 + i), ID: "trunk:" + string(rune('a'+i)), Kind: "queue", At: t0.Add(time.Duration(2+i) * time.Minute)}
		record.Refs.PRs = append(record.Refs.PRs, struct {
			Repo   string
			Number int
		}{repo, 7})
		if err := s.queued(context.Background(), record, run); err != nil {
			t.Fatal(err)
		}
		expect(t, s.Desk, 7, want)
	}
}
