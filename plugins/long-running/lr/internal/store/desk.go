package store

import (
	"database/sql"
	"errors"
	"fmt"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
)

type Proof string

const (
	ProofSameDiff Proof = "same-diff"
	ProofFidelity Proof = "fidelity"
)

type PRState struct {
	Repo     string
	Number   int
	Branch   string
	Base     string
	Head     string
	Change   string
	Stack    string
	State    machine.State
	Reason   string
	Mover    machine.Mover
	Deadline time.Time
}

type Ask struct {
	Repo   string
	Number int
	Change string
	Head   string
	Opened time.Time
}

type Carry struct {
	Repo       string
	Number     int
	FromNumber int
	FromHead   string
	ToHead     string
	Proof      Proof
	Grade      string
	At         time.Time
}

type Grade struct {
	Repo    string
	Number  int
	Head    string
	Grader  string
	Attempt int
	Verdict string
	Record  string
	Posted  time.Time
}

type Owed struct {
	Key     string
	Repo    string
	Stack   string
	Action  string
	Number  int
	Head    string
	Created time.Time
}

type Due struct {
	Key    string
	Repo   string
	Number int
	Kind   string
	At     time.Time
}

type Tx struct{ tx *sql.Tx }

func (s *Store) Update(apply func(*Tx) error) (err error) {
	tx, err := s.db.Begin()
	if err != nil {
		return err
	}
	defer func() {
		if err != nil {
			_ = tx.Rollback()
		}
	}()
	if err = apply(&Tx{tx}); err != nil {
		return err
	}
	return tx.Commit()
}

func (t *Tx) State(repo string, number int) (PRState, bool, error) {
	var pr PRState
	err := t.tx.QueryRow("SELECT repo, number, branch, base, head, change, stack, state, reason, mover, deadline FROM pr_states WHERE repo = ? AND number = ?", repo, number).
		Scan(&pr.Repo, &pr.Number, &pr.Branch, &pr.Base, &pr.Head, &pr.Change, &pr.Stack, &pr.State, &pr.Reason, &pr.Mover, moment{&pr.Deadline})
	if errors.Is(err, sql.ErrNoRows) {
		return PRState{}, false, nil
	}
	return pr, err == nil, err
}

func (t *Tx) PutState(pr PRState) error {
	_, err := t.tx.Exec(
		"INSERT INTO pr_states (repo, number, branch, base, head, change, stack, state, reason, mover, deadline) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (repo, number) DO UPDATE SET branch = excluded.branch, base = excluded.base, head = excluded.head, change = excluded.change, stack = excluded.stack, state = excluded.state, reason = excluded.reason, mover = excluded.mover, deadline = excluded.deadline",
		pr.Repo, pr.Number, pr.Branch, pr.Base, pr.Head, pr.Change, pr.Stack, string(pr.State), pr.Reason, string(pr.Mover), when(pr.Deadline),
	)
	return err
}

func (t *Tx) OpenAsk(ask Ask) error {
	_, err := t.tx.Exec(
		"INSERT INTO asks (repo, number, change, head, opened) VALUES (?, ?, ?, ?, ?) ON CONFLICT (repo, number, change) DO UPDATE SET head = excluded.head, opened = iif(asks.closed IS NULL, asks.opened, excluded.opened), closed = NULL",
		ask.Repo, ask.Number, ask.Change, ask.Head, Stamp(ask.Opened),
	)
	return err
}

func (t *Tx) CloseAsk(repo string, number int, change string, at time.Time) error {
	_, err := t.tx.Exec("UPDATE asks SET closed = ? WHERE repo = ? AND number = ? AND change = ? AND closed IS NULL", Stamp(at), repo, number, change)
	return err
}

func (t *Tx) Carry(c Carry) error {
	_, err := t.tx.Exec(
		"INSERT INTO carries (repo, number, from_number, from_head, to_head, proof, grade, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (repo, number, to_head) DO NOTHING",
		c.Repo, c.Number, c.FromNumber, c.FromHead, c.ToHead, string(c.Proof), text(c.Grade), Stamp(c.At),
	)
	return err
}

func (t *Tx) PutGrade(g Grade) error {
	_, err := t.tx.Exec(
		"INSERT INTO grades (repo, number, head, grader, attempt, verdict, record, posted) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (repo, number, head, grader) DO UPDATE SET attempt = excluded.attempt, verdict = excluded.verdict, record = excluded.record, posted = excluded.posted WHERE excluded.attempt >= grades.attempt",
		g.Repo, g.Number, g.Head, g.Grader, g.Attempt, g.Verdict, g.Record, Stamp(g.Posted),
	)
	return err
}

func (t *Tx) Grades(repo string, number int, head string) (map[string]Grade, error) {
	rows, err := t.tx.Query("SELECT repo, number, head, grader, attempt, verdict, record, posted FROM grades WHERE repo = ? AND number = ? AND head = ?", repo, number, head)
	found, err := collect(rows, err, func(rows *sql.Rows) (Grade, error) {
		var g Grade
		err := rows.Scan(&g.Repo, &g.Number, &g.Head, &g.Grader, &g.Attempt, &g.Verdict, &g.Record, moment{&g.Posted})
		return g, err
	})
	if err != nil {
		return nil, err
	}
	grades := make(map[string]Grade, len(found))
	for _, g := range found {
		grades[g.Grader] = g
	}
	return grades, nil
}

func (t *Tx) Raise(repo, stack string) error {
	_, err := t.tx.Exec("INSERT INTO stacks (repo, stack, revision) VALUES (?, ?, 1) ON CONFLICT (repo, stack) DO UPDATE SET revision = stacks.revision + 1", repo, stack)
	return err
}

func (t *Tx) Owe(o Owed) error {
	result, err := t.tx.Exec(
		"INSERT INTO owed (key, repo, stack, revision, action, number, head, created) SELECT ?, repo, stack, revision, ?, ?, ?, ? FROM stacks WHERE repo = ? AND stack = ? ON CONFLICT (key) DO UPDATE SET stack = excluded.stack, revision = excluded.revision",
		o.Key, o.Action, o.Number, o.Head, Stamp(o.Created), o.Repo, o.Stack,
	)
	if wrote, err := changed(result, err); err != nil || wrote {
		return err
	}
	return fmt.Errorf("owed %s: stack %s of %s has no revision", o.Key, o.Stack, o.Repo)
}

func (s *Store) Owed() ([]Owed, error) {
	rows, err := s.db.Query("SELECT owed.key, owed.repo, owed.stack, owed.action, owed.number, owed.head, owed.created FROM owed JOIN stacks USING (repo, stack, revision) ORDER BY owed.created, owed.key")
	return collect(rows, err, func(rows *sql.Rows) (Owed, error) {
		var o Owed
		err := rows.Scan(&o.Key, &o.Repo, &o.Stack, &o.Action, &o.Number, &o.Head, moment{&o.Created})
		return o, err
	})
}

func (t *Tx) SetDue(d Due) error {
	_, err := t.tx.Exec(
		"INSERT INTO due (key, repo, number, kind, at) VALUES (?, ?, ?, ?, ?) ON CONFLICT (key) DO UPDATE SET repo = excluded.repo, number = excluded.number, kind = excluded.kind, at = excluded.at",
		d.Key, d.Repo, d.Number, d.Kind, Stamp(d.At),
	)
	return err
}

func (t *Tx) ClearDue(key string) error {
	_, err := t.tx.Exec("DELETE FROM due WHERE key = ?", key)
	return err
}

func (s *Store) NextDue() (Due, bool, error) {
	var d Due
	err := s.db.QueryRow("SELECT key, repo, number, kind, at FROM due ORDER BY at, key LIMIT 1").Scan(&d.Key, &d.Repo, &d.Number, &d.Kind, moment{&d.At})
	if errors.Is(err, sql.ErrNoRows) {
		return Due{}, false, nil
	}
	return d, err == nil, err
}

func (t *Tx) Advance(source string, position int64) error {
	return advance(t.tx, source, position)
}
