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
)

const defaultSince = 2 * time.Hour

func missingError(left []string) error {
	if len(left) == 0 {
		return nil
	}
	return fmt.Errorf("missing: %d\n%s", len(left), strings.Join(left, "\n"))
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
	var publish, sent bool
	render := &cobra.Command{
		Use:   "render",
		Short: "Print the whole owner update and exit 1 naming what it leaves out and each gap",
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
			return missingError(append(Missing(found.Text, found.Needs), found.Gaps...))
		},
	}
	render.Flags().BoolVar(&publish, "publish", false, "write the dashboard's steps.json from the same rows")
	render.Flags().BoolVar(&sent, "sent", false, "publish, and save this render as the update the owner is sent, which the next one diffs against")
	cmd.AddCommand(render)
	cmd.AddCommand(&cobra.Command{
		Use:   "check FILE",
		Short: "Exit 1 naming each section, lane, ask and PR a written update leaves out; - reads stdin",
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
			return missingError(Missing(string(written), found.Needs))
		},
	})
	cmd.AddCommand(statusCommand(a, &driveName))
	return cmd
}

func statusCommand(a *app.App, driveName *string) *cobra.Command {
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
				parsed, err := time.Parse(time.RFC3339, sinceText)
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
	status.Flags().StringVar(&sinceText, "since", "", "an RFC 3339 time; delivered records after it count (default: 2 h ago)")
	status.Flags().StringArrayVar(&kinds, "kind", nil, "a record kind that counts as a delivery, repeatable")
	status.Flags().StringArrayVar(&only, "only", nil, "read only this section, repeatable")
	status.Flags().BoolVar(&asJSON, "json", false, "print the sections as one JSON object")
	return status
}
