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
	} {
		read, runs := failingFirst(t, 1, said)
		if _, err := (Exec{}).Run(context.Background(), read); err != nil || runs() != 2 {
			t.Fatalf("%q: err %v after %d runs, want a pass on the retry", said, err, runs())
		}
	}
}

func TestARefusalThatMeansNoIsNeverRetried(t *testing.T) {
	for _, said := range []string{"failed: 401 {\"message\":\"Authentication required.\"}", "HTTP 404: Not Found", "HTTP 422: Validation Failed"} {
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
