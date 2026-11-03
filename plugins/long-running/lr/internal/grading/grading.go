package grading

import (
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
	Dispatch(grader, repo string, number int, head string) (string, error)
	Alive(worker string) bool
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

func Act(s *store.Store, pool Pool, items []store.Item, snapshot map[int]machine.Facts, now time.Time, generation int64) ([]Dispatched, error) {
	workers, err := s.Workers()
	if err != nil {
		return nil, err
	}
	held := make(map[int64][]string, len(workers))
	for _, worker := range workers {
		held[worker.Item] = append(held[worker.Item], worker.Name)
	}
	var dispatched []Dispatched
	for _, item := range items {
		facts, known := snapshot[item.Number]
		if item.Kind != ItemKind || !known {
			continue
		}
		gone, err := releaseGone(s, pool, held[item.ID])
		if err != nil {
			return dispatched, err
		}
		if !gone && item.Worker != "" && item.Deadline.After(now) {
			continue
		}
		if facts.Head != item.Head {
			continue
		}
		for _, grader := range Missing(facts) {
			name, err := pool.Dispatch(grader, facts.Repo, facts.Number, facts.Head)
			if err != nil {
				return dispatched, err
			}
			if err := s.Heartbeat(store.Worker{Name: name, Generation: generation, Heartbeat: now, Item: item.ID}); err != nil {
				return dispatched, err
			}
			dispatched = append(dispatched, Dispatched{grader, facts.Number, facts.Head})
		}
	}
	return dispatched, nil
}

func releaseGone(s *store.Store, pool Pool, workers []string) (bool, error) {
	gone := false
	for _, name := range workers {
		if pool.Alive(name) {
			continue
		}
		if _, err := s.ReleaseWorker(name); err != nil {
			return gone, err
		}
		gone = true
	}
	return gone, nil
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
