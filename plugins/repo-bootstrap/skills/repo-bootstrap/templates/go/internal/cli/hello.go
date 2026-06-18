package cli

import (
	"log/slog"

	"github.com/spf13/cobra"
)

// newHelloCmd is the starter command — a placeholder. Replace it with real
// commands; building the product begins after the repo is scaffolded.
func newHelloCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "hello",
		Short: "Print a greeting — the starter command",
		RunE: func(cmd *cobra.Command, _ []string) error {
			slog.Debug("hello invoked")
			cmd.Println("Hello from {{PROJECT_NAME}}!")
			return nil
		},
	}
}
