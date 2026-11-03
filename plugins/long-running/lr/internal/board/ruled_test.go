package board

import (
	"context"
	"encoding/json"
	"fmt"
	"reflect"
	"slices"
	"strings"
	"testing"
	"time"

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

	rulings, _ := cards.Ruled(context.Background(), versionCard, "")

	if got, want := rulingIDs(rulings), []string{"newer", "older"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("named %v, want %v", got, want)
	}
	if line := rulings[0].Line(); !strings.Contains(line, "Jsut delete those old agent rows") {
		t.Fatalf("line %q does not carry his words", line)
	}
}

func TestAnOptionOfferedToHimIsNotAnOrderOfHis(t *testing.T) {
	cards := ruledCards(t, spokenAnswer{"asked", "Old cloud agents?", "Options: just delete the cloud agent rows (Recommended) | keep them", "2026-10-22T05:00:00Z"})

	if rulings, _ := cards.Ruled(context.Background(), versionCard, ""); len(rulings) != 0 {
		t.Fatalf("named %v", rulingIDs(rulings))
	}
}

func TestACardsOwnAnswerDoesNotRefuseItsRedraw(t *testing.T) {
	cards := ruledCards(t, spokenAnswer{"own", versionCard, "Just delete them.", "2026-11-03T18:07:00Z"})

	if rulings, _ := cards.Ruled(context.Background(), versionCard, ""); len(rulings) != 0 {
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

	rulings, _ := cards.Ruled(context.Background(), reAsked, "")

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

	rulings, _ := cards.Ruled(context.Background(), "An old chat will not open on the new build", "")
	got := rulingIDs(rulings)

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

	rulings, _ := cards.Ruled(ctx, versionCard, "")
	if got, want := rulingIDs(rulings), []string{"rule/words"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("named %v, want %v", got, want)
	}
}

func TestARulingsReadThatFailsRefusesNothingAndSaysWhichSourceWentUnread(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	fake := &shelltest.Fake{}
	fake.Fail(1, "Error: unknown flag: --full", "ccn", "-R", "/co", "answer", "list")
	fake.Reply("[]", "ccn", "-R", "/co", "ledger", "list")
	cards := Cards{Runner: fake, Ledger: &ledgertest.Fake{}, Bus: "d", Checkout: "/co", Session: "s1", Program: "p"}

	rulings, unread := cards.Ruled(context.Background(), versionCard, "")

	if len(rulings) != 0 {
		t.Fatalf("named %v", rulingIDs(rulings))
	}
	if unread == nil || !strings.Contains(unread.Error(), "his ccn answers were not read") || strings.Contains(unread.Error(), "register") {
		t.Fatalf("unread = %v", unread)
	}
}

const (
	signalCard     = "Open the signal redesign's PRs now?"
	signalCardFile = `{"title":"Open the signal redesign's PRs now?","body":"Your order was branches, no PR, before you woke. Five of six signal answers are built.","options":[{"label":"Keep them as branches for now","hint":"A copy still needs the signal to open until you lift this"}]}`
)

func registerCards(t *testing.T, rule map[string]string, answers ...spokenAnswer) Cards {
	t.Helper()
	t.Setenv("CCI_HOME", t.TempDir())
	store := &ledgertest.Fake{}
	if err := store.SetRow(context.Background(), "reg", "order/overnight-branches", rule); err != nil {
		t.Fatal(err)
	}
	listed, err := json.Marshal(answers)
	if err != nil {
		t.Fatal(err)
	}
	fake := &shelltest.Fake{}
	fake.Reply(string(listed), "ccn", "-R", "/co", "answer", "list")
	fake.Reply(`[{"id":"reg","updated_at":"2026-11-03T00:00:00Z"}]`, "ccn", "-R", "/co", "ledger", "list")
	return Cards{Runner: fake, Ledger: store, Bus: "d", Checkout: "/co", Session: "s1", Program: "p", Now: func() time.Time { return time.Date(2026, 11, 3, 20, 45, 0, 0, time.UTC) }}
}

func TestACardAskingHimToLiftARuleWhoseEndPassedIsRefusedNamingWhyItEnded(t *testing.T) {
	for name, c := range map[string]struct {
		rule    map[string]string
		answers []spokenAnswer
		why     string
	}{
		"overnight": {
			rule: map[string]string{"words": "build each recommended option as a branch, never a PR, until he wakes", "status": "in-force"},
			why:  "ended: its words hold it overnight",
		},
		"until he picks": {
			rule:    map[string]string{"words": "keep the signal redesign as branches until he picks", "status": "in-force"},
			answers: []spokenAnswer{{"a1b2c3d4e5f6a7b8", "Signal redesign: which transport carries a share?", "Direct or relay.", "2026-11-03T18:00:00Z"}},
			why:     "ended: its words hold it until he picks, and he answered a1b2c3d4e5f6",
		},
		"superseded": {
			rule: map[string]string{"words": "keep the signal redesign as branches", "status": "superseded", "superseded_by": "order/prs-open"},
			why:  "ended: superseded by order/prs-open",
		},
	} {
		t.Run(name, func(t *testing.T) {
			cards := registerCards(t, c.rule, c.answers...)

			rulings, _ := cards.Ruled(context.Background(), signalCard, signalCardFile)

			if len(rulings) == 0 || rulings[0].Mark != markEnded || rulings[0].ID != "order/overnight-branches" || !strings.HasPrefix(rulings[0].Title, c.why) {
				t.Fatalf("rulings = %+v, want ENDED order/overnight-branches %q first", rulings, c.why)
			}
		})
	}
}

func TestARuleStatingNoEndLetsTheCardDraw(t *testing.T) {
	cards := registerCards(t, map[string]string{"words": "build each recommended option as a branch, never a PR", "status": "in-force"})

	rulings, _ := cards.Ruled(context.Background(), signalCard, signalCardFile)

	if slices.ContainsFunc(rulings, func(r Ruling) bool { return r.Mark == markEnded }) {
		t.Fatalf("refused: %+v", rulings)
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
