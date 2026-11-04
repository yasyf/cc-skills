package store

import (
	"path/filepath"
	"testing"
)

func TestAReviewRowsTableOfTheOldShapeIsRebuiltAndRefolded(t *testing.T) {
	path := filepath.Join(t.TempDir(), "lr.sqlite")
	s, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.db.Exec("DROP TABLE review_rows; CREATE TABLE review_rows (repo TEXT, number INTEGER, drawn_at TEXT); INSERT INTO review_rows VALUES ('o/r', 1, 'then')"); err != nil {
		t.Fatal(err)
	}
	if err := s.Advance(ReviewRowsCursor, 9); err != nil {
		t.Fatal(err)
	}
	if err := s.Advance("other", 9); err != nil {
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

	rows, err := s.ReviewRows("o/r")
	if err != nil || len(rows) != 0 {
		t.Fatalf("rows = %+v, %v, want an empty table of the current shape", rows, err)
	}
	if position, err := s.Cursor(ReviewRowsCursor); err != nil || position != 0 {
		t.Fatalf("cursor = %d, %v, want it reset so the fold reads its records again", position, err)
	}
	if position, err := s.Cursor("other"); err != nil || position != 9 {
		t.Fatalf("another cursor = %d, %v, want it kept", position, err)
	}
}
