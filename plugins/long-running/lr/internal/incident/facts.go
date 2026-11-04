package incident

import (
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"slices"
	"strings"
)

const (
	factDict  = "dict"
	factValue = "value"
)

type factRow struct {
	path, parent, key, kind string
	record                  sql.NullString
}

func factPath(parent, key string) string {
	return parent + "/" + strings.NewReplacer("~", "~0", "/", "~1").Replace(key)
}

func scanFacts(rows *sql.Rows) ([]factRow, error) {
	defer rows.Close()
	var found []factRow
	for rows.Next() {
		var row factRow
		if err := rows.Scan(&row.path, &row.parent, &row.key, &row.kind, &row.record); err != nil {
			return nil, err
		}
		found = append(found, row)
	}
	return found, rows.Err()
}

func factTree(found []factRow, parent string) (map[string]any, error) {
	nodes := map[string]map[string]any{parent: {}}
	for _, row := range found {
		if row.kind == factDict {
			nodes[row.path] = map[string]any{}
		}
	}
	for _, row := range found {
		if row.kind == factDict {
			nodes[row.parent][row.key] = nodes[row.path]
			continue
		}
		var value any
		if err := json.Unmarshal([]byte(row.record.String), &value); err != nil {
			return nil, fmt.Errorf("fact %s: %w", row.path, err)
		}
		nodes[row.parent][row.key] = value
	}
	return nodes[parent], nil
}

func readAllFacts(tx *sql.Tx) (map[string]any, error) {
	rows, err := tx.Query("SELECT path, parent, key, kind, record FROM fact ORDER BY ordinal")
	if err != nil {
		return nil, err
	}
	found, err := scanFacts(rows)
	if err != nil {
		return nil, err
	}
	return factTree(found, "")
}

func readFacts(tx *sql.Tx, keys []string) (Facts, error) {
	var found []factRow
	for _, key := range keys {
		path := factPath("", key)
		rows, err := tx.Query("SELECT path, parent, key, kind, record FROM fact WHERE path = ? OR (path >= ? AND path < ?) ORDER BY ordinal", path, path+"/", path+"0")
		if err != nil {
			return Facts{}, err
		}
		subtree, err := scanFacts(rows)
		if err != nil {
			return Facts{}, err
		}
		found = append(found, subtree...)
	}
	tree, err := factTree(found, "")
	if err != nil {
		return Facts{}, err
	}
	raw, err := json.Marshal(tree)
	if err != nil {
		return Facts{}, err
	}
	var facts Facts
	return facts, json.Unmarshal(raw, &facts)
}

func normalize(value any) (any, error) {
	raw, err := json.Marshal(value)
	if err != nil {
		return nil, err
	}
	var normal any
	return normal, json.Unmarshal(raw, &normal)
}

func readFact(tx *sql.Tx, parent, key string) (any, bool, error) {
	path := factPath(parent, key)
	rows, err := tx.Query("SELECT path, parent, key, kind, record FROM fact WHERE path = ? OR (path >= ? AND path < ?) ORDER BY ordinal", path, path+"/", path+"0")
	if err != nil {
		return nil, false, err
	}
	found, err := scanFacts(rows)
	if err != nil {
		return nil, false, err
	}
	tree, err := factTree(found, parent)
	if err != nil {
		return nil, false, err
	}
	value, present := tree[key]
	return value, present, nil
}

func dropFact(tx *sql.Tx, parent, key string) error {
	path := factPath(parent, key)
	_, err := tx.Exec("DELETE FROM fact WHERE path = ? OR (path >= ? AND path < ?)", path, path+"/", path+"0")
	return err
}

func setFact(tx *sql.Tx, parent, key string, value any) error {
	path := factPath(parent, key)
	children, isDict := value.(map[string]any)
	kind, record := factValue, sql.NullString{}
	if isDict {
		kind = factDict
	} else {
		encoded, err := encode(value)
		if err != nil {
			return err
		}
		record = sql.NullString{String: encoded, Valid: true}
	}
	var heldKind string
	var heldRecord sql.NullString
	err := tx.QueryRow("SELECT kind, record FROM fact WHERE path = ?", path).Scan(&heldKind, &heldRecord)
	switch {
	case errors.Is(err, sql.ErrNoRows):
		if _, err := tx.Exec("INSERT INTO fact(path, parent, key, kind, record) VALUES (?, ?, ?, ?, ?)", path, parent, key, kind, record); err != nil {
			return err
		}
	case err != nil:
		return err
	case heldKind != kind || heldRecord != record:
		if heldKind == factDict {
			if err := removeDescendants(tx, path); err != nil {
				return err
			}
		}
		if _, err := tx.Exec("UPDATE fact SET kind = ?, record = ? WHERE path = ?", kind, record, path); err != nil {
			return err
		}
	}
	if !isDict {
		return nil
	}
	return replaceChildren(tx, path, children)
}

func replaceChildren(tx *sql.Tx, path string, children map[string]any) error {
	rows, err := tx.Query("SELECT key FROM fact WHERE parent = ?", path)
	if err != nil {
		return err
	}
	defer rows.Close()
	var stale []string
	for rows.Next() {
		var key string
		if err := rows.Scan(&key); err != nil {
			return err
		}
		if _, kept := children[key]; !kept {
			stale = append(stale, key)
		}
	}
	if err := rows.Err(); err != nil {
		return err
	}
	if err := rows.Close(); err != nil {
		return err
	}
	for _, key := range stale {
		child := factPath(path, key)
		if err := removeDescendants(tx, child); err != nil {
			return err
		}
		if _, err := tx.Exec("DELETE FROM fact WHERE path = ?", child); err != nil {
			return err
		}
	}
	keys := make([]string, 0, len(children))
	for key := range children {
		keys = append(keys, key)
	}
	slices.Sort(keys)
	for _, key := range keys {
		if err := setFact(tx, path, key, children[key]); err != nil {
			return err
		}
	}
	return nil
}

func removeDescendants(tx *sql.Tx, path string) error {
	_, err := tx.Exec("WITH RECURSIVE subtree(path) AS (SELECT path FROM fact WHERE parent = ? UNION ALL SELECT fact.path FROM fact JOIN subtree ON fact.parent = subtree.path) DELETE FROM fact WHERE path IN (SELECT path FROM subtree)", path)
	return err
}
