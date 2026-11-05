package bulk

import (
	"cmp"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/spf13/cobra"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/orca"
)

func newPoster(cmd *cobra.Command, a *app.App, drive, author string) orca.Poster {
	return orca.Poster{Bus: orca.CCI{}, Runner: a.Runner, Env: os.Getenv, Now: a.Now, Sleep: time.Sleep, Out: cmd.ErrOrStderr(), Drive: drive, Author: author}
}

func absolute(paths []string) ([]string, error) {
	resolved := make([]string, len(paths))
	for i, path := range paths {
		abs, err := filepath.Abs(path)
		if err != nil {
			return nil, err
		}
		resolved[i] = abs
	}
	return resolved, nil
}

func Command(a *app.App) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "bulk",
		Short: "Read a corpus on Sonnet into a findings file, triage it on Opus into fix lanes, and hand the launcher the list",
	}
	cmd.AddCommand(readCommand(a), triageCommand(a), fileCommand(a))
	return cmd
}

func readCommand(a *app.App) *cobra.Command {
	var question, launcher, driveName, contract string
	var transcripts, records, logs []string
	cmd := &cobra.Command{
		Use:   "read LANE",
		Short: "Launch a Sonnet reader over the corpus, and print only the findings path and the lane names",
		Long: `Write the reader's and the triage's briefs and launch the reader through the orca runner. The reader writes the findings file and starts the triage with lr bulk triage; the triage writes fix-lane briefs and a one-pager and files the list with lr bulk file. Nothing but the findings path and the lane names comes back to the caller, and the launcher gets one done record with the list to launch.

Exit 0 when the reader launched or the runner is alive and still settling it, 1 when the runner or a check refused it, and 2 when no verdict came in time and the runner has posted no orca alive record in five minutes.`,
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			caller := os.Getenv(lanes.LaneEnv)
			author := cmp.Or(caller, launcher)
			send := newPoster(cmd, a, driveName, author)
			entry, err := send.Entry(cmd.Context())
			if err != nil {
				return err
			}
			binary, err := os.Executable()
			if err != nil {
				return err
			}
			transcriptFiles, err := absolute(transcripts)
			if err != nil {
				return err
			}
			logFiles, err := absolute(logs)
			if err != nil {
				return err
			}
			if contract != "" {
				if contract, err = filepath.Abs(contract); err != nil {
					return err
				}
			}
			job := Job{
				Drive: entry.Bus(), State: entry.StateDir, Binary: binary, Question: question,
				Reader: args[0], Triage: TriageLane(args[0]), Launcher: cmp.Or(launcher, caller),
				Transcripts: transcriptFiles, Records: records, Logs: logFiles, Contract: contract,
			}
			if err := job.Start(cmd.Context(), send); err != nil {
				return err
			}
			_, err = fmt.Fprintf(cmd.OutOrStdout(), "findings: %s\nreader: %s\ntriage: %s\n", job.Findings(), job.Reader, job.Triage)
			return err
		},
	}
	cmd.Flags().StringVar(&question, "question", "", "what the reader answers from the corpus")
	cmd.Flags().StringArrayVar(&transcripts, "transcript", nil, "a transcript file cc-transcript list printed (repeatable)")
	cmd.Flags().StringArrayVar(&records, "records", nil, "the arguments of one cci tail over the drive's records, such as \"--lane root --since '2:13 AM'\" (repeatable)")
	cmd.Flags().StringArrayVar(&logs, "log", nil, "a log file (repeatable)")
	cmd.Flags().StringVar(&launcher, "launcher", "", "the lane that launches the fix lanes; default the calling lane")
	cmd.Flags().StringVar(&contract, "contract", "", "a file every fix lane's brief must name, the launcher's contract for its helpers")
	cmd.Flags().StringVar(&driveName, "drive", "", "the cci drive the orca runner reads; default the drive bound to this session")
	_ = cmd.MarkFlagRequired("question")
	return cmd
}

func triageCommand(a *app.App) *cobra.Command {
	return &cobra.Command{
		Use:   "triage JOB",
		Short: "Refuse findings with an uncited claim, or launch the Opus triage; the reader's last step",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			job, err := Load(args[0])
			if err != nil {
				return err
			}
			return job.StartTriage(cmd.Context(), newPoster(cmd, a, job.Drive, job.Reader))
		},
	}
}

func fileCommand(a *app.App) *cobra.Command {
	var link string
	cmd := &cobra.Command{
		Use:   "file JOB",
		Short: "Refuse a fix list the runner or the launcher could not use, or post it to the launcher to launch; the triage's last step",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			job, err := Load(args[0])
			if err != nil {
				return err
			}
			return job.File(cmd.Context(), newPoster(cmd, a, job.Drive, job.Triage), link)
		},
	}
	cmd.Flags().StringVar(&link, "one-pager", "", "the link the published one-pager answers on")
	_ = cmd.MarkFlagRequired("one-pager")
	return cmd
}
