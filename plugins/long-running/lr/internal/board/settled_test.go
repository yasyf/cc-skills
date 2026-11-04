package board

import (
	"bytes"
	"cmp"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
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

var oldChatUp = []any{oldChatBlock()}

func oldChatBlock() map[string]any {
	return map[string]any{"id": oldChatCard, "type": "card", "title": oldChatTitle, "children": []any{
		map[string]any{"id": oldChatCard + "-pick", "type": "choice", "options": []any{map[string]any{"id": "a", "label": "Show the can't-open screen"}}},
	}}
}

type judgeCalls struct {
	mu       sync.Mutex
	evidence []string
	verdict  string
	confirms string
	err      error
}

func (j *judgeCalls) judge(_ context.Context, kind, evidence, _ string) (*Judgment, error) {
	j.mu.Lock()
	defer j.mu.Unlock()
	if kind == confirmKind {
		return &Judgment{Verdict: cmp.Or(j.confirms, "no"), Sentence: "Act on your reading of his words and say what you did."}, j.err
	}
	j.evidence = append(j.evidence, evidence)
	if j.err != nil {
		return nil, j.err
	}
	if kind == decidedKind && strings.Contains(evidence, upgradeOnce.Title) {
		return &Judgment{Verdict: j.verdict, Sentence: "Old chats get one background upgrade attempt when opened, and nothing from it is stored."}, nil
	}
	return &Judgment{Verdict: "no"}, nil
}

func settledBoard(t *testing.T, board []any, answers ...ownerAnswer) (Cards, *shelltest.Fake, *[]string) {
	t.Helper()
	return settledBoardFirst(t, func(*shelltest.Fake) {}, board, answers...)
}

func settledBoardFirst(t *testing.T, first func(*shelltest.Fake), board []any, answers ...ownerAnswer) (Cards, *shelltest.Fake, *[]string) {
	t.Helper()
	listed, err := json.Marshal(answers)
	if err != nil {
		t.Fatal(err)
	}
	all, err := json.Marshal(map[string]any{"doc": map[string]any{"blocks": board}, "interactions": map[string]any{"choices": map[string]any{}}})
	if err != nil {
		t.Fatal(err)
	}
	one, err := json.Marshal(map[string]any{"rev": 7, "onBoard": true, "doc": map[string]any{"blocks": []any{oldChatBlock()}}, "answers": []any{}})
	if err != nil {
		t.Fatal(err)
	}
	fake := &shelltest.Fake{}
	first(fake)
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
		Reports: func(context.Context) ([]ownerAnswer, error) { return nil, nil },
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
	cards, _, _ := settledBoard(t, nil, sonnetPool, upgradeOnce)
	judge := &judgeCalls{verdict: "yes"}
	block, err := json.Marshal(oldChatBlock())
	if err != nil {
		t.Fatal(err)
	}

	refused, err := cards.Check(context.Background(), judge.judge, block)

	if err != nil || refused == nil {
		t.Fatalf("refused = %v, %v; want answer %s named", refused, err, upgradeOnce.ID)
	}
	if refusal := refused.Error(); !strings.Contains(refusal, upgradeOnce.Title) || !strings.Contains(refusal, "29b2e59") || !strings.Contains(refusal, "one background upgrade attempt") {
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
			cards, _, _ := settledBoard(t, nil, upgradeOnce)
			refused, err := cards.Check(context.Background(), judge.judge, block)
			if refused != nil {
				t.Fatalf("refused on %s: %v", name, refused)
			}
			if (err != nil) != (name == "failed") {
				t.Fatalf("err = %v, want a warning only when the judge failed", err)
			}
		})
	}
}

func TestASweepTakesDownAnOpenCardHisAnswerSettlesWithAPlainWhy(t *testing.T) {
	cards, fake, withdrawn := settledBoard(t, oldChatUp, sonnetPool, upgradeOnce)
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
	cards, fake, _ := settledBoard(t, oldChatUp, upgradeOnce)
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
	next, _, _ := settledBoard(t, oldChatUp, upgradeOnce, newer)
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
	cards, fake, _ := settledBoard(t, oldChatUp, upgradeOnce)
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

func TestADrawAskingWhatACardOnHisBoardAlreadyAsksIsRefused(t *testing.T) {
	cards, _, _ := settledBoard(t, oldChatUp)
	second := oldChatBlock()
	second["id"], second["title"] = "card-old-snapshot-again", "  an old chat will not open on the new build: is that the expected you meant?   What should happen to chats like this?"
	block, err := json.Marshal(second)
	if err != nil {
		t.Fatal(err)
	}

	refused, err := cards.Check(context.Background(), (&judgeCalls{}).judge, block)

	if err != nil || refused == nil || !strings.Contains(refused.Error(), oldChatCard+" already asks him") {
		t.Fatalf("refused = %v, %v; want the card already on his board named", refused, err)
	}
}

func TestADrawAskingHimToGradeAReadingOfHisWordsIsRefused(t *testing.T) {
	cards, _, _ := settledBoard(t, nil)
	reading := map[string]any{"id": "card-read-sessions", "type": "card", "title": "Did I read this correctly? Try to move more sessions to remote machines from now on", "children": []any{
		map[string]any{"id": "card-read-sessions-pick", "type": "choice", "options": []any{map[string]any{"id": "right", "label": "Right"}, map[string]any{"id": "wrong", "label": "Wrong"}}},
	}}
	block, err := json.Marshal(reading)
	if err != nil {
		t.Fatal(err)
	}

	refused, err := cards.Check(context.Background(), (&judgeCalls{confirms: "yes"}).judge, block)

	if err != nil || refused == nil || !strings.Contains(refused.Error(), "grade a reading of his own words") {
		t.Fatalf("refused = %v, %v; want the reading card refused", refused, err)
	}
}

func TestARepushOfACardAlreadyOnHisBoardIsLeftToTheSweep(t *testing.T) {
	cards, _, _ := settledBoard(t, oldChatUp, upgradeOnce)
	judge := &judgeCalls{verdict: "yes", confirms: "yes"}
	block, err := json.Marshal(oldChatBlock())
	if err != nil {
		t.Fatal(err)
	}

	refused, err := cards.Check(context.Background(), judge.judge, block)

	if refused != nil || err != nil || len(judge.evidence) != 0 {
		t.Fatalf("refused = %v, %v after %d judgments; want the card already up passed unjudged", refused, err, len(judge.evidence))
	}
}

func TestACardWhoseExplainMoreIsOnlyALinkIsRefusedBeforeAnyJudgeRuns(t *testing.T) {
	board, _, _ := settledBoard(t, nil, upgradeOnce)
	judged := &judgeCalls{verdict: "no"}
	for name, children := range map[string][]any{
		"asking":     {map[string]any{"id": "why", "type": "markdown", "md": "**Explain more:** see https://docs.poetic.design/one-pagers/x/"}, map[string]any{"id": "pick", "type": "choice"}},
		"not asking": {map[string]any{"id": "why", "type": "markdown", "md": "https://docs.poetic.design/one-pagers/x/"}},
	} {
		card, err := json.Marshal(map[string]any{"id": "card-x", "type": "card", "title": "Which store holds the topics?", "children": children})
		if err != nil {
			t.Fatal(err)
		}
		refused, unchecked := board.Check(context.Background(), judged.judge, card)
		if refused == nil || !strings.HasPrefix(refused.Error(), "refused: ") || unchecked != nil {
			t.Errorf("%s: refused %v, unchecked %v", name, refused, unchecked)
		}
	}
	if len(judged.evidence) != 0 {
		t.Fatalf("a bare card reached the judge: %v", judged.evidence)
	}
}

func touchedAt(t *testing.T, at time.Time) func(*shelltest.Fake) {
	t.Helper()
	one, err := json.Marshal(map[string]any{"rev": 7, "onBoard": true, "doc": map[string]any{"blocks": []any{oldChatBlock()}}, "answers": []any{
		map[string]any{"origin": "agent", "at": cardsNow.Format(time.RFC3339)},
		map[string]any{"origin": "human", "at": at.Format(time.RFC3339)},
	}})
	if err != nil {
		t.Fatal(err)
	}
	return func(fake *shelltest.Fake) { fake.Reply(string(one), "cc-present", "outcomes", "--block") }
}

func TestASweepLeavesACardHeTouchedInTheLastTwoMinutes(t *testing.T) {
	cards, fake, withdrawn := settledBoardFirst(t, touchedAt(t, cardsNow.Add(-time.Minute)), oldChatUp, upgradeOnce)
	var out bytes.Buffer

	if err := cards.Unask(context.Background(), "supervisor", (&judgeCalls{verdict: "yes"}).judge, &out); err != nil {
		t.Fatal(err)
	}

	if removed := removedBlocks(fake); len(removed) != 0 || len(*withdrawn) != 0 {
		t.Fatalf("removed %q, withdrew %q from under his hands", removed, *withdrawn)
	}
	if !strings.Contains(out.String(), "HELD "+oldChatCard) {
		t.Fatalf("out = %q", out.String())
	}
}

func TestASweepKeepsGoingWhenTheBoardHoldsACardHeTouched(t *testing.T) {
	refusal := "refusing to remove block: the human's choice.selected holds it for 30 minutes unless the round is submitted"
	cards, _, withdrawn := settledBoardFirst(t, func(fake *shelltest.Fake) { fake.Fail(1, refusal, "cc-present", "remove-block") }, oldChatUp, upgradeOnce)
	var out bytes.Buffer

	if err := cards.Unask(context.Background(), "supervisor", (&judgeCalls{verdict: "yes"}).judge, &out); err != nil {
		t.Fatalf("a held card failed the pass: %v", err)
	}

	if len(*withdrawn) != 0 || !strings.Contains(out.String(), "HELD "+oldChatCard) {
		t.Fatalf("withdrew %q, out %q", *withdrawn, out.String())
	}
}

func TestASweepTakesDownACardHeTouchedLongerAgo(t *testing.T) {
	cards, fake, _ := settledBoardFirst(t, touchedAt(t, cardsNow.Add(-3*time.Minute)), oldChatUp, upgradeOnce)

	if err := cards.Unask(context.Background(), "supervisor", (&judgeCalls{verdict: "yes"}).judge, &bytes.Buffer{}); err != nil {
		t.Fatal(err)
	}

	if removed := removedBlocks(fake); len(removed) != 1 {
		t.Fatalf("removed %q, want the card", removed)
	}
}

func TestASweepCutOffMidPassKeepsWhatItJudged(t *testing.T) {
	first, second := oldChatBlock(), oldChatBlock()
	second["id"] = "card-old-chat-twin"
	onBoard := func(card map[string]any) string {
		one, err := json.Marshal(map[string]any{"onBoard": true, "doc": map[string]any{"blocks": []any{card}}})
		if err != nil {
			t.Fatal(err)
		}
		return string(one)
	}
	cards, _, _ := settledBoardFirst(t, func(fake *shelltest.Fake) {
		fake.Reply(onBoard(first), "cc-present", "outcomes", "--block", oldChatCard)
		fake.Reply(onBoard(second), "cc-present", "outcomes", "--block", "card-old-chat-twin")
	}, []any{first, second}, upgradeOnce)
	ctx, cutOff := context.WithCancel(context.Background())
	defer cutOff()
	var judged []string
	judge := func(_ context.Context, _, _, card string) (*Judgment, error) {
		judged = append(judged, card)
		if len(judged) == 2 {
			cutOff()
		}
		return &Judgment{Verdict: "no"}, nil
	}

	if err := cards.Unask(ctx, "supervisor", judge, &bytes.Buffer{}); !errors.Is(err, context.Canceled) {
		t.Fatalf("Unask = %v, want the cut-off", err)
	}
	judged = nil
	if err := cards.Unask(context.Background(), "supervisor", judge, &bytes.Buffer{}); err != nil {
		t.Fatal(err)
	}

	if len(judged) != 1 || !strings.Contains(judged[0], "card-old-chat-twin") {
		t.Fatalf("the next pass judged %q, want only the card the cut-off pass never finished", judged)
	}
}

func TestACardItsSweepStampedStillHasNoEndCondition(t *testing.T) {
	cards, _, _ := settledBoard(t, oldChatUp, upgradeOnce)
	judge := &judgeCalls{verdict: "unsure"}
	if err := cards.Pass(context.Background(), "supervisor", judge.judge, &bytes.Buffer{}); err != nil {
		t.Fatal(err)
	}

	var out bytes.Buffer
	if err := cards.Pass(context.Background(), "supervisor", judge.judge, &out); err != nil {
		t.Fatalf("a stamped card failed the next pass: %v", err)
	}

	if !strings.Contains(out.String(), "PROBLEM "+oldChatCard+": no end condition") {
		t.Fatalf("out = %q", out.String())
	}
	if missing, err := cards.Missing(context.Background(), []string{oldChatCard}); err != nil || len(missing) != 1 {
		t.Fatalf("Missing = %v, %v; want the stamped card named", missing, err)
	}
}

var stepsDone = ownerAnswer{ID: "398366", Title: "report record from owner-apps-1", Body: "owner-apps-1: the old chat card is done: an old chat will not open on the new build, and all three steps of what should happen to chats like this are finished.", Updated: "2026-11-04T05:50:00.000Z"}

func finishedJudge(verdict string, judged *[]string) Judge {
	return func(_ context.Context, kind, evidence, _ string) (*Judgment, error) {
		if kind != finishedKind {
			return nil, fmt.Errorf("the finished sweep asked the %s judge", kind)
		}
		*judged = append(*judged, evidence)
		return &Judgment{Verdict: verdict, Sentence: "The lane finished all three steps."}, nil
	}
}

func TestASweepTakesDownACardALaneReportsFinished(t *testing.T) {
	cards, fake, withdrawn := settledBoard(t, oldChatUp)
	cards.Reports = func(context.Context) ([]ownerAnswer, error) { return []ownerAnswer{stepsDone}, nil }
	var judged []string

	if err := cards.Finished(context.Background(), "supervisor", finishedJudge("yes", &judged), &bytes.Buffer{}); err != nil {
		t.Fatal(err)
	}

	removed := removedBlocks(fake)
	if len(removed) != 1 || !strings.HasPrefix(removed[0], oldChatCard+" a lane already finished what it asks: The lane finished all three steps") {
		t.Fatalf("removed %q", removed)
	}
	if len(judged) != 1 || !strings.Contains(judged[0], stepsDone.Body) || len(*withdrawn) != 1 {
		t.Fatalf("judged %q, withdrew %q", judged, *withdrawn)
	}
}

func TestASweepKeepsACardWhenItsReportIsOnlyRelatedOrUnsure(t *testing.T) {
	for _, verdict := range []string{"no", "unsure"} {
		cards, fake, _ := settledBoard(t, oldChatUp)
		cards.Reports = func(context.Context) ([]ownerAnswer, error) { return []ownerAnswer{stepsDone}, nil }
		var judged []string
		if err := cards.Finished(context.Background(), "supervisor", finishedJudge(verdict, &judged), &bytes.Buffer{}); err != nil {
			t.Fatal(err)
		}
		if removed := removedBlocks(fake); len(removed) != 0 || len(judged) != 1 {
			t.Fatalf("%s: removed %q after %d judgments", verdict, removed, len(judged))
		}
	}
}

func TestAReportOnAnotherSubjectNeverReachesTheJudge(t *testing.T) {
	cards, fake, _ := settledBoard(t, oldChatUp)
	unrelated := ownerAnswer{ID: "398400", Title: "report record from lane-b", Body: "The Sonnet pool bills to the shared account now.", Updated: stepsDone.Updated}
	cards.Reports = func(context.Context) ([]ownerAnswer, error) { return []ownerAnswer{unrelated}, nil }
	var judged []string

	if err := cards.Finished(context.Background(), "supervisor", finishedJudge("yes", &judged), &bytes.Buffer{}); err != nil {
		t.Fatal(err)
	}

	if removed := removedBlocks(fake); len(removed) != 0 || len(judged) != 0 {
		t.Fatalf("removed %q, judged %q", removed, judged)
	}
}

func TestASweepJudgesEachReportOnce(t *testing.T) {
	cards, _, _ := settledBoard(t, oldChatUp)
	cards.Reports = func(context.Context) ([]ownerAnswer, error) { return []ownerAnswer{stepsDone}, nil }
	var judged []string
	for range 2 {
		if err := cards.Finished(context.Background(), "supervisor", finishedJudge("no", &judged), &bytes.Buffer{}); err != nil {
			t.Fatal(err)
		}
	}
	if len(judged) != 1 {
		t.Fatalf("judged %d times, want once", len(judged))
	}
}

func TestAFailedReportReadKeepsEveryCardAndSaysSo(t *testing.T) {
	cards, fake, _ := settledBoard(t, oldChatUp)
	cards.Reports = func(context.Context) ([]ownerAnswer, error) { return nil, errors.New("the bus is down") }
	var out bytes.Buffer

	if err := cards.Finished(context.Background(), "supervisor", finishedJudge("yes", &[]string{}), &out); err != nil {
		t.Fatal(err)
	}

	if removed := removedBlocks(fake); len(removed) != 0 || !strings.Contains(out.String(), "WARNING every card stays up unchecked against the lanes' reports") {
		t.Fatalf("removed %q, out %q", removed, out.String())
	}
}

func TestAReportBodyReadsAtMostTheHeadOfItsFile(t *testing.T) {
	short := filepath.Join(t.TempDir(), "report.txt")
	if err := os.WriteFile(short, []byte("all three steps are done"), 0o600); err != nil {
		t.Fatal(err)
	}
	if body := reportBody("first line", short); body != "all three steps are done" {
		t.Fatalf("body = %q", body)
	}

	log := filepath.Join(t.TempDir(), "build.log")
	if err := os.WriteFile(log, []byte(strings.Repeat("x", 10*reportBytes)), 0o600); err != nil {
		t.Fatal(err)
	}
	if body := reportBody("first line", log); len(body) != reportBytes {
		t.Fatalf("read %d bytes of an attached log, want %d", len(body), reportBytes)
	}

	if body := reportBody("first line", filepath.Join(t.TempDir(), "gone.txt")); body != "first line" {
		t.Fatalf("body = %q with no file", body)
	}
}
