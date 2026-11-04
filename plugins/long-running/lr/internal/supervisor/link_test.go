package supervisor

import (
	"context"
	"io"
	"slices"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/launchd"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const launchctlNotLoaded = 113

type launchctl struct {
	loaded bool
	verbs  []string
}

func (l *launchctl) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	l.verbs = append(l.verbs, strings.Join(c.Argv[1:], " "))
	switch c.Argv[1] {
	case "print":
		if !l.loaded {
			return shell.Result{Code: launchctlNotLoaded}, nil
		}
	case "bootstrap":
		l.loaded = true
	}
	return shell.Result{}, nil
}

func TestEverySupervisorStartMovesTheRunningBoardLinkOntoThePinnedBinary(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	ctl := &launchctl{}
	agents := launchd.Launchd{Runner: ctl, Home: t.TempDir(), UID: 501, Out: io.Discard}
	env := lanes.Env{Config: config.Config{Drive: "d1"}, State: t.TempDir(), Claude: t.TempDir()}
	const restart = "kickstart -k gui/501/com.long-running.d1.board-link"

	for start := 1; start <= 2; start++ {
		if err := installLink(context.Background(), agents, env); err != nil {
			t.Fatal(err)
		}
		restarts := 0
		for _, verb := range ctl.verbs {
			if verb == restart {
				restarts++
			}
		}
		if restarts != start {
			t.Fatalf("after start %d: %d restarts in %q; a job whose plist did not change keeps running its old binary without one", start, restarts, ctl.verbs)
		}
	}
	if !slices.ContainsFunc(ctl.verbs, func(verb string) bool { return strings.HasPrefix(verb, "bootstrap") }) {
		t.Fatalf("verbs %q never loaded the job", ctl.verbs)
	}
}
