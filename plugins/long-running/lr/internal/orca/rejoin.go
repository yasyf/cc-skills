package orca

import (
	"bytes"
	"context"
	"encoding/base64"
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
	renewWithin = 12 * time.Hour
)

type Rejoin struct {
	Runner shell.Runner
	Store  *store.Store
	Config string
	Sleep  func(time.Duration)
	Now    func() time.Time
	Out    io.Writer
}

func (r Rejoin) Pass(ctx context.Context) error {
	file, err := readRunnerFile(r.Config)
	if err != nil {
		return err
	}
	if file.Orca.Sprite == nil {
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
		if err := r.repair(ctx, lane); err != nil {
			return err
		}
	}
	return nil
}

func (r Rejoin) repair(ctx context.Context, lane store.Lane) error {
	attempt := attemptOf(lane.Brief)
	said, err := os.ReadFile(filepath.Join(attempt, joinReceipt))
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	joined := strings.Contains(string(said), joinedMesh)
	if joined {
		renewed, err := r.renew(ctx, lane, attempt)
		if err != nil || renewed {
			return err
		}
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
	if !joined {
		status, errs, code := w.capture("sprite", "exec", "-s", allocation.Workspace, "--no-stdin", "--", "sh", "-c", relayStatus)
		if code == 0 {
			return w.receipt(joinReceipt, joinedMesh+" before this pass: "+status)
		}
		if !strings.Contains(status+errs, noMesh) {
			fmt.Fprintf(r.Out, "%s: Sprite %s did not answer: %s\n", lane.Name, allocation.Workspace, strings.TrimSpace(status+errs))
			return nil
		}
	}
	log, joinErr := w.join(allocation.Workspace)
	if joined && joinErr != nil {
		fmt.Fprintf(r.Out, "%s on Sprite %s stays on the shared service key: its move to a delegation of its own failed: %v\n", lane.Name, allocation.Workspace, joinErr)
		return nil
	}
	if err := w.receipt(joinReceipt, log); err != nil {
		return err
	}
	if joinErr != nil {
		return fmt.Errorf("%s on Sprite %s did not rejoin the cci relay, so this pass stops here: %w", lane.Name, allocation.Workspace, joinErr)
	}
	fmt.Fprintf(r.Out, "%s rejoined on Sprite %s\n", lane.Name, allocation.Workspace)
	return nil
}

func (r Rejoin) renew(ctx context.Context, lane store.Lane, attempt string) (bool, error) {
	raw, err := os.ReadFile(filepath.Join(attempt, delegationFile))
	if errors.Is(err, os.ErrNotExist) {
		return false, nil
	}
	if err != nil {
		return true, err
	}
	var held struct {
		InstancePub []byte    `json:"instance_pub"`
		Drives      []string  `json:"drives"`
		Expires     time.Time `json:"expires"`
	}
	if err := json.Unmarshal(raw, &held); err != nil {
		return true, fmt.Errorf("%s: %w", filepath.Join(attempt, delegationFile), err)
	}
	if held.Expires.Sub(r.Now()) > renewWithin {
		return true, nil
	}
	w := &workerLaunch{WorkerStarter: &WorkerStarter{Runner: r.Runner, Sleep: r.Sleep}, ctx: ctx, attempt: attempt}
	var log bytes.Buffer
	if _, err := w.delegate(base64.StdEncoding.EncodeToString(held.InstancePub), held.Drives, &log); err != nil {
		return true, fmt.Errorf("%s's delegation, expiring %s, did not renew, so this pass stops here: %w: %s", lane.Name, held.Expires.Format(time.RFC3339), err, strings.TrimSpace(log.String()))
	}
	fmt.Fprintf(r.Out, "%s's delegation renewed for %s\n", lane.Name, delegationLife)
	return true, nil
}

func RejoinCommand(a *app.App) *cobra.Command {
	return &cobra.Command{
		Use:   "rejoin",
		Short: "Join every live Sprite lane with a failed join receipt to the cci relay, stopping at the first refusal",
		Long: `Join every live Sprite lane with a failed join receipt to the cci relay, stopping at the first refusal.

It reads each live lane's relay-join.log and relay-delegation.json, so a pass runs nothing on a Sprite whose receipt shows a join and whose delegation has more than 12 hours left. A joined lane whose delegation has less renews it with one cci relay delegate on this Mac, which signs the Sprite's key a fresh 24-hour delegation and registers it with the relay; the Sprite adopts it from its next pull, and nothing runs on it. A joined lane with no delegation file joined on the shared service key, so the pass joins it again on a delegation of its own, which drops the key. A lane whose Sprite already holds a mesh gets a fresh receipt. No Sprite ever holds or passes on a service key. The supervisor runs this pass every five minutes.`,
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
			return Rejoin{Runner: a.Runner, Store: s, Config: filepath.Join(state, "runner.json"), Sleep: time.Sleep, Now: time.Now, Out: cmd.OutOrStdout()}.Pass(cmd.Context())
		},
	}
}
