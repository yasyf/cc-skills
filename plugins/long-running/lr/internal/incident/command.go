package incident

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"syscall"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/update"
)

var (
	adoptable = []string{RoleFix, RoleEvidence}
	grants    = []string{"thread", "channel", "sync", "rebuild"}
	runFacts  = []string{"checkout", "trunk", "repo", "pipeline", "orca_run", "orca_repo", "bus", "comms_lane", "root_lane"}
)

func claudeDir() (string, error) {
	if dir := os.Getenv("CLAUDE_CONFIG_DIR"); dir != "" {
		return dir, nil
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, ".claude"), nil
}

func defaultStore() (Store, error) {
	root, err := DefaultRoot()
	return Store{Root: root}, err
}

func pairs(values, allowed []string, flag string) (map[string]string, error) {
	out := make(map[string]string, len(values))
	for _, value := range values {
		name, held, _ := strings.Cut(value, "=")
		if !slices.Contains(allowed, name) || held == "" {
			return nil, fmt.Errorf("--%s %q is not NAME=VALUE with NAME one of %s", flag, value, strings.Join(allowed, ", "))
		}
		out[name] = held
	}
	return out, nil
}

func required(cmd *cobra.Command, names ...string) {
	for _, name := range names {
		if err := cmd.MarkFlagRequired(name); err != nil {
			panic(err)
		}
	}
}

func Command(a *app.App) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "incident",
		Short: "One durable owner for an active incident, from intake to the final reply",
	}
	cmd.AddCommand(openCommand(a), noteCommand(), grantCommand(), runCommand(a), briefCommand())
	cmd.AddCommand(update.IncidentCommands(a)...)
	return cmd
}

func openCommand(a *app.App) *cobra.Command {
	var o Opening
	var adopt, grant []string
	cmd := &cobra.Command{
		Use:   "open",
		Short: "Record an incident for the executor to own",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) (err error) {
			if o.Adopted, err = pairs(adopt, adoptable, "adopt"); err != nil {
				return err
			}
			if o.Grants, err = pairs(grant, grants, "grant"); err != nil {
				return err
			}
			s, err := defaultStore()
			if err != nil {
				return err
			}
			id, err := s.Open(o, a.Now())
			if err != nil {
				return err
			}
			path, err := s.Path(id)
			if err != nil {
				return err
			}
			fmt.Fprintf(cmd.OutOrStdout(), "incident %s at %s; run: lr incident run --incident %s\n", id, path, id)
			return nil
		},
	}
	flags := cmd.Flags()
	flags.StringVar(&o.Kind, "kind", "", "pr-review or alert")
	flags.StringVar(&o.ID, "incident", "", "the incident id; default the UTC month, day, hour, and minute")
	flags.StringVar(&o.Thread, "thread", "", "the Slack permalink the incident answers in")
	flags.StringVar(&o.Onset, "onset", "", "ISO time the outage began; failed builds since then are owned")
	flags.StringVar(&o.Reported, "reported", "", "ISO time of the first report; the fix is due live 20 minutes later")
	flags.StringVar(&o.Bus, "bus", "", "the cci drive")
	flags.StringVar(&o.CommsLane, "comms-lane", "", "the lane that posts to Slack")
	flags.StringVar(&o.RootLane, "root-lane", "", "the lane that answers the executor's asks")
	flags.StringVar(&o.Checkout, "checkout", "", "a checkout of the repo; activation adds its own worktree from it")
	flags.StringVar(&o.Pipeline, "pipeline", "", "the Buildkite pipeline whose stored settings the fix changes")
	flags.StringVar(&o.Target, "target", "", "the broken service; default the pipeline")
	flags.StringVar(&o.Alert, "alert", "", "the alert's own link (Sentry issue, monitor); default the thread")
	flags.StringVar(&o.CodePath, "code-path", "", "where the fix lane starts; default the pipeline or target")
	flags.StringVar(&o.Runbook, "runbook", "", "where the fix lane logs a production apply")
	flags.StringArrayVar(&adopt, "adopt", nil, "ROLE=LANE, a fix or evidence lane already running; the executor records it instead of launching one")
	flags.StringVar(&o.Repo, "repo", "Forge-AI/monorepo", "the fix's repository")
	flags.StringVar(&o.Trunk, "trunk", "dev", "the repository's trunk")
	flags.StringArrayVar(&grant, "grant", nil, "NAME=REF, the authority for one kind of side effect")
	flags.StringVar(&o.ExpectConfig, "expect-config", "", "text the stored pipeline configuration must carry once live")
	flags.IntVar(&o.RepairPR, "repair-pr", 0, "the fix PR, when one already exists; skips the launches")
	flags.StringVar(&o.OrcaRun, "orca-run", "", "the Orca Run the lanes launch under")
	flags.StringVar(&o.OrcaRepo, "orca-repo", "", "the Orca repo the lanes launch in")
	flags.StringVar(&o.Common, "common", "", "the drive's common.md lane contract, prepended to both briefs")
	flags.StringArrayVar(&o.Rulings, "ruling", nil, "an owner design ruling on the touched subsystem, verbatim with its id; the fix brief quotes it")
	flags.StringVar(&o.EntryPoint, "entry-point", "", "the symbol, at file:line, that the rulings make the fix call")
	required(cmd, "kind", "thread", "onset", "reported", "bus", "comms-lane", "root-lane", "checkout")
	return cmd
}

func noteCommand() *cobra.Command {
	var id, mechanism, live, notOurs, designCheck string
	var pr int
	var designOK bool
	cmd := &cobra.Command{
		Use:   "note",
		Short: "A worker reports the repair PR, the mechanism, or live or not-ours evidence",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			s, err := defaultStore()
			if err != nil {
				return err
			}
			revision := int64(0)
			err = s.Input(id, func(tx *Tx) error {
				for key, value := range map[string]string{"mechanism": mechanism, "live": live, "not_ours": notOurs, "design_check": designCheck} {
					if value == "" {
						continue
					}
					if err := tx.SetFact(key, value); err != nil {
						return err
					}
				}
				if cmd.Flags().Changed("pr") {
					if err := tx.SetFact("repair_pr", pr); err != nil {
						return err
					}
				}
				if designOK {
					if err := tx.SetFact("design_ok", true); err != nil {
						return err
					}
				}
				revision = tx.InputRevision + 1
				return nil
			})
			if err != nil {
				return err
			}
			fmt.Fprintf(cmd.OutOrStdout(), "noted on %s at input revision %d\n", id, revision)
			return nil
		},
	}
	flags := cmd.Flags()
	flags.StringVar(&id, "incident", "", "the incident id")
	flags.IntVar(&pr, "pr", 0, "the repair PR")
	flags.StringVar(&mechanism, "mechanism", "", "the mechanism, one line")
	flags.StringVar(&live, "live", "", "evidence the fix is live, for an incident with no pipeline to activate")
	flags.StringVar(&notOurs, "not-ours", "", "the evidence that the alert is not ours; the incident closes with no fix")
	flags.StringVar(&designCheck, "design-check", "", "the entry point the fix calls and how it meets each ruling, recorded before the PR opens")
	flags.BoolVar(&designOK, "design-ok", false, "the coordinator confirms the design check")
	required(cmd, "incident")
	return cmd
}

func grantCommand() *cobra.Command {
	var id, name, ref string
	cmd := &cobra.Command{
		Use:   "grant",
		Short: "Add the authority a decision asked for",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			if !slices.Contains(grants, name) {
				return fmt.Errorf("--name %q is not one of %s", name, strings.Join(grants, ", "))
			}
			s, err := defaultStore()
			if err != nil {
				return err
			}
			if err := s.Input(id, func(tx *Tx) error { return tx.SetChild("grants", name, ref) }); err != nil {
				return err
			}
			fmt.Fprintf(cmd.OutOrStdout(), "%s grant %s=%s\n", id, name, ref)
			return nil
		},
	}
	flags := cmd.Flags()
	flags.StringVar(&id, "incident", "", "the incident id")
	flags.StringVar(&name, "name", "", "thread, channel, sync, or rebuild")
	flags.StringVar(&ref, "ref", "", "the authority's reference")
	required(cmd, "incident", "name", "ref")
	return cmd
}

func runCommand(a *app.App) *cobra.Command {
	var id, owner, alert, fixBrief, evidenceBrief, commsBrief, drive, lane string
	var interval, timeout float64
	var once bool
	cmd := &cobra.Command{
		Use:   "run [slug]",
		Short: "Own an incident until its final reply posts, or launch a slug's fix, evidence and comms lanes",
		Long: "lr incident run --incident ID owns an opened incident until its final reply posts.\n" +
			"lr incident run SLUG --fix-brief FILE [--evidence-brief FILE] [--comms-brief FILE] [--alert TEXT] posts SLUG-fix, SLUG-evidence and SLUG-comms to the orca desk on the incident route and waits for the runner's verdicts.",
		Args: cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if len(args) == 1 {
				if id != "" || fixBrief == "" {
					return errors.New("name a slug with --fix-brief, or --incident alone")
				}
				claude, err := claudeDir()
				if err != nil {
					return err
				}
				return DeskLaunch{
					Bus: CCI{}, Drive: drive, Author: lane, Name: args[0], FixBrief: fixBrief, EvidenceBrief: evidenceBrief, CommsBrief: commsBrief, Alert: alert,
					Claude: claude, Timeout: time.Duration(timeout * float64(time.Second)), Poll: verdictPoll, Now: a.Now, Out: cmd.OutOrStdout(),
				}.Run(cmd.Context())
			}
			if id == "" {
				return errors.New("name an --incident, or a slug with --fix-brief")
			}
			return own(cmd, a, id, owner, time.Duration(interval*float64(time.Second)), once)
		},
	}
	flags := cmd.Flags()
	flags.StringVar(&id, "incident", "", "the incident id")
	flags.StringVar(&owner, "owner", "", "the owner this runner acts as; default the executor that opened the incident")
	flags.Float64Var(&interval, "interval", DefaultInterval.Seconds(), "seconds between passes")
	flags.BoolVar(&once, "once", false, "run one pass and stop")
	flags.StringVar(&alert, "alert", "", "the Alert line; each brief is then a template this fills")
	flags.StringVar(&fixBrief, "fix-brief", "", "the fix lane's brief, or its template with --alert")
	flags.StringVar(&evidenceBrief, "evidence-brief", "", "the evidence lane's brief, or its template with --alert")
	flags.StringVar(&commsBrief, "comms-brief", "", "the comms lane's brief, or its template with --alert")
	flags.StringVar(&drive, "drive", "", "the cci drive the orca runner reads; default the drive bound to this session")
	flags.StringVar(&lane, "lane", "root", "the lane posting the launch lines")
	flags.Float64Var(&timeout, "timeout-seconds", DefaultTimeout.Seconds(), "how long to wait for the runner's verdicts")
	return cmd
}

func own(cmd *cobra.Command, a *app.App, id, owner string, interval time.Duration, once bool) error {
	s, err := defaultStore()
	if err != nil {
		return err
	}
	s, release := s.Held()
	defer release()
	if err := os.MkdirAll(s.Root, 0o750); err != nil {
		return err
	}
	lock, err := os.OpenFile(filepath.Join(s.Root, "."+id+".runner"), os.O_CREATE|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	defer lock.Close()
	if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		if errors.Is(err, syscall.EWOULDBLOCK) {
			return fmt.Errorf("%s already has a runner", id)
		}
		return err
	}
	header, err := s.Header(id)
	if err != nil {
		return err
	}
	if owner == "" {
		owner = sender(id)
	}
	if header.Owner != owner {
		return fmt.Errorf("%s is owned by %s at generation %d; run with --owner %s only as that owner", id, header.Owner, header.OwnerGeneration, header.Owner)
	}
	facts, err := s.Facts(id, runFacts...)
	if err != nil {
		return err
	}
	world, err := liveWorld(a, header, facts)
	if err != nil {
		return err
	}
	runner, err := NewRunner(s, id, world, a.Now, a.Log)
	if err != nil {
		return err
	}
	final, err := drive(cmd, runner, interval, once)
	if errors.Is(err, ErrStaleGeneration) {
		return fmt.Errorf("%w; this runner no longer owns the incident", err)
	}
	if err != nil {
		return err
	}
	fmt.Fprintf(cmd.OutOrStdout(), "%s %s\n", id, final.Status)
	return nil
}

func drive(cmd *cobra.Command, runner *Runner, interval time.Duration, once bool) (Header, error) {
	if !once {
		return runner.Run(cmd.Context(), interval)
	}
	if err := runner.Advance(cmd.Context()); err != nil {
		return Header{}, err
	}
	return runner.Store.Header(runner.ID)
}

func liveWorld(a *app.App, header Header, facts Facts) (World, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return World{}, err
	}
	claude, err := claudeDir()
	if err != nil {
		return World{}, err
	}
	opened, err := store.Parse(header.OpenedAt)
	if err != nil {
		return World{}, err
	}
	return World{
		Orca:      OrcaCLI{Runner: a.Runner, Lane: sender(header.ID), Run: facts.OrcaRun, Repo: facts.OrcaRepo, Launcher: filepath.Join(claude, "long-running", "current", "bin", "orca-launch.sh")},
		GitHub:    GitHubCLI{Runner: a.Runner, Repo: facts.Repo, Trunk: facts.Trunk},
		Buildkite: BuildkiteCLI{Runner: a.Runner, Pipeline: facts.Pipeline},
		Activation: ActivationCLI{
			Runner: a.Runner, Checkout: facts.Checkout, Pipeline: facts.Pipeline,
			Worktree: filepath.Join(home, ".claude", "worktrees", filepath.Base(facts.Checkout), "incident-"+header.ID+"-activation"),
		},
		Comms: CommsBus{
			Bus: CCI{}, Drive: facts.Bus, Sender: sender(header.ID), CommsLane: facts.CommsLane, RootLane: facts.RootLane,
			Topic: topic(header.ID), OpenedAt: opened,
		},
		Records: RecordsCLI{Runner: a.Runner, Repo: facts.Checkout},
	}, nil
}

func briefCommand() *cobra.Command {
	var id string
	var roles []string
	cmd := &cobra.Command{
		Use:   "brief",
		Short: "Write the lane briefs run would launch and print their paths",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			s, err := defaultStore()
			if err != nil {
				return err
			}
			facts, err := s.Facts(id, briefFields...)
			if err != nil {
				return err
			}
			if len(roles) == 0 {
				roles = adoptable
			}
			for _, role := range roles {
				if !slices.Contains(adoptable, role) {
					return fmt.Errorf("--role %q is not one of %s", role, strings.Join(adoptable, ", "))
				}
				path, err := writeBrief(id, facts, role, filepath.Join(s.Root, id))
				if err != nil {
					return err
				}
				fmt.Fprintln(cmd.OutOrStdout(), path)
			}
			return nil
		},
	}
	flags := cmd.Flags()
	flags.StringVar(&id, "incident", "", "the incident id")
	flags.StringArrayVar(&roles, "role", nil, "one lane's brief, fix or evidence; default both")
	required(cmd, "incident")
	return cmd
}
