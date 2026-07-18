// Package daemon is the {{PROJECT_NAME}} daemon: its command tree, serve loop,
// and the daemonkit lifecycle wiring (build version, takeover / skew-watch, and
// launchd service management).
package daemon

import "github.com/spf13/cobra"

// appName is the daemonkit application identifier: it names the ~/.<app> state
// dir (paths.Paths.App), the unix socket under it, and the daemon binary.
const appName = "{{PROJECT_NAME}}"

// NewRootCmd builds the {{PROJECT_NAME}}d command tree: `serve` runs the daemon
// in the foreground (launchd and the client-spawn path both exec it), and
// `service` installs, removes, and reports the launchd registration.
func NewRootCmd() *cobra.Command {
	root := &cobra.Command{
		Use:           "{{PROJECT_NAME}}d",
		Short:         "The {{PROJECT_NAME}} daemon",
		Version:       selfVersion(),
		SilenceUsage:  true,
		SilenceErrors: true,
	}
	root.SetVersionTemplate("{{.Version}}\n")
	root.AddCommand(newServeCmd())
	root.AddCommand(newServiceCmd())
	return root
}
