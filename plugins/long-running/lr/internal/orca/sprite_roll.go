package orca

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"maps"
	"os"
	"os/signal"
	"os/user"
	"path/filepath"
	"slices"
	"strings"
	"syscall"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/awake"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/wait"
)

const spriteInstallTimeout = 5 * time.Minute

type runWorkers interface {
	Workers(ctx context.Context, run string) ([]Worker, error)
}

type hostedTasks interface {
	Sprites() (map[SpriteKey]Sprite, error)
}

type SpriteRoll struct {
	Runner shell.Runner
	Orca   runWorkers
	Fleet  hostedTasks
	Store  *store.Store
	Host   string
	Now    func() time.Time
	Out    io.Writer
}

func RollHost(ctx context.Context, runner shell.Runner, s *store.Store, path string, now func() time.Time, out io.Writer) error {
	file, err := readRunnerFile(path)
	if err != nil || file.Host.Sprite == "" {
		return err
	}
	env, err := runnerEnv(path)
	if err != nil {
		return err
	}
	roll := SpriteRoll{Runner: runner, Store: s, Now: now, Out: out}
	pins, err := roll.pins(env)
	if err != nil {
		return err
	}
	return roll.install(ctx, pins, []string{file.Host.Sprite}, file.Host.Sprite)
}

func (r SpriteRoll) pins(env func(string) string) (spritePins, error) {
	pins, err := readPins(env)
	if err != nil {
		return pins, err
	}
	for _, tool := range pins.tools {
		why, err := r.Store.BadTool(tool.Name, tool.Version)
		if err != nil {
			return pins, err
		}
		if why != "" {
			return pins, refuse("nothing rolled: %s %s is marked bad, since %s; pin another version", tool.Name, tool.Version, why)
		}
	}
	return pins, nil
}

func (r SpriteRoll) Run(ctx context.Context, run string, env func(string) string, probe string, probeOnly bool) error {
	pins, err := r.pins(env)
	if err != nil {
		return err
	}
	workers, err := r.Orca.Workers(ctx, run)
	if err != nil {
		return err
	}
	hosted, err := r.Fleet.Sprites()
	if err != nil {
		return err
	}
	var boxes []string
	for _, worker := range workers {
		sprite, onSprite := hosted[SpriteKey{worker.Workspace, worker.Terminal}]
		if !worker.Settled() && onSprite && !slices.Contains(boxes, sprite.Workspace) {
			boxes = append(boxes, sprite.Workspace)
		}
	}
	if r.Host != "" && !slices.Contains(boxes, r.Host) {
		boxes = append(boxes, r.Host)
	}
	slices.Sort(boxes)
	if probe != "" {
		at := slices.Index(boxes, probe)
		if at < 0 {
			return refuse("nothing rolled: %s hosts no live worker of Run %s", probe, run)
		}
		boxes = slices.Concat([]string{probe}, slices.Delete(boxes, at, at+1))
		if probeOnly {
			boxes = boxes[:1]
		}
	}
	return r.install(ctx, pins, boxes, probe)
}

func (r SpriteRoll) install(ctx context.Context, pins spritePins, boxes []string, probe string) error {
	var unreached []string
	for done, box := range boxes {
		result, err := r.Runner.Run(ctx, shell.Command{Argv: pins.installArgv(box, nil), Timeout: spriteInstallTimeout})
		installed := installedTools(result.Stdout)
		if recorded := r.Store.RecordSpriteTools(box, installed, r.Now()); recorded != nil {
			return recorded
		}
		if err != nil && len(installed) == 0 && spriteUnreached(result) {
			if box == probe {
				return refuse("lr sprites roll: could not reach the probe %s, so %d more Sprites keep what they run: %s", box, len(boxes)-done-1, lastSaid(result))
			}
			fmt.Fprintf(r.Out, "lr sprites roll: could not reach %s: %s\n", box, lastSaid(result))
			unreached = append(unreached, box)
			continue
		}
		if err != nil {
			return r.failed(box, pins, installed, result, len(boxes)-done-1)
		}
		fmt.Fprintf(r.Out, "lr sprites roll: %s runs %s\n", box, censusOf(installed))
	}
	if len(unreached) > 0 {
		fmt.Fprintf(r.Out, "lr sprites roll: never reached: %s\n", strings.Join(unreached, " "))
	}
	return nil
}

func spriteUnreached(result shell.Result) bool {
	said := result.Stdout + result.Stderr
	return result.Code == shell.CodeTimedOut || strings.Contains(said, "failed to start sprite command") || strings.Contains(said, "failed to fetch sprite version")
}

func lastSaid(result shell.Result) string {
	said := strings.TrimSpace(result.Stdout + result.Stderr)
	return said[strings.LastIndex(said, "\n")+1:]
}

func (r SpriteRoll) failed(box string, pins spritePins, installed map[string]string, result shell.Result, left int) error {
	said := lastSaid(result)
	failed := slices.IndexFunc(pins.tools, func(tool spriteTool) bool { return installed[tool.Name] != tool.Version })
	if installed["cci"] == "" || installed["lr"] == "" || failed < 0 {
		return refuse("lr sprites roll: the cci or lr install failed on %s, so %d more Sprites keep what they run: %s", box, left, said)
	}
	tool := pins.tools[failed]
	if err := r.Store.MarkBadTool(tool.Name, tool.Version, fmt.Sprintf("its install failed on %s: %s", box, said), r.Now()); err != nil {
		return err
	}
	return refuse("lr sprites roll: %s %s failed on %s and is marked bad, so %d more Sprites keep what they run: %s", tool.Name, tool.Version, box, left, said)
}

func censusOf(installed map[string]string) string {
	words := make([]string, 0, len(installed))
	for _, name := range slices.Sorted(maps.Keys(installed)) {
		words = append(words, name+"="+installed[name])
	}
	return strings.Join(words, " ")
}

func writeCensus(out io.Writer, tools []store.SpriteTool) {
	if len(tools) == 0 {
		fmt.Fprintln(out, "lr sprites: no launch or roll has recorded a Sprite's tools")
	}
	for i := 0; i < len(tools); {
		box, installed, at := tools[i].Sprite, map[string]string{}, tools[i].At
		for ; i < len(tools) && tools[i].Sprite == box; i++ {
			installed[tools[i].Name] = tools[i].Version
			if tools[i].At.After(at) {
				at = tools[i].At
			}
		}
		fmt.Fprintf(out, "%s %s at %s\n", box, censusOf(installed), store.Stamp(at))
	}
}

func refusalExit(cmd *cobra.Command, err error) error {
	var failed *Failed
	if errors.As(err, &failed) {
		fmt.Fprintln(cmd.ErrOrStderr(), failed.Line)
		return &wait.ExitError{Code: failedExit}
	}
	return err
}

func runnerEnv(path string) (func(string) string, error) {
	file, err := readRunnerFile(path)
	if err != nil {
		return nil, err
	}
	if file.Orca.Sprite == nil {
		return nil, errors.New(path + " has no orca.sprite to roll")
	}
	tools, err := json.Marshal(file.Orca.Sprite.Tools)
	if err != nil {
		return nil, err
	}
	return func(name string) string {
		switch name {
		case "WORKER_TOOLS":
			return string(tools)
		case "BINRUN_PLUGIN_ROOT":
			return os.Getenv(name)
		}
		return file.Orca.Sprite.Env[name]
	}, nil
}

func SpritesCommand(a *app.App) *cobra.Command {
	opened := func() (*store.Store, string, string, error) {
		cfg, state, err := lanes.Loaded(a)
		if err != nil {
			return nil, "", "", err
		}
		s, err := store.Open(filepath.Join(state, lanes.StoreFile))
		return s, cfg.OrcaRun, state, err
	}
	cmd := &cobra.Command{
		Use:   "sprites",
		Short: "Print what each Sprite runs, one line per Sprite, from the versions its launches and rolls installed",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			s, _, _, err := opened()
			if err != nil {
				return err
			}
			defer func() { _ = s.Close() }()
			tools, err := s.SpriteTools()
			if err != nil {
				return err
			}
			writeCensus(cmd.OutOrStdout(), tools)
			return nil
		},
	}
	var probe string
	var probeOnly bool
	roll := &cobra.Command{
		Use:   "roll",
		Short: "Install runner.json's Sprite pins on the desk host and every Sprite with a live worker in the drive's Run, the probe Sprite first; a pin that fails is marked bad, stops the roll, and is never rolled again",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			s, run, state, err := opened()
			if err != nil {
				return err
			}
			defer func() { _ = s.Close() }()
			env, err := runnerEnv(filepath.Join(state, "runner.json"))
			if err != nil {
				return err
			}
			file, err := readRunnerFile(filepath.Join(state, "runner.json"))
			if err != nil {
				return err
			}
			home, err := stateHome()
			if err != nil {
				return err
			}
			roller := SpriteRoll{Runner: a.Runner, Orca: Client{Runner: a.Runner}, Fleet: CCRemote{Runner: a.Runner, State: filepath.Join(home, "cc-remote")}, Store: s, Host: file.Host.Sprite, Now: a.Now, Out: cmd.OutOrStdout()}
			if probeOnly && probe == "" {
				return errors.New("--probe-only needs --probe, so it names the one Sprite to install on")
			}
			return refusalExit(cmd, roller.Run(cmd.Context(), run, env, probe, probeOnly))
		},
	}
	roll.Flags().StringVar(&probe, "probe", "", "the Sprite to install on first; default the first by name")
	roll.Flags().BoolVar(&probeOnly, "probe-only", false, "install on the --probe Sprite and stop, so a person can check it before the rest")
	pin := &cobra.Command{
		Use:   "pin",
		Short: "Move each orca.sprite.tools pin to the version this Mac's plugins run, once its release asset matches its release's checksum",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			s, _, state, err := opened()
			if err != nil {
				return err
			}
			defer func() { _ = s.Close() }()
			claude, err := drive.ConfigDir()
			if err != nil {
				return err
			}
			return refusalExit(cmd, MoveSpritePins(cmd.Context(), s, claude, filepath.Join(state, "runner.json"), cmd.OutOrStdout()))
		},
	}
	hold := &cobra.Command{
		Use:   "hold",
		Short: "Run on a Sprite as its service: keep the Sprite awake while a claude or codex runs on it, so the hold lapses within 5 minutes of the agent's exit, and append a memory sample to ~/.cc-remote/samples.log every 10 seconds",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			ctx, stop := signal.NotifyContext(cmd.Context(), syscall.SIGTERM, os.Interrupt)
			defer stop()
			log := slog.New(slog.NewTextHandler(cmd.ErrOrStderr(), nil))
			home, err := user.Current()
			if err != nil {
				log.Warn("no memory samples will be written", "err", err)
			} else {
				go awake.Sampler{Proc: "/proc", Events: "/sys/fs/cgroup/memory.events", Path: filepath.Join(home.HomeDir, awake.SampleFile), Every: awake.SampleEvery, Now: a.Now, Log: log}.Run(ctx)
			}
			awake.Holder{Runner: a.Runner, Task: workerAwake, Every: awake.Every, Log: log, Wanted: awake.WorkerAlive(a.Runner)}.Run(ctx)
			return nil
		},
	}
	cmd.AddCommand(roll, pin, hold)
	return cmd
}
