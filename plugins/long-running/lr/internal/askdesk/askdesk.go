package askdesk

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

	"github.com/yasyf/cc-inbox/client"
)

const (
	AskKind      = "ask"
	AnswerKind   = "answer"
	PartKind     = "note"
	StandingAttr = "standing"
	CatchUp      = time.Hour
	claimKind    = "state"
	claimEvery   = 5 * time.Minute
	openStatus   = "open"
	pollEvery    = 2 * time.Second
	relayBody    = 64 << 10
	partAttr     = "part"
	partsAttr    = "parts"

	refollowAfter = 10 * time.Second
)

type Reply struct {
	Head, Body, URL string
	Exit            int
	Stderr          string
	Refused         bool
}

type Desk struct {
	Lane   string
	Drive  string
	Duty   string
	Bodies string
	Now    func() time.Time
	Log    io.Writer
	Read   func(context.Context, string) Reply
	Apart  func(client.Record) bool
}

func (d Desk) holdDuty(ctx context.Context) (release func()) {
	ctx, cancel := context.WithCancel(ctx)
	released := make(chan struct{})
	claim := client.Draft{Drive: d.Drive, Lane: d.Lane, Kind: claimKind, Text: d.Lane + " serves: " + d.Duty, Attrs: map[string]string{StandingAttr: d.Duty}}
	go func() {
		defer close(released)
		for {
			if _, err := client.Post(ctx, claim); err != nil && ctx.Err() == nil {
				_, _ = fmt.Fprintf(d.Log, "%s did not claim its standing duty this beat: %v\n", d.Lane, err)
			}
			select {
			case <-ctx.Done():
				return
			case <-time.After(claimEvery):
			}
		}
	}()
	return func() {
		cancel()
		<-released
	}
}

func (d Desk) Serve(ctx context.Context) error {
	defer d.holdDuty(ctx)()
	query := client.Query{Drive: d.Drive, Since: d.Now().Add(-CatchUp), Kinds: []string{AskKind}}
	for {
		next, err := d.Pass(ctx, query)
		if err != nil {
			return err
		}
		query = next
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(pollEvery):
		}
	}
}

func (d Desk) Follow(ctx context.Context) {
	defer d.holdDuty(ctx)()
	if d.Apart == nil {
		d.keepFollowing(ctx, every)
		return
	}
	var wg sync.WaitGroup
	for _, apart := range []bool{true, false} {
		wg.Go(func() {
			d.keepFollowing(ctx, func(ask client.Record) bool { return d.Apart(ask) == apart })
		})
	}
	wg.Wait()
}

func (d Desk) keepFollowing(ctx context.Context, takes func(client.Record) bool) {
	for ctx.Err() == nil {
		err := d.follow(ctx, takes)
		if ctx.Err() != nil {
			return
		}
		_, _ = fmt.Fprintf(d.Log, "%s stopped following its asks and follows again: %v\n", d.Lane, err)
		select {
		case <-ctx.Done():
		case <-time.After(refollowAfter):
		}
	}
}

func (d Desk) follow(ctx context.Context, takes func(client.Record) bool) error {
	caught, err := d.pass(ctx, client.Query{Drive: d.Drive, Since: d.Now().Add(-CatchUp), Kinds: []string{AskKind}}, takes)
	if err != nil {
		return err
	}
	return client.Follow(ctx, client.Watch{Drive: d.Drive, Reader: d.Lane, After: caught.After}, func(ask client.Record) error {
		if string(ask.Kind) != AskKind || !slices.Contains(ask.To, d.Lane) || !takes(ask) {
			return nil
		}
		if err := d.answer(ctx, ask); err != nil {
			_, _ = fmt.Fprintf(d.Log, "%s: ask %s from %s stays open: %v\n", d.Lane, ask.Name(), ask.Lane, err)
		}
		return nil
	})
}

func (d Desk) Pass(ctx context.Context, query client.Query) (client.Query, error) {
	return d.pass(ctx, query, every)
}

func every(client.Record) bool { return true }

func (d Desk) pass(ctx context.Context, query client.Query, takes func(client.Record) bool) (client.Query, error) {
	asks, err := client.Tail(ctx, query)
	if err != nil {
		return query, err
	}
	for _, ask := range asks {
		query = client.Query{Drive: query.Drive, After: ask.Seq, Kinds: query.Kinds}
		if ask.Status != openStatus || !slices.Contains(ask.To, d.Lane) || !takes(ask) {
			continue
		}
		if err := d.answer(ctx, ask); err != nil {
			_, _ = fmt.Fprintf(d.Log, "%s: ask %s from %s stays open: %v\n", d.Lane, ask.Name(), ask.Lane, err)
		}
	}
	return query, nil
}

func (d Desk) answer(ctx context.Context, ask client.Record) error {
	asked, err := client.WholeText(ask)
	if err != nil {
		return err
	}
	read := d.Read(ctx, asked)
	answer := client.Draft{
		Drive: d.Drive, Lane: d.Lane, Kind: AnswerKind, Text: read.Head, To: []string{ask.Lane},
		Re: ask.Seq, Resolves: ask.Seq, URL: read.URL, Force: true, Attrs: map[string]string{},
	}
	if read.Refused {
		answer.Kind, answer.Resolves = PartKind, 0
	}
	if read.Exit != 0 || read.Stderr != "" {
		answer.Attrs[exitAttr], answer.Attrs[stderrAttr] = strconv.Itoa(read.Exit), read.Stderr
	}
	if read.Body != "" {
		parts := relayParts(read.Body)
		for n, part := range parts[1:] {
			path, err := d.bodyFile(part)
			if err != nil {
				return err
			}
			if _, err := client.Post(ctx, client.Draft{
				Drive: d.Drive, Lane: d.Lane, Kind: PartKind, Text: fmt.Sprintf("part %d of %d of the answer to ask %s", n+2, len(parts), ask.Name()),
				To: []string{ask.Lane}, Re: ask.Seq, Path: path, Attrs: map[string]string{partAttr: strconv.Itoa(n + 2)}, Force: true,
			}); err != nil {
				return err
			}
		}
		if len(parts) > 1 {
			answer.Attrs[partsAttr] = strconv.Itoa(len(parts))
		}
		if answer.Path, err = d.bodyFile(parts[0]); err != nil {
			return err
		}
	}
	_, err = client.Post(ctx, answer)
	return err
}

func (d Desk) bodyFile(body string) (string, error) {
	sum := sha256.Sum256([]byte(body))
	path := filepath.Join(d.Bodies, hex.EncodeToString(sum[:])[:16]+".txt")
	if err := os.MkdirAll(d.Bodies, 0o750); err != nil {
		return "", err
	}
	return path, os.WriteFile(path, []byte(body), 0o600)
}

func relayParts(body string) []string {
	var parts []string
	for len(body) > relayBody {
		cut := relayBody
		for !utf8.RuneStart(body[cut]) {
			cut--
		}
		parts, body = append(parts, body[:cut]), body[cut:]
	}
	return append(parts, body)
}

func FirstBytes(text string, limit int) string {
	if len(text) <= limit {
		return text
	}
	cut := strings.ToValidUTF8(text[:limit], "")
	return fmt.Sprintf("%s\n… cut: the first %d of %d bytes\n", cut, len(cut), len(text))
}

func LastBytes(text string, limit int) string {
	if len(text) <= limit {
		return text
	}
	cut := strings.ToValidUTF8(text[len(text)-limit:], "")
	return fmt.Sprintf("… cut: the last %d of %d bytes\n%s", len(cut), len(text), cut)
}
