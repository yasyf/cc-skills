package deskcreds

import (
	"context"
	"errors"
	"fmt"
	"os"
	"sync"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/roll"
)

func Label(drive string) string {
	return "com.long-running." + drive + ".desk-creds"
}

func (e Env) Keep(ctx context.Context, config string, port int) error {
	runner, err := readRunner(config)
	if err != nil {
		return err
	}
	taking, err := roll.Watch(ctx)
	if err != nil {
		return err
	}
	context.AfterFunc(taking, func() { e.sayStale(ctx, runner.Drive, context.Cause(taking)) })
	var pushing sync.Mutex
	push := func() error {
		pushing.Lock()
		defer pushing.Unlock()
		return e.Push(ctx, config, port)
	}
	go func() {
		for {
			_ = push()
			select {
			case <-ctx.Done():
				return
			case <-time.After(pushEvery):
			}
		}
	}()
	return client.Follow(ctx, client.Watch{Drive: runner.Drive, Reader: PushLane}, func(ask client.Record) error {
		if ask.Kind != askKind {
			return nil
		}
		if err := push(); err != nil {
			_, err := client.Post(ctx, client.Draft{
				Drive: runner.Drive, Lane: PushLane, Kind: "blocker", To: []string{"root"}, Re: ask.Seq, Force: true,
				Text: fmt.Sprintf("desk-creds on %s holds nothing and the push failed: %v; the Mac's %s log names each credential", runner.Host.Sprite, err, Label(runner.Drive)),
			})
			return err
		}
		_, err := client.Post(ctx, client.Draft{
			Drive: runner.Drive, Lane: PushLane, Kind: "answer", To: []string{ask.Lane}, Resolves: ask.Seq, Force: true,
			Text: fmt.Sprintf("pushed every credential to %s", runner.Host.Sprite),
		})
		return err
	})
}

func (e Env) sayStale(ctx context.Context, driveName string, why error) {
	var moved roll.Moved
	if !errors.As(why, &moved) {
		_, _ = fmt.Fprintf(e.Err, "desk-creds keep stopped following the lr install link: %v\n", why)
		return
	}
	_, err := client.Post(ctx, client.Draft{
		Drive: driveName, Lane: PushLane, Kind: "state", To: []string{"root"}, ID: "desk-creds-stale:" + moved.To, Force: true,
		Text: fmt.Sprintf("desk-creds keep still runs lr %s: %v. Its start reads the keychain and can raise a Touch ID prompt, so it never restarts itself; `launchctl kickstart -k gui/%d/%s` while he is at the unlocked Mac moves it onto %s", moved.From, moved, os.Getuid(), Label(driveName), moved.To),
	})
	if err != nil {
		_, _ = fmt.Fprintf(e.Err, "desk-creds keep did not post that it runs a stale lr: %v\n", err)
	}
}
