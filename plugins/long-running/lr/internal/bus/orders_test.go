package bus_test

import (
	"fmt"
	"testing"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
)

func TestOnlyAnOwnerRecordCarriesAnOrder(t *testing.T) {
	said := map[string]string{"event": "choice.selected"}
	if taps := bus.OrdersOf(legacyRecord(t, "report", "board-watch", "Owner on board: Land #7? (c): picked “Land #7 now”", nil, said), repo); len(taps) > 0 {
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
		{"a port card's source in its title", "port of #50061: Viewer fit? (c): picked “Land it”", "", 0, "", false},
		{"a port card naming its port", "port of #50061: Land #50460? (c): picked “Land it”", "", 50460, "", true},
		{"a port card opening with its dev source", "Port, old tree to new: dev #50061: Approve #50460 at abc1234? (c): picked “Approve”", "", 50460, "", true},
		{"a port card linking its dev source", "Port, old tree to new: dev [#50061](https://github.com/" + repo + "/pull/50061): Land #50460? (c): picked “Land it”", "", 50460, "", true},
	} {
		t.Run(tt.name, func(t *testing.T) {
			attrs := map[string]string{"event": "choice.selected", "heads": tt.heads}
			got := bus.OrdersOf(legacyRecord(t, "owner", "board-watch", tt.text, nil, attrs), repo)
			if len(got) > 0 != tt.ordered || tt.ordered && (len(got) != 1 || got[0].PR != tt.pr || got[0].Head != tt.head) {
				t.Fatalf("OrdersOf = %+v", got)
			}
		})
	}
}

func TestAnApproveOnACardShowingTwoPRsOrdersEachAtTheHeadItShowed(t *testing.T) {
	attrs := map[string]string{"event": "choice.selected", "heads": repo + "#7=ABC1234," + repo + "#8=def5678"}
	got := bus.OrdersOf(legacyRecord(t, "owner", "board-watch", "Land both? (c): picked “Approve both”", nil, attrs), repo)
	if len(got) != 2 || got[0].PR != 7 || got[0].Head != "abc1234" || got[1].PR != 8 || got[1].Head != "def5678" || got[0].Value != "tapped" || got[1].Value != "tapped" {
		t.Fatalf("OrdersOf = %+v, want #7 at abc1234 and #8 at def5678, both tapped", got)
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

func TestHisTypedAnswerToACardOrdersOnlyWhenItIsALandWordNamingOnePR(t *testing.T) {
	for _, tt := range []struct {
		name, text string
		pr         int
		ordered    bool
	}{
		{"the option's own label", `Owner: "Land this now" #51241`, 51241, true},
		{"a label that is not a land word", `Owner: "One call per 8 segments" #50807`, 0, false},
		{"a label that only mentions landing", `Owner: "Wait, do not land" #51241`, 0, false},
		{"a land word naming no PR", `Owner: "Land this now"`, 0, false},
		{"a land word naming two PRs", `Owner: "Land this now" #51241 #51242`, 0, false},
		{"a land word on another repository's PR", `Owner: "Land this now" other/repo#51241`, 0, false},
		{"words with no quoted label", "Owner in thread: merge #51241", 0, false},
	} {
		t.Run(tt.name, func(t *testing.T) {
			got := bus.OrdersOf(legacyRecord(t, "owner", "owner-relay", tt.text, nil, nil), repo)
			if len(got) > 0 != tt.ordered || tt.ordered && (len(got) != 1 || got[0].PR != tt.pr || got[0].Head != "" || got[0].Value != "tapped") {
				t.Fatalf("OrdersOf = %+v", got)
			}
		})
	}
}

func TestAHoldThatSaysItWaitsOnHisAnswerHoldsUntilHisTap(t *testing.T) {
	for _, tt := range []struct {
		name, text string
		attrs      map[string]string
		prs        []int
		want       bool
	}{
		{"the poster's attribute", "hold #7", map[string]string{"until": "tap"}, []int{7}, true},
		{"words for a hold already open", "#7 is product code: no landing until he answers its card.", nil, []int{7}, true},
		{"an ordinary hold", "hold #7 on the cutover", nil, []int{7}, false},
		{"words on a hold naming two PRs", "no landing until he answers", nil, []int{7, 8}, false},
	} {
		t.Run(tt.name, func(t *testing.T) {
			if got := bus.HoldsUntilTap(legacyRecord(t, "hold", "lane", tt.text, tt.prs, tt.attrs)); got != tt.want {
				t.Fatalf("HoldsUntilTap = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestAPickOnSeveralPRsIsCarriedOnlyWhenALaterRecordOrdersEachOfThem(t *testing.T) {
	pick := legacyRecord(t, "owner", "board-watch", "Four small changes are green and ready to land. Approve them? (c): picked “Approve all four”", nil, map[string]string{"event": "choice.selected", "heads": repo + "#7=abc1234," + repo + "#8=def5678"})
	carried := func(numbers ...int) []client.Record {
		records := make([]client.Record, 0, len(numbers))
		for _, number := range numbers {
			records = append(records, legacyRecord(t, "owner", "owner-relay", fmt.Sprintf(`Owner: "Approve all four" #%d`, number), nil, nil))
		}
		return records
	}
	if bus.UnnamedLand(pick, repo) {
		t.Fatal("a pick whose card showed both PRs at their heads read as naming none")
	}
	if bus.CarriedEach(pick, carried(7), repo) {
		t.Fatal("a pick carried for one of its two PRs read as carried")
	}
	if !bus.CarriedEach(pick, carried(7, 8), repo) {
		t.Fatal("a pick carried for each of its PRs read as not carried")
	}
}
