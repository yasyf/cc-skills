package desks

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"maps"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	communicator  = "owner-relay"
	laneEnv       = "CLAUDE_LONG_RUNNING_LANE"
	launchRunner  = "orca"
	landingRunner = "landing"
	runnerLane    = "desk-runner"
	watchdog      = "drive-watchdog"
	beatStale     = 5 * time.Minute
	beatWait      = 90 * time.Second
	beatPoll      = 2 * time.Second
)

type desk struct{ name, model, effort string }

type owing struct {
	desk
	lane, from string
	text       []byte
}

var (
	standing = []desk{
		{"landing-desk", "opus", "xhigh"},
		{"fidelity-desk", "opus", "xhigh"},
		{"red-sweep", "opus", "high"},
		{"alerts-desk", "sonnet", "low"},
		{watchdog, "opus", "xhigh"},
		{"board-refresher", "opus", "xhigh"},
	}
	placedLocal = regexp.MustCompile(`(?m)^ccx:(.* )?place=local(\s|$)`)
	up          = []store.LaneState{store.LaneLaunching, store.LaneDesired}
)

type drive struct {
	app    *app.App
	config config.Config
	state  string
}

func Command(a *app.App) *cobra.Command {
	cmd := &cobra.Command{Use: "desks", Short: "The drive's desks, which the coordinator owns and starts"}
	cmd.AddCommand(&cobra.Command{
		Use:   "start",
		Short: "Start every runner and standing desk that is not up: the landing runner and each desk remote, the launch runner from this Orca terminal",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return start(cmd.Context(), a, cmd.OutOrStdout())
		},
	})
	return cmd
}

func start(ctx context.Context, a *app.App, out io.Writer) error {
	lane, err := boundLane(ctx, a.Runner)
	if err != nil {
		return err
	}
	if lane == communicator {
		return fmt.Errorf("refused: the communicator (lane %s) starts no runner and no desk; the coordinator (lane %s) owns desk-runner, the launch runner and every desk. Post it to the coordinator: cci post --lane %s --kind ask --to %s", communicator, lanes.Root, communicator, lanes.Root)
	}
	d, err := loaded(a)
	if err != nil {
		return err
	}
	owed, err := d.owed(out)
	if err != nil {
		return err
	}
	if err := d.startLaunchRunner(ctx, out); err != nil {
		return err
	}
	if err := d.startLandingRunner(ctx, out); err != nil {
		return err
	}
	return d.launch(ctx, out, owed)
}

func loaded(a *app.App) (drive, error) {
	cfg, state, err := lanes.Loaded(a)
	return drive{app: a, config: cfg, state: state}, err
}

func boundLane(ctx context.Context, runner shell.Runner) (string, error) {
	if lane := os.Getenv(laneEnv); lane != "" {
		return lane, nil
	}
	me, err := runner.Run(ctx, shell.Command{Argv: []string{"cci", "me"}})
	if err != nil {
		return "", err
	}
	for word := range strings.FieldsSeq(me.Stdout) {
		if lane, found := strings.CutPrefix(word, "lane="); found {
			return lane, nil
		}
	}
	return "", nil
}

func (d drive) run(ctx context.Context, argv ...string) (string, error) {
	result, err := d.app.Runner.Run(ctx, shell.Command{Argv: argv})
	return result.Stdout, err
}

func (d drive) brief(lane string) string { return filepath.Join(d.state, lane, "brief.md") }

func (d drive) runnerConfig() string { return filepath.Join(d.state, "runner.json") }

func (d drive) owed(out io.Writer) ([]owing, error) {
	s, err := store.Open(filepath.Join(d.state, lanes.StoreFile))
	if err != nil {
		return nil, err
	}
	defer func() { _ = s.Close() }()
	rows, err := s.AllLanes()
	if err != nil {
		return nil, err
	}
	heard, err := s.Heard()
	if err != nil {
		return nil, err
	}
	posted := map[string]poster{}
	for name, at := range heard {
		canonical := lanes.Canonical(d.config.Drive, name)
		if at.After(posted[canonical].at) {
			posted[canonical] = poster{name, at}
		}
	}
	known := map[string]store.Lane{}
	for _, row := range rows {
		row.Name = lanes.Canonical(d.config.Drive, row.Name)
		known[row.Name] = row
	}
	for name := range posted {
		if _, registered := known[name]; !registered {
			known[name] = store.Lane{Name: name}
		}
	}
	taken := map[string]bool{}
	for name := range known {
		taken[name] = true
	}
	var owed []owing
	var faults []error
	for _, standingDesk := range standing {
		next := owing{desk: standingDesk, lane: standingDesk.name, from: d.brief(standingDesk.name)}
		if row, found, live := d.holder(known, posted, standingDesk.name); found {
			if live {
				seq, err := s.NewestSeq(posted[row.Name].name)
				if err != nil {
					return nil, err
				}
				newest := "no record yet"
				if seq != 0 {
					newest = fmt.Sprintf("newest record #%d", seq)
				}
				fmt.Fprintf(out, "%s is up as %s, %s; launching another takes `lr request stand-down %s --successor <lane>` first\n", standingDesk.name, row.Name, newest, row.Name)
				continue
			}
			next.lane, next.from = lanes.SuccessorName(row.Name, taken), row.Brief
			taken[next.lane] = true
			fmt.Fprintf(out, "%s is %s as %s, so %s launches from its brief %s\n", standingDesk.name, row.State, row.Name, next.lane, row.Brief)
		}
		text, err := os.ReadFile(next.from)
		switch {
		case err != nil:
			faults = append(faults, fmt.Errorf("%s has no brief to launch: %w", standingDesk.name, err))
		case placedLocal.Match(text):
			faults = append(faults, fmt.Errorf("%s: %s says place=local, and every desk runs remote", standingDesk.name, next.from))
		default:
			next.text = text
			owed = append(owed, next)
		}
	}
	return owed, errors.Join(faults...)
}

type poster struct {
	name string
	at   time.Time
}

func (d drive) holder(known map[string]store.Lane, posted map[string]poster, role string) (row store.Lane, found, live bool) {
	numbered := regexp.MustCompile(`^` + regexp.QuoteMeta(role) + `(?:-(\d+))?$`)
	newestNumber := 0
	for _, name := range slices.Sorted(maps.Keys(known)) {
		match := numbered.FindStringSubmatch(name)
		if match == nil {
			continue
		}
		number := 1
		if match[1] != "" {
			number, _ = strconv.Atoi(match[1])
		}
		lane, at := known[name], posted[name].at
		laneLive := slices.Contains(up, lane.State) || at.After(lane.Since) && d.app.Now().Sub(at) < lanes.StaleAfter
		if lane.State == "" && !laneLive {
			continue
		}
		if !found || laneLive && !live || laneLive == live && number > newestNumber {
			row, found, live, newestNumber = lane, true, laneLive, number
		}
	}
	return row, found, live
}

func (d drive) beating(ctx context.Context, runner string) (bool, error) {
	newest, err := d.run(ctx, "cci", "tail", "--drive", d.config.Drive, "--since", "0", "--json", "--lane", runnerLane, "--kind=state", "--topic="+runner, "--to", watchdog+"@"+d.config.Drive, "--limit", "1")
	if err != nil || strings.TrimSpace(newest) == "" {
		return false, err
	}
	var record struct {
		At time.Time `json:"at"`
	}
	if err := json.Unmarshal([]byte(newest), &record); err != nil {
		return false, fmt.Errorf("the %s runner's alive record: %w", runner, err)
	}
	return d.app.Now().Sub(record.At) < beatStale, nil
}

func (d drive) startLaunchRunner(ctx context.Context, out io.Writer) error {
	if _, err := d.run(ctx, "desk-runner.py", "rebind", "--config", d.runnerConfig()); err != nil {
		return err
	}
	alive, err := d.beating(ctx, launchRunner)
	if err != nil {
		return err
	}
	if alive {
		fmt.Fprintf(out, "%s runner is up\n", launchRunner)
		return nil
	}
	log := filepath.Join(d.state, launchRunner+"-runner.log")
	if _, err := d.app.Runner.Run(ctx, shell.Command{Argv: []string{"sh", "-c", `nohup "$@" >>"$0" 2>&1 </dev/null &`, log, "desk-runner.py", "run", "--config", d.runnerConfig(), "--desk", launchRunner}, Env: lanes.Identity(runnerLane)}); err != nil {
		return err
	}
	deadline := d.app.Now().Add(beatWait)
	for {
		alive, err := d.beating(ctx, launchRunner)
		if err != nil {
			return err
		}
		if alive {
			fmt.Fprintf(out, "%s runner started from this Orca terminal, on the Mac, since it drives the Mac's Orca app\n", launchRunner)
			return nil
		}
		if d.app.Now().After(deadline) {
			return fmt.Errorf("the %s runner posted no alive record within %s, so no desk was launched; read %s", launchRunner, beatWait, log)
		}
		time.Sleep(beatPoll)
	}
}

func (d drive) startLandingRunner(ctx context.Context, out io.Writer) error {
	alive, err := d.beating(ctx, landingRunner)
	if err != nil {
		return err
	}
	if alive {
		fmt.Fprintf(out, "%s runner is up\n", landingRunner)
		return nil
	}
	installed, err := d.run(ctx, "desk-creds.py", "install", "--config", d.runnerConfig(), "--desk", landingRunner)
	fmt.Fprint(out, installed)
	return err
}

func (d drive) launch(ctx context.Context, out io.Writer, owed []owing) error {
	verdicts := make([]string, len(owed))
	faults := make([]error, len(owed))
	var launches sync.WaitGroup
	for i, next := range owed {
		launches.Go(func() {
			verdict, err := d.launchOne(ctx, next)
			verdicts[i] = verdict
			if err != nil {
				faults[i] = fmt.Errorf("%s: %w", next.lane, err)
			}
		})
	}
	launches.Wait()
	for _, verdict := range verdicts {
		fmt.Fprint(out, verdict)
	}
	return errors.Join(faults...)
}

func (d drive) launchOne(ctx context.Context, next owing) (string, error) {
	brief := d.brief(next.lane)
	if next.from != brief {
		if err := os.MkdirAll(filepath.Dir(brief), 0o750); err != nil {
			return "", err
		}
		if err := os.WriteFile(brief, next.text, 0o600); err != nil {
			return "", err
		}
	}
	return d.run(ctx, "desk-launch.py", "lane", next.lane, next.model, next.effort, brief, "--drive", d.config.Drive, "--lane", lanes.Root)
}
