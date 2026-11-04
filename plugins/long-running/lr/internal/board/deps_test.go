package board

import (
	"context"
	"encoding/json"
	"fmt"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ledger/ledgertest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	registerID   = "reg1"
	postgresRule = "prompt:1103-postgres"
	lookupsCard  = "card-48879"
)

var lookupsDoc = map[string]any{
	"id": lookupsCard, "type": "card", "title": "Merge #48879 so lookups keep reading the S3 index?",
	"children": []any{
		map[string]any{"id": lookupsCard + "-pr", "type": "pr", "repo": "Forge-AI/monorepo", "number": 48879},
		map[string]any{"id": lookupsCard + "-pick", "type": "choice", "prompt": "Merge it?", "options": []any{map[string]any{"id": "yes", "label": "Merge"}}},
	},
}

type depShell struct {
	mu      sync.Mutex
	board   map[string]map[string]any
	calls   []string
	removed []string
	redrawn []map[string]any
	refuse  string
}

func (f *depShell) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	argv := strings.Join(c.Argv, " ")
	f.calls = append(f.calls, argv)
	switch {
	case strings.Contains(argv, "ledger list --label register --label program:brook"):
		return shell.Result{Stdout: fmt.Sprintf(`[{"id":%q,"updated_at":"2026-11-03T08:00:00Z"}]`, registerID)}, nil
	case strings.Contains(argv, "ledger add"):
		return shell.Result{Stdout: `{"id":"c4rd5"}`}, nil
	case strings.Contains(argv, "outcomes --block"):
		card, onBoard := f.board[c.Argv[3]]
		var blocks []any
		if onBoard {
			blocks = []any{card}
		}
		encoded, err := json.Marshal(map[string]any{"onBoard": onBoard, "doc": map[string]any{"blocks": blocks}, "answers": []any{}})
		return shell.Result{Stdout: string(encoded)}, err
	case strings.Contains(argv, "outcomes --all"):
		blocks := make([]any, 0, len(f.board))
		for _, card := range f.board {
			blocks = append(blocks, card)
		}
		encoded, err := json.Marshal(map[string]any{"doc": map[string]any{"blocks": blocks}})
		return shell.Result{Stdout: string(encoded)}, err
	case strings.Contains(argv, "remove-block"):
		if f.refuse != "" {
			return shell.Result{Code: 1}, &shell.ExitError{Argv: c.Argv, Code: 1, Stderr: f.refuse}
		}
		f.removed = append(f.removed, c.Argv[2])
		delete(f.board, c.Argv[2])
		return shell.Result{}, nil
	case strings.Contains(argv, "update-block -"):
		var card map[string]any
		if err := json.Unmarshal([]byte(c.Stdin), &card); err != nil {
			return shell.Result{}, err
		}
		f.redrawn = append(f.redrawn, card)
		return shell.Result{}, nil
	}
	return shell.Result{}, fmt.Errorf("unexpected call %s", argv)
}

type posted struct {
	kind  string
	to    []string
	topic string
	text  string
}

type depFixture struct {
	Recheck
	shell   *depShell
	ledger  *ledgertest.Fake
	mu      sync.Mutex
	judged  []string
	posts   []posted
	answers map[string]map[string]*Judgment
}

func newDeps(t *testing.T, cards ...map[string]any) *depFixture {
	t.Helper()
	s, err := store.Memory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	on := &depShell{board: map[string]map[string]any{}}
	for _, card := range cards {
		on.board[card["id"].(string)] = card
	}
	f := &depFixture{shell: on, ledger: &ledgertest.Fake{}, answers: map[string]map[string]*Judgment{}}
	f.Recheck = Recheck{
		Lane: "root",
		Cards: Cards{
			Runner: on, Ledger: f.ledger, Bus: "brook-cci", Checkout: "/co", Session: "s1", StateDir: t.TempDir(), Program: "brook", Store: s,
			Now: func() time.Time { return cardsNow },
			Withdraw: func(_ context.Context, _, card, text string) error {
				f.post(posted{"withdraw", []string{"root"}, card, text})
				return nil
			},
			Report: func(_ context.Context, _ string, to []string, topic, text string) error {
				f.post(posted{"report", to, topic, text})
				return nil
			},
		},
		Judge: func(_ context.Context, kind, _, artifact string) (*Judgment, error) {
			var card struct {
				ID string `json:"id"`
			}
			if err := json.Unmarshal([]byte(artifact), &card); err != nil {
				return nil, err
			}
			f.mu.Lock()
			defer f.mu.Unlock()
			f.judged = append(f.judged, kind+" "+card.ID)
			return f.answers[card.ID][kind], nil
		},
	}
	f.rule(t, "rule/lookups-s3", map[string]string{"kind": "design", "status": "in-force", "words": "lookups read the S3 index", "names": `["lookups"]`})
	return f
}

func (f *depFixture) post(one posted) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.posts = append(f.posts, one)
}

func (f *depFixture) rule(t *testing.T, key string, fields map[string]string) {
	t.Helper()
	if err := f.ledger.SetRow(context.Background(), registerID, key, fields); err != nil {
		t.Fatal(err)
	}
}

func (f *depFixture) says(card, kind, verdict, sentence string) {
	if f.answers[card] == nil {
		f.answers[card] = map[string]*Judgment{}
	}
	f.answers[card][kind] = &Judgment{Verdict: verdict, Sentence: sentence}
}

func (f *depFixture) postgresRuling(t *testing.T) {
	t.Helper()
	f.rule(t, postgresRule, map[string]string{"kind": "design", "status": "in-force", "words": "move lookups to postgres", "names": `["lookups", "Postgres"]`})
}

func TestDependRecordsThePRsAndRegisterNamesACardRestsOn(t *testing.T) {
	f := newDeps(t, lookupsDoc)
	deps, err := f.Cards.Depend(context.Background(), lookupsCard, "lane-a", []string{"card:card-merge-order"})
	if err != nil {
		t.Fatal(err)
	}
	if want := []string{"#48879", "card:card-merge-order", "lookups"}; !reflect.DeepEqual(deps, want) {
		t.Fatalf("deps = %v", deps)
	}
	refs, err := f.Cards.Store.CardsDependingOn([]string{"#48879"})
	if err != nil || !reflect.DeepEqual(refs, []store.CardRef{{Session: "s1", Card: lookupsCard, Lane: "lane-a"}}) {
		t.Fatalf("refs = %v, %v", refs, err)
	}
}

func TestAPostgresRulingWithdrawsTheLookupsCardItDecidesAndTellsTheCommunicator(t *testing.T) {
	f := newDeps(t, lookupsDoc)
	ctx := context.Background()
	if _, err := f.Cards.Depend(ctx, lookupsCard, "lane-a", nil); err != nil {
		t.Fatal(err)
	}
	f.postgresRuling(t)
	f.says(lookupsCard, premiseKind, "yes", "Lookups now read Postgres, not the S3 index.")
	f.says(lookupsCard, decidedKind, "yes", "his ruling moves lookups to Postgres, so the S3 merge is moot")
	moved, err := f.Ruling(ctx, []string{postgresRule})
	if err != nil {
		t.Fatal(err)
	}
	if len(moved) != 1 || moved[0].How != "withdrew" || !reflect.DeepEqual(f.shell.removed, []string{lookupsCard}) {
		t.Fatalf("moved = %v, removed = %v", moved, f.shell.removed)
	}
	report := f.posts[len(f.posts)-1]
	if report.kind != "report" || !reflect.DeepEqual(report.to, []string{communicator}) || !strings.Contains(report.text, "withdrew card-48879, because his ruling moves lookups to Postgres") {
		t.Fatalf("report = %+v", report)
	}
	if refs, err := f.Cards.Store.CardsDependingOn([]string{"lookups"}); err != nil || len(refs) != 0 {
		t.Fatalf("a withdrawn card still depends: %v, %v", refs, err)
	}
}

func TestARulingRedrawsARenderCardItWouldHaveDecided(t *testing.T) {
	render := map[string]any{"id": lookupsCard, "type": "card", "title": lookupsDoc["title"], "children": append([]any{map[string]any{"id": "shot", "type": "image", "src": "asset:6184"}}, lookupsDoc["children"].([]any)...)}
	f := newDeps(t, render)
	ctx := context.Background()
	if _, err := f.Cards.Depend(ctx, lookupsCard, "lane-a", nil); err != nil {
		t.Fatal(err)
	}
	f.postgresRuling(t)
	f.says(lookupsCard, premiseKind, "yes", "Lookups now read Postgres.")
	f.says(lookupsCard, decidedKind, "yes", "his ruling moves lookups to Postgres, so the S3 merge is moot")
	moved, err := f.Ruling(ctx, []string{postgresRule})
	if err != nil {
		t.Fatal(err)
	}
	if len(moved) != 1 || moved[0].How != "redrew" || len(f.shell.removed) != 0 {
		t.Fatalf("moved = %v, removed = %v; his ruling elsewhere is no yes to a render he has not seen", moved, f.shell.removed)
	}
}

func TestAPostgresRulingRedrawsTheLookupsCardOnItsNewPremiseKeepingItsQuestion(t *testing.T) {
	f := newDeps(t, lookupsDoc)
	ctx := context.Background()
	if _, err := f.Cards.Depend(ctx, lookupsCard, "lane-a", nil); err != nil {
		t.Fatal(err)
	}
	f.postgresRuling(t)
	f.says(lookupsCard, premiseKind, "yes", "Lookups now read Postgres, so the merge only drops the S3 index.")
	f.says(lookupsCard, decidedKind, "no", "whether to merge is still open")
	moved, err := f.Ruling(ctx, []string{postgresRule})
	if err != nil {
		t.Fatal(err)
	}
	if len(moved) != 1 || moved[0].How != "redrew" || len(f.shell.removed) != 0 || len(f.shell.redrawn) != 1 {
		t.Fatalf("moved = %v, removed = %v, redrawn = %d", moved, f.shell.removed, len(f.shell.redrawn))
	}
	children := f.shell.redrawn[0]["children"].([]any)
	first := children[0].(map[string]any)
	if first["id"] != lookupsCard+"-premise" || !strings.Contains(first["md"].(string), "Lookups now read Postgres") {
		t.Fatalf("premise = %v", first)
	}
	if len(children) != 3 || children[2].(map[string]any)["id"] != lookupsCard+"-pick" {
		t.Fatalf("the redraw dropped the question: %v", children)
	}
	if report := f.posts[len(f.posts)-1]; !strings.Contains(report.text, "redrew card-48879 on the new premise") {
		t.Fatalf("report = %+v", report)
	}
}

func TestARulingOnAnUnrelatedObjectTouchesNoCard(t *testing.T) {
	f := newDeps(t, lookupsDoc)
	ctx := context.Background()
	if _, err := f.Cards.Depend(ctx, lookupsCard, "lane-a", nil); err != nil {
		t.Fatal(err)
	}
	f.rule(t, "prompt:1103-billing", map[string]string{"kind": "order", "status": "in-force", "words": "bill per seat", "names": `["billing"]`})
	before := len(f.shell.calls)
	moved, err := f.Ruling(ctx, []string{"prompt:1103-billing"})
	if err != nil {
		t.Fatal(err)
	}
	if len(moved) != 0 || len(f.judged) != 0 || len(f.posts) != 0 || len(f.shell.removed)+len(f.shell.redrawn) != 0 {
		t.Fatalf("moved = %v, judged = %v, posts = %v", moved, f.judged, f.posts)
	}
	if calls := f.shell.calls[before:]; len(calls) != 1 {
		t.Fatalf("an unrelated ruling read %v", calls)
	}
}

func TestAnExtendedRulingIsJudgedOnTheWordsJustAddedAndNeverItsOldOnes(t *testing.T) {
	const oldWords, addedWords = "drop the old sync PR, #42605", "ship #48879 once it is green"
	closed := map[string]any{"id": "card-closed", "type": "card", "title": "Keep the old sync PR?", "children": []any{}}
	f := newDeps(t, lookupsDoc, closed)
	ctx := context.Background()
	if _, err := f.Cards.Depend(ctx, lookupsCard, "lane-a", nil); err != nil {
		t.Fatal(err)
	}
	if err := f.Cards.Store.PutCardDeps(store.CardRef{Session: "s1", Card: "card-closed", Lane: "lane-b"}, []string{"#42605"}); err != nil {
		t.Fatal(err)
	}
	repeats, err := json.Marshal([]map[string]string{{"key": "prompt:1", "at": "Oct 24", "words": "an earlier addition"}, {"key": "prompt:2", "at": "Nov 4", "words": addedWords}})
	if err != nil {
		t.Fatal(err)
	}
	f.rule(t, "rule/old", map[string]string{"kind": "order", "status": "in-force", "words": oldWords, "repeats": string(repeats)})
	judge := f.Judge
	var shown []string
	f.Judge = func(ctx context.Context, kind, evidence, artifact string) (*Judgment, error) {
		f.mu.Lock()
		shown = append(shown, evidence)
		f.mu.Unlock()
		return judge(ctx, kind, evidence, artifact)
	}
	f.says(lookupsCard, premiseKind, "no", "")
	moved, err := f.Ruling(ctx, []string{"rule/old"})
	if err != nil {
		t.Fatal(err)
	}
	if len(moved) != 0 || len(f.posts) != 0 {
		t.Fatalf("moved = %v, posts = %v", moved, f.posts)
	}
	if !reflect.DeepEqual(f.judged, []string{premiseKind + " " + lookupsCard}) {
		t.Fatalf("judged = %v, want only the card resting on the PR his added words cite", f.judged)
	}
	if len(shown) != 1 || !strings.Contains(shown[0], addedWords) || strings.Contains(shown[0], "old sync") || strings.Contains(shown[0], "earlier addition") {
		t.Fatalf("evidence = %q, want his added words and none of the older ones", shown)
	}
}

func TestAnUnsureJudgeKeepsTheCardAndFlagsItToItsLane(t *testing.T) {
	f := newDeps(t, lookupsDoc)
	ctx := context.Background()
	if _, err := f.Cards.Depend(ctx, lookupsCard, "lane-a", nil); err != nil {
		t.Fatal(err)
	}
	f.postgresRuling(t)
	f.says(lookupsCard, premiseKind, "unsure", "it is unclear whether the merge still reads the index")
	moved, err := f.Ruling(ctx, []string{postgresRule})
	if err != nil {
		t.Fatal(err)
	}
	if len(moved) != 1 || moved[0].How != "flagged" || len(f.shell.removed)+len(f.shell.redrawn) != 0 {
		t.Fatalf("moved = %v, removed = %v, redrawn = %v", moved, f.shell.removed, f.shell.redrawn)
	}
	if flag := f.posts[0]; !reflect.DeepEqual(flag.to, []string{"lane-a"}) || flag.topic != lookupsCard || !strings.Contains(flag.text, "stays on the owner's board") {
		t.Fatalf("flag = %+v", flag)
	}
	if report := f.posts[1]; !strings.Contains(report.text, "kept card-48879 and flagged it to lane-a") {
		t.Fatalf("report = %+v", report)
	}
}

func TestARefusedWithdrawKeepsTheCardAndFlagsIt(t *testing.T) {
	f := newDeps(t, lookupsDoc)
	ctx := context.Background()
	if _, err := f.Cards.Depend(ctx, lookupsCard, "lane-a", nil); err != nil {
		t.Fatal(err)
	}
	f.postgresRuling(t)
	f.shell.refuse = "refusing to remove card-48879: the owner touched it 4 minutes ago"
	f.says(lookupsCard, premiseKind, "yes", "Lookups now read Postgres.")
	f.says(lookupsCard, decidedKind, "yes", "his ruling decides it")
	moved, err := f.Ruling(ctx, []string{postgresRule})
	if err != nil {
		t.Fatal(err)
	}
	if len(moved) != 1 || moved[0].How != "flagged" || !strings.Contains(moved[0].Why, "the owner touched it") {
		t.Fatalf("moved = %v", moved)
	}
}

func TestAPickRechecksTheCardsRestingOnTheCardItAnswersAndNotTheCardItself(t *testing.T) {
	order := map[string]any{"id": "card-merge-order", "type": "card", "title": "Which lands first?", "children": []any{}}
	f := newDeps(t, lookupsDoc, order)
	ctx := context.Background()
	if _, err := f.Cards.Depend(ctx, lookupsCard, "lane-a", []string{"card:card-merge-order"}); err != nil {
		t.Fatal(err)
	}
	if _, err := f.Cards.Depend(ctx, "card-merge-order", "lane-b", nil); err != nil {
		t.Fatal(err)
	}
	f.says(lookupsCard, premiseKind, "no", "")
	if _, err := f.Pick(ctx, "card-merge-order", "picked the index change first"); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(f.judged, []string{premiseKind + " " + lookupsCard}) {
		t.Fatalf("judged = %v", f.judged)
	}
}

func TestRecheckReadsAreFlatAtOneAndAHundredOpenCards(t *testing.T) {
	reads := func(open int) (int, int) {
		cards := []map[string]any{lookupsDoc}
		f := newDeps(t, cards...)
		for i := range open {
			if err := f.Cards.Store.PutCardDeps(store.CardRef{Session: "s1", Card: fmt.Sprintf("card-%d", i), Lane: "lane-a"}, []string{fmt.Sprintf("#%d", 1000+i), fmt.Sprintf("object-%d", i)}); err != nil {
				t.Fatal(err)
			}
		}
		if err := f.Cards.Store.PutCardDeps(store.CardRef{Session: "s1", Card: lookupsCard, Lane: "lane-a"}, []string{"#48879", "lookups"}); err != nil {
			t.Fatal(err)
		}
		f.postgresRuling(t)
		f.says(lookupsCard, premiseKind, "unsure", "unclear")
		if _, err := f.Ruling(context.Background(), []string{postgresRule}); err != nil {
			t.Fatal(err)
		}
		return len(f.shell.calls), len(f.judged)
	}
	oneCalls, oneJudged := reads(1)
	manyCalls, manyJudged := reads(100)
	if oneCalls != manyCalls || oneJudged != manyJudged {
		t.Fatalf("reads grew with open cards: %d calls and %d judge calls at 1, %d and %d at 100", oneCalls, oneJudged, manyCalls, manyJudged)
	}
}

func TestAPickReadsTheBoardOnceHoweverManyCardsRestOnTheCard(t *testing.T) {
	reads := func(resting int) int {
		f := newDeps(t, map[string]any{"id": "card-merge-order", "type": "card", "title": "Which lands first?", "children": []any{}})
		for i := range resting {
			card := fmt.Sprintf("card-%d", i)
			f.shell.board[card] = map[string]any{"id": card, "type": "card", "title": card, "children": []any{}}
			if err := f.Cards.Store.PutCardDeps(store.CardRef{Session: "s1", Card: card, Lane: "lane-a"}, []string{cardKey("card-merge-order")}); err != nil {
				t.Fatal(err)
			}
			f.says(card, premiseKind, "no", "")
		}
		if _, err := f.Pick(context.Background(), "card-merge-order", "picked the index change first"); err != nil {
			t.Fatal(err)
		}
		if len(f.judged) != resting {
			t.Fatalf("judged %d of %d resting cards", len(f.judged), resting)
		}
		outcomes := 0
		for _, call := range f.shell.calls {
			if strings.Contains(call, "cc-present outcomes") {
				outcomes++
			}
		}
		return outcomes
	}
	if none, one, many := reads(0), reads(1), reads(1000); none != 0 || one != 1 || many != 1 {
		t.Fatalf("board reads: %d with no resting card, %d at 1, %d at 1,000", none, one, many)
	}
}

func TestAPickShowsTheJudgeTheQuestionItAnswered(t *testing.T) {
	const title, prompt, said = "What should a red Watch do to a rollout?", "Pick the Watch's job", "picked \u201cstay on telemetry only\u201d"
	answered := map[string]any{"id": "card-watch", "type": "card", "title": title, "children": []any{
		map[string]any{"id": "card-watch-pick", "type": "choice", "prompt": prompt, "options": []any{map[string]any{"id": "telemetry", "label": "stay on telemetry only"}}},
	}}
	related := map[string]any{"id": "card-next", "type": "card", "title": "Accept a Watch rollback's cost on next?", "children": []any{}}
	unrelated := map[string]any{"id": "card-billing", "type": "card", "title": "Bill per seat?", "children": []any{}}
	f := newDeps(t, answered, related, unrelated)
	for _, card := range []string{"card-next", "card-billing"} {
		if err := f.Cards.Store.PutCardDeps(store.CardRef{Session: "s1", Card: card, Lane: "lane-a"}, []string{cardKey("card-watch")}); err != nil {
			t.Fatal(err)
		}
	}
	var seen sync.Map
	f.Judge = func(_ context.Context, kind, evidence, artifact string) (*Judgment, error) {
		seen.Store(kind, evidence)
		if !strings.Contains(evidence, title) || !strings.Contains(evidence, prompt) {
			return &Judgment{Verdict: "unsure", Sentence: "the question he answered isn't shown"}, nil
		}
		if !strings.Contains(artifact, "Watch") {
			return &Judgment{Verdict: "no"}, nil
		}
		if kind == decidedKind {
			return &Judgment{Verdict: "no", Sentence: "whether to accept the cost is still open"}, nil
		}
		return &Judgment{Verdict: "yes", Sentence: "A red Watch no longer rolls back."}, nil
	}
	moved, err := f.Pick(context.Background(), "card-watch", said)
	if err != nil {
		t.Fatal(err)
	}
	if len(moved) != 1 || moved[0].Card.Card != "card-next" || moved[0].How != "redrew" {
		t.Fatalf("moved = %v, want only card-next redrawn and card-billing left alone", moved)
	}
	evidence, _ := seen.Load(premiseKind)
	if !strings.Contains(evidence.(string), said) {
		t.Fatalf("evidence = %v, want his pick beside its question", evidence)
	}
	for _, one := range f.posts {
		if strings.Contains(one.text, "could not settle") {
			t.Fatalf("a judge shown the question still flagged: %+v", one)
		}
	}
}

func TestAPickOnACardOffTheBoardSaysSoInsteadOfJudgingWithoutItsQuestion(t *testing.T) {
	f := newDeps(t, lookupsDoc)
	if err := f.Cards.Store.PutCardDeps(store.CardRef{Session: "s1", Card: lookupsCard, Lane: "lane-a"}, []string{cardKey("card-gone")}); err != nil {
		t.Fatal(err)
	}
	if _, err := f.Pick(context.Background(), "card-gone", "picked it"); err == nil || !strings.Contains(err.Error(), "card-gone is not on board s1") {
		t.Fatalf("err = %v", err)
	}
	if len(f.judged) != 0 {
		t.Fatalf("judged %v without the question", f.judged)
	}
}
