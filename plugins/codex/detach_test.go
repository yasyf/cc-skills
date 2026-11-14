package main

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// TestRunWorkerRerunsOnceWithNoServerRequiredWhenAServerDidNotStart drives the real
// --worker with a fake codex that dies on a required MCP server's start while any
// server is required, and answers the question once none is.
func TestRunWorkerRerunsOnceWithNoServerRequiredWhenAServerDidNotStart(t *testing.T) {
	bin := buildCodexAsk(t)
	codexDir := t.TempDir()
	writeExec(t, filepath.Join(codexDir, "fakecodex"), `echo run >> "$RUNS"
case " $* " in
*" mcp_servers.datadog.required=true "*)
	cat >/dev/null
	[ "$STARTED" = 1 ] && printf '%s\n' '{"type":"thread.started","thread_id":"019f-first"}'
	echo 'Error: thread/start: required MCP servers failed to initialize: datadog: MCP client startup timed out after 30s'
	exit 1 ;;
*" mcp_servers.datadog.required=false "*)
	cat > "$REPLY_TMP"
	exit 0 ;;
esac
exit 7
`)

	for _, c := range []struct {
		id, started, wantStatus, wantRuns, wantReply string
	}{
		{"died-before-its-thread-reruns", "0", "0", "run\nrun\n", "hi\n"},
		{"died-after-its-thread-started-ends", "1", "1", "run\n", ""},
	} {
		t.Run(c.id, func(t *testing.T) {
			sdir := t.TempDir()
			question := join(sdir, "question")
			if err := os.WriteFile(question, []byte("hi\n"), 0o644); err != nil { //nolint:gosec // test fixture question file
				t.Fatal(err)
			}
			reply := join(sdir, "reply")
			argv := []string{"fakecodex", "-c", "mcp_servers.datadog.required=true", "-c", "mcp_servers.slack.enabled=false"}
			cb, err := json.Marshal(cmdSpec{Argv: argv, Question: question, Reply: reply, ReplyTmp: reply + ".tmp", Log: join(sdir, "codex.log")})
			if err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(join(sdir, "cmd"), cb, 0o644); err != nil { //nolint:gosec // test fixture cmd file
				t.Fatal(err)
			}

			worker := exec.Command(bin, "--worker", sdir, bin, bin) //nolint:gosec // test drives the self-built binary from t.TempDir
			worker.Env = append(os.Environ(), "PATH="+codexDir+string(os.PathListSeparator)+"/usr/bin:/bin", "CAPT_HOOK_BIN=",
				"RUNS="+join(sdir, "runs"), "STARTED="+c.started, "REPLY_TMP="+reply+".tmp")
			if out, err := worker.CombinedOutput(); err != nil {
				t.Fatalf("--worker failed: %v\n%s", err, out)
			}
			if got := strings.TrimSpace(readFile(join(sdir, "status"))); got != c.wantStatus {
				t.Errorf("status = %q, want %q; log:\n%s", got, c.wantStatus, readFile(join(sdir, "codex.log")))
			}
			if got := readFile(join(sdir, "runs")); got != c.wantRuns {
				t.Errorf("runs = %q, want %q", got, c.wantRuns)
			}
			if got := readFile(reply); got != c.wantReply {
				t.Errorf("reply = %q, want %q", got, c.wantReply)
			}
		})
	}
}
