package prdesk

import (
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

type Event interface {
	meta() Meta
	record(tx *store.Tx) ([]int, error)
}

type Meta struct {
	Source string
	Seq    int64
	Key    string
	At     time.Time
	Repo   string
}

func (m Meta) meta() Meta { return m }

type PullRequest struct {
	Meta
	Number int
	Branch string
	Base   string
	Head   string
	Open   bool
	Merged bool
	Change string
}

type Review struct {
	Meta
	Number   int
	Reviewer string
	State    string
	Head     string
}

type Check struct {
	Meta
	SHA        string
	Name       string
	Conclusion string
	ID         int64
}

type Push struct {
	Meta
	Landed []int
}

type Queue struct {
	Meta
	Number  int
	Entry   string
	Head    string
	Attempt int
	Outcome string
	Reason  machine.Eviction
}

type Tap struct {
	Meta
	Number  int
	Head    string
	Verdict machine.Tap
}

type Grade struct {
	Meta
	Number  int
	Head    string
	Grader  string
	Attempt int
	Verdict string
}

type Hold struct {
	Meta
	Number int
	Lifter machine.Mover
	Reason string
	Until  string
}

type Behavior struct {
	Meta
	Number      int
	Head        string
	Since       string
	Same        bool
	WhatChanged string
}

type Lift struct {
	Meta
	Hold string
}

type Timer struct {
	Meta
	Due store.Due
}

func (e PullRequest) record(tx *store.Tx) ([]int, error) {
	if e.Change != "" {
		if err := tx.PutChange(e.Repo, e.Head, e.Change); err != nil {
			return nil, err
		}
	}
	return []int{e.Number}, tx.PutPRFacts(store.PRFacts{Repo: e.Repo, Number: e.Number, Branch: e.Branch, Base: e.Base, Head: e.Head, Change: e.Change, Open: e.Open, Merged: e.Merged, At: e.At})
}

func (e Review) record(tx *store.Tx) ([]int, error) {
	return []int{e.Number}, tx.PutReview(store.Review{Repo: e.Repo, Number: e.Number, Reviewer: e.Reviewer, State: e.State, Head: e.Head, At: e.At})
}

func (e Check) record(tx *store.Tx) ([]int, error) {
	if err := tx.PutCheck(store.Check{Repo: e.Repo, SHA: e.SHA, Name: e.Name, Conclusion: e.Conclusion, At: e.At, ID: e.ID}); err != nil {
		return nil, err
	}
	prs, err := tx.AtHead(e.Repo, e.SHA)
	touched := make([]int, 0, len(prs))
	for _, pr := range prs {
		touched = append(touched, pr.Number)
	}
	return touched, err
}

func (e Push) record(tx *store.Tx) ([]int, error) {
	var touched []int
	for _, number := range e.Landed {
		if err := tx.Land(e.Repo, number); err != nil {
			return nil, err
		}
		lifted, err := tx.LiftUntil(e.Repo, fmt.Sprintf("pr-landed:#%d", number), e.At)
		if err != nil {
			return nil, err
		}
		touched = append(touched, number)
		for _, h := range lifted {
			touched = append(touched, h.Number)
		}
	}
	return touched, nil
}

func (e Queue) record(tx *store.Tx) ([]int, error) {
	return []int{e.Number}, tx.PutEntry(store.Entry{Repo: e.Repo, Number: e.Number, ID: e.Entry, Head: e.Head, Attempt: e.Attempt, Outcome: e.Outcome, Reason: string(e.Reason)})
}

func (e Tap) record(tx *store.Tx) ([]int, error) {
	change, err := tx.ChangeOf(e.Repo, e.Head)
	if err != nil {
		return nil, err
	}
	if change == "" {
		change = e.Head
	}
	return []int{e.Number}, tx.PutTap(store.Tap{Repo: e.Repo, Number: e.Number, Head: e.Head, Change: change, Verdict: string(e.Verdict), Record: e.Key, At: e.At})
}

func (e Grade) record(tx *store.Tx) ([]int, error) {
	change, err := tx.ChangeOf(e.Repo, e.Head)
	if err != nil {
		return nil, err
	}
	return []int{e.Number}, tx.PutGrade(store.Grade{Repo: e.Repo, Number: e.Number, Head: e.Head, Change: change, Grader: e.Grader, Attempt: e.Attempt, Verdict: e.Verdict, Record: e.Key, Posted: e.At})
}

func (e Behavior) record(tx *store.Tx) ([]int, error) {
	behavior := gates.BehaviorChanged
	if e.Same {
		behavior = gates.BehaviorSame
	}
	return []int{e.Number}, tx.PutBehavior(store.Behavior{Repo: e.Repo, Number: e.Number, Head: e.Head, Since: e.Since, Behavior: behavior, WhatChanged: e.WhatChanged, Record: e.Key, At: e.At})
}

func (e Hold) record(tx *store.Tx) ([]int, error) {
	if err := tx.OpenHold(store.Hold{Key: e.Key, Repo: e.Repo, Number: e.Number, Lifter: string(e.Lifter), Reason: e.Reason, Until: e.Until, Opened: e.At}); err != nil {
		return nil, err
	}
	past, until, err := e.past(tx)
	if err != nil {
		return nil, err
	}
	if past {
		_, _, err = tx.Lift(e.Key, e.At)
		return []int{e.Number}, err
	}
	if !until.IsZero() {
		err = tx.SetDue(store.Due{Key: holdDue + ":" + e.Key, Repo: e.Repo, Number: e.Number, Kind: holdDue, At: until})
	}
	return []int{e.Number}, err
}

func (e Hold) past(tx *store.Tx) (bool, time.Time, error) {
	kind, value, _ := strings.Cut(e.Until, ":")
	switch kind {
	case "time":
		until, err := time.Parse(time.RFC3339, value)
		if err != nil {
			return false, time.Time{}, fmt.Errorf("hold %s: %w", e.Key, err)
		}
		return !until.After(e.At), until, nil
	case "pr-landed":
		number, err := strconv.Atoi(strings.TrimPrefix(value, "#"))
		if err != nil {
			return false, time.Time{}, fmt.Errorf("hold %s: %w", e.Key, err)
		}
		landed, _, err := tx.PRFacts(e.Repo, number)
		return landed.Landed, time.Time{}, err
	}
	return false, time.Time{}, nil
}

func (e Lift) record(tx *store.Tx) ([]int, error) {
	lifted, err := tx.LiftAll(e.Hold, e.At)
	touched := make([]int, 0, len(lifted))
	for _, h := range lifted {
		touched = append(touched, h.Number)
	}
	return touched, err
}

func (e Timer) record(tx *store.Tx) ([]int, error) {
	if err := tx.ClearDue(e.Due.Key); err != nil {
		return nil, err
	}
	if e.Due.Kind == holdDue {
		if _, _, err := tx.Lift(strings.TrimPrefix(e.Due.Key, holdDue+":"), e.At); err != nil {
			return nil, err
		}
	}
	return []int{e.Due.Number}, nil
}
