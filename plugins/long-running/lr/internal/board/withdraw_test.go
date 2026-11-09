package board

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	why          = "being redone after review"
	ownerSlackID = "UOWNER"
	cardPost     = "https://acme.slack.com/archives/D0B6Z/p1793600000000100"
	keptPost     = "https://acme.slack.com/archives/D0B6Z/p1793599700000100"
)

func slackTS(ago time.Duration) string {
	return fmt.Sprintf("%d.000200", cardsNow.Add(-ago).Unix())
}

func cardThread(t *testing.T, messages ...map[string]any) string {
	t.Helper()
	root := map[string]any{"ts": slackTS(2 * time.Minute), "from_bot": true, "raw": map[string]any{}}
	encoded, err := json.Marshal(map[string]any{"channel_id": "D0B6Z", "thread_ts": root["ts"], "messages": append([]map[string]any{root}, messages...)})
	if err != nil {
		t.Fatal(err)
	}
	return string(encoded)
}

func TestALanesWithdrawWaitsWhileHeWritesInTheCardsSlackThread(t *testing.T) {
	reacted := func(ago time.Duration, users ...string) map[string]any {
		return map[string]any{"ts": slackTS(ago), "from_bot": true, "raw": map[string]any{"reactions": []any{map[string]any{"name": "eyes", "users": users}}}}
	}
	for name, c := range map[string]struct {
		thread  []map[string]any
		refused string
		whoami  int
	}{
		"his reply a minute ago":              {thread: []map[string]any{{"ts": slackTS(time.Minute), "from_user": true}}, refused: "replied in"},
		"his reaction on a bot reply":         {thread: []map[string]any{reacted(time.Minute, ownerSlackID)}, refused: "reacted to a message in", whoami: 1},
		"his reply eleven minutes ago":        {thread: []map[string]any{{"ts": slackTS(11 * time.Minute), "from_user": true}}},
		"the bot's own reaction":              {thread: []map[string]any{reacted(time.Minute, "UBOT")}, whoami: 1},
		"his reaction on an hour-old message": {thread: []map[string]any{reacted(time.Hour, ownerSlackID)}},
		"no word from him":                    {},
	} {
		on := &cardShell{cards: []string{"card-pick-1"}, posts: map[string]string{"card-pick-1": cardPost}, threads: map[string]string{cardPost: cardThread(t, c.thread...)}}
		f := newCards(t, on, nil)
		err := f.LanePull(context.Background(), "owner-relay", "card-pick-1", "", why, &bytes.Buffer{})
		if c.refused == "" {
			if err != nil || !reflect.DeepEqual(on.removed, []string{"card-pick-1"}) {
				t.Errorf("%s: err %v, removed %v, want it withdrawn", name, err, on.removed)
			}
		} else {
			hisMessage := "https://acme.slack.com/archives/D0B6Z/p" + strings.ReplaceAll(slackTS(time.Minute), ".", "") + "?thread_ts=" + slackTS(2*time.Minute) + "&cid=D0B6Z"
			if err == nil || !strings.Contains(err.Error(), c.refused) || !strings.Contains(err.Error(), hisMessage) || len(on.removed) != 0 {
				t.Errorf("%s: err %v, removed %v, want it refused naming %s", name, err, on.removed, hisMessage)
			}
		}
		if on.whoami != c.whoami {
			t.Errorf("%s: read his Slack id %d times, want %d", name, on.whoami, c.whoami)
		}
	}
}

func TestAPRTapWithdrawWaitsWhileHeWritesInItsTapCardsThread(t *testing.T) {
	for name, c := range map[string]struct {
		thread  []map[string]any
		refused bool
	}{
		"his reply a minute ago":       {thread: []map[string]any{{"ts": slackTS(time.Minute), "from_user": true}}, refused: true},
		"his reply eleven minutes ago": {thread: []map[string]any{{"ts": slackTS(11 * time.Minute), "from_user": true}}},
		"no word from him":             {},
	} {
		f, on, s := tapCards(t)
		card := tapCardID(readyRow(nil))
		on.posts = map[string]string{card: cardPost}
		on.threads = map[string]string{cardPost: cardThread(t, c.thread...)}
		err := f.LanePull(context.Background(), "owner-relay", "#45583", "", why, &bytes.Buffer{})
		waiting, readErr := waitingRows(s, repo, []int{number})
		if readErr != nil {
			t.Fatal(readErr)
		}
		if c.refused {
			if err == nil || !strings.Contains(err.Error(), "replied in "+card+"'s Slack thread") || len(waiting) != 1 || len(on.removed) != 0 {
				t.Errorf("%s: err %v, waiting %v, removed %v, want it refused with the tap still on his list", name, err, waiting, on.removed)
			}
			continue
		}
		if err != nil || len(waiting) != 0 {
			t.Errorf("%s: err %v, waiting %v, want the tap withdrawn", name, err, waiting)
		}
	}
}

func TestAWithdrawnDuplicateLinksTheCardThatStays(t *testing.T) {
	on := &cardShell{cards: []string{"card-dup", "card-kept"}, posts: map[string]string{"card-kept": keptPost}}
	f := newCards(t, on, nil)
	if err := f.LanePull(context.Background(), "owner-relay", "card-dup", "card-kept", "a second card for the same pick", &bytes.Buffer{}); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(on.removed, []string{"card-dup"}) || !reflect.DeepEqual(on.replaced, []string{keptPost}) {
		t.Fatalf("removed %v replaced by %v, want card-dup linking %s", on.removed, on.replaced, keptPost)
	}

	unposted := &cardShell{cards: []string{"card-dup", "card-kept"}}
	if err := newCards(t, unposted, nil).LanePull(context.Background(), "owner-relay", "card-dup", "card-kept", "a second card for the same pick", &bytes.Buffer{}); err == nil || len(unposted.removed) != 0 {
		t.Fatalf("err %v, removed %v, want a refusal when the kept card has no Slack post to link", err, unposted.removed)
	}
}

func tapCards(t *testing.T) (*cardsFixture, *cardShell, *store.Store) {
	t.Helper()
	s := withLedger(t, readyRow(nil))
	on := &cardShell{cards: []string{MergeWaits(number, head)}}
	f := newCards(t, on, nil)
	f.Store = s
	return f, on, s
}

func TestWithdrawingATapTakesItOffTheMergeListTheBoardAndSlackAndARerunChangesNothing(t *testing.T) {
	f, on, s := tapCards(t)
	ctx := context.Background()
	var out bytes.Buffer
	pulled, err := f.Pull(ctx, "meta-desk", "#45583", why, &out)
	if err != nil || !pulled {
		t.Fatalf("pulled = %v, %v", pulled, err)
	}
	if found, err := waitingRows(s, repo, []int{number}); err != nil || len(found) != 0 {
		t.Fatalf("the ledger still has it waiting on him: %+v, %v", found, err)
	}
	if want := []string{MergeWaits(number, head)}; !reflect.DeepEqual(on.removed, want) || !reflect.DeepEqual(on.reasons, []string{why}) {
		t.Fatalf("removed = %v with %q", on.removed, on.reasons)
	}
	on.cards = nil
	out.Reset()
	pulled, err = f.Pull(ctx, "meta-desk", "#45583", why, &out)
	if err != nil || pulled || len(on.removed) != 1 || len(f.posted) != 2 {
		t.Fatalf("a rerun pulled %v, removed %v, posted %q, err %v", pulled, on.removed, f.posted, err)
	}
	if !strings.Contains(out.String(), "UNCHANGED") {
		t.Fatalf("out = %s", out.String())
	}
}

func TestWithdrawingAPRRedrawsItsSlackPostAtOnceAndThePostReadsWithdrawn(t *testing.T) {
	f, on, s := tapCards(t)
	if _, err := s.PutFact(repo, number, head, FactCardDrawn, drawnSignature(Card(repo, readyRow(nil))), cardsNow); err != nil {
		t.Fatal(err)
	}
	var out bytes.Buffer
	if _, err := f.Pull(context.Background(), "meta-desk", "#45583", why, &out); err != nil {
		t.Fatal(err)
	}
	if want := []string{fmt.Sprintf("https://github.com/%s/pull/%d", repo, number)}; !reflect.DeepEqual(on.redrawn, want) || !strings.Contains(out.String(), "REDRAWN") {
		t.Fatalf("redrawn = %v, out = %s, want the post redrawn now", on.redrawn, out.String())
	}
	rows, err := s.ReviewRows(repo)
	if err != nil || len(rows) != 1 {
		t.Fatalf("rows = %+v, %v", rows, err)
	}
	card := Card(repo, rows[0])
	if card.Title != "Withdrawn: #45583, faster chats" || card.Waits != "" || card.Options != nil || card.PRs[0].Blocks != "Withdrawn: "+why+"." {
		t.Fatalf("card = %+v, want it read withdrawn with the reason and no buttons", card)
	}
	if pulled, err := f.Pull(context.Background(), "meta-desk", "#45583", why, &bytes.Buffer{}); err != nil || pulled || len(on.redrawn) != 2 {
		t.Fatalf("a rerun pulled %v and redrew %v, %v; want nothing pulled and the post redrawn again", pulled, on.redrawn, err)
	}
}

func TestWithdrawingAPRWithNoSlackPostRedrawsNothing(t *testing.T) {
	f, on, _ := tapCards(t)
	if _, err := f.Pull(context.Background(), "meta-desk", "#45583", why, &bytes.Buffer{}); err != nil {
		t.Fatal(err)
	}
	if len(on.redrawn) != 0 {
		t.Fatalf("redrawn = %v, want none", on.redrawn)
	}
}

func TestWithdrawingAListedPRShrinksItsListAtOnce(t *testing.T) {
	liveDrive(t)
	f := newLink(t)
	rows := burst(t, f, 3)
	readyPass(t, f)
	cards := f.Cards
	cards.Session = "board-1"
	if _, err := cards.Pull(context.Background(), "meta-desk", fmt.Sprintf("#%d", rows[0].Number), why, &bytes.Buffer{}); err != nil {
		t.Fatal(err)
	}
	posts := listPosts(t, f)
	if len(posts) != 2 || posts[1].Group != posts[0].Group || len(posts[1].Rows) != len(rows)-1 {
		t.Fatalf("posts = %+v, want the same list edited down to %d", posts, len(rows)-1)
	}
}

func TestWithdrawingAnAskCardRemovesOnlyThatCard(t *testing.T) {
	on := &cardShell{cards: []string{"card-pick-1", "card-pick-2"}}
	f := newCards(t, on, nil)
	pulled, err := f.Pull(context.Background(), "meta-desk", "card-pick-1", why, &bytes.Buffer{})
	if err != nil || pulled {
		t.Fatalf("pulled = %v, %v", pulled, err)
	}
	if !reflect.DeepEqual(on.removed, []string{"card-pick-1"}) {
		t.Fatalf("removed = %v", on.removed)
	}
}

func TestAWhyNamingAnIDIsRefused(t *testing.T) {
	on := &cardShell{cards: []string{"card-pick-1"}}
	f := newCards(t, on, nil)
	for _, said := range []string{"", "superseded by #49700", "see card-pick-2", "moved to merge:49622:abc1234", "its words go to root first (617717)"} {
		if _, err := f.Pull(context.Background(), "meta-desk", "card-pick-1", said, &bytes.Buffer{}); err == nil {
			t.Fatalf("--why %q went to the owner", said)
		}
	}
	if len(on.removed) != 0 {
		t.Fatalf("removed = %v", on.removed)
	}
}

func TestWithdrawingAPRAlsoTakesDownItsTapCardAndNothingRedrawsIt(t *testing.T) {
	f, on, s := tapCards(t)
	card := tapCardID(readyRow(nil))
	on.cards = append(on.cards, card)
	if _, err := s.PutFact(repo, number, head, FactTapCard, tapCardDrawn, cardsNow); err != nil {
		t.Fatal(err)
	}
	if _, err := f.Pull(context.Background(), "meta-desk", "#45583", why, &bytes.Buffer{}); err != nil {
		t.Fatal(err)
	}
	if want := []string{MergeWaits(number, head), card}; !reflect.DeepEqual(on.removed, want) {
		t.Fatalf("removed = %v, want %v", on.removed, want)
	}
	if stored, err := s.Facts(repo, number, head); err != nil || stored[FactTapCard] != tapCardDrawn {
		t.Fatalf("tap card fact = %q, %v, want it kept so no card comes back", stored[FactTapCard], err)
	}
}

func TestWithdrawingATapCardByIDFreesItsRowOnlyWhenNoSlackCardStands(t *testing.T) {
	for how, wantKept := range map[string]bool{tapCardDrawn: false, tapCardOnBoard: false, tapCardInSlack: true} {
		f, on, s := tapCards(t)
		card := tapCardID(readyRow(nil))
		on.cards = []string{card}
		if _, err := s.PutFact(repo, number, head, FactTapCard, how, cardsNow); err != nil {
			t.Fatal(err)
		}
		if _, err := f.Pull(context.Background(), "meta-desk", card, why, &bytes.Buffer{}); err != nil {
			t.Fatal(err)
		}
		stored, err := s.Facts(repo, number, head)
		if err != nil {
			t.Fatal(err)
		}
		if kept := stored[FactTapCard] != ""; kept != wantKept {
			t.Errorf("fact %q: kept = %v, want %v", how, kept, wantKept)
		}
	}
}
