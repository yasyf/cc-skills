package reply

import (
	"context"
	"slices"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

const mergeCardBoard = `{"doc":{"blocks":[` +
	`{"id":"merge:2196:abc1234","type":"card","title":"Approve #2196, the relay fix?","slack":{"url":"https://brook.slack.com/archives/D0B6Z/p1794100000000020"},"children":[{"id":"merge-2196","type":"choice"}]}` +
	`]},"interactions":{"choices":{}}}`

func TestAPickCardNamingAPRWithALiveCardIsRefusedAsASecondPost(t *testing.T) {
	for name, r := range map[string]Reply{
		"named in prs":   {ID: "r1", Title: "Land the relay fix?", MD: "m", Picks: []string{"Land", "Hold"}, PRs: []string{landPR}},
		"named in title": {ID: "r1", Title: "Cleanup: close #2196?", MD: "m", Picks: []string{"Close it", "Keep it"}},
	} {
		t.Run(name, func(t *testing.T) {
			fake := &shelltest.Fake{}
			fake.Reply(mergeCardBoard, "cc-present", "outcomes")
			_, err := fanout(t, fake).onePostPerPR(context.Background(), r)
			if err == nil || !strings.Contains(err.Error(), "SECOND POST") || !strings.Contains(err.Error(), "p1794100000000020") {
				t.Fatalf("err = %v, want a refusal naming the live card", err)
			}
		})
	}
}

func TestAReplyNamingAPRWithALiveCardLinksItAndDrawsNoSecondCard(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply(mergeCardBoard, "cc-present", "outcomes")
	f := fanout(t, scriptedOver(fake))
	r, err := f.onePostPerPR(context.Background(), Reply{ID: "r1", Title: "t", MD: "The relay fix is green.", Status: info, PRs: []string{landPR}})
	if err != nil {
		t.Fatal(err)
	}
	f.Send(context.Background(), r)
	for _, call := range calls(fake, "cc-slack") {
		if slices.Contains(call.Argv, "--tap-pr") {
			t.Fatalf("dm-status asked for %s's card again: %q", landPR, call.Argv)
		}
	}
	if !strings.Contains(r.MD, "https://brook.slack.com/archives/D0B6Z/p1794100000000020") {
		t.Fatalf("md = %q, want a link to the PR's one card", r.MD)
	}
}

func TestAPRWithNoLiveCardStillDrawsItsCard(t *testing.T) {
	fake := scripted()
	f := fanout(t, fake)
	r, err := f.onePostPerPR(context.Background(), Reply{ID: "r1", Title: "t", MD: "m", Status: info, PRs: []string{landPR}})
	if err != nil {
		t.Fatal(err)
	}
	f.Send(context.Background(), r)
	var asked bool
	for _, call := range calls(fake, "cc-slack") {
		asked = asked || slices.Contains(call.Argv, "--tap-pr")
	}
	if !asked {
		t.Fatal("a PR with no card drew none")
	}
}

func TestPickRulesThatOnceWarnedNowRefuse(t *testing.T) {
	for name, tc := range map[string]struct {
		reply Reply
		says  string
	}{
		"one pick":          {Reply{Picks: []string{"Land it :: it lands"}, Recommended: "Land it"}, "ONE PICK"},
		"five picks":        {Reply{Picks: []string{"A :: a", "B :: b", "C :: c", "D :: d", "E :: e"}, Recommended: "A"}, "TOO MANY PICKS"},
		"a pick unsaid":     {Reply{Picks: []string{"Land :: it lands", "Hold"}, Recommended: "Land"}, "UNSAID PICK"},
		"none recommended":  {Reply{Picks: []string{"Land :: it lands", "Hold :: it waits"}}, "UNRECOMMENDED"},
		"a good set passes": {Reply{Picks: []string{"Land :: it lands", "Hold :: it waits"}, Recommended: "Land"}, ""},
	} {
		t.Run(name, func(t *testing.T) {
			err := tc.reply.unpickable()
			if tc.says == "" && err != nil || tc.says != "" && (err == nil || !strings.Contains(err.Error(), tc.says)) {
				t.Fatalf("err = %v, want %q", err, tc.says)
			}
		})
	}
}

func TestATapThatAPRLedgerRowRefusesNowStopsTheReply(t *testing.T) {
	f, fake := fanout(t, nil), scripted()
	ledgerRow(t, Fanout{Entry: f.Entry}, carded)
	text, failed := throughTheTool(t, f.Entry, fake, map[string]any{"title": "Cleanup: close #2196 or keep it?", "md": "#2196 is ready for you.", "picks": []string{"Close it :: it closes", "Keep it :: it stays"}, "recommended": "Close it", "unprompted": true})
	if !failed || !strings.Contains(text, "REFUSED") || len(calls(fake, "cc-slack")) != 0 {
		t.Fatalf("reply = %q, failed %v; want the missing Approve refused with nothing posted", text, failed)
	}
}

func TestATakeOverWritesTheCardAsTheCallersLane(t *testing.T) {
	t.Setenv(laneEnv, "owner-fixer")
	fake := scripted()
	fanout(t, fake).Send(context.Background(), Reply{ID: "r9", Title: "Land?", MD: "m", Picks: []string{"Land", "Hold"}, Recommended: "Land", TakeOver: true})
	var argv []string
	for _, call := range fake.Calls() {
		if call.Argv[0] == "cc-present" && call.Argv[1] == "update-block" && !onThread(call) {
			argv = call.Argv
		}
	}
	if !slices.Contains(argv, "--take-over") || flag(t, argv, "--lane") != "owner-fixer" {
		t.Fatalf("update-block = %q, want it taken over as owner-fixer", argv)
	}
}

func TestACardWhoseLaneIsGoneIsRefusedWithoutATakeOverWhenTheLaneIsNotRetired(t *testing.T) {
	t.Setenv(laneEnv, "owner-fixer")
	fake := &shelltest.Fake{}
	fake.Fail(1, `cc-present: refusing to write card "card-r9": lane board-refresher-20 owns it`, "cc-present", "update-block")
	sent := fanout(t, scriptedOver(fake)).Send(context.Background(), Reply{ID: "r9", Title: "Land?", MD: "m", Picks: []string{"Land", "Hold"}, Recommended: "Land"})
	if drawn := surface(sent, "board"); !strings.Contains(drawn.Error, "owns it") {
		t.Fatalf("board = %+v, want the refusal kept when the owning lane is not retired", drawn)
	}
}
