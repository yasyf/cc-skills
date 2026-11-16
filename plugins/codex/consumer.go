package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"slices"
	"time"

	"github.com/spf13/cobra"

	"github.com/yasyf/cc-interact/agent"
	"github.com/yasyf/cc-interact/cmd"
	"github.com/yasyf/cc-interact/daemon"
	"github.com/yasyf/cc-interact/event"
	"github.com/yasyf/cc-interact/procs"
	"github.com/yasyf/daemonkit/paths"
)

// appVersion is the runtime build identity and the --version output
// install-binary.sh pins on. goreleaser stamps the release value; "dev" marks
// an unstamped build the installer must not clobber.
var appVersion = "dev"

// wakeTimeout bounds the detached worker's owner-wake dial. Every failure inside
// it is swallowed so a dead daemon never crashes the worker or loses the
// completed run; a var so a test can shrink the bound.
var wakeTimeout = 30 * time.Second

const (
	// binaryName is the cobra root name and the prefix agent-inject stamps on
	// delivered directives.
	binaryName = "codex-ask"

	// appDir is the state-directory root (~/.cc-codex-ask) — the isolation axis
	// keeping codex-ask's daemon, socket, and db off cc-review/cc-orchestrate.
	appDir = ".cc-codex-ask"

	// statusOpen is the sole live subject status; ActiveStatuses treats it as
	// resumable across session rotation.
	statusOpen = "open"
)

func appPaths() paths.Paths { return paths.Paths{App: appDir} }

func launcher() daemon.Launcher {
	spec, err := appSpec()
	if err != nil {
		panic(err)
	}
	return daemon.Launcher{Daemon: spec, Paths: appPaths(), RuntimeBuild: appVersion}
}

func newClient(context.Context) (*daemon.Client, error) { return launcher().NewClient() }

// cwdOr resolves the scope: the explicit flag, else the process working directory.
func cwdOr(cwd string) string {
	if cwd != "" {
		return cwd
	}
	d, err := os.Getwd()
	if err != nil {
		panic(err)
	}
	return d
}

func agentGreeting(info agent.Info) string {
	return fmt.Sprintf("You are agent %s on the codex-ask channel; a parent's wake naming pending directives is authorized.", info.AgentID)
}

func buildServer() (*daemon.Server, error) {
	spec, err := appSpec()
	if err != nil {
		return nil, err
	}
	return daemon.New(daemon.Config{
		AppName:        binaryName,
		Paths:          appPaths(),
		Daemon:         spec,
		RuntimeBuild:   appVersion,
		ActiveStatuses: []string{statusOpen},
		AgentGreeting:  agentGreeting,
	})
}

func serve(ctx context.Context) error {
	s, err := buildServer()
	if err != nil {
		return err
	}
	return s.Serve(ctx)
}

// deps wires the substrate commands to codex-ask's launcher, control client, and
// window identity, for a windowed (non-headless) consumer keyed on the Claude
// window pid.
func deps() cmd.Deps {
	return cmd.Deps{
		Paths:                  appPaths(),
		Version:                appVersion,
		NewClient:              newClient,
		EnsureCurrent:          func(ctx context.Context) error { return launcher().EnsureCurrent(ctx, daemon.UpgradeTimeout) },
		EnsureCurrentIfRunning: func(ctx context.Context) error { return launcher().EnsureCurrentIfRunning(ctx) },
		Stop:                   func(ctx context.Context) error { return launcher().Stop(ctx, daemon.UpgradeTimeout) },
		ClaudePID:              procs.ClaudePID,
		Serve:                  serve,
	}
}

// directWake dials the daemon and enqueues one steering directive addressed to
// agentID: EnsureCurrent → NewClient → OpAgentDirect. It is the single enqueue
// path shared by the `direct` subcommand and the detached worker's owner wake.
// claudePID is passed in (the worker persists it from dispatch time; the
// subcommand supplies procs.ClaudePID) rather than resolved here.
func directWake(ctx context.Context, d cmd.Deps, session, scope string, claudePID int, agentID, origin, text string) (daemon.Reply, error) {
	if err := d.EnsureCurrent(ctx); err != nil {
		return daemon.Reply{}, err
	}
	body, _ := json.Marshal(map[string]string{"agent_id": agentID, "origin": origin, "text": text})
	client, err := d.NewClient(ctx)
	if err != nil {
		return daemon.Reply{}, err
	}
	defer func() { _ = client.Close() }()
	reply, err := client.Do(ctx, daemon.Envelope{
		Op: daemon.OpAgentDirect, Session: session, ClaudePID: claudePID, Scope: scope, Body: body,
	})
	if err != nil {
		return daemon.Reply{}, err
	}
	if !reply.OK {
		return daemon.Reply{}, errors.New(reply.Error)
	}
	return reply, nil
}

// wakeOwner enqueues a wake-only directive to the dispatching owner subagent once
// the worker's terminal status is durably on disk. Disk is truth: the directive
// names the terminal state and the reply/log paths, never the reply payload. The
// dial is bounded (wakeTimeout) and fail-open — a dead daemon is swallowed (at
// most a line in the lane log), so the worker still exits with status intact.
func wakeOwner(d cmd.Deps, sdir string, c cmdSpec) {
	text := fmt.Sprintf(
		"codex dispatch %s. Read the reply from disk at %s (log: %s). "+
			"Wake-only completion notice — the result is in the file, not in this message.",
		classify(sdir).state, c.Reply, c.Log)
	ctx, cancel := context.WithTimeout(context.Background(), wakeTimeout)
	defer cancel()
	if _, err := directWake(ctx, d, c.Session, c.Scope, c.ClaudePID, c.Owner, event.OriginSystem, text); err != nil {
		appendLine(c.Log, "codex-ask: owner wake failed (fail-open): "+err.Error())
	}
}

// appendLine best-effort appends one line to an existing file (the lane log),
// swallowing every error — a wake-failure note must never itself fail the worker.
func appendLine(path, line string) {
	f, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0) //nolint:gosec // appends to the lane's own log by path, by design
	if err != nil {
		return
	}
	defer func() { _ = f.Close() }()
	_, _ = fmt.Fprintln(f, line)
}

// directCmd enqueues a steering directive addressed to an agent via directWake
// and prints the daemon's reply; an empty --agent targets the top-level agent.
func directCmd(d cmd.Deps) *cobra.Command {
	var session, cwd, agentID, origin string
	c := &cobra.Command{
		Use:   "direct <text>",
		Short: "Enqueue a steering directive for an agent (empty --agent = the top-level agent)",
		Args:  cobra.ExactArgs(1),
		RunE: func(c *cobra.Command, args []string) error {
			reply, err := directWake(c.Context(), d, session, cwdOr(cwd), d.ClaudePID(), agentID, origin, args[0])
			if err != nil {
				return err
			}
			_, err = fmt.Fprintf(c.OutOrStdout(), "%s\n", reply.Body)
			return err
		},
	}
	c.Flags().StringVar(&session, "session", os.Getenv("CLAUDE_CODE_SESSION_ID"), "session id (defaults to $CLAUDE_CODE_SESSION_ID)")
	c.Flags().StringVar(&cwd, "cwd", "", "working directory / scope (defaults to the current directory)")
	c.Flags().StringVar(&agentID, "agent", "", "target agent id (empty targets the top-level agent)")
	c.Flags().StringVar(&origin, "origin", event.OriginHuman, "directive origin label")
	return c
}

// consumerRoot is the cobra tree for the additive cc-interact subcommands. Its
// name is binaryName, so agent-inject stamps "codex-ask" on delivered
// directives. It sits beside main's flag dispatch, never shadowing it:
// every subcommand name is a plain word, not a leading --flag case.
func consumerRoot() *cobra.Command {
	d := deps()
	r := &cobra.Command{
		Use:           binaryName,
		Short:         "codex-ask cc-interact steering plane",
		SilenceUsage:  true,
		SilenceErrors: true,
	}
	r.AddCommand(
		cmd.DaemonCmd(d),
		cmd.StopCmd(d),
		cmd.AgentStartCmd(d),
		cmd.AgentInjectCmd(d),
		cmd.AgentStopCmd(d),
		cmd.AgentReportCmd(d),
		directCmd(d),
	)
	r.AddCommand(platformCmds()...)
	return r
}

// consumerSubcommands are the additive subcommand names main routes to
// consumerRoot before falling through to askMode. Each is a plain word, so it
// cannot collide with a leading --flag dispatch case.
var consumerSubcommands = map[string]bool{
	"daemon":       true,
	"stop":         true,
	"agent-start":  true,
	"agent-inject": true,
	"agent-stop":   true,
	"agent-report": true,
	"direct":       true,
}

func isConsumerSubcommand(name string) bool {
	return consumerSubcommands[name] ||
		slices.ContainsFunc(platformCmds(), func(c *cobra.Command) bool { return c.Name() == name })
}

// runConsumer executes the cc-interact subcommand tree, exiting non-zero on error
// like main's other modes.
func runConsumer(args []string) {
	root := consumerRoot()
	root.SetArgs(args)
	if err := root.Execute(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
