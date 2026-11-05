package landing

import (
	"cmp"
	"context"
	"fmt"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/pass"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	ResubmittedFact = "resubmitted"
	FreeRetriesFact = "free-retries"
	RestackedFact   = "restacked"
	conflictValue   = "conflict"
	restacksPerPass = 2
	freeRetries     = 3
)

type Queue interface {
	Enqueue(ctx context.Context, number int, head string) (string, error)
	Reenter(ctx context.Context, number int) (string, error)
	Dequeue(ctx context.Context, number int) (string, error)
	LastFailure(ctx context.Context, number int, since time.Time) (Failure, error)
	States(ctx context.Context, numbers []int) (map[int]QueueEntry, error)
}

type Restacker interface {
	Restack(ctx context.Context, number int, head, base string) (string, error)
}

type Head struct {
	Number int
	Head   string
}

type Move struct {
	Number int
	From   string
	To     string
}

type Said struct {
	Facts    machine.Facts
	Accepted string
	Refused  string
	Text     string
	Alarm    bool
}

type Done struct {
	Said        []Said
	Enqueued    []Head
	Resubmitted []Head
	Dequeued    []int
	Restacked   []Move
	Conflicts   []int
}

func Clear(acts []pass.Act, snapshot []machine.Facts, trunk string) []pass.Act {
	byBranch := make(map[string]machine.Facts, len(snapshot))
	for _, f := range snapshot {
		byBranch[f.Branch] = f
	}
	kept := slices.DeleteFunc(slices.Clone(acts), func(act pass.Act) bool {
		return act.Verdict.Action == machine.ActionEnqueue && !clearBelow(act.Facts, byBranch, trunk)
	})
	slices.SortStableFunc(kept, func(a, b pass.Act) int {
		return cmp.Compare(dequeueRank(a, byBranch), dequeueRank(b, byBranch))
	})
	return kept
}

func dequeueRank(act pass.Act, byBranch map[string]machine.Facts) int {
	if act.Verdict.Action != machine.ActionDequeue {
		return 1
	}
	rank, f := 0, act.Facts
	for range len(byBranch) {
		below, open := byBranch[f.Base]
		if !open {
			break
		}
		rank, f = rank-1, below
	}
	return rank
}

func clearBelow(f machine.Facts, byBranch map[string]machine.Facts, trunk string) bool {
	for range len(byBranch) + 1 {
		if f.Base == trunk {
			return true
		}
		below, open := byBranch[f.Base]
		if !open || !below.ClearsAbove() {
			return false
		}
		f = below
	}
	return false
}

func Act(ctx context.Context, s *store.Store, queue Queue, restacker Restacker, acts []pass.Act, now time.Time) (Done, error) {
	var done Done
	for _, act := range acts {
		var err error
		switch act.Verdict.Action {
		case machine.ActionEnqueue:
			err = enqueue(ctx, s, queue, act.Facts, now, &done)
		case machine.ActionDequeue:
			var said string
			if said, err = queue.Dequeue(ctx, act.Facts.Number); err == nil {
				done.Dequeued = append(done.Dequeued, act.Facts.Number)
				if said != "" {
					done.Said = append(done.Said, dequeued(act.Facts, said))
				}
			}
		case machine.ActionRestack:
			err = restack(ctx, s, restacker, act.Facts, now, &done)
		}
		if err != nil {
			return done, err
		}
	}
	return done, nil
}

func dequeued(f machine.Facts, said string) Said {
	answer := Said{Facts: f, Accepted: "DEQUEUED", Refused: "DEQUEUE-REFUSED"}
	switch {
	case f.QueuedHead != f.Head:
		answer.Text = "the queue held an older head"
	case f.Hold != "":
		answer.Text = "an open hold names it"
	case !f.Admitted():
		answer.Accepted, answer.Refused, answer.Alarm = "UNTAPPED-DEQUEUED", "UNTAPPED-DEQUEUE-REFUSED", true
		if f.TapFrom != "" {
			answer.Text = fmt.Sprintf("his tap at %s does not carry to this head: %s; ", f.TapFrom[:min(10, len(f.TapFrom))], f.TapReturned)
		}
		if f.TapRefused != "" {
			answer.Text += "his tap was refused: " + f.TapRefused + "; "
		}
		answer.Text += "it was queued with no owner tap and no go naming an exempt class at this head; a class on fidelity's ok admits it only through the go sweep, which posts its go on the next pass and needs no ask, and the pass queues it; lr landing go prints why the sweep refuses; an alert fix lands by plain squash once approved and green: at once on his Approve or while an open incident names it, else once the alert wait from its approval post has passed"
	default:
		answer.Text = "an open hold names a PR below it"
	}
	answer.Text += "; the queue said " + said
	return answer
}

func enqueue(ctx context.Context, s *store.Store, queue Queue, f machine.Facts, now time.Time, done *Done) error {
	if f.Queue == machine.QueueEvicted {
		return reenter(ctx, s, queue, f, now, done)
	}
	said, err := queue.Enqueue(ctx, f.Number, f.Head)
	if err != nil {
		return err
	}
	if err := recordSaid(s, f, said, now, done); err != nil {
		return err
	}
	done.Enqueued = append(done.Enqueued, Head{f.Number, f.Head})
	return nil
}

func reenter(ctx context.Context, s *store.Store, queue Queue, f machine.Facts, now time.Time, done *Done) error {
	said, err := queue.Reenter(ctx, f.Number)
	if err != nil {
		return err
	}
	if err := recordSaid(s, f, said, now, done); err != nil {
		return err
	}
	facts, err := s.Facts(f.Repo, f.Number, f.Head)
	if err != nil {
		return err
	}
	free, err := strconv.Atoi(cmp.Or(facts[FreeRetriesFact], "0"))
	if err != nil {
		return fmt.Errorf("#%d %s: %w", f.Number, FreeRetriesFact, err)
	}
	fact, value := ResubmittedFact, strconv.Itoa(f.Resubmitted+1)
	if free < freeRetries && !strings.HasPrefix(said, "exit 0") {
		fact, value = FreeRetriesFact, strconv.Itoa(free+1)
	}
	if _, err := s.PutFact(f.Repo, f.Number, f.Head, fact, value, now); err != nil {
		return err
	}
	if fact == ResubmittedFact {
		if err := forgetEviction(s, f); err != nil {
			return err
		}
	}
	done.Resubmitted = append(done.Resubmitted, Head{f.Number, f.Head})
	return nil
}

func recordSaid(s *store.Store, f machine.Facts, said string, now time.Time, done *Done) error {
	if said == "" {
		return nil
	}
	if _, err := s.PutFact(f.Repo, f.Number, f.Head, pass.EnqueuedFact, said, now); err != nil {
		return err
	}
	done.Said = append(done.Said, Said{Facts: f, Accepted: "QUEUED", Refused: "REFUSED", Text: said})
	return nil
}

func restack(ctx context.Context, s *store.Store, restacker Restacker, f machine.Facts, now time.Time, done *Done) error {
	if len(done.Restacked)+len(done.Conflicts) >= restacksPerPass {
		return nil
	}
	facts, err := s.Facts(f.Repo, f.Number, f.Head)
	if err != nil {
		return err
	}
	if _, restacked := facts[RestackedFact]; restacked {
		return nil
	}
	moved, err := restacker.Restack(ctx, f.Number, f.Head, f.Base)
	if err != nil {
		return err
	}
	value := moved
	if moved == "" {
		value = conflictValue
	}
	if _, err := s.PutFact(f.Repo, f.Number, f.Head, RestackedFact, value, now); err != nil {
		return err
	}
	if moved == "" {
		done.Conflicts = append(done.Conflicts, f.Number)
		done.Said = append(done.Said, Said{Facts: f, Accepted: "RESTACKED", Refused: "RESTACK-CONFLICT", Text: fmt.Sprintf("replaying it onto %s conflicts, so the supervisor pushed nothing and resolving it is yours", f.Base)})
		return nil
	}
	done.Restacked = append(done.Restacked, Move{f.Number, f.Head, moved})
	return nil
}
