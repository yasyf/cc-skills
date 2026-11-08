package board

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"strings"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

func CardCommand(a *app.App) *cobra.Command {
	cmd := &cobra.Command{Use: "card", Short: "His PR card, checked before it is drawn"}
	var bodyFile, pr string
	check := &cobra.Command{
		Use:   "check",
		Short: "Exit 1 naming each line his PR card cannot draw from a PR body, the check lr reply runs before his Approve: a Summary in user words, a Plan: line and a Context section",
		Args:  cobra.NoArgs,
		RunE: func(c *cobra.Command, _ []string) error {
			var body string
			switch {
			case bodyFile == "-":
				raw, err := io.ReadAll(c.InOrStdin())
				if err != nil {
					return err
				}
				body = string(raw)
			case bodyFile != "":
				raw, err := os.ReadFile(bodyFile)
				if err != nil {
					return err
				}
				body = string(raw)
			case pr != "":
				done, err := a.Runner.Run(c.Context(), shell.Command{Argv: []string{"gh", "pr", "view", pr, "--json", "body"}})
				if err != nil {
					return err
				}
				var read struct{ Body string }
				if err := json.Unmarshal([]byte(done.Stdout), &read); err != nil {
					return err
				}
				body = read.Body
			default:
				return errors.New("name the body with --body-file or the PR with --pr")
			}
			gaps := prrow.BodyGaps(body)
			if len(gaps) == 0 {
				return nil
			}
			return fmt.Errorf("PR CARD GAP: his card cannot draw %s; fix the PR body before it reaches him", strings.Join(gaps, "; "))
		},
	}
	check.Flags().StringVar(&bodyFile, "body-file", "", "the PR body to check, - for stdin")
	check.Flags().StringVar(&pr, "pr", "", "the PR to read the body of, by number or URL")
	cmd.AddCommand(check)
	return cmd
}
