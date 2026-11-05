package store

import (
	"database/sql"
	"errors"
)

func (s *Store) TakeCard(card, lane string) error {
	_, err := s.db.Exec("INSERT INTO card_takers (card, lane) VALUES (?, ?) ON CONFLICT (card) DO UPDATE SET lane = excluded.lane", card, lane)
	return err
}

func (s *Store) CardTaker(card string) (string, error) {
	var lane string
	err := s.db.QueryRow("SELECT lane FROM card_takers WHERE card = ?", card).Scan(&lane)
	if errors.Is(err, sql.ErrNoRows) {
		return "", nil
	}
	return lane, err
}
