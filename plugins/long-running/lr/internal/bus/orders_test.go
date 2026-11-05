package bus_test

import (
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
)

func TestOnlyAnOwnerRecordCarriesAnOrder(t *testing.T) {
	said := map[string]string{"event": "choice.selected"}
	if _, ordered := bus.OrderOf(legacyRecord(t, "report", "board-watch", "Owner on board: Land #7? (c): picked “Land #7 now”", nil, said), repo); ordered {
		t.Fatal("a lane's report carried a land order")
	}
}

func TestAPickReadsItsOwnPRAndItsOwnHeadsRepository(t *testing.T) {
	for _, tt := range []struct {
		name, text, heads string
		pr                int
		head              string
		ordered           bool
	}{
		{"a bare number", "Merge #7? (c): picked “Merge now”", "", 7, "", true},
		{"this repository's name", repo + "#7 (c): picked “Land it”", "", 7, "", true},
		{"another repository's PR", "other/repo#7 (c): picked “Land it”", "", 0, "", false},
		{"a yes that lands", "Land #7? (c): picked “Yes, land it”", "", 7, "", true},
		{"a label that only mentions landing", "Land #7? (c): picked “Wait, do not land”", "", 0, "", false},
		{"the head its card asked about", "Land #7? (c): picked “Land it” at " + repo + "#7 ABC1234", repo + "#7=ABC1234", 7, "abc1234", true},
		{"a head on another repository", "Land #7? (c): picked “Land it”", "other/repo#7=abc1234", 0, "", false},
		{"two PRs its card asked about", "Land #7? (c): picked “Land it”", repo + "#7=abc1234," + repo + "#8=def5678", 0, "", false},
	} {
		t.Run(tt.name, func(t *testing.T) {
			attrs := map[string]string{"event": "choice.selected", "heads": tt.heads}
			got, ordered := bus.OrderOf(legacyRecord(t, "owner", "board-watch", tt.text, nil, attrs), repo)
			if ordered != tt.ordered || ordered && (got.PR != tt.pr || got.Head != tt.head) {
				t.Fatalf("OrderOf = %+v, %v", got, ordered)
			}
		})
	}
}

func TestALabelLessPickEscalatesUnlessItIsASlackPermissionAnswer(t *testing.T) {
	for block, want := range map[string]bool{
		"merge:50129:655dbb7-answer":      true,
		"card-tap-50129-655dbb7-choice":   true,
		"slack-80ad0fa34a9b-answer":       false,
		"slack:C0C5SEUJK5L:1.2:ab-answer": false,
	} {
		pick := legacyRecord(t, "owner", "board-watch", "Backfilled from board 59e75579: the owner answered this choice", nil, map[string]string{"event": "choice.selected", "block": block, "picked": "yes"})
		if got := bus.UnreadPick(pick); got != want {
			t.Errorf("UnreadPick(%s) = %v, want %v", block, got, want)
		}
	}
}
