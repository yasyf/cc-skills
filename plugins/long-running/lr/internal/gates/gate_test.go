package gates

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"slices"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const clearFact = "gate-clear"

type told struct{ kind, id, text string }

func keeper(s *store.Store, said *[]told) Keeper {
	return Keeper{
		Store: s,
		Now:   func() time.Time { return t0 },
		Log:   slog.New(slog.NewTextHandler(io.Discard, nil)),
		Tell: func(_ context.Context, _ machine.Facts, kind, id, text string) {
			*said = append(*said, told{kind, id, text})
		},
	}
}

func checking(reads *int, verdict Verdict, err error) func(context.Context, machine.Facts) (Verdict, error) {
	return func(context.Context, machine.Facts) (Verdict, error) {
		*reads++
		return verdict, err
	}
}

func TestARefusalTellsOnceUnderItsCauseAndTheOtherGatesStillRead(t *testing.T) {
	var said []told
	var reads int
	refused := keeper(withFacts(t, 1, nil), &said).Refuses(context.Background(), green,
		Gate{Name: "first", Kind: "blocker", Check: checking(&reads, Verdict{Cause: "broke", Refusal: "it broke"}, nil)},
		Gate{Name: "second", Kind: "report", Check: checking(&reads, Verdict{}, nil)},
	)
	if !refused || reads != 2 {
		t.Fatalf("refused = %v, reads = %d", refused, reads)
	}
	want := []told{{"blocker", "broke:1:" + newHead, "it broke"}}
	if !slices.Equal(said, want) {
		t.Fatalf("said = %+v, want %+v", said, want)
	}
}

func TestAHeldHeadStaysRefusedWithoutTellingAgain(t *testing.T) {
	var said []told
	var reads int
	s := withFacts(t, 1, nil)
	gate := Gate{Name: "held", Kind: "report", Clear: clearFact, Check: checking(&reads, Verdict{Held: true}, nil)}
	if !keeper(s, &said).Refuses(context.Background(), green, gate) {
		t.Fatal("a held head passed")
	}
	if len(said) != 0 {
		t.Fatalf("said = %+v", said)
	}
	if got := factsOn(t, s, 1, newHead)[clearFact]; got != "" {
		t.Fatalf("a held head kept its clear: %q", got)
	}
}

func TestAGateThatDoesNotReadPassesTheHeadAndKeepsNoClear(t *testing.T) {
	var said []told
	var reads int
	s := withFacts(t, 1, nil)
	gate := Gate{Name: "broken", Kind: "blocker", Clear: clearFact, Check: checking(&reads, Verdict{}, errors.New("boom"))}
	if keeper(s, &said).Refuses(context.Background(), green, gate) {
		t.Fatal("an unread gate refused")
	}
	if len(said) != 0 || factsOn(t, s, 1, newHead)[clearFact] != "" {
		t.Fatalf("said = %+v, facts = %v", said, factsOn(t, s, 1, newHead))
	}
}

func TestAClearThatDoesNotReadPassesTheHeadWithoutCheckingIt(t *testing.T) {
	var said []told
	var reads int
	s := withFacts(t, 1, nil)
	if err := s.Close(); err != nil {
		t.Fatal(err)
	}
	gate := Gate{Name: "unread", Kind: "blocker", Clear: clearFact, Check: checking(&reads, Verdict{Refusal: "no"}, nil)}
	if keeper(s, &said).Refuses(context.Background(), green, gate) || reads != 0 {
		t.Fatalf("reads = %d, said = %+v", reads, said)
	}
}

func TestAPassKeepsItsClearAndTheHeadIsNotReadAgain(t *testing.T) {
	var said []told
	var reads int
	k := keeper(withFacts(t, 1, nil), &said)
	gate := Gate{Name: "pass", Kind: "report", Clear: clearFact, Check: checking(&reads, Verdict{}, nil)}
	for range 2 {
		if k.Refuses(context.Background(), green, gate) {
			t.Fatal("a passing gate refused")
		}
	}
	if reads != 1 {
		t.Fatalf("reads = %d, want one", reads)
	}
}

func TestAPassThatMustRereadIsReadAgainNextPass(t *testing.T) {
	var said []told
	var reads int
	k := keeper(withFacts(t, 1, nil), &said)
	gate := Gate{Name: "unsettled", Kind: "report", Clear: clearFact, Check: checking(&reads, Verdict{Reread: true}, nil)}
	for range 2 {
		if k.Refuses(context.Background(), green, gate) {
			t.Fatal("an unsettled gate refused")
		}
	}
	if reads != 2 {
		t.Fatalf("reads = %d, want two", reads)
	}
}

func TestAGateWithNoClearTouchesNoFactAndReadsEveryPass(t *testing.T) {
	var said []told
	var reads int
	s := withFacts(t, 1, nil)
	k := keeper(s, &said)
	gate := Gate{Name: "always", Kind: "blocker", Check: checking(&reads, Verdict{}, nil)}
	for range 2 {
		if k.Refuses(context.Background(), green, gate) {
			t.Fatal("a passing gate refused")
		}
	}
	if reads != 2 || len(factsOn(t, s, 1, newHead)) != 0 {
		t.Fatalf("reads = %d, facts = %v", reads, factsOn(t, s, 1, newHead))
	}
}

func TestARefusalKeepsItsCauseOnTheHead(t *testing.T) {
	var said []told
	var reads int
	s := withFacts(t, 1, nil)

	keeper(s, &said).Refuses(context.Background(), green, Gate{Name: "first", Kind: "blocker", Check: checking(&reads, Verdict{Cause: "broke", Refusal: "it broke"}, nil)})

	facts, err := s.Facts(green.Repo, green.Number, green.Head)
	if err != nil {
		t.Fatal(err)
	}
	if facts[RefusedFact] != "broke" {
		t.Fatalf("facts = %v", facts)
	}
}
