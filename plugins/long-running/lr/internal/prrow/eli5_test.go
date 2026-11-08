package prrow

import (
	"context"
	"encoding/json"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const companion = "<!-- pr-reviewer bot companion -->\n### ELI5\n\nChats now reconnect by themselves after the network drops.\n\n![diagram](https://example.com/d.png)\n\n### Files\n\n- relay.go\n\n<sub>bot</sub>"

func TestTheELI5IsTheReviewerBotsSectionAloneAndOnlyFromItsCompanion(t *testing.T) {
	if got := ELI5Of(companion); got != "Chats now reconnect by themselves after the network drops." {
		t.Fatalf("ELI5 = %q", got)
	}
	if got := ELI5Of("### ELI5\n\nA reviewer's own words."); got != "" {
		t.Fatalf("a comment without the companion marker gave %q", got)
	}
}

func TestTheELI5LineFallsBackToTheBodysContextUntilTheBotPosts(t *testing.T) {
	row := store.ReviewRow{Description: "## Context\n\nChats dropped on a bad network.\n\n## Summary\n\nChats reconnect."}
	if got := ELI5Words(row); got != "Chats dropped on a bad network." {
		t.Fatalf("fallback = %q", got)
	}
	row.ELI5 = "Chats now reconnect by themselves."
	if got := ELI5Words(row); got != row.ELI5 {
		t.Fatalf("bot's ELI5 lost to the body: %q", got)
	}
}

func TestEveryPRsELI5IsReadInOneGitHubCall(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply(`{"data":{"repository":{"pr7":{"comments":{"nodes":[{"body":"looks good"},{"body":`+quoted(companion)+`}]}},"pr8":{"comments":{"nodes":[{"body":"no bot yet"}]}}}}}`, "gh", "api", "graphql")
	got, err := ReadELI5(context.Background(), fake, "/repo", "org/repo", []int{7, 8})
	if err != nil {
		t.Fatal(err)
	}
	if len(fake.Calls()) != 1 || got[7] != "Chats now reconnect by themselves after the network drops." || got[8] != "" {
		t.Fatalf("calls %d, ELI5 %+v", len(fake.Calls()), got)
	}
}

func quoted(s string) string {
	raw, _ := json.Marshal(s)
	return string(raw)
}
