package store

import (
	"database/sql"
	"time"
)

type ReviewRow struct {
	Repo     string    `json:"repo"`
	Number   int       `json:"number"`
	Head     string    `json:"head"`
	Lane     string    `json:"lane"`
	Title    string    `json:"title"`
	ForUsers string    `json:"for_users"`
	Plan     string    `json:"plan"`
	Risk     string    `json:"risk"`
	Review   string    `json:"review"`
	Waits    string    `json:"waits"`
	Ready    bool      `json:"ready"`
	Tapped   bool      `json:"tapped"`
	DrawnAt  time.Time `json:"drawn_at"`
}

func (s *Store) PutReviewRows(repo string, rows []ReviewRow, at time.Time) error {
	return s.Update(func(t *Tx) error {
		if _, err := t.tx.Exec("DELETE FROM review_rows WHERE repo = ?", repo); err != nil {
			return err
		}
		for _, row := range rows {
			if _, err := t.tx.Exec("INSERT INTO review_rows (repo, number, head, lane, title, for_users, plan, risk, review, waits, ready, tapped, drawn_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
				repo, row.Number, row.Head, row.Lane, row.Title, row.ForUsers, row.Plan, row.Risk, row.Review, row.Waits, row.Ready, row.Tapped, Stamp(at)); err != nil {
				return err
			}
		}
		return nil
	})
}

func (s *Store) ReviewRows(repo string) ([]ReviewRow, error) {
	rows, err := s.db.Query("SELECT repo, number, head, lane, title, for_users, plan, risk, review, waits, ready, tapped, drawn_at FROM review_rows WHERE repo = ? ORDER BY ready DESC, number", repo)
	return collect(rows, err, func(rows *sql.Rows) (ReviewRow, error) {
		var row ReviewRow
		var drawn string
		if err := rows.Scan(&row.Repo, &row.Number, &row.Head, &row.Lane, &row.Title, &row.ForUsers, &row.Plan, &row.Risk, &row.Review, &row.Waits, &row.Ready, &row.Tapped, &drawn); err != nil {
			return row, err
		}
		var err error
		row.DrawnAt, err = Parse(drawn)
		return row, err
	})
}
