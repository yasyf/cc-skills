package install

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	SpriteHome    = "/home/sprite"
	SpritePlugins = SpriteHome + "/.claude/plugins/cache/skills/" + pluginName
	rollWait      = 20 * time.Minute
	copyTimeout   = 10 * time.Minute
	rollFaults    = 3
	stagedSuffix  = ".next"
	extractScript = `mkdir -p "$1" && tar -xzf "$2" -m -C "$1"; status=$?; rm -f "$2"; exit $status`
	moveScript    = `mv "$1" "$2"`
	rollScript    = `ln -sfn "$1" "$2"
for pid in $(pgrep -f "desk-runner[.]py run --config .* --desk $4\$"); do echo "$pid" >"$3.stop.$pid"; done
deadline=$(($(date +%s) + $5))
until pgrep -f "$1/.*desk-runner[.]py run --config .* --desk $4\$" >/dev/null; do
  [ "$(date +%s)" -lt "$deadline" ] || { echo "the $4 desk runner did not restart on $1 within $5 s" >&2; exit 1; }
  sleep 5
done`
)

type RollConfig struct {
	Runner  string
	Desk    string
	CIRedOK string
}

func rollCommand(a *app.App) *cobra.Command {
	var c RollConfig
	cmd := &cobra.Command{
		Use:   "roll",
		Short: "Move a desk on its Sprite host onto runner.json's pinned version at the end of its pass, once that version's own preflight accepts the config it will read",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			claude, err := drive.ConfigDir()
			if err != nil {
				return err
			}
			dir, err := Installs(claude)
			if err != nil {
				return err
			}
			return Roll(cmd.Context(), Checks{Runner: a.Runner, CCX: a.CCX, Installs: dir, Claude: claude}, c, cmd.OutOrStdout())
		},
	}
	cmd.Flags().StringVar(&c.Runner, "runner", "", "the runner.json naming the host and the version")
	cmd.Flags().StringVar(&c.Desk, "desk", "", "the desk to move")
	cmd.Flags().StringVar(&c.CIRedOK, "ci-red-ok", "", "why this version must run though main's CI has not passed on it")
	_ = cmd.MarkFlagRequired("runner")
	_ = cmd.MarkFlagRequired("desk")
	return cmd
}

func HostConfig(raw map[string]any) (map[string]any, error) {
	host, isHost := raw["host"].(map[string]any)
	if !isHost {
		return nil, errors.New("runner.json has no host")
	}
	out := make(map[string]any, len(raw))
	for key, value := range raw {
		out[key] = value
	}
	out["landing"] = overlaid(raw["landing"], host["landing"])
	if _, named := host["orca"]; named {
		out["orca"] = overlaid(raw["orca"], host["orca"])
	}
	return out, nil
}

func overlaid(section, host any) map[string]any {
	given, _ := section.(map[string]any)
	out := make(map[string]any, len(given))
	for key, value := range given {
		out[key] = value
	}
	overrides, _ := host.(map[string]any)
	for key, value := range overrides {
		override, isMap := value.(map[string]any)
		if !isMap {
			out[key] = value
			continue
		}
		merged := map[string]any{}
		if base, isBase := out[key].(map[string]any); isBase {
			for name, kept := range base {
				merged[name] = kept
			}
		}
		for name, set := range override {
			merged[name] = set
		}
		out[key] = merged
	}
	return out
}

func EncodedConfig(config map[string]any) ([]byte, error) {
	var out bytes.Buffer
	encoder := json.NewEncoder(&out)
	encoder.SetEscapeHTML(false)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(config); err != nil {
		return nil, err
	}
	return out.Bytes(), nil
}

func SpriteExec(sprite string, files []string, argv ...string) []string {
	out := []string{"sprite", "exec", "-s", sprite, "--no-stdin"}
	for _, file := range files {
		out = append(out, "--file", file)
	}
	return append(append(out, "--"), argv...)
}

func CopyTree(ctx context.Context, runner shell.Runner, sprite, scratch, root, dest string, names ...string) error {
	archive := filepath.Join(scratch, filepath.Base(dest)+".tgz")
	if _, err := runner.Run(ctx, shell.Command{
		Argv:    slices.Concat([]string{"tar", "--no-xattrs", "--exclude=__pycache__", "--exclude=.in_use", "-C", root, "-czf", archive}, names),
		Env:     []string{"COPYFILE_DISABLE=1"},
		Timeout: copyTimeout,
	}); err != nil {
		return err
	}
	remote := "/tmp/lr-copy-" + filepath.Base(archive)
	_, err := runner.Run(ctx, shell.Command{Argv: SpriteExec(sprite, []string{archive + ":" + remote}, "sh", "-c", extractScript, "sh", dest, remote), Timeout: copyTimeout})
	return err
}

func Roll(ctx context.Context, c Checks, config RollConfig, out io.Writer) error {
	data, err := os.ReadFile(config.Runner)
	if err != nil {
		return err
	}
	var raw map[string]any
	if err := json.Unmarshal(data, &raw); err != nil {
		return fmt.Errorf("%s: %w", config.Runner, err)
	}
	host, _ := raw["host"].(map[string]any)
	name, _ := host["sprite"].(string)
	version, _ := raw["version"].(string)
	if name == "" || version == "" {
		return fmt.Errorf("%s names no host.sprite and version to roll onto", config.Runner)
	}
	if fault := c.rollFault(ctx, version, config.CIRedOK); fault != "" {
		return fmt.Errorf("lr install roll: nothing moved, since %s", fault)
	}
	onHost, err := HostConfig(raw)
	if err != nil {
		return err
	}
	encoded, err := EncodedConfig(onHost)
	if err != nil {
		return err
	}
	scratch, err := os.MkdirTemp("", "lr-roll")
	if err != nil {
		return err
	}
	defer func() { _ = os.RemoveAll(scratch) }()
	staged := filepath.Join(scratch, "runner.json")
	if err := os.WriteFile(staged, encoded, 0o600); err != nil {
		return err
	}
	plugins := SpritePlugins
	if err := CopyTree(ctx, c.Runner, name, scratch, c.Installs, plugins, version); err != nil {
		return fmt.Errorf("lr install roll: nothing moved, since %s did not take the %s copy: %s", name, version, lastLine(err))
	}
	next := config.Runner + stagedSuffix
	root := plugins + "/" + version
	result, err := c.Runner.Run(ctx, shell.Command{
		Argv:    SpriteExec(name, []string{staged + ":" + next}, root+"/bin/desk-creds.py", "run", "--config", next, "--desk", config.Desk, "--preflight"),
		Env:     []string{pinEnv + "=" + version},
		Timeout: probeTimeout,
	})
	if err != nil {
		return fmt.Errorf("lr install roll: nothing moved, since the %s desk on %s would refuse: %s", config.Desk, version, refusal(result, err))
	}
	if _, err := c.Runner.Run(ctx, shell.Command{Argv: SpriteExec(name, nil, "sh", "-c", moveScript, "sh", next, config.Runner), Timeout: readTimeout}); err != nil {
		return err
	}
	seconds := strconv.Itoa(int(rollWait.Seconds()))
	if _, err := c.Runner.Run(ctx, shell.Command{
		Argv:    SpriteExec(name, nil, "sh", "-c", rollScript, "sh", root, SpriteHome+"/.claude/"+pluginName+"/"+pluginLink, config.Runner, config.Desk, seconds),
		Timeout: rollWait + time.Minute,
	}); err != nil {
		return fmt.Errorf("lr install roll: the %s desk on %s: %s", config.Desk, name, lastLine(err))
	}
	_, err = fmt.Fprintf(out, "lr install roll: the %s desk on %s runs %s\n", config.Desk, name, version)
	return err
}

func (c Checks) rollFault(ctx context.Context, version, ciRedOK string) string {
	if faults := c.ReleaseFaults(ctx, version); len(faults) > 0 {
		return fmt.Sprintf("the %s install is not its release: %s", version, strings.Join(faults[:min(rollFaults, len(faults))], "; "))
	}
	if ciRedOK != "" {
		return ""
	}
	if red := c.CIFault(ctx, version); red != "" {
		return red + "; roll once it passes, or pass --ci-red-ok with the reason it must run now"
	}
	return ""
}

func refusal(result shell.Result, err error) string {
	if result.Code == gateRefused {
		var said struct {
			Why string `json:"why"`
		}
		if json.Unmarshal([]byte(result.Stdout), &said) == nil && said.Why != "" {
			return said.Why
		}
	}
	return lastLine(err)
}
