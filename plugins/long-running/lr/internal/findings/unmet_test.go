package findings

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
)

const tapText = "Owner on board d1, Send the DM? (card-x-pick): picked “Send it”"

func ago(w *world, age time.Duration) func(*client.Record) {
	return func(r *client.Record) { r.At = w.book.Now().Add(-age) }
}

func stillUnmet(seq string) func(*client.Record) {
	return func(r *client.Record) { r.Fields.Attrs = map[string]string{StillUnmetAttr: seq} }
}

func TestAnActUnderHisWordRunsInItsPassAndAfterItOnlyOnAFreshStillUnmetRecordFromItsOwner(t *testing.T) {
	cases := []struct {
		name    string
		age     time.Duration
		by      string
		said    time.Duration
		refused bool
	}{
		{"in the pass that read it", 5 * time.Minute, "", 0, false},
		{"past its pass with no record", 2 * time.Hour, "", 0, true},
		{"past its pass, its owner says it is still unmet", 2 * time.Hour, "lane-a-2", 5 * time.Minute, false},
		{"past its pass, root says it is still unmet", 2 * time.Hour, "root", 5 * time.Minute, false},
		{"past its pass, another lane says it is still unmet", 2 * time.Hour, "lane-z", 5 * time.Minute, true},
		{"past its pass, its owner said so in an earlier pass", 2 * time.Hour, "lane-a", time.Hour, true},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			w := newWorld(t, 15)
			w.owned("pick/10", "10", "lane-a", stateRouted, "12")
			w.bus.add(10, "owner", "board-watch", tapText, ago(w, c.age))
			if c.by != "" {
				w.bus.add(20, "answer", c.by, "the DM is still unsent", ago(w, c.said), stillUnmet("10"))
			}

			refused, err := w.book.Unmet(context.Background(), 10)
			if err != nil {
				t.Fatal(err)
			}

			if (refused != "") != c.refused {
				t.Fatalf("refused = %q, want refused %v", refused, c.refused)
			}
			for _, words := range []string{"REFUSED #10", UnmetPost("lane-a", "10"), "lr findings close --why <reason> 10"} {
				if c.refused && !strings.Contains(refused, words) {
					t.Fatalf("refusal %q lacks %q", refused, words)
				}
			}
		})
	}
}

func TestARefusalNamesALedgerRowAlreadyCarriedOutAndAWordWithNoRowItsRoutedLane(t *testing.T) {
	w := newWorld(t, 15)
	w.owned("pick/10", "10", "lane-a", stateFixed, "30")
	w.bus.add(10, "owner", "board-watch", tapText, ago(w, 2*time.Hour))
	w.bus.add(11, "owner", "owner-relay", "Owner: restart the obs desk now", ago(w, 2*time.Hour), func(r *client.Record) { r.To = []string{"install-desk", "root"} })

	carried, err := w.book.Unmet(context.Background(), 10)
	if err != nil {
		t.Fatal(err)
	}
	unrowed, err := w.book.Unmet(context.Background(), 11)
	if err != nil {
		t.Fatal(err)
	}

	if !strings.Contains(carried, "The findings ledger has it fixed by 30.") {
		t.Fatalf("carried = %q", carried)
	}
	if !strings.Contains(unrowed, UnmetPost("install-desk", "11")) || strings.Contains(unrowed, "lr findings close") {
		t.Fatalf("unrowed = %q", unrowed)
	}
}

func TestOnlyAnOwnerRecordIsAnActsAuthority(t *testing.T) {
	w := newWorld(t, 15)
	w.bus.add(10, "ask", "root", "send the DM", ago(w, 2*time.Hour))

	if _, err := w.book.Unmet(context.Background(), 10); err == nil || !strings.Contains(err.Error(), "not an owner record") {
		t.Fatalf("err = %v", err)
	}
}

func TestASendIsCheckedAgainstTheNewestApprovedMessageToItsRecipient(t *testing.T) {
	w := newWorld(t, 15)
	w.owned("pick/10", "10", "lane-a", stateRouted, "12")
	w.bus.add(10, "owner", "board-watch", tapText, ago(w, 2*time.Hour))
	w.bus.add(11, "ask", "board-watch", "SEND card-x: the owner picked “Send it”", ago(w, 2*time.Hour), func(r *client.Record) {
		r.Re, r.Topic = 10, sendTopic+"card-x"
		r.Fields.Attrs = map[string]string{sendToAttr: "U0TEAMMATE1"}
	})

	stale, err := w.book.SendUnmet(context.Background(), "U0TEAMMATE1")
	if err != nil {
		t.Fatal(err)
	}
	other, err := w.book.SendUnmet(context.Background(), "U0SOMEONE22")
	if err != nil {
		t.Fatal(err)
	}

	if !strings.HasPrefix(stale, "REFUSED #10") {
		t.Fatalf("stale = %q", stale)
	}
	if other != "" {
		t.Fatalf("other = %q", other)
	}
}
