package cardtext

import "testing"

func TestChangeKindReadsTheTitlesGitmoji(t *testing.T) {
	for title, want := range map[string]string{
		"ops: 🐛 correct LTX retirement failure guidance":      "Fix",
		"sand-next: :bug: the usage-meter flusher count":      "Fix",
		"memory: :fire: each dreamed change faces one judge":  "Cleanup",
		"iris: ♻️ a subagent's answer is an ordinary message": "Cleanup",
		"release: ✨ the release card shows what ships":        "New",
		"sand: faster chats": "New",
		"long-running: 🚑 a lane silent 30 minutes is relaunched": "Fix",
	} {
		if got := ChangeKind(title); got != want {
			t.Errorf("ChangeKind(%q) = %q, want %q", title, got, want)
		}
	}
}
