package daemon

import (
	"context"
	"fmt"
	"io"

	"github.com/spf13/cobra"
	"github.com/yasyf/daemonkit/paths"
	"github.com/yasyf/daemonkit/service"
)

// serviceLabel is the launchd label / reverse-DNS identifier for the agent.
// TODO(bootstrap): set your reverse-DNS domain.
const serviceLabel = "com.example." + appName

func agent() service.Agent {
	return service.Agent{
		Label:         serviceLabel,
		Formula:       appName,
		Args:          []string{"serve"},
		LogPath:       paths.Paths{App: appName}.LogPath(),
{{#MODE_CLIENT_SPAWN}}
		RestartPolicy: service.NoRestart,
{{/MODE_CLIENT_SPAWN}}
{{#MODE_LAUNCHD}}
		RestartPolicy: service.RestartAlways,
{{/MODE_LAUNCHD}}
	}
}

func newServiceCmd() *cobra.Command {
	cmd := &cobra.Command{Use: "service", Short: "Manage the launchd registration"}
	cmd.AddCommand(
		&cobra.Command{
			Use:   "install",
			Short: "Register and start the LaunchAgent",
			RunE:  func(c *cobra.Command, _ []string) error { return agent().Install(c.Context()) },
		},
		&cobra.Command{
			Use:   "uninstall",
			Short: "Bootout and remove the LaunchAgent",
			RunE:  func(c *cobra.Command, _ []string) error { return agent().Uninstall(c.Context()) },
		},
		&cobra.Command{
			Use:   "status",
			Short: "Report the LaunchAgent and daemon status",
			RunE:  func(c *cobra.Command, _ []string) error { return status(c.Context(), c.OutOrStdout()) },
		},
	)
	return cmd
}

// status prints the launchd registration lines then the live daemon's health.
func status(ctx context.Context, out io.Writer) error {
	for _, line := range agent().StatusLines(ctx) {
		fmt.Fprintln(out, line)
	}
	socket := paths.Paths{App: appName}.SocketPath()
	peer := lifecyclePeer(socket, selfVersion())
	defer peer.Close()
	h, err := peer.Health(ctx)
	if err != nil {
		fmt.Fprintf(out, "daemon: unreachable (%v)\n", err)
		return nil
	}
	fmt.Fprintf(out, "daemon: %s pid=%d state=%s\n", h.Build, h.PID, h.State)
	return nil
}
