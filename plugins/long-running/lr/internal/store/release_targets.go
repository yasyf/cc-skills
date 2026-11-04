package store

import (
	"encoding/json"
	"fmt"
	"time"
)

func (s *Store) ReleaseTargets() (map[string][]string, error) {
	rows, err := s.db.Query("SELECT sha, targets FROM release_targets")
	if err != nil {
		return nil, err
	}
	defer func() { _ = rows.Close() }()
	found := map[string][]string{}
	for rows.Next() {
		var sha, encoded string
		if err := rows.Scan(&sha, &encoded); err != nil {
			return nil, err
		}
		var targets []string
		if err := json.Unmarshal([]byte(encoded), &targets); err != nil {
			return nil, fmt.Errorf("release targets of %s: %w", sha, err)
		}
		found[sha] = targets
	}
	return found, rows.Err()
}

func (s *Store) PutReleaseTargets(sha string, targets []string, landed time.Time) error {
	encoded, err := json.Marshal(targets)
	if err != nil {
		return err
	}
	_, err = s.db.Exec(
		"INSERT INTO release_targets (sha, targets, landed) VALUES (?, ?, ?) ON CONFLICT (sha) DO UPDATE SET targets = excluded.targets, landed = excluded.landed",
		sha, string(encoded), Stamp(landed),
	)
	return err
}

func (s *Store) DropReleaseTargets(before time.Time) error {
	_, err := s.db.Exec("DELETE FROM release_targets WHERE landed < ?", Stamp(before))
	return err
}
