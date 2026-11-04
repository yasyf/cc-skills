package incident

import (
	"database/sql"
	"fmt"
)

const matchingFactEntries = `
SELECT entry.key FROM fact AS field
JOIN fact AS entry ON entry.path = field.parent
WHERE field.kind = 'value' AND field.key = ? AND field.record = ?
AND field.parent >= ? AND field.parent < ? AND entry.parent = ?
ORDER BY entry.key`

func factGroup(tx *sql.Tx, key string) (string, error) {
	path := factPath("", key)
	var kind string
	if err := tx.QueryRow("SELECT kind FROM fact WHERE path = ?", path).Scan(&kind); err != nil {
		return "", fmt.Errorf("fact group %s: %w", key, err)
	}
	if kind != factDict {
		return "", fmt.Errorf("fact group %s is %s, want %s", key, kind, factDict)
	}
	return path, nil
}

func factKeys(rows *sql.Rows) ([]string, error) {
	defer rows.Close()
	var keys []string
	for rows.Next() {
		var key string
		if err := rows.Scan(&key); err != nil {
			return nil, err
		}
		keys = append(keys, key)
	}
	return keys, rows.Err()
}

func (s Store) FactEntry(id, group, key string) (any, bool, error) {
	var value any
	var found bool
	err := s.read(id, func(tx *sql.Tx) error {
		parent, err := factGroup(tx, group)
		if err != nil {
			return err
		}
		value, found, err = readFact(tx, parent, key)
		return err
	})
	return value, found, err
}

func (s Store) FactEntryKeys(id, group string) ([]string, error) {
	var keys []string
	err := s.read(id, func(tx *sql.Tx) error {
		parent, err := factGroup(tx, group)
		if err != nil {
			return err
		}
		rows, err := tx.Query("SELECT key FROM fact WHERE parent = ? ORDER BY key", parent)
		if err != nil {
			return err
		}
		keys, err = factKeys(rows)
		return err
	})
	return keys, err
}

func (s Store) MatchingFactEntries(id, group, field string, value any) ([]string, error) {
	record, err := encode(value)
	if err != nil {
		return nil, err
	}
	var keys []string
	err = s.read(id, func(tx *sql.Tx) error {
		parent, err := factGroup(tx, group)
		if err != nil {
			return err
		}
		rows, err := tx.Query(matchingFactEntries, field, record, parent+"/", parent+"0", parent)
		if err != nil {
			return err
		}
		keys, err = factKeys(rows)
		return err
	})
	return keys, err
}

func (t *Tx) SetFactEntry(group, key string, value any) error {
	parent, err := factGroup(t.sql, group)
	if err != nil {
		return err
	}
	normal, err := normalize(value)
	if err != nil {
		return err
	}
	return setFact(t.sql, parent, key, normal)
}

func (t *Tx) DropFactEntry(group, key string) error {
	parent, err := factGroup(t.sql, group)
	if err != nil {
		return err
	}
	return dropFact(t.sql, parent, key)
}
