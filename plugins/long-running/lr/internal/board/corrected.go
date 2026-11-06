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
	staleCursor     = "board-card-corrections:"
	correctionsRead = 24 * time.Hour
)

var answeringKinds = []string{"correction", "head"}

func (c Cards) RecordAsk(ctx context.Context, card string, ask int64) error {
	return c.record(ctx, card, map[string]string{askField: strconv.FormatInt(ask, 10)})
}

func (c Cards) Corrected(ctx context.Context, lane string) error {
	cursor := staleCursor + c.Session
	after, err := c.Store.Cursor(cursor)
	if err != nil {
		return err
	}
	query := client.Query{Drive: c.Bus, After: after, Kinds: answeringKinds}
	if after == 0 {
		query.Since = c.Now().Add(-correctionsRead)
	}
	fresh, err := c.Tail(ctx, query)
	if err != nil || len(fresh) == 0 {
		return err
	}
	read := fresh[len(fresh)-1].Seq
	drawnFrom, oldest, err := c.drawnFrom(ctx)
	if err != nil {
		return err
	}
	if len(drawnFrom) == 0 {
		return c.Store.Advance(cursor, read)
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
	return c.Store.Advance(cursor, read)
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
	walked := map[int64]bool{record.Seq: true}
	for up := answered(record, oldest); len(up) > 0; {
		var unwalked []int64
		for _, seq := range up {
			if walked[seq] {
				continue
			}
			walked[seq] = true
			unwalked = append(unwalked, seq)
			if _, drawn := drawnFrom[seq]; drawn {
				sources = append(sources, seq)
			}
		}
		if len(unwalked) == 0 {
			break
		}
		found, err := c.Among(ctx, c.Bus, unwalked)
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
		Text:  fmt.Sprintf("STALE %s on board %s: %s %s from %s answers the thread of #%d, the record this card was drawn from, so its words may no longer be true. Redraw it on the corrected words, or withdraw it, before he taps.", card, c.Session, record.Kind, record.Name(), record.Lane, source),
		Attrs: map[string]string{"session": c.Session, "card": card},
	})
	return err
}
