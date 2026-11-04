package landing

import (
	"encoding/json"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
)

func TestFirstSpritePrefersFidelityAndReadsAStringOrAList(t *testing.T) {
	cases := []struct {
		name     string
		sections map[string]json.RawMessage
		want     string
	}{
		{"a fidelity string", map[string]json.RawMessage{"fidelity": json.RawMessage(`{"sprite":"vet4"}`)}, "vet4"},
		{"a fidelity list", map[string]json.RawMessage{"fidelity": json.RawMessage(`{"sprite":["fleet-8","fleet-9"]}`)}, "fleet-8"},
		{"fidelity over finder", map[string]json.RawMessage{"fidelity": json.RawMessage(`{"sprite":"a"}`), "finder": json.RawMessage(`{"sprite":"b"}`)}, "a"},
		{"finder when fidelity names none", map[string]json.RawMessage{"fidelity": json.RawMessage(`{}`), "finder": json.RawMessage(`{"sprite":["b"]}`)}, "b"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got, err := firstSprite(c.sections)
			if err != nil || got != c.want {
				t.Fatalf("got %q, %v; want %q", got, err, c.want)
			}
		})
	}
}

func TestFirstSpriteRefusesARunnerThatNamesNone(t *testing.T) {
	if got, err := firstSprite(map[string]json.RawMessage{"finder": json.RawMessage(`{"sprite":[]}`)}); err == nil {
		t.Fatalf("got %q", got)
	}
}

func TestRestackReadsTheRunnerBesideConfigWhenNoRunnerIsPassed(t *testing.T) {
	cases := []struct {
		name, runner, config, want string
	}{
		{"an explicit runner wins", "/d/other.json", "/d/lr.json", "/d/other.json"},
		{"the runner beside lr.json", "", "/d/lr.json", "/d/runner.json"},
		{"a config that is the runner itself", "", "/d/runner.json", "/d/runner.json"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got, err := restackRunner(c.runner, &app.App{ConfigPath: c.config})
			if err != nil || got != c.want {
				t.Fatalf("got %q, %v; want %q", got, err, c.want)
			}
		})
	}
	t.Setenv("HOME", t.TempDir())
	t.Setenv(drive.Env, "a-drive-no-registry-holds")
	if got, err := restackRunner("", &app.App{}); err == nil {
		t.Fatalf("got %q with neither flag and no bound drive", got)
	}
}
