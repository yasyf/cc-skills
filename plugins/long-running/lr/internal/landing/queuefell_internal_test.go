package landing

import (
	"context"
	"encoding/json"
	"strconv"
	"testing"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func queueRecord(t *testing.T, outcome, repo string, number int) client.Record {
	t.Helper()
	body, err := json.Marshal(map[string]any{
		"seq": 4, "lane": "trunk-webhook", "kind": "queue", "text": "removed from the queue",
		"fields": map[string]string{"outcome": outcome},
		"refs":   map[string]any{"prs": []string{repo + "#" + strconv.Itoa(number)}},
	})
	if err != nil {
		t.Fatal(err)
	}
	var record client.Record
	if err := json.Unmarshal(body, &record); err != nil {
		t.Fatal(err)
	}
	return record
}

func TestOnlyAFailedOrCanceledRecordNamingAnOpenPRInTheRepoFallsOut(t *testing.T) {
	const repo = "Forge-AI/monorepo"
	prs := []store.PR{{Number: 7, Head: "abc", Lane: "lane-1", State: machine.StateQueued}, {Number: 8, Head: "def", State: machine.StateLanded}}
	for name, c := range map[string]struct {
		outcome, repo string
		number, fell  int
	}{
		"failed":        {"failed", repo, 7, 1},
		"canceled":      {"canceled", repo, 7, 1},
		"passed":        {"passed", repo, 7, 0},
		"another repo":  {"failed", "yasyf/cc-skills", 7, 0},
		"already gone":  {"failed", repo, 8, 0},
		"unknown to us": {"failed", repo, 9, 0},
	} {
		t.Run(name, func(t *testing.T) {
			if got := fellOut(queueRecord(t, c.outcome, c.repo, c.number), repo, prs); len(got) != c.fell {
				t.Fatalf("fell = %+v", got)
			}
		})
	}
}

func TestAPassOverNoOutcomePostsNothingAndMovesTheCursor(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	s, err := store.Memory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	posted, err := client.Post(context.Background(), client.Draft{Drive: queueDrive, Lane: "trunk-webhook", Kind: queueKind, PRs: []string{"Forge-AI/monorepo#7"}, Text: "entered the queue"})
	if err != nil {
		t.Fatal(err)
	}

	if err := (QueueFell{Store: s, Drive: "d", Repo: "Forge-AI/monorepo"}).Pass(context.Background()); err != nil {
		t.Fatal(err)
	}

	cursor, err := s.Cursor(queueCursor)
	if err != nil {
		t.Fatal(err)
	}
	if cursor != posted.Seq {
		t.Fatalf("cursor = %d, want %d", cursor, posted.Seq)
	}
	records, err := client.Tail(context.Background(), client.Query{Drive: "d"})
	if err != nil {
		t.Fatal(err)
	}
	if len(records) != 0 {
		t.Fatalf("posted %+v", records)
	}
}
