package orca

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/incident"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/wait"
)

const (
	failedExit     = 1
	unsettledExit  = 2
	defaultTimeout = 600.0
)

func LaunchCommand(a *app.App) *cobra.Command {
	cmd := lanes.LaunchCommand(a)
	cmd.AddCommand(
		startCommand(a),
		workerCommand(a),
		claudeCommand(),
		laneCommand(a),
		incidentCommand(a),
		RelayCommand(a),
		lineCommand(a, "hold SLUG OWNER WHAT", "Start the runner's clock on an urgent hold", 3, holdLine),
		lineCommand(a, "unhold SLUG", "Lift a hold", 1, unholdLine),
		lineCommand(a, "withdraw R<n>|unfixed:<msg>:<n>|LANE", "Withdraw a launch by key, or a lane", 1, withdrawLine),
	)
	return cmd
}

func RelayCommand(a *app.App) *cobra.Command {
	return lineCommand(a, "relay LANE[,LANE...] TEXT", "Relay text to each named lane's live dispatch", 2, relayLine)
}

func RelayRootCommand(a *app.App) *cobra.Command {
	cmd := RelayCommand(a)
	cmd.AddCommand(relayRunCommand(a), relayRebindCommand(a))
	return cmd
}

type posterFlags struct {
	drive, lane string
}

func (f *posterFlags) bind(cmd *cobra.Command) {
	cmd.Flags().StringVar(&f.drive, "drive", "", "the cci drive the orca runner reads; default the drive bound to this session")
	cmd.Flags().StringVar(&f.lane, "lane", "", "the lane posting the lines; default the lane bound to this session, then the coordinator")
}

func (f *posterFlags) poster(cmd *cobra.Command, a *app.App) Poster {
	return Poster{Bus: CCI{}, Runner: a.Runner, Env: os.Getenv, Now: a.Now, Sleep: time.Sleep, Out: cmd.OutOrStdout(), Drive: f.drive, Author: f.author(cmd, a)}
}

func (f *posterFlags) author(cmd *cobra.Command, a *app.App) string {
	if f.lane != "" {
		return f.lane
	}
	bound, err := lanes.BoundLane(cmd.Context(), a.Runner)
	if err != nil || bound == "" {
		return coordinatorLane
	}
	return bound
}

func (p Poster) LaunchOrExit(ctx context.Context, order Order, timeout time.Duration) error {
	label, err := p.Launch(ctx, order, timeout)
	switch {
	case err != nil:
		return err
	case label == "":
		return &wait.ExitError{Code: unsettledExit}
	case label != verdictLaunched && label != verdictPosted:
		return &wait.ExitError{Code: failedExit}
	}
	return nil
}

func seconds(value float64) time.Duration {
	return time.Duration(value * float64(time.Second))
}

func lineCommand(a *app.App, use, short string, count int, line func([]string) (string, string)) *cobra.Command {
	var flags posterFlags
	cmd := &cobra.Command{
		Use:   use,
		Short: short,
		Args:  cobra.ExactArgs(count),
		RunE: func(cmd *cobra.Command, args []string) error {
			topic, text := line(args)
			return flags.poster(cmd, a).Line(cmd.Context(), topic, text)
		},
	}
	flags.bind(cmd)
	return cmd
}

func laneCommand(a *app.App) *cobra.Command {
	var flags posterFlags
	var now bool
	var role string
	var timeout float64
	var written NewBrief
	var model, effort, id string
	cmd := &cobra.Command{
		Use:   "lane LANE MODEL EFFORT BRIEF | lane LANE [--model MODEL --effort EFFORT] --title TITLE --body FILE",
		Short: "Post one lane's launch to the orca runner and wait for its verdict; a stem launches as its next number",
		Long: `Run every check the runner applies, post the launch, print each stage the launch reaches with its seconds since the post, and print the runner's verdict.

Exit 0 when the lane launched, or when no verdict came within --timeout-seconds while the runner is alive, since its verdict then arrives as a desk-runner record; 1 when the runner refused it or a check did; and 2 when no verdict came and the runner has posted no orca alive record in five minutes. A drive whose orca runner posted no orca alive record in five minutes posts nothing.

With --body, a file or - for stdin, the launch writes the brief itself, to <state dir>/<lane>/brief.md, from --title, the register slice line, --scale, --ccx or --place, the --contract file, the body, the posting lane's <state dir>/<lane>/brief-lines.txt and an Owner orders line naming each owner:m<row> the body cites, since a bare m<row> cites a row as background and orders nothing; it takes no BRIEF, and the model and effort default to opus high. A Fable or astra model, or effort xhigh or max, needs --owner-pick naming the owner row whose words asked for it, which writes the brief's Owner pick line. The runner refreshes the register slice at launch.`,
		Args: func(cmd *cobra.Command, args []string) error {
			if written.BodyFile == "" {
				return cobra.ExactArgs(4)(cmd, args)
			}
			if len(args) != 1 && len(args) != 3 {
				return errors.New("--body takes LANE, or LANE MODEL EFFORT, and writes the brief itself")
			}
			return nil
		},
		RunE: func(cmd *cobra.Command, args []string) error {
			poster := flags.poster(cmd, a)
			lane, err := poster.Numbered(cmd.Context(), args[0])
			if err != nil {
				return err
			}
			if written.BodyFile == "" {
				order := Order{Lane: lane, Model: args[1], Effort: args[2], Role: role, Brief: args[3], Now: now, ID: id}
				return poster.LaunchOrExit(cmd.Context(), order, seconds(timeout))
			}
			entry, err := poster.Entry(cmd.Context())
			if err != nil {
				return err
			}
			written.StandingDir = filepath.Join(entry.StateDir, poster.Author)
			written.Stdin = cmd.InOrStdin()
			brief, err := written.Write(entry.StateDir, lane)
			if err != nil {
				return err
			}
			fmt.Fprintf(cmd.OutOrStdout(), "wrote %s\n", brief)
			order := Order{Lane: lane, Model: model, Effort: effort, Role: role, Brief: brief, Now: now, ID: id}
			if len(args) == 3 {
				order.Model, order.Effort = args[1], args[2]
			}
			return poster.LaunchOrExit(cmd.Context(), order, seconds(timeout))
		},
	}
	flags.bind(cmd)
	cmd.Flags().StringVar(&written.Title, "title", "", "with --body, the brief's title")
	cmd.Flags().StringVar(&written.BodyFile, "body", "", "write the brief from this file, or - for stdin, instead of taking BRIEF")
	cmd.Flags().StringVar(&model, "model", defaultLaneModel, "with --body, the lane's model")
	cmd.Flags().StringVar(&effort, "effort", defaultLaneEffort, "with --body, the lane's effort")
	cmd.Flags().StringVar(&written.OwnerPick, "owner-pick", "", "with --body, owner:m<row>, the owner row whose words asked for a Fable or astra model or an effort above high")
	cmd.Flags().StringVar(&written.Contract, "contract", "", "with --body, a file the brief carries above the body, such as a desk's helper contract")
	cmd.Flags().StringVar(&written.Scale, "scale", "medium", "with --body, the brief's Scale line")
	cmd.Flags().StringVar(&written.Place, "place", placeLocal, "with --body, where the lane runs: local, on this Mac, or remote")
	cmd.Flags().StringVar(&written.CCX, "ccx", "", "with --body, the brief's whole ccx line in place of --place, as role=read,place=local,why=<reason>; incident=none unless it names one")
	cmd.MarkFlagsMutuallyExclusive("ccx", "place")
	cmd.Flags().StringSliceVar(&written.Topics, "topics", nil, "with --body, the subjects the lane decides on, for the register slice's owner answers")
	cmd.Flags().BoolVar(&now, "now", false, "owner-directed: launch beside a live lane already holding this work")
	cmd.Flags().StringVar(&id, "id", "", "the launch line's cci idempotency key: a repost under a key already on the drive returns the line first posted, so a retried launch reaches the runner once")
	cmd.Flags().StringVar(&role, "role", "", "the role the lane registry records for it, over the brief's ccx role")
	cmd.Flags().Float64Var(&timeout, "timeout-seconds", defaultTimeout, "how long to wait for the runner's verdict")
	return cmd
}

func incidentCommand(a *app.App) *cobra.Command {
	var flags posterFlags
	var alert string
	var timeout float64
	cmd := &cobra.Command{
		Use:   "incident NAME FIX_BRIEF [EVIDENCE_BRIEF [COMMS_BRIEF]]",
		Short: "Post NAME-fix, NAME-evidence and NAME-comms NOW on the incident route and wait for their verdicts",
		Long:  "With --alert each brief is a template: its Coordinator: lines go, every <lane> becomes that lane's name, its Alert: line becomes Alert: TEXT, and the result is written to <state dir>/<lane>/brief.md. A brief still holding a placeholder the coordinator fills posts nothing.",
		Args:  cobra.RangeArgs(2, 4),
		RunE: func(cmd *cobra.Command, args []string) error {
			evidence, comms := "", ""
			if len(args) >= 3 {
				evidence = args[2]
			}
			if len(args) == 4 {
				comms = args[3]
			}
			if alert == "" {
				faults, err := wrongLaneFaults(incidentOrders(args[0], args[1], evidence, comms))
				if err != nil {
					return err
				}
				if len(faults) > 0 {
					return fmt.Errorf("refused, nothing was posted: %s", strings.Join(faults, "; "))
				}
			}
			claude, err := drive.ConfigDir()
			if err != nil {
				return err
			}
			err = incident.DeskLaunch{
				Bus: incident.CCI{}, Runner: a.Runner, Drive: flags.drive, Author: flags.lane, Name: args[0], FixBrief: args[1], EvidenceBrief: evidence, CommsBrief: comms, Alert: alert,
				Claude: claude, Timeout: seconds(timeout), Poll: verdictPoll, Now: a.Now, Out: cmd.OutOrStdout(),
			}.Run(cmd.Context())
			if errors.Is(err, incident.ErrUnsettled) {
				return &wait.ExitError{Code: unsettledExit}
			}
			return err
		},
	}
	flags.bind(cmd)
	cmd.Flags().StringVar(&alert, "alert", "", "the Alert line: the monitor or Slack link, what fails where, and the owner's words")
	cmd.Flags().Float64Var(&timeout, "timeout-seconds", defaultTimeout, "how long to wait for the runner's verdicts")
	return cmd
}

func workerCommand(a *app.App) *cobra.Command {
	return &cobra.Command{
		Use:   "worker LANE MODEL EFFORT BRIEF",
		Short: "Prepare one Sprite and start a worker on it, or a local worktree with WORKER_LOCAL=1, and print one JSON line",
		Long: `Prepare one Sprite through cc-remote, copy what the brief names to it, install cci, the lr this pack pins for linux-x86_64 in bin/lr.binrun, and each WORKER_TOOLS pin on the Sprite, and start a worker. Print one JSON line naming the task, dispatch, terminal and worktree. A failure prints one line on stderr, exit 1, and keeps what it made in BRIEF's .worker directory. A brief gets one attempt at worker-start. MODEL incident runs gpt-6.1-sol on the fast tier and incident-opus runs claude-opus-5-5 in fast mode, each as an incident lane.

  WORKER_RUN            the Orca Run id, required
  WORKER_CHECKOUT       the checkout whose tools/cc-remote starts Sprites, required
  WORKER_CCI_URL        the cci linux_amd64 release archive a Sprite installs, required
  WORKER_CCI_SHA256     the SHA-256 of that archive, required
  WORKER_TOOLS          a JSON list of {kind, name, url, sha256, version} pins, binary, package or plugin, each installed from its verified archive
  WORKER_CC_REMOTE      the cc-remote command, default the checkout's own
  CC_REMOTE_CONFIG      the cc-remote config, default the checkout's own
  WORKER_NOTES_REPO     the checkout ccn reads docs and tasks from, default WORKER_CHECKOUT
  WORKER_REF            the branch or tag a Sprite checks out
  WORKER_EXPECT_BASE    the full commit that checkout must be at
  WORKER_SERVICE_TIER   a codex service tier; fast needs WORKER_INCIDENT=1
  WORKER_MCP_CONFIG     the agent's MCP config
  WORKER_RELAY_SPONSOR  the Sprite whose service key joins this one to the cci relay, required on a Sprite route
  WORKER_LANDING_REPO   the repo the drive's landing desk queues, set on the Sprite's agent as CLAUDE_LONG_RUNNING_LANDING_REPO
  WORKER_LOCAL          1 starts a local worktree and claude instead
  WORKER_REPO           the Orca repository of a local worktree, required with WORKER_LOCAL=1
  WORKER_BASE_BRANCH    the base of a local worktree, default origin/dev`,
		Args: cobra.ExactArgs(4),
		RunE: func(cmd *cobra.Command, args []string) error {
			starter := &WorkerStarter{Runner: a.Runner, Env: os.Getenv, Sleep: time.Sleep, Stderr: cmd.ErrOrStderr(), Spawn: SpawnDetached, Bus: CCI{}}
			line, err := starter.Run(cmd.Context(), args[0], args[1], args[2], args[3])
			var failed *Failed
			if errors.As(err, &failed) {
				fmt.Fprintln(cmd.ErrOrStderr(), failed.Line)
				return &wait.ExitError{Code: failedExit}
			}
			if err != nil {
				return err
			}
			fmt.Fprintln(cmd.OutOrStdout(), line)
			return nil
		},
	}
}

func startCommand(a *app.App) *cobra.Command {
	return &cobra.Command{
		Use:   "start LANE MODEL EFFORT BRIEF",
		Short: "Launch one Orca worker from the terminal that coordinates the Run, and print one line",
		Long: `Launch one Orca worker for LANE and print one line:

  LANE ready task=ID dispatch=ID terminal=HANDLE worktree=PATH

Anything else is a failure, printed as one line naming the step, exit 1. A launch that fails before worker-start, or that worker-start refuses before it dispatches anything, rolls back what it made and says so.

A relaunch whose receipt names a worktree that is still a checkout resumes in it. Every other launch makes a worktree under a name no launch has used: PREFIX+LANE-RUN-N, on branch USER/PREFIX+LANE-RUN-N. orca worktree create makes it by default; ORCA_LAUNCH_SPARSE=1 adds it with git worktree add from the main checkout, sparse in a repo over ORCA_LAUNCH_SPARSE_FILES tracked files.

MODEL is opus, sonnet, fable, a claude-* model id, sol or codex (gpt-6.1-sol on the standard tier), incident (gpt-6.1-sol on the fast tier), incident-opus (claude-opus-5-5 in fast mode), astra (gpt-6-astra, for exceptional cases only), or a gpt-* model id. EFFORT is low, medium, high, xhigh or max.

Only the terminal bound to the Run may call worker-start, so a launch from any other terminal creates nothing and names the rebind command.

  ORCA_LAUNCH_RUN            orchestration Run id, default the Run of this session's drive
  ORCA_LAUNCH_STALL_SECONDS  give up with LAUNCH-FAILED once one wait lasts this long, default 180
  ORCA_LAUNCH_REPO           Orca repo id, default the repo at the parent's main checkout
  ORCA_LAUNCH_PARENT         coordinator worktree path, default the current directory
  ORCA_LAUNCH_NO_PARENT      1 creates a top-level worktree, as incident always does
  ORCA_LAUNCH_PREFIX         worktree name prefix, default none
  ORCA_LAUNCH_ROOT           directory a new worktree goes in
  ORCA_LAUNCH_BASE           base branch, default the parent checkout's origin/HEAD
  ORCA_LAUNCH_STATE          receipt directory, default $XDG_STATE_HOME/long-running/orca-launch/RUN
  ORCA_LAUNCH_CLAUDE_ARGS    further claude args, default none
  ORCA_LAUNCH_MCP_CONFIG     space-separated --mcp-config values for a claude worker, default none
  ORCA_LAUNCH_SLACK_MCP_CONFIG  --mcp-config value for a brief whose ccx: line says mcp=slack, default none
  ORCA_LAUNCH_CODEX_MCP      inline TOML table for an incident worker's mcp_servers, default {}
  ORCA_LAUNCH_BOOT_SECONDS   ceiling on the waits for a terminal, its agent and its first turn, default 180
  ORCA_LAUNCH_WORKTREE_SECONDS  ceiling on the waits for a worktree to register and check out, default 180
  ORCA_LAUNCH_PREPARE        shell command run in each worktree this launch creates, before its worker starts, default tools/hooks/warm-lane.sh where the worktree has it
  ORCA_LAUNCH_PREPARE_SECONDS  ceiling on that command, default 120
  ORCA_LAUNCH_SPARSE         1 adds each new worktree by git worktree add
  ORCA_LAUNCH_SPARSE_FILES   tracked-file count above which an added worktree is sparse, default 5000`,
		Args: cobra.ExactArgs(4),
		RunE: func(cmd *cobra.Command, args []string) error {
			starter := &Starter{Runner: a.Runner, Bus: CCI{}, Env: os.Getenv, Sleep: time.Sleep, Now: a.Now, Stderr: cmd.ErrOrStderr()}
			line, err := starter.Run(cmd.Context(), args[0], args[1], args[2], args[3])
			var failed *Failed
			if errors.As(err, &failed) {
				fmt.Fprintln(cmd.OutOrStdout(), failed.Line)
				return &wait.ExitError{Code: failedExit}
			}
			if err != nil {
				return err
			}
			fmt.Fprintln(cmd.OutOrStdout(), line)
			return nil
		},
	}
}
