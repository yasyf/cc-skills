package daemon

import (
	"context"
	"sync"

	dkdaemon "github.com/yasyf/daemonkit/daemon"
	"github.com/yasyf/daemonkit/drain"
	"github.com/yasyf/daemonkit/trust"
	"github.com/yasyf/daemonkit/wire"
)

type runtimeBundle struct {
	daemon  *dkdaemon.Runtime
	server  *wire.Server
	workers *runtimeWorkers
}

func newRuntime(ctx context.Context, socket, build string) (*runtimeBundle, error) {
	intake := &drain.Intake{}
	workers := newRuntimeWorkers(ctx)
	server := &wire.Server{
		Build: build,
		Trust: (trust.Policy{}).Check,
	}
	peer := lifecyclePeer(socket, build)
	runtime, err := dkdaemon.NewRuntime(dkdaemon.RuntimeConfig{
		Socket:    socket,
		Build:     build,
		Protocol:  protocolVersion,
		Peer:      peer,
		Contract:  dkdaemon.RequestDaemon,
		WaitMode:  dkdaemon.PIDExit,
		Admission: intake,
		Server:    server,
		Workers:   workers,
		State:     runtimeState{},
		Resources: peer,
	})
	if err != nil {
		workers.Close()
		workers.Cancel()
		_ = workers.Wait(context.Background())
		_ = peer.Close()
		return nil, err
	}
	server.RegisterLifecycle(runtime)
	return &runtimeBundle{daemon: runtime, server: server, workers: workers}, nil
}

func lifecyclePeer(socket, build string) *wire.LifecyclePeer {
	return &wire.LifecyclePeer{Config: wire.ClientConfig{
		Dial:  wire.UnixDialer(socket),
		Build: build,
	}}
}

type runtimeWorkers struct {
	ctx    context.Context
	cancel context.CancelFunc

	mu     sync.Mutex
	closed bool
	wg     sync.WaitGroup
}

func newRuntimeWorkers(parent context.Context) *runtimeWorkers {
	ctx, cancel := context.WithCancel(context.WithoutCancel(parent))
	return &runtimeWorkers{ctx: ctx, cancel: cancel}
}

func (w *runtimeWorkers) Start(run func(context.Context)) {
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.closed {
		panic("daemon: worker registration after intake closed")
	}
	w.wg.Add(1)
	go func() {
		defer w.wg.Done()
		run(w.ctx)
	}()
}

func (w *runtimeWorkers) Close() {
	w.mu.Lock()
	w.closed = true
	w.mu.Unlock()
}

func (w *runtimeWorkers) Cancel() { w.cancel() }

func (w *runtimeWorkers) Wait(ctx context.Context) error {
	w.wg.Wait()
	return ctx.Err()
}

type runtimeState struct{}

func (runtimeState) Close() error { return nil }
