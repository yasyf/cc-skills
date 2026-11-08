package prrow

import (
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func TestChangeWordsShowOnlyAUserSentence(t *testing.T) {
	owed := "Not written yet: the lane that made this PR has been asked to say in its description what a user will see change."
	for name, test := range map[string]struct{ summary, want, flaw string }{
		"a plain sentence": {"Smoke jobs behind a failed step show as not reached.", "Smoke jobs behind a failed step show as not reached.", ""},
		"plan lines left to the plan": {
			"Plan: the class fix for the smoke order.\n\nSmoke jobs behind a failed step show as not reached.\n\nPlan row: owner:m835",
			"Smoke jobs behind a failed step show as not reached.", "",
		},
		"only plan text":       {"Plan: the class fix for the smoke order.\n\nPlan row: owner:m835", owed, "only plan text"},
		"no Summary":           {"", owed, "no Summary"},
		"a repeated paragraph": {"Smoke goes quiet.\n\nSmoke goes quiet.", owed, "twice"},
		"a hash":               {"It closes as cc-notes `cde3b364fdd72f358f069a0b7cf3ba4debcce510` requires.", owed, "cde3b364fdd72f358f069a0b7cf3ba4debcce510"},
		"an exit code":         {"A Watch now exits 79 when its deploy never ran.", owed, "exits 79"},
		"a brief name":         {"The watch-unreached-1 brief orders it.", owed, "watch-unreached-1 brief"},
		"hex-looking words":    {"The defaced cache is gone.", "The defaced cache is gone.", ""},
		"a symbol name": {
			"`Replication::release` now ends a closed database's lane itself, in a tracked task that holds the record's owner lock. A wake can no longer bring an ended lane back.",
			owed, "`Replication::release`",
		},
		"a bare symbol": {"Replication::release now ends a closed database's lane.", owed, "Replication::release"},
		"a route":       {"The server sends /wake only under that lock and only while the database is open.", owed, "/wake"},
		"a path":        {"Search reads go/sand/next/internal/memory first.", owed, "go/sand/next/internal/memory"},
		"a source file": {"It moves the check into memory/search.go.", owed, "memory/search.go"},
		"a column name": {"The row_count column fills on create.", owed, "row_count"},
		"a call":        {"It calls settle() once per chat.", owed, "settle()"},
		"plain words a user reads": {
			"Chats on iOS and macOS open in one second, read/write locks are gone, and [the chat](https://app.withforge.com/chats/1) loads on 11/06/2026.",
			"Chats on iOS and macOS open in one second, read/write locks are gone, and [the chat](https://app.withforge.com/chats/1) loads on 11/06/2026.", "",
		},
		"carriage returns": {"Plan: step 2.\r\n\r\nChats load.\r\n", "Chats load.", ""},
	} {
		t.Run(name, func(t *testing.T) {
			row := store.ReviewRow{Lane: "watch-lane", ForUsers: test.summary}
			if got := ChangeWords(row); got != test.want {
				t.Fatalf("ChangeWords = %q, want %q", got, test.want)
			}
			flaw := ChangeFlaw(test.summary)
			if (test.flaw == "") != (flaw == "") || !strings.Contains(flaw, test.flaw) {
				t.Fatalf("ChangeFlaw = %q, want one naming %q", flaw, test.flaw)
			}
		})
	}
}

func TestRiskWordsAreTheWholeProfileOnceItsAvailabilityLineIsPlain(t *testing.T) {
	profile := "- **Threat model:** no new surface.\n- **Availability:** a wrong answer here is a stale or failed read of a forked chat. A parked base read by file has the guards #51308's dormant read has.\n- **Data:** reads only."
	if got := RiskWords(store.ReviewRow{Risk: "\n" + profile + "\n"}); got != profile {
		t.Fatalf("risk = %q, want the whole profile", got)
	}
	for name, risk := range map[string]string{
		"no availability line": "Low: one retry loop.",
		"a code term":          "- **Availability:** `read_attached` may fail.",
		"a hash":               "- **Availability:** reads at b51dd19 may be stale.",
	} {
		if got := RiskWords(store.ReviewRow{Risk: risk}); got != riskOwed {
			t.Errorf("%s: risk = %q, want it owed", name, got)
		}
	}
	if got := RiskWords(store.ReviewRow{}); got != "" {
		t.Fatalf("no risk profile drew %q, want nothing", got)
	}
}
