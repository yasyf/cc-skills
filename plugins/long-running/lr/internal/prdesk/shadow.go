package prdesk

import (
	"context"
	"fmt"
	"log/slog"
	"slices"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
)

const (
	Lane        = "pr-desk"
	busSource   = "bus"
	timerSource = "timer"
	ReadEvery   = time.Minute
)

var (
	readied  = []machine.State{machine.StateReady, machine.StateQueued, machine.StateEvicted}
	followed = []string{"lr.tap", "lr.grade", "review", "owner", "go", "decide", "decision"}
)

type Shadow struct {
	Desk    Desk
	Repo    string
	Drive   string
	To      []string
	Board   string
	Origin  string
	Graders []string
	GoLanes []string
	Read    func(ctx context.Context, at time.Time) ([]Event, error)
	Tail    func(ctx context.Context, q client.Query) ([]client.Record, error)
	Post    func(ctx context.Context, d client.Draft) (client.Record, error)
	Now     func() time.Time
	Log     *slog.Logger
}

func (s Shadow) Run(ctx context.Context) error {
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	wake := make(chan struct{}, 1)
	apply := func(e Event) error {
		moved, err := s.Desk.Apply(e)
		if err != nil {
			return err
		}
		select {
		case wake <- struct{}{}:
		default:
		}
		return s.disagree(ctx, moved)
	}
	errs := make(chan error, 3)
	go func() { errs <- s.follow(ctx, apply) }()
	go func() { errs <- s.reads(ctx, apply) }()
	go func() { errs <- s.timers(ctx, wake, apply) }()
	return <-errs
}

func (s Shadow) follow(ctx context.Context, apply func(Event) error) error {
	after, err := s.Desk.Store.Cursor(busSource)
	if err != nil {
		return err
	}
	for {
		records, err := s.Tail(ctx, client.Query{Drive: s.Drive, After: after, Kinds: followed})
		if err != nil {
			return err
		}
		for _, r := range records {
			after = r.Seq
			for _, e := range s.events(r) {
				if err := apply(e); err != nil {
					return err
				}
			}
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(time.Second):
		}
	}
}

func (s Shadow) events(r client.Record) []Event {
	key := r.ID
	if key == "" {
		key = fmt.Sprintf("#%d", r.Seq)
	}
	meta := Meta{Source: busSource, Seq: r.Seq, Key: key, At: r.At, Repo: s.Repo}
	fromBoard := r.Lane == s.Board && r.Origin == s.Origin
	if parsed, err := bus.Parse(r); err == nil {
		switch body := parsed.Body.(type) {
		case bus.Tap:
			verdict := machine.Tap(body.Value)
			switch {
			case body.By == "rule":
				verdict = machine.TapTapped
			case body.Condition != "":
				verdict = machine.TapConditional
			}
			if fromBoard {
				return []Event{Tap{meta, body.PR, body.Head, verdict}}
			}
		case bus.Grade:
			if slices.Contains(s.Graders, client.Stem(r.Lane)) {
				return []Event{Grade{meta, body.PR, body.Head, body.Grader, 0, body.Verdict}}
			}
		}
		return nil
	}
	facts := bus.LegacyFacts(r, s.Repo, s.GoLanes)
	events := make([]Event, 0, len(facts))
	for _, f := range facts {
		switch f.Kind {
		case "finder", "fidelity":
			events = append(events, Grade{meta, f.PR, f.Head, f.Kind, 0, f.Value})
		case "verdict":
			events = append(events, Grade{meta, f.PR, f.Head, "appeal", 0, "settle"})
		case "exempt":
			events = append(events, Tap{meta, f.PR, f.Head, machine.TapTapped})
		case "tap":
			if fromBoard {
				events = append(events, Tap{meta, f.PR, f.Head, machine.Tap(f.Value)})
			}
		}
	}
	return events
}

func (s Shadow) reads(ctx context.Context, apply func(Event) error) error {
	tick := time.NewTicker(ReadEvery)
	defer tick.Stop()
	for {
		events, err := s.Read(ctx, s.Now().UTC().Truncate(time.Second))
		if err != nil {
			s.Log.Warn("the minute's GitHub read failed and changed nothing", "err", err)
		}
		for _, e := range events {
			if err := apply(e); err != nil {
				return err
			}
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-tick.C:
		}
	}
}

func (s Shadow) timers(ctx context.Context, wake <-chan struct{}, apply func(Event) error) error {
	for {
		due, found, err := s.Desk.Store.NextDue()
		if err != nil {
			return err
		}
		var fire <-chan time.Time
		if found {
			wait := due.At.Sub(s.Now())
			if wait <= 0 {
				if err := apply(Timer{Meta{Source: timerSource, Seq: due.At.Unix(), Key: due.Key, At: due.At, Repo: due.Repo}, due}); err != nil {
					return err
				}
				continue
			}
			fire = time.After(wait)
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-wake:
		case <-fire:
		}
	}
}

func (s Shadow) disagree(ctx context.Context, moved []Moved) error {
	now := s.Now()
	for _, m := range moved {
		var cause, why string
		switch {
		case m.After.State == machine.StateReady && !m.After.Deadline.IsZero() && !now.Before(m.After.Deadline):
			cause, why = "unqueued", "the desk would have queued it by now and nothing has"
		case m.Before.State != "" && !slices.Contains(readied, m.Before.State) && (m.After.State == machine.StateQueued || m.After.State == machine.StateLanded):
			cause, why = "landed-early", fmt.Sprintf("it reached the queue while the desk had it %s: %s", m.Before.State, m.Before.Reason)
		default:
			continue
		}
		if _, err := s.Post(ctx, client.Draft{
			Drive: s.Drive, Lane: Lane, Kind: "note", To: s.To,
			ID:   fmt.Sprintf("pr-shadow:%s#%d:%s:%s", m.After.Repo, m.After.Number, m.After.Head, cause),
			PRs:  []string{fmt.Sprintf("%s#%d", m.After.Repo, m.After.Number)},
			Text: fmt.Sprintf("SHADOW #%d at %s: %s", m.After.Number, m.After.Head[:12], why),
		}); err != nil {
			return err
		}
	}
	return nil
}
