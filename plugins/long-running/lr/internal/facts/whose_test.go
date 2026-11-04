package facts_test

import (
	"context"
	"slices"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/facts"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func whoseSource(t *testing.T) (facts.Source, *shelltest.Fake) {
	t.Helper()
	s, err := store.Memory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	runner := &shelltest.Fake{}
	runner.Reply(`{"author":{"login":"andrew","name":"Andrew Benton"}}`, "ccx", "vcs", "gh", "--max-age", "5m", "--", "pr", "view", "46018")
	runner.Reply(`{"author":{"login":"drive-bot","name":""}}`, "ccx", "vcs", "gh", "--max-age", "5m", "--", "pr", "view")
	return facts.Source{Repo: repo, Author: "drive-bot", Runner: runner, CCX: ccx.CLI{Runner: runner}, Store: s, Now: time.Now}, runner
}

func TestWhoseNamesTheAuthorAndWhetherTheDriveOwnsIt(t *testing.T) {
	source, _ := whoseSource(t)
	if err := source.Store.Own("chat-version-land-1", []int{49336}); err != nil {
		t.Fatal(err)
	}
	if _, err := source.Store.PutFact(repo, 46019, head, "tap", "tapped", time.Now()); err != nil {
		t.Fatal(err)
	}
	got, err := source.Whose(context.Background(), []int{46018, 49336, 46019, 47000})
	if err != nil {
		t.Fatal(err)
	}
	want := []facts.Whose{
		{Number: 46018, Author: "andrew", Name: "Andrew Benton", Foreign: true},
		{Number: 49336, Author: "drive-bot", Lane: "chat-version-land-1", Ours: true},
		{Number: 46019, Author: "drive-bot", Ours: true},
		{Number: 47000, Author: "drive-bot"},
	}
	if !slices.Equal(got, want) {
		t.Fatalf("whose = %+v; want %+v", got, want)
	}
}

func TestWhoseReadsEachAuthorOnceAcrossPosts(t *testing.T) {
	reads := func(posts int) int {
		source, runner := whoseSource(t)
		for range posts {
			if _, err := source.Whose(context.Background(), []int{46018, 49336}); err != nil {
				t.Fatal(err)
			}
		}
		return len(runner.Calls())
	}
	if one, thousand := reads(1), reads(1000); one != 2 || thousand != one {
		t.Fatalf("GitHub reads at 1 post = %d, at 1,000 posts = %d; want 2 for both, one per pull request", one, thousand)
	}
}
