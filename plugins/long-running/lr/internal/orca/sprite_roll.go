package orca

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"maps"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
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
	Now    func() time.Time
	Out    io.Writer
}

func (r SpriteRoll) Run(ctx context.Context, run string, env func(string) string, probe string) error {
	pins, err := readPins(env)
	if err != nil {
		return err
	}
	for _, tool := range pins.tools {
		why, err := r.Store.BadTool(tool.Name, tool.Version)
		if err != nil {
			return err
		}
		if why != "" {
			return refuse("nothing rolled: %s %s is marked bad, since %s; pin another version", tool.Name, tool.Version, why)
		}
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
	slices.Sort(boxes)
	if probe != "" {
		at := slices.Index(boxes, probe)
		if at < 0 {
			return refuse("nothing rolled: %s hosts no live worker of Run %s", probe, run)
		}
		boxes = slices.Concat([]string{probe}, slices.Delete(boxes, at, at+1))
	}
	for done, box := range boxes {
		result, err := r.Runner.Run(ctx, shell.Command{Argv: pins.installArgv(box, nil), Timeout: spriteInstallTimeout})
		installed := installedTools(result.Stdout)
		if recorded := r.Store.RecordSpriteTools(box, installed, r.Now()); recorded != nil {
			return recorded
		}
		if err != nil {
			return r.failed(box, pins, installed, result, len(boxes)-done-1)
		}
		fmt.Fprintf(r.Out, "lr sprites roll: %s runs %s\n", box, censusOf(installed))
	}
	return nil
}

func (r SpriteRoll) failed(box string, pins spritePins, installed map[string]string, result shell.Result, left int) error {
	said := strings.TrimSpace(result.Stdout + result.Stderr)
	said = said[strings.LastIndex(said, "\n")+1:]
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
	roll := &cobra.Command{
		Use:   "roll",
		Short: "Install runner.json's Sprite pins on every Sprite with a live worker in the drive's Run, the probe Sprite first; a pin that fails is marked bad, stops the roll, and is never rolled again",
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
			home, err := stateHome()
			if err != nil {
				return err
			}
			roller := SpriteRoll{Runner: a.Runner, Orca: Client{Runner: a.Runner}, Fleet: CCRemote{Runner: a.Runner, State: filepath.Join(home, "cc-remote")}, Store: s, Now: a.Now, Out: cmd.OutOrStdout()}
			return refusalExit(cmd, roller.Run(cmd.Context(), run, env, probe))
		},
	}
	roll.Flags().StringVar(&probe, "probe", "", "the Sprite to install on first; default the first by name")
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
	cmd.AddCommand(roll, pin)
	return cmd
}
