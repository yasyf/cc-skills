package orca

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestWhatWritePinsMovesIsNoSettingAndAnythingElseIs(t *testing.T) {
	const runner = `{"drive":"d","deadlines":{"hold_minutes":15},"orca":{"sprite":{"tools":[{"kind":"binary","name":"x","version":"1"}],"env":{"WORKER_CCI_URL":"u1","WORKER_CCI_SHA256":"s1","WORKER_CHECKOUT":"/repo"}}}}`
	path := filepath.Join(t.TempDir(), "runner.json")
	write := func(text string) string {
		t.Helper()
		if err := os.WriteFile(path, []byte(text), 0o600); err != nil {
			t.Fatal(err)
		}
		settings, err := Settings(path)
		if err != nil {
			t.Fatal(err)
		}
		return settings
	}
	before := write(runner)

	if err := writePins(path, []spriteTool{{Kind: "binary", Name: "x", Version: "2"}}, []spriteTool{{URL: "u2", SHA256: "s2"}}); err != nil {
		t.Fatal(err)
	}
	after, err := Settings(path)
	if err != nil {
		t.Fatal(err)
	}
	if after != before {
		t.Fatalf("a pin move changed the settings:\n%s\n%s", before, after)
	}

	for _, change := range [][2]string{{`"hold_minutes":15`, `"hold_minutes":16`}, {`"WORKER_CHECKOUT":"/repo"`, `"WORKER_CHECKOUT":"/other"`}} {
		if write(strings.Replace(runner, change[0], change[1], 1)) == before {
			t.Fatalf("%s changed nothing", change[1])
		}
	}
}
