package store

import (
	"database/sql"
	"strings"
)

type Rule struct {
	ID        string
	Words     string
	Said      string
	CheckKind string
	CheckRef  string
	PRs       []string
}

func (s *Store) CheckedRules() ([]Rule, error) {
	rows, err := s.db.Query("SELECT id, words, said, check_kind, check_ref, coalesce(prs, '') FROM rules WHERE check_kind IS NOT NULL ORDER BY said, id")
	return collect(rows, err, func(rows *sql.Rows) (Rule, error) {
		var rule Rule
		var prs string
		err := rows.Scan(&rule.ID, &rule.Words, &rule.Said, &rule.CheckKind, &rule.CheckRef, &prs)
		rule.PRs = strings.Fields(prs)
		return rule, err
	})
}
