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
	sonnetPool   = ownerAnswer{ID: "5a5a5a5", Title: "Which pool should Sonnet bill to?", Body: "The shared pool.", Updated: "2026-11-04T05:40:00Z"}
	answeredHere = answeredWith(boardCardLabel+oldChatCard, ownerLabel)
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
	options  string
	err      error
}

func (j *judgeCalls) judge(_ context.Context, kind, evidence, _ string) (*Judgment, error) {
	j.mu.Lock()
	defer j.mu.Unlock()
	if kind == confirmKind {
		return &Judgment{Verdict: cmp.Or(j.confirms, "no"), Sentence: "Act on your reading of his words and say what you did."}, j.err
	}
	if kind == optionsKind {
		return &Judgment{Verdict: cmp.Or(j.options, "no"), Sentence: "It offers only a retry or a skip; add the option that fixes the cause."}, j.err
	}
	if kind == setOptionsKind {
		return &Judgment{Verdict: "no"}, j.err
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
	fake.Reply("[]", "ccn", "-R", "/co", "ledger", "list", "--json")
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
		Report:  func(context.Context, string, []string, string, string) error { return nil },
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

func TestASweepTakesDownAnOpenCardHisAnswerToItSettlesWithAPlainWhy(t *testing.T) {
	cards, fake, withdrawn := settledBoard(t, oldChatUp, sonnetPool, answeredHere)
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

type laneReport struct{ by, to, topic, text string }

func TestASweepKeepsACardHisAnswerToAnotherQuestionSettlesAndTellsTheLaneThatDrewIt(t *testing.T) {
	for name, tc := range map[string]struct{ drew, told string }{
		"a lane drew it":               {"owner-apps-1", "owner-apps-1"},
		"no lane is recorded":          {"", ""},
		"the sweep's own lane drew it": {"board-refresher", ""},
	} {
		t.Run(name, func(t *testing.T) {
			cards, fake, withdrawn := settledBoard(t, oldChatUp, upgradeOnce)
			if tc.drew != "" {
				if _, err := cards.Until(context.Background(), oldChatCard, "at:3d", tc.drew); err != nil {
					t.Fatal(err)
				}
			}
			var reports []laneReport
			cards.Report = func(_ context.Context, by string, to []string, topic, text string) error {
				reports = append(reports, laneReport{by, strings.Join(to, ","), topic, text})
				return nil
			}
			var out bytes.Buffer

			for range 2 {
				if err := cards.Unask(context.Background(), "board-refresher", (&judgeCalls{verdict: "yes"}).judge, &out); err != nil {
					t.Fatal(err)
				}
			}

			if removed := removedBlocks(fake); len(removed) != 0 || len(*withdrawn) != 0 {
				t.Fatalf("removed %q, withdrew %q; his answer was to another question", removed, *withdrawn)
			}
			report := out.String()
			if tc.told == "" {
				if len(reports) != 0 || strings.Count(report, "KEPT "+oldChatCard+": Card "+oldChatCard+" stays") != 1 {
					t.Fatalf("reports %+v, out %q; the sweep's own lane reads the report printed, never posted to itself", reports, report)
				}
			} else {
				if len(reports) != 1 || reports[0].to != tc.told || reports[0].topic != oldChatCard {
					t.Fatalf("reports %+v, want one to %s about %s", reports, tc.told, oldChatCard)
				}
				if !strings.Contains(report, "KEPT "+oldChatCard+": told "+tc.told) {
					t.Fatalf("out = %q", report)
				}
				report = reports[0].text
			}
			for _, named := range []string{upgradeOnce.Title, "29b2e59", "one background upgrade attempt", "lr ask-owner --withdraw " + oldChatCard} {
				if !strings.Contains(report, named) {
					t.Fatalf("report %q does not carry %q", report, named)
				}
			}
		})
	}
}

func TestASweepPrefersHisAnswerToTheCardItselfOverOneToAnotherQuestion(t *testing.T) {
	cards, fake, _ := settledBoard(t, oldChatUp, upgradeOnce, answeredHere)
	var reports []string
	cards.Report = func(_ context.Context, _ string, _ []string, _, text string) error {
		reports = append(reports, text)
		return nil
	}

	if err := cards.Unask(context.Background(), "board-refresher", (&judgeCalls{verdict: "yes"}).judge, &bytes.Buffer{}); err != nil {
		t.Fatal(err)
	}

	if removed := removedBlocks(fake); len(removed) != 1 || len(reports) != 0 {
		t.Fatalf("removed %q, reported %q; want the card withdrawn on his answer to it", removed, reports)
	}
}

func alwaysSettles(context.Context, string, string, string) (*Judgment, error) {
	return &Judgment{Verdict: "yes", Sentence: "Old chats show the can't-open screen."}, nil
}

func TestASweepKeepsACardWhoseQuestionAnAnswerOffItReadsTheSame(t *testing.T) {
	same := upgradeOnce
	same.Title = "  an old chat will not open on the new build: is that the expected you meant?   What should happen to chats like this?"
	cards, fake, _ := settledBoard(t, oldChatUp, same)
	var out bytes.Buffer

	if err := cards.Unask(context.Background(), "board-refresher", alwaysSettles, &out); err != nil {
		t.Fatal(err)
	}

	if removed := removedBlocks(fake); len(removed) != 0 || !strings.Contains(out.String(), "KEPT "+oldChatCard) {
		t.Fatalf("removed %q, out %q; no tap on this card stands behind it", removed, out.String())
	}
}

func TestOnlyHisTapOrNumberedReplyOrARelayRecordOnACardClosesIt(t *testing.T) {
	onThisCard := boardCardLabel + oldChatCard
	inThread := threadProof + "1793861669.537559"
	tapped := "proof:board-watch:key"
	for name, tc := range map[string]struct {
		tags   []string
		body   string
		closes bool
	}{
		"his question in its thread":     {[]string{ownerLabel, "board-answer", onThisCard, inThread}, "what does that word mean?\nBoard question: x", false},
		"his typed words with no number": {[]string{ownerLabel, "board-answer", onThisCard, inThread}, "also fix the markdown\nBoard question: x", false},
		"his complaint listed by number": {[]string{ownerLabel, "board-answer", onThisCard, inThread}, "1. this line is not needed, 2) there is no button here\nBoard question: x", false},
		"a lane's reply recorded on it":  {[]string{cardLabel + oldChatCard}, "It means the Description panel.", false},
		"his write-in with no number":    {[]string{ownerLabel, "board-answer", onThisCard, tapped}, "wrote “why this one?”\nBoard question: x", false},
		"his tap on another card":        {[]string{ownerLabel, "board-answer", boardCardLabel + "card-another", tapped}, "picked “Show the can't-open screen”\nBoard question: x", false},
		"his tap":                        {[]string{ownerLabel, "board-answer", onThisCard, tapped}, "picked “Show the can't-open screen”\nBoard question: x", true},
		"his write-in naming an option":  {[]string{ownerLabel, "board-answer", onThisCard, tapped}, "wrote “1 but say why”\nBoard question: x", true},
		"his numbered reply":             {[]string{ownerLabel, "board-answer", onThisCard, inThread}, "I pick 1\nBoard question: x", true},
		"a relay's record of his words":  {[]string{ownerLabel, cardLabel + oldChatCard}, "Owner 10:59 PM in the card's thread: \"keep it\".", true},
	} {
		t.Run(name, func(t *testing.T) {
			row := ownerAnswer{ID: "153ea56635c28aa4", Title: oldChatTitle, Body: tc.body, Updated: "2026-11-05T06:54:31Z", Tags: tc.tags}
			cards, fake, withdrawn := settledBoard(t, oldChatUp, row)
			if _, err := cards.Until(context.Background(), oldChatCard, "at:3d", "owner-apps-1"); err != nil {
				t.Fatal(err)
			}
			var reports []laneReport
			cards.Report = func(_ context.Context, by string, to []string, topic, text string) error {
				reports = append(reports, laneReport{by, strings.Join(to, ","), topic, text})
				return nil
			}

			if err := cards.Unask(context.Background(), "board-refresher", alwaysSettles, &bytes.Buffer{}); err != nil {
				t.Fatal(err)
			}

			removed := removedBlocks(fake)
			if tc.closes {
				if len(removed) != 1 || len(*withdrawn) != 1 || len(reports) != 0 {
					t.Fatalf("removed %q, withdrew %q, reports %+v; want the card closed", removed, *withdrawn, reports)
				}
				return
			}
			if len(removed) != 0 || len(*withdrawn) != 0 {
				t.Fatalf("removed %q, withdrew %q; want the card left open with its buttons", removed, *withdrawn)
			}
			if len(reports) != 1 || reports[0].to != "owner-apps-1" || reports[0].topic != oldChatCard {
				t.Fatalf("reports %+v, want one to the lane that drew it", reports)
			}
		})
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

func renderBlock(shot map[string]any) map[string]any {
	card := oldChatBlock()
	children := card["children"].([]any)
	if shot["type"] == "image" {
		card["children"] = append(children, shot)
		return card
	}
	children[0].(map[string]any)["options"].([]any)[0].(map[string]any)["visual"] = shot["visual"]
	return card
}

var (
	screenshotChild  = map[string]any{"id": "shot", "type": "image", "src": "asset:6184", "alt": "The new screen"}
	screenshotVisual = map[string]any{"visual": map[string]any{"id": "shot", "type": "image", "src": "asset:6184", "alt": "The new screen"}}
)

func answeredWith(tags ...string) ownerAnswer {
	answer := upgradeOnce
	answer.Tags = tags
	return answer
}

func TestOnlyHisAnswerToACardSettlesItWhenItShowsARender(t *testing.T) {
	cases := []struct {
		name             string
		card             map[string]any
		answer           ownerAnswer
		settle, withdraw bool
	}{
		{"a screenshot child and an older answer stays up", renderBlock(screenshotChild), answeredWith("from:owner"), false, false},
		{"a screenshot on an option and an older answer stays up", renderBlock(screenshotVisual), answeredWith("from:owner"), false, false},
		{"his answer to the card before it stays up", renderBlock(screenshotChild), answeredWith("card:card-chat-live", "board-card:card-chat-live"), false, false},
		{"his Slack tap on it settles", renderBlock(screenshotChild), answeredWith(cardLabel+oldChatCard, ownerLabel), true, true},
		{"his board pick on it settles", renderBlock(screenshotVisual), answeredWith(boardCardLabel+oldChatCard, ownerLabel), true, true},
		{"no screenshot and an older answer to another question refuses a draw and leaves a drawn card up", oldChatBlock(), answeredWith("from:owner"), true, false},
	}
	for _, tc := range cases {
		t.Run("sweep: "+tc.name, func(t *testing.T) {
			shown, err := json.Marshal(map[string]any{"rev": 7, "onBoard": true, "doc": map[string]any{"blocks": []any{tc.card}}, "answers": []any{}})
			if err != nil {
				t.Fatal(err)
			}
			cards, fake, withdrawn := settledBoardFirst(t, func(fake *shelltest.Fake) { fake.Reply(string(shown), "cc-present", "outcomes", "--block") }, []any{tc.card}, tc.answer)
			if err := cards.Unask(context.Background(), "board-refresher", (&judgeCalls{verdict: "yes"}).judge, &bytes.Buffer{}); err != nil {
				t.Fatal(err)
			}
			if removed := removedBlocks(fake); (len(removed) == 1) != tc.withdraw || (len(*withdrawn) == 1) != tc.withdraw {
				t.Fatalf("removed %q, withdrew %q, want withdrawn = %v", removed, *withdrawn, tc.withdraw)
			}
		})
		t.Run("draw: "+tc.name, func(t *testing.T) {
			block, err := json.Marshal(tc.card)
			if err != nil {
				t.Fatal(err)
			}
			cards, _, _ := settledBoard(t, nil, tc.answer)
			refused, err := cards.Check(context.Background(), (&judgeCalls{verdict: "yes"}).judge, block)
			if err != nil || (refused != nil) != tc.settle {
				t.Fatalf("refused = %v, %v, want refused = %v", refused, err, tc.settle)
			}
		})
	}
}

func TestHisAnswerToARenderCardReachesTheJudgeBehindCloserAnswers(t *testing.T) {
	theirs := answeredWith(cardLabel + oldChatCard)
	theirs.Updated = "2026-11-04T01:00:00Z"
	answers := make([]ownerAnswer, 0, answersJudged+2)
	answers = append(answers, theirs)
	for i := range answersJudged + 1 {
		closer := upgradeOnce
		closer.ID = fmt.Sprintf("c%d", i)
		answers = append(answers, closer)
	}
	block, err := json.Marshal(renderBlock(screenshotChild))
	if err != nil {
		t.Fatal(err)
	}
	cards, _, _ := settledBoard(t, nil, answers...)
	refused, err := cards.Check(context.Background(), (&judgeCalls{verdict: "yes"}).judge, block)
	if err != nil || refused == nil {
		t.Fatalf("refused = %v, %v; want his answer judged past the cap", refused, err)
	}
}

func TestALaneReportNeverFinishesACardShowingARender(t *testing.T) {
	card := renderBlock(screenshotChild)
	shown, err := json.Marshal(map[string]any{"rev": 7, "onBoard": true, "doc": map[string]any{"blocks": []any{card}}, "answers": []any{}})
	if err != nil {
		t.Fatal(err)
	}
	cards, fake, _ := settledBoardFirst(t, func(fake *shelltest.Fake) { fake.Reply(string(shown), "cc-present", "outcomes", "--block") }, []any{card})
	cards.Reports = func(context.Context) ([]ownerAnswer, error) { return []ownerAnswer{stepsDone}, nil }
	var judged []string

	if err := cards.Finished(context.Background(), "supervisor", finishedJudge("yes", &judged), &bytes.Buffer{}); err != nil {
		t.Fatal(err)
	}

	if removed := removedBlocks(fake); len(removed) != 0 || len(judged) != 0 {
		t.Fatalf("removed %q after judging %q", removed, judged)
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
	cards, fake, withdrawn := settledBoardFirst(t, touchedAt(t, cardsNow.Add(-time.Minute)), oldChatUp, answeredHere)
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
	cards, _, withdrawn := settledBoardFirst(t, func(fake *shelltest.Fake) { fake.Fail(1, refusal, "cc-present", "remove-block") }, oldChatUp, answeredHere)
	var out bytes.Buffer

	if err := cards.Unask(context.Background(), "supervisor", (&judgeCalls{verdict: "yes"}).judge, &out); err != nil {
		t.Fatalf("a held card failed the pass: %v", err)
	}

	if len(*withdrawn) != 0 || !strings.Contains(out.String(), "HELD "+oldChatCard) {
		t.Fatalf("withdrew %q, out %q", *withdrawn, out.String())
	}
}

func TestASweepTakesDownACardHeTouchedLongerAgo(t *testing.T) {
	cards, fake, _ := settledBoardFirst(t, touchedAt(t, cardsNow.Add(-3*time.Minute)), oldChatUp, answeredHere)

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

func TestASweepLeavesUpACardALaneReportsFinishedAndTellsTheLaneThatDrewIt(t *testing.T) {
	cards, fake, withdrawn := settledBoard(t, oldChatUp)
	if _, err := cards.Until(context.Background(), oldChatCard, "at:3d", "owner-apps-1"); err != nil {
		t.Fatal(err)
	}
	cards.Reports = func(context.Context) ([]ownerAnswer, error) { return []ownerAnswer{stepsDone}, nil }
	var told []laneReport
	cards.Report = func(_ context.Context, by string, to []string, topic, text string) error {
		told = append(told, laneReport{by, strings.Join(to, ","), topic, text})
		return nil
	}
	var judged []string

	if err := cards.Finished(context.Background(), "supervisor", finishedJudge("yes", &judged), &bytes.Buffer{}); err != nil {
		t.Fatal(err)
	}

	if removed := removedBlocks(fake); len(removed) != 0 || len(*withdrawn) != 0 {
		t.Fatalf("removed %q, withdrew %q; a lane's report never closes his card", removed, *withdrawn)
	}
	if len(judged) != 1 || !strings.Contains(judged[0], stepsDone.Body) {
		t.Fatalf("judged %q", judged)
	}
	if len(told) != 1 || told[0].to != "owner-apps-1" || !strings.Contains(told[0].text, "The lane finished all three steps") || !strings.Contains(told[0].text, "lr ask-owner --withdraw "+oldChatCard) {
		t.Fatalf("told %+v, want the drawing lane told why it may withdraw", told)
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

func TestACardOfferingOnlyWaysToLiveWithAFailureIsRefusedSayingWhatToAdd(t *testing.T) {
	cards, _, _ := settledBoard(t, nil)
	block := json.RawMessage(`{"id":"card-lost-batch","type":"card","title":"Lost the nightly batch: resend it or skip the night?","summary":"The connection dropped before the batch id came back.","children":[{"id":"pick","type":"choice","options":[{"id":"a","label":"Resend once"},{"id":"b","label":"Skip the night"}]}]}`)
	refused, err := cards.Check(context.Background(), (&judgeCalls{options: "yes"}).judge, block)
	if err != nil || refused == nil || !strings.Contains(refused.Error(), "add the option that fixes the cause") {
		t.Fatalf("Check = %v, %v; want a refusal saying what to add", refused, err)
	}
}

const pendingBugsCard = `{"id":"card-next-bugs-seven","type":"card","title":"Pending next-tree bugs: what each needs from you?","summary":"Seven things wait on you, and the old-chat blocker waits on lanes.","children":[{"id":"pick","type":"choice","options":[{"id":"a","label":"Tap all four ready fixes"},{"id":"b","label":"Tap them after the old-chat fixes land"},{"id":"c","label":"Hold all of them"}]}]}`

func setOptionsJudge(verdict string, failure error, read *string) Judge {
	return func(_ context.Context, kind, _, artifact string) (*Judgment, error) {
		if kind != setOptionsKind {
			return &Judgment{Verdict: "no"}, nil
		}
		*read = artifact
		if failure != nil {
			return nil, failure
		}
		return &Judgment{Verdict: verdict, Sentence: "Name each of the four ready fixes, with its pull request link and what it does for a user."}, nil
	}
}

func TestACardWhoseOptionActsOnASetItDoesNotNameWarnsSayingWhatToList(t *testing.T) {
	cards, _, _ := settledBoard(t, nil)
	var read string
	warned := cards.UnnamedSet(context.Background(), setOptionsJudge("yes", nil, &read), json.RawMessage(pendingBugsCard))
	if !strings.Contains(warned, "Name each of the four ready fixes") {
		t.Fatalf("warned %q; want it to say what to list", warned)
	}
	for _, option := range []string{"Tap all four ready fixes", "Tap them after the old-chat fixes land", "Hold all of them"} {
		if !strings.Contains(read, option) {
			t.Fatalf("the judge read %q, without the option %q", read, option)
		}
	}
	if refused, unchecked := cards.Check(context.Background(), setOptionsJudge("yes", nil, &read), json.RawMessage(pendingBugsCard)); refused != nil || unchecked != nil {
		t.Fatalf("Check = %v, %v; a set it does not name never refuses the card", refused, unchecked)
	}
}

func TestACardNamingItsWholeSetDrawsWithNoWarning(t *testing.T) {
	cards, _, _ := settledBoard(t, nil)
	var read string
	block := json.RawMessage(`{"id":"card-relay-fixes","type":"card","title":"Land the two relay fixes tonight?","summary":"[#50123](https://github.com/Forge-AI/monorepo/pull/50123) stops a dropped reply when a chat reconnects. [#50124](https://github.com/Forge-AI/monorepo/pull/50124) shows the retry button again.","children":[{"id":"pick","type":"choice","options":[{"id":"a","label":"Land both"},{"id":"b","label":"Hold both"}]}]}`)
	if warned := cards.UnnamedSet(context.Background(), setOptionsJudge("no", nil, &read), block); warned != "" || read == "" {
		t.Fatalf("warned %q after the judge read %q; want it judged and silent", warned, read)
	}
}

func TestACardDrawsSilentlyWhenTheJudgeOfItsSetIsDown(t *testing.T) {
	cards, _, _ := settledBoard(t, nil)
	var read string
	if warned := cards.UnnamedSet(context.Background(), setOptionsJudge("", errors.New("claude: overloaded"), &read), json.RawMessage(pendingBugsCard)); warned != "" || read == "" {
		t.Fatalf("warned %q after the judge read %q; want it asked and silent", warned, read)
	}
}
