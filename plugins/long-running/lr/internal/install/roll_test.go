package install

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

func TestTheHostConfigMergesHostLandingOverLandingOneLevelDown(t *testing.T) {
	raw := map[string]any{
		"drive":   "d",
		"landing": map[string]any{"repo": "o/r", "adapter": map[string]any{"kind": "trunk", "key_item": "k"}},
		"host":    map[string]any{"sprite": "s", "landing": map[string]any{"checkout": "/home/sprite/co", "adapter": map[string]any{"key_item": "other"}}},
	}

	got, err := HostConfig(raw)
	if err != nil {
		t.Fatal(err)
	}

	want := map[string]any{"repo": "o/r", "checkout": "/home/sprite/co", "adapter": map[string]any{"kind": "trunk", "key_item": "other"}}
	if !reflect.DeepEqual(got["landing"], want) {
		t.Fatalf("landing %+v", got["landing"])
	}
	if raw["landing"].(map[string]any)["checkout"] != nil {
		t.Fatal("the merge edited the runner's own config")
	}
}

type rollWorld struct {
	calls   []shell.Command
	refuse  string
	failing string
}

func (w *rollWorld) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	w.calls = append(w.calls, c)
	joined := strings.Join(c.Argv, " ")
	if w.refuse != "" && strings.Contains(joined, "--preflight") {
		return shell.Result{Code: gateRefused, Stdout: w.refuse}, &shell.ExitError{Argv: c.Argv, Code: gateRefused}
	}
	if w.failing != "" && strings.Contains(joined, w.failing) {
		return shell.Result{Code: 1, Stderr: "lost connection"}, &shell.ExitError{Argv: c.Argv, Code: 1, Stderr: "lost connection\n"}
	}
	return shell.Result{}, nil
}

func (w *rollWorld) saw(text string) bool {
	return slices.ContainsFunc(w.calls, func(c shell.Command) bool { return strings.Contains(strings.Join(c.Argv, " "), text) })
}

func rollFixture(t *testing.T) (Checks, RollConfig, *rollWorld) {
	t.Helper()
	dir := t.TempDir()
	runner := filepath.Join(dir, "runner.json")
	data, err := json.Marshal(map[string]any{"drive": "d", "version": "0.7.99", "landing": map[string]any{"repo": "o/r"}, "host": map[string]any{"sprite": "dar-desk-1", "landing": map[string]any{"checkout": "/co"}}})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(runner, data, 0o600); err != nil {
		t.Fatal(err)
	}
	world := &rollWorld{}
	return Checks{Runner: world, Installs: t.TempDir()}, RollConfig{Runner: runner, Desk: "landing"}, world
}

func TestAConfigTheTargetRefusesStopsNoDeskAndKeepsTheRunningConfig(t *testing.T) {
	checks, config, world := rollFixture(t)
	world.refuse = `{"why":"runner.json names go-sweep, which never registered on drive d; start each lane or remove it from runner.json"}`

	var out strings.Builder
	err := Roll(context.Background(), checks, config, &out)

	if err == nil || !strings.Contains(err.Error(), "nothing moved, since the landing desk on 0.7.99 would refuse: runner.json names go-sweep") {
		t.Fatalf("got %v", err)
	}
	if world.saw(rollScript) || world.saw(moveScript) {
		t.Fatalf("a refusal moved the desk: %+v", world.calls)
	}
}

func TestARollCopiesByFileChecksTheShippedConfigAndOnlyThenStopsTheDesk(t *testing.T) {
	checks, config, world := rollFixture(t)

	var out strings.Builder
	if err := Roll(context.Background(), checks, config, &out); err != nil {
		t.Fatal(err)
	}

	order := []string{"tar --no-xattrs", extractScript, "--preflight", moveScript, rollScript}
	if len(world.calls) != len(order) {
		t.Fatalf("calls %+v", world.calls)
	}
	for i, want := range order {
		if !strings.Contains(strings.Join(world.calls[i].Argv, " "), want) {
			t.Fatalf("call %d %v, want %q", i, world.calls[i].Argv, want)
		}
	}
	for _, call := range world.calls[1:] {
		if !slices.Contains(call.Argv, "--no-stdin") {
			t.Fatalf("a Sprite exec holds stdin open: %v", call.Argv)
		}
	}
	if world.calls[1].Stdin != "" || !slices.Contains(world.calls[1].Argv, "--file") {
		t.Fatalf("the copy streams over stdin: %+v", world.calls[1])
	}
	if !strings.Contains(out.String(), "the landing desk on dar-desk-1 runs 0.7.99") {
		t.Fatalf("out %q", out.String())
	}
}

func TestACopyThatDropsNamesTheHostAndMovesNothing(t *testing.T) {
	checks, config, world := rollFixture(t)
	world.failing = extractScript

	var out strings.Builder
	err := Roll(context.Background(), checks, config, &out)

	if err == nil || !strings.Contains(err.Error(), "dar-desk-1 did not take the 0.7.99 copy: lost connection") {
		t.Fatalf("got %v", err)
	}
	if world.saw("--preflight") || world.saw(rollScript) {
		t.Fatalf("went on after the copy failed: %+v", world.calls)
	}
}

func TestACIRedVersionRollsOnlyWithAReason(t *testing.T) {
	checks := cachedChecks(t, map[string]string{"api repos/o/r/commits/long-running--v1.2.3": parentOfTag, runsPath: `{"workflow_runs":[]}`})
	write(t, filepath.Join(checks.dir("1.2.3"), ".claude-plugin", "plugin.json"), "{}")
	checks.Runner = releaseMatches{}
	if fault := checks.rollFault(context.Background(), "1.2.3", ""); !strings.Contains(fault, "no long-running CI run on main has started") || !strings.Contains(fault, "--ci-red-ok") {
		t.Fatalf("fault %q", fault)
	}
	if fault := checks.rollFault(context.Background(), "1.2.3", "the desk is down"); fault != "" {
		t.Fatalf("fault %q", fault)
	}
}

type releaseMatches struct{}

func (releaseMatches) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	switch {
	case slices.Contains(c.Argv, "ls-tree"):
		return shell.Result{Stdout: "100644 blob abc\tplugins/long-running/.claude-plugin/plugin.json\x00"}, nil
	case slices.Contains(c.Argv, "hash-object"):
		return shell.Result{Stdout: "abc\n"}, nil
	}
	return shell.Result{}, nil
}
