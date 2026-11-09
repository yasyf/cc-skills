package supervisor

import (
	"context"
	"slices"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

func TestHisMessageUnansweredTenMinutesIsRaisedToOwnerRelayAndRootOnce(t *testing.T) {
	now := time.Date(2026, 11, 9, 5, 30, 0, 0, time.UTC)
	stale := strconv.FormatInt(now.Add(-11*time.Minute).Unix(), 10) + ".000100"
	fresh := strconv.FormatInt(now.Add(-4*time.Minute).Unix(), 10) + ".000200"
	runner := &shelltest.Fake{}
	runner.Reply(`{"replies":[{"ts":"`+stale+`","text":"why is this card still up?","permalink":"https://slack.test/p1"},{"ts":"`+fresh+`","text":"and this one","permalink":"https://slack.test/p2"}]}`, "cc-slack", "dm-owed")
	type sent struct {
		to   []string
		body bus.Body
	}
	var posts []sent
	u := unanswered{runner: runner, store: memory(t), now: func() time.Time { return now }, post: func(to []string, body bus.Body) error {
		posts = append(posts, sent{to, body})
		return nil
	}}

	for range 2 {
		if err := u.raise(context.Background()); err != nil {
			t.Fatal(err)
		}
	}

	if len(posts) != 1 || !slices.Equal(posts[0].to, []string{lanes.OwnerRelay, lanes.Root}) {
		t.Fatalf("posts = %+v, want one raise to owner-relay and root", posts)
	}
	raised, ok := posts[0].body.(bus.Escalation)
	if !ok || raised.Subject != "unanswered:"+stale || !strings.Contains(raised.Cause, "https://slack.test/p1") {
		t.Fatalf("body = %+v, want the escalation for the 11-minute message", posts[0].body)
	}
}
