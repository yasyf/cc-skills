package asklane

import (
	"bytes"
	"context"
	_ "embed"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"text/template"
	"time"

	"github.com/spf13/cobra"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/orca"
)

const (
	stem      = "ask-lane"
	model     = "sonnet"
	effort    = "low"
	briefFile = "brief.md"
)

var (
	//go:embed brief.md.tmpl
	briefText string
	brief     = template.Must(template.New("ask").Parse(briefText))
)

type Ask struct {
	Drive, State, Lane, Asker, Question string
	Mac                                 bool
}

func Command(a *app.App) *cobra.Command {
	var mac bool
	var driveName string
	cmd := &cobra.Command{
		Use:   "ask-lane QUESTION",
		Short: "Launch a Sonnet search lane that posts its answer to the asking lane, and return without waiting on it",
		Long: `Write a search lane's brief under the drive's state dir and post its launch to the orca runner. The lane is named ask-lane, or the next free ask-lane-<n>. It answers in one answer record to the asking lane, naming the seqs and paths it read, then ends.

The lane runs on a Sprite, which has no Slack and no Mac transcripts; --mac runs it on the drive's Mac for a question that needs either.`,
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			asker, err := lanes.BoundLane(cmd.Context(), a.Runner)
			if err != nil {
				return err
			}
			send := orca.Poster{Bus: orca.CCI{}, Runner: a.Runner, Env: os.Getenv, Now: a.Now, Sleep: time.Sleep, Out: cmd.ErrOrStderr(), Drive: driveName, Author: asker}
			entry, err := send.Entry(cmd.Context())
			if err != nil {
				return err
			}
			ask := Ask{Drive: entry.Bus(), State: entry.StateDir, Asker: asker, Question: args[0], Mac: mac}
			return ask.Start(cmd.Context(), send, cmd.OutOrStdout())
		},
	}
	cmd.Flags().BoolVar(&mac, "mac", false, "run the lane on the drive's Mac, for a question that needs Slack or a session transcript")
	cmd.Flags().StringVar(&driveName, "drive", "", "the cci drive the orca runner reads; default the drive bound to this session")
	return cmd
}

func (a Ask) Start(ctx context.Context, send orca.Poster, out io.Writer) error {
	if strings.TrimSpace(a.Question) == "" {
		return errors.New("lr ask-lane refused: the question is blank")
	}
	lane, err := claim(a.State)
	if err != nil {
		return err
	}
	a.Lane = lane
	var text bytes.Buffer
	if err := brief.Execute(&text, a); err != nil {
		return err
	}
	path := filepath.Join(a.State, lane, briefFile)
	if err := os.WriteFile(path, text.Bytes(), 0o600); err != nil {
		return err
	}
	if err := send.LaunchOrExit(ctx, orca.Order{Lane: lane, Model: model, Effort: effort, Brief: path}, 0); err != nil {
		return err
	}
	_, err = fmt.Fprintf(out, "%s: its answer arrives as an answer record to %s\n", lane, a.Asker)
	return err
}

func claim(state string) (string, error) {
	entries, err := os.ReadDir(state)
	if err != nil {
		return "", err
	}
	taken := map[string]bool{}
	for _, entry := range entries {
		taken[entry.Name()] = true
	}
	for {
		lane := stem
		if taken[lane] {
			lane = lanes.SuccessorName(stem, taken)
		}
		err := os.Mkdir(filepath.Join(state, lane), 0o750)
		if !errors.Is(err, fs.ErrExist) {
			return lane, err
		}
		taken[lane] = true
	}
}
