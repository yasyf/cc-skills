package watch

import (
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
)

func TestASilentRootWithAnOpenAskOrBlockerIsWokenThroughOrcaDeskNamingTheDueClocks(t *testing.T) {
	quiet := at(t, "2026-11-08T16:47:00Z")
	records := []client.Record{{Seq: 612503, Lane: "root", Kind: "state", At: quiet, Text: "parked"}}
	open := []client.Record{
		{Seq: 613887, Lane: "owner-relay", Kind: "blocker", To: []string{"root", "finder-desk"}, At: quiet.Add(43 * time.Minute), Text: "9:30 clock passed: neither root nor finder-desk answered 613467"},
		{Seq: 613637, Lane: "owner-relay", Kind: "ask", To: []string{"root"}, At: quiet.Add(30 * time.Minute), Text: "#51261 body owners, clock 10:05"},
		{Seq: 613700, Lane: "lane-a", Kind: "ask", To: []string{"finder-desk"}, At: quiet.Add(30 * time.Minute), Text: "not for root"},
	}

	if got := WakeRoot(records, open, quiet.Add(wakeQuiet-time.Minute)); got != nil {
		t.Fatalf("root four minutes quiet was woken: %+v", got)
	}
	got := WakeRoot(records, open, quiet.Add(53*time.Minute))
	if got == nil {
		t.Fatal("root 53 minutes quiet with an open blocker was not woken")
	}
	for _, words := range []string{"orca-desk: relay to root: WAKE", "no root record since #612503 at 8:47 AM", "2 records to you are open", "blocker #613887 from owner-relay (clock 9:30 AM due)", "ask #613637 from owner-relay: #51261"} {
		if !strings.Contains(got.Line, words) {
			t.Fatalf("wake %q lacks %q", got.Line, words)
		}
	}
	if strings.Contains(got.Line, "613700") || strings.Contains(got.Line, "10:05 AM due") {
		t.Fatalf("wake %q names a record not to root or a clock not yet due", got.Line)
	}
	if again := WakeRoot(records, open, quiet.Add(53*time.Minute+wakeAgain)); again.Subject == got.Subject {
		t.Fatalf("a root still quiet %s later is not woken again: both are %s", wakeAgain, got.Subject)
	}

	answered := []client.Record{records[0], {Seq: 613990, Lane: "root", Kind: "answer", At: quiet.Add(10 * time.Minute), Re: 613637}}
	if got := WakeRoot(answered, open[1:2], quiet.Add(53*time.Minute)); got != nil {
		t.Fatalf("an answered ask woke root: %+v", got)
	}
}
