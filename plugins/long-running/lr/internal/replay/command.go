package replay

import (
	"encoding/json"
	"errors"
	"os"
	"strconv"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/install"
)

func deps(cmd *cobra.Command, a *app.App) (Deps, error) {
	claude, err := drive.ConfigDir()
	if err != nil {
		return Deps{}, err
	}
	installs, err := install.Installs(claude)
	if err != nil {
		return Deps{}, err
	}
	return Deps{Runner: a.Runner, CCX: a.CCX, Installs: installs, Now: a.Now, Stdout: cmd.OutOrStdout(), Tail: client.Tail}, nil
}

func Command(a *app.App) *cobra.Command {
	cmd := &cobra.Command{Use: "replay", Short: "Prove a fix on the records that reported it, refuse an install that acts differently on the drive's last day, and count how often each fixed defect came back"}
	cmd.AddCommand(runCommand(), gateCommand(a), recurCommand(a), captureCommand(a))
	return cmd
}

func runCommand() *cobra.Command {
	var input, scripts string
	cmd := &cobra.Command{
		Use:   "run",
		Short: "Print the decisions this binary makes on one input",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			data, err := os.ReadFile(input)
			if err != nil {
				return err
			}
			var given Input
			if err := json.Unmarshal(data, &given); err != nil {
				return err
			}
			decided, err := Decide(given)
			if err != nil {
				return err
			}
			return json.NewEncoder(cmd.OutOrStdout()).Encode(decided)
		},
	}
	cmd.Flags().StringVar(&input, "input", "", "the input file")
	cmd.Flags().StringVar(&scripts, "scripts", "", "read by Python packs, which the gate reaches through the same bin/replay.py")
	_ = cmd.MarkFlagRequired("input")
	_ = cmd.Flags().MarkHidden("scripts")
	return cmd
}

func gateCommand(a *app.App) *cobra.Command {
	var driveName, repo, oldVersion, newVersion string
	cmd := &cobra.Command{
		Use:   "gate",
		Short: "Refuse, exiting 3, an install that relaunches a lane or refuses a PR the serving version did not on the drive's last 24 hours",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			d, err := deps(cmd, a)
			if err != nil {
				return err
			}
			code, err := d.Gate(cmd.Context(), driveName, repo, oldVersion, newVersion)
			if err != nil {
				return err
			}
			if code != 0 {
				os.Exit(code)
			}
			return nil
		},
	}
	cmd.Flags().StringVar(&driveName, "drive", "", "the drive")
	cmd.Flags().StringVar(&repo, "repo", "", "the repository whose class checks replay")
	cmd.Flags().StringVar(&oldVersion, "old", "", "the version serving now")
	cmd.Flags().StringVar(&newVersion, "new", "", "the version to install")
	for _, name := range []string{"drive", "old", "new"} {
		_ = cmd.MarkFlagRequired(name)
	}
	return cmd
}

func recurCommand(a *app.App) *cobra.Command {
	var driveName string
	cmd := &cobra.Command{
		Use:   "recur",
		Short: "Report each fixed defect's recurrences in the 24 hours after its install",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			d, err := deps(cmd, a)
			if err != nil {
				return err
			}
			return d.Recur(cmd.Context(), driveName)
		},
	}
	cmd.Flags().StringVar(&driveName, "drive", "", "the drive")
	_ = cmd.MarkFlagRequired("drive")
	return cmd
}

func captureCommand(a *app.App) *cobra.Command {
	c := Capture{Upto: noUpperBound}
	cmd := &cobra.Command{
		Use:   "capture",
		Short: "Write a fixture from the records that reported a defect, scrubbed for a public repository",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			if c.Check != "lanes" && c.Check != "test-only" {
				return errors.New("--check must be lanes or test-only, got " + strconv.Quote(c.Check))
			}
			d, err := deps(cmd, a)
			if err != nil {
				return err
			}
			return d.Capture(cmd.Context(), c, cmd.OutOrStdout())
		},
	}
	flags := cmd.Flags()
	flags.StringVar(&c.Drive, "drive", "", "the drive")
	flags.StringVar(&c.Class, "class", "", "the fixture's defect class")
	flags.Int64Var(&c.Defect, "defect", 0, "the record that reported the defect")
	flags.StringVar(&c.Fix, "fix", "", "the PR that fixed it")
	flags.StringVar(&c.Recurs, "recurs", "", "a pattern a later report of the defect matches")
	flags.StringArrayVar(&c.RecursKind, "recurs-kind", nil, "a record kind such a report carries")
	flags.StringVar(&c.Check, "check", "", "lanes or test-only")
	flags.StringArrayVar(&c.Lanes, "lane", nil, "a lane the records are about")
	flags.Int64Var(&c.From, "from", 1, "the first record's seq")
	flags.Int64Var(&c.Upto, "upto", noUpperBound, "the last record's seq")
	flags.StringVar(&c.Repo, "repo", "", "the repository of --pr")
	flags.IntSliceVar(&c.PRs, "pr", nil, "a PR whose changed files the class check read")
	flags.StringVar(&c.Expect, "expect", "", "what the fixed pack decides, as JSON")
	flags.StringVar(&c.Out, "out", "", "the fixture file")
	for _, name := range []string{"drive", "class", "defect", "fix", "recurs", "check", "expect", "out"} {
		_ = cmd.MarkFlagRequired(name)
	}
	return cmd
}
