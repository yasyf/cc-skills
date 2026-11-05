package landing

import (
	"context"
	"errors"
	"fmt"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	entryFailed    = "failed"
	entryCancelled = "cancelled"
	entryNotReady  = "notReady"
	entryPending   = "pendingFailure"
)

var (
	trunkSideFailures = []string{"GITHUB_API_MERGE_PR_FAILED", "START_TEST_RUN_RETRY_EXHAUSTED"}
	failedEntries     = []string{entryFailed, entryPending}
)

type Failure struct {
	State       string
	At          string
	Reason      string
	Run         string
	Check       string
	URL         string
	MatesFailed []int
	Job         string
	Test        string
	Infra       string
}

type QueueEntry struct {
	State     string
	Head      string
	ChangedAt string
	Readiness string
}

type Evictions struct {
	Store *store.Store
	Queue Queue
	Drive string
	Now   func() time.Time
}

func (e Evictions) Pass(ctx context.Context, snapshot []machine.Facts) ([]machine.Facts, error) {
	read := slices.Clone(snapshot)
	failed := make([]error, 0, 1+len(read))
	failed = append(failed, e.readQueued(ctx, read))
	for at := range read {
		failed = append(failed, e.settle(ctx, &read[at]))
	}
	return read, errors.Join(failed...)
}

func (e Evictions) readQueued(ctx context.Context, read []machine.Facts) error {
	var numbers []int
	for _, f := range read {
		if f.Ours() && !f.Merged && !f.Closed && f.Queue == machine.QueueQueued {
			numbers = append(numbers, f.Number)
		}
	}
	if len(numbers) == 0 {
		return nil
	}
	states, err := e.Queue.States(ctx, numbers)
	if err != nil {
		return err
	}
	var failed []error
	for at := range read {
		f := &read[at]
		entry, known := states[f.Number]
		if !known || entry.Head != f.Head {
			continue
		}
		switch entry.State {
		case entryFailed, entryCancelled:
			f.Queue, f.QueuedHead = machine.QueueEvicted, ""
		case entryNotReady:
			failed = append(failed, e.sayNotReady(ctx, *f, entry))
		}
	}
	return errors.Join(failed...)
}

func (e Evictions) sayNotReady(ctx context.Context, f machine.Facts, entry QueueEntry) error {
	since, err := time.Parse(time.RFC3339, entry.ChangedAt)
	if err != nil {
		return fmt.Errorf("#%d not ready since %q: %w", f.Number, entry.ChangedAt, err)
	}
	if e.Now().Sub(since) < machine.QueueClock {
		return nil
	}
	to, unowned, err := e.audience(f)
	if err != nil {
		return err
	}
	_, err = client.Post(ctx, client.Draft{
		Drive: e.Drive, Lane: self, Kind: "blocker", To: to, PRs: []string{pullRef(f)},
		ID:   fmt.Sprintf("not-ready:%d:%s:%s", f.Number, f.Head, entry.ChangedAt),
		Text: fmt.Sprintf("%sNOT-READY #%d at %s: its label reads queued, and Trunk holds it notReady since %s, readiness %s. Nothing tests it until that clears.", unowned, f.Number, shortHead(f.Head), entry.ChangedAt, entry.Readiness),
	})
	return err
}

func (e Evictions) settle(ctx context.Context, f *machine.Facts) error {
	if !f.Ours() || f.Merged || f.Closed {
		return nil
	}
	if f.Queue == machine.QueueQueued && f.Eviction != "" {
		f.Eviction, f.EvictionOwn = "", false
		return forgetEviction(e.Store, *f)
	}
	if f.Queue != machine.QueueEvicted || f.Eviction != "" {
		return nil
	}
	failure, err := e.Queue.LastFailure(ctx, f.Number)
	if err != nil {
		return fmt.Errorf("#%d queue failure unread: %w", f.Number, err)
	}
	evidence, err := e.notItsOwn(ctx, failure)
	if err != nil {
		return err
	}
	own := evidence == "" || f.Resubmitted >= 1
	if err := e.tell(ctx, *f, failure, evidence, own); err != nil {
		return err
	}
	f.Eviction, f.EvictionOwn = failure.words(), own
	now := e.Now()
	if _, err := e.Store.PutFact(f.Repo, f.Number, f.Head, machine.EvictionFact, f.Eviction, now); err != nil {
		return err
	}
	if !own {
		return nil
	}
	_, err = e.Store.PutFact(f.Repo, f.Number, f.Head, machine.EvictionOwnFact, "yes", now)
	return err
}

func (e Evictions) notItsOwn(ctx context.Context, failure Failure) (string, error) {
	switch {
	case failure.State == entryCancelled:
		return "the queue entry was canceled, no test failed", nil
	case slices.Contains(trunkSideFailures, failure.Reason):
		return "Trunk's own step failed", nil
	case len(failure.MatesFailed) > 0:
		return "the same test run also failed " + numbered(failure.MatesFailed), nil
	case failure.Infra != "":
		return "its log reads as infrastructure", nil
	case failure.Test == "" && failure.Job == "":
		return "", nil
	}
	flakes, err := openFlakes(ctx, e.Drive)
	if err != nil {
		return "", err
	}
	named := flakeFound{test: failure.Test, job: failure.Job}.subject()
	if at := slices.IndexFunc(flakes, func(r client.Record) bool { return r.Fields.Attrs[flakeAttr] == named }); at >= 0 {
		return "open flake " + flakes[at].Name() + " names it", nil
	}
	return "", nil
}

func (e Evictions) tell(ctx context.Context, f machine.Facts, failure Failure, evidence string, own bool) error {
	to, unowned, err := e.audience(f)
	if err != nil {
		return err
	}
	kind, next := "report", "Not its own change: "+evidence+". It re-enters the queue once at this head, as soon as it is landable."
	if own {
		kind, next = "blocker", "It stays out of the queue until a new head; read the check, then push a fix."
		if f.Resubmitted >= 1 {
			next = "This is its second eviction at this head, so it stays out of the queue until a new head; read the check, then push a fix."
		}
	}
	_, err = client.Post(ctx, client.Draft{
		Drive: e.Drive, Lane: self, Kind: kind, To: to, PRs: []string{pullRef(f)},
		ID:   fmt.Sprintf("evicted:%d:%s:%s", f.Number, f.Head, failure.At),
		Text: fmt.Sprintf("%sEVICTED #%d at %s: %s. %s", unowned, f.Number, shortHead(f.Head), failure.words(), next),
	})
	return err
}

func (e Evictions) audience(f machine.Facts) ([]string, string, error) {
	holder, unowned, err := reach(e.Store, e.Drive, f)
	if err != nil {
		return nil, "", err
	}
	to, err := rowAudience(e.Store, e.Drive, f, holder)
	return to, unowned, err
}

func rowAudience(s *store.Store, drive string, f machine.Facts, holder string, others ...string) ([]string, error) {
	named, err := s.OwnerOf([]int{f.Number})
	if err != nil {
		return nil, err
	}
	want := append([]string{holder, lanes.OwnerRelay}, others...)
	if owner := named[f.Number]; owner != "" {
		running, err := lanes.Running(s, drive, owner)
		if err != nil {
			return nil, err
		}
		want = append(want, running)
	}
	to := want[:0]
	for _, lane := range want {
		if !slices.Contains(to, lane) {
			to = append(to, lane)
		}
	}
	return to, nil
}

func (failure Failure) words() string {
	if failure.State == "" {
		return "Trunk's history names no failed or canceled attempt"
	}
	state := "failed"
	if failure.State == entryCancelled {
		state = "canceled"
	}
	said := []string{fmt.Sprintf("Trunk %s it at %s (%s)", state, failure.At, failure.Reason)}
	if failure.Check != "" {
		said = append(said, fmt.Sprintf("failing check %s %s", failure.Check, failure.URL))
	}
	switch {
	case failure.Test != "":
		said = append(said, fmt.Sprintf("job %s: FAIL %s", failure.Job, failure.Test))
	case failure.Infra != "":
		said = append(said, fmt.Sprintf("job %s: %s", failure.Job, failure.Infra))
	case failure.Job != "":
		said = append(said, fmt.Sprintf("job %s, no failing test named in its log", failure.Job))
	}
	return strings.Join(said, "; ")
}

func forgetEviction(s *store.Store, f machine.Facts) error {
	return errors.Join(s.DeleteFact(f.Repo, f.Number, f.Head, machine.EvictionFact), s.DeleteFact(f.Repo, f.Number, f.Head, machine.EvictionOwnFact))
}

func numbered(numbers []int) string {
	named := make([]string, len(numbers))
	for at, number := range numbers {
		named[at] = fmt.Sprintf("#%d", number)
	}
	return strings.Join(named, ", ")
}
