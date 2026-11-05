package update

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/wait"
)

const (
	defaultSince = 2 * time.Hour
	gapsExit     = 3
)

func missingError(left []string) error {
	if len(left) == 0 {
		return nil
	}
	return fmt.Errorf("missing: %d: %s", len(left), strings.Join(left, "; "))
}

func exitOf(found Report, stderr io.Writer) error {
	if left := Missing(found.Text, found.Needs); len(left) > 0 {
		return missingError(append(left, found.Gaps...))
	}
	if len(found.Gaps) == 0 {
		return nil
	}
	fmt.Fprintf(stderr, "lr: gaps: %d: %s\n", len(found.Gaps), strings.Join(found.Gaps, "; "))
	return &wait.ExitError{Code: gapsExit}
}

type session struct {
	entry drive.Entry
	store *store.Store
	src   Sources
}

func open(a *app.App, name string) (session, error) {
	entry, err := drive.Named(name)
	if err != nil {
		return session{}, err
	}
	s, err := store.Open(entry.Store())
	if err != nil {
		return session{}, err
	}
	return session{entry, s, Wire(a, entry, s)}, nil
}

func Command(a *app.App) *cobra.Command {
	var driveName string
	cmd := &cobra.Command{Use: "update", Short: "The owner update, read from sources, never from memory"}
	cmd.PersistentFlags().StringVar(&driveName, "drive", os.Getenv("LR_DRIVE"), "the drive's bus name")
	report := func(c *cobra.Command) (Report, string, error) {
		opened, err := open(a, driveName)
		if err != nil {
			return Report{}, "", err
		}
		defer opened.store.Close()
		in, err := Collect(c.Context(), opened.src, opened.entry.Bus(), a.Now())
		if err != nil {
			return Report{}, "", err
		}
		return Render(in), opened.entry.StateDir, nil
	}
	var publish, sent, rendering bool
	var checking string
	render := &cobra.Command{
		Use:   "render",
		Short: "Print the whole owner update; exit 1 naming what it leaves out, or 3 naming only its gaps",
		Args:  cobra.NoArgs,
		RunE: func(c *cobra.Command, _ []string) error {
			found, stateDir, err := report(c)
			if err != nil {
				return err
			}
			fmt.Fprint(c.OutOrStdout(), found.Text)
			if publish || sent {
				if err := found.Publish(stateDir, sent); err != nil {
					return err
				}
			}
			return exitOf(found, c.ErrOrStderr())
		},
	}
	cmd.PersistentFlags().BoolVar(&publish, "publish", false, "with render: write the dashboard's steps.json from the same rows")
	cmd.PersistentFlags().BoolVar(&sent, "sent", false, "with render: publish, and save this render as the update the owner is sent, which the next one diffs against")
	check := &cobra.Command{
		Use:   "check FILE",
		Short: "Exit 1 naming each section, lane, ask, PR and tooling program or blocker a written update leaves out; - reads stdin",
		Args:  cobra.ExactArgs(1),
		RunE: func(c *cobra.Command, args []string) error {
			found, _, err := report(c)
			if err != nil {
				return err
			}
			var written []byte
			if args[0] == "-" {
				written, err = io.ReadAll(c.InOrStdin())
			} else {
				written, err = os.ReadFile(args[0])
			}
			if err != nil {
				return err
			}
			return missingError(append(Missing(string(written), found.Needs), ToolingLeft(string(written), found.Programs)...))
		},
	}
	status, since := statusCommand(a, &driveName, cmd)
	away := &cobra.Command{
		Use:   "away",
		Short: "Print the update for his return from sources: what waits on him with its buttons, what landed and is live, what broke and who is on it, by workstream, and every plan step's owner, since the update he was last sent or --since",
		Args:  cobra.NoArgs,
		RunE: func(c *cobra.Command, _ []string) error {
			opened, err := open(a, driveName)
			if err != nil {
				return err
			}
			defer opened.store.Close()
			if *since != "" {
				at, err := sinceOf(*since, a.Now())
				if err != nil {
					return err
				}
				opened.src.Before = func() (Snapshot, error) { return Snapshot{At: at}, nil }
			}
			in, err := Collect(c.Context(), opened.src, opened.entry.Bus(), a.Now())
			if err != nil {
				return err
			}
			fmt.Fprint(c.OutOrStdout(), Away(in))
			return nil
		},
	}
	cmd.AddCommand(render, check, status, away, pinCommand(&driveName))
	cmd.Args = cobra.NoArgs
	cmd.Flags().BoolVar(&rendering, "render", false, "the render verb, under the flag owner_update.py callers name")
	cmd.Flags().StringVar(&checking, "check", "", "the check verb on this file, under the flag owner_update.py callers name")
	cmd.RunE = func(c *cobra.Command, _ []string) error {
		switch {
		case rendering:
			return render.RunE(c, nil)
		case checking != "":
			return check.RunE(c, []string{checking})
		}
		return status.RunE(c, nil)
	}
	return cmd
}

func statusCommand(a *app.App, driveName *string, parent *cobra.Command) (*cobra.Command, *string) {
	var sinceText string
	var kinds, only []string
	var asJSON bool
	status := &cobra.Command{
		Use:   "status",
		Short: "One line per open ask, in-flight task, delivery, board row and incident",
		Args:  cobra.NoArgs,
		RunE: func(c *cobra.Command, _ []string) error {
			now := a.Now()
			since := now.Add(-defaultSince)
			if sinceText != "" {
				parsed, err := sinceOf(sinceText, now)
				if err != nil {
					return err
				}
				since = parsed
			}
			opened, err := open(a, *driveName)
			if err != nil {
				return err
			}
			defer opened.store.Close()
			found, err := ReadStatus(c.Context(), opened.src, now, since, kinds, only)
			if err != nil {
				return err
			}
			if asJSON {
				return json.NewEncoder(c.OutOrStdout()).Encode(found)
			}
			lines := found.Lines()
			if len(lines) == 0 {
				lines = []string{"nothing open"}
			}
			fmt.Fprintln(c.OutOrStdout(), strings.Join(lines, "\n"))
			return nil
		},
	}
	parent.PersistentFlags().StringVar(&sinceText, "since", "", "with status or away: an RFC 3339 time, a duration such as 8h, or a Pacific clock time such as 11:30 PM; status counts what was delivered after it (default: 2 h ago), away reads from it (default: the update he was last sent)")
	parent.PersistentFlags().StringArrayVar(&kinds, "kind", nil, "with status: a record kind that counts as a delivery, repeatable")
	parent.PersistentFlags().StringArrayVar(&only, "only", nil, "with status: read only this section, repeatable")
	parent.PersistentFlags().BoolVar(&asJSON, "json", false, "with status: print the sections as one JSON object")
	return status, &sinceText
}
