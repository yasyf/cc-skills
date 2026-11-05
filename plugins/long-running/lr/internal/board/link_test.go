package board

import (
	"cmp"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ledger/ledgertest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	linkDrive = "d"
	busStart  = 1
	asker     = "ci-speed"
	dmURL     = "https://slack.test/p1"
)

type linkShell struct {
	mu       sync.Mutex
	drawn    map[string]string
	answered map[string]bool
	calls    []string
	refuse   string
	missing  bool

	redrawing int
}

func (f *linkShell) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	argv := strings.Join(c.Argv, " ")
	if f.missing {
		return shell.Result{}, errors.New("cc-present: not found")
	}
	switch {
	case strings.Contains(argv, "redraw-pr-cards"):
		f.calls = append(f.calls, argv)
		return shell.Result{Stdout: fmt.Sprintf(`{"redrawing":%d}`, f.redrawing)}, nil
	case strings.Contains(argv, "ledger add"):
		return shell.Result{Stdout: `{"id":"c4rd5"}`}, nil
	case strings.Contains(argv, "ledger list"), strings.Contains(argv, "answer list"):
		return shell.Result{Stdout: "[]"}, nil
	case strings.Contains(argv, "outcomes --all"):
		blocks := make([]json.RawMessage, 0, len(f.drawn))
		for _, block := range f.drawn {
			blocks = append(blocks, json.RawMessage(block))
		}
		encoded, err := json.Marshal(map[string]any{"doc": map[string]any{"blocks": blocks}})
		return shell.Result{Stdout: string(encoded)}, err
	case strings.Contains(argv, "outcomes --block"):
		id := c.Argv[3]
		if f.answered[id] {
			return shell.Result{Stdout: fmt.Sprintf(`{"answers":[{"origin":"human","payload":{"blockId":"%s-choice","optionIds":["x"]}}]}`, id)}, nil
		}
		block, onBoard := f.drawn[id]
		if !onBoard {
			return shell.Result{}, &shell.ExitError{Argv: c.Argv, Code: 1, Stderr: fmt.Sprintf("cc-present: block %q was never on this board", id)}
		}
		return shell.Result{Stdout: fmt.Sprintf(`{"answers":[],"onBoard":true,"doc":{"blocks":[%s]}}`, block)}, nil
	}
	f.calls = append(f.calls, argv+" <"+c.Stdin)
	if f.refuse != "" {
		refused := f.refuse
		f.refuse = ""
		return shell.Result{}, &shell.ExitError{Argv: c.Argv, Code: 1, Stderr: refused}
	}
	if c.Argv[0] == "cc-slack" {
		return shell.Result{}, nil
	}
	if c.Argv[1] == "remove-block" {
		delete(f.drawn, c.Argv[2])
		return shell.Result{}, nil
	}
	var top struct {
		ID string `json:"id"`
	}
	if err := json.Unmarshal([]byte(c.Stdin), &top); err != nil {
		return shell.Result{}, err
	}
	if f.drawn == nil {
		f.drawn = map[string]string{}
	}
	f.drawn[top.ID] = c.Stdin
	if c.Argv[1] == "ask" {
		return shell.Result{Stdout: "slack: " + dmURL + "\nrev: 7\n"}, nil
	}
	return shell.Result{}, nil
}

func (f *linkShell) seen() []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	return slices.Clone(f.calls)
}

func judgedNo(_ context.Context, _, _, _ string) (*Judgment, error) {
	return &Judgment{Verdict: "no"}, nil
}

type linkFixture struct {
	Cards Cards
	shell *linkShell
	state string
}

func newLink(t *testing.T) *linkFixture {
	t.Helper()
	s, err := store.Memory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	on := &linkShell{}
	state := t.TempDir()
	return &linkFixture{Cards: Cards{
		Runner: on, Ledger: &ledgertest.Fake{}, Bus: linkDrive, Checkout: "/co", StateDir: state, Program: "brook", Store: s,
		Now:      func() time.Time { return cardsNow },
		Withdraw: func(context.Context, string, string, string) error { return nil },
		Report:   func(context.Context, string, []string, string, string) error { return nil },
	}, shell: on, state: state}
}

func (f *linkFixture) link() Link {
	return Link{Drive: linkDrive, State: f.state, Judge: judgedNo, Cards: func(session string) (Cards, func(), error) {
		cards := f.Cards
		cards.Session = cmp.Or(session, "board-1")
		return cards, func() {}, nil
	}}
}

func (f *linkFixture) run(t *testing.T) {
	t.Helper()
	records, err := client.Tail(context.Background(), client.Query{Drive: linkDrive, Kinds: []string{cardKind}})
	if err != nil {
		t.Fatal(err)
	}
	if len(records) == 0 {
		return
	}
	last := strconv.FormatInt(records[len(records)-1].Seq, 10)
	ctx, stop := context.WithCancel(context.Background())
	defer stop()
	served := make(chan error, 1)
	go func() {
		served <- f.link().Serve(ctx)
	}()
	deadline := time.Now().Add(30 * time.Second)
	for {
		raw, _ := os.ReadFile(filepath.Join(f.state, cursorFile))
		if string(raw) == last {
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("the cursor stayed at %q, never reaching card %s", raw, last)
		}
		time.Sleep(20 * time.Millisecond)
	}
	stop()
	if err := <-served; err != nil && !errors.Is(err, context.Canceled) {
		t.Fatalf("Serve = %v", err)
	}
}

func liveDrive(t *testing.T) {
	t.Helper()
	t.Setenv("CCI_HOME", t.TempDir())
	if _, err := client.Post(context.Background(), client.Draft{Drive: linkDrive, Lane: "root", Kind: "note", Text: "a drive has records before its first card"}); err != nil {
		t.Fatal(err)
	}
}

func draw(t *testing.T, key, block string) {
	t.Helper()
	if _, err := DrawCard(context.Background(), linkDrive, "pr-desk", key, cardBody{Block: json.RawMessage(block)}); err != nil {
		t.Fatalf("DrawCard %s: %v", key, err)
	}
}

func askCard(t *testing.T, lane string) client.Record {
	t.Helper()
	block, err := askedCard(strings.NewReader(""), Ask{
		Title: "Which number do you accept for the test job?", Background: "It takes 100 s now and a warm change waits 3 min 45 s.",
		Options: []string{"Accept it :: The bar moves to what is measured.", "Keep 3 minutes :: A shared compile cache, landed by Friday."}, NothingToBuild: "a choice of target",
	}, "", "yasyf/cc-skills")
	if err != nil {
		t.Fatal(err)
	}
	record, err := DrawCard(context.Background(), linkDrive, lane, "ask", cardBody{Block: block, Until: "at:3d"})
	if err != nil {
		t.Fatal(err)
	}
	return record
}

func addressedTo(t *testing.T, kind, lane string) []client.Record {
	t.Helper()
	records, err := client.Tail(context.Background(), client.Query{Drive: linkDrive, Kinds: []string{kind}})
	if err != nil {
		t.Fatal(err)
	}
	return slices.DeleteFunc(records, func(record client.Record) bool { return !slices.Contains(record.To, lane) })
}

const askedID = "card-which-number-do-you-accept-for"

func TestALanesCallDrawsOneCardOnTheMainBoardWithTheLaneAsOwner(t *testing.T) {
	liveDrive(t)
	f := newLink(t)
	askCard(t, asker)
	askCard(t, asker)
	f.run(t)

	calls := f.shell.seen()
	if len(calls) != 1 || !strings.HasPrefix(calls[0], "cc-present ask - --session board-1 --lane "+asker+" <") {
		t.Fatalf("calls %q, want one ask on the main board naming the lane", calls)
	}
	_, rows, err := f.Cards.rows(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if rows[askedID][laneField] != asker {
		t.Fatalf("card row %v, want the asking lane as its owner", rows[askedID])
	}
	said, err := awaitLink(context.Background(), linkDrive, asker, "ask", busStart)
	if err != nil || !strings.Contains(said, dmURL) || !strings.Contains(said, "his answer wakes "+asker) {
		t.Fatalf("awaitLink = %q, %v; want the DM link and who his answer wakes", said, err)
	}
}

func TestHisAnswerWakesTheLaneThatDrewTheCard(t *testing.T) {
	liveDrive(t)
	f := newLink(t)
	askCard(t, asker)
	f.run(t)

	cards := f.Cards
	cards.Session = "board-1"
	waker := Waker{Cards: cards, Lane: "board-watch", Tail: client.Tail, Post: client.Post, Relay: func(context.Context, string, string) error { return nil }, Warn: io.Discard}
	if err := waker.Wake(context.Background(), CardAnswer{Card: askedID, Block: askedID + "-choice", Said: "Keep 3 minutes", Key: "m1", Unclaimed: true}); err != nil {
		t.Fatal(err)
	}
	wakes := addressedTo(t, wakeKind, asker)
	if len(wakes) != 1 || !strings.Contains(wakes[0].Text, "Keep 3 minutes") {
		t.Fatalf("wakes %+v, want one to %s carrying his answer", wakes, asker)
	}
}

func TestABrandNewCardDrawsThoughTheBoardHasNeverSeenItsID(t *testing.T) {
	liveDrive(t)
	f := newLink(t)
	askCard(t, asker)
	f.run(t)

	if defects := addressedTo(t, linkRefusalKind, asker); len(defects) != 0 {
		t.Fatalf("a new card was refused: %+v", defects)
	}
	if calls := f.shell.seen(); len(calls) != 1 {
		t.Fatalf("calls %q, want the new card drawn", calls)
	}
}

func TestOneAskGetsOneCardOwnedByTheLaneThatAsked(t *testing.T) {
	liveDrive(t)
	f := newLink(t)
	ctx := context.Background()
	ask, err := client.Post(ctx, client.Draft{Drive: linkDrive, Lane: asker, Kind: "ask", To: []string{"board-refresher"}, Text: "CARD: card-counts: How should run counts work?"})
	if err != nil {
		t.Fatal(err)
	}
	card := func(id, title string) json.RawMessage {
		return json.RawMessage(fmt.Sprintf(`{"id":%q,"type":"card","title":%q,"summary":"Counts rescan history.","children":[{"id":"pick","type":"choice","options":[{"id":"a","label":"A"}]}]}`, id, title))
	}
	if _, err := DrawCard(ctx, linkDrive, "owner-relay", "first", cardBody{Block: card("card-counts", "How should run counts work?"), Until: "at:3d", Ask: ask.Seq}); err != nil {
		t.Fatal(err)
	}
	f.run(t)
	for key, drawn := range map[string]json.RawMessage{"edit": card("card-counts", "How should run counts work now?"), "second": card("card-other", "What do you want for run counts?")} {
		if _, err := DrawCard(ctx, linkDrive, "board-refresher", key, cardBody{Block: drawn, Until: "at:3d", Ask: ask.Seq}); err != nil {
			t.Fatal(err)
		}
	}
	f.run(t)

	calls := f.shell.seen()
	if len(calls) != 2 || !strings.Contains(calls[0], "--lane "+asker) || !strings.Contains(calls[1], "--lane "+asker) || !strings.Contains(calls[1], "work now?") {
		t.Fatalf("calls %q, want the card and its edit, both owned by the asking lane", calls)
	}
	_, err = awaitLink(ctx, linkDrive, "board-refresher", "second", busStart)
	if err == nil || !strings.Contains(err.Error(), "already has card-counts") {
		t.Fatalf("a second card for one ask: err = %v, want the refusal naming the first", err)
	}
}

func TestACallWithNoBackgroundIsRefusedAndSaysWhatToAdd(t *testing.T) {
	_, err := askedCard(strings.NewReader(""), Ask{Title: "Which one?", Options: []string{"One :: It does one.", "Two :: It does two."}, NothingToBuild: "a pick"}, "", "yasyf/cc-skills")
	if err == nil || !strings.Contains(err.Error(), "--background") {
		t.Fatalf("err = %v, want a refusal naming --background", err)
	}
	whole := `{"id":"card-x","type":"card","title":"Which one?","children":[{"id":"from","type":"markdown","md":"**From:** New."},{"id":"pick","type":"choice","options":[{"id":"a","label":"A"}]}]}`
	if _, err := askedCard(strings.NewReader(whole), Ask{}, "-", "yasyf/cc-skills"); err == nil || !strings.Contains(err.Error(), "--background") {
		t.Fatalf("a whole card with only its From line: err = %v, want the same refusal", err)
	}
}

func TestACardHeAlreadyAnsweredIsNotDrawnTwice(t *testing.T) {
	liveDrive(t)
	f := newLink(t)
	f.shell.answered = map[string]bool{askedID: true}
	askCard(t, asker)
	f.run(t)

	if calls := f.shell.seen(); len(calls) != 0 {
		t.Fatalf("drew %q over his answer", calls)
	}
	_, err := awaitLink(context.Background(), linkDrive, asker, "ask", busStart)
	if err == nil || !strings.Contains(err.Error(), "already answered "+askedID) {
		t.Fatalf("awaitLink err = %v, want the refusal naming the answered card", err)
	}
}

func TestACallWithNoLinkRunningFailsSayingHowToStartIt(t *testing.T) {
	liveDrive(t)
	askCard(t, asker)
	ctx, stop := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer stop()
	_, err := awaitLink(ctx, linkDrive, asker, "ask", busStart)
	if err == nil || !strings.Contains(err.Error(), "lr board link install") {
		t.Fatalf("err = %v, want it to name how to start the link", err)
	}
}

func TestAFirstStartSkipsTheCardChangesAlreadyOnTheBus(t *testing.T) {
	liveDrive(t)
	f := newLink(t)
	draw(t, "old", `{"id":"old"}`)
	if err := SeedCursor(context.Background(), linkDrive, f.state); err != nil {
		t.Fatal(err)
	}
	draw(t, "new", `{"id":"new"}`)
	f.run(t)

	want := []string{`cc-present update-block - --session board-1 --lane pr-desk <{"id":"new"}`}
	if got := f.shell.seen(); !slices.Equal(got, want) {
		t.Fatalf("presented %q, want %q", got, want)
	}
	if err := SeedCursor(context.Background(), linkDrive, f.state); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(filepath.Join(f.state, cursorFile))
	if err != nil {
		t.Fatal(err)
	}
	records, err := client.Tail(context.Background(), client.Query{Drive: linkDrive, Kinds: []string{cardKind}})
	if err != nil {
		t.Fatal(err)
	}
	if string(raw) != strconv.FormatInt(records[len(records)-1].Seq, 10) {
		t.Fatalf("a second seed moved the cursor to %s", raw)
	}
}

func TestCardsPostedWhileTheMacIsAwayArriveInOrderWhenItReturns(t *testing.T) {
	liveDrive(t)
	f := newLink(t)
	draw(t, "owed-1", `{"id":"one"}`)
	draw(t, "owed-2", `{"id":"two"}`)
	f.run(t)

	want := []string{
		`cc-present update-block - --session board-1 --lane pr-desk <{"id":"one"}`,
		`cc-present update-block - --session board-1 --lane pr-desk <{"id":"two"}`,
	}
	if got := f.shell.seen(); !slices.Equal(got, want) {
		t.Fatalf("presented %q, want %q", got, want)
	}
}

func TestARestartResumesFromItsCursorAndNeverRedrawsACard(t *testing.T) {
	liveDrive(t)
	f := newLink(t)
	draw(t, "owed-1", `{"id":"one"}`)
	f.run(t)
	f.shell.calls = nil

	draw(t, "owed-2", `{"id":"two"}`)
	f.run(t)

	want := []string{`cc-present update-block - --session board-1 --lane pr-desk <{"id":"two"}`}
	if got := f.shell.seen(); !slices.Equal(got, want) {
		t.Fatalf("after the restart presented %q, want %q", got, want)
	}
}

func TestARepeatOfTheSameKeyIsOneCard(t *testing.T) {
	liveDrive(t)
	draw(t, "owed-1", `{"id":"one"}`)
	draw(t, "owed-1", `{"id":"one"}`)
	records, err := client.Tail(context.Background(), client.Query{Drive: linkDrive, Kinds: []string{cardKind}})
	if err != nil {
		t.Fatal(err)
	}
	if len(records) != 1 {
		t.Fatalf("two posts of one key stored %d records", len(records))
	}
}

func TestAWithdrawalTakesTheLanesCardDown(t *testing.T) {
	liveDrive(t)
	f := newLink(t)
	f.shell.drawn = map[string]string{"one": `{"id":"one","type":"card"}`}
	if _, err := DrawCard(context.Background(), linkDrive, asker, "owed-3", cardBody{Remove: "one", Reason: "the fix landed"}); err != nil {
		t.Fatal(err)
	}
	f.run(t)
	want := []string{"cc-present remove-block one --reason the fix landed --lane " + asker + " --take-over --session board-1 <"}
	if got := f.shell.seen(); !slices.Equal(got, want) {
		t.Fatalf("presented %q, want %q", got, want)
	}
	said, err := awaitLink(context.Background(), linkDrive, asker, "owed-3", busStart)
	if err != nil || !strings.Contains(said, "WITHDRAWN one") {
		t.Fatalf("awaitLink = %q, %v", said, err)
	}
}

func TestAChangeMustBeOneDrawOrOneRemoval(t *testing.T) {
	liveDrive(t)
	for _, change := range []struct {
		block  json.RawMessage
		remove string
	}{{nil, ""}, {json.RawMessage(`{"id":"one"}`), "one"}} {
		if _, err := DrawCard(context.Background(), linkDrive, "pr-desk", "owed", cardBody{Block: change.block, Remove: change.remove}); err == nil {
			t.Fatalf("DrawCard(%s, %q) stored a card", change.block, change.remove)
		}
	}
}

func TestABoardRefusalTellsTheSenderAndTheNextCardStillDraws(t *testing.T) {
	liveDrive(t)
	f := newLink(t)
	f.shell.refuse = "block has no title"
	draw(t, "owed-1", `{"id":"bad"}`)
	draw(t, "owed-2", `{"id":"good"}`)
	f.run(t)

	if got := len(f.shell.seen()); got != 2 {
		t.Fatalf("a refusal stopped the link after %d calls, want 2", got)
	}
	defects := addressedTo(t, linkRefusalKind, "pr-desk")
	if len(defects) != 1 || !strings.Contains(defects[0].Text, "block has no title") {
		t.Fatalf("defects %+v, want one naming cc-present's words", defects)
	}
}

func TestAMissingBoardToolStopsTheLinkAndKeepsTheCardOwed(t *testing.T) {
	liveDrive(t)
	f := newLink(t)
	f.shell.missing = true
	draw(t, "owed-1", `{"id":"one"}`)
	ctx, stop := context.WithTimeout(context.Background(), 30*time.Second)
	defer stop()
	if err := f.link().Serve(ctx); err == nil || errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("Serve = %v, want the missing tool's error", err)
	}
	if _, err := os.Stat(filepath.Join(f.state, cursorFile)); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("the cursor moved past an undelivered card: %v", err)
	}
}

func TestACardFromAskFlagsRecommendsItsFirstOptionAndCarriesItsPRs(t *testing.T) {
	block, err := Ask{
		Title: "Land PR 2190 tonight?", Background: "It is yasyf/cc-skills#2196 rebased.", From: "owner m31",
		Options: []string{"Land :: It goes out tonight.", "Hold :: It waits for the morning."}, Branch: "yasyf/cc-skills:land",
		Shots: []string{"https://x.test/a.png :: The fixed screen."},
	}.Block("Forge-AI/monorepo")
	if err != nil {
		t.Fatal(err)
	}
	var card struct {
		ID       string `json:"id"`
		Children []struct {
			Type    string `json:"type"`
			Md      string `json:"md"`
			Repo    string `json:"repo"`
			Number  int    `json:"number"`
			Src     string `json:"src"`
			Alt     string `json:"alt"`
			Options []struct {
				Label       string            `json:"label"`
				Md          string            `json:"md"`
				Recommended bool              `json:"recommended"`
				Build       map[string]string `json:"build"`
			} `json:"options"`
		} `json:"children"`
	}
	if err := json.Unmarshal(block, &card); err != nil {
		t.Fatal(err)
	}
	if card.ID != "card-land-pr-2190-tonight" {
		t.Fatalf("id %q", card.ID)
	}
	kinds := make([]string, len(card.Children))
	for i, child := range card.Children {
		kinds[i] = child.Type
	}
	if want := []string{"markdown", "image", "choice", "pr", "pr"}; !slices.Equal(kinds, want) {
		t.Fatalf("children %v, want %v", kinds, want)
	}
	options := card.Children[2].Options
	if !options[0].Recommended || options[0].Md != "It goes out tonight." || options[0].Build["branch"] != "land" || options[1].Recommended {
		t.Fatalf("options %+v", options)
	}
	if card.Children[3].Repo != "Forge-AI/monorepo" || card.Children[3].Number != 2190 || card.Children[4].Repo != "yasyf/cc-skills" || card.Children[4].Number != 2196 {
		t.Fatalf("prs %+v", card.Children[3:])
	}
}

func TestACardAboutAPortOpensWithItAndLinksItsSourceWithNoCardForIt(t *testing.T) {
	block, err := Ask{
		Title: "Keep the old encoder?", Background: "The client sends numbers as strings.", PortOf: 49412,
		Options: []string{"Keep :: Nothing changes.", "Switch :: Numbers arrive as numbers."}, NothingToBuild: "a pick",
	}.Block("Forge-AI/monorepo")
	if err != nil {
		t.Fatal(err)
	}
	var card struct {
		ID       string `json:"id"`
		Title    string `json:"title"`
		Children []struct {
			Type   string `json:"type"`
			Md     string `json:"md"`
			Number int    `json:"number"`
		} `json:"children"`
	}
	if err := json.Unmarshal(block, &card); err != nil {
		t.Fatal(err)
	}
	if card.Title != "port of #49412: Keep the old encoder?" || card.ID != "card-keep-the-old-encoder" {
		t.Fatalf("title %q, id %q", card.Title, card.ID)
	}
	if card.Children[0].Md != "**From:** port of [#49412](https://github.com/Forge-AI/monorepo/pull/49412)" {
		t.Fatalf("from = %q, want the source linked", card.Children[0].Md)
	}
	for _, child := range card.Children {
		if child.Type == "pr" {
			t.Fatalf("children %+v, want no PR card for the source, since a pick on it would land the source", card.Children)
		}
	}
}

func TestACardLinksEachNumberItNamesAndCarriesThatPR(t *testing.T) {
	block, err := Ask{
		Title: "Land the port?", PortOf: 50350, NothingToBuild: "a pick",
		Background: "#50459 ports #50350, and [#7](https://github.com/o/r/pull/7) came first.",
		Options:    []string{"Land :: #50459 goes out.", "Hold :: Nothing moves."},
	}.Block("Forge-AI/monorepo")
	if err != nil {
		t.Fatal(err)
	}
	var card struct {
		Summary  string `json:"summary"`
		Children []struct {
			Type    string `json:"type"`
			Repo    string `json:"repo"`
			Number  int    `json:"number"`
			Options []struct {
				Md string `json:"md"`
			} `json:"options"`
		} `json:"children"`
	}
	if err := json.Unmarshal(block, &card); err != nil {
		t.Fatal(err)
	}
	const port = "[#50459](https://github.com/Forge-AI/monorepo/pull/50459)"
	if want := port + " ports [#50350](https://github.com/Forge-AI/monorepo/pull/50350), and [#7](https://github.com/o/r/pull/7) came first."; card.Summary != want {
		t.Fatalf("summary = %q, want %q", card.Summary, want)
	}
	var prs []string
	for _, child := range card.Children {
		if child.Type == "choice" && child.Options[0].Md != port+" goes out." {
			t.Fatalf("option = %q", child.Options[0].Md)
		}
		if child.Type == "pr" {
			prs = append(prs, fmt.Sprintf("%s#%d", child.Repo, child.Number))
		}
	}
	if want := []string{"Forge-AI/monorepo#50459", "o/r#7"}; !slices.Equal(prs, want) {
		t.Fatalf("prs = %v, want %v", prs, want)
	}
}

func TestABackgroundInParagraphsListsItsFirstLineAndShowsTheRestOnTheCard(t *testing.T) {
	block, err := Ask{
		Title: "How do runs recover?", NothingToBuild: "a pick",
		Background: "Nine runs stopped today.\n\nThe account has room for twice its limit.\n\n- one waits\n- one retries\n",
		Options:    []string{"Wait :: A refused call resumes.", "Raise :: Fewer refusals."},
	}.Block("o/r")
	if err != nil {
		t.Fatal(err)
	}
	var card struct {
		Summary  string `json:"summary"`
		Children []struct {
			Type string `json:"type"`
			Md   string `json:"md"`
		} `json:"children"`
	}
	if err := json.Unmarshal(block, &card); err != nil {
		t.Fatal(err)
	}
	if card.Summary != "Nine runs stopped today." {
		t.Fatalf("summary = %q", card.Summary)
	}
	if want := "The account has room for twice its limit.\n\n- one waits\n- one retries"; card.Children[0].Type != "markdown" || card.Children[0].Md != want {
		t.Fatalf("first child = %+v, want markdown %q", card.Children[0], want)
	}
	if !strings.HasPrefix(card.Children[1].Md, "**From:**") || lacksBackground(block) {
		t.Fatalf("children = %+v", card.Children)
	}
}

func TestAPRNamedByLinkAndByNumberDrawsOneBlockForEachPR(t *testing.T) {
	block, err := Ask{
		Title: "Keep the gate?", NothingToBuild: "a pick",
		Background: "[cc-slack #114](https://github.com/yasyf/cc-slack/pull/114) fixes the read. #114 ports it, and yasyf/cc-skills#114 is another.",
		Options:    []string{"Keep :: [PR #114](https://github.com/yasyf/cc-slack/pull/114) lands.", "Drop :: Nothing moves."},
	}.Block("Forge-AI/monorepo")
	if err != nil {
		t.Fatal(err)
	}
	var card struct {
		Summary  string `json:"summary"`
		Children []struct {
			ID     string `json:"id"`
			Type   string `json:"type"`
			Repo   string `json:"repo"`
			Number int    `json:"number"`
		} `json:"children"`
	}
	if err := json.Unmarshal(block, &card); err != nil {
		t.Fatal(err)
	}
	if want := "[cc-slack #114](https://github.com/yasyf/cc-slack/pull/114) fixes the read. [#114](https://github.com/Forge-AI/monorepo/pull/114) ports it, and yasyf/cc-skills#114 is another."; card.Summary != want {
		t.Fatalf("summary = %q, want %q", card.Summary, want)
	}
	ids := map[string]bool{}
	var prs []string
	for _, child := range card.Children {
		if ids[child.ID] {
			t.Fatalf("block id %q is drawn twice in %+v", child.ID, card.Children)
		}
		ids[child.ID] = true
		if child.Type == "pr" {
			prs = append(prs, fmt.Sprintf("%s#%d", child.Repo, child.Number))
		}
	}
	if want := []string{"yasyf/cc-slack#114", "Forge-AI/monorepo#114", "yasyf/cc-skills#114"}; !slices.Equal(prs, want) {
		t.Fatalf("prs = %v, want %v", prs, want)
	}
}

func TestAskFlagsAreRefusedSayingWhatToChange(t *testing.T) {
	good := Ask{Title: "Which one?", Background: "Why it matters.", Options: []string{"One :: It does one.", "Two :: It does two."}, NothingToBuild: "a pick"}
	for name, tc := range map[string]struct {
		change func(*Ask)
		says   string
	}{
		"no question":         {func(a *Ask) { a.Title = "Pick one" }, "not a question"},
		"one option":          {func(a *Ask) { a.Options = a.Options[:1] }, "--option 'Keep it :: what picking it does'"},
		"five options":        {func(a *Ask) { a.Options = slices.Repeat(a.Options, 3)[:5] }, "2 to 4 options"},
		"a bare option":       {func(a *Ask) { a.Options[1] = "Two" }, "does not say what picking it does"},
		"nothing built":       {func(a *Ask) { a.NothingToBuild = "" }, "--nothing-to-build REASON"},
		"both built":          {func(a *Ask) { a.Branch = "o/r:b" }, "--nothing-to-build REASON"},
		"a bad branch":        {func(a *Ask) { a.NothingToBuild, a.Branch = "", "just-a-branch" }, "not OWNER/NAME:BRANCH"},
		"an unexplained shot": {func(a *Ask) { a.Shots = []string{"/x.png"} }, "does not say what it shows"},
	} {
		ask := good
		ask.Options = slices.Clone(good.Options)
		tc.change(&ask)
		if _, err := ask.Block("o/r"); err == nil || !strings.Contains(err.Error(), tc.says) {
			t.Errorf("%s: err = %v, want it to say %q", name, err, tc.says)
		}
	}
}
