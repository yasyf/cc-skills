package store

import "database/sql"

type Rule struct {
	ID        string
	Words     string
	Said      string
	CheckKind string
	CheckRef  string
}

func (s *Store) CheckedRules() ([]Rule, error) {
	rows, err := s.db.Query("SELECT id, words, said, check_kind, check_ref FROM rules WHERE check_kind IS NOT NULL ORDER BY said, id")
	return collect(rows, err, func(rows *sql.Rows) (Rule, error) {
		var rule Rule
		err := rows.Scan(&rule.ID, &rule.Words, &rule.Said, &rule.CheckKind, &rule.CheckRef)
		return rule, err
	})
}
