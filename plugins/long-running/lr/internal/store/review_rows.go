package store

import (
	"database/sql"
	"time"
)

const (
	ReviewRowsCursor       = "review-rows"
	ReviewRowsGithubCursor = "review-rows-github"
)

const reviewRowColumns = "repo, number, title, lane, branch, base, parent, parent_state, head, head_at, read_head, for_users, plan_line, plan_row, risk, description, unsafe_wait, ci, approvers, review, grade, grade_findings, mergeable, queue, hold, superseded_by, waits_on, ready_for_tap, state, tapped, tap_refused, tapped_at, tapped_by, landed_at, last_record, written_at"

type ReviewRow struct {
	Repo          string `json:"repo"`
	Number        int    `json:"number"`
	Title         string `json:"title"`
	Lane          string `json:"lane"`
	Branch        string `json:"branch"`
	Base          string `json:"base"`
	Parent        string `json:"parent"`
	ParentState   string `json:"parent_state"`
	Head          string `json:"head"`
	HeadAt        string `json:"head_at"`
	ReadHead      string `json:"read_head"`
	ForUsers      string `json:"for_users"`
	PlanLine      string `json:"plan_line"`
	PlanRow       string `json:"plan_row"`
	Risk          string `json:"risk"`
	Description   string `json:"description"`
	UnsafeWait    string `json:"unsafe_wait"`
	CI            string `json:"ci"`
	Approvers     string `json:"approvers"`
	Review        string `json:"review"`
	Grade         string `json:"grade"`
	GradeFindings string `json:"grade_findings"`
	Mergeable     string `json:"mergeable"`
	Queue         string `json:"queue"`
	Hold          string `json:"hold"`
	SupersededBy  string `json:"superseded_by"`
	WaitsOn       string `json:"waits_on"`
	ReadyForTap   bool   `json:"ready_for_tap"`
	State         string `json:"state"`
	Tapped        bool   `json:"tapped"`
	TapRefused    string `json:"tap_refused"`
	TappedAt      string `json:"tapped_at"`
	TappedBy      string `json:"tapped_by"`
	LandedAt      string `json:"landed_at"`
	LastRecord    int64  `json:"last_record"`
	WrittenAt     string `json:"written_at"`
}

func (r *ReviewRow) columns() []any {
	return []any{&r.Repo, &r.Number, &r.Title, &r.Lane, &r.Branch, &r.Base, &r.Parent, &r.ParentState, &r.Head, &r.HeadAt, &r.ReadHead, &r.ForUsers, &r.PlanLine, &r.PlanRow, &r.Risk, &r.Description, &r.UnsafeWait, &r.CI, &r.Approvers, &r.Review, &r.Grade, &r.GradeFindings, &r.Mergeable, &r.Queue, &r.Hold, &r.SupersededBy, &r.WaitsOn, &r.ReadyForTap, &r.State, &r.Tapped, &r.TapRefused, &r.TappedAt, &r.TappedBy, &r.LandedAt, &r.LastRecord, &r.WrittenAt}
}

func (r ReviewRow) values() []any {
	return []any{r.Repo, r.Number, r.Title, r.Lane, r.Branch, r.Base, r.Parent, r.ParentState, r.Head, r.HeadAt, r.ReadHead, r.ForUsers, r.PlanLine, r.PlanRow, r.Risk, r.Description, r.UnsafeWait, r.CI, r.Approvers, r.Review, r.Grade, r.GradeFindings, r.Mergeable, r.Queue, r.Hold, r.SupersededBy, r.WaitsOn, r.ReadyForTap, r.State, r.Tapped, r.TapRefused, r.TappedAt, r.TappedBy, r.LandedAt, r.LastRecord, r.WrittenAt}
}

func (t *Tx) PutReviewRows(rows []ReviewRow) error {
	for _, row := range rows {
		if _, err := t.tx.Exec("INSERT OR REPLACE INTO review_rows ("+reviewRowColumns+") VALUES ("+placeholders(len(row.values()))+")", row.values()...); err != nil {
			return err
		}
	}
	return nil
}

func (s *Store) PutReviewRows(rows []ReviewRow) error {
	return s.Update(func(t *Tx) error { return t.PutReviewRows(rows) })
}

type rowReader interface {
	Query(query string, args ...any) (*sql.Rows, error)
}

func reviewRows(db rowReader, repo string) ([]ReviewRow, error) {
	rows, err := db.Query("SELECT "+reviewRowColumns+" FROM review_rows WHERE repo = ? ORDER BY ready_for_tap DESC, number", repo)
	return collect(rows, err, func(rows *sql.Rows) (ReviewRow, error) {
		var row ReviewRow
		err := rows.Scan(row.columns()...)
		return row, err
	})
}

func (s *Store) ReviewRows(repo string) ([]ReviewRow, error) {
	return reviewRows(s.db, repo)
}

func (t *Tx) ReviewRows(repo string) ([]ReviewRow, error) {
	return reviewRows(t.tx, repo)
}

func (s *Store) ReviewRow(repo string, number int) (ReviewRow, bool, error) {
	var row ReviewRow
	err := s.db.QueryRow("SELECT "+reviewRowColumns+" FROM review_rows WHERE repo = ? AND number = ?", repo, number).Scan(row.columns()...)
	if err == sql.ErrNoRows {
		return row, false, nil
	}
	return row, err == nil, err
}

func (s *Store) FactsAtRowHeads(repo string) (map[int]map[string]string, error) {
	return factsAtRowHeads(s.db, repo)
}

func (t *Tx) FactsAtRowHeads(repo string) (map[int]map[string]string, error) {
	return factsAtRowHeads(t.tx, repo)
}

func factsAtRowHeads(db rowReader, repo string) (map[int]map[string]string, error) {
	rows, err := db.Query(`SELECT r.number, f.kind, f.value FROM review_rows r
JOIN facts f ON f.repo = r.repo AND f.number = r.number AND length(f.head) >= 7 AND length(r.head) >= 7
  AND (substr(r.head, 1, length(f.head)) = f.head OR substr(f.head, 1, length(r.head)) = r.head)
WHERE r.repo = ? ORDER BY f.posted, f.kind = 'verdict', length(f.head)`, repo)
	type fact struct {
		number      int
		kind, value string
	}
	found, err := collect(rows, err, func(rows *sql.Rows) (fact, error) {
		var one fact
		err := rows.Scan(&one.number, &one.kind, &one.value)
		return one, err
	})
	if err != nil {
		return nil, err
	}
	at := make(map[int]map[string]string)
	for _, one := range found {
		if at[one.number] == nil {
			at[one.number] = make(map[string]string)
		}
		foldFact(at[one.number], one.kind, one.value)
	}
	return at, nil
}

func (t *Tx) Cursor(source string) (int64, error) {
	var position int64
	err := t.tx.QueryRow("SELECT position FROM cursors WHERE source = ?", source).Scan(&position)
	if err == sql.ErrNoRows {
		return 0, nil
	}
	return position, err
}

func (t *Tx) PutFact(repo string, number int, head, kind, value string, at time.Time) error {
	_, err := t.tx.Exec(
		"INSERT INTO facts (repo, number, head, kind, value, posted) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (repo, number, head, kind) DO UPDATE SET value = excluded.value, posted = excluded.posted WHERE facts.value IS NOT excluded.value",
		repo, number, head, kind, value, Stamp(at),
	)
	return err
}

func (t *Tx) Fact(repo string, number int, head, kind string) (string, error) {
	var value string
	err := t.tx.QueryRow("SELECT value FROM facts WHERE repo = ? AND number = ? AND head = ? AND kind = ?", repo, number, head, kind).Scan(&value)
	if err == sql.ErrNoRows {
		return "", nil
	}
	return value, err
}

func (t *Tx) HeadSeen(repo string, number int, head, kind string) (bool, error) {
	var seen int
	err := t.tx.QueryRow("SELECT count(*) FROM facts WHERE repo = ?1 AND number = ?2 AND kind = ?4 AND length(head) >= 7 AND (substr(?3, 1, length(head)) = head OR length(?3) >= 7 AND substr(head, 1, length(?3)) = ?3)", repo, number, head, kind).Scan(&seen)
	return seen > 0, err
}
