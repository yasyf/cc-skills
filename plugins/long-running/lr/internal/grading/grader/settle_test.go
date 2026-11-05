package grader

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"
)

func TestOnlyALostCodexWorkerSettlesAsAPassThatReruns(t *testing.T) {
	cases := []struct {
		name string
		err  error
		lost bool
	}{
		{"a lost codex worker", &ExitError{Argv: []string{"codex-ask", "-m", "sol"}, Code: codexWorkerLost}, true},
		{"a codex failure", &ExitError{Argv: []string{"codex-ask", "-m", "sol"}, Code: 1}, false},
		{"another command's same code", &ExitError{Argv: []string{"git", "fetch"}, Code: codexWorkerLost}, false},
		{"a failure with no exit", errors.New("no reply"), false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			dir := t.TempDir()
			if err := settle(dir, struct{}{}, c.err); !errors.Is(err, c.err) {
				t.Fatalf("settle returned %v, want %v", err, c.err)
			}
			data, err := os.ReadFile(filepath.Join(dir, ResultFile))
			if err != nil {
				t.Fatal(err)
			}
			var result struct {
				Error string `json:"error"`
				Lost  bool   `json:"no_result"`
			}
			if err := json.Unmarshal(data, &result); err != nil {
				t.Fatal(err)
			}
			if result.Error == "" || result.Lost != c.lost {
				t.Fatalf("result %s, want an error and no_result %v", data, c.lost)
			}
		})
	}
}
