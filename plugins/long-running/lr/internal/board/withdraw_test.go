package board

import (
	"bytes"
	"context"
	"reflect"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const why = "being redone after review"

func tapCards(t *testing.T) (*cardsFixture, *cardShell, *store.Store) {
	t.Helper()
	s := withWords(t)
	if err := s.PutPR(store.PR{Repo: repo, Number: number, Head: head, Lane: "lane-7"}); err != nil {
		t.Fatal(err)
	}
	on := &cardShell{cards: []string{MergeWaits(number, head)}}
	f := newCards(t, on, nil)
	f.Store = s
	return f, on, s
}

func TestWithdrawingATapTakesItOffTheMergeListTheBoardAndSlackAndARerunChangesNothing(t *testing.T) {
	f, on, s := tapCards(t)
	ctx := context.Background()
	var out bytes.Buffer
	pulled, err := f.Pull(ctx, "meta-desk", "#45583", why, &out)
	if err != nil || !pulled {
		t.Fatalf("pulled = %v, %v", pulled, err)
	}
	if found := rows(t, s, facts(nil)); len(found) != 0 {
		t.Fatalf("the merge list still asks %+v", found)
	}
	if _, ok := Card(repo, rows(t, s, facts(nil)), number); ok {
		t.Fatal("lr board card still draws the withdrawn tap")
	}
	stored, err := StoredRows(s, repo, []int{number})
	if err != nil || len(stored) != 0 {
		t.Fatalf("stored rows = %+v, %v", stored, err)
	}
	if want := []string{MergeWaits(number, head)}; !reflect.DeepEqual(on.removed, want) || !reflect.DeepEqual(on.reasons, []string{why}) {
		t.Fatalf("removed = %v with %q", on.removed, on.reasons)
	}
	on.cards = nil
	out.Reset()
	pulled, err = f.Pull(ctx, "meta-desk", "#45583", why, &out)
	if err != nil || pulled || len(on.removed) != 1 || len(f.posted) != 2 {
		t.Fatalf("a rerun pulled %v, removed %v, posted %q, err %v", pulled, on.removed, f.posted, err)
	}
	if !strings.Contains(out.String(), "UNCHANGED") {
		t.Fatalf("out = %s", out.String())
	}
}

func TestANewHeadAfterAWithdrawnTapAsksAgain(t *testing.T) {
	f, _, s := tapCards(t)
	if _, err := f.Pull(context.Background(), "meta-desk", MergeWaits(number, head), why, &bytes.Buffer{}); err != nil {
		t.Fatal(err)
	}
	openTap(t, s, moved, t0)
	found := rows(t, s, facts(func(f *machine.Facts) { f.Head = moved }))
	if len(found) != 1 || found[0].Head != moved {
		t.Fatalf("rows = %+v", found)
	}
}

func TestWithdrawingAnAskCardRemovesOnlyThatCard(t *testing.T) {
	on := &cardShell{cards: []string{"card-pick-1", "card-pick-2"}}
	f := newCards(t, on, nil)
	pulled, err := f.Pull(context.Background(), "meta-desk", "card-pick-1", why, &bytes.Buffer{})
	if err != nil || pulled {
		t.Fatalf("pulled = %v, %v", pulled, err)
	}
	if !reflect.DeepEqual(on.removed, []string{"card-pick-1"}) {
		t.Fatalf("removed = %v", on.removed)
	}
}

func TestAWhyNamingAnIDIsRefused(t *testing.T) {
	on := &cardShell{cards: []string{"card-pick-1"}}
	f := newCards(t, on, nil)
	for _, said := range []string{"", "superseded by #49700", "see card-pick-2", "moved to merge:49622:abc1234"} {
		if _, err := f.Pull(context.Background(), "meta-desk", "card-pick-1", said, &bytes.Buffer{}); err == nil {
			t.Fatalf("--why %q went to the owner", said)
		}
	}
	if len(on.removed) != 0 {
		t.Fatalf("removed = %v", on.removed)
	}
}
