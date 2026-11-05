package lanes

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
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

func TestAdoptMetaMakesTheRouteAnswerMetaForTheDeskAndItsListedLanes(t *testing.T) {
	a, s := driveState(t)
	byRoot := storeLane("by-root")
	byRoot.Launcher = Root
	for _, lane := range []store.Lane{storeLane("meta-desk-2"), storeLane("helper-1"), byRoot, storeLane("drive-lane")} {
		if _, err := s.WantLane(lane, t0); err != nil {
			t.Fatal(err)
		}
	}
	listed := filepath.Join(t.TempDir(), "mylanes.txt")
	if err := os.WriteFile(listed, []byte("helper-1\nold-name\nby-root\nmeta-desk-2\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	text, err := execute(t, adoptMetaCommand(a), "meta-desk-2", "--from", listed)
	if err != nil {
		t.Fatal(err)
	}

	equal(t, text, "meta-desk-2 is the meta lane; 1 lanes adopted; 1 have no row: old-name\n")
	for lane, meta := range map[string]bool{"meta-desk-2": true, "helper-1": true, "by-root": false, "drive-lane": false} {
		answer, err := AnswerRoute(s, lane)
		if err != nil {
			t.Fatal(err)
		}
		equal(t, answer.Meta, meta)
	}
	equal(t, row(t, s, "by-root").Launcher, Root)
	desk, err := MetaDesk(s)
	if err != nil {
		t.Fatal(err)
	}
	equal(t, desk, "meta-desk-2")
}

func TestAdoptMetaRefusesADeskWithNoRow(t *testing.T) {
	a, _ := driveState(t)
	listed := filepath.Join(t.TempDir(), "mylanes.txt")
	if err := os.WriteFile(listed, nil, 0o600); err != nil {
		t.Fatal(err)
	}

	if _, err := execute(t, adoptMetaCommand(a), "no-such-desk", "--from", listed); err == nil || !strings.Contains(err.Error(), "no-such-desk") {
		t.Fatalf("err %v", err)
	}
}

func TestTheSameVerbPrintsTheNamesThatMeanOneLane(t *testing.T) {
	a, _ := driveState(t)
	for lane, want := range map[string]string{
		"bugs-2": "bugs\nbugs-2\n",
		"bugs":   "bugs\nbugs-2\nbugs-3\n",
	} {
		out, err := execute(t, Command(a), "same", lane, "bugs", "bugs-2", "bugs-3", "bugs-fix", "other-2")
		if err != nil {
			t.Fatal(err)
		}
		equal(t, out, want)
	}
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
	for _, key := range []string{"lane", "state", "live", "heard", "prs", "handoff", "retired_by", "placement", "predecessor", "route", "brief", "step", "standing", "deliverable", "woke", "node", "late_minutes"} {
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

	equal(t, strings.TrimSpace(text), `{"lane":"desk-1","route":"root","state":"retired","standing":"","meta":false}`)
}

func TestEveryVerbTakesAStemForTheLaneThatHoldsIt(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	a, s := driveState(t)
	for _, lane := range []string{"desk-2", "desk-3"} {
		if _, err := s.WantLane(storeLane(lane), t0); err != nil {
			t.Fatal(err)
		}
		if _, err := client.Post(context.Background(), client.Draft{Drive: testDrive, Lane: lane, Kind: "state", Text: lane + " up"}); err != nil {
			t.Fatal(err)
		}
	}

	route, err := execute(t, RouteCommand(a), "desk")
	if err != nil {
		t.Fatal(err)
	}
	listed, err := execute(t, Command(a), "desk")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := execute(t, RequestCommand(a), "park", "desk", "--by", "coordinator"); err != nil {
		t.Fatal(err)
	}

	equal(t, strings.TrimSpace(route), `{"lane":"desk","route":"desk-3","state":"desired","standing":"","meta":false}`)
	if !strings.HasPrefix(listed, "desk-3 ") || strings.Contains(listed, "desk-2") {
		t.Fatalf("lr lanes desk = %q; want desk-3's line alone", listed)
	}
	records, err := client.Tail(context.Background(), client.Query{Drive: testDrive, Kinds: []string{"decide"}})
	if err != nil {
		t.Fatal(err)
	}
	equal(t, len(records), 1)
	equal(t, recordOf(records[0]).Attr("lane"), "desk-3")
}

func TestARouteToAnAliasNoLaneHoldsAnswersForItsNewestGeneration(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	a, s := driveState(t)
	for _, lane := range []string{"desk-2", "desk-10"} {
		if _, err := s.WantLane(storeLane(lane), t0); err != nil {
			t.Fatal(err)
		}
		if err := s.SetLaneState(lane, store.LaneRetired, t0, "", ""); err != nil {
			t.Fatal(err)
		}
		if _, err := client.Post(context.Background(), client.Draft{Drive: testDrive, Lane: lane, Kind: "state", Text: lane + " up"}); err != nil {
			t.Fatal(err)
		}
		if _, err := client.Post(context.Background(), client.Draft{Drive: testDrive, Lane: "root", Kind: "stand-down", To: []string{lane}, Text: lane + " is done"}); err != nil {
			t.Fatal(err)
		}
	}

	route, err := execute(t, RouteCommand(a), "desk")
	if err != nil {
		t.Fatal(err)
	}
	equal(t, strings.TrimSpace(route), `{"lane":"desk","route":"root","state":"retired","standing":"","meta":false}`)

	route, err = execute(t, RouteCommand(a), "nobody")
	if err != nil {
		t.Fatal(err)
	}
	equal(t, strings.TrimSpace(route), `{"lane":"nobody","route":"nobody","state":"unregistered","standing":"","meta":false}`)
}

func TestANumberedLaneStaysItselfThoughANewerLaneHoldsItsStem(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	a, s := driveState(t)
	for _, lane := range []string{"desk-2", "desk-3"} {
		if _, err := s.WantLane(storeLane(lane), t0); err != nil {
			t.Fatal(err)
		}
		if _, err := client.Post(context.Background(), client.Draft{Drive: testDrive, Lane: lane, Kind: "state", Text: lane + " up"}); err != nil {
			t.Fatal(err)
		}
	}

	route, err := execute(t, RouteCommand(a), "desk-2")
	if err != nil {
		t.Fatal(err)
	}

	equal(t, strings.TrimSpace(route), `{"lane":"desk-2","route":"desk-2","state":"desired","standing":"","meta":false}`)
}

func TestTheRequestVerbPostsOneTypedRecordAndRefusesAStandingLaneAlone(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	t.Setenv(LaneEnv, Root)
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
	if _, err := execute(t, RequestCommand(a), "stand-down", refresher, "--successor", "board-refresher-7", "--last-read", "9", "--by", "coordinator"); err == nil || !strings.Contains(err.Error(), "its stand-down carries its open items") {
		t.Fatalf("err %v", err)
	}
	text, err := execute(t, RequestCommand(a), "stand-down", refresher, "--successor", "board-refresher-7", "--last-read", "9", "--pr", "12", "--open", "card-cleanup-daemon-restart", "--by", "coordinator")
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
	equal(t, []any{r.Lane, r.Kind, isRequest(r), r.Attr("verb"), r.Attr("lane"), r.Attr("successor"), r.Attr("last-read"), r.Attr("open"), r.PRs()}, []any{"coordinator", "decide", true, "stand-down", refresher, "board-refresher-7", "9", "card-cleanup-daemon-restart", []int{12}})
}

func TestTheRequestVerbPostsNothingUntilTheCallerHasReadTheLaneItWouldStandDown(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	t.Setenv(LaneEnv, Root)
	a, s := driveState(t)
	for _, lane := range []store.Lane{storeLane(lane2), storeLane("conflicts-3")} {
		if _, err := s.WantLane(lane, t0); err != nil {
			t.Fatal(err)
		}
	}
	if err := s.SetLanePRs(lane2, []int{46001, 46002}); err != nil {
		t.Fatal(err)
	}
	texts := []string{"restacked #46001", "pushed #46001 e590637"}
	seqs := make([]string, 0, len(texts))
	for _, text := range texts {
		posted, err := client.Post(context.Background(), client.Draft{Drive: testDrive, Lane: lane2, Kind: "state", Text: text})
		if err != nil {
			t.Fatal(err)
		}
		if err := s.LaneHeard(lane2, t0, "state", posted.Seq); err != nil {
			t.Fatal(err)
		}
		seqs = append(seqs, strconv.FormatInt(posted.Seq, 10))
	}

	for _, c := range []struct {
		args          []string
		shown, hidden []string
		rerun         string
	}{
		{[]string{"stand-down", lane2}, []string{"restacked #46001", "pushed #46001 e590637"}, nil, "lr request stand-down conflicts-2 --last-read " + seqs[1] + " --by root"},
		{[]string{"stand-down", lane2, "--last-read", seqs[0]}, []string{"pushed #46001 e590637"}, []string{"restacked #46001"}, "lr request stand-down conflicts-2 --last-read " + seqs[1] + " --by root"},
		{[]string{"owner", "conflicts-3", "--pr", "46001", "--pr", "46002"}, []string{"restacked #46001", "pushed #46001 e590637"}, nil, "lr request owner conflicts-3 --pr 46001 --pr 46002 --last-read " + seqs[1] + " --by root"},
	} {
		_, err := execute(t, RequestCommand(a), c.args...)
		if err == nil {
			t.Fatalf("%v posted", c.args)
		}
		for _, text := range append(c.shown, c.rerun) {
			contains(t, err.Error(), text)
		}
		for _, text := range c.hidden {
			if strings.Contains(err.Error(), text) {
				t.Fatalf("%v shows the read record %q:\n%s", c.args, text, err)
			}
		}
	}
	for _, args := range [][]string{{"stand-down", lane2, "--last-read", seqs[1]}, {"owner", "conflicts-3", "--pr", "46001"}} {
		if _, err := execute(t, RequestCommand(a), args...); err != nil {
			t.Fatalf("%v: %v", args, err)
		}
	}

	posted, err := client.Tail(context.Background(), client.Query{Drive: testDrive, Kinds: []string{"decide"}})
	if err != nil {
		t.Fatal(err)
	}
	verbs := make([]string, len(posted))
	for i, record := range posted {
		verbs[i] = recordOf(record).Attr("verb")
	}
	equal(t, verbs, []string{"stand-down", "owner"})
}

func TestTheRequestVerbPostsUnderTheSessionsLaneAndRefusesASessionBoundToNone(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	t.Setenv(LaneEnv, "")
	a, _ := driveState(t)
	me := "window lane drive=" + testDrive + " lane=meta-desk-3 branch=b\n"
	a.Runner = scripted(func(argv []string) (shell.Result, error) {
		equal(t, argv, []string{"cci", "me"})
		return shell.Result{Stdout: me}, nil
	})

	if _, err := execute(t, RequestCommand(a), "owner", "conflicts-3", "--pr", "46001"); err != nil {
		t.Fatal(err)
	}
	me = "window lane drive=" + testDrive + " branch=b\n"
	if _, err := execute(t, RequestCommand(a), "owner", "conflicts-3", "--pr", "46002"); err == nil || !strings.Contains(err.Error(), "no lane is bound to this session") {
		t.Fatalf("err %v", err)
	}

	records, err := client.Tail(context.Background(), client.Query{Drive: testDrive})
	if err != nil {
		t.Fatal(err)
	}
	equal(t, len(records), 1)
	r := recordOf(records[0])
	equal(t, []any{r.Lane, r.PRs()}, []any{"meta-desk-3", []int{46001}})
}

func TestTheStrandedPRBlockersCommandRunAsWrittenHandsThePRsToTheLaneItNames(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	t.Setenv(LaneEnv, Root)
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

func TestASessionInNoDriveIsRefusedInOneLine(t *testing.T) {
	t.Setenv("CLAUDE_CONFIG_DIR", t.TempDir())
	t.Setenv(drive.Env, "")
	_, err := execute(t, Command(&app.App{}))
	if !errors.Is(err, drive.ErrNoDrive) || strings.Contains(err.Error(), "\n") {
		t.Fatalf("err %v", err)
	}
	if _, statErr := os.Stat("lr.sqlite"); statErr == nil {
		t.Fatal("opened a store in the working directory")
	}
}
