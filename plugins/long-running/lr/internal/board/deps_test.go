package board

import (
	"context"
	"encoding/json"
	"fmt"
	"reflect"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ledger/ledgertest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/scaletest"
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
	mu           sync.Mutex
	board        map[string]map[string]any
	calls        []string
	removed      []string
	reasons      []string
	replaced     []string
	redrawn      []map[string]any
	refuse       string
	refuseRedraw string
}

func (f *depShell) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	argv := strings.Join(c.Argv, " ")
	f.calls = append(f.calls, argv)
	switch {
	case strings.Contains(argv, "ledger list --label register --label program:brook"):
		return shell.Result{Stdout: fmt.Sprintf(`[{"id":%q,"updated_at":"2026-11-03T08:00:00Z"}]`, registerID)}, nil
	case strings.Contains(argv, "ledger list --json"):
		return shell.Result{Stdout: "[]"}, nil
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
		f.reasons = append(f.reasons, c.Argv[slices.Index(c.Argv, "--reason")+1])
		if i := slices.Index(c.Argv, "--replaced-by"); i > 0 {
			f.replaced = append(f.replaced, c.Argv[i+1])
		}
		delete(f.board, c.Argv[2])
		return shell.Result{}, nil
	case strings.HasPrefix(argv, "cc-slack redraw-pr-cards --pr "):
		return shell.Result{Stdout: `{"redrawing":1}`}, nil
	case strings.Contains(argv, "update-block -"):
		if f.refuseRedraw != "" {
			return shell.Result{Code: 1}, &shell.ExitError{Argv: c.Argv, Code: 1, Stderr: f.refuseRedraw}
		}
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
		Lane: boardWatch,
		Cards: Cards{
			Runner: on, Ledger: f.ledger, Bus: "brook-cci", Checkout: "/co", Session: "s1", StateDir: t.TempDir(), Program: "brook", Store: s,
			Now: func() time.Time { return cardsNow },
			Withdraw: func(_ context.Context, lane, card, text string) error {
				return f.post(lane, posted{"withdraw", []string{"root"}, card, text})
			},
			Report: func(_ context.Context, lane string, to []string, topic, text string) error {
				return f.post(lane, posted{"report", to, topic, text})
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

func (f *depFixture) post(lane string, one posted) error {
	rootName := func(name string) bool { return name == "root" || name == "main" }
	if !slices.ContainsFunc(one.to, func(to string) bool { return to != lane && (!rootName(to) || !rootName(lane)) }) {
		return fmt.Errorf("lr: --to %s addresses only %s, the lane posting this record; name the lane that should read it", strings.Join(one.to, ","), lane)
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	f.posts = append(f.posts, one)
	return nil
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

func TestAPostgresRulingThatReadsAsDecidingTheLookupsCardTakesItsButtonsAndTellsTheLaneThatDrewIt(t *testing.T) {
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
	want := "superseded by your ruling: “move lookups to postgres”"
	if len(moved) != 1 || moved[0].How != "withdrew" || !reflect.DeepEqual(f.shell.removed, []string{lookupsCard}) || !reflect.DeepEqual(f.shell.reasons, []string{want}) || len(f.shell.replaced) != 0 {
		t.Fatalf("moved = %v, removed = %v with %q linking %v; want the card withdrawn naming his ruling", moved, f.shell.removed, f.shell.reasons, f.shell.replaced)
	}
	if !slices.ContainsFunc(f.posts, func(p posted) bool {
		return p.kind == "report" && reflect.DeepEqual(p.to, []string{"lane-a"}) && strings.Contains(p.text, "settles your card "+lookupsCard)
	}) {
		t.Fatalf("posts = %+v, want lane-a told its card is settled", f.posts)
	}
}

func TestAPickThatDecidesACardRestingOnItTakesThatCardsButtonsAndLinksThePick(t *testing.T) {
	const pickPost = "https://acme.slack.com/archives/D0B6Z/p1793599000000100"
	order := map[string]any{"id": "card-merge-order", "type": "card", "title": "Which lands first?", "slack": map[string]any{"url": pickPost}, "children": []any{
		map[string]any{"id": "card-merge-order-pick", "type": "choice", "prompt": "Which first?", "options": []any{map[string]any{"id": "index", "label": "the index change"}}},
	}}
	for name, tc := range map[string]struct {
		decided, refuse, how string
		removed              []string
	}{
		"a pick that decides it":            {decided: "yes", how: "withdrew", removed: []string{lookupsCard}},
		"a pick that leaves it open":        {decided: "no", how: "redrew"},
		"a card the board holds for him":    {decided: "yes", refuse: "refusing to remove block: the human's choice.selected holds it for 30 minutes", how: "held"},
		"a judge unsure whether it decides": {decided: "unsure", how: "flagged"},
	} {
		t.Run(name, func(t *testing.T) {
			f := newDeps(t, lookupsDoc, order)
			ctx := context.Background()
			if _, err := f.Cards.Depend(ctx, lookupsCard, "lane-a", []string{"card:card-merge-order"}); err != nil {
				t.Fatal(err)
			}
			f.shell.refuse = tc.refuse
			f.says(lookupsCard, premiseKind, "yes", "The index change lands first, so this merge waits on it.")
			f.says(lookupsCard, decidedKind, tc.decided, "his pick lands the index change first, which is what this card asks")

			moved, err := f.Pick(ctx, "card-merge-order", "picked “the index change”")
			if err != nil {
				t.Fatal(err)
			}

			if len(moved) != 1 || moved[0].How != tc.how || !slices.Equal(f.shell.removed, tc.removed) {
				t.Fatalf("moved = %v, removed = %v; want %s with %v removed", moved, f.shell.removed, tc.how, tc.removed)
			}
			if tc.how == "withdrew" && (!reflect.DeepEqual(f.shell.reasons, []string{"superseded by your answer on another card: picked “the index change”"}) || !reflect.DeepEqual(f.shell.replaced, []string{pickPost})) {
				t.Fatalf("reasons %q linking %v; want his pick named and its card linked", f.shell.reasons, f.shell.replaced)
			}
		})
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

func lookupsPR(t *testing.T, f *depFixture) store.ReviewRow {
	t.Helper()
	f.Cards.Repo = repo
	row := readyRow(func(r *store.ReviewRow) { r.Title = "lookups read the S3 index" })
	if err := f.Cards.Store.PutReviewRows([]store.ReviewRow{row}); err != nil {
		t.Fatal(err)
	}
	if _, err := f.Cards.Store.PutFact(repo, number, head, FactCardDrawn, "drawn", cardsNow); err != nil {
		t.Fatal(err)
	}
	return row
}

func TestARulingThatSupersedesAPRsShapeTakesItsTapOffAndItsPostReadsSuperseded(t *testing.T) {
	f := newDeps(t)
	row := lookupsPR(t, f)
	f.postgresRuling(t)
	f.says(tapCardID(row), premiseKind, "yes", "Lookups now read Postgres, so this S3 change no longer stands.")
	moved, err := f.Ruling(context.Background(), []string{postgresRule})
	if err != nil {
		t.Fatal(err)
	}
	if len(moved) != 1 || moved[0].How != "superseded" || moved[0].Card.Card != fmt.Sprintf("%s#%d", repo, number) {
		t.Fatalf("moved = %+v, want the PR's tap superseded", moved)
	}
	if !slices.ContainsFunc(f.shell.calls, func(call string) bool { return strings.HasSuffix(call, fmt.Sprintf("/pull/%d", number)) }) {
		t.Fatalf("calls = %v, want its approval post redrawn in this pass", f.shell.calls)
	}
	rows, err := f.Cards.Store.ReviewRows(repo)
	if err != nil || len(rows) != 1 {
		t.Fatalf("rows = %+v, %v", rows, err)
	}
	card := Card(repo, rows[0])
	if card.Title != "Superseded: #45583, lookups read the S3 index" || card.Options != nil || card.PRs[0].Blocks != "Superseded: move lookups to postgres." {
		t.Fatalf("card = %+v, want it to read superseded in his words with no buttons", card)
	}
	if !slices.ContainsFunc(f.posts, func(p posted) bool {
		return slices.Equal(p.to, []string{row.Lane}) && strings.Contains(p.text, "Rebuild it on the ruling")
	}) {
		t.Fatalf("posts = %+v, want the PR's lane told", f.posts)
	}
}

func TestARulingThatLeavesAPRsShapeKeepsItsTap(t *testing.T) {
	f := newDeps(t)
	row := lookupsPR(t, f)
	f.postgresRuling(t)
	f.says(tapCardID(row), premiseKind, "no", "the PR does not rest on where lookups read")
	moved, err := f.Ruling(context.Background(), []string{postgresRule})
	if err != nil || len(moved) != 0 {
		t.Fatalf("moved = %+v, %v", moved, err)
	}
	if rows, err := f.Cards.Store.ReviewRows(repo); err != nil || rows[0].State != "open" {
		t.Fatalf("rows = %+v, %v, want the tap kept", rows, err)
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

func TestARecheckWriteNamesItselfAndTakesTheCardOver(t *testing.T) {
	f := newDeps(t, lookupsDoc)
	ctx := context.Background()
	if _, err := f.Cards.Depend(ctx, lookupsCard, "lane-a", nil); err != nil {
		t.Fatal(err)
	}
	f.postgresRuling(t)
	f.says(lookupsCard, premiseKind, "yes", "Lookups now read Postgres.")
	f.says(lookupsCard, decidedKind, "no", "whether to merge is open")
	if _, err := f.Ruling(ctx, []string{postgresRule}); err != nil {
		t.Fatal(err)
	}
	i := slices.IndexFunc(f.shell.calls, func(call string) bool { return strings.Contains(call, "update-block") })
	if i < 0 || !strings.Contains(f.shell.calls[i], "--lane board-watch --take-over") {
		t.Fatalf("calls = %v, want --lane board-watch --take-over", f.shell.calls)
	}
}

func TestARulingRecheckReachesTheRootThatDrewACardAndWithdrawsItsTapToRoot(t *testing.T) {
	f := newDeps(t, lookupsDoc)
	ctx := context.Background()
	if _, err := f.Cards.Depend(ctx, lookupsCard, "root", nil); err != nil {
		t.Fatal(err)
	}
	row := lookupsPR(t, f)
	f.postgresRuling(t)
	f.says(lookupsCard, premiseKind, "unsure", "it is unclear whether the merge still reads the index")
	f.says(tapCardID(row), premiseKind, "yes", "Lookups now read Postgres, so this S3 change no longer stands.")
	if _, err := f.Ruling(ctx, []string{postgresRule}); err != nil {
		t.Fatal(err)
	}
	if !slices.ContainsFunc(f.posts, func(p posted) bool {
		return p.kind == "report" && p.topic == lookupsCard && slices.Equal(p.to, []string{"root"})
	}) {
		t.Fatalf("posts = %+v, want root told of the card it drew", f.posts)
	}
	if !slices.ContainsFunc(f.posts, func(p posted) bool { return p.kind == "withdraw" && slices.Equal(p.to, []string{"root"}) }) {
		t.Fatalf("posts = %+v, want the tap's withdraw posted to root", f.posts)
	}
}

func TestARefusedRedrawTellsTheLaneTheSentenceItWanted(t *testing.T) {
	f := newDeps(t, lookupsDoc)
	ctx := context.Background()
	if _, err := f.Cards.Depend(ctx, lookupsCard, "lane-a", nil); err != nil {
		t.Fatal(err)
	}
	f.postgresRuling(t)
	f.shell.refuseRedraw = "refusing to write card-48879: stale rev"
	f.says(lookupsCard, premiseKind, "yes", "Lookups now read Postgres.")
	f.says(lookupsCard, decidedKind, "no", "whether to merge is still open")
	moved, err := f.Ruling(ctx, []string{postgresRule})
	if err != nil {
		t.Fatal(err)
	}
	if len(moved) != 1 || moved[0].How != "held" {
		t.Fatalf("moved = %v", moved)
	}
	flag := f.posts[0]
	if !strings.Contains(flag.text, `redraw it to state "Lookups now read Postgres."`) || !strings.Contains(flag.text, "stale rev") || strings.Contains(flag.text, "could not settle") {
		t.Fatalf("flag = %+v", flag)
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
	if none, one, many := reads(0), reads(1), reads(scaletest.N()); none != 0 || one != 1 || many != 1 {
		t.Fatalf("board reads: %d with no resting card, %d at 1, %d at %d", none, one, many, scaletest.N())
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
