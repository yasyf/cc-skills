package store

import (
	"database/sql"
	"encoding/json"
	"time"
)

type BranchCard struct {
	Lane     string
	Card     string
	Answered time.Time
	Ordered  time.Time
	Held     []int
}

func (s *Store) PutBranchCards(lane string, cards []string) error {
	for _, card := range cards {
		if _, err := s.db.Exec("INSERT OR IGNORE INTO branch_cards (lane, card) VALUES (?, ?)", lane, card); err != nil {
			return err
		}
	}
	return nil
}

func (s *Store) BranchCards() ([]BranchCard, error) {
	rows, err := s.db.Query("SELECT lane, card, answered, ordered, held FROM branch_cards ORDER BY lane, card")
	return collect(rows, err, func(rows *sql.Rows) (BranchCard, error) {
		var c BranchCard
		var held string
		if err := rows.Scan(&c.Lane, &c.Card, moment{&c.Answered}, moment{&c.Ordered}, nullable[string]{&held}); err != nil || held == "" {
			return c, err
		}
		return c, json.Unmarshal([]byte(held), &c.Held)
	})
}

func (s *Store) AnswerBranchCard(lane, card string, at time.Time) error {
	_, err := s.db.Exec("UPDATE branch_cards SET answered = ? WHERE lane = ? AND card = ? AND answered IS NULL", Stamp(at), lane, card)
	return err
}

func (s *Store) OrderBranch(lane, holder string, held []int, at time.Time) error {
	encoded, err := json.Marshal(held)
	if err != nil {
		return err
	}
	_, err = s.db.Exec("UPDATE OR REPLACE branch_cards SET lane = ?, ordered = ?, held = ? WHERE lane = ?", holder, Stamp(at), string(encoded), lane)
	return err
}

func (s *Store) DropBranch(lane string) error {
	_, err := s.db.Exec("DELETE FROM branch_cards WHERE lane = ?", lane)
	return err
}
