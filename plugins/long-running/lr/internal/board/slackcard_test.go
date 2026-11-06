package board

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/scaletest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func TestTheApproveCardIsTheLedgerRowWithApproveAndHold(t *testing.T) {
	row := readyRow(nil)
	want := SlackCard{
		Status: "needs_you", Title: "Approve #45583 at e718a74?",
		PRs: []SlackPRRow{{
			URL: "https://github.com/org/repo/pull/45583", Title: "sand: faster chats", Head: head,
			Review: "CI green; approved by poetic-svc; grade pass; mergeable clean",
			Change: "chats load faster", Plan: "checked against the plan", Risk: "one cache key", Blocks: "Only your approval.", Description: "## Context",
		}},
		Options: []string{"Approve", "Hold"}, Recommended: "Approve", Waits: MergeWaits(number, head),
	}
	if card := Card(repo, row); !reflect.DeepEqual(card, want) {
		t.Fatalf("card = %+v, want %+v", card, want)
	}
}

func TestOnlyALedgerRowReadyAndUntappedWaitsOnHim(t *testing.T) {
	for name, c := range map[string]struct {
		row     store.ReviewRow
		waiting bool
		buttons bool
	}{
		"ready":          {readyRow(nil), true, true},
		"not ready":      {readyRow(func(r *store.ReviewRow) { r.ReadyForTap = false }), false, true},
		"already tapped": {readyRow(func(r *store.ReviewRow) { r.Tapped = true }), false, false},
		"another PR":     {readyRow(func(r *store.ReviewRow) { r.Number = number + 1 }), false, true},
		"held":           {readyRow(func(r *store.ReviewRow) { r.ReadyForTap, r.Hold = false, "root:until #49305 lands" }), false, true},
		"its own word":   {readyRow(func(r *store.ReviewRow) { r.ReadyForTap, r.WaitsOn = false, prrow.FlakeLands }), false, false},
		"exempt":         {readyRow(func(r *store.ReviewRow) { r.ReadyForTap, r.State = false, prrow.StateExempt }), false, false},
		"exempt, CI running": {readyRow(func(r *store.ReviewRow) {
			r.ReadyForTap, r.State, r.CI = false, prrow.StateExempt, prrow.CIPending
		}), false, false},
		"no plan line":             {readyRow(func(r *store.ReviewRow) { r.ReadyForTap, r.PlanRow = false, "" }), false, true},
		"words unread at its head": {readyRow(func(r *store.ReviewRow) { r.ReadHead = "" }), true, false},
	} {
		t.Run(name, func(t *testing.T) {
			found, err := waitingRows(withLedger(t, c.row), repo, []int{number})
			if err != nil || (len(found) == 1) != c.waiting {
				t.Fatalf("waiting = %+v, %v, want waiting %v", found, err, c.waiting)
			}
			if card := Card(repo, c.row); (card.Waits != "" && len(card.Options) == 2) != c.buttons {
				t.Fatalf("card = %+v, want buttons %v", card, c.buttons)
			}
		})
	}
}

func TestAnOpenRowOffersApproveWithTheOneThingItLandsBehind(t *testing.T) {
	for name, c := range map[string]struct {
		change func(*store.ReviewRow)
		lands  string
	}{
		"CI running":   {func(r *store.ReviewRow) { r.CI = prrow.CIPending }, "CI is green at this version"},
		"no review":    {func(r *store.ReviewRow) { r.Approvers = prrow.None }, "it has an approving review at this version"},
		"grade":        {func(r *store.ReviewRow) { r.Grade = prrow.GradePending }, "its review grade passes"},
		"a stack":      {func(r *store.ReviewRow) { r.ParentState, r.Parent = prrow.ParentOpen, "#7" }, "the change below it, #7, lands"},
		"in the queue": {func(r *store.ReviewRow) { r.State = prrow.StateQueued }, "the merge queue takes it"},
		"held":         {func(r *store.ReviewRow) { r.Hold, r.CI = "root:until #49305 lands (record #7)", prrow.CIPending }, "its hold lifts: until #49305 lands"},
	} {
		t.Run(name, func(t *testing.T) {
			row := readyRow(func(r *store.ReviewRow) { r.ReadyForTap = false; c.change(r) })
			card := Card(repo, row)
			if card.Status != "needs_you" || card.Recommended != approveLabel || card.Waits != MergeWaits(number, head) {
				t.Fatalf("card = %+v, want Approve at this head", card)
			}
			if got := card.PRs[0]; got.Lands != c.lands || got.Blocks != "" {
				t.Fatalf("card PR = %+v, want it to land once %s", got, c.lands)
			}
		})
	}
}

func TestARowThatNoLongerWaitsStillDrawsItsCardInItsOwnWords(t *testing.T) {
	landed := readyRow(func(r *store.ReviewRow) {
		r.Tapped, r.State, r.WaitsOn = true, prrow.StateLanded, "Nothing; it landed."
	})
	card := Card(repo, landed)
	if card.Status != "done" || card.Waits != "" || len(card.Options) != 0 || card.Recommended != "" {
		t.Fatalf("card = %+v, want a done card with no buttons", card)
	}
	if got := card.PRs[0]; got.Blocks != "Nothing; it landed." || got.Change != "chats load faster" || got.Title != "sand: faster chats" {
		t.Fatalf("card PR = %+v, want the row's own words", got)
	}
}

func waitingRows(s *store.Store, repo string, numbers []int) ([]store.ReviewRow, error) {
	rows, err := ledgerRows(s, repo, numbers)
	return slices.DeleteFunc(rows, func(row store.ReviewRow) bool { return !waitsOnHim(row) }), err
}

func TestATapOnAHeldRowsCardStandsAndTheRowStaysHeld(t *testing.T) {
	const why = "held, root:the cutover has not run (record #7)"
	s, hooks := withLedger(t, prrow.Decide(readyRow(func(r *store.ReviewRow) {
		r.Hold, r.SupersededBy = "root:the cutover has not run (record #7)", prrow.None
	}))), &recorded{}
	found, err := ledgerRows(s, repo, []int{number})
	if err != nil {
		t.Fatal(err)
	}
	if card := Card(repo, found[0]); card.Recommended != approveLabel || card.Waits != MergeWaits(number, head) || card.PRs[0].Lands != "its hold lifts: the cutover has not run" {
		t.Fatalf("card = %+v, want Approve landing once the hold lifts", card)
	}
	if err := Respond(s, repo, hooks.hooks(), found, number, MergeWaits(number, head), "Approve", t0); err != nil {
		t.Fatal(err)
	}
	if len(hooks.taps) != 1 || len(hooks.refused) != 0 {
		t.Fatalf("taps = %+v, refused = %v", hooks.taps, hooks.refused)
	}
	tapped, stored, err := s.ReviewRow(repo, number)
	if err != nil || !stored || !tapped.Tapped || tapped.ReadyForTap || tapped.WaitsOn != why {
		t.Fatalf("row = %+v, %v, %v, want it tapped and still held", tapped, stored, err)
	}
}

func TestATapOnAnUntilTapRowsCardStands(t *testing.T) {
	s, hooks := withLedger(t, readyRow(func(r *store.ReviewRow) { r.AwaitsTap = "asks his tap" })), &recorded{}
	found, err := waitingRows(s, repo, []int{number})
	if err != nil || len(found) != 1 {
		t.Fatalf("waiting = %+v, %v", found, err)
	}
	if err := Respond(s, repo, hooks.hooks(), found, number, MergeWaits(number, head), "Approve", t0); err != nil {
		t.Fatal(err)
	}
	if len(hooks.taps) != 1 || len(hooks.refused) != 0 {
		t.Fatalf("taps = %+v, refused = %v", hooks.taps, hooks.refused)
	}
}

func TestMergeOnTheCardTapsTheRowAndHoldHoldsIt(t *testing.T) {
	s, hooks := withLedger(t, readyRow(nil)), &recorded{}
	found, err := ledgerRows(s, repo, []int{number})
	if err != nil {
		t.Fatal(err)
	}
	if err := Respond(s, repo, hooks.hooks(), found, number, MergeWaits(number, head), "Hold", t0); err != nil {
		t.Fatal(err)
	}
	if want := []Key{{number, head}}; !reflect.DeepEqual(hooks.holds, want) || len(hooks.taps) != 0 {
		t.Fatalf("holds = %+v, taps = %+v", hooks.holds, hooks.taps)
	}
	if err := Respond(s, repo, hooks.hooks(), found, number, MergeWaits(number, head), "Merge", t0); err != nil {
		t.Fatal(err)
	}
	if want := []TapRecord{{number, head, "owner", machine.TapTapped}}; !reflect.DeepEqual(hooks.taps, want) {
		t.Fatalf("taps = %+v", hooks.taps)
	}
	if again, err := waitingRows(s, repo, []int{number}); err != nil || len(again) != 0 {
		t.Fatalf("rows after his tap = %+v, %v", again, err)
	}
}

func TestATapOnACardItsRowHasMovedPastCountsAtTheHeadTheCardShowed(t *testing.T) {
	s, hooks := withLedger(t, readyRow(nil)), &recorded{}
	if _, err := s.PutFact(repo, number, moved, "ci", "green", t0); err != nil {
		t.Fatal(err)
	}
	found, err := ledgerRows(s, repo, []int{number})
	if err != nil {
		t.Fatal(err)
	}
	if err := Respond(s, repo, hooks.hooks(), found, number, MergeWaits(number, moved), "Approve", t0); err != nil {
		t.Fatal(err)
	}
	if want := []TapRecord{{number, moved, "owner", machine.TapTapped}}; !reflect.DeepEqual(hooks.taps, want) || len(hooks.refused) != 0 {
		t.Fatalf("taps = %+v, refused = %+v, want his tap at the head the card showed", hooks.taps, hooks.refused)
	}
	shown, err := s.Facts(repo, number, moved)
	if err != nil || shown["tap"] != string(machine.TapTapped) {
		t.Fatalf("facts at the shown head = %v, %v, want his tap", shown, err)
	}
	row, ok, err := s.ReviewRow(repo, number)
	if err != nil || !ok || row.Tapped {
		t.Fatalf("row = %+v, %v, %v; tap carry decides whether it reaches the row's head", row, ok, err)
	}
}

func TestAHoldOnACardItsRowHasMovedPastHoldsTheRowsHead(t *testing.T) {
	s, hooks := withLedger(t, readyRow(nil)), &recorded{}
	found, err := ledgerRows(s, repo, []int{number})
	if err != nil {
		t.Fatal(err)
	}
	if err := Respond(s, repo, hooks.hooks(), found, number, MergeWaits(number, moved), "Hold", t0); err != nil {
		t.Fatal(err)
	}
	if want := []Key{{number, head}}; !reflect.DeepEqual(hooks.holds, want) || len(hooks.refused) != 0 {
		t.Fatalf("holds = %+v, refused = %+v", hooks.holds, hooks.refused)
	}
}

func TestACardAnswerWithAnotherLabelIsRefused(t *testing.T) {
	s, hooks := withLedger(t, readyRow(nil)), &recorded{}
	found, err := ledgerRows(s, repo, []int{number})
	if err != nil {
		t.Fatal(err)
	}
	err = Respond(s, repo, hooks.hooks(), found, number, MergeWaits(number, head), "Ship it", t0)
	if err == nil || !strings.Contains(err.Error(), `not "Ship it"`) {
		t.Fatalf("err = %v", err)
	}
	if len(hooks.taps)+len(hooks.holds) != 0 {
		t.Fatalf("taps = %+v, holds = %+v", hooks.taps, hooks.holds)
	}
}

func TestARefusedCardAnswerLeavesARecordNamingWhy(t *testing.T) {
	s, hooks := withLedger(t, readyRow(nil)), &recorded{}
	if err := Respond(s, repo, hooks.hooks(), nil, number, MergeWaits(number, head), "Approve", t0); err == nil {
		t.Fatal("a tap on a PR with no row was not refused")
	}
	if len(hooks.refused) != 1 || !strings.Contains(hooks.refused[0], "no row") {
		t.Fatalf("refused = %+v", hooks.refused)
	}
}

func TestABareNumberNamesEveryDrivePRHoldingItAndNoOther(t *testing.T) {
	s := withLedger(t, store.ReviewRow{Repo: "yasyf/cc-skills", Number: 2475, Head: head}, store.ReviewRow{Repo: "yasyf/cc-slack", Number: 66, Head: head}, store.ReviewRow{Repo: "Forge-AI/monorepo", Number: 66, Head: head}, store.ReviewRow{Repo: "Forge-AI/monorepo", Number: 49032, Head: head})
	held, err := Holders(s, []int{2475, 66, 7}, []SlackPR{{"yasyf/cc-skills", 2475}})
	if err != nil {
		t.Fatal(err)
	}
	want := []SlackPR{{"yasyf/cc-skills", 2475}, {"Forge-AI/monorepo", 66}, {"yasyf/cc-slack", 66}}
	if !reflect.DeepEqual(held, want) {
		t.Fatalf("held = %+v, want %+v", held, want)
	}
}

func TestACardReadsTheNamedDriveAndSkipsOneThatEnded(t *testing.T) {
	home := t.TempDir()
	t.Setenv("CLAUDE_CONFIG_DIR", home)
	registry := filepath.Join(home, "long-running", "drives")
	running := filepath.Join(home, "running")
	for _, dir := range []string{registry, running} {
		if err := os.MkdirAll(dir, 0o700); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.WriteFile(filepath.Join(running, configFile), []byte("{}"), 0o600); err != nil {
		t.Fatal(err)
	}
	for name, state := range map[string]string{"ended": filepath.Join(home, "ended"), "running": running} {
		entry := fmt.Sprintf(`{"drive":%q,"state_dir":%q,"sessions":["s"]}`, name, state)
		if err := os.WriteFile(filepath.Join(registry, name+".json"), []byte(entry), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	for name, want := range map[string]string{"": "running", "ended": "ended"} {
		found, err := cardDrives(name)
		if err != nil {
			t.Fatal(err)
		}
		if len(found) != 1 || found[0].Drive != want {
			t.Fatalf("cardDrives(%q) = %+v, want only %s", name, found, want)
		}
	}
}

type countedShell struct{ calls int }

func (c *countedShell) Run(context.Context, shell.Command) (shell.Result, error) {
	c.calls++
	return shell.Result{}, fmt.Errorf("the card ran a command")
}

func TestReadingACardMarksNoRowShownAndOnlyACardThatStandsAtItsHeadDoes(t *testing.T) {
	home := t.TempDir()
	t.Setenv("CLAUDE_CONFIG_DIR", home)
	registry, state := filepath.Join(home, "long-running", "drives"), filepath.Join(home, "running")
	for _, dir := range []string{registry, state} {
		if err := os.MkdirAll(dir, 0o700); err != nil {
			t.Fatal(err)
		}
	}
	cfg := fmt.Sprintf(`{"repo":%q,"drive":"running","checkout":"/c","author":"a","label":"l","movers":["lanes"]}`, repo)
	entry := fmt.Sprintf(`{"drive":"running","repo":%q,"state_dir":%q,"sessions":["s"]}`, repo, state)
	for path, body := range map[string]string{filepath.Join(state, configFile): cfg, filepath.Join(registry, "running.json"): entry} {
		if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	row := readyRow(nil)
	s, err := store.Open(filepath.Join(state, "lr.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	if err := s.PutReviewRows([]store.ReviewRow{row}); err != nil {
		t.Fatal(err)
	}
	send := func(ask any) {
		t.Helper()
		body, err := json.Marshal(ask)
		if err != nil {
			t.Fatal(err)
		}
		counted := &countedShell{}
		cmd := Command(&app.App{Runner: counted, CCX: ccx.CLI{Runner: counted}, Now: func() time.Time { return t0 }})
		cmd.SetArgs([]string{"card", "--drive", "running", "--lane", "board-watch"})
		cmd.SetIn(strings.NewReader(string(body)))
		cmd.SetOut(&strings.Builder{})
		if err := cmd.Execute(); err != nil {
			t.Fatal(err)
		}
	}
	said := func() string {
		t.Helper()
		stored, err := s.Facts(repo, row.Number, row.Head)
		if err != nil {
			t.Fatal(err)
		}
		return stored[FactTapCard]
	}
	pr := SlackPR{repo, row.Number}
	send(map[string]any{"prs": []SlackPR{pr}})
	if got := said(); got != "" {
		t.Fatalf("reading its card marked the row %q, so a render hides it from him", got)
	}
	send(map[string]any{"shown": []SlackShown{{pr, MergeWaits(row.Number, "0000000")}}})
	if got := said(); got != "" {
		t.Fatalf("a card at another head marked the row %q", got)
	}
	send(map[string]any{"shown": []SlackShown{{pr, MergeWaits(row.Number, row.Head)}}})
	if got := said(); got != tapCardInSlack {
		t.Fatalf("a card that stands at its head reads %q, want %q", got, tapCardInSlack)
	}
}

func TestTheCardReadsNothingButTheStoreAtOneAndManyPRs(t *testing.T) {
	for _, n := range []int{1, scaletest.N()} {
		t.Run(strconv.Itoa(n), func(t *testing.T) {
			home := t.TempDir()
			t.Setenv("CLAUDE_CONFIG_DIR", home)
			registry, state := filepath.Join(home, "long-running", "drives"), filepath.Join(home, "running")
			for _, dir := range []string{registry, state} {
				if err := os.MkdirAll(dir, 0o700); err != nil {
					t.Fatal(err)
				}
			}
			cfg := fmt.Sprintf(`{"repo":%q,"drive":"running","checkout":"/c","author":"a","label":"l","movers":["lanes"]}`, repo)
			entry := fmt.Sprintf(`{"drive":"running","repo":%q,"state_dir":%q,"sessions":["s"]}`, repo, state)
			for path, body := range map[string]string{filepath.Join(state, configFile): cfg, filepath.Join(registry, "running.json"): entry} {
				if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
					t.Fatal(err)
				}
			}
			s, err := store.Open(filepath.Join(state, "lr.sqlite"))
			if err != nil {
				t.Fatal(err)
			}
			ready := make([]store.ReviewRow, n)
			asked := make([]SlackPR, n)
			for i := range n {
				ready[i] = readyRow(func(r *store.ReviewRow) { r.Number = number + i })
				asked[i] = SlackPR{repo, number + i}
			}
			if err := s.PutReviewRows(ready); err != nil {
				t.Fatal(err)
			}
			if err := s.Close(); err != nil {
				t.Fatal(err)
			}
			counted := &countedShell{}
			ask, err := json.Marshal(map[string]any{"prs": asked})
			if err != nil {
				t.Fatal(err)
			}
			var out strings.Builder
			cmd := Command(&app.App{Runner: counted, CCX: ccx.CLI{Runner: counted}, Now: func() time.Time { return t0 }})
			cmd.SetArgs([]string{"card", "--drive", "running", "--lane", "board-watch"})
			cmd.SetIn(strings.NewReader(string(ask)))
			cmd.SetOut(&out)
			if err := cmd.Execute(); err != nil {
				t.Fatal(err)
			}
			var answer struct {
				Cards []SlackPRCard `json:"cards"`
			}
			if err := json.Unmarshal([]byte(out.String()), &answer); err != nil {
				t.Fatal(err)
			}
			if len(answer.Cards) != n || counted.calls != 0 {
				t.Fatalf("%d cards for %d PRs with %d commands run, want %d and none", len(answer.Cards), n, counted.calls, n)
			}
		})
	}
}

func TestASlackMergeCardTapIsAnOwnerRecordThatReachesTheLandingHost(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	ctx := context.Background()

	if err := postHooks(ctx, "d", "pr-cards", repo).Tap(TapRecord{number, head, byOwner, machine.TapTapped}); err != nil {
		t.Fatal(err)
	}

	written, err := client.Tail(ctx, client.Query{Drive: "d", Kinds: []string{"owner"}})
	if err != nil {
		t.Fatal(err)
	}
	if len(written) != 1 {
		t.Fatalf("records = %+v", written)
	}
	if tap, ordered := bus.OrderOf(written[0], repo); !ordered || tap.PR != number || tap.Head != head || tap.Value != string(machine.TapTapped) {
		t.Fatalf("order = %+v, %v", tap, ordered)
	}
}

func TestPlanWordsSayASentenceTheLineAndRowShareOnce(t *testing.T) {
	for _, tc := range []struct {
		line, row, want string
	}{
		{"the plan's step", "the plan's step", "the plan's step"},
		{"", "the plan's step", "the plan's step"},
		{"the plan's step", "", "the plan's step"},
		{"line 73", "the plan's step", "line 73 the plan's step"},
	} {
		if got := PlanWords(store.ReviewRow{PlanLine: tc.line, PlanRow: tc.row}); got != tc.want {
			t.Fatalf("PlanWords(%q, %q) = %q, want %q", tc.line, tc.row, got, tc.want)
		}
	}
}

func TestAnAlertFixCardSaysThePacificMinuteItMergesItselfAt(t *testing.T) {
	row := readyRow(func(r *store.ReviewRow) { r.MergesAt = "2026-11-05T13:12:00Z" })

	card := Card(repo, row)

	if card.MergesAt != row.MergesAt || card.Title != "Approve #45583 at e718a74? It merges itself at 5:12 AM unless you tap Hold" {
		t.Fatalf("card = %+v, want the minute it merges itself", card)
	}
	if moved := Card(repo, readyRow(func(r *store.ReviewRow) { r.MergesAt = "2026-11-05T14:12:00Z" })); drawnSignature(moved) == drawnSignature(card) {
		t.Fatal("a moved minute draws the same card, so his post would keep the old one")
	}
}

func TestHisApprovalPostKeepsTheMinuteItFirstReachedHimAtAHead(t *testing.T) {
	row := readyRow(nil)
	s := withLedger(t, row)
	shown := []SlackShown{{SlackPR{repo, row.Number}, MergeWaits(row.Number, row.Head)}}

	for _, at := range []time.Time{t0, t0.Add(time.Hour)} {
		if err := MarkShown(s, repo, []store.ReviewRow{row}, shown, at); err != nil {
			t.Fatal(err)
		}
	}

	stored, err := s.Facts(repo, row.Number, row.Head)
	if err != nil || stored[gates.ShownFact] != store.Stamp(t0) {
		t.Fatalf("shown at %q, %v, want %s, the first time it reached him", stored[gates.ShownFact], err, store.Stamp(t0))
	}
}

func TestACardShownAtAHeadTheStoreHasNotReadStillRecordsItAtThatHead(t *testing.T) {
	row := readyRow(nil)
	moved := readyRow(func(r *store.ReviewRow) { r.Head = "bbbbbbb0123456789012345678901234567890ab" })
	cardAt := MergeWaits(row.Number, row.Head)
	for name, rows := range map[string][]store.ReviewRow{"a row at another head": {moved}, "no row": nil} {
		t.Run(name, func(t *testing.T) {
			s := withLedger(t, moved)
			if err := MarkShown(s, repo, rows, []SlackShown{{SlackPR{repo, row.Number}, cardAt}}, t0); err != nil {
				t.Fatal(err)
			}
			facts, err := s.HeadFacts(repo, row.Number, []string{gates.ShownFact})
			if err != nil || len(facts) != 1 || facts[0].Head != cardHead(cardAt) {
				t.Fatalf("shown facts = %+v, %v, want one at the card's own head", facts, err)
			}
		})
	}
}
