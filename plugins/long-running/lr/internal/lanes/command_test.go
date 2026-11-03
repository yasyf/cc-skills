package lanes

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func driveState(t *testing.T) (*app.App, *store.Store) {
	t.Helper()
	dir := t.TempDir()
	path := filepath.Join(dir, "lr.json")
	writeJSON(t, path, map[string]any{"repo": "o/r", "drive": testDrive, "checkout": "/c", "author": "a", "label": "l", "movers": []string{"lanes"}})
	s, err := store.Open(filepath.Join(dir, StoreFile))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return &app.App{ConfigPath: path, Now: func() time.Time { return t0 }}, s
}

func execute(t *testing.T, cmd *cobra.Command, args ...string) (string, error) {
	t.Helper()
	var out bytes.Buffer
	cmd.SetOut(&out)
	cmd.SetErr(&out)
	cmd.SetArgs(args)
	cmd.SilenceUsage = true
	err := cmd.ExecuteContext(context.Background())
	return out.String(), err
}

func TestTheLanesVerbPrintsTheRegistryAndOneJSONObjectPerLane(t *testing.T) {
	a, s := driveState(t)
	if _, err := s.WantLane(storeLane("desk-1"), t0); err != nil {
		t.Fatal(err)
	}
	if err := s.LaneHeard("other-1", t0, "state", 4); err != nil {
		t.Fatal(err)
	}

	text, err := execute(t, Command(a), "desk-1", "never-1")
	if err != nil {
		t.Fatal(err)
	}
	lines := strings.Split(strings.TrimSpace(text), "\n")
	equal(t, len(lines), 2)
	if !strings.HasPrefix(lines[0], "desk-1 desired live heard never") {
		t.Fatal(lines[0])
	}
	equal(t, lines[1], "never-1 unregistered")

	text, err = execute(t, Command(a), "--json", "desk-1")
	if err != nil {
		t.Fatal(err)
	}
	var row map[string]any
	if err := json.Unmarshal([]byte(text), &row); err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{"lane", "state", "live", "heard", "prs", "handoff", "retired_by", "placement", "predecessor", "route", "brief", "step", "standing", "deliverable", "woke"} {
		if _, found := row[key]; !found {
			t.Fatalf("%q missing from %v", key, row)
		}
	}
	equal(t, row["prs"], []any{})
	equal(t, row["heard"], nil)
}

func TestTheRouteVerbPrintsOneObjectTheHooksRead(t *testing.T) {
	a, s := driveState(t)
	if _, err := s.WantLane(storeLane("desk-1"), t0); err != nil {
		t.Fatal(err)
	}
	if err := s.SetLaneState("desk-1", store.LaneRetired, t0, "", ""); err != nil {
		t.Fatal(err)
	}

	text, err := execute(t, RouteCommand(a), "desk-1")
	if err != nil {
		t.Fatal(err)
	}

	equal(t, strings.TrimSpace(text), `{"lane":"desk-1","route":"root","state":"retired","standing":""}`)
}

func TestTheRequestVerbPostsOneTypedRecordAndRefusesAStandingLaneAlone(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	a, s := driveState(t)
	standing := storeLane(refresher)
	standing.Brief = standingBrief(t, refresher)
	if _, err := s.WantLane(standing, t0); err != nil {
		t.Fatal(err)
	}

	if _, err := execute(t, RequestCommand(a), "stand-down", refresher); err == nil || !strings.Contains(err.Error(), "stands down only for a successor") {
		t.Fatalf("err %v", err)
	}
	if _, err := execute(t, RequestCommand(a), "shelve", refresher); err == nil || !strings.Contains(err.Error(), "unknown request") {
		t.Fatalf("err %v", err)
	}
	text, err := execute(t, RequestCommand(a), "stand-down", refresher, "--successor", "board-refresher-7", "--last-read", "9", "--pr", "12", "--by", "coordinator")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(text, "#") {
		t.Fatal(text)
	}

	records, err := client.Tail(context.Background(), client.Query{Drive: testDrive})
	if err != nil {
		t.Fatal(err)
	}
	equal(t, len(records), 1)
	r := recordOf(records[0])
	equal(t, []any{r.Lane, r.Kind, isRequest(r), r.Attr("verb"), r.Attr("lane"), r.Attr("successor"), r.Attr("last-read"), r.PRs()}, []any{"coordinator", "decide", true, "stand-down", refresher, "board-refresher-7", "9", []int{12}})
}

func TestTheStrandedPRBlockersCommandRunAsWrittenHandsThePRsToTheLaneItNames(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "", 45492, 46512)
	f.feed = append(f.feed, ask(2, "stand-down", lane2, t0.Add(-mins(5)), nil, "", map[string]string{"last-read": "1"}))
	f.states = map[int]string{45492: NeedsWork, 46512: NeedsWork}
	tick(t, s, f, t0.Add(time.Hour))

	blockers := f.ofKind("blocker")
	equal(t, len(blockers), 1)
	_, quoted, _ := strings.Cut(blockers[0].Text, "`")
	suggested, _, _ := strings.Cut(quoted, "`")
	equal(t, suggested, "lr request owner <lane> --pr 45492 --pr 46512")
	args := strings.Fields(strings.ReplaceAll(suggested, "<lane>", "conflicts-3"))
	a, _ := driveState(t)
	if _, err := execute(t, RequestCommand(a), args[2:]...); err != nil {
		t.Fatal(err)
	}
	records, err := client.Tail(context.Background(), client.Query{Drive: testDrive})
	if err != nil {
		t.Fatal(err)
	}
	equal(t, len(records), 1)
	posted := recordOf(records[0])
	posted.Seq = 3
	f.feed, f.posts = append(f.feed, posted), nil
	tick(t, s, f, t0.Add(time.Hour+mins(1)))

	equal(t, len(f.ofKind("defect")), 0)
	equal(t, owners(t, s, 45492, 46512), map[int]string{45492: "conflicts-3", 46512: "conflicts-3"})
}

func TestAMissingConfigNamesTheFlag(t *testing.T) {
	_, err := execute(t, Command(&app.App{}))
	if err == nil || !strings.Contains(err.Error(), "--config") {
		t.Fatalf("err %v", err)
	}
	if _, statErr := os.Stat("lr.sqlite"); statErr == nil {
		t.Fatal("opened a store in the working directory")
	}
}
