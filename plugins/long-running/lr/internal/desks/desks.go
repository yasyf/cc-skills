package desks

import (
	"context"
	"errors"
	"fmt"
	"io"
	"maps"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
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
	communicator = "owner-relay"
	watchdog     = "drive-watchdog"
)

type desk struct{ name, model, effort string }

type owing struct {
	desk
	lane, from string
	text       []byte
}

var (
	standing = []desk{
		tabled("fidelity-desk"),
		tabled("red-sweep"),
		tabled("alerts-desk"),
		{watchdog, "opus", "high"},
		tabled("board-refresher"),
	}
	placedLocal = regexp.MustCompile(`(?m)^ccx:(.* )?place=local(\s|$)`)
	up          = []store.LaneState{store.LaneLaunching, store.LaneDesired}
)

func tabled(name string) desk {
	cost := lanes.DeskCosts[name]
	return desk{name, cost.Model, cost.Effort}
}

type drive struct {
	app    *app.App
	config config.Config
	state  string
}

func Command(a *app.App) *cobra.Command {
	cmd := &cobra.Command{Use: "desks", Short: "The drive's desks, which the coordinator owns and starts"}
	cmd.AddCommand(&cobra.Command{
		Use:   "start",
		Short: "Take the drive's Orca Run for this terminal and start every standing desk that is not up, each one remote",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return start(cmd.Context(), a, cmd.OutOrStdout())
		},
	})
	return cmd
}

func start(ctx context.Context, a *app.App, out io.Writer) error {
	lane, err := lanes.BoundLane(ctx, a.Runner)
	if err != nil {
		return err
	}
	if lane == communicator {
		return fmt.Errorf("refused: the communicator (lane %s) starts no runner and no desk; the coordinator (lane %s) owns every desk. Post it to the coordinator: cci post --lane %s --kind ask --to %s", communicator, lanes.Root, communicator, lanes.Root)
	}
	d, err := loaded(a)
	if err != nil {
		return err
	}
	owed, err := d.owed(out)
	if err != nil {
		return err
	}
	if _, err := d.run(ctx, "lr", "relay", "rebind", "--config", a.ConfigPath); err != nil {
		return err
	}
	return d.launch(ctx, out, owed)
}

func loaded(a *app.App) (drive, error) {
	cfg, state, err := lanes.Loaded(a)
	return drive{app: a, config: cfg, state: state}, err
}

func (d drive) run(ctx context.Context, argv ...string) (string, error) {
	result, err := d.app.Runner.Run(ctx, shell.Command{Argv: argv})
	return result.Stdout, err
}

func (d drive) brief(lane string) string { return filepath.Join(d.state, lane, "brief.md") }

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
				fmt.Fprintf(out, "%s is up as %s, %s; launching another takes `lr request stand-down %s --successor <lane> --open '<items>'` first\n", standingDesk.name, row.Name, newest, row.Name)
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
