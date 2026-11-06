package askdesk

import (
	"context"
	"slices"
	"strings"
	"testing"
)

func TestTheStoreDeskRunsOnlyAListedVerbAndNamesTheCardPathsWhenAskedForACard(t *testing.T) {
	var ran [][]string
	read := StoreRuns("/mac/lr.json", func(_ context.Context, argv []string, _ string) (string, string, int, error) {
		ran = append(ran, argv)
		return "", "", 0, nil
	})

	card := read(context.Background(), "Please make the owner tap card for landing diagnostic PR #51773 at 3a2d276")
	unlisted := read(context.Background(), `{"verb": "update render", "args": ["--publish", "--"]}`)
	listed := read(context.Background(), `{"verb": "board waits", "args": ["--pr=51766", "--"]}`)

	if !strings.HasPrefix(card.Head, failedRead) || !strings.HasSuffix(card.Head, cardPaths) {
		t.Fatalf("a card ask was answered %q, want a refusal naming the card paths", card.Head)
	}
	if !strings.HasPrefix(unlisted.Head, failedRead) || strings.Contains(unlisted.Head, cardPaths) {
		t.Fatalf("an unlisted verb was answered %q, want a plain refusal", unlisted.Head)
	}
	if want := [][]string{{"board", "waits", "--config", "/mac/lr.json", "--pr=51766", "--"}}; !slices.EqualFunc(ran, want, slices.Equal) || strings.HasPrefix(listed.Head, failedRead) {
		t.Fatalf("the desk ran %q and answered %q, want only %q", ran, listed.Head, want)
	}
}
