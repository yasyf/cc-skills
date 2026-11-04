package store

import "database/sql"

type Tx struct{ tx *sql.Tx }

func (s *Store) Update(apply func(*Tx) error) (err error) {
	tx, err := s.db.Begin()
	if err != nil {
		return err
	}
	defer func() {
		if err != nil {
			_ = tx.Rollback()
		}
	}()
	if err = apply(&Tx{tx}); err != nil {
		return err
	}
	return tx.Commit()
}

func (t *Tx) Advance(source string, position int64) error {
	return advance(t.tx, source, position)
}
