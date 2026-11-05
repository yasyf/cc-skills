package grading

import (
	"context"
	"errors"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	GraderFinder   = "finder"
	GraderFidelity = "fidelity"
	ItemKind       = string(machine.ActionGrade)
)

var carriedKinds = []string{GraderFidelity, GraderFinder, "verdict"}

type Pool interface {
	Settle(ctx context.Context, workers []string) (map[string]bool, error)
	Plan(ctx context.Context, items []store.Item, snapshot map[int]machine.Facts, queued int, now time.Time) ([]store.Item, error)
	Dispatch(ctx context.Context, grader string, facts machine.Facts) (string, error)
	Whole(ctx context.Context, snapshot map[int]machine.Facts) ([]string, error)
	Reopen(ctx context.Context, snapshot map[int]machine.Facts) ([]string, error)
	Parked(ctx context.Context, snapshot map[int]machine.Facts) (map[int]string, error)
	TellStopped(ctx context.Context, facts machine.Facts, why string) error
}

type Dispatched struct {
	Grader string
	Number int
	Head   string
}

func Missing(facts machine.Facts) []string {
	var graders []string
	if facts.Finder == "" {
		graders = append(graders, GraderFinder)
	}
	if facts.Fidelity == "" {
		graders = append(graders, GraderFidelity)
	}
	return graders
}

func Act(ctx context.Context, s *store.Store, pool Pool, items []store.Item, snapshot map[int]machine.Facts, now time.Time, generation int64) ([]Dispatched, error) {
	graded := make(map[int64]bool, len(items))
	queued := 0
	for _, item := range items {
		facts, known := snapshot[item.Number]
		if item.Kind != ItemKind || !known {
			continue
		}
		graded[item.ID] = true
		if facts.Head == item.Head && !facts.Held() {
			queued += len(Missing(facts))
		}
	}
	working, err := s.Workers()
	if err != nil {
		return nil, err
	}
	held := make(map[string]bool, len(working))
	for _, worker := range working {
		held[worker.Name] = true
	}
	ranked, err := pool.Plan(ctx, items, snapshot, queued, now)
	if err != nil {
		return nil, err
	}
	full := map[string]bool{}
	var dispatched []Dispatched
	for _, item := range ranked {
		facts := snapshot[item.Number]
		if !graded[item.ID] || facts.Head != item.Head || facts.Held() || (held[item.Worker] && item.Deadline.After(now)) {
			continue
		}
		missing := Missing(facts)
		var stopped []string
		for _, grader := range missing {
			if full[grader] {
				continue
			}
			name, err := pool.Dispatch(ctx, grader, facts)
			var stop *Stopped
			if errors.As(err, &stop) {
				stopped = append(stopped, grader+": "+stop.Why)
				continue
			}
			if errors.Is(err, ErrNoCapacity) {
				full[grader] = true
				continue
			}
			if errors.Is(err, ErrAnswered) {
				continue
			}
			if err != nil {
				return dispatched, err
			}
			if err := s.Heartbeat(store.Worker{Name: name, Generation: generation, Heartbeat: now, Item: item.ID}); err != nil {
				return dispatched, err
			}
			dispatched = append(dispatched, Dispatched{grader, facts.Number, facts.Head})
		}
		if len(stopped) > 0 && len(stopped) == len(missing) && !now.Before(item.Deadline) {
			why := strings.Join(stopped, "; ")
			if err := pool.TellStopped(ctx, facts, why); err != nil {
				return dispatched, err
			}
			if _, err := s.PutFact(item.Repo, item.Number, item.Head, machine.GradeStoppedFact, why, now); err != nil {
				return dispatched, err
			}
		}
	}
	started, err := pool.Whole(ctx, snapshot)
	if err != nil {
		return dispatched, err
	}
	reopened, err := pool.Reopen(ctx, snapshot)
	if err != nil {
		return dispatched, err
	}
	parked, err := pool.Parked(ctx, snapshot)
	if err != nil {
		return dispatched, err
	}
	if err := markParked(s, snapshot, parked, now); err != nil {
		return dispatched, err
	}
	for _, name := range append(started, reopened...) {
		if err := s.Heartbeat(store.Worker{Name: name, Generation: generation, Heartbeat: now}); err != nil {
			return dispatched, err
		}
	}
	return dispatched, nil
}

func markParked(s *store.Store, snapshot map[int]machine.Facts, parked map[int]string, now time.Time) error {
	repos := map[string]bool{}
	for number, facts := range snapshot {
		repos[facts.Repo] = true
		if line := parked[number]; line != "" {
			if _, err := s.PutFact(facts.Repo, number, facts.Head, machine.FidelityWaitsFact, line, now); err != nil {
				return err
			}
		}
	}
	for repo := range repos {
		marked, err := s.FactsOfKind(repo, machine.FidelityWaitsFact)
		if err != nil {
			return err
		}
		for key := range marked {
			if facts, open := snapshot[key.Number]; open && facts.Head == key.Head && parked[key.Number] != "" {
				continue
			}
			if err := s.DeleteFact(repo, key.Number, key.Head, machine.FidelityWaitsFact); err != nil {
				return err
			}
		}
	}
	return nil
}

func Settle(ctx context.Context, s *store.Store, pool Pool, now time.Time, generation int64) error {
	workers, err := s.Workers()
	if err != nil {
		return err
	}
	workers = slices.DeleteFunc(workers, func(worker store.Worker) bool { return refOf(worker.Name).lane == "" })
	names := make([]string, 0, len(workers))
	for _, worker := range workers {
		names = append(names, worker.Name)
	}
	live, err := pool.Settle(ctx, names)
	if err != nil {
		return err
	}
	for _, worker := range workers {
		if live[worker.Name] {
			worker.Generation, worker.Heartbeat = generation, now
			if err := s.Heartbeat(worker); err != nil {
				return err
			}
			continue
		}
		if _, err := s.ReleaseWorker(worker.Name); err != nil {
			return err
		}
	}
	return nil
}

func CarryGrades(s *store.Store, repo string, number int, oldHead, newHead string, patchIDs map[string]string, at time.Time) ([]string, error) {
	if oldHead == newHead || patchIDs[oldHead] == "" || patchIDs[oldHead] != patchIDs[newHead] {
		return nil, nil
	}
	facts, err := s.Facts(repo, number, oldHead)
	if err != nil {
		return nil, err
	}
	var carried []string
	for _, kind := range carriedKinds {
		value, graded := facts[kind]
		if !graded {
			continue
		}
		if _, err := s.PutFact(repo, number, newHead, kind, value, at); err != nil {
			return carried, err
		}
		carried = append(carried, kind)
	}
	return carried, nil
}
