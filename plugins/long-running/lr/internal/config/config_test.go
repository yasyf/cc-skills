package config

import (
	"bytes"
	"encoding/json"
	"io"
	"maps"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func good() map[string]any {
	return map[string]any{"repo": "org/repo", "drive": "d", "checkout": "/srv/checkout", "author": "drive-bot", "label": "drive", "movers": []string{"lanes"}}
}

func write(t *testing.T, raw any) string {
	t.Helper()
	data, err := json.Marshal(raw)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "lr.json")
	if err := os.WriteFile(path, data, 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func with(changes map[string]any) map[string]any {
	raw := good()
	maps.Copy(raw, changes)
	return raw
}

func TestConfigurationLoads(t *testing.T) {
	loaded, err := Load(write(t, with(map[string]any{"remote_box": "box-1"})), io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	want := Config{Repo: "org/repo", Drive: "d", Checkout: "/srv/checkout", Author: "drive-bot", Label: "drive", Movers: []string{"lanes"}, RemoteBox: "box-1", Trunk: "main"}
	if !reflect.DeepEqual(loaded, want) {
		t.Fatalf("got %+v, want %+v", loaded, want)
	}
}

func TestKeysAndMoversANewerReleaseWroteLoadWithAWarningAndConfigureNothing(t *testing.T) {
	var warned bytes.Buffer
	loaded, err := Load(write(t, with(map[string]any{"movers": []string{"lanes", "board"}, "desks": []string{"finder-0"}, "go_lanes_v9": "x"})), &warned)
	if err != nil {
		t.Fatal(err)
	}
	plain, err := Load(write(t, good()), io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(loaded, plain) {
		t.Fatalf("got %+v, want %+v", loaded, plain)
	}
	for _, said := range []string{"ignoring desks, go_lanes_v9", "ignoring movers board"} {
		if !strings.Contains(warned.String(), said) {
			t.Errorf("warnings %q lack %q", warned.String(), said)
		}
	}
}

func TestASubDrivesConfigNamesItsParentWithNoWarning(t *testing.T) {
	raw := good()
	raw["parent"] = "brook"
	var warned bytes.Buffer

	loaded, err := Load(write(t, raw), &warned)
	if err != nil {
		t.Fatal(err)
	}

	if loaded.Parent != "brook" || warned.Len() != 0 {
		t.Fatalf("parent %q, warnings %q", loaded.Parent, warned.String())
	}
}

func TestARefusedFileNamesWhatIsWrong(t *testing.T) {
	withoutMovers := good()
	delete(withoutMovers, "movers")
	cases := []struct {
		name string
		raw  any
		says string
	}{
		{"a missing field", map[string]any{"repo": "org/repo", "drive": "d", "checkout": "/x"}, "missing author, label, movers"},
		{"a config without movers runs nothing by default", withoutMovers, "missing movers"},
		{"a blank required field", with(map[string]any{"repo": " "}), "repo must be non-empty text"},
		{"a required field that is not text", with(map[string]any{"repo": 7}), "repo must be non-empty text"},
		{"an optional field that is not text", with(map[string]any{"trunk": 7}), "trunk must be text"},
		{"no movers", with(map[string]any{"movers": []string{}}), "movers must name at least one of lanes, shadow, landing, grading, items"},
		{"only movers this release does not run", with(map[string]any{"movers": []string{"board"}}), "movers must name at least one of"},
		{"movers that are not a list", with(map[string]any{"movers": "lanes"}), "movers must be a list of mover names"},
		{"the landing mover without its queue script", with(map[string]any{"movers": []string{"lanes", "landing"}}), "landing mover needs queue_script"},
		{"release pipelines without the Buildkite client", with(map[string]any{"release_pipelines": "org/release"}), "release_pipelines needs buildkite_cli"},
		{"a file that is not an object", []string{"lanes"}, "must be a JSON object"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			_, err := Load(write(t, c.raw), io.Discard)
			if err == nil || !strings.Contains(err.Error(), c.says) {
				t.Fatalf("got %v, want it to say %q", err, c.says)
			}
		})
	}
}

func TestTheRemoteBoxIsOptionalUntilThePoolRunsRemotely(t *testing.T) {
	loaded, err := Load(write(t, good()), io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	if loaded.RemoteBox != "" {
		t.Fatalf("got %q", loaded.RemoteBox)
	}
}

func TestGoLanesNameTheLanesBesidesRootWhoseGoMayNameAnExemptClass(t *testing.T) {
	loaded, err := Load(write(t, with(map[string]any{"go_lanes": "go-carry-sweep, go-sweep"})), io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	if got, want := loaded.GoLaneNames(), []string{"go-carry-sweep", "go-sweep"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestReleasePipelinesNameTheSlugsTheSupervisorWatches(t *testing.T) {
	loaded, err := Load(write(t, with(map[string]any{"release_pipelines": "org/release, org/hotfix", "buildkite_cli": "tools/ci bk"})), io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	if got, want := loaded.ReleasePipelineSlugs(), []string{"org/release", "org/hotfix"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}
