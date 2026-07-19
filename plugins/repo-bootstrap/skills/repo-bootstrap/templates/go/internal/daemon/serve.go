package daemon

import (
	"context"
	"fmt"
{{#MODE_CLIENT_SPAWN}}
	"time"
{{/MODE_CLIENT_SPAWN}}

	"github.com/spf13/cobra"
{{#MODE_CLIENT_SPAWN}}
	dkdaemon "github.com/yasyf/daemonkit/daemon"
{{/MODE_CLIENT_SPAWN}}
	"github.com/yasyf/daemonkit/paths"
{{#MODE_CLIENT_SPAWN}}
	"github.com/yasyf/daemonkit/proc"
	"github.com/yasyf/daemonkit/version"
{{/MODE_CLIENT_SPAWN}}
)

{{#MODE_CLIENT_SPAWN}}
const upgradeTimeout = 30 * time.Second

{{/MODE_CLIENT_SPAWN}}
func newServeCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "serve",
		Short: "Run the {{PROJECT_NAME}} daemon in the foreground",
		RunE:  func(cmd *cobra.Command, _ []string) error { return serve(cmd.Context()) },
	}
}

// serve gives daemonkit sole ownership of listener takeover, persistent v4
// sessions, admission, and ordered shutdown.
func serve(ctx context.Context) error {
	p := paths.Paths{App: appName}
	if err := p.EnsureStateDir(); err != nil {
		return fmt.Errorf("ensure state dir: %w", err)
	}
	runtime, err := newRuntime(ctx, p.SocketPath(), selfVersion())
	if err != nil {
		return fmt.Errorf("build runtime: %w", err)
	}

{{#MODE_CLIENT_SPAWN}}
	idle := &dkdaemon.IdleExit{
		Exit: func(ctx context.Context) {
			_ = runtime.daemon.Shutdown(context.WithoutCancel(ctx))
		},
	}
	runtime.server.OnActivity(idle.Touch)
	runtime.workers.Start(idle.Run)
{{/MODE_CLIENT_SPAWN}}
	return runtime.daemon.Run(ctx)
}

{{#MODE_CLIENT_SPAWN}}
// EnsureRunning starts or upgrades the daemon and waits for the exact build and
// wire protocol before returning to the caller.
func EnsureRunning(ctx context.Context) error {
	p := paths.Paths{App: appName}
	if err := p.EnsureStateDir(); err != nil {
		return fmt.Errorf("ensure state dir: %w", err)
	}
	if err := p.EnsureLockDir(); err != nil {
		return fmt.Errorf("ensure lock dir: %w", err)
	}
	build := selfVersion()
	peer := lifecyclePeer(p.SocketPath(), build)
	defer peer.Close()
	if health, err := peer.Health(ctx); err == nil && version.Newer(health.Build, build) {
		return fmt.Errorf("daemon build %s is newer than client build %s", health.Build, build)
	}
	spawn := proc.Spawn{
		Socket:  p.SocketPath(),
		LogPath: p.LogPath(),
		Args:    []string{"serve"},
		Timeout: upgradeTimeout,
		Available: func() bool {
			probeCtx, cancel := context.WithTimeout(context.Background(), 500*time.Millisecond)
			defer cancel()
			health, err := peer.Health(probeCtx)
			return err == nil && health.Build == build && health.Protocol == protocolVersion
		},
		CanHost: func() error { return nil },
	}
	return dkdaemon.EnsureCurrent(ctx, dkdaemon.EnsureConfig{
		Peer: peer, Protocol: protocolVersion, LockPath: p.StartLockPath(),
		Ensure: spawn.EnsureRunning, Timeout: upgradeTimeout,
	}, build)
}
{{/MODE_CLIENT_SPAWN}}
