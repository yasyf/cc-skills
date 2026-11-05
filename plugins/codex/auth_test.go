package main

import (
	"fmt"
	"os/exec"
	"path/filepath"
	"testing"
)

func TestDispatchHandsTheAmbientKeyToCodexOnlyWithoutALogin(t *testing.T) {
	for _, tc := range []struct {
		name  string
		login int
		want  string
	}{
		{"signed in", 0, "|"},
		{"no login", 1, "|sk-test"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			seen := filepath.Join(mustTempDir(t), "auth")
			stubDir := mustTempDir(t)
			writeStub(t, stubDir, stubCodex("[]", fmt.Sprintf("[ \"$1\" = login ] && exit %d\nprintf '%%s|%%s' \"$OPENAI_API_KEY\" \"$CODEX_API_KEY\" > %s\n", tc.login, seen)+stubCodexReplyBody))
			scope := canonicalScope(t)
			c := exec.Command(codexAskBin(t), "ping") //nolint:gosec // drives the built binary under test
			c.Dir = scope
			c.Env = append(dispatchEnv(shortHome(t), "", mustTempDir(t), stubDir, scope), "OPENAI_API_KEY=sk-test")
			out, err := c.Output()
			if err != nil {
				t.Fatalf("dispatch: %v", err)
			}
			laneDir(t, string(out))
			if got := readFile(seen); got != tc.want {
				t.Fatalf("codex exec saw OPENAI_API_KEY|CODEX_API_KEY = %q, want %q", got, tc.want)
			}
		})
	}
}
