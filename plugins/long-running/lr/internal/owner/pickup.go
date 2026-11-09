package owner

import (
	"cmp"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	pickupsDir   = "pickups"
	holdAfter    = 30 * time.Second
	holdingClock = 5 * time.Minute
	holdingWords = "Picked up: %s. You get the answer in this thread by %s."
	firstState   = "Reading your message…"
)

type Pickup struct {
	Channel string    `json:"channel"`
	TS      string    `json:"ts"`
	Thread  string    `json:"thread"`
	State   string    `json:"state"`
	At      time.Time `json:"at"`
}

func (p Pickup) thread() string {
	return cmp.Or(p.Thread, p.TS)
}

func (p Pickup) argv(state string) []string {
	argv := []string{"cc-slack", "pickup", "--channel", p.Channel, "--ts", p.TS, "--thread", p.thread()}
	if state != "" {
		argv = append(argv, "--state", state)
	}
	return argv
}

func PickupDir(entry drive.Entry) string {
	return filepath.Join(entry.StateDir, pickupsDir)
}

type desk struct {
	dir   string
	now   func() time.Time
	after func(time.Duration, func())
}

func (d desk) pickUp(ctx context.Context, runner shell.Runner, event slackEvent) error {
	p := Pickup{Channel: event.Channel, TS: event.TS, Thread: event.Thread, At: d.now()}
	if _, err := runner.Run(ctx, shell.Command{Argv: p.argv(""), Timeout: slackCall}); err != nil {
		return err
	}
	if d.dir == "" {
		return nil
	}
	if err := d.save(p); err != nil {
		return err
	}
	d.after(holdAfter, func() { d.hold(context.WithoutCancel(ctx), runner, p.TS) })
	return nil
}

func (d desk) hold(ctx context.Context, runner shell.Runner, ts string) {
	p, open, err := d.load(ts)
	if err != nil || !open {
		return
	}
	text := fmt.Sprintf(holdingWords, strings.TrimSuffix(cmp.Or(p.State, firstState), "…"), d.now().Add(holdingClock).In(pacific).Format(time.Kitchen))
	_, _ = runner.Run(ctx, shell.Command{Argv: []string{"cc-slack", "reply", "--channel", p.Channel, "--thread", p.thread(), "--text", text}, Timeout: slackCall})
}

func (d desk) path(ts string) string {
	return filepath.Join(d.dir, ts+".json")
}

func (d desk) save(p Pickup) error {
	if err := os.MkdirAll(d.dir, 0o750); err != nil {
		return err
	}
	data, err := json.Marshal(p)
	if err != nil {
		return err
	}
	return os.WriteFile(d.path(p.TS), data, 0o600)
}

func (d desk) load(ts string) (Pickup, bool, error) {
	data, err := os.ReadFile(d.path(ts))
	if errors.Is(err, fs.ErrNotExist) {
		return Pickup{}, false, nil
	}
	if err != nil {
		return Pickup{}, false, err
	}
	var p Pickup
	return p, true, json.Unmarshal(data, &p)
}

func openPickups(dir string) ([]Pickup, error) {
	files, err := filepath.Glob(filepath.Join(dir, "*.json"))
	if err != nil {
		return nil, err
	}
	open := make([]Pickup, 0, len(files))
	for _, file := range files {
		data, err := os.ReadFile(file)
		if errors.Is(err, fs.ErrNotExist) {
			continue
		}
		if err != nil {
			return nil, err
		}
		var p Pickup
		if err := json.Unmarshal(data, &p); err != nil {
			return nil, fmt.Errorf("pickup %s: %w", file, err)
		}
		open = append(open, p)
	}
	return open, nil
}

func Answered(dir, thread string) error {
	open, err := openPickups(dir)
	if err != nil {
		return err
	}
	for _, p := range open {
		if p.thread() != thread {
			continue
		}
		if err := os.Remove(desk{dir: dir}.path(p.TS)); err != nil && !errors.Is(err, fs.ErrNotExist) {
			return err
		}
	}
	return nil
}

func working(ctx context.Context, runner shell.Runner, dir, state string) error {
	open, err := openPickups(dir)
	if err != nil {
		return err
	}
	d := desk{dir: dir}
	for _, p := range open {
		if p.State == state {
			continue
		}
		if _, err := runner.Run(ctx, shell.Command{Argv: p.argv(state), Timeout: slackCall}); err != nil {
			return err
		}
		p.State = state
		if err := d.save(p); err != nil {
			return err
		}
	}
	return nil
}

func workingCommand(a *app.App, driveName *string) *cobra.Command {
	return &cobra.Command{
		Use:   "working STATE",
		Short: "Put STATE, such as \"Waiting for a subagent…\", on the Slack working line under each of his messages still waiting for an answer",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			entry, err := drive.Named(*driveName)
			if errors.Is(err, drive.ErrNoDrive) {
				return nil
			}
			if err != nil {
				return err
			}
			return working(cmd.Context(), a.Runner, PickupDir(entry), args[0])
		},
	}
}
