package shell

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"
)

func TestARunCarriesStdinDirAndEnvAndCapturesBothStreams(t *testing.T) {
	dir, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	result, err := Exec{}.Run(context.Background(), Command{
		Argv:  []string{"sh", "-c", `cat; printf ' %s %s' "$LR_PROBE" "$PWD"; echo oops >&2`},
		Stdin: "in",
		Dir:   dir,
		Env:   []string{"LR_PROBE=set"},
	})
	if err != nil {
		t.Fatal(err)
	}
	if want := "in set " + dir; result.Stdout != want {
		t.Fatalf("stdout %q, want %q", result.Stdout, want)
	}
	if result.Stderr != "oops\n" || result.Code != 0 {
		t.Fatalf("result %+v", result)
	}
}

func TestARunWithAStdoutWriterStreamsThereInsteadOfCapturing(t *testing.T) {
	var streamed strings.Builder

	result, err := Exec{}.Run(context.Background(), Command{Argv: []string{"sh", "-c", "echo out; echo said >&2"}, Stdout: &streamed})

	if err != nil || result.Stdout != "" || result.Stderr != "said\n" || streamed.String() != "out\n" {
		t.Fatalf("result %+v, streamed %q, err %v", result, streamed.String(), err)
	}
}

func TestANonZeroExitReturnsItsCodeAndStderr(t *testing.T) {
	result, err := Exec{}.Run(context.Background(), Command{Argv: []string{"sh", "-c", "echo out; echo refused >&2; exit 3"}})
	var exit *ExitError
	if !errors.As(err, &exit) {
		t.Fatalf("got %v, want an ExitError", err)
	}
	if exit.Code != 3 || result.Code != 3 || result.Stdout != "out\n" {
		t.Fatalf("exit %+v, result %+v", exit, result)
	}
	if !strings.Contains(err.Error(), "refused") {
		t.Fatalf("the error %q does not carry stderr", err)
	}
}

func TestACommandsOwnExitDecidesItsResultWhileADescendantHoldsItsPipes(t *testing.T) {
	result, err := Exec{}.Run(context.Background(), Command{Argv: []string{"sh", "-c", "sleep 8 & echo answered"}})
	if err != nil || result.Stdout != "answered\n" {
		t.Fatalf("a clean exit read as result %+v, err %v", result, err)
	}
	var exit *ExitError
	if _, err := (Exec{}).Run(context.Background(), Command{Argv: []string{"sh", "-c", "sleep 8 & exit 3"}}); !errors.As(err, &exit) || exit.Code != 3 {
		t.Fatalf("a failing exit read as %v", err)
	}
}

func TestAMissingBinaryIsCode127(t *testing.T) {
	result, err := Exec{}.Run(context.Background(), Command{Argv: []string{"lr-no-such-binary"}})
	if !errors.Is(err, exec.ErrNotFound) || result.Code != CodeMissingBinary {
		t.Fatalf("code %d, err %v", result.Code, err)
	}
}

func relinking(t *testing.T) (brew, plugin string) {
	t.Helper()
	config := t.TempDir()
	brew, plugin = t.TempDir(), t.TempDir()
	t.Setenv("CLAUDE_CONFIG_DIR", config)
	t.Setenv("PATH", brew)
	if err := os.MkdirAll(filepath.Join(plugin, "bin"), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(config, "plugins"), 0o750); err != nil {
		t.Fatal(err)
	}
	return brew, plugin
}

func installs(t *testing.T, scope, plugin string) {
	t.Helper()
	registry := fmt.Sprintf(`{"version":2,"plugins":{"tool@market":[{"scope":%q,"installPath":%q}]}}`, scope, plugin)
	if err := os.WriteFile(filepath.Join(os.Getenv("CLAUDE_CONFIG_DIR"), "plugins", "installed_plugins.json"), []byte(registry), 0o600); err != nil {
		t.Fatal(err)
	}
}

func script(t *testing.T, path, body string) {
	t.Helper()
	if err := os.WriteFile(path, []byte("#!/bin/sh\n"+body+"\n"), 0o700); err != nil { //nolint:gosec // the stub has to execute
		t.Fatal(err)
	}
}

func TestAToolMissingForAWindowRunsFromItsPluginThenFromPATHOnceItIsBack(t *testing.T) {
	brew, plugin := relinking(t)
	installs(t, "user", plugin)
	script(t, filepath.Join(plugin, "bin", "tool"), "echo plugin")

	during, err := Exec{}.Run(context.Background(), Command{Argv: []string{"tool"}})
	if err != nil || during.Stdout != "plugin\n" {
		t.Fatalf("with the link gone: result %+v, err %v", during, err)
	}
	if found, err := LookPath("tool"); err != nil || found != filepath.Join(plugin, "bin", "tool") {
		t.Fatalf("LookPath with the link gone = %q, %v", found, err)
	}

	script(t, filepath.Join(brew, "tool"), "echo brew")
	after, err := Exec{}.Run(context.Background(), Command{Argv: []string{"tool"}})
	if err != nil || after.Stdout != "brew\n" {
		t.Fatalf("with the link back: result %+v, err %v", after, err)
	}
}

func TestAToolMissingFromPATHAndEveryPluginIsCode127AndNamesTheUpgradeWindow(t *testing.T) {
	_, plugin := relinking(t)
	installs(t, "user", plugin)

	result, err := Exec{}.Run(context.Background(), Command{Argv: []string{"tool"}})
	if !errors.Is(err, exec.ErrNotFound) || result.Code != CodeMissingBinary || !strings.Contains(err.Error(), "tool is missing from PATH, as while a package upgrade relinks it, and no installed plugin carries it") {
		t.Fatalf("code %d, err %v", result.Code, err)
	}
	if _, err := LookPath("tool"); !errors.Is(err, exec.ErrNotFound) {
		t.Fatalf("LookPath err = %v", err)
	}
}

func TestAnIdempotentCallToAToolMissingForGoodRetriesEveryWaitThenSaysToInstallIt(t *testing.T) {
	relinking(t)
	waits := retryWaits
	retryWaits = make([]time.Duration, len(waits))
	t.Cleanup(func() { retryWaits = waits })

	result, err := Exec{}.Run(context.Background(), Command{Argv: []string{"tool"}, Idempotent: true})

	if want := fmt.Sprintf("still missing after %d tries, so install it", len(waits)+1); !errors.Is(err, exec.ErrNotFound) || result.Code != CodeMissingBinary || !strings.Contains(err.Error(), want) {
		t.Fatalf("code %d, err %v; want %q", result.Code, err, want)
	}
}

func TestAProjectPinnedPluginCopyDoesNotStandInForAMissingTool(t *testing.T) {
	_, plugin := relinking(t)
	installs(t, "project", plugin)
	script(t, filepath.Join(plugin, "bin", "tool"), "echo pinned")

	result, err := Exec{}.Run(context.Background(), Command{Argv: []string{"tool"}})
	if !errors.Is(err, exec.ErrNotFound) || result.Code != CodeMissingBinary {
		t.Fatalf("result %+v, err %v", result, err)
	}
}

func TestAToolThatRanFromItsPluginAndFailedIsNeverRunAgain(t *testing.T) {
	_, plugin := relinking(t)
	installs(t, "user", plugin)
	ran := filepath.Join(plugin, "ran")
	script(t, filepath.Join(plugin, "bin", "tool"), "echo once >> "+ran+"\nexit 3")

	result, err := Exec{}.Run(context.Background(), Command{Argv: []string{"tool"}})
	var exit *ExitError
	if !errors.As(err, &exit) || result.Code != 3 {
		t.Fatalf("code %d, err %v", result.Code, err)
	}
	if said, err := os.ReadFile(ran); err != nil || string(said) != "once\n" {
		t.Fatalf("ran %q, err %v; want one run", said, err)
	}
}

func TestATimeoutIsCode124AndKillsTheCommand(t *testing.T) {
	started := time.Now()
	result, err := Exec{}.Run(context.Background(), Command{Argv: []string{"sh", "-c", "exec sleep 30"}, Timeout: 100 * time.Millisecond})
	if !errors.Is(err, context.DeadlineExceeded) || result.Code != CodeTimedOut {
		t.Fatalf("code %d, err %v", result.Code, err)
	}
	if elapsed := time.Since(started); elapsed > 10*time.Second {
		t.Fatalf("the run took %s", elapsed)
	}
}

func failingFirst(t *testing.T, failures int, stderr string) (Command, func() int) {
	t.Helper()
	waits := retryWaits
	retryWaits = make([]time.Duration, len(waits))
	t.Cleanup(func() { retryWaits = waits })
	count := filepath.Join(t.TempDir(), "runs")
	script := `echo x >> "$1"; [ "$(wc -l < "$1")" -gt "$2" ] && { echo ok; exit 0; }; echo "$3" >&2; exit 1`
	runs := func() int {
		raw, err := os.ReadFile(count)
		if err != nil {
			t.Fatal(err)
		}
		return strings.Count(string(raw), "\n")
	}
	return Command{Argv: []string{"sh", "-c", script, "sh", count, strconv.Itoa(failures), stderr}, Idempotent: true}, runs
}

func TestAnIdempotentReadBuildkiteAnswersOverloadedIsRetriedUntilItPasses(t *testing.T) {
	read, runs := failingFirst(t, 2, "ci bk: GET /pipelines/release/builds failed: 503 envoy overloaded")

	result, err := Exec{}.Run(context.Background(), read)

	if err != nil || result.Stdout != "ok\n" || runs() != 3 {
		t.Fatalf("result %+v, err %v, runs %d; want the third run's ok", result, err, runs())
	}
}

func TestEachTransientAnswerIsRetried(t *testing.T) {
	for _, said := range []string{
		"failed: 429 Too Many Requests",
		"HTTP 502: Bad Gateway (https://api.github.com/graphql)",
		"read: connection reset by peer",
		"dial tcp: i/o timeout",
		"You have exceeded a secondary rate limit",
		`{"ok":false,"error":{"code":"runtime_timeout","message":"Timed out waiting for the remote Orca runtime to respond."}}`,
		`{"ok":false,"error":{"code":"remote_runtime_unavailable"}}`,
		"FileNotFoundError: [Errno 2] No such file or directory: 'ccn'",
		"sh: ccn: command not found",
	} {
		read, runs := failingFirst(t, 1, said)
		if _, err := (Exec{}).Run(context.Background(), read); err != nil || runs() != 2 {
			t.Fatalf("%q: err %v after %d runs, want a pass on the retry", said, err, runs())
		}
	}
}

func TestARefusalThatMeansNoIsNeverRetried(t *testing.T) {
	for _, said := range []string{"failed: 401 {\"message\":\"Authentication required.\"}", "HTTP 404: Not Found", "HTTP 422: Validation Failed", "FileNotFoundError: [Errno 2] No such file or directory: 'brief.md'"} {
		read, runs := failingFirst(t, 1, said)
		if _, err := (Exec{}).Run(context.Background(), read); err == nil || runs() != 1 {
			t.Fatalf("%q: err %v after %d runs, want one failed run", said, err, runs())
		}
	}
}

func TestACommandNotMarkedIdempotentRunsOnce(t *testing.T) {
	write, runs := failingFirst(t, 1, "HTTP 503: Service Unavailable")
	write.Idempotent = false

	if _, err := (Exec{}).Run(context.Background(), write); err == nil || runs() != 1 {
		t.Fatalf("err %v after %d runs, want one failed run", err, runs())
	}
}

func TestARetriedReadThatStillFailsNamesItsTriesAndLastError(t *testing.T) {
	read, runs := failingFirst(t, 99, "failed: 503 envoy overloaded")

	_, err := Exec{}.Run(context.Background(), read)

	var exit *ExitError
	want := len(retryWaits) + 1
	if !errors.As(err, &exit) || exit.Tries != want || runs() != want {
		t.Fatalf("err %v after %d runs, want an ExitError after %d tries", err, runs(), want)
	}
	if said := err.Error(); !strings.Contains(said, fmt.Sprintf("exited 1 after %d tries: failed: 503 envoy overloaded", want)) {
		t.Fatalf("the error %q does not name its tries and last error", said)
	}
}
