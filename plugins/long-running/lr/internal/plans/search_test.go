package plans

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestSearchReturnsTheSectionAnsweringTheQuestionWithItsLine(t *testing.T) {
	plan := "# Durable agents\n\nIntro.\n\n## Inbox\n\nA chat has one batched inbox read, on the inbox signal or the 30 s tick.\n\n## Release\n\nCut at dev head.\n"
	ccn := func(_ context.Context, args ...string) (string, error) {
		if strings.Join(args, " ") == "plan list --label program:dar --json" {
			return `[{"id":"9fba4d8aaaa","title":"the plan"}]`, nil
		}
		return `{"id":"9fba4d8aaaa","body":` + quoted(plan) + `}`, nil
	}
	file := filepath.Join(t.TempDir(), "plan.md")
	if err := os.WriteFile(file, []byte("# Sprite copy\n\nNothing about it here.\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	hits, err := Search(context.Background(), ccn, "dar", "how is the inbox read batched?", 5, file)
	if err != nil {
		t.Fatal(err)
	}

	if len(hits) != 1 || hits[0].Source != "ccn plan 9fba4d8" || hits[0].Line != 7 || hits[0].Heading != "Inbox" || !strings.Contains(hits[0].Text, "one batched inbox read") {
		t.Fatalf("hits = %+v, want the Inbox section paragraph at line 7 alone", hits)
	}
}

func quoted(text string) string {
	return `"` + strings.NewReplacer(`\`, `\\`, `"`, `\"`, "\n", `\n`).Replace(text) + `"`
}
