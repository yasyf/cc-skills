package board

import (
	"context"
	"encoding/json"
	"fmt"
	"reflect"
	"strings"
	"testing"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ledger/ledgertest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

const versionCard = "How does a cloud agent's chat get a version number?"

type spokenAnswer struct {
	ID    string `json:"id"`
	Title string `json:"title"`
	Body  string `json:"body"`
	At    string `json:"created_at"`
}

func ruledCards(t *testing.T, answers ...spokenAnswer) Cards {
	t.Helper()
	t.Setenv("CCI_HOME", t.TempDir())
	listed, err := json.Marshal(answers)
	if err != nil {
		t.Fatal(err)
	}
	fake := &shelltest.Fake{}
	fake.Reply(string(listed), "ccn", "-R", "/co", "answer", "list")
	fake.Reply("[]", "ccn", "-R", "/co", "ledger", "list")
	return Cards{Runner: fake, Ledger: &ledgertest.Fake{}, Bus: "d", Checkout: "/co", Session: "s1", Program: "p"}
}

func rulingIDs(rulings []Ruling) []string {
	ids := make([]string, 0, len(rulings))
	for _, ruling := range rulings {
		ids = append(ids, ruling.ID)
	}
	return ids
}

func TestACardOnWhatHeOrderedDeletedNamesHisWordsNewestFirst(t *testing.T) {
	cards := ruledCards(t,
		spokenAnswer{"older", "Round 2: old cloud agents?", `Picked (a) and wrote in: just delete every old one.`, "2026-10-22T05:00:00Z"},
		spokenAnswer{"newer", "What happens to the old cloud-agent rows when durable agents unify?", "Jsut delete those old agent rows, nothing needs them.", "2026-11-02T22:19:00Z"},
		spokenAnswer{"pool", "Which pool should Sonnet bill to?", "Just delete the pool.", "2026-11-03T01:00:00Z"},
	)

	rulings := cards.Ruled(context.Background(), versionCard)

	if got, want := rulingIDs(rulings), []string{"newer", "older"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("named %v, want %v", got, want)
	}
	if line := rulings[0].Line(); !strings.Contains(line, "Jsut delete those old agent rows") {
		t.Fatalf("line %q does not carry his words", line)
	}
}

func TestAnOptionOfferedToHimIsNotAnOrderOfHis(t *testing.T) {
	cards := ruledCards(t, spokenAnswer{"asked", "Old cloud agents?", "Options: just delete the cloud agent rows (Recommended) | keep them", "2026-10-22T05:00:00Z"})

	if rulings := cards.Ruled(context.Background(), versionCard); len(rulings) != 0 {
		t.Fatalf("named %v", rulingIDs(rulings))
	}
}

func TestACardsOwnAnswerDoesNotRefuseItsRedraw(t *testing.T) {
	cards := ruledCards(t, spokenAnswer{"own", versionCard, "Just delete them.", "2026-11-03T18:07:00Z"})

	if rulings := cards.Ruled(context.Background(), versionCard); len(rulings) != 0 {
		t.Fatalf("named %v", rulingIDs(rulings))
	}
}

func TestACardReAskingWhatHeAnsweredNamesTheAnswerBeforeAnyOrder(t *testing.T) {
	const reAsked = "An old chat will not open on the new build"
	cards := ruledCards(t,
		spokenAnswer{"same", "How do old chats and new chats stay separate on a new build?", "Convert each one once, in the background.", "2026-11-02T21:09:00Z"},
		spokenAnswer{"order", "Old chat cleanup", "Just delete the old chat rows.", "2026-10-01T00:00:00Z"},
		spokenAnswer{"elsewhere", "How do old apps build?", "From source.", "2026-11-03T00:00:00Z"},
	)

	rulings := cards.Ruled(context.Background(), reAsked)

	if got, want := rulingIDs(rulings), []string{"same", "order"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("named %v, want %v", got, want)
	}
	if line := rulings[0].Line(); !strings.HasPrefix(line, "ANSWERED same 2026-11-02") {
		t.Fatalf("line %q", line)
	}
}

func TestHisSlackLineCountsAsAnAnswer(t *testing.T) {
	cards := ruledCards(t)
	line, err := client.Post(context.Background(), client.Draft{Drive: cards.Bus, Lane: lanes.OwnerRelay, Kind: kindOwner, Force: true, Text: "Old chats on the new build convert once, then read-only."})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := client.Post(context.Background(), client.Draft{Drive: cards.Bus, Lane: "lane-a", Kind: "note", Force: true, Text: "Old chats on the new build convert once, then read-only."}); err != nil {
		t.Fatal(err)
	}

	got := rulingIDs(cards.Ruled(context.Background(), "An old chat will not open on the new build"))

	if want := []string{fmt.Sprintf("#%d", line.Seq)}; !reflect.DeepEqual(got, want) {
		t.Fatalf("named %v, want %v", got, want)
	}
}

func TestARegisterRowCountsByHisWordsNotItsSummary(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	store := &ledgertest.Fake{}
	ctx := context.Background()
	if err := store.SetRow(ctx, "reg", "rule/words", map[string]string{"words": "get rid of the cloud agent rows"}); err != nil {
		t.Fatal(err)
	}
	if err := store.SetRow(ctx, "reg", "rule/summary", map[string]string{"words": "keep the chat", "evidence": "get rid of the cloud agent rows"}); err != nil {
		t.Fatal(err)
	}
	fake := &shelltest.Fake{}
	fake.Reply("[]", "ccn", "-R", "/co", "answer", "list")
	fake.Reply(`[{"id":"reg","updated_at":"2026-11-03T00:00:00Z"}]`, "ccn", "-R", "/co", "ledger", "list")
	cards := Cards{Runner: fake, Ledger: store, Bus: "d", Checkout: "/co", Session: "s1", Program: "p"}

	if got, want := rulingIDs(cards.Ruled(ctx, versionCard)), []string{"rule/words"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("named %v, want %v", got, want)
	}
}

func TestARulingsReadThatFailsRefusesNothing(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	cards := Cards{Runner: &shelltest.Fake{}, Ledger: &ledgertest.Fake{}, Bus: "d", Checkout: "/co", Session: "s1", Program: "p"}

	if rulings := cards.Ruled(context.Background(), versionCard); len(rulings) != 0 {
		t.Fatalf("named %v", rulingIDs(rulings))
	}
}

func TestOpenAsksKeepOnlyUnansweredChoicesAndSayWhichReachedSlack(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply(`{"doc":{"blocks":[
		{"id":"sent","type":"card","title":"Sent","slack":{"url":"https://slack/p1"},"children":[{"id":"sent-choice","type":"choice"}]},
		{"id":"unsent","type":"card","title":"Unsent","children":[{"id":"unsent-choice","type":"choice"}]},
		{"id":"answered","type":"card","title":"Answered","children":[{"id":"answered-choice","type":"choice"}]},
		{"id":"reading","type":"card","title":"Reading","children":[{"id":"reading-md","type":"markdown"}]},
		{"id":"card-merge-triage","type":"card","title":"Merges","children":[{"id":"merge-choice","type":"choice"}]},
		{"id":"sec","type":"section","title":"Section"}
	]},"interactions":{"choices":{"answered-choice":{"value":"a"}}}}`, "cc-present", "outcomes", "--all")
	cards := Cards{Runner: fake, Session: "s1"}

	asks, err := cards.OpenAsks(context.Background())
	if err != nil {
		t.Fatal(err)
	}

	want := []OpenAsk{{"sent", "Sent", true}, {"unsent", "Unsent", false}}
	if !reflect.DeepEqual(asks, want) {
		t.Fatalf("asks = %+v, want %+v", asks, want)
	}
}
