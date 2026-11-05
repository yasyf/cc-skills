package landing_test

import (
	"context"
	"slices"
	"strings"
	"testing"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/landing"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
)

func TestAStuckHeadIsSaidOnceAndClearedWhenItCanMove(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	ctx := context.Background()
	s := newStore(t)
	const why = "its grade waits on the pool"
	said := func() []client.Record {
		t.Helper()
		records, err := client.Tail(ctx, client.Query{Drive: "d", Kinds: []string{bus.StuckKind}})
		if err != nil {
			t.Fatal(err)
		}
		return records
	}
	fresh := facts(1, nil)

	if err := landing.SayStuck(ctx, s, "d", []machine.Facts{fresh}, map[int]string{1: why}); err != nil {
		t.Fatal(err)
	}
	first := said()
	if len(first) != 1 || !slices.Contains(first[0].To, "root") {
		t.Fatalf("first said = %+v, want one record to root", first)
	}
	folded := bus.LegacyFacts(first[0], fresh.Repo, nil)
	if len(folded) != 1 || folded[0].Kind != bus.StuckFact || folded[0].Head != fresh.Head || !strings.HasSuffix(folded[0].Value, why) {
		t.Fatalf("folded = %+v", folded)
	}

	known := facts(1, func(f *machine.Facts) { f.Stuck = why })
	if err := landing.SayStuck(ctx, s, "d", []machine.Facts{known}, map[int]string{1: why}); err != nil {
		t.Fatal(err)
	}
	if got := said(); len(got) != 1 {
		t.Fatalf("an unchanged reason posted again: %+v", got)
	}

	if err := landing.SayStuck(ctx, s, "d", []machine.Facts{known}, map[int]string{}); err != nil {
		t.Fatal(err)
	}
	cleared := said()
	if len(cleared) != 2 {
		t.Fatalf("a head that can move again posted %d records, want a second", len(cleared))
	}
	if folded := bus.LegacyFacts(cleared[1], fresh.Repo, nil); len(folded) != 1 || folded[0].Value != "" {
		t.Fatalf("the clear folded to %+v", folded)
	}
}
