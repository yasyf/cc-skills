package orca

import (
	"cmp"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"log/slog"
	"maps"
	"os"
	"os/signal"
	"path/filepath"
	"regexp"
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

const (
	spriteInstallTimeout = 15 * time.Minute
	sameFailureStop      = 3
)

var pinAtFault = regexp.MustCompile(`^refused: |curl: \(22\)|Not found in archive`)

type runWorkers interface {
	Workers(ctx context.Context, run string) ([]Worker, error)
}

type hostedTasks interface {
	Sprites() (map[SpriteKey]Sprite, error)
}

type SpriteRoll struct {
	Runner  shell.Runner
	Orca    runWorkers
	Fleet   hostedTasks
	Store   *store.Store
	Host    string
	Graders []string
	Skip    []string
	Now     func() time.Time
	Out     io.Writer
}

func FollowPins(ctx context.Context, runner shell.Runner, s *store.Store, claude, path string, now func() time.Time, out io.Writer) error {
	err := MoveSpritePins(ctx, s, claude, path, out)
	if errors.Is(err, fs.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	return errors.Join(RollHost(ctx, runner, s, path, now, out), RollGraders(ctx, runner, s, path, now, out))
}

func RollGraders(ctx context.Context, runner shell.Runner, s *store.Store, path string, now func() time.Time, out io.Writer) error {
	file, err := readRunnerFile(path)
	if err != nil {
		return err
	}
	graders := file.graders()
	if len(graders) == 0 {
		return nil
	}
	env, err := runnerEnv(path)
	if err != nil {
		return err
	}
	pins, err := readPins(env)
	if err != nil {
		return err
	}
	pins = pins.forGrader(graderTool)
	roll := SpriteRoll{Runner: runner, Store: s, Now: now, Out: out}
	if len(pins.tools) == 0 {
		return nil
	}
	if err := roll.notBad(pins); err != nil {
		return err
	}
	var behind []string
	for _, box := range graders {
		level, err := roll.runs(box, pins)
		if err != nil {
			return err
		}
		if !level {
			behind = append(behind, box)
		}
	}
	return roll.install(ctx, pins, behind, "")
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
	pins, err := readPins(env)
	if err != nil {
		return err
	}
	roll := SpriteRoll{Runner: runner, Store: s, Host: file.Host.Sprite, Now: now, Out: out}
	if err := roll.notBad(pins.forHost()); err != nil {
		return err
	}
	level, err := roll.runs(file.Host.Sprite, pins.forHost())
	if err != nil || level {
		return err
	}
	return roll.install(ctx, pins, []string{file.Host.Sprite}, file.Host.Sprite)
}

func (r SpriteRoll) runs(box string, pins spritePins) (bool, error) {
	recorded, err := r.Store.SpriteTools()
	if err != nil {
		return false, err
	}
	held := map[string]string{}
	for _, tool := range recorded {
		if tool.Sprite == box {
			held[tool.Name] = tool.Version
		}
	}
	wanted := slices.Clone(pins.tools)
	if version := cciVersion.FindStringSubmatch(pins.ccURL); version != nil {
		wanted = append(wanted, spriteTool{Name: "cci", Version: version[1]})
	}
	for _, tool := range wanted {
		if held[tool.Name] != tool.Version {
			return false, nil
		}
	}
	return unmoved(held) == "", nil
}

func unmoved(installed map[string]string) string {
	running, ok := installed["cci-daemon"]
	if !ok || running == "none" || running == installed["cci"] {
		return ""
	}
	return "its cci daemon runs " + running + ", not yet moved to cci " + installed["cci"]
}

func (r SpriteRoll) pins(env func(string) string) (spritePins, error) {
	pins, err := readPins(env)
	if err != nil {
		return pins, err
	}
	return pins, r.notBad(pins)
}

func (r SpriteRoll) notBad(pins spritePins) error {
	for _, tool := range pins.tools {
		why, err := r.Store.BadTool(tool.Name, tool.Version)
		if err != nil {
			return err
		}
		if why != "" {
			return refuse("nothing rolled: %s %s is marked bad, since %s; pin another version", tool.Name, tool.Version, why)
		}
	}
	return nil
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
	r.Graders = slices.DeleteFunc(slices.Clone(r.Graders), func(box string) bool { return slices.Contains(boxes, box) || box == r.Host })
	if r.Host != "" && !slices.Contains(boxes, r.Host) {
		boxes = append(boxes, r.Host)
	}
	slices.Sort(boxes)
	boxes = slices.Concat(r.Graders, boxes)
	if slices.Contains(r.Skip, probe) {
		return refuse("nothing rolled: --skip names the probe %s", probe)
	}
	var skipped []string
	boxes = slices.DeleteFunc(boxes, func(box string) bool {
		held := slices.Contains(r.Skip, box)
		if held {
			skipped = append(skipped, box)
		}
		return held
	})
	if len(skipped) > 0 {
		fmt.Fprintf(r.Out, "lr sprites roll: skipped, as --skip asks: %s\n", strings.Join(skipped, " "))
	}
	if probe != "" {
		at := slices.Index(boxes, probe)
		if at < 0 {
			return refuse("nothing rolled: %s hosts no live worker of Run %s and runner.json names it no grader", probe, run)
		}
		boxes = slices.Concat([]string{probe}, slices.Delete(boxes, at, at+1))
		if probeOnly {
			boxes = boxes[:1]
		}
	}
	return r.install(ctx, pins, boxes, probe)
}

func (r SpriteRoll) install(ctx context.Context, pins spritePins, boxes []string, probe string) error {
	proven := map[string]bool{}
	var unreached, failed []string
	said := map[string]string{}
	var last string
	streak := 0
	for done, box := range boxes {
		boxPins := r.pinsOn(box, pins)
		try, err := r.installOn(ctx, box, boxPins)
		if err != nil {
			return err
		}
		result, installed := try.result, try.installed
		if try.failed != nil && len(installed) == 0 && spriteUnreached(result) {
			if box == probe {
				return refuse("lr sprites roll: could not reach the probe %s, so %d more Sprites keep what they run: %s", box, len(boxes)-done-1, lastSaid(result))
			}
			fmt.Fprintf(r.Out, "lr sprites roll: could not reach %s: %s\n", box, lastSaid(result))
			unreached = append(unreached, box)
			continue
		}
		for name := range installed {
			proven[name] = true
		}
		if try.failed == nil {
			proven[""] = true
			last, streak = "", 0
			fmt.Fprintf(r.Out, "lr sprites roll: %s runs %s\n", box, censusOf(installed))
			continue
		}
		if box != r.Host && !proven[failedTool(boxPins, installed)] {
			return r.failed(box, boxPins, installed, result, len(boxes)-done-1)
		}
		failed = append(failed, box)
		said[box] = lastSaid(result)
		if said[box] != last {
			last, streak = said[box], 0
		}
		streak++
		fmt.Fprintf(r.Out, "lr sprites roll: %s failed, the roll goes on and tries it again at the end: %s\n", box, said[box])
		if streak == sameFailureStop {
			return refuse("lr sprites roll: %d Sprites in a row failed the same way, so a pin is likely at fault though none is marked bad, and %d more Sprites keep what they run: %s", streak, len(boxes)-done-1, said[box])
		}
	}
	var stayed []string
	for _, box := range failed {
		try, err := r.installOn(ctx, box, r.pinsOn(box, pins))
		if err != nil {
			return err
		}
		if try.failed == nil {
			fmt.Fprintf(r.Out, "lr sprites roll: %s runs %s on its second try\n", box, censusOf(try.installed))
			continue
		}
		said[box] = lastSaid(try.result)
		stayed = append(stayed, box)
	}
	if err := r.reportStayed(stayed, said); err != nil {
		return err
	}
	if len(unreached) > 0 {
		fmt.Fprintf(r.Out, "lr sprites roll: never reached: %s\n", strings.Join(unreached, " "))
	}
	if len(stayed) > 0 {
		return refuse("lr sprites roll: %d of %d Sprites failed twice and keep what they hold, and no pin is marked bad: %s", len(stayed), len(boxes), strings.Join(stayed, " "))
	}
	return nil
}

func (r SpriteRoll) pinsOn(box string, pins spritePins) spritePins {
	switch {
	case box == r.Host:
		return pins.forHost()
	case slices.Contains(r.Graders, box):
		return pins.forGrader(graderTools...)
	}
	return pins
}

type installTry struct {
	result    shell.Result
	installed map[string]string
	failed    error
}

func (r SpriteRoll) installOn(ctx context.Context, box string, pins spritePins) (installTry, error) {
	result, failed := r.Runner.Run(ctx, shell.Command{Argv: pins.installArgv(box, nil), Timeout: spriteInstallTimeout})
	try := installTry{result: result, installed: installedTools(result.Stdout), failed: failed}
	return try, r.Store.RecordSpriteTools(box, try.installed, r.Now())
}

func (r SpriteRoll) reportStayed(stayed []string, said map[string]string) error {
	if len(stayed) == 0 {
		return nil
	}
	tools, err := r.Store.SpriteTools()
	if err != nil {
		return err
	}
	held := map[string]map[string]string{}
	for _, tool := range tools {
		if held[tool.Sprite] == nil {
			held[tool.Sprite] = map[string]string{}
		}
		held[tool.Sprite][tool.Name] = tool.Version
	}
	for _, box := range stayed {
		fmt.Fprintf(r.Out, "lr sprites roll: %s failed twice and holds %s: %s\n", box, cmp.Or(censusOf(held[box]), "nothing a roll recorded"), said[box])
	}
	return nil
}

func failedTool(pins spritePins, installed map[string]string) string {
	switch {
	case installed["cci"] == "":
		return "cci"
	case installed["lr"] == "":
		return "lr"
	}
	if failed := slices.IndexFunc(pins.tools, func(tool spriteTool) bool { return installed[tool.Name] != tool.Version }); failed >= 0 {
		return pins.tools[failed].Name
	}
	return ""
}

func spriteUnreached(result shell.Result) bool {
	said := result.Stdout + result.Stderr
	return result.Code == shell.CodeTimedOut || strings.Contains(said, "failed to start sprite command") || strings.Contains(said, "failed to fetch sprite version")
}

func lastSaid(result shell.Result) string {
	said := strings.TrimSpace(result.Stdout + result.Stderr)
	if fault := installFault(said); fault != "" {
		return fault
	}
	return said[strings.LastIndex(said, "\n")+1:]
}

func (r SpriteRoll) failed(box string, pins spritePins, installed map[string]string, result shell.Result, left int) error {
	said := lastSaid(result)
	if result.Code == shell.CodeTimedOut {
		return refuse("lr sprites roll: the install ran out of time on %s, so %d more Sprites keep what they run and no pin is marked bad: %s", box, left, said)
	}
	failed := slices.IndexFunc(pins.tools, func(tool spriteTool) bool { return installed[tool.Name] != tool.Version })
	if installed["cci"] == "" || installed["lr"] == "" || failed < 0 {
		return refuse("lr sprites roll: the cci or lr install failed on %s, so %d more Sprites keep what they run: %s", box, left, said)
	}
	if !pinAtFault.MatchString(said) {
		return refuse("lr sprites roll: nothing shows a pin at fault for the failed install on %s, so %d more Sprites keep what they run and no pin is marked bad: %s", box, left, said)
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
		line := fmt.Sprintf("%s %s at %s", box, censusOf(installed), store.Stamp(at))
		if lag := unmoved(installed); lag != "" {
			line += "; " + lag
		}
		fmt.Fprintln(out, line)
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
	var skip []string
	roll := &cobra.Command{
		Use:   "roll",
		Short: "Install runner.json's Sprite pins on the desk host, every Sprite with a live worker in the drive's Run, and the grader pins on its finder and fidelity Sprites, the probe Sprite first; a pin that fails before any box installed it is marked bad, stops the roll, and is never rolled again; a later box's failure is retried once at the end, and three in a row failing the same way stop the roll",
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
			roller := SpriteRoll{Runner: a.Runner, Orca: Client{Runner: a.Runner}, Fleet: CCRemote{Runner: a.Runner, State: filepath.Join(home, "cc-remote")}, Store: s, Host: file.Host.Sprite, Graders: file.graders(), Skip: skip, Now: a.Now, Out: cmd.OutOrStdout()}
			if probeOnly && probe == "" {
				return errors.New("--probe-only needs --probe, so it names the one Sprite to install on")
			}
			return refusalExit(cmd, roller.Run(cmd.Context(), run, env, probe, probeOnly))
		},
	}
	roll.Flags().StringVar(&probe, "probe", "", "the Sprite to install on first; default the first by name")
	roll.Flags().BoolVar(&probeOnly, "probe-only", false, "install on the --probe Sprite and stop, so a person can check it before the rest")
	roll.Flags().StringSliceVar(&skip, "skip", nil, "a Sprite the roll leaves alone and names, never counting it a failure; repeat or comma-separate for more")
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
		Short: "Run on a Sprite as its service: keep the Sprite awake while a claude or codex runs on it, so the hold lapses within 5 minutes of the agent's exit, append a sample to /tmp/cc-remote-samples.log every 10 seconds, and every 5 minutes push each checkout's unpushed commits and uncommitted edits to refs/lr/work/<lane> on its origin when they changed",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			ctx, stop := signal.NotifyContext(cmd.Context(), syscall.SIGTERM, os.Interrupt)
			defer stop()
			log := slog.New(slog.NewTextHandler(cmd.ErrOrStderr(), nil))
			home, err := os.UserHomeDir()
			if err != nil {
				return err
			}
			sampler := awake.Sampler{Proc: "/proc", Events: "/sys/fs/cgroup/memory.events", Kernel: awake.KernelLog, Path: awake.SamplePath, Every: awake.SampleEvery, Now: a.Now, Log: log}
			go sampler.Run(ctx)
			go awake.Work{Runner: a.Runner, Proc: "/proc", Home: home, Every: awake.WorkEvery, Log: log}.Run(ctx)
			awake.Holder{Runner: a.Runner, Task: workerAwake, Every: awake.Every, Log: log, Wanted: awake.WorkerAlive(a.Runner)}.Run(ctx)
			return nil
		},
	}
	grader := &cobra.Command{
		Use:   "grader NAME...",
		Short: "Make each named Sprite a grader: install the grader pins, link this Mac's home and grading checkouts onto the Sprite's checkout, and fetch its whole history; the coordinator publishes the job's returned records",
		Args:  cobra.MinimumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			s, _, state, err := opened()
			if err != nil {
				return err
			}
			defer func() { _ = s.Close() }()
			home, err := os.UserHomeDir()
			if err != nil {
				return err
			}
			maker := &GraderMaker{Runner: a.Runner, Store: s, Home: home, Now: a.Now, Out: cmd.OutOrStdout()}
			return refusalExit(cmd, maker.Make(cmd.Context(), filepath.Join(state, "runner.json"), args))
		},
	}
	cmd.AddCommand(roll, pin, hold, grader)
	return cmd
}
