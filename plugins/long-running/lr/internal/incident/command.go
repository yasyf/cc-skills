package incident

import (
	"encoding/json"
	"os"
	"path/filepath"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/update"
)

func claudeDir() (string, error) {
	if dir := os.Getenv("CLAUDE_CONFIG_DIR"); dir != "" {
		return dir, nil
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, ".claude"), nil
}

func Command(a *app.App) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "incident",
		Short: "Launch an incident's lanes and read its state from the bus",
	}
	cmd.AddCommand(runCommand(a), writtenCommand(a))
	cmd.AddCommand(update.IncidentCommands(a)...)
	return cmd
}

func runCommand(a *app.App) *cobra.Command {
	var alert, fixBrief, evidenceBrief, commsBrief, drive, lane string
	var timeout float64
	cmd := &cobra.Command{
		Use:   "run SLUG",
		Short: "Launch a slug's fix, evidence and comms lanes through the orca desk",
		Long:  "lr incident run SLUG --fix-brief FILE [--evidence-brief FILE] [--comms-brief FILE] [--alert TEXT] posts SLUG-fix, SLUG-evidence and SLUG-comms to the orca desk on the incident route and waits for the runner's verdicts.",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			claude, err := claudeDir()
			if err != nil {
				return err
			}
			return DeskLaunch{
				Bus: CCI{}, Runner: a.Runner, Drive: drive, Author: lane, Name: args[0], FixBrief: fixBrief, EvidenceBrief: evidenceBrief, CommsBrief: commsBrief, Alert: alert,
				Claude: claude, Timeout: time.Duration(timeout * float64(time.Second)), Poll: verdictPoll, Now: a.Now, Out: cmd.OutOrStdout(),
			}.Run(cmd.Context())
		},
	}
	flags := cmd.Flags()
	flags.StringVar(&alert, "alert", "", "the Alert line; each brief is then a template this fills, its <lane>, Alert: line and every {field}")
	flags.StringVar(&fixBrief, "fix-brief", "", "the fix lane's brief, or its template with --alert")
	flags.StringVar(&evidenceBrief, "evidence-brief", "", "the evidence lane's brief, or its template with --alert")
	flags.StringVar(&commsBrief, "comms-brief", "", "the comms lane's brief, or its template with --alert")
	flags.StringVar(&drive, "drive", "", "the cci drive the orca runner reads; default the drive bound to this session")
	flags.StringVar(&lane, "lane", "root", "the lane posting the launch lines")
	flags.Float64Var(&timeout, "timeout-seconds", DefaultTimeout.Seconds(), "how long to wait for the runner's verdicts")
	if err := cmd.MarkFlagRequired("fix-brief"); err != nil {
		panic(err)
	}
	return cmd
}

func writtenCommand(a *app.App) *cobra.Command {
	var since time.Duration
	cmd := &cobra.Command{
		Use:   "written",
		Short: "Each incident this machine's action store wrote within --since, with its target, alert, repair PR and not-ours facts, as JSON",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			root, err := DefaultRoot()
			if err != nil {
				return err
			}
			written, err := Store{Root: root}.WrittenSince(a.Now().Add(-since))
			if err != nil {
				return err
			}
			return json.NewEncoder(cmd.OutOrStdout()).Encode(written)
		},
	}
	cmd.Flags().DurationVar(&since, "since", 6*time.Hour, "how long ago the oldest write it reports may be")
	return cmd
}
