package store

import (
	"path/filepath"
	"testing"
)

func TestOpeningAStoreDropsTheRetiredPRStateTables(t *testing.T) {
	path := filepath.Join(t.TempDir(), "lr.sqlite")
	s, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.db.Exec("CREATE TABLE pr_states (repo TEXT); CREATE TABLE pr_facts (repo TEXT); CREATE TABLE taps (repo TEXT); CREATE TABLE queue (repo TEXT)"); err != nil {
		t.Fatal(err)
	}
	if err := s.Close(); err != nil {
		t.Fatal(err)
	}

	s, err = Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = s.Close() }()

	var left int
	if err := s.db.QueryRow("SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name IN ('pr_states', 'pr_facts', 'taps', 'queue')").Scan(&left); err != nil {
		t.Fatal(err)
	}
	if left != 0 {
		t.Fatalf("%d retired tables left, want none", left)
	}
}
