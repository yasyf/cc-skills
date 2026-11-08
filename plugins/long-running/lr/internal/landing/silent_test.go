package landing_test

import (
	"context"
	"slices"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/landing"
)

func TestRootAndTheMetaDeskHearOnceWhenNoLandingPassEndsFor15MinutesAndOnceWhenOneDoes(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	ctx := context.Background()
	s := newStore(t)
	check := func(at time.Time) {
		t.Helper()
		if err := (landing.Silence{Store: s, Drive: "d", Now: func() time.Time { return at }}).Check(ctx); err != nil {
			t.Fatal(err)
		}
	}
	told := func() []client.Record {
		t.Helper()
		records, err := client.Tail(ctx, client.Query{Drive: "d", Kinds: []string{landing.SilentKind}})
		if err != nil {
			t.Fatal(err)
		}
		return records
	}
	passEnded := func() time.Time {
		t.Helper()
		if err := landing.SayPassEnded(ctx, "d", 166, []string{"#1 at 1111111 on his tap at this head"}, 9*time.Minute); err != nil {
			t.Fatal(err)
		}
		return time.Now()
	}

	check(time.Now().Add(time.Hour))
	if got := told(); len(got) != 0 {
		t.Fatalf("a drive whose landing host never posted a pass was called silent: %+v", got)
	}

	first := passEnded()
	check(first.Add(time.Minute))
	check(first.Add(landing.SilentAfter + time.Minute))
	check(first.Add(landing.SilentAfter + 2*time.Minute))
	silent := told()
	if len(silent) != 1 || silent[0].Fields.Attrs["state"] != "silent" || !toRootAndMeta(silent[0]) {
		t.Fatalf("told = %+v, want one silent record to root and meta-desk", silent)
	}

	second := passEnded()
	check(second.Add(time.Minute))
	check(second.Add(2 * time.Minute))
	recovered := told()
	if len(recovered) != 2 || recovered[1].Fields.Attrs["state"] != "recovered" || !toRootAndMeta(recovered[1]) {
		t.Fatalf("told = %+v, want one recovery after the silence", recovered)
	}

	check(second.Add(landing.SilentAfter + 4*time.Minute))
	if again := told(); len(again) != 3 || again[2].Fields.Attrs["state"] != "silent" {
		t.Fatalf("told = %+v, want a second silence raised after the recovery", again)
	}
}

func toRootAndMeta(record client.Record) bool {
	return len(record.To) == 2 && slices.Contains(record.To, "root") && slices.Contains(record.To, "meta-desk")
}
