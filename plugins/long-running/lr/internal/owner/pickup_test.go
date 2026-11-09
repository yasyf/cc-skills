package owner

import (
	"bytes"
	"context"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

type heldDesk struct {
	desk
	held []func()
}

func newHeldDesk(t *testing.T) *heldDesk {
	h := &heldDesk{}
	h.desk = desk{dir: t.TempDir(), now: func() time.Time { return time.Date(2026, 11, 9, 6, 31, 0, 0, time.UTC) }, after: func(d time.Duration, f func()) {
		if d != holdAfter {
			t.Fatalf("hold after %s, want %s", d, holdAfter)
		}
		h.held = append(h.held, f)
	}}
	return h
}

func watchedBy(t *testing.T, h *heldDesk) *shelltest.Fake {
	t.Helper()
	runner := watched(hisEvent)
	runner.Reply(`{"ok":true}`, "cc-slack", "reply")
	if err := watch(context.Background(), runner, noThreads, noRows, h.desk, &bytes.Buffer{}); err != nil {
		t.Fatal(err)
	}
	return runner.Fake
}

func verbs(fake *shelltest.Fake, verb string) [][]string {
	var found [][]string
	for _, call := range fake.Calls() {
		if len(call.Argv) > 1 && call.Argv[1] == verb {
			found = append(found, call.Argv[2:])
		}
	}
	return found
}

func TestHisMessageUnansweredAfterThirtySecondsGetsOneHoldingReplyWithAClock(t *testing.T) {
	h := newHeldDesk(t)
	fake := watchedBy(t, h)

	if len(h.held) != 1 {
		t.Fatalf("holds = %d, want one for his message", len(h.held))
	}
	h.held[0]()

	replies := verbs(fake, "reply")
	if len(replies) != 1 {
		t.Fatalf("replies = %q, want one holding reply", replies)
	}
	want := []string{"--channel", "D0B6Z", "--thread", "1.0", "--text", "Picked up: Reading your message. You get the answer in this thread by 10:36PM."}
	if !slices.Equal(replies[0], want) {
		t.Fatalf("holding reply = %q, want %q", replies[0], want)
	}
}

func TestAnAnswerInHisThreadCancelsTheHoldingReply(t *testing.T) {
	h := newHeldDesk(t)
	fake := watchedBy(t, h)

	if err := Answered(h.dir, "1.0"); err != nil {
		t.Fatal(err)
	}
	h.held[0]()

	if replies := verbs(fake, "reply"); len(replies) != 0 {
		t.Fatalf("replies = %q, want none once his thread was answered", replies)
	}
}

func TestTheWorkingLineMovesOnlyWhenTheStateChanges(t *testing.T) {
	h := newHeldDesk(t)
	watchedBy(t, h)
	fake := &shelltest.Fake{}
	fake.Reply(`{"ok":true}`, "cc-slack", "pickup")

	for _, state := range []string{"Waiting for a subagent…", "Waiting for a subagent…", "Writing the reply…"} {
		if err := working(context.Background(), fake, h.dir, state); err != nil {
			t.Fatal(err)
		}
	}

	got := verbs(fake, "pickup")
	if len(got) != 2 || !strings.HasSuffix(strings.Join(got[0], " "), "--state Waiting for a subagent…") || !strings.HasSuffix(strings.Join(got[1], " "), "--state Writing the reply…") {
		t.Fatalf("pickups = %q, want one per new state", got)
	}
	if err := Answered(h.dir, "1.0"); err != nil {
		t.Fatal(err)
	}
	if err := working(context.Background(), fake, h.dir, "Reading a record…"); err != nil {
		t.Fatal(err)
	}
	if again := verbs(fake, "pickup"); len(again) != 2 {
		t.Fatalf("pickups = %q, want none after his thread was answered", again)
	}
}
