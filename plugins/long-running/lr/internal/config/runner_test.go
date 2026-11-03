package config

import (
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func runnerFile(t *testing.T, content string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "runner.json")
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

const fullRunner = `{
  "drive": "brook",
  "store": "~/state/actions",
  "orca": {
    "desk_inbox": "~/state/inbox",
    "run": "run-1",
    "briefs": {"repo": "/repo", "log": "log-1"},
    "launch_env": {"A": "1"},
    "gc": "gc-1"
  },
  "deadlines": {"start_minutes": 99},
  "incident": {"case_lanes": 5},
  "alert": {"facts": "/facts.json"},
  "landing": {"fidelity_lane": ["fidelity-desk"]},
  "finder": {"concurrent": 4}
}`

func TestARunnerFileLoadsWithDefaultsForWhatItOmits(t *testing.T) {
	t.Setenv("HOME", "/home/me")
	path := runnerFile(t, fullRunner)
	got, err := LoadRunner(path)
	if err != nil {
		t.Fatal(err)
	}
	if got.Source != path || got.Store != "/home/me/state/actions" || got.Drive != "brook" || got.CaseLanes != 5 || got.AlertFacts != "/facts.json" {
		t.Fatalf("runner %+v", got)
	}
	wantOrca := RunnerOrca{DeskInbox: "/home/me/state/inbox", Run: "run-1", Briefs: RunnerBriefs{Repo: "/repo", Log: "log-1"}, LaunchEnv: map[string]string{"A": "1"}, GC: "gc-1"}
	if !reflect.DeepEqual(got.Orca, wantOrca) {
		t.Fatalf("orca %+v", got.Orca)
	}
	if got.Deadlines.StartMinutes != 99 || got.Deadlines.LaunchMinutes != 15 || got.Deadlines.IncidentReleaseMinutes != 3 || got.Deadlines.PRSilentEveryMinutes != 5 {
		t.Fatalf("deadlines %+v", got.Deadlines)
	}
}

func TestASectionAModuleLaneOwnsStaysRawForThatLane(t *testing.T) {
	got, err := LoadRunner(runnerFile(t, fullRunner))
	if err != nil {
		t.Fatal(err)
	}
	if string(got.Sections["finder"]) != `{"concurrent": 4}` {
		t.Fatalf("finder %s", got.Sections["finder"])
	}
	if _, found := got.Sections["landing"]; !found {
		t.Fatal("the landing section was dropped")
	}
}

func TestARunnerFileWithoutItsRequiredFieldsNamesThem(t *testing.T) {
	_, err := LoadRunner(runnerFile(t, `{"orca": {"run": "r"}}`))
	if err == nil || !strings.Contains(err.Error(), "missing drive, orca.briefs.repo, orca.desk_inbox") {
		t.Fatalf("got %v", err)
	}
}

func TestAnAbsentCaseLanesKeepsItsDefault(t *testing.T) {
	got, err := LoadRunner(runnerFile(t, `{"drive": "d", "orca": {"desk_inbox": "/i", "run": "r", "briefs": {"repo": "/b"}}}`))
	if err != nil {
		t.Fatal(err)
	}
	if got.CaseLanes != 2 || got.Store != "" {
		t.Fatalf("runner %+v", got)
	}
}
