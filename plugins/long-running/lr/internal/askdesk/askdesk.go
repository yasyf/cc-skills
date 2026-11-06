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
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
)

const (
	AskKind    = "ask"
	AnswerKind = "answer"
	CatchUp    = time.Hour
	openStatus = "open"
	pollEvery  = 2 * time.Second

	refollowAfter = 10 * time.Second
)

type Reply struct {
	Head, Body, URL string
}

type Desk struct {
	Lane   string
	Drive  string
	Bodies string
	Now    func() time.Time
	Log    io.Writer
	Read   func(context.Context, string) Reply
}

func (d Desk) Serve(ctx context.Context) error {
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
	for ctx.Err() == nil {
		err := d.follow(ctx)
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

func (d Desk) follow(ctx context.Context) error {
	caught, err := d.Pass(ctx, client.Query{Drive: d.Drive, Since: d.Now().Add(-CatchUp), Kinds: []string{AskKind}})
	if err != nil {
		return err
	}
	return client.Follow(ctx, client.Watch{Drive: d.Drive, Reader: d.Lane, After: caught.After}, func(ask client.Record) error {
		if string(ask.Kind) != AskKind || !slices.Contains(ask.To, d.Lane) {
			return nil
		}
		if err := d.answer(ctx, ask); err != nil {
			_, _ = fmt.Fprintf(d.Log, "%s: ask #%d from %s stays open: %v\n", d.Lane, ask.Seq, ask.Lane, err)
		}
		return nil
	})
}

func (d Desk) Pass(ctx context.Context, query client.Query) (client.Query, error) {
	asks, err := client.Tail(ctx, query)
	if err != nil {
		return query, err
	}
	for _, ask := range asks {
		query = client.Query{Drive: query.Drive, After: ask.Seq, Kinds: query.Kinds}
		if ask.Status != openStatus || !slices.Contains(ask.To, d.Lane) {
			continue
		}
		if err := d.answer(ctx, ask); err != nil {
			_, _ = fmt.Fprintf(d.Log, "%s: ask #%d from %s stays open: %v\n", d.Lane, ask.Seq, ask.Lane, err)
		}
	}
	return query, nil
}

func (d Desk) answer(ctx context.Context, ask client.Record) error {
	read := d.Read(ctx, ask.Text)
	answer := client.Draft{
		Drive: d.Drive, Lane: d.Lane, Kind: AnswerKind, Text: read.Head, To: []string{ask.Lane},
		Resolves: ask.Seq, URL: read.URL, Force: true,
	}
	if read.Body != "" {
		sum := sha256.Sum256([]byte(read.Body))
		answer.Path = filepath.Join(d.Bodies, hex.EncodeToString(sum[:])[:16]+".txt")
		if err := os.MkdirAll(d.Bodies, 0o750); err != nil {
			return err
		}
		if err := os.WriteFile(answer.Path, []byte(read.Body), 0o600); err != nil {
			return err
		}
	}
	_, err := client.Post(ctx, answer)
	return err
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
