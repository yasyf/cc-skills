package store

import (
	"database/sql"
	"errors"
	"time"
)

type PRFacts struct {
	Repo   string
	Number int
	Branch string
	Base   string
	Head   string
	Change string
	Open   bool
	Merged bool
	Landed bool
	At     time.Time
}

type Check struct {
	Repo       string
	SHA        string
	Name       string
	Conclusion string
	At         time.Time
	ID         int64
}

type Review struct {
	Repo     string
	Number   int
	Reviewer string
	State    string
	Head     string
	At       time.Time
}

type Tap struct {
	Repo    string
	Number  int
	Head    string
	Change  string
	Verdict string
	Record  string
	At      time.Time
}

type Behavior struct {
	Repo        string
	Number      int
	Head        string
	Since       string
	Behavior    string
	WhatChanged string
	Record      string
	At          time.Time
}

type Hold struct {
	Key    string
	Repo   string
	Number int
	Lifter string
	Reason string
	Until  string
	Opened time.Time
}

type Entry struct {
	Repo    string
	Number  int
	ID      string
	Head    string
	First   int
	Attempt int
	Outcome string
	Reason  string
}

const prFactColumns = "repo, number, branch, base, head, change, open, merged, landed, at"

func (t *Tx) PutPRFacts(f PRFacts) error {
	_, err := t.tx.Exec(
		"INSERT INTO pr_facts ("+prFactColumns+") VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?) ON CONFLICT (repo, number) DO UPDATE SET branch = excluded.branch, base = excluded.base, head = excluded.head, change = excluded.change, open = excluded.open, merged = excluded.merged, at = excluded.at WHERE excluded.at >= pr_facts.at",
		f.Repo, f.Number, f.Branch, f.Base, f.Head, f.Change, f.Open, f.Merged, Stamp(f.At),
	)
	return err
}

func (t *Tx) Land(repo string, number int) error {
	_, err := t.tx.Exec("UPDATE pr_facts SET landed = 1 WHERE repo = ? AND number = ?", repo, number)
	return err
}

func (t *Tx) PRFacts(repo string, number int) (PRFacts, bool, error) {
	return t.onePR("WHERE repo = ? AND number = ?", repo, number)
}

func (t *Tx) OnBranch(repo, branch string) (PRFacts, bool, error) {
	return t.onePR("WHERE repo = ? AND branch = ? ORDER BY open DESC, at DESC LIMIT 1", repo, branch)
}

func (t *Tx) Above(repo, branch string) ([]PRFacts, error) {
	rows, err := t.tx.Query("SELECT "+prFactColumns+" FROM pr_facts WHERE repo = ? AND base = ? AND open = 1 ORDER BY number", repo, branch)
	return collect(rows, err, func(rows *sql.Rows) (PRFacts, error) { return scanPRFacts(rows) })
}

func (t *Tx) OpenPRs(repo string) ([]PRFacts, error) {
	rows, err := t.tx.Query("SELECT "+prFactColumns+" FROM pr_facts WHERE repo = ? AND open = 1 ORDER BY number", repo)
	return collect(rows, err, func(rows *sql.Rows) (PRFacts, error) { return scanPRFacts(rows) })
}

func (t *Tx) AtHead(repo, sha string) ([]PRFacts, error) {
	rows, err := t.tx.Query("SELECT "+prFactColumns+" FROM pr_facts WHERE repo = ? AND head = ? ORDER BY number", repo, sha)
	return collect(rows, err, func(rows *sql.Rows) (PRFacts, error) { return scanPRFacts(rows) })
}

func (t *Tx) onePR(where string, args ...any) (PRFacts, bool, error) {
	f, err := scanPRFacts(t.tx.QueryRow("SELECT "+prFactColumns+" FROM pr_facts "+where, args...))
	if errors.Is(err, sql.ErrNoRows) {
		return PRFacts{}, false, nil
	}
	return f, err == nil, err
}

func scanPRFacts(row interface{ Scan(...any) error }) (PRFacts, error) {
	var f PRFacts
	err := row.Scan(&f.Repo, &f.Number, &f.Branch, &f.Base, &f.Head, &f.Change, &f.Open, &f.Merged, &f.Landed, moment{&f.At})
	return f, err
}

func (t *Tx) PutCheck(c Check) error {
	_, err := t.tx.Exec(
		"INSERT INTO checks (repo, sha, name, conclusion, at, id) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (repo, sha, name) DO UPDATE SET conclusion = excluded.conclusion, at = excluded.at, id = excluded.id WHERE (excluded.at, excluded.id) >= (checks.at, checks.id)",
		c.Repo, c.SHA, c.Name, c.Conclusion, Stamp(c.At), c.ID,
	)
	return err
}

func (t *Tx) Checks(repo, sha string) (map[string]string, error) {
	rows, err := t.tx.Query("SELECT name, conclusion FROM checks WHERE repo = ? AND sha = ?", repo, sha)
	found, err := collect(rows, err, func(rows *sql.Rows) ([2]string, error) {
		var pair [2]string
		err := rows.Scan(&pair[0], &pair[1])
		return pair, err
	})
	if err != nil {
		return nil, err
	}
	conclusions := make(map[string]string, len(found))
	for _, pair := range found {
		conclusions[pair[0]] = pair[1]
	}
	return conclusions, nil
}

func (t *Tx) PutReview(r Review) error {
	_, err := t.tx.Exec(
		"INSERT INTO reviews (repo, number, reviewer, state, head, at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (repo, number, reviewer) DO UPDATE SET state = excluded.state, head = excluded.head, at = excluded.at WHERE excluded.at >= reviews.at",
		r.Repo, r.Number, r.Reviewer, r.State, r.Head, Stamp(r.At),
	)
	return err
}

func (t *Tx) Reviews(repo string, number int) ([]Review, error) {
	rows, err := t.tx.Query("SELECT repo, number, reviewer, state, head, at FROM reviews WHERE repo = ? AND number = ? ORDER BY reviewer", repo, number)
	return collect(rows, err, func(rows *sql.Rows) (Review, error) {
		var r Review
		err := rows.Scan(&r.Repo, &r.Number, &r.Reviewer, &r.State, &r.Head, moment{&r.At})
		return r, err
	})
}

func (t *Tx) PutTap(tap Tap) error {
	_, err := t.tx.Exec(
		"INSERT INTO taps (repo, number, head, change, verdict, record, at) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (repo, change) DO UPDATE SET number = excluded.number, head = excluded.head, verdict = excluded.verdict, record = excluded.record, at = excluded.at WHERE excluded.at >= taps.at",
		tap.Repo, tap.Number, tap.Head, tap.Change, tap.Verdict, tap.Record, Stamp(tap.At),
	)
	return err
}

func (t *Tx) Tap(repo, change string) (Tap, error) {
	var tap Tap
	err := t.tx.QueryRow("SELECT repo, number, head, change, verdict, record, at FROM taps WHERE repo = ? AND change = ?", repo, change).
		Scan(&tap.Repo, &tap.Number, &tap.Head, &tap.Change, &tap.Verdict, &tap.Record, moment{&tap.At})
	if errors.Is(err, sql.ErrNoRows) {
		return Tap{}, nil
	}
	return tap, err
}

func (t *Tx) TappedBefore(repo string, number int, change string) (bool, error) {
	var found int
	err := t.tx.QueryRow("SELECT count(*) FROM taps WHERE repo = ? AND number = ? AND change != ? AND verdict IN ('tapped', 'rejected')", repo, number, change).Scan(&found)
	return found > 0, err
}

func (t *Tx) PutChange(repo, head, change string) error {
	if _, err := t.tx.Exec("INSERT INTO changes (repo, head, change) VALUES (?, ?, ?) ON CONFLICT (repo, head) DO UPDATE SET change = excluded.change", repo, head, change); err != nil {
		return err
	}
	if _, err := t.tx.Exec("UPDATE OR IGNORE taps SET change = ? WHERE repo = ? AND change = ?", change, repo, head); err != nil {
		return err
	}
	_, err := t.tx.Exec("UPDATE grades SET change = ? WHERE repo = ? AND head = ? AND change = ''", change, repo, head)
	return err
}

func (t *Tx) ChangeOf(repo, head string) (string, error) {
	var change string
	err := t.tx.QueryRow("SELECT change FROM changes WHERE repo = ? AND head = ?", repo, head).Scan(&change)
	if errors.Is(err, sql.ErrNoRows) {
		return "", nil
	}
	return change, err
}

func (t *Tx) PutBehavior(b Behavior) error {
	_, err := t.tx.Exec(
		"INSERT INTO behaviors (repo, number, head, since, behavior, what_changed, record, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (repo, number, head) DO UPDATE SET since = excluded.since, behavior = excluded.behavior, what_changed = excluded.what_changed, record = excluded.record, at = excluded.at WHERE excluded.at >= behaviors.at",
		b.Repo, b.Number, b.Head, b.Since, b.Behavior, b.WhatChanged, b.Record, Stamp(b.At),
	)
	return err
}

func (t *Tx) BehaviorAt(repo string, number int, head string) (Behavior, error) {
	var b Behavior
	err := t.tx.QueryRow("SELECT repo, number, head, since, behavior, what_changed, record, at FROM behaviors WHERE repo = ? AND number = ? AND head = ?", repo, number, head).
		Scan(&b.Repo, &b.Number, &b.Head, &b.Since, &b.Behavior, &b.WhatChanged, &b.Record, moment{&b.At})
	if errors.Is(err, sql.ErrNoRows) {
		return Behavior{}, nil
	}
	return b, err
}

func (t *Tx) OpenHold(h Hold) error {
	_, err := t.tx.Exec(
		"INSERT INTO holds (key, repo, number, lifter, reason, until, opened) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (key) DO NOTHING",
		h.Key, h.Repo, h.Number, h.Lifter, h.Reason, h.Until, Stamp(h.Opened),
	)
	return err
}

func (t *Tx) LiftBy(key, lifter string, at time.Time) ([]Hold, error) {
	rows, err := t.tx.Query("UPDATE holds SET lifted = ?1 WHERE (key = ?2 OR key LIKE ?2 || '#%') AND lifter = ?3 AND lifted IS NULL RETURNING "+holdColumns, Stamp(at), key, lifter)
	return collect(rows, err, func(rows *sql.Rows) (Hold, error) { return scanHold(rows) })
}

func (t *Tx) Lift(key string, at time.Time) (Hold, bool, error) {
	h, err := scanHold(t.tx.QueryRow("UPDATE holds SET lifted = ? WHERE key = ? AND lifted IS NULL RETURNING "+holdColumns, Stamp(at), key))
	if errors.Is(err, sql.ErrNoRows) {
		return Hold{}, false, nil
	}
	return h, err == nil, err
}

func (t *Tx) LiftUntil(repo, until string, at time.Time) ([]Hold, error) {
	rows, err := t.tx.Query("UPDATE holds SET lifted = ? WHERE repo = ? AND until = ? AND lifted IS NULL RETURNING "+holdColumns, Stamp(at), repo, until)
	return collect(rows, err, func(rows *sql.Rows) (Hold, error) { return scanHold(rows) })
}

func (t *Tx) Holds(repo string, number int) ([]Hold, error) {
	rows, err := t.tx.Query("SELECT "+holdColumns+" FROM holds WHERE repo = ? AND number = ? AND lifted IS NULL ORDER BY opened, key", repo, number)
	return collect(rows, err, func(rows *sql.Rows) (Hold, error) { return scanHold(rows) })
}

const holdColumns = "key, repo, number, lifter, reason, until, opened"

func scanHold(row interface{ Scan(...any) error }) (Hold, error) {
	var h Hold
	err := row.Scan(&h.Key, &h.Repo, &h.Number, &h.Lifter, &h.Reason, &h.Until, moment{&h.Opened})
	return h, err
}

func (t *Tx) PutEntry(e Entry) error {
	_, err := t.tx.Exec(
		"INSERT INTO queue (repo, number, entry, head, first, attempt, outcome, reason) VALUES (?1, ?2, ?7, ?3, ?4, ?4, ?5, ?6) ON CONFLICT (repo, number) DO UPDATE SET entry = excluded.entry, first = iif(queue.head = excluded.head, queue.first, excluded.attempt), head = excluded.head, attempt = excluded.attempt, outcome = excluded.outcome, reason = excluded.reason WHERE excluded.attempt >= queue.attempt",
		e.Repo, e.Number, e.Head, e.Attempt, e.Outcome, e.Reason, e.ID,
	)
	return err
}

func (t *Tx) Entry(repo string, number int) (Entry, bool, error) {
	var e Entry
	err := t.tx.QueryRow("SELECT repo, number, entry, head, first, attempt, outcome, reason FROM queue WHERE repo = ? AND number = ?", repo, number).
		Scan(&e.Repo, &e.Number, &e.ID, &e.Head, &e.First, &e.Attempt, &e.Outcome, &e.Reason)
	if errors.Is(err, sql.ErrNoRows) {
		return Entry{}, false, nil
	}
	return e, err == nil, err
}
