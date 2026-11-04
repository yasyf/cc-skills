package store

import (
	"path/filepath"
	"testing"
	"time"
)

func TestOrderingABranchOntoALaneHoldingTheSameCardKeepsOneRow(t *testing.T) {
	s, err := Open(filepath.Join(t.TempDir(), "lr.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = s.Close() }()
	if err := s.PutBranchCards("port-1", []string{"card-a", "card-b"}); err != nil {
		t.Fatal(err)
	}
	if err := s.PutBranchCards("port-2", []string{"card-a"}); err != nil {
		t.Fatal(err)
	}
	if err := s.OrderBranch("port-1", "port-2", []int{7}, time.Date(2026, 11, 4, 10, 0, 0, 0, time.UTC)); err != nil {
		t.Fatalf("a successor already holding the card stopped the order: %v", err)
	}
	cards, err := s.BranchCards()
	if err != nil {
		t.Fatal(err)
	}
	if len(cards) != 2 {
		t.Fatalf("cards = %+v, want card-a and card-b once each", cards)
	}
	for _, card := range cards {
		if card.Lane != "port-2" || card.Ordered.IsZero() || len(card.Held) != 1 {
			t.Fatalf("card = %+v, want it ordered on port-2", card)
		}
	}
}
