package board

import (
	"bytes"
	"context"
	"reflect"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const why = "being redone after review"

func tapCards(t *testing.T) (*cardsFixture, *cardShell, *store.Store) {
	t.Helper()
	s := withLedger(t, readyRow(nil))
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
	if found, err := waitingRows(s, repo, []int{number}); err != nil || len(found) != 0 {
		t.Fatalf("the ledger still has it waiting on him: %+v, %v", found, err)
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
