package grading

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	lrfacts "github.com/yasyf/cc-skills/plugins/long-running/lr/internal/facts"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

var regradeTarget = regexp.MustCompile(`^([\w.-]+/[\w.-]+)#(\d+)@([0-9a-f]{7,40})$`)

func regradeCommand(a *app.App) *cobra.Command {
	var driveName string
	var report []string
	cmd := &cobra.Command{
		Use:   "regrade OWNER/NAME#N@HEAD",
		Short: "Run one more fidelity pass over a head a grade already names; a PR no longer open reports only to --report",
		Args:  cobra.ExactArgs(1),
		RunE: func(c *cobra.Command, args []string) error {
			target := regradeTarget.FindStringSubmatch(args[0])
			if target == nil {
				return fmt.Errorf("%q is not OWNER/NAME#N@HEAD", args[0])
			}
			number, err := strconv.Atoi(target[2])
			if err != nil {
				return err
			}
			entry, err := drive.Named(driveName)
			if err != nil {
				return err
			}
			cfg, err := config.Load(filepath.Join(entry.StateDir, driveConfig), c.ErrOrStderr())
			if err != nil {
				return err
			}
			s, err := store.Open(entry.Store())
			if err != nil {
				return err
			}
			defer func() { _ = s.Close() }()
			source, err := filepath.EvalSymlinks(filepath.Join(entry.StateDir, runnerConfig))
			if err != nil {
				return err
			}
			pool, err := NewLivePool(a, source, s)
			if err != nil {
				return err
			}
			pushed, err := lrfacts.New(a, cfg, s).AtPushedHeads(c.Context())
			if err != nil {
				return err
			}
			facts, audience, err := regradeFacts(target[1], number, target[3], pushed[number].Facts, report)
			if err != nil {
				return err
			}
			name, err := pool.Regrade(c.Context(), facts, audience)
			if errors.Is(err, ErrAnswered) {
				return fmt.Errorf("%s: its regrade already settled", args[0])
			}
			if err != nil {
				return err
			}
			if err := s.Heartbeat(store.Worker{Name: name, Heartbeat: a.Now()}); err != nil {
				return err
			}
			fmt.Fprintln(c.OutOrStdout(), name)
			return nil
		},
	}
	cmd.Flags().StringVar(&driveName, "drive", os.Getenv("LR_DRIVE"), "the drive's bus name")
	cmd.Flags().StringSliceVar(&report, "report", nil, "the lanes a grade of a PR no longer open goes to, in place of its lane and desks")
	return cmd
}

func regradeFacts(repo string, number int, head string, open machine.Facts, report []string) (machine.Facts, []string, error) {
	if open.Repo != repo {
		if len(report) == 0 {
			return machine.Facts{}, nil, fmt.Errorf("%s#%d is not open, so its grade reports only: name --report", repo, number)
		}
		return machine.Facts{Repo: repo, Number: number, Head: head}, report, nil
	}
	if !strings.HasPrefix(open.Head, head) {
		return machine.Facts{}, nil, fmt.Errorf("%s#%d moved to %s; its next pass grades that head", repo, number, short(open.Head))
	}
	return open, nil, nil
}
