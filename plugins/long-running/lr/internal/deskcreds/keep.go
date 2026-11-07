package deskcreds

import (
	"context"
	"fmt"
	"slices"
	"strings"
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
	asked := map[string]bool{}
	pushTo := func(host Host, sprite string) error {
		host.Sprite = sprite
		gone, err := e.push(ctx, host, port)
		if gone {
			delete(asked, sprite)
		}
		return err
	}
	go func() {
		for {
			pushing.Lock()
			if current, err := readRunner(config); err == nil {
				_ = pushTo(current.Host, current.Host.Sprite)
				for _, sprite := range sortedKeys(asked) {
					if sprite != current.Host.Sprite {
						_ = pushTo(current.Host, sprite)
					}
				}
			}
			pushing.Unlock()
			select {
			case <-ctx.Done():
				return
			case <-time.After(pushEvery):
			}
		}
	}()
	return client.Follow(ctx, client.Watch{Drive: runner.Drive, Reader: PushLane}, func(ask client.Record) error {
		rest, named := strings.CutPrefix(ask.ID, startPrefix)
		sprite, _, _ := strings.Cut(rest, "/")
		if ask.Kind != askKind || !named || sprite == "" || !slices.Contains(ask.To, PushLane) {
			return nil
		}
		pushing.Lock()
		asked[sprite] = true
		current, err := readRunner(config)
		if err == nil {
			err = pushTo(current.Host, sprite)
		}
		pushing.Unlock()
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
