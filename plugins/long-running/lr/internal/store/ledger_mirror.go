package store

import (
	"database/sql"
	"encoding/json"
	"time"
)

// PutLedgerRows replaces this store's copy of one ccn ledger. The mirror is the
// cutover seam: it lets lr asks and lr update render answer without a git object
// walk while ledger.py still writes the ledger, and it goes when ledger.py does,
// leaving the store as the only ledger.
func (s *Store) PutLedgerRows(ledger string, rows map[string]map[string]string, at time.Time) (err error) {
	tx, err := s.db.Begin()
	if err != nil {
		return err
	}
	defer func() {
		if err != nil {
			_ = tx.Rollback()
		}
	}()
	if _, err = tx.Exec("DELETE FROM ledger_rows WHERE ledger = ?", ledger); err != nil {
		return err
	}
	for key, fields := range rows {
		if err = putLedgerRow(tx, ledger, key, fields, at); err != nil {
			return err
		}
	}
	return tx.Commit()
}

func (s *Store) PutLedgerRow(ledger, key string, fields map[string]string, at time.Time) error {
	return putLedgerRow(s.db, ledger, key, fields, at)
}

type execer interface {
	Exec(query string, args ...any) (sql.Result, error)
}

func putLedgerRow(db execer, ledger, key string, fields map[string]string, at time.Time) error {
	encoded, err := json.Marshal(fields)
	if err != nil {
		return err
	}
	_, err = db.Exec(
		"INSERT INTO ledger_rows (ledger, key, fields, synced) VALUES (?, ?, ?, ?) ON CONFLICT (ledger, key) DO UPDATE SET fields = excluded.fields, synced = excluded.synced",
		ledger, key, string(encoded), Stamp(at),
	)
	return err
}

func (s *Store) LedgerRows(ledger string) (map[string]map[string]string, error) {
	rows, err := s.db.Query("SELECT key, fields FROM ledger_rows WHERE ledger = ?", ledger)
	found, err := collect(rows, err, func(rows *sql.Rows) ([2]string, error) {
		var pair [2]string
		err := rows.Scan(&pair[0], &pair[1])
		return pair, err
	})
	if err != nil {
		return nil, err
	}
	out := make(map[string]map[string]string, len(found))
	for _, pair := range found {
		var fields map[string]string
		if err := json.Unmarshal([]byte(pair[1]), &fields); err != nil {
			return nil, err
		}
		out[pair[0]] = fields
	}
	return out, nil
}

func (s *Store) LedgerSynced(ledger string) (time.Time, error) {
	var at time.Time
	err := s.db.QueryRow("SELECT MAX(synced) FROM ledger_rows WHERE ledger = ?", ledger).Scan(moment{&at})
	return at, err
}
