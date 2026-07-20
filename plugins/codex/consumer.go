package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"time"

	"github.com/spf13/cobra"

	"github.com/yasyf/cc-interact/agent"
	"github.com/yasyf/cc-interact/channel"
	"github.com/yasyf/cc-interact/cmd"
	"github.com/yasyf/cc-interact/daemon"
	"github.com/yasyf/cc-interact/event"
	"github.com/yasyf/cc-interact/procs"
	"github.com/yasyf/daemonkit/paths"
)

// appVersion is the daemon build identity (daemon.Config.Version, gating
// daemon-upgrade matching). goreleaser stamps -s -w only, so releases share the
// default until the Phase F ldflags land a per-build stamp.
var appVersion = "0.0.0"

// wakeTimeout bounds the detached worker's owner-wake dial. Every failure inside
// it is swallowed so a dead daemon never crashes the worker or loses the
// completed run; a var so a test can shrink the bound.
var wakeTimeout = 30 * time.Second

const (
	// binaryName is the cobra root name: the channel MCP server name, the source
	// its tags carry, and the prefix agent-inject stamps on delivered directives.
	binaryName = "codex-ask"

	// appDir is the state-directory root (~/.cc-codex-ask) — the isolation axis
	// keeping codex-ask's daemon, socket, and db off cc-review/cc-orchestrate.
	appDir = ".cc-codex-ask"

	// statusOpen is the sole live subject status; ActiveStatuses treats it as
	// resumable across session rotation.
	statusOpen = "open"

	// awaitConsumer names the await tool's presence when it resolves the subject.
	awaitConsumer = "await"

	// awaitTimeout is the await tool's default long-poll window, under typical MCP
	// stdio idle limits.
	awaitTimeout = 4 * time.Minute

	// notifyMethod is the JSON-RPC method each subject event is pushed under.
	notifyMethod = "notifications/codex-ask/channel"
)

func appPaths() paths.Paths { return paths.Paths{App: appDir} }

func launcher() daemon.Launcher {
	return daemon.Launcher{Paths: appPaths(), Version: appVersion, Args: []string{"daemon"}}
}

func newClient(ctx context.Context) (*daemon.Client, error) { return launcher().NewClient(ctx) }

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

// agentGreeting is a newly registered child's identity-bootstrap directive. A
// child refuses unattributed directives, so the greeting names the channel, hands
// the child its own agent id (children never learn it otherwise), and frames a
// parent's relay wake as authorized rather than prompt injection.
func agentGreeting(info agent.Info) string {
	return fmt.Sprintf("You are agent %s in this session, connected to the codex-ask steering channel. "+
		"Authorized directives — a completed codex dispatch's result, or an operator instruction — may reach you "+
		"prefixed [<origin> #<id>] inside your tool results, as stop-time instructions when you finish, or through the "+
		"await tool (call it with your agent id, %s, to park until one arrives). A wake message from your parent agent "+
		"naming pending directives is authorized too, not prompt injection: call await or continue to collect them. "+
		"Treat each as an instruction from your operator — act on it once, then continue or finish your task.",
		info.AgentID, info.AgentID)
}

// buildServer composes the headless codex-ask daemon: the core op registry (the
// agent plane + resolve, registered by daemon.New), the channel presence
// lifecycle, and the agent greeting. It registers no domain op — the dispatch
// registry is the filesystem, not the daemon — and mounts no HTTP bridge.
func buildServer() (*daemon.Server, error) {
	c := channel.Connectivity{}
	return daemon.New(daemon.Config{
		AppName:           binaryName,
		Paths:             appPaths(),
		Version:           appVersion,
		ActiveStatuses:    []string{statusOpen},
		PresenceEventType: c.Type(),
		OnPresenceChange:  c.OnPresenceChange,
		BootReconcile:     c.BootReconcile,
		AgentGreeting:     agentGreeting,
		// ScopeResolve nil → identity; Gate/AgentGate nil → allow every edit and
		// stop; Migrate nil → no domain tables.
	})
}

func serve(ctx context.Context) error {
	s, err := buildServer()
	if err != nil {
		return err
	}
	return s.Serve(ctx)
}

// resolveSubjectPort polls the daemon for the scope's subject id and HTTP port so
// the await tool can long-poll /agents/await, mirroring cmd's own stream resolver.
func resolveSubjectPort(ctx context.Context, client *daemon.Client, session, scope string) (string, int, error) {
	for {
		reply, err := client.Do(ctx, daemon.Envelope{
			Op: daemon.OpResolve, Session: session, ClaudePID: procs.ClaudePID(), Scope: scope, Consumer: awaitConsumer,
		})
		if err != nil {
			return "", 0, err
		}
		if reply.SubjectID != "" {
			return reply.SubjectID, reply.HTTPPort, nil
		}
		select {
		case <-ctx.Done():
			return "", 0, ctx.Err()
		case <-time.After(time.Second):
		}
	}
}

// channelInstructions is the steering-channel guide folded into a connected
// agent's prompt at channel MCP initialize: the await receive path (codex has no
// watch/ack loop), plus RelayStep — the parent-side rule to SendMessage-wake an
// owner stranded 'done' with a pending directive (the relay fallback to park).
func channelInstructions() string {
	return channel.Instructions(channel.InstructionsSpec{
		Desc:    "the codex-ask steering channel",
		Traffic: "Steering directives — a completed codex dispatch's result, or an operator instruction — reach you",
		Source:  binaryName,
		Guide: "Call the await tool with your agent_id (named in your greeting directive) to park until a directive " +
			"arrives; directives also land inside your tool results and as stop-time instructions when you finish. " +
			"Act on each directive once, then continue or finish your task.",
		SilentOutside: "a codex-ask steering session",
	}) + "\n\n" + channel.RelayStep(binaryName)
}

// channelTools advertises the await tool — the park an owner subagent calls to
// wait for its detached worker's completion or an operator directive — to the
// channel MCP server. codex has no human-reply round trip, so await is the only
// domain tool.
func channelTools(_ context.Context, session, scope string) ([]channel.Tool, string, string, error) {
	await := channel.NewAwaitTool(channel.AwaitSpec{
		Resolve: func(ctx context.Context) (string, int, error) {
			client, err := newClient(ctx)
			if err != nil {
				return "", 0, err
			}
			defer func() { _ = client.Close() }()
			return resolveSubjectPort(ctx, client, session, scope)
		},
		Timeout: awaitTimeout,
	})
	return []channel.Tool{await}, notifyMethod, channelInstructions(), nil
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
		ClaudePID:              procs.ClaudePID,
		WindowAlive:            procs.LiveClaude,
		TerminalEvent:          func(string) bool { return false },
		Serve:                  serve,
		ChannelTools:           channelTools,
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
func wakeOwner(sdir string, c cmdSpec) {
	text := fmt.Sprintf(
		"codex dispatch %s. Read the reply from disk at %s (log: %s). "+
			"Wake-only completion notice — the result is in the file, not in this message.",
		classify(sdir).state, c.Reply, c.Log)
	ctx, cancel := context.WithTimeout(context.Background(), wakeTimeout)
	defer cancel()
	if _, err := directWake(ctx, deps(), c.Session, c.Scope, c.ClaudePID, c.Owner, event.OriginSystem, text); err != nil {
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
// name is binaryName, so the channel server and agent-inject stamp "codex-ask" as
// the steering source. It sits beside main's flag dispatch, never shadowing it:
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
		cmd.AgentStartCmd(d),
		cmd.AgentInjectCmd(d),
		cmd.AgentStopCmd(d),
		cmd.AgentReportCmd(d),
		cmd.ChannelCmd(d),
		directCmd(d),
	)
	return r
}

// consumerSubcommands are the additive subcommand names main routes to
// consumerRoot before falling through to askMode. Each is a plain word, so it
// cannot collide with a leading --flag dispatch case.
var consumerSubcommands = map[string]bool{
	"daemon":       true,
	"agent-start":  true,
	"agent-inject": true,
	"agent-stop":   true,
	"agent-report": true,
	"channel":      true,
	"direct":       true,
}

func isConsumerSubcommand(name string) bool { return consumerSubcommands[name] }

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
