package reply

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"sync/atomic"

	"github.com/yasyf/cc-interact/channel"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/supervisor"
	"golang.org/x/sys/unix"
)

const pinEnv = "LONG_RUNNING_PIN"

// handoff is the server's stdin. It ends the serve loop when a frame waits and
// a newer lr is installed, and it reads a byte at a time so the channel server's
// scanner never buffers a frame the process it hands over to must still read.
type handoff struct {
	in       io.Reader
	claude   string
	running  string
	probe    func(path string) error
	log      *slog.Logger
	inFlight atomic.Int64
	midFrame bool
	moved    string
}

func newHandoff(ctx context.Context, runner shell.Runner, log *slog.Logger, in io.Reader) (*handoff, error) {
	claude, err := drive.ConfigDir()
	if err != nil {
		return nil, err
	}
	return &handoff{in: in, claude: claude, running: supervisor.Installed(claude), log: log, probe: func(path string) error {
		_, err := runner.Run(ctx, shell.Command{Argv: []string{path, "--version"}, Timeout: postTimeout})
		return err
	}}, nil
}

func (h *handoff) Read(p []byte) (int, error) {
	if !h.midFrame {
		if err := frameWaiting(h.in); err != nil {
			return 0, err
		}
		if h.moveDue() {
			return 0, io.EOF
		}
	}
	for n := range p {
		if _, err := h.in.Read(p[n : n+1]); err != nil {
			return n, err
		}
		if p[n] == '\n' {
			h.midFrame = false
			return n + 1, nil
		}
	}
	h.midFrame = true
	return len(p), nil
}

func (h *handoff) moveDue() bool {
	if os.Getenv(pinEnv) != "" || h.inFlight.Load() > 0 {
		return false
	}
	installed := supervisor.Installed(h.claude)
	if !supervisor.Behind(h.running, installed) {
		return false
	}
	path := filepath.Join(h.claude, "long-running", "current", "bin", "lr")
	if err := h.probe(path); err != nil {
		h.log.Warn("the installed lr did not run, so this reply server stays on its own until a newer install", "installed", installed, "err", err)
		h.running = installed
		return false
	}
	h.moved = path
	return true
}

func (h *handoff) counted(tools []channel.Tool) []channel.Tool {
	for i, tool := range tools {
		handler := tool.Handler
		tools[i].Handler = func(ctx context.Context, args json.RawMessage, progress func(string)) (string, bool) {
			h.inFlight.Add(1)
			defer h.inFlight.Add(-1)
			return handler(ctx, args, progress)
		}
	}
	return tools
}

func frameWaiting(in io.Reader) error {
	file, ok := in.(*os.File)
	if !ok {
		return nil
	}
	fds := []unix.PollFd{{Fd: int32(file.Fd()), Events: unix.POLLIN}} //nolint:gosec // G115: a descriptor fits poll's int32 by the kernel's own limit
	for {
		_, err := unix.Poll(fds, -1)
		if !errors.Is(err, unix.EINTR) {
			return err
		}
	}
}
