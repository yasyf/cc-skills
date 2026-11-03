package store

import (
	"database/sql"
	"strings"
)

type CardRef struct {
	Session string
	Card    string
	Lane    string
}

func (s *Store) PutCardDeps(ref CardRef, deps []string) (err error) {
	tx, err := s.db.Begin()
	if err != nil {
		return err
	}
	defer func() {
		if err != nil {
			_ = tx.Rollback()
		}
	}()
	if _, err = tx.Exec("DELETE FROM card_deps WHERE session = ? AND card = ?", ref.Session, ref.Card); err != nil {
		return err
	}
	for _, dep := range deps {
		if _, err = tx.Exec("INSERT OR IGNORE INTO card_deps (dep, session, card, lane) VALUES (?, ?, ?, ?)", dep, ref.Session, ref.Card, ref.Lane); err != nil {
			return err
		}
	}
	return tx.Commit()
}

func (s *Store) DropCardDeps(ref CardRef) error {
	_, err := s.db.Exec("DELETE FROM card_deps WHERE session = ? AND card = ?", ref.Session, ref.Card)
	return err
}

func (s *Store) CardDeps(ref CardRef) ([]string, error) {
	rows, err := s.db.Query("SELECT dep FROM card_deps WHERE session = ? AND card = ? ORDER BY dep", ref.Session, ref.Card)
	return collect(rows, err, func(rows *sql.Rows) (string, error) {
		var dep string
		err := rows.Scan(&dep)
		return dep, err
	})
}

func (s *Store) CardsDependingOn(deps []string) ([]CardRef, error) {
	if len(deps) == 0 {
		return nil, nil
	}
	args := make([]any, len(deps))
	for i, dep := range deps {
		args[i] = dep
	}
	rows, err := s.db.Query(dependentsQuery(len(deps)), args...)
	return collect(rows, err, func(rows *sql.Rows) (CardRef, error) {
		var ref CardRef
		err := rows.Scan(&ref.Session, &ref.Card, &ref.Lane)
		return ref, err
	})
}

func dependentsQuery(n int) string {
	return "SELECT DISTINCT session, card, lane FROM card_deps WHERE dep IN (" + strings.TrimSuffix(strings.Repeat("?, ", n), ", ") + ") ORDER BY session, card"
}
