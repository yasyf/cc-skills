package deskcreds

import (
	"context"
	"fmt"
	"slices"
	"sync"
	"time"

	"github.com/yasyf/cc-inbox/client"
)

func Label(drive string) string {
	return "com.long-running." + drive + ".desk-creds"
}

func (e Env) Keep(ctx context.Context, config string, port int) error {
	runner, err := readRunner(config)
	if err != nil {
		return err
	}
	var pushing sync.Mutex
	push := func() (string, error) {
		pushing.Lock()
		defer pushing.Unlock()
		return e.Push(ctx, config, port)
	}
	go func() {
		for {
			_, _ = push()
			select {
			case <-ctx.Done():
				return
			case <-time.After(pushEvery):
			}
		}
	}()
	return client.Follow(ctx, client.Watch{Drive: runner.Drive, Reader: PushLane}, func(ask client.Record) error {
		if ask.Kind != askKind || !slices.Contains(ask.To, PushLane) {
			return nil
		}
		sprite, err := push()
		if err != nil {
			_, err := client.Post(ctx, client.Draft{
				Drive: runner.Drive, Lane: PushLane, Kind: "blocker", To: []string{"root"}, Re: ask.Seq, Force: true,
				Text: fmt.Sprintf("%v; the Mac's %s log says why for each credential", err, Label(runner.Drive)),
			})
			return err
		}
		_, err = client.Post(ctx, client.Draft{
			Drive: runner.Drive, Lane: PushLane, Kind: "answer", To: []string{ask.Lane}, Resolves: ask.Seq, Force: true,
			Text: fmt.Sprintf("pushed every credential to %s", sprite),
		})
		return err
	})
}
