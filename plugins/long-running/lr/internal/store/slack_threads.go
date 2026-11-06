package store

import (
	"database/sql"
	"encoding/json"
	"errors"
)

const (
	threadRead      = "SELECT reply, card, lane, ledger_row, question, options, prs FROM slack_threads WHERE ts = ?"
	alertThreadRead = "SELECT reply, ts FROM alert_threads WHERE alert = ?"
)

type SlackThread struct {
	TS       string   `json:"-"`
	Reply    string   `json:"reply,omitempty"`
	Card     string   `json:"card,omitempty"`
	Lane     string   `json:"lane,omitempty"`
	Row      string   `json:"row,omitempty"`
	Question string   `json:"question,omitempty"`
	Options  []string `json:"options,omitempty"`
	PRs      []int    `json:"prs,omitempty"`
}

func (s *Store) PutSlackThread(thread SlackThread) error {
	options, err := listed(thread.Options)
	if err != nil {
		return err
	}
	prs, err := listed(thread.PRs)
	if err != nil {
		return err
	}
	_, err = s.db.Exec(`INSERT INTO slack_threads (ts, reply, card, lane, ledger_row, question, options, prs) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT (ts) DO UPDATE SET
		  reply = COALESCE(NULLIF(excluded.reply, ''), reply),
		  card = COALESCE(NULLIF(excluded.card, ''), card),
		  lane = COALESCE(NULLIF(lane, ''), excluded.lane),
		  ledger_row = COALESCE(NULLIF(excluded.ledger_row, ''), ledger_row),
		  question = COALESCE(NULLIF(excluded.question, ''), question),
		  options = COALESCE(NULLIF(excluded.options, ''), options),
		  prs = COALESCE(NULLIF(prs, ''), excluded.prs)`,
		thread.TS, thread.Reply, thread.Card, thread.Lane, thread.Row, thread.Question, options, prs)
	return err
}

func listed[T any](values []T) (string, error) {
	if len(values) == 0 {
		return "", nil
	}
	encoded, err := json.Marshal(values)
	return string(encoded), err
}

func (s *Store) SlackThreadAt(ts string) (SlackThread, bool, error) {
	thread := SlackThread{TS: ts}
	var options, prs string
	err := s.db.QueryRow(threadRead, ts).
		Scan(&thread.Reply, &thread.Card, &thread.Lane, &thread.Row, &thread.Question, &options, &prs)
	if errors.Is(err, sql.ErrNoRows) {
		return SlackThread{}, false, nil
	}
	if err != nil {
		return thread, false, err
	}
	if options != "" {
		if err := json.Unmarshal([]byte(options), &thread.Options); err != nil {
			return thread, false, err
		}
	}
	if prs != "" {
		if err := json.Unmarshal([]byte(prs), &thread.PRs); err != nil {
			return thread, false, err
		}
	}
	return thread, true, nil
}

type AlertThread struct {
	Reply string
	TS    string
}

func (s *Store) PutAlertThread(alert string, first AlertThread) error {
	_, err := s.db.Exec("INSERT INTO alert_threads (alert, reply, ts) VALUES (?, ?, ?) ON CONFLICT (alert) DO NOTHING", alert, first.Reply, first.TS)
	return err
}

func (s *Store) AlertThreadOf(alert string) (AlertThread, bool, error) {
	var first AlertThread
	err := s.db.QueryRow(alertThreadRead, alert).Scan(&first.Reply, &first.TS)
	if errors.Is(err, sql.ErrNoRows) {
		return AlertThread{}, false, nil
	}
	return first, err == nil, err
}
