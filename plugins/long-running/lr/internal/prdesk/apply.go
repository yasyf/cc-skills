package prdesk

import (
	"fmt"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	QueueIn  = "queued"
	QueueOut = "evicted"

	holdDue     = "hold"
	deadlineDue = "deadline"
)

var (
	graders = []string{"finder", "fidelity"}
	passing = []string{"success", "neutral", "skipped"}
)

type Repo struct {
	Name     string
	Landing  string
	Required []string
	TapFree  bool
}

type Desk struct {
	Store *store.Store
	Repos map[string]Repo
}

type Moved struct {
	Before store.PRState
	After  store.PRState
}

func (d Desk) Apply(e Event) ([]Moved, error) {
	m := e.meta()
	repo, found := d.Repos[m.Repo]
	if !found {
		return nil, fmt.Errorf("%s %s: %s is not one of this desk's repos", m.Source, m.Key, m.Repo)
	}
	var moved []Moved
	err := d.Store.Update(func(tx *store.Tx) error {
		moved = nil
		touched, err := e.record(tx)
		if err != nil {
			return fmt.Errorf("%s %s: %w", m.Source, m.Key, err)
		}
		placed := map[string]bool{}
		for _, number := range touched {
			if err := place(tx, repo, number, m.At, placed, &moved); err != nil {
				return fmt.Errorf("%s %s: #%d: %w", m.Source, m.Key, number, err)
			}
		}
		return tx.Advance(m.Source, m.Seq)
	})
	return moved, err
}

func place(tx *store.Tx, repo Repo, number int, at time.Time, placed map[string]bool, moved *[]Moved) error {
	f, found, err := tx.PRFacts(repo.Name, number)
	if err != nil || !found {
		return err
	}
	if !f.Open {
		if err := placeStack(tx, repo, f, nil, at, moved); err != nil {
			return err
		}
		above, err := tx.Above(repo.Name, f.Branch)
		if err != nil {
			return err
		}
		for _, child := range above {
			if err := place(tx, repo, child.Number, at, placed, moved); err != nil {
				return err
			}
		}
		return nil
	}
	bottom, beneath := f, (*machine.Place)(nil)
	for bottom.Base != repo.Landing {
		parent, found, err := tx.OnBranch(repo.Name, bottom.Base)
		if err != nil {
			return err
		}
		if !found {
			break
		}
		if !parent.Open {
			if beneath, err = stored(tx, parent); err != nil {
				return err
			}
			break
		}
		bottom = parent
	}
	if placed[bottom.Branch] {
		return nil
	}
	placed[bottom.Branch] = true
	return placeStack(tx, repo, bottom, beneath, at, moved)
}

func placeStack(tx *store.Tx, repo Repo, bottom store.PRFacts, beneath *machine.Place, at time.Time, moved *[]Moved) error {
	if err := tx.Raise(repo.Name, bottom.Branch); err != nil {
		return err
	}
	var climb func(f store.PRFacts, beneath *machine.Place) error
	climb = func(f store.PRFacts, beneath *machine.Place) error {
		here, err := placeOne(tx, repo, f, beneath, bottom.Branch, at, moved)
		if err != nil || !f.Open {
			return err
		}
		above, err := tx.Above(repo.Name, f.Branch)
		if err != nil {
			return err
		}
		for _, child := range above {
			if err := climb(child, &here); err != nil {
				return err
			}
		}
		return nil
	}
	return climb(bottom, beneath)
}

func stored(tx *store.Tx, f store.PRFacts) (*machine.Place, error) {
	row, found, err := tx.State(f.Repo, f.Number)
	if err != nil || !found {
		return nil, err
	}
	return &machine.Place{State: row.State, Mover: row.Mover, Reason: row.Reason}, nil
}

func placeOne(tx *store.Tx, repo Repo, f store.PRFacts, beneath *machine.Place, stack string, at time.Time, moved *[]Moved) (machine.Place, error) {
	seen, err := read(tx, repo, f)
	if err != nil {
		return machine.Place{}, err
	}
	here := machine.Decide(seen.pr, beneath)
	switch {
	case here.State == machine.StateAwaitingTap && seen.whatChanged != "":
		here.Reason += "; since the tap: " + seen.whatChanged
	case here.State == machine.StateGrading && len(seen.missing) > 0:
		here.Reason = "this head waits on " + strings.Join(seen.missing, " and ")
	}
	prev, had, err := tx.State(repo.Name, f.Number)
	if err != nil {
		return here, err
	}
	change := seen.change
	waiting := here.State == machine.StateAwaitingTap || here.State == machine.StateHeld && here.Mover == machine.MoverOwner
	if waiting {
		if err := tx.OpenAsk(store.Ask{Repo: repo.Name, Number: f.Number, Change: change, Head: f.Head, Opened: at}); err != nil {
			return here, err
		}
	}
	deadline := prev.Deadline
	if !had || prev.State != here.State || prev.Mover != here.Mover || prev.Head != f.Head {
		deadline = time.Time{}
		if here.Deadline > 0 {
			deadline = at.Add(here.Deadline)
		}
	}
	row := store.PRState{
		Repo: repo.Name, Number: f.Number, Branch: f.Branch, Base: f.Base, Head: f.Head, Change: change, Stack: stack,
		State: here.State, Reason: here.Reason, Mover: here.Mover, Deadline: deadline,
	}
	if err := tx.PutState(row); err != nil {
		return here, err
	}
	*moved = append(*moved, Moved{prev, row})
	if seen.carry != nil {
		if err := tx.Carry(*seen.carry); err != nil {
			return here, err
		}
	}
	if had && (!waiting || prev.Change != change) {
		if err := tx.CloseAsk(repo.Name, f.Number, prev.Change, at); err != nil {
			return here, err
		}
	}
	owe := func(action string) error {
		return tx.Owe(store.Owed{
			Key:  fmt.Sprintf("%s:%s:%s#%d:%s", action, here.State, repo.Name, f.Number, f.Head),
			Repo: repo.Name, Stack: stack, Action: action, Number: f.Number, Head: f.Head, Created: at,
		})
	}
	var actions []string
	switch {
	case here.State == machine.StateGrading:
		for _, grader := range seen.ungraded {
			actions = append(actions, "grade-"+grader)
		}
	case here.State == machine.StateReady:
		actions = append(actions, "enqueue")
	case here.State == machine.StateEvicted && seen.pr.Eviction == machine.EvictionConflict:
		actions = append(actions, "restack")
	case here.State == machine.StateEvicted:
		actions = append(actions, "reenter")
	case here.State == machine.StateAppeal && here.Mover == machine.MoverAppeals:
		actions = append(actions, "appeal")
	case here.State == machine.StateNeedsFix || here.State == machine.StateRejected || here.State == machine.StateAppeal:
		actions = append(actions, "tell")
	}
	if seen.inQueue && !slices.Contains([]machine.State{machine.StateQueued, machine.StateLanded, machine.StateReleased, machine.StateLive, machine.StateClosed}, here.State) {
		actions = append(actions, "dequeue")
	}
	key := fmt.Sprintf("%s:%s#%d", deadlineDue, repo.Name, f.Number)
	switch {
	case deadline.IsZero():
		err = tx.ClearDue(key)
	case at.Before(deadline):
		err = tx.SetDue(store.Due{Key: key, Repo: repo.Name, Number: f.Number, Kind: deadlineDue, At: deadline})
	default:
		actions = append(actions, "overdue")
		err = tx.ClearDue(key)
	}
	if err != nil {
		return here, err
	}
	for _, action := range actions {
		if err := owe(action); err != nil {
			return here, err
		}
	}
	return here, nil
}

type view struct {
	pr          machine.PR
	change      string
	ungraded    []string
	missing     []string
	inQueue     bool
	carry       *store.Carry
	whatChanged string
}

func read(tx *store.Tx, repo Repo, f store.PRFacts) (view, error) {
	checks, err := tx.Checks(repo.Name, f.Head)
	if err != nil {
		return view{}, err
	}
	change := f.Change
	var grades map[string]store.Grade
	if change == "" {
		change = f.Head
		grades, err = tx.Grades(repo.Name, f.Number, f.Head)
	} else {
		grades, err = tx.GradesOfChange(repo.Name, f.Number, change)
	}
	if err != nil {
		return view{}, err
	}
	reviews, err := tx.Reviews(repo.Name, f.Number)
	if err != nil {
		return view{}, err
	}
	judged, err := tx.BehaviorAt(repo.Name, f.Number, f.Head)
	if err != nil {
		return view{}, err
	}
	before, err := tx.TappedBefore(repo.Name, f.Number, change)
	if err != nil {
		return view{}, err
	}
	tap, carry, whatChanged, err := tapOf(tx, repo, f, change, grades, judged)
	if err != nil {
		return view{}, err
	}
	holds, err := tx.Holds(repo.Name, f.Number)
	if err != nil {
		return view{}, err
	}
	entry, queued, err := tx.Entry(repo.Name, f.Number)
	if err != nil {
		return view{}, err
	}
	landing := f.Merged && f.Base == repo.Landing
	atHead := queued && entry.Head == f.Head
	out := view{
		pr: machine.PR{
			Closed:    !f.Open && !landing,
			Merged:    landing && !f.Landed,
			Landed:    f.Landed,
			Queued:    atHead && entry.Outcome == QueueIn,
			Reentered: atHead && entry.Attempt > entry.First,
			Checks:    machine.CISuccess,
			Tap:       machine.Tap(tap),
			TapFree:   repo.TapFree,
		},
		change:      change,
		inQueue:     queued && entry.Outcome == QueueIn,
		carry:       carry,
		whatChanged: whatChanged,
	}
	if atHead && entry.Outcome == QueueOut {
		out.pr.Eviction = machine.Eviction(entry.Reason)
	}
	for _, name := range repo.Required {
		conclusion, reported := checks[name]
		switch {
		case !reported:
			if out.pr.Checks == machine.CISuccess {
				out.pr.Checks = machine.CIPending
			}
		case !slices.Contains(passing, conclusion):
			out.pr.Checks = machine.CIFailure
			out.pr.Failing = append(out.pr.Failing, name)
		}
	}
	for _, r := range reviews {
		switch {
		case r.State == "changes_requested":
			out.pr.Review = machine.ReviewChanges
		case r.State == "approved" && r.Head == f.Head && out.pr.Review == "":
			out.pr.Review = machine.ReviewApproved
		}
	}
	for _, grader := range graders {
		if _, graded := grades[grader]; !graded {
			out.ungraded = append(out.ungraded, grader)
			out.missing = append(out.missing, "its "+grader+" grade")
		}
	}
	if before && judged.Behavior == "" {
		out.missing = append(out.missing, "the fidelity judgment of its behavior since the tapped change")
		if !slices.Contains(out.ungraded, "fidelity") {
			out.ungraded = append(out.ungraded, "fidelity")
		}
	}
	out.pr.Graded = len(out.ungraded) == 0
	open := grades["finder"].Verdict == string(machine.FinderAsk) || grades["fidelity"].Verdict == string(machine.FidelityFail)
	switch grades["appeal"].Verdict {
	case "uphold":
		out.pr.Finding = machine.FindingUpheld
	case "conflict":
		out.pr.Finding = machine.FindingConflict
	case "":
		if open {
			out.pr.Finding = machine.FindingOpen
		}
	}
	if len(holds) > 0 {
		out.pr.Hold = machine.Hold{Lifter: machine.Mover(holds[0].Lifter), Reason: holds[0].Reason}
	}
	return out, nil
}

func tapOf(tx *store.Tx, repo Repo, f store.PRFacts, change string, grades map[string]store.Grade, b store.Behavior) (string, *store.Carry, string, error) {
	tap, err := tx.Tap(repo.Name, change)
	if err != nil {
		return "", nil, "", err
	}
	own := strings.HasPrefix(f.Head, tap.Head)
	if tap.Verdict == string(machine.TapConditional) && !own {
		tap = store.Tap{}
	}
	carry := func(from store.Tap, proof store.Proof, grade string) *store.Carry {
		return &store.Carry{Repo: repo.Name, Number: f.Number, FromNumber: from.Number, FromHead: from.Head, ToHead: f.Head, Proof: proof, Grade: grade, At: f.At}
	}
	if tap.Verdict != "" {
		if own {
			return tap.Verdict, nil, "", nil
		}
		return tap.Verdict, carry(tap, store.ProofSameDiff, ""), "", nil
	}
	if b.Behavior == "" {
		return "", nil, "", nil
	}
	if b.Behavior != gates.BehaviorSame || grades["fidelity"].Verdict != string(machine.FidelityPass) && grades["appeal"].Verdict == "" {
		return "", nil, b.WhatChanged, nil
	}
	since, err := tx.ChangeOf(repo.Name, b.Since)
	if err != nil {
		return "", nil, "", err
	}
	if since == "" {
		since = b.Since
	}
	tapped, err := tx.Tap(repo.Name, since)
	if err != nil || tapped.Verdict != string(machine.TapTapped) && tapped.Verdict != string(machine.TapRejected) {
		return "", nil, "", err
	}
	return tapped.Verdict, carry(tapped, store.ProofFidelity, b.Record), "", nil
}
