package daemon

import (
	"context"
	"fmt"
	"net"
	"os"

	"github.com/spf13/cobra"
	dkdaemon "github.com/yasyf/daemonkit/daemon"
	"github.com/yasyf/daemonkit/paths"
	"github.com/yasyf/daemonkit/proc"
	"github.com/yasyf/daemonkit/wire"
	"github.com/yasyf/daemonkit/wire/lifeproto"
)

func newServeCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "serve",
		Short: "Run the {{PROJECT_NAME}} daemon in the foreground",
		RunE:  func(cmd *cobra.Command, _ []string) error { return serve(cmd.Context()) },
	}
}

// serve binds the daemon socket and answers the lifeproto lifecycle until ctx
// ends, resolving version skew per the launchd mode chosen at scaffold time.
func serve(ctx context.Context) error {
	p := paths.Paths{App: appName}
	if err := p.EnsureStateDir(); err != nil {
		return fmt.Errorf("ensure state dir: %w", err)
	}
	self := selfVersion()
	socket := p.SocketPath()

{{#MODE_CLIENT_SPAWN}}
	// Client-spawn: a strictly-newer successor takes over an older incumbent
	// before binding; a tie exits (never evicts).
	peer := &socketPeer{socket: socket}
	switch outcome, err := dkdaemon.Run(ctx, dkdaemon.TakeoverConfig{
		Self:     self,
		Peer:     peer,
		Contract: dkdaemon.RequestDaemon,
		WaitMode: dkdaemon.SocketRelease,
	}); {
	case err != nil:
		return fmt.Errorf("takeover: %w", err)
	case outcome == dkdaemon.ExitSelf:
		return nil
	}
{{/MODE_CLIENT_SPAWN}}

	ln, lock, err := proc.SingleEntrant{
		Socket: socket,
		Evict:  func() (bool, error) { return true, nil },
	}.Listen(ctx)
	if err != nil {
		return fmt.Errorf("bind %s: %w", socket, err)
	}
	defer lock.Close()
	defer ln.Close()

{{#MODE_CLIENT_SPAWN}}
	// Retire the daemon once idle: a client-spawn daemon is respawned on demand.
	idle := &dkdaemon.IdleExit{Exit: func(context.Context) { _ = ln.Close() }}
	go idle.Run(ctx)
{{/MODE_CLIENT_SPAWN}}
{{#MODE_LAUNCHD}}
	// launchd holds the old binary alive across an upgrade, so the incumbent
	// watches for a newer installed artifact and drains on skew.
	// TODO(bootstrap): point Installed at the real installed version.
	watch := dkdaemon.NewSkewWatch(dkdaemon.SkewConfig{
		Running:   func() string { return self },
		Installed: func() (string, error) { return self, nil },
		OnSkew:    func(context.Context) error { return fmt.Errorf("{{PROJECT_NAME}}d: newer artifact installed; exiting for launchd relaunch") },
	})
	go func() { _ = watch.Run(ctx) }()
{{/MODE_LAUNCHD}}

	return serveLifecycle(ctx, ln, self)
}

{{#MODE_CLIENT_SPAWN}}
// EnsureRunning ensures a {{PROJECT_NAME}}d serves the socket, spawning a
// detached one when needed. A client (your CLI) calls this before dialing.
// TODO(bootstrap): call this from the CLI's client path.
func EnsureRunning(ctx context.Context) error {
	p := paths.Paths{App: appName}
	if err := p.EnsureStateDir(); err != nil {
		return fmt.Errorf("ensure state dir: %w", err)
	}
	socket := p.SocketPath()
	return proc.Spawn{
		Socket:  socket,
		LogPath: p.LogPath(),
		Args:    []string{"serve"},
		Available: func() bool {
			c, err := net.Dial("unix", socket)
			if err != nil {
				return false
			}
			_ = c.Close()
			return true
		},
		CanHost: func() error { return nil },
	}.EnsureRunning(ctx)
}
{{/MODE_CLIENT_SPAWN}}

// serveLifecycle accepts connections on ln and answers each with one lifeproto
// exchange until ctx is done.
func serveLifecycle(ctx context.Context, ln net.Listener, self string) error {
	go func() {
		<-ctx.Done()
		_ = ln.Close()
	}()
	for {
		conn, err := ln.Accept()
		if err != nil {
			if ctx.Err() != nil {
				return ctx.Err()
			}
			return fmt.Errorf("accept: %w", err)
		}
		go handleConn(conn, self)
	}
}

// handleConn answers one lifeproto request. TODO(bootstrap): enforce trust
// (trust.Policy.Check on the peer creds) and wire real shutdown/handoff.
func handleConn(conn net.Conn, self string) {
	defer conn.Close()
	f := wire.NewFraming(conn)
	env, _, err := lifeproto.ReadEnvelope(f)
	if err != nil {
		return
	}
	var resp any
	switch env.Op {
	case lifeproto.OpHealth:
		resp = lifeproto.NewHealthResponse(self, os.Getpid(), string(dkdaemon.StateHealthy), false, false, features())
	case lifeproto.OpHello:
		resp = lifeproto.NewHelloResponse(features())
	case lifeproto.OpShutdown:
		resp = lifeproto.NewShutdownResponse(true)
	case lifeproto.OpHandoff:
		resp = lifeproto.NewHandoffResponse(false)
	default:
		return
	}
	_ = lifeproto.Write(f, resp)
}

// features lists the lifecycle capabilities this daemon advertises; a successor
// consults these bits, never a version compare, before requesting a handoff.
// TODO(bootstrap): add dkdaemon.FeatureHandoff once handoff releases the socket.
func features() []string { return nil }
