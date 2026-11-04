package board

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ledger/ledgertest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

const (
	oldChatCard  = "card-old-snapshot-new-build-choice"
	oldChatTitle = "An old chat will not open on the new build: is that the expected you meant? What should happen to chats like this?"
)

var (
	upgradeOnce = ownerAnswer{
		ID:      "29b2e59e0267d3e81d0c6406d336be7193204c73",
		Title:   "When an old-build chat converts successfully on the next build, is the result stored, and when does conversion run?",
		Body:    `Owner, verbatim: "nvm dont store it anywhere". The old build persists nothing from a conversion attempt. Any conversion attempt runs only in the background and lazily, when a chat is opened on the new build.`,
		Updated: "2026-11-04T05:37:55Z",
	}
	sonnetPool = ownerAnswer{ID: "5a5a5a5", Title: "Which pool should Sonnet bill to?", Body: "The shared pool.", Updated: "2026-11-04T05:40:00Z"}
)

func oldChatBlock() map[string]any {
	return map[string]any{"id": oldChatCard, "type": "card", "title": oldChatTitle, "children": []any{
		map[string]any{"id": oldChatCard + "-pick", "type": "choice", "options": []any{map[string]any{"id": "a", "label": "Show the can't-open screen"}}},
	}}
}

type judgeCalls struct {
	mu       sync.Mutex
	evidence []string
	verdict  string
	err      error
}

func (j *judgeCalls) judge(_ context.Context, kind, evidence, _ string) (*Judgment, error) {
	j.mu.Lock()
	defer j.mu.Unlock()
	j.evidence = append(j.evidence, evidence)
	if j.err != nil {
		return nil, j.err
	}
	if kind == decidedKind && strings.Contains(evidence, upgradeOnce.Title) {
		return &Judgment{Verdict: j.verdict, Sentence: "Old chats get one background upgrade attempt when opened, and nothing from it is stored."}, nil
	}
	return &Judgment{Verdict: "no"}, nil
}

func settledBoard(t *testing.T, answers ...ownerAnswer) (Cards, *shelltest.Fake, *[]string) {
	t.Helper()
	listed, err := json.Marshal(answers)
	if err != nil {
		t.Fatal(err)
	}
	all, err := json.Marshal(map[string]any{"doc": map[string]any{"blocks": []any{oldChatBlock()}}, "interactions": map[string]any{"choices": map[string]any{}}})
	if err != nil {
		t.Fatal(err)
	}
	one, err := json.Marshal(map[string]any{"rev": 7, "onBoard": true, "doc": map[string]any{"blocks": []any{oldChatBlock()}}, "answers": []any{}})
	if err != nil {
		t.Fatal(err)
	}
	fake := &shelltest.Fake{}
	fake.Reply(string(listed), "ccn", "-R", "/co", "answer", "list")
	fake.Reply(`{"id":"c4rd5"}`, "ccn", "-R", "/co", "ledger", "add")
	fake.Reply(string(all), "cc-present", "outcomes", "--all")
	fake.Reply(string(one), "cc-present", "outcomes", "--block")
	fake.Reply("", "cc-present", "remove-block")
	var withdrawn []string
	cards := Cards{
		Runner: fake, Ledger: &ledgertest.Fake{}, Bus: "d", Checkout: "/co", Session: "s1", StateDir: t.TempDir(), Program: "p", Now: func() time.Time { return cardsNow },
		Withdraw: func(_ context.Context, _, card, text string) error {
			withdrawn = append(withdrawn, card+": "+text)
			return nil
		},
	}
	return cards, fake, &withdrawn
}

func removedBlocks(fake *shelltest.Fake) []string {
	var removed []string
	for _, call := range fake.Calls() {
		if slices.Equal(call.Argv[:2], []string{"cc-present", "remove-block"}) {
			removed = append(removed, call.Argv[2]+" "+call.Argv[slices.Index(call.Argv, "--reason")+1])
		}
	}
	return removed
}

func TestADrawHisAnswerSettlesIsRefusedNamingHisAnswer(t *testing.T) {
	cards, _, _ := settledBoard(t, sonnetPool, upgradeOnce)
	judge := &judgeCalls{verdict: "yes"}
	block, err := json.Marshal(oldChatBlock())
	if err != nil {
		t.Fatal(err)
	}

	settled, err := cards.Settled(context.Background(), judge.judge, block)

	if err != nil || settled == nil || settled.Answer.ID != upgradeOnce.ID {
		t.Fatalf("settled = %+v, %v; want answer %s", settled, err, upgradeOnce.ID)
	}
	if refusal := settled.Refusal().Error(); !strings.Contains(refusal, upgradeOnce.Title) || !strings.Contains(refusal, "29b2e59") || !strings.Contains(refusal, "one background upgrade attempt") {
		t.Fatalf("refusal %q does not name his answer and why it settles the card", refusal)
	}
	if len(judge.evidence) != 1 {
		t.Fatalf("judged %d answers, want only the one sharing the card's subject", len(judge.evidence))
	}
}

func TestADrawStaysUnrefusedWhenTheJudgeIsUnsureOrFails(t *testing.T) {
	block, err := json.Marshal(oldChatBlock())
	if err != nil {
		t.Fatal(err)
	}
	for name, judge := range map[string]*judgeCalls{"unsure": {verdict: "unsure"}, "failed": {err: errors.New("claude: overloaded")}} {
		t.Run(name, func(t *testing.T) {
			cards, _, _ := settledBoard(t, upgradeOnce)
			settled, err := cards.Settled(context.Background(), judge.judge, block)
			if settled != nil {
				t.Fatalf("refused on %s: %+v", name, settled)
			}
			if (err != nil) != (name == "failed") {
				t.Fatalf("err = %v, want a warning only when the judge failed", err)
			}
		})
	}
}

func TestASweepTakesDownAnOpenCardHisAnswerSettlesWithAPlainWhy(t *testing.T) {
	cards, fake, withdrawn := settledBoard(t, sonnetPool, upgradeOnce)
	var out bytes.Buffer

	if err := cards.Unask(context.Background(), "board-refresher", (&judgeCalls{verdict: "yes"}).judge, &out); err != nil {
		t.Fatal(err)
	}

	removed := removedBlocks(fake)
	if len(removed) != 1 || !strings.HasPrefix(removed[0], oldChatCard+" his answer to \""+upgradeOnce.Title+"\" already settles it") {
		t.Fatalf("removed %q, want the old-chat card with his answer as the reason", removed)
	}
	if namesID.MatchString(strings.TrimPrefix(removed[0], oldChatCard)) {
		t.Fatalf("the reason he reads names an id: %q", removed[0])
	}
	if len(*withdrawn) != 1 || !strings.Contains(out.String(), "WITHDRAWN "+oldChatCard) {
		t.Fatalf("withdrawn %q, out %q", *withdrawn, out.String())
	}
}

func TestASweepJudgesACardOnlyAgainstAnswersNewerThanItsLastCheck(t *testing.T) {
	cards, fake, _ := settledBoard(t, upgradeOnce)
	judge := &judgeCalls{verdict: "unsure"}
	for range 2 {
		if err := cards.Unask(context.Background(), "board-refresher", judge.judge, &bytes.Buffer{}); err != nil {
			t.Fatal(err)
		}
	}
	if len(judge.evidence) != 1 || len(removedBlocks(fake)) != 0 {
		t.Fatalf("judged %d times and removed %v; want one judgment, the card kept", len(judge.evidence), removedBlocks(fake))
	}

	newer := ownerAnswer{ID: "77e7e7e", Title: "What happens when an old chat will not open on the new build?", Body: "It stays read-only.", Updated: "2026-11-04T06:29:11Z"}
	next, _, _ := settledBoard(t, upgradeOnce, newer)
	next.Ledger, next.StateDir = cards.Ledger, cards.StateDir
	judge.evidence = nil
	if err := next.Unask(context.Background(), "board-refresher", judge.judge, &bytes.Buffer{}); err != nil {
		t.Fatal(err)
	}
	if len(judge.evidence) != 1 || !strings.Contains(judge.evidence[0], newer.Title) {
		t.Fatalf("judged %q, want only the answer newer than the card's last check", judge.evidence)
	}
}

func TestASweepKeepsACardAndWarnsWhenTheJudgeFails(t *testing.T) {
	cards, fake, _ := settledBoard(t, upgradeOnce)
	judge := &judgeCalls{err: errors.New("claude: overloaded")}
	var out bytes.Buffer
	for range 2 {
		if err := cards.Unask(context.Background(), "board-refresher", judge.judge, &out); err != nil {
			t.Fatal(err)
		}
	}
	if len(removedBlocks(fake)) != 0 || strings.Count(out.String(), "WARNING "+oldChatCard+" stays up") != 2 || len(judge.evidence) != 2 {
		t.Fatalf("removed %v, out %q, judged %d; want the card kept, warned and judged again next pass", removedBlocks(fake), out.String(), len(judge.evidence))
	}
}
