package reply

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-interact/channel"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
)

const serveAsLr = "LR_REPLY_TEST_SERVE_AS_LR"

func TestMain(m *testing.M) {
	if os.Getenv(serveAsLr) == "" {
		os.Exit(m.Run())
	}
	server := Command(app.New(os.Stderr), verbTree(nil))
	server.SetArgs(os.Args[1:])
	if err := server.ExecuteContext(context.Background()); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	os.Exit(0)
}

func install(t *testing.T, config, version string) {
	t.Helper()
	root := filepath.Join(config, "versions", version)
	if err := os.MkdirAll(filepath.Join(root, "bin"), 0o750); err != nil {
		t.Fatal(err)
	}
	shim := fmt.Sprintf("#!/bin/sh\n[ \"$1\" = --version ] && exit 0\necho %s >> %q\nshift\nexec \"$%s\" \"$@\"\n", version, filepath.Join(config, "started"), serveAsLr)
	if err := os.WriteFile(filepath.Join(root, "bin", "lr"), []byte(shim), 0o700); err != nil { //nolint:gosec // the stub has to execute
		t.Fatal(err)
	}
	dir := filepath.Join(config, "long-running")
	if err := os.MkdirAll(dir, 0o750); err != nil {
		t.Fatal(err)
	}
	staged := filepath.Join(dir, "current.staged")
	if err := os.Symlink(root, staged); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(staged, filepath.Join(dir, "current")); err != nil {
		t.Fatal(err)
	}
}

func ping(id int) string {
	return fmt.Sprintf(`{"jsonrpc":"2.0","id":%d,"method":"ping"}`, id) + "\n"
}

func pipe(t *testing.T) (*os.File, *os.File) {
	t.Helper()
	read, write, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_ = read.Close()
		_ = write.Close()
	})
	return read, write
}

func feed(t *testing.T, to io.Writer, frames string) {
	t.Helper()
	if _, err := io.WriteString(to, frames); err != nil {
		t.Fatal(err)
	}
}

type frame struct {
	ID     int    `json:"id"`
	Method string `json:"method"`
}

func next(t *testing.T, lines *bufio.Scanner) frame {
	t.Helper()
	if !lines.Scan() {
		t.Fatalf("the server wrote nothing more: %v", lines.Err())
	}
	var f frame
	if err := json.Unmarshal(lines.Bytes(), &f); err != nil {
		t.Fatalf("%v: %q", err, lines.Text())
	}
	return f
}

func TestARunningServerMovesToTheInstalledLrOnItsStdioAndLosesNoFrame(t *testing.T) {
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	config := t.TempDir()
	install(t, config, "1.0.0")
	stdin, frames := pipe(t)
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
	defer cancel()
	server := exec.CommandContext(ctx, filepath.Join(config, "long-running", "current", "bin", "lr"), "reply", "serve")
	server.Env = append(os.Environ(), "CLAUDE_CONFIG_DIR="+config, serveAsLr+"="+self, drive.Env+"=", pinEnv+"=")
	server.Stdin = stdin
	server.Stderr = os.Stderr
	stdout, err := server.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := server.Start(); err != nil {
		t.Fatal(err)
	}
	lines := bufio.NewScanner(stdout)

	feed(t, frames, ping(1))
	if got := next(t, lines); got.ID != 1 {
		t.Fatalf("before the install the server wrote %+v, want the answer to 1", got)
	}
	install(t, config, "1.0.1")
	feed(t, frames, ping(2)+ping(3))
	if got := next(t, lines); got.Method != "notifications/tools/list_changed" {
		t.Fatalf("the moved server opened with %+v, want it to announce its tools", got)
	}
	for _, id := range []int{2, 3} {
		if got := next(t, lines); got.ID != id {
			t.Fatalf("the moved server wrote %+v, want the answer to %d", got, id)
		}
	}
	if err := frames.Close(); err != nil {
		t.Fatal(err)
	}
	if err := server.Wait(); err != nil {
		t.Fatalf("the one process the client spawned ended with %v", err)
	}
	started, err := os.ReadFile(filepath.Join(config, "started"))
	if err != nil {
		t.Fatal(err)
	}
	if string(started) != "1.0.0\n1.0.1\n" {
		t.Fatalf("shims started = %q, want the first install then the second", started)
	}
}

func TestATurnInFlightHoldsTheMoveAndItsAnswerIsWrittenFirst(t *testing.T) {
	config := t.TempDir()
	install(t, config, "1.0.0")
	stdin, frames := pipe(t)
	stdout, written := pipe(t)
	started, release := make(chan struct{}), make(chan struct{})
	h := &handoff{in: stdin, claude: config, running: "1.0.0", probe: func(string) error { return nil }}
	server := channel.NewServer(channel.ServerInfo{Name: "lr"}, h.counted([]channel.Tool{{Name: "slow", Handler: func(context.Context, json.RawMessage, func(string)) (string, bool) {
		close(started)
		<-release
		return "done", false
	}}}))
	served := make(chan error, 1)
	go func() { served <- server.Serve(context.Background(), h, written) }()
	lines := bufio.NewScanner(stdout)

	feed(t, frames, `{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"slow"}}`+"\n")
	<-started
	install(t, config, "1.0.1")
	feed(t, frames, ping(2))
	if got := next(t, lines); got.ID != 2 {
		t.Fatalf("with a turn in flight the server wrote %+v, want it to go on answering", got)
	}
	close(release)
	if got := next(t, lines); got.ID != 1 {
		t.Fatalf("the server wrote %+v, want the turn's answer", got)
	}
	feed(t, frames, ping(3))
	if err := <-served; err != nil {
		t.Fatal(err)
	}
	if want := filepath.Join(config, "long-running", "current", "bin", "lr"); h.moved != want {
		t.Fatalf("moved = %q, want %q", h.moved, want)
	}
	if err := frames.Close(); err != nil {
		t.Fatal(err)
	}
	left, err := io.ReadAll(stdin)
	if err != nil {
		t.Fatal(err)
	}
	if string(left) != ping(3) {
		t.Fatalf("left in the pipe = %q, want the waiting frame whole", left)
	}
}

func TestAnInstallNeverSplitsAFrame(t *testing.T) {
	config := t.TempDir()
	install(t, config, "1.0.0")
	h := &handoff{in: strings.NewReader("abcdef\n"), claude: config, running: "1.0.0", probe: func(string) error { return nil }}
	part := make([]byte, 4)
	if n, err := h.Read(part); n != 4 || err != nil {
		t.Fatalf("first read = %d, %v", n, err)
	}
	install(t, config, "1.0.1")
	if n, err := h.Read(part); err != nil || string(part[:n]) != "ef\n" {
		t.Fatalf("second read = %q, %v, want the rest of the frame", part[:n], err)
	}
	if n, err := h.Read(part); n != 0 || !errors.Is(err, io.EOF) || h.moved == "" {
		t.Fatalf("after the frame: %d, %v, moved %q; want the move", n, err, h.moved)
	}
}

func TestAPinnedServerStaysOnItsVersion(t *testing.T) {
	t.Setenv(pinEnv, "1.0.0")
	config := t.TempDir()
	install(t, config, "1.0.1")
	h := &handoff{in: strings.NewReader("a\n"), claude: config, running: "1.0.0", probe: func(string) error {
		t.Fatal("a pinned server probed the install")
		return nil
	}}
	if n, err := h.Read(make([]byte, 4)); n != 2 || err != nil {
		t.Fatalf("read = %d, %v, want the frame", n, err)
	}
}

func TestAnInstallThatDoesNotRunLeavesTheServerServingUntilANewerOne(t *testing.T) {
	config := t.TempDir()
	install(t, config, "1.0.1")
	var probes int
	broken := true
	h := &handoff{in: strings.NewReader("a\nb\nc\n"), claude: config, running: "1.0.0", log: slog.New(slog.DiscardHandler), probe: func(string) error {
		probes++
		if broken {
			return errors.New("segfault")
		}
		return nil
	}}
	buf := make([]byte, 4)
	for range 2 {
		if n, err := h.Read(buf); n != 2 || err != nil {
			t.Fatalf("read = %d, %v, want the frame", n, err)
		}
	}
	if probes != 1 || h.moved != "" {
		t.Fatalf("probes = %d, moved %q; want one failed probe and no move", probes, h.moved)
	}
	broken = false
	install(t, config, "1.0.2")
	if _, err := h.Read(buf); !errors.Is(err, io.EOF) || h.moved == "" {
		t.Fatalf("after a newer install: %v, moved %q; want the move", err, h.moved)
	}
}
