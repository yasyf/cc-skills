package lanes

import (
	"context"
	"encoding/json"
	"slices"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func terminalsHost(worktrees *[]string, asked *[][]string) shell.Runner {
	return scripted(func(argv []string) (shell.Result, error) {
		if argv[0] == "sysctl" {
			return shell.Result{Stdout: "19\n"}, nil
		}
		if len(argv) > 1 && argv[1] == "ask-owner" {
			*asked = append(*asked, argv)
			return shell.Result{Stdout: "drew card-terminal-cap\n"}, nil
		}
		rows := make([]map[string]string, 0, len(*worktrees))
		for _, worktree := range *worktrees {
			rows = append(rows, map[string]string{"worktreePath": worktree})
		}
		raw, err := json.Marshal(map[string]any{"result": map[string]any{"terminals": rows}})
		return shell.Result{Stdout: string(raw)}, err
	})
}

func TestNearTheTerminalCapOwnerRelayHearsOnceWithTheCountAndHisCard(t *testing.T) {
	s := memory(t)
	if err := s.PutLanes([]store.Lane{{Name: "live-1", State: store.LaneDesired}, {Name: "gone-1", State: store.LaneRetired}, {Name: "held-1", State: store.LaneHeld}}, t0); err != nil {
		t.Fatal(err)
	}
	worktrees := []string{
		"/w/dar-live-1-f77854b1-1", "/w/dar-live-1-f77854b1-2", "/w/dar-gone-1-f77854b1-1", "/w/dar-held-1-f77854b1-1",
		"/w/dar-no-row-f77854b1-1", "/w/dar-live-1-aaaaaaaa-1", "/w/captain-hook", "/w/dar-root-aaaaaaaa-1", "/w/monorepo",
	}
	bus := &fakeBus{}
	var asked [][]string
	check := &TerminalCap{Env: Env{Bus: bus, Runner: terminalsHost(&worktrees, &asked), Config: config.Config{Drive: testDrive, OrcaRun: "run_f77854b1e90c"}}, Store: s, Home: t.TempDir(), Prefix: "dar-"}

	tick := func() {
		t.Helper()
		check.read = t0.Add(-terminalCapEvery)
		if err := check.Check(context.Background(), t0); err != nil {
			t.Fatal(err)
		}
	}
	tick()
	tick()
	if len(bus.posted) != 1 {
		t.Fatalf("posted = %+v", bus.posted)
	}
	text := bus.posted[0].Text
	for _, want := range []string{"9 of 10 Orca terminals", "2 under lanes lr reads live, 1 retired, 1 held, 1 unregistered, 4 outside the drive", "Closing a terminal needs the owner's word", "drew card-terminal-cap"} {
		if !strings.Contains(text, want) {
			t.Errorf("%q lacks %q", text, want)
		}
	}
	if !slices.Equal(bus.posted[0].To, []string{OwnerRelay, Root}) {
		t.Fatalf("to = %v", bus.posted[0].To)
	}
	if len(asked) != 1 || !slices.Contains(asked[0], "--id") || !slices.Contains(asked[0], terminalCapCard) {
		t.Fatalf("asked = %v", asked)
	}

	worktrees = worktrees[:8]
	tick()
	tick()
	if len(asked) != 2 || !slices.Contains(asked[1], "--withdraw") {
		t.Fatalf("a drop below 90%% withdraws the card once, asked = %v", asked)
	}
	worktrees = append(worktrees, "/w/monorepo")
	tick()
	if len(bus.posted) != 2 {
		t.Fatalf("a drop below 90%% and a return raise one more alarm, posted = %d", len(bus.posted))
	}
}
