package main

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
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
			writeStub(t, stubDir, stubCodex("[]", fmt.Sprintf("printf '%%s|%%s' \"$OPENAI_API_KEY\" \"$CODEX_API_KEY\" > %s\n", seen)+stubCodexReplyBody))
			scope := canonicalScope(t)
			c := exec.Command(codexAskBin(t), "ping") //nolint:gosec // drives the built binary under test
			c.Dir = scope
			c.Env = append(dispatchEnv(shortHome(t), "", mustTempDir(t), stubDir, scope), "OPENAI_API_KEY=sk-test", fmt.Sprintf("STUB_CODEX_LOGIN=%d", tc.login))
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

func TestDispatchWithNoCredentialRefusesBeforeMintingARun(t *testing.T) {
	runs := mustTempDir(t)
	stubDir := mustTempDir(t)
	writeStub(t, stubDir, stubCodexReply)
	scope := canonicalScope(t)
	c := exec.Command(codexAskBin(t), "ping") //nolint:gosec // drives the built binary under test
	c.Dir = scope
	c.Env = append(dispatchEnv(shortHome(t), "", runs, stubDir, scope), "STUB_CODEX_LOGIN=1")
	out, err := c.CombinedOutput()
	var exit *exec.ExitError
	if !errors.As(err, &exit) || exit.ExitCode() != 3 || !strings.Contains(string(out), "no codex credential") {
		t.Fatalf("want exit 3 naming the missing credential, got %v: %s", err, out)
	}
	if entries, _ := os.ReadDir(runs); len(entries) != 0 {
		t.Fatalf("a refused dispatch minted %d run dirs", len(entries))
	}
}
