package askdesk

import (
	"context"
	"strings"
	"testing"
)

func TestAStoreDeskAskForACardIsRefusedNamingAskOwnerAndTheReadyCardsPass(t *testing.T) {
	read := StoreReads(map[string]Serve{"board rows": nil})

	card := read(context.Background(), "Please make the owner tap card for landing diagnostic PR #51773 at 3a2d276")
	other := read(context.Background(), "lr runner show")

	if want := failedRead + "the store desk serves only lr board rows" + cardPaths; card.Head != want {
		t.Fatalf("a card ask was refused with %q, want %q", card.Head, want)
	}
	if strings.Contains(other.Head, cardPaths) {
		t.Fatalf("an ask naming no card was refused with the card paths: %q", other.Head)
	}
}
