package prompts

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
)

func busPost(drive string) func(context.Context, Notice) (int64, error) {
	return func(ctx context.Context, n Notice) (int64, error) {
		draft := client.Draft{Drive: drive, Lane: Lane, Kind: n.Kind, Text: n.Text, Topic: n.Topic, Resolves: n.Resolves}
		if n.To != "" {
			draft.To = []string{n.To}
		}
		record, err := client.Post(ctx, draft)
		return record.Seq, err
	}
}

const reshowEvery = 3 * time.Minute

func NewWatch(a *app.App, drive string) *Watch {
	return &Watch{Runner: a.Runner, Post: busPost(drive), Now: a.Now, Sleep: time.Sleep, Reshow: reshowEvery}
}

func printShown(out io.Writer, shown Shown, asJSON bool) error {
	if !asJSON {
		_, err := fmt.Fprintln(out, shown.Text())
		return err
	}
	data, err := json.Marshal(shown)
	if err != nil {
		return err
	}
	_, err = fmt.Fprintln(out, string(data))
	return err
}

func show(cmd *cobra.Command, a *app.App, drive string, asJSON bool) error {
	entry, err := Find(drive)
	if errors.Is(err, ErrNoDrive) {
		if asJSON {
			_, err := fmt.Fprintln(cmd.OutOrStdout(), "{}")
			return err
		}
		_, err := fmt.Fprintln(cmd.OutOrStdout(), err)
		return err
	}
	if err != nil {
		return err
	}
	shown, err := View(entry, a.Now())
	if err != nil {
		return err
	}
	return printShown(cmd.OutOrStdout(), shown, asJSON)
}

func answerCommandVerb(a *app.App) *cobra.Command {
	var args AnswerArgs
	cmd := &cobra.Command{
		Use:   "answer (--dispatch ID | --terminal HANDLE [--environment NAME]) --by NAME [--episode KEY] (yes | no --instead TEXT)",
		Short: "Choose Yes or No on the approval dialog open in one terminal, and record who chose it in the Run's mail",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, positional []string) error {
			args.Choice = positional[0]
			if _, known := choices[args.Choice]; !known {
				return fmt.Errorf("the choice is yes or no, not %q", args.Choice)
			}
			if (args.Dispatch == "") == (args.Terminal == "") {
				return errors.New("name one of --dispatch and --terminal")
			}
			watch := &Watch{Runner: a.Runner, Now: a.Now, Sleep: time.Sleep}
			answered, err := watch.Answer(cmd.Context(), args)
			if err != nil {
				return err
			}
			if _, err := fmt.Fprintln(cmd.OutOrStdout(), answered.Line); err != nil {
				return err
			}
			if !answered.OK {
				return errors.New("the answer did not settle; the line above says what happened")
			}
			return nil
		},
	}
	cmd.Flags().StringVar(&args.Dispatch, "dispatch", "", "the worker's dispatch id, ctx_...")
	cmd.Flags().StringVar(&args.Terminal, "terminal", "", "the terminal handle")
	cmd.Flags().StringVar(&args.Environment, "environment", "", "the paired Orca server holding --terminal")
	cmd.Flags().StringVar(&args.By, "by", "", "who decided, as the Run's mail should name them")
	cmd.Flags().StringVar(&args.Episode, "episode", "", "the prompt's key from the watch's record; refuse when the dialog on screen is another")
	cmd.Flags().StringVar(&args.Instead, "instead", "", "with no: what the agent should do instead, typed and submitted once the dialog closes")
	if err := cmd.MarkFlagRequired("by"); err != nil {
		panic(err)
	}
	return cmd
}

func Command(a *app.App) *cobra.Command {
	var drive string
	var asJSON bool
	cmd := &cobra.Command{
		Use:   "prompts --drive NAME [--json]",
		Short: "Poll every terminal of the drive once for a prompt nobody sees, tell whoever can act, and print the result",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			entry, err := Find(drive)
			if err != nil {
				return err
			}
			err = NewWatch(a, entry.Bus()).Check(cmd.Context(), entry)
			if errors.Is(err, ErrHeld) {
				fmt.Fprintf(a.Stderr, "prompt-watch %s: %v\n", entry.Drive, err)
				err = nil
			}
			if err != nil {
				return err
			}
			shown, err := View(entry, a.Now())
			if err != nil {
				return err
			}
			return printShown(cmd.OutOrStdout(), shown, asJSON)
		},
	}
	cmd.Flags().StringVar(&drive, "drive", os.Getenv("LR_DRIVE"), "the drive's registry id or bus name")
	cmd.Flags().BoolVar(&asJSON, "json", false, "print the view as JSON")
	showCmd := &cobra.Command{
		Use:   "show --drive NAME [--json]",
		Short: "Print the last poll without polling",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return show(cmd, a, drive, asJSON)
		},
	}
	showCmd.Flags().StringVar(&drive, "drive", os.Getenv("LR_DRIVE"), "the drive's registry id or bus name")
	showCmd.Flags().BoolVar(&asJSON, "json", false, "print the view as JSON")
	var terminal string
	var cards bool
	bindCmd := &cobra.Command{
		Use:   "bind --drive NAME [--root-terminal HANDLE] [--slack-cards]",
		Short: "Record the coordinator's current terminal, or turn on owner cards for its prompts, in the drive's registry entry",
		Args:  cobra.NoArgs,
		RunE: func(*cobra.Command, []string) error {
			return Bind(drive, terminal, cards)
		},
	}
	bindCmd.Flags().StringVar(&drive, "drive", os.Getenv("LR_DRIVE"), "the drive's registry id or bus name")
	bindCmd.Flags().StringVar(&terminal, "root-terminal", "", "the terminal the coordinator now runs in")
	bindCmd.Flags().BoolVar(&cards, "slack-cards", false, "also card the owner's DM when the coordinator's own prompt has no supervisor to wake")
	cmd.AddCommand(showCmd, bindCmd, answerCommandVerb(a))
	return cmd
}
