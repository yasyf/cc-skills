package board

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/scaletest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	liveHead = "632c4a5aa1b2c3d4e5f60718293a4b5c6d7e8f90"
	tapped   = "2026-11-03T02:00:00Z"
)

func outcomesOf(t *testing.T, raw string) Outcomes {
	t.Helper()
	var outcomes Outcomes
	if err := json.Unmarshal([]byte(raw), &outcomes); err != nil {
		t.Fatal(err)
	}
	return outcomes
}

func liveOf(t *testing.T, raw string, snapshots ...Snapshot) Live {
	t.Helper()
	live, err := LiveOf(outcomesOf(t, raw), snapshots, nil)
	if err != nil {
		t.Fatal(err)
	}
	return live
}

func TestAPRWaitsOnHisTapOnlyWhenItsRowIsReadyAndUntapped(t *testing.T) {
	rows := []store.ReviewRow{
		{Number: 1, Head: liveHead, HeadAt: "2026-11-03T02:00:00Z", Title: "ready", ReadyForTap: true},
		{Number: 2, Head: liveHead, Title: "tapped", ReadyForTap: true, Tapped: true},
		{Number: 3, Head: liveHead, Title: "held", WaitsOn: "held, root"},
	}
	live, err := LiveOf(Outcomes{}, nil, rows)
	if err != nil {
		t.Fatal(err)
	}
	waits := live.Waits()
	if len(waits) != 1 || waits[0].Line() != "tap #1 at 632c4a5, head since 6:00 PM: ready" {
		t.Fatalf("waits = %+v", waits)
	}
}

func TestAVerdictStandsOnlyAtTheHeadItNames(t *testing.T) {
	live := liveOf(t, `{"interactions":{"triage":{"merge-prs":{"47321":{"verdict":"approved","head":"632c4a5","at":"`+tapped+`"}}}}}`)
	if got := live.Decided("47321", liveHead); got != verdictApproved {
		t.Fatalf("at its head = %q", got)
	}
	if got := live.Decided("47321", "aaaaaaa"); got != "" {
		t.Fatalf("at a later head = %q", got)
	}
}

func TestAPickWaitsUntilTheOwnerPicksOrWritesAnAnswer(t *testing.T) {
	doc := `{"doc":{"blocks":[{"id":"card-a","type":"card","title":"Which one?","children":[{"id":"open","type":"choice"},{"id":"picked","type":"choice"},{"id":"wrote","type":"choice"}]}]},`
	live := liveOf(t, doc+`"interactions":{"choices":{"picked":{"optionIds":["x"]},"wrote":{"other":"neither"}}}}`)
	if len(live.Picks) != 1 || live.Picks[0].Line() != "pick card-a open: Which one?, up since just now" {
		t.Fatalf("picks = %+v", live.Picks)
	}
}

func TestAWaitIsDatedByTheFirstOfTheUnbrokenRunOfSnapshotsThatShowIt(t *testing.T) {
	at := func(hour int) time.Time { return time.Date(2026, 11, 3, hour, 0, 0, 0, time.UTC) }
	kept, gone := `{"cards":[["card-a",[]]]}`, `{"cards":[["card-b",[]]]}`
	shown, err := firstShown([]Snapshot{{1, at(1), kept}, {2, at(2), gone}, {3, at(3), kept}, {4, at(4), kept}})
	if err != nil {
		t.Fatal(err)
	}
	if !shown["card:card-a"].Equal(at(3)) {
		t.Fatalf("shown = %v", shown)
	}
	if _, stayed := shown["card:card-b"]; stayed {
		t.Fatalf("a card off the newest snapshot stayed: %v", shown)
	}
}

func TestTheNewestVerdictAcrossBlocksWinsForAPR(t *testing.T) {
	newest := newestByPR(map[string]map[string]Verdict{
		"a": {"47321": {Verdict: "rejected", At: "2026-11-03T01:00:00Z"}},
		"b": {"merge-row-47321": {Verdict: "approved", At: "2026-11-03T02:00:00Z"}, "free-text": {Verdict: "approved"}},
	})
	if len(newest) != 1 || newest["47321"].Verdict != "approved" {
		t.Fatalf("newest = %+v", newest)
	}
}

type scripted struct{ result shell.Result }

func (s scripted) Run(context.Context, shell.Command) (shell.Result, error) {
	return s.result, nil
}

func TestABoardThatDoesNotReadIsNamed(t *testing.T) {
	_, err := ReadOutcomes(context.Background(), scripted{shell.Result{Code: 1, Stderr: "no board"}}, "s")
	if err == nil || err.Error() != "cc-present outcomes exited 1: no board" {
		t.Fatalf("err = %v", err)
	}
	outcomes, err := ReadOutcomes(context.Background(), scripted{shell.Result{Stdout: "note\n" + `{"doc":{"a":1},"interactions":{}}`}}, "s")
	if err != nil || outcomes.Doc["a"] != 1.0 {
		t.Fatalf("outcomes = %+v, %v", outcomes, err)
	}
}

func TestTheMainBoardIsTheArtifactBoundToTheRootSession(t *testing.T) {
	listing := "cc-present daemon 0.54.14 · port 61118\n\n" +
		"SUBJECT                           SLUG                                     SESSION      STATUS  EVENTS  URL\n" +
		"f02f306df1d09223c6ea40de1a5a8097  dashboard-mockups-for-your-tap--7b8a71f4  lane-session  open    12      http://127.0.0.1:61118/p/dashboard-mockups-for-your-tap--7b8a71f4\n" +
		"0c1d6f0e9b2a4c3d8e7f6a5b4c3d2e1f  drive-board--1a2b3c4d                     root-session  open    90      http://127.0.0.1:61118/p/drive-board--1a2b3c4d\n"
	names, err := MainBoard(context.Background(), scripted{shell.Result{Stdout: listing}}, "root-session")
	if err != nil || !slices.Equal(names, []string{"0c1d6f0e9b2a4c3d8e7f6a5b4c3d2e1f", "drive-board--1a2b3c4d"}) {
		t.Fatalf("names = %q, %v", names, err)
	}
	if _, err := MainBoard(context.Background(), scripted{shell.Result{Code: 1, Stderr: "no daemon"}}, "root-session"); err == nil {
		t.Fatal("an unread listing named a main board")
	}
}

type countedBoard struct {
	outcomes string
	calls    int
}

func (c *countedBoard) Run(context.Context, shell.Command) (shell.Result, error) {
	c.calls++
	return shell.Result{Stdout: c.outcomes}, nil
}

func TestWaitsPrintsALinePerReadyRowAndPickWithOneBoardReadAtAnyCount(t *testing.T) {
	const picks = 54
	blocks := make([]string, picks)
	for i := range blocks {
		blocks[i] = fmt.Sprintf(`{"id":"card-%d","type":"card","title":"Which %d?","children":[{"id":"pick-%d","type":"choice"}]}`, i, i, i)
	}
	outcomes := `{"doc":{"blocks":[` + strings.Join(blocks, ",") + `]},"interactions":{}}`
	for _, ready := range []int{47, scaletest.N()} {
		t.Run(strconv.Itoa(ready), func(t *testing.T) {
			home := t.TempDir()
			t.Setenv("CLAUDE_CONFIG_DIR", home)
			t.Setenv("CCI_HOME", t.TempDir())
			registry, state := filepath.Join(home, "long-running", "drives"), filepath.Join(home, "running")
			for _, dir := range []string{registry, state} {
				if err := os.MkdirAll(dir, 0o700); err != nil {
					t.Fatal(err)
				}
			}
			entry := fmt.Sprintf(`{"drive":"running","repo":%q,"state_dir":%q,"sessions":["s"]}`, repo, state)
			if err := os.WriteFile(filepath.Join(registry, "running.json"), []byte(entry), 0o600); err != nil {
				t.Fatal(err)
			}
			s, err := store.Open(filepath.Join(state, "lr.sqlite"))
			if err != nil {
				t.Fatal(err)
			}
			rows := []store.ReviewRow{{Repo: repo, Number: 1, Head: head, ReadyForTap: true, Tapped: true}, {Repo: repo, Number: 2, Head: head, WaitsOn: "held, root"}, {Repo: "other/repo", Number: 3, Head: head, ReadyForTap: true}}
			for i := range ready {
				rows = append(rows, store.ReviewRow{Repo: repo, Number: number + i, Head: head, ReadyForTap: true})
			}
			if err := s.PutReviewRows(rows); err != nil {
				t.Fatal(err)
			}
			if err := s.Close(); err != nil {
				t.Fatal(err)
			}
			board := &countedBoard{outcomes: outcomes}
			var out strings.Builder
			cmd := Command(&app.App{Runner: board, Now: func() time.Time { return t0 }})
			cmd.SetArgs([]string{"waits", "--drive", "running"})
			cmd.SetOut(&out)
			if err := cmd.Execute(); err != nil {
				t.Fatal(err)
			}
			lines := strings.Split(strings.TrimSpace(out.String()), "\n")
			if len(lines) != ready+picks || board.calls != 1 {
				t.Fatalf("%d lines with %d board reads, want %d and 1", len(lines), board.calls, ready+picks)
			}
			if want := fmt.Sprintf("tap #%d at e718a74", number); !strings.HasPrefix(lines[0], want) {
				t.Fatalf("first line = %q, want %q", lines[0], want)
			}
		})
	}
}
