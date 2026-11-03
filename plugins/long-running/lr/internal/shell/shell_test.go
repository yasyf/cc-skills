package shell

import (
	"context"
	"errors"
	"os/exec"
	"path/filepath"
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
