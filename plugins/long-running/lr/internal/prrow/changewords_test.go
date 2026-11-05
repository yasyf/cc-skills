package prrow

import (
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func TestChangeWordsShowOnlyAUserSentence(t *testing.T) {
	owed := "Not written yet: watch-lane, the lane that made this PR, has been asked to say in its description what a user will see change."
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
		"carriage returns":     {"Plan: step 2.\r\n\r\nChats load.\r\n", "Chats load.", ""},
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
