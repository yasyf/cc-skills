package prrow

import (
	"os"
	"path/filepath"
	"slices"
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

func TestTheArchExampleARefusalQuotesDrawsOnHisCard(t *testing.T) {
	row := store.ReviewRow{Description: "## Show me\n\n" + archExample + "\n"}
	if flaw := showMeFlaw(row); flaw != "" {
		t.Fatalf("the quoted example is refused: %s", flaw)
	}
	if ShowMeDiagram(row) == nil {
		t.Fatal("the quoted example draws no diagram")
	}
}

func TestRiskWordsIsTheRiskSectionsFirstPlainSentence(t *testing.T) {
	row := store.ReviewRow{Risk: "- **Threat model:** app cookies stop relying on SameSite. App actions already require a matching Origin.\n- **Data:** no new data."}
	if got := RiskWords(row); got != "app cookies stop relying on SameSite." {
		t.Fatalf("RiskWords = %q, want the first sentence without its bold label", got)
	}
	if got := RiskWords(store.ReviewRow{}); got != "" {
		t.Fatalf("RiskWords of no Risk section = %q, want none", got)
	}
}

func TestARenderedDiagramPostsAsItsPictureNeverAsSourceOrAPath(t *testing.T) {
	home, err := os.UserHomeDir()
	if err != nil {
		t.Fatal(err)
	}
	const shown = "## Show me\n\nPicture (rendered from the diagram below): `~/.vcache/scratch/bugs/s100.png`\n\n![An empty tag goes back to be redrafted](~/.vcache/scratch/bugs/s100.png)\n\nThe draft is checked before it shows.\n\n```dot\ndigraph G { a -> b }\n```\n"
	row := store.ReviewRow{Description: shown}

	if pictures := ShowMePictures(row); !slices.Equal(pictures, []string{filepath.Join(home, ".vcache/scratch/bugs/s100.png")}) {
		t.Fatalf("pictures = %q, want the one local image as an absolute path", pictures)
	}
	if words := ShowMeWords(row); words != "The draft is checked before it shows." {
		t.Fatalf("words = %q, want only the sentence, with no path, image link or dot source", words)
	}
	if flaw := showMeFlaw(row); flaw != "" {
		t.Fatalf("flaw = %q, want a rendered picture to stand as the show-me", flaw)
	}
	unrendered := store.ReviewRow{Description: "## Show me\n\n```mermaid\ngraph TD; a-->b\n```\n"}
	if flaw := showMeFlaw(unrendered); !strings.Contains(flaw, "prints as text") {
		t.Fatalf("flaw = %q, want diagram source with no picture refused", flaw)
	}
}
