package board

import (
	"context"
	"fmt"
	"slices"
	"strconv"
	"time"

	"github.com/yasyf/cc-inbox/client"
)

const (
	staleTopic      = "stale-card:"
	staleCursor     = "board-card-corrections"
	correctionsRead = 24 * time.Hour
)

var answeringKinds = []string{"correction", "head"}

func (c Cards) RecordAsk(ctx context.Context, card string, ask int64) error {
	return c.record(ctx, card, map[string]string{askField: strconv.FormatInt(ask, 10)})
}

func (c Cards) Corrected(ctx context.Context, lane string) error {
	after, err := c.Store.Cursor(staleCursor)
	if err != nil {
		return err
	}
	fresh, err := c.Tail(ctx, client.Query{Drive: c.Bus, After: after, Since: c.Now().Add(-correctionsRead), Kinds: answeringKinds})
	if err != nil || len(fresh) == 0 {
		return err
	}
	read := fresh[len(fresh)-1].Seq
	drawnFrom, oldest, err := c.drawnFrom(ctx)
	if err != nil {
		return err
	}
	if len(drawnFrom) == 0 {
		return c.Store.Advance(staleCursor, read)
	}
	for _, record := range fresh {
		sources, err := c.reaches(ctx, record, drawnFrom, oldest)
		if err != nil {
			return err
		}
		for _, source := range sources {
			for _, card := range drawnFrom[source] {
				if err := c.flagStale(ctx, lane, card, source, record); err != nil {
					return err
				}
			}
		}
	}
	return c.Store.Advance(staleCursor, read)
}

func (c Cards) drawnFrom(ctx context.Context) (map[int64][]string, int64, error) {
	_, rows, err := c.rows(ctx)
	if err != nil {
		return nil, 0, err
	}
	shown, _, _, err := c.onBoard(ctx)
	if err != nil {
		return nil, 0, err
	}
	drawnFrom := map[int64][]string{}
	var oldest int64
	for _, card := range shown {
		if rows[card][askField] == "" {
			continue
		}
		ask, err := strconv.ParseInt(rows[card][askField], 10, 64)
		if err != nil {
			return nil, 0, fmt.Errorf("card %s ask %q: %w", card, rows[card][askField], err)
		}
		drawnFrom[ask] = append(drawnFrom[ask], card)
		if oldest == 0 || ask < oldest {
			oldest = ask
		}
	}
	return drawnFrom, oldest, nil
}

func (c Cards) reaches(ctx context.Context, record client.Record, drawnFrom map[int64][]string, oldest int64) ([]int64, error) {
	var sources []int64
	for up := answered(record, oldest); len(up) > 0; {
		for _, seq := range up {
			if _, drawn := drawnFrom[seq]; drawn && !slices.Contains(sources, seq) {
				sources = append(sources, seq)
			}
		}
		found, err := c.Among(ctx, c.Bus, up)
		if err != nil {
			return nil, err
		}
		up = nil
		for _, earlier := range found {
			up = append(up, answered(earlier, oldest)...)
		}
	}
	return sources, nil
}

func answered(record client.Record, oldest int64) []int64 {
	return slices.DeleteFunc(append([]int64{record.Re, record.Resolves}, record.AlsoResolves...), func(seq int64) bool { return seq < oldest })
}

func (c Cards) flagStale(ctx context.Context, lane, card string, source int64, record client.Record) error {
	_, err := c.Post(ctx, client.Draft{
		Drive: c.Bus, Lane: lane, Kind: askKind, ID: fmt.Sprintf("%s%s:%d", staleTopic, card, record.Seq), Topic: staleTopic + card, To: []string{communicator}, Re: record.Seq, Force: true,
		Text:  fmt.Sprintf("STALE %s on board %s: %s #%d from %s answers the thread of #%d, the record this card was drawn from, so its words may no longer be true. Redraw it on the corrected words, or withdraw it, before he taps.", card, c.Session, record.Kind, record.Seq, record.Lane, source),
		Attrs: map[string]string{"session": c.Session, "card": card},
	})
	return err
}
