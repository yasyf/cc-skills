package daemon

import (
	"context"
	"fmt"
	"net"

	dkdaemon "github.com/yasyf/daemonkit/daemon"
	"github.com/yasyf/daemonkit/wire"
	"github.com/yasyf/daemonkit/wire/lifeproto"
)

// socketPeer adapts a running {{PROJECT_NAME}}d over its lifeproto unix socket to
// the daemonkit daemon.Peer interface, so a successor's takeover and the status
// command speak the one frozen protocol.
type socketPeer struct{ socket string }

var _ dkdaemon.Peer = (*socketPeer)(nil)

func (p *socketPeer) dial(ctx context.Context) (*wire.Framing, net.Conn, error) {
	var d net.Dialer
	conn, err := d.DialContext(ctx, "unix", p.socket)
	if err != nil {
		return nil, nil, fmt.Errorf("dial %s: %w", p.socket, err)
	}
	return wire.NewFraming(conn), conn, nil
}

// Health probes the peer's lifeproto health snapshot.
func (p *socketPeer) Health(ctx context.Context) (dkdaemon.Health, error) {
	f, conn, err := p.dial(ctx)
	if err != nil {
		return dkdaemon.Health{}, err
	}
	defer conn.Close()
	if err := lifeproto.Write(f, lifeproto.NewHealthRequest()); err != nil {
		return dkdaemon.Health{}, err
	}
	var resp lifeproto.HealthResponse
	if err := f.ReadJSON(&resp); err != nil {
		return dkdaemon.Health{}, fmt.Errorf("read health: %w", err)
	}
	return dkdaemon.Health{
		Version:  resp.Version,
		PID:      resp.PID,
		State:    dkdaemon.State(resp.State),
		Draining: resp.Draining,
		Busy:     resp.Busy,
		Features: resp.Features,
	}, nil
}

// Shutdown asks the peer to exit.
func (p *socketPeer) Shutdown(ctx context.Context) error {
	f, conn, err := p.dial(ctx)
	if err != nil {
		return err
	}
	defer conn.Close()
	if err := lifeproto.Write(f, lifeproto.NewShutdownRequest()); err != nil {
		return err
	}
	var resp lifeproto.ShutdownResponse
	return f.ReadJSON(&resp)
}

// Handoff asks the peer to release its socket for a successor.
func (p *socketPeer) Handoff(ctx context.Context) error {
	f, conn, err := p.dial(ctx)
	if err != nil {
		return err
	}
	defer conn.Close()
	if err := lifeproto.Write(f, lifeproto.NewHandoffRequest()); err != nil {
		return err
	}
	var resp lifeproto.HandoffResponse
	return f.ReadJSON(&resp)
}
