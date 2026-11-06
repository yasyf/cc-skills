package askdesk

import (
	"bytes"
	"cmp"
	"context"
	"errors"
	"fmt"
	"io"
	"maps"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
)

const (
	StoreDesk  = "store-desk"
	answerWait = 3 * time.Minute
	failedRead = "store read failed: "
)

var errAnswered = errors.New("answered")

type Serve func(ctx context.Context, args []string, out, errs io.Writer) error

type CCI interface {
	Post(context.Context, client.Draft) (client.Record, error)
	Follow(context.Context, client.Watch, func(client.Record) error) error
}

type LiveCCI struct{}

func (LiveCCI) Post(ctx context.Context, d client.Draft) (client.Record, error) {
	return client.Post(ctx, d)
}

func (LiveCCI) Follow(ctx context.Context, w client.Watch, emit func(client.Record) error) error {
	return client.Follow(ctx, w, emit)
}

func StoreReads(reads map[string]Serve) func(context.Context, string) Reply {
	return func(ctx context.Context, text string) Reply {
		fields := strings.Fields(strings.TrimPrefix(strings.TrimSpace(text), "lr "))
		for name, serve := range reads {
			words := strings.Fields(name)
			if len(fields) < len(words) || !slices.Equal(fields[:len(words)], words) {
				continue
			}
			var out, said bytes.Buffer
			if err := serve(ctx, fields[len(words):], &out, &said); err != nil {
				return Reply{Head: failedRead + err.Error(), Body: said.String() + err.Error()}
			}
			return Reply{Head: fmt.Sprintf("%s: %d lines", strings.Join(fields, " "), strings.Count(out.String(), "\n")), Body: out.String()}
		}
		why := "the store desk serves only lr " + strings.Join(slices.Sorted(maps.Keys(reads)), ", lr ")
		return Reply{Head: failedRead + why, Body: why}
	}
}

func AskMac(ctx context.Context, bus CCI, drive, lane, read string, out io.Writer) error {
	asked, err := bus.Post(ctx, client.Draft{Drive: drive, Lane: lane, Kind: AskKind, Text: read, To: []string{StoreDesk}, Force: true})
	if err != nil {
		return err
	}
	waiting, stop := context.WithTimeout(ctx, answerWait)
	defer stop()
	var answer client.Record
	err = bus.Follow(waiting, client.Watch{Drive: drive, Reader: client.Stem(lane), After: asked.Seq}, func(r client.Record) error {
		if r.Resolves != asked.Seq {
			return nil
		}
		answer = r
		return errAnswered
	})
	if err != nil && !errors.Is(err, errAnswered) {
		return err
	}
	if answer.Seq == 0 {
		return fmt.Errorf("this Sprite holds no copy of the drive's store, and the Mac's %s has not answered ask %s in %s: the supervisor there is down or runs an lr older than the store desk", StoreDesk, asked.Name(), answerWait)
	}
	whole, err := client.WholeText(answer)
	if err != nil {
		return err
	}
	body := strings.TrimPrefix(strings.TrimPrefix(whole, answer.Text), "\n")
	if failed, found := strings.CutPrefix(answer.Text, failedRead); found {
		return fmt.Errorf("the Mac's %s: %s", StoreDesk, cmp.Or(body, failed))
	}
	_, err = io.WriteString(out, body)
	return err
}
