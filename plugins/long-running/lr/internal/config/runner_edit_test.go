package config

import (
	"os"
	"strings"
	"testing"
)

func TestSettingARunnerKeyChangesOnlyThatKeyAndKeepsTheFileLoadable(t *testing.T) {
	path := runnerFile(t, fullRunner)

	for key, value := range map[string]string{
		"landing.desk_lane":       `["desk-a","desk-b"]`,
		"landing.release_owner":   "peer:abc",
		"deadlines.start_minutes": "7",
		"finder.checkout":         "/repo",
	} {
		if err := SetRunnerKey(path, key, value); err != nil {
			t.Fatalf("set %s: %v", key, err)
		}
	}

	got, err := LoadRunner(path)
	if err != nil {
		t.Fatal(err)
	}
	if got.Deadlines.StartMinutes != 7 || got.Drive != "brook" || got.CaseLanes != 5 {
		t.Fatalf("runner %+v", got)
	}
	for section, want := range map[string]string{
		"finder":  `"concurrent": 4`,
		"landing": `"desk_lane": [`,
	} {
		if !strings.Contains(string(got.Sections[section]), want) {
			t.Fatalf("%s section %s lacks %s", section, got.Sections[section], want)
		}
	}
	if !strings.Contains(string(got.Sections["landing"]), `"release_owner": "peer:abc"`) || !strings.Contains(string(got.Sections["landing"]), `"fidelity_lane"`) {
		t.Fatalf("landing section %s", got.Sections["landing"])
	}
}

func TestAnEditThatWouldBreakRunnerJSONWritesNothing(t *testing.T) {
	path := runnerFile(t, fullRunner)

	for name, edit := range map[string]func() error{
		"unsetting a required key":    func() error { return UnsetRunnerKey(path, "drive") },
		"setting under a scalar":      func() error { return SetRunnerKey(path, "drive.name", "x") },
		"unsetting what is not there": func() error { return UnsetRunnerKey(path, "landing.desk_lane") },
	} {
		if err := edit(); err == nil {
			t.Fatalf("%s succeeded", name)
		}
	}

	written, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if string(written) != fullRunner {
		t.Fatalf("runner.json changed to %s", written)
	}
	left, err := os.ReadDir(strings.TrimSuffix(path, "/runner.json"))
	if err != nil || len(left) != 1 {
		t.Fatalf("directory holds %v, %v", left, err)
	}
}

func TestUnsettingARunnerKeyRemovesIt(t *testing.T) {
	path := runnerFile(t, fullRunner)

	if err := UnsetRunnerKey(path, "finder.concurrent"); err != nil {
		t.Fatal(err)
	}

	got, err := LoadRunner(path)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(got.Sections["finder"]), "concurrent") {
		t.Fatalf("finder section %s", got.Sections["finder"])
	}
}
