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
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
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
		"held":           {readyRow(func(r *store.ReviewRow) { r.ReadyForTap, r.Hold = false, "root:until #49305 lands" }), false, false},
		"its own word":   {readyRow(func(r *store.ReviewRow) { r.ReadyForTap, r.WaitsOn = false, prrow.FlakeLands }), false, false},
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

func TestAHeldRowSaysWhatHoldsItAndOffersNoApprove(t *testing.T) {
	row := readyRow(func(r *store.ReviewRow) {
		r.ReadyForTap, r.Hold, r.WaitsOn = false, "root:until #49305 lands", "held, root:until #49305 lands"
	})
	card := Card(repo, row)
	if card.Waits != "" || len(card.Options) != 0 || card.Status != "info" {
		t.Fatalf("card = %+v, want no buttons", card)
	}
	if got := card.PRs[0].Blocks; !strings.HasPrefix(got, "Held: ") {
		t.Fatalf("blocks = %q, want what holds it", got)
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

func TestATapOnAHeldRowsCardIsRefusedNamingTheHoldAndHoldStillHolds(t *testing.T) {
	const why = "held, root:the cutover has not run (record #7)"
	s, hooks := withLedger(t, prrow.Decide(readyRow(func(r *store.ReviewRow) {
		r.Hold, r.SupersededBy = "root:the cutover has not run (record #7)", prrow.None
	}))), &recorded{}
	found, err := ledgerRows(s, repo, []int{number})
	if err != nil {
		t.Fatal(err)
	}
	err = Respond(s, repo, hooks.hooks(), found, number, MergeWaits(number, head), "Approve", t0)
	if err == nil || !strings.Contains(err.Error(), why) {
		t.Fatalf("err = %v, want it to name the hold", err)
	}
	if !reflect.DeepEqual(hooks.refused, []string{why}) || len(hooks.taps) != 0 {
		t.Fatalf("refused = %v, taps = %+v", hooks.refused, hooks.taps)
	}
	if err := Respond(s, repo, hooks.hooks(), found, number, MergeWaits(number, head), "Hold", t0); err != nil {
		t.Fatal(err)
	}
	if want := []Key{{number, head}}; !reflect.DeepEqual(hooks.holds, want) {
		t.Fatalf("holds = %+v", hooks.holds)
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

func TestACardAnswerForAMovedHeadOrAnotherLabelIsRefused(t *testing.T) {
	s, hooks := withLedger(t, readyRow(nil)), &recorded{}
	found, err := ledgerRows(s, repo, []int{number})
	if err != nil {
		t.Fatal(err)
	}
	for label, said := range map[string]string{"Approve": "moved to e718a7434fa0", "Merge": "moved to e718a7434fa0", "Ship it": `not "Ship it"`} {
		err := Respond(s, repo, hooks.hooks(), found, number, MergeWaits(number, moved), label, t0)
		if err == nil || !strings.Contains(err.Error(), said) {
			t.Fatalf("%s: err = %v, want it to say %q", label, err, said)
		}
	}
	if len(hooks.taps)+len(hooks.holds) != 0 {
		t.Fatalf("taps = %+v, holds = %+v", hooks.taps, hooks.holds)
	}
}

func TestARefusedCardAnswerLeavesARecordNamingWhy(t *testing.T) {
	s, hooks := withLedger(t, readyRow(nil)), &recorded{}
	found, err := ledgerRows(s, repo, []int{number})
	if err != nil {
		t.Fatal(err)
	}
	if err := Respond(s, repo, hooks.hooks(), found, number, MergeWaits(number, moved), "Approve", t0); err == nil {
		t.Fatal("a tap on a moved head was not refused")
	}
	if err := Respond(s, repo, hooks.hooks(), nil, number, MergeWaits(number, head), "Approve", t0); err == nil {
		t.Fatal("a tap on a PR with no row was not refused")
	}
	if len(hooks.refused) != 2 || !strings.Contains(hooks.refused[0], "moved to") || !strings.Contains(hooks.refused[1], "no row") {
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

func TestTheCardReadsNothingButTheStoreAtOneAndAThousandPRs(t *testing.T) {
	for _, n := range []int{1, 1000} {
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
