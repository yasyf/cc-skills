package reply

import (
	"context"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

func TestAMessageCardDrawsOnlyADraftCheckPostPassesAndShowsItAsItGoesOut(t *testing.T) {
	ctx := context.Background()
	text := "hey <@U0ANUBHAV1>, could you make the `FORGE_REBASE_TOKEN` secret on the fork?"
	r := Reply{
		Title: "Send this message to Anubhav?", MD: "The fork's nightly sync needs one token.", Picks: []string{"Send it", "Do not send"}, Recommended: "Send it",
		Message: &Message{To: "U0ANUBHAV1", Text: "Hi Anubhav, could you make a token.", Pick: "Send it :: sends it to him once"},
	}

	failing := &shelltest.Fake{}
	failing.FailOut(1, "1:1 plain-name 'Hi Anubhav': address the person as a <@U…> mention\n", "check-post")
	if _, err := fanout(t, failing).checkedMessage(ctx, r); err == nil || !strings.Contains(err.Error(), "DRAFT FAILS CHECK-POST") || !strings.Contains(err.Error(), "plain-name 'Hi Anubhav'") {
		t.Fatalf("a draft check-post flags drew: %v", err)
	}
	if _, err := fanout(t, &shelltest.Fake{}).checkedMessage(ctx, r); err == nil || !strings.Contains(err.Error(), "DRAFT UNCHECKED") {
		t.Fatalf("a draft check-post never read drew: %v", err)
	}
	unpicked := r
	unpicked.Message = &Message{To: "U0ANUBHAV1", Text: text, Pick: "Ship it"}
	if _, err := fanout(t, &shelltest.Fake{}).checkedMessage(ctx, unpicked); err == nil || !strings.Contains(err.Error(), "none of this card's picks") {
		t.Fatalf("a message no pick sends drew: %v", err)
	}
	unnamed := r
	unnamed.Message = &Message{To: "Anubhav", Text: text, Pick: "Send it"}
	if _, err := fanout(t, &shelltest.Fake{}).checkedMessage(ctx, unnamed); err == nil || !strings.Contains(err.Error(), "not a Slack user or channel id") {
		t.Fatalf("a message to a plain name drew: %v", err)
	}

	passing := &shelltest.Fake{}
	passing.Reply("", "check-post")
	good := r
	good.Message = &Message{To: "U0ANUBHAV1", Text: text, Pick: "Send it :: sends it to him once"}
	checked, err := fanout(t, passing).checkedMessage(ctx, good)
	if err != nil {
		t.Fatal(err)
	}
	if calls := passing.Calls(); len(calls) != 1 || calls[0].Stdin != text {
		t.Fatalf("check-post read %+v, want the message text", calls)
	}
	if checked.Message.Pick != "Send it" || checked.MD != "The fork's nightly sync needs one token.\n\n```\n"+text+"\n```" {
		t.Fatalf("checked = %+v %q", checked.Message, checked.MD)
	}
}
