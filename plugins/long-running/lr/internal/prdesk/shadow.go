package prdesk

import (
	"context"
	"fmt"
	"log/slog"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	Lane        = "pr-desk"
	busSource   = "bus"
	timerSource = "timer"
	ReadEvery   = time.Minute
)

var readied = []machine.State{machine.StateReady, machine.StateQueued, machine.StateEvicted}

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
	Hash    func(ctx context.Context, base, head string) (string, error)
	Trunk   func(ctx context.Context, number int) (Submitted, error)
	Post    func(ctx context.Context, d client.Draft) (client.Record, error)
	Now     func() time.Time
	Log     *slog.Logger
}

func (s Shadow) Run(ctx context.Context) error {
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	wake := make(chan struct{}, 1)
	apply := func(e Event) error {
		e, err := s.changed(ctx, e)
		if err != nil || e == nil {
			return err
		}
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
	errs := make(chan error, 4)
	go func() {
		errs <- s.tail(ctx, client.Query{Drive: s.Drive}, busSource, func(r client.Record) error {
			for _, e := range s.events(r) {
				if err := apply(e); err != nil {
					return err
				}
			}
			return nil
		})
	}()
	go func() {
		errs <- s.tail(ctx, client.Query{Drive: trunkDrive, Kinds: []string{"queue"}}, trunkSource, func(r client.Record) error {
			return s.queued(ctx, r, apply)
		})
	}()
	go func() { errs <- s.reads(ctx, apply) }()
	go func() { errs <- s.timers(ctx, wake, apply) }()
	return <-errs
}

func (s Shadow) tail(ctx context.Context, q client.Query, source string, each func(client.Record) error) error {
	after, err := s.Desk.Store.Cursor(source)
	if err != nil {
		return err
	}
	for {
		q.After = after
		records, err := s.Tail(ctx, q)
		if err != nil {
			return err
		}
		for _, r := range records {
			after = r.Seq
			if err := each(r); err != nil {
				return err
			}
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(time.Second):
		}
	}
}

func (s Shadow) queued(ctx context.Context, r client.Record, apply func(Event) error) error {
	for _, pr := range r.Refs.PRs {
		if !strings.EqualFold(pr.Repo, s.Repo) {
			continue
		}
		read, err := s.Trunk(ctx, pr.Number)
		if err != nil {
			s.Log.Warn("a Trunk read failed and changed nothing", "pr", pr.Number, "err", err)
			continue
		}
		var stored store.Entry
		if err := s.Desk.Store.Update(func(tx *store.Tx) (err error) {
			stored, _, err = tx.Entry(s.Repo, pr.Number)
			return err
		}); err != nil {
			return err
		}
		attempt := stored.Attempt
		if read.ID != stored.ID {
			attempt++
		}
		outcome, reason := read.outcome()
		if err := apply(Queue{Meta{Source: trunkSource, Seq: r.Seq, Key: r.ID, At: r.At, Repo: s.Repo}, pr.Number, read.ID, read.Head, attempt, outcome, reason}); err != nil {
			return err
		}
	}
	return nil
}

func (s Shadow) changed(ctx context.Context, e Event) (Event, error) {
	pr, isPR := e.(PullRequest)
	if !isPR || pr.Change != "" {
		return e, nil
	}
	var stored store.PRFacts
	if err := s.Desk.Store.Update(func(tx *store.Tx) (err error) {
		stored, _, err = tx.PRFacts(pr.Repo, pr.Number)
		return err
	}); err != nil {
		return nil, err
	}
	if stored.Head == pr.Head && stored.Base == pr.Base && stored.Change != "" || !pr.Open {
		pr.Change = stored.Change
		return pr, nil
	}
	change, err := s.Hash(ctx, pr.Base, pr.Head)
	if err != nil {
		s.Log.Warn("a diff hash failed, so this read of the PR changes nothing until the next", "pr", pr.Number, "head", pr.Head, "err", err)
		return nil, nil
	}
	pr.Change = change
	return pr, nil
}

func (s Shadow) events(r client.Record) []Event {
	key := r.ID
	if key == "" {
		key = fmt.Sprintf("#%d", r.Seq)
	}
	meta := Meta{Source: busSource, Seq: r.Seq, Key: key, At: r.At, Repo: s.Repo}
	fromBoard := r.Lane == s.Board && r.Origin == s.Origin
	poster := machine.Mover(client.Stem(r.Lane))
	resolved := slices.DeleteFunc(append([]int64{r.Resolves}, r.AlsoResolves...), func(seq int64) bool { return seq == 0 })
	if string(r.Kind) == "hold" && len(resolved) == 0 {
		held := bus.HeldBy(r, s.Repo)
		holds := make([]Event, 0, len(held))
		for _, number := range held {
			m := meta
			m.Key = fmt.Sprintf("#%d#%d", r.Seq, number)
			holds = append(holds, Hold{m, number, poster, r.Text, "lifter"})
		}
		return holds
	}
	lifts := make([]Event, 0, len(resolved))
	for _, seq := range resolved {
		lifts = append(lifts, Lift{meta, fmt.Sprintf("#%d", seq)})
	}
	return append(lifts, s.said(r, meta, fromBoard)...)
}

func (s Shadow) said(r client.Record, meta Meta, fromBoard bool) []Event {
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
	if tap, ordered := bus.OrderOf(r, s.Repo); ordered {
		if !fromBoard || tap.Head == "" {
			return nil
		}
		return []Event{Tap{meta, tap.PR, tap.Head, machine.Tap(tap.Value)}}
	}
	facts := bus.LegacyFacts(r, s.Repo, s.GoLanes)
	events := make([]Event, 0, len(facts))
	judged := map[string]int{}
	judgment := func(f bus.Fact) *Behavior {
		key := fmt.Sprintf("%d %s", f.PR, f.Head)
		if i, found := judged[key]; found {
			return events[i].(*Behavior)
		}
		judged[key] = len(events)
		b := &Behavior{Meta: meta, Number: f.PR, Head: f.Head}
		events = append(events, b)
		return b
	}
	for _, f := range facts {
		switch f.Kind {
		case gates.BehaviorFact:
			judgment(f).Same = f.Value == gates.BehaviorSame
		case gates.SinceFact:
			judgment(f).Since = f.Value
		case gates.ChangedFact:
			judgment(f).WhatChanged = f.Value
		case "finder", "fidelity":
			events = append(events, Grade{meta, f.PR, f.Head, f.Kind, 0, f.Value})
		case "verdict":
			events = append(events, Grade{meta, f.PR, f.Head, "appeal", 0, "settle"})
		case "exempt":
			events = append(events, Tap{meta, f.PR, f.Head, machine.TapTapped})
		}
	}
	return events
}

func (s Shadow) reads(ctx context.Context, apply func(Event) error) error {
	tick := time.NewTicker(ReadEvery)
	defer tick.Stop()
	var seen map[Event]bool
	for {
		events, err := s.Read(ctx, s.Now().UTC().Truncate(time.Second))
		if err != nil {
			s.Log.Warn("the minute's GitHub read failed and changed nothing", "err", err)
		}
		var fresh []Event
		fresh, seen = unseen(seen, events)
		for _, e := range fresh {
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

func unseen(seen map[Event]bool, events []Event) ([]Event, map[Event]bool) {
	if len(events) == 0 {
		return nil, seen
	}
	read := make(map[Event]bool, len(events))
	var fresh []Event
	for _, e := range events {
		read[e] = true
		if !seen[e] {
			fresh = append(fresh, e)
		}
	}
	return fresh, read
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
			queued, err := s.queuedAt(m.After)
			if err != nil {
				return err
			}
			cause, why = "landed-early", fmt.Sprintf("it reached the queue while the desk had it %s: %s", m.Before.State, m.Before.Reason)
			if !queued {
				cause, why = "merged-outside-queue", fmt.Sprintf("it was merged outside the queue while the desk had it %s: %s", m.Before.State, m.Before.Reason)
			}
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

func (s Shadow) queuedAt(row store.PRState) (bool, error) {
	var entry store.Entry
	var found bool
	err := s.Desk.Store.Update(func(tx *store.Tx) (err error) {
		entry, found, err = tx.Entry(row.Repo, row.Number)
		return err
	})
	return found && entry.Head == row.Head, err
}
