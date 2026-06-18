// Package cli builds the cobra command tree.
package cli

import (
	"github.com/spf13/cobra"

	"{{MODULE_PATH}}/internal/version"
)

// NewRootCmd builds the root command and registers its subcommands.
func NewRootCmd() *cobra.Command {
	root := &cobra.Command{
		Use:           "{{PROJECT_NAME}}",
		Short:         "{{DESCRIPTION}}",
		Version:       version.String(),
		SilenceUsage:  true,
		SilenceErrors: true,
	}
	root.SetVersionTemplate("{{.Version}}\n")
	root.AddCommand(newHelloCmd())
	return root
}
