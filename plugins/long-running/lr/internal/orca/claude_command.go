package orca

import (
	"fmt"
	"os"
	"os/exec"
	"regexp"
	"slices"
	"strings"
	"syscall"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
)

const ownerPickFlag = "--owner-pick"

var ownerPick = regexp.MustCompile(`^owner:m[0-9]+$`)

type claudeStart struct {
	args                []string
	model, effort, pick string
}

func claudeCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "claude [--owner-pick owner:m<row>] [CLAUDE ARGS...]",
		Short: "Start claude on the drive's cost settings: opus unless --model names another, effort high, and compaction at 300k tokens",
		Long: `Exec claude with ` + laneCompact + `, so it compacts at 300k tokens, adding --model opus when no --model is given and --effort high when no --effort is.

A Fable or astra model, or effort xhigh or max, starts only with --owner-pick naming the owner row whose words asked for it. Every other argument goes to claude unchanged.`,
		DisableFlagParsing: true,
		RunE: func(cmd *cobra.Command, args []string) error {
			if len(args) == 1 && (args[0] == "--help" || args[0] == "-h") {
				return cmd.Help()
			}
			start, err := parseClaudeStart(args)
			if err != nil {
				return err
			}
			argv, err := start.argv()
			if err != nil {
				return err
			}
			path, err := exec.LookPath("claude")
			if err != nil {
				return err
			}
			return syscall.Exec(path, append([]string{"claude"}, argv...), append(os.Environ(), laneCompact))
		},
	}
}

func parseClaudeStart(args []string) (claudeStart, error) {
	var start claudeStart
	for i := 0; i < len(args); i++ {
		name, value, joined := strings.Cut(args[i], "=")
		if name != ownerPickFlag && name != "--model" && name != "--effort" {
			start.args = append(start.args, args[i])
			continue
		}
		if !joined {
			if i+1 == len(args) {
				return start, fmt.Errorf("%s needs a value", name)
			}
			i++
			value = args[i]
		}
		switch name {
		case ownerPickFlag:
			if !ownerPick.MatchString(value) {
				return start, fmt.Errorf("%s takes owner:m<row>, the owner row whose words asked for this model or effort, not %q", ownerPickFlag, value)
			}
			start.pick = value
			continue
		case "--model":
			start.model = value
		case "--effort":
			start.effort = value
		}
		start.args = append(start.args, name, value)
	}
	return start, nil
}

func (s claudeStart) argv() ([]string, error) {
	argv := slices.Clone(s.args)
	if s.model == "" {
		argv = append(argv, "--model", defaultLaneModel)
	}
	if s.effort == "" {
		argv = append(argv, "--effort", defaultLaneEffort)
	}
	if s.pick != "" {
		return argv, nil
	}
	again := "lr launch claude " + strings.Join(append([]string{ownerPickFlag, "owner:m<row>"}, s.args...), " ")
	if fableModel.MatchString(s.model) || astraModel.MatchString(s.model) {
		return nil, fmt.Errorf("%s starts only when the owner named it for this work: run `%s` naming his words, or drop --model for opus", s.model, again)
	}
	if slices.Contains(lanes.AboveHigh, s.effort) {
		return nil, fmt.Errorf("effort %s is above high, which starts only when the owner named it: run `%s` naming his words, or drop --effort for high", s.effort, again)
	}
	return argv, nil
}
