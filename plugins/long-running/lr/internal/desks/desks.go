package desks

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"sync"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const communicator = "owner-relay"

type owing struct {
	lane, model, effort string
	text                []byte
}

type registry interface {
	AllLanes() ([]store.Lane, error)
	Heard() (map[string]time.Time, error)
}

var placedLocal = regexp.MustCompile(`(?m)^ccx:(.* )?place=local(\s|$)`)

type drive struct {
	app    *app.App
	config config.Config
	state  string
}

func Command(a *app.App) *cobra.Command {
	cmd := &cobra.Command{Use: "desks", Short: "The drive's desks, which the coordinator owns and starts"}
	cmd.AddCommand(&cobra.Command{
		Use:   "start",
		Short: "Take the drive's Orca Run for this terminal and relaunch every standing desk in the registry whose row is down",
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
	s, err := store.Open(filepath.Join(d.state, lanes.StoreFile))
	if err != nil {
		return err
	}
	defer func() { _ = s.Close() }()
	owed, faults, err := d.owed(s, out)
	if err != nil {
		return err
	}
	if _, err := d.run(ctx, "lr", "relay", "rebind", "--config", a.ConfigPath); err != nil {
		return err
	}
	return errors.Join(d.launch(ctx, out, owed), faults)
}

func loaded(a *app.App) (drive, error) {
	cfg, state, err := lanes.Loaded(a)
	return drive{app: a, config: cfg, state: state}, err
}

func (d drive) run(ctx context.Context, argv ...string) (string, error) {
	result, err := d.app.Runner.Run(ctx, shell.Command{Argv: argv})
	return result.Stdout, err
}

func (d drive) owed(s registry, out io.Writer) (owed []owing, faults error, err error) {
	rows, err := s.AllLanes()
	if err != nil {
		return nil, nil, err
	}
	heard, err := s.Heard()
	if err != nil {
		return nil, nil, err
	}
	taken := map[string]bool{}
	for name := range heard {
		taken[lanes.Canonical(d.config.Drive, name)] = true
	}
	for _, row := range rows {
		taken[lanes.Canonical(d.config.Drive, row.Name)] = true
	}
	desks, err := lanes.StandingDesks(d.config.Drive, rows, d.app.Now())
	if err != nil {
		return nil, nil, err
	}
	var faulted []error
	for _, desk := range desks {
		row := desk.Lane
		if desk.Live {
			fmt.Fprintf(out, "%s is up as %s; launching another takes `lr request stand-down %s --successor <lane> --open '<items>'` first\n", desk.Stem, row.Name, row.Name)
			continue
		}
		next := owing{lane: lanes.SuccessorName(row.Name, taken), model: desk.Cost.Model, effort: desk.Cost.Effort}
		taken[next.lane] = true
		fmt.Fprintf(out, "%s is %s as %s, so %s launches from its brief %s\n", desk.Stem, row.State, row.Name, next.lane, row.Brief)
		text, err := os.ReadFile(row.Brief)
		if err != nil {
			faulted = append(faulted, fmt.Errorf("%s has no brief to launch: %w", desk.Stem, err))
			continue
		}
		next.text = lanes.WithDuty(text, desk.Duty)
		switch need := lanes.MacDeskNeed(next.lane); {
		case need != "" && !lanes.DeskOnMac(next.text):
			next.text = append([]byte("ccx: place=local why="+need+"\n"), next.text...)
		case need == "" && placedLocal.Match(next.text) && !lanes.DeskOnMac(next.text):
			next.text = append([]byte("ccx: place=remote\n"), next.text...)
		}
		owed = append(owed, next)
	}
	return owed, errors.Join(faulted...), nil
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
	brief := filepath.Join(d.state, next.lane, "brief.md")
	if err := os.MkdirAll(filepath.Dir(brief), 0o750); err != nil {
		return "", err
	}
	if err := os.WriteFile(brief, next.text, 0o600); err != nil {
		return "", err
	}
	return d.run(ctx, "desk-launch.py", "lane", next.lane, next.model, next.effort, brief, "--drive", d.config.Drive, "--lane", lanes.Root)
}
