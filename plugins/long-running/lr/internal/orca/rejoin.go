package orca

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	joinedMesh  = "joined mesh"
	noMesh      = "no relay mesh"
	relayStatus = "~/.local/bin/cci relay status 2>&1"
)

type Rejoin struct {
	Runner shell.Runner
	Store  *store.Store
	Config string
	Sleep  func(time.Duration)
	Out    io.Writer
}

func (r Rejoin) Pass(ctx context.Context) error {
	file, err := readRunnerFile(r.Config)
	if err != nil {
		return err
	}
	if file.Orca.Sprite == nil || file.Orca.Sprite.Env[sponsorEnv] == "" {
		return nil
	}
	wanted, err := r.Store.Lanes(store.LaneLaunching, store.LaneDesired)
	if err != nil {
		return err
	}
	for _, lane := range wanted {
		if lane.Placement != routeSprite {
			continue
		}
		if err := r.repair(ctx, lane, file.Orca.Sprite.Env[sponsorEnv]); err != nil {
			return err
		}
	}
	return nil
}

func (r Rejoin) repair(ctx context.Context, lane store.Lane, sponsor string) error {
	attempt := attemptOf(lane.Brief)
	said, err := os.ReadFile(filepath.Join(attempt, joinReceipt))
	if errors.Is(err, os.ErrNotExist) || strings.Contains(string(said), joinedMesh) {
		return nil
	}
	if err != nil {
		return err
	}
	raw, err := os.ReadFile(filepath.Join(attempt, spriteMade))
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	var allocation struct {
		Workspace string `json:"workspace"`
	}
	if err := json.Unmarshal(raw, &allocation); err != nil {
		return fmt.Errorf("%s: %w", filepath.Join(attempt, spriteMade), err)
	}
	w := &workerLaunch{WorkerStarter: &WorkerStarter{Runner: r.Runner, Sleep: r.Sleep}, ctx: ctx, attempt: attempt}
	status, errs, code := w.capture("sprite", "exec", "-s", allocation.Workspace, "--no-stdin", "--", "sh", "-c", relayStatus)
	if code == 0 {
		return w.receipt(joinReceipt, joinedMesh+" before this pass: "+status)
	}
	if !strings.Contains(status+errs, noMesh) {
		fmt.Fprintf(r.Out, "%s: Sprite %s did not answer: %s\n", lane.Name, allocation.Workspace, strings.TrimSpace(status+errs))
		return nil
	}
	log, joinErr := w.join(allocation.Workspace, sponsor)
	if err := w.receipt(joinReceipt, log); err != nil {
		return err
	}
	if joinErr != nil {
		return fmt.Errorf("%s on Sprite %s did not rejoin the cci relay, so this pass stops here: %w", lane.Name, allocation.Workspace, joinErr)
	}
	fmt.Fprintf(r.Out, "%s rejoined on Sprite %s\n", lane.Name, allocation.Workspace)
	return nil
}

func RejoinCommand(a *app.App) *cobra.Command {
	return &cobra.Command{
		Use:   "rejoin",
		Short: "Join every live Sprite lane with a failed join receipt to the cci relay, stopping at the first refusal",
		Long: `Join every live Sprite lane with a failed join receipt to the cci relay, stopping at the first refusal.

It reads each live lane's relay-join.log, so a pass runs nothing on a Sprite whose receipt shows a join. A lane whose Sprite already holds a mesh gets a fresh receipt. The sponsor's service key moves from the sponsor Sprite to the stranded one over stdin, as a launch moves it, and is never written or printed. The supervisor runs this pass every five minutes.`,
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			_, state, err := lanes.Loaded(a)
			if err != nil {
				return err
			}
			s, err := store.Open(filepath.Join(state, lanes.StoreFile))
			if err != nil {
				return err
			}
			defer func() { _ = s.Close() }()
			return Rejoin{Runner: a.Runner, Store: s, Config: filepath.Join(state, "runner.json"), Sleep: time.Sleep, Out: cmd.OutOrStdout()}.Pass(cmd.Context())
		},
	}
}
