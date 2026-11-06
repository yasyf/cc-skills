package board

import (
	"bytes"
	"cmp"
	"context"
	"encoding/json"
	"fmt"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ledger/ledgertest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const liveText = "api (platform) on plat-usw2-prod: outside a release at 0dfbd44e165e, applied 3h0m ago\n"

var cardsNow = time.Date(2026, 11, 2, 8, 0, 0, 0, time.UTC)

type cardShell struct {
	ledgers   string
	cards     []string
	ancestors map[string]bool
	answers   map[string]bool
	questions map[string]bool
	offers    map[string][]string
	shows     map[string][]int
	repos     map[string]string
	rowed     map[string]bool
	removed   []string
	reasons   []string
	asked     []string
	redrawn   []string
}

func (f *cardShell) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	argv := strings.Join(c.Argv, " ")
	switch {
	case strings.Contains(argv, "ledger list"):
		return shell.Result{Stdout: cmp.Or(f.ledgers, "[]")}, nil
	case strings.Contains(argv, "ledger add"):
		return shell.Result{Stdout: `{"id":"c4rd5"}`}, nil
	case strings.Contains(argv, "outcomes --block"):
		card := c.Argv[3]
		if !f.answers[card] {
			return shell.Result{Stdout: `{"answers":[]}`}, nil
		}
		return shell.Result{Stdout: fmt.Sprintf(`{"answers":[{"origin":"human","payload":{"blockId":"%s-choice","optionIds":["x"]}}]}`, card)}, nil
	case strings.Contains(argv, "outcomes --all"):
		blocks := []map[string]any{{"id": "sec", "type": "markdown"}, {"id": mergeTriage, "type": "card"}}
		for _, card := range f.cards {
			block := map[string]any{"id": card, "type": "card"}
			var children []map[string]any
			if f.questions[card] || f.offers[card] != nil {
				var options []map[string]any
				for _, label := range f.offers[card] {
					options = append(options, map[string]any{"label": label})
				}
				children = append(children, map[string]any{"id": card + "-choice", "type": "choice", "options": options})
			}
			for _, number := range f.shows[card] {
				children = append(children, map[string]any{"id": fmt.Sprintf("%s-pr-%d", card, number), "type": "pr", "repo": cmp.Or(f.repos[card], defaultRepo), "number": number})
			}
			if children != nil {
				block["children"] = children
			}
			blocks = append(blocks, block)
		}
		encoded, err := json.Marshal(map[string]any{"doc": map[string]any{"blocks": blocks}})
		return shell.Result{Stdout: string(encoded)}, err
	case strings.HasPrefix(argv, "cc-present ask - "):
		f.asked = append(f.asked, c.Stdin)
		return shell.Result{}, nil
	case strings.HasPrefix(argv, "cc-slack redraw-pr-cards --pr "):
		f.redrawn = append(f.redrawn, c.Argv[3])
		return shell.Result{Stdout: `{"redrawing":1}`}, nil
	case strings.Contains(argv, "remove-block"):
		f.removed = append(f.removed, c.Argv[2])
		if i := slices.Index(c.Argv, "--reason"); i > 0 {
			f.reasons = append(f.reasons, c.Argv[i+1])
		}
		return shell.Result{}, nil
	case strings.Contains(argv, "answer list"):
		card := ""
		for _, arg := range c.Argv {
			if strings.HasPrefix(arg, "board-card:") {
				card = strings.TrimPrefix(arg, "board-card:")
			}
		}
		if f.rowed[card] {
			return shell.Result{Stdout: fmt.Sprintf(`[{"tags":["board-block:%s-choice"]}]`, card)}, nil
		}
		return shell.Result{Stdout: "[]"}, nil
	case strings.HasPrefix(argv, "tools/ci release live"):
		return shell.Result{Stdout: liveText}, nil
	case strings.Contains(argv, "merge-base"):
		if f.ancestors[c.Argv[5]+" "+c.Argv[6]] {
			return shell.Result{}, nil
		}
		return shell.Result{Code: 1}, &shell.ExitError{Argv: c.Argv, Code: 1}
	}
	return shell.Result{}, fmt.Errorf("unexpected call %s", argv)
}

type prStatus struct {
	ccx.Client
	states  map[int]string
	reads   int
	repos   []string
	missing string
}

func (p *prStatus) PRStatus(_ context.Context, repo string, numbers []int) ([]ccx.PRStatus, error) {
	p.reads++
	p.repos = append(p.repos, repo)
	if repo == p.missing {
		return nil, fmt.Errorf("ccx vcs pr status --repo %s --json 1 exited 1: prstate: ghapi: graphql: Could not resolve to a Repository with the name '%s'", repo, repo)
	}
	out := make([]ccx.PRStatus, len(numbers))
	for i, number := range numbers {
		state := p.states[number]
		if state == "" {
			state = "OPEN"
		}
		out[i] = ccx.PRStatus{Number: number, State: state}
	}
	return out, nil
}

type cardsFixture struct {
	Cards
	shell  *cardShell
	status *prStatus
	posted []string
	clock  time.Time
}

func newCards(t *testing.T, on *cardShell, states map[int]string, closed ...string) *cardsFixture {
	t.Helper()
	f := &cardsFixture{shell: on, status: &prStatus{states: states}, clock: cardsNow}
	f.Cards = Cards{
		Runner: on, Ledger: &ledgertest.Fake{}, CCX: f.status, Bus: "brook-cci", Checkout: "/co", Session: "s1", StateDir: t.TempDir(),
		Now: func() time.Time { return f.clock },
		Closed: func(_ context.Context, seqs []string) (map[string]bool, error) {
			out := map[string]bool{}
			for _, seq := range seqs {
				out[seq] = slices.Contains(closed, seq)
			}
			return out, nil
		},
		Withdraw: func(_ context.Context, _, _, text string) error {
			f.posted = append(f.posted, text)
			return nil
		},
	}
	return f
}

func TestUntilRecordsTheEventThatEndsACard(t *testing.T) {
	for given, stored := range map[string]string{
		"at:4h":                               "at:2026-11-02T12:00:00Z",
		"pr:47440":                            "pr:Forge-AI/monorepo#47440",
		"pr:yasyf/cc-skills#1691":             "pr:yasyf/cc-skills#1691",
		"release:api@plat-usw2-prod:0dfbd44e": "release:api@plat-usw2-prod:0dfbd44e",
		"record:#308624":                      "record:308624",
		"answered":                            "answered",
	} {
		f := newCards(t, &cardShell{}, nil)
		end, err := f.Until(context.Background(), "card-a", given, "")
		if err != nil {
			t.Fatal(err)
		}
		if end.String() != stored {
			t.Errorf("%s = %s, want %s", given, end, stored)
		}
		known, err := f.ends(context.Background())
		if err != nil || known["card-a"]["until"] != stored {
			t.Errorf("%s stored %v, %v", given, known, err)
		}
	}
}

func TestUntilRefusesACardWithNoCheckableEnd(t *testing.T) {
	for given, said := range map[string]string{
		"soon":                           "names no end condition",
		"when Anubhav's release is done": "names no end condition",
		"pr:abc":                         "is not an end condition",
		"at:2026-11-02T07:00:00Z":        "has already passed",
	} {
		f := newCards(t, &cardShell{}, nil)
		_, err := f.Until(context.Background(), "card-a", given, "")
		if err == nil || !strings.Contains(err.Error(), said) {
			t.Errorf("%s: err = %v", given, err)
		}
		if known, err := f.ends(context.Background()); err != nil || len(known) != 0 {
			t.Errorf("%s recorded %v, %v", given, known, err)
		}
	}
}

func TestMissingListsBoardCardsWithNoEndAndSkipsTheMergeList(t *testing.T) {
	f := newCards(t, &cardShell{cards: []string{"card-a", "card-b"}}, nil)
	if _, err := f.Until(context.Background(), "card-a", "pr:1", ""); err != nil {
		t.Fatal(err)
	}
	got, err := f.Missing(context.Background(), nil)
	if err != nil || !reflect.DeepEqual(got, []string{"card-b"}) {
		t.Fatalf("missing = %v, %v", got, err)
	}
}

func TestPathsListsEachOpenCardsDesignPathsFromTheLedgerAlone(t *testing.T) {
	f := newCards(t, &cardShell{}, nil)
	ctx := context.Background()
	if _, err := f.Until(ctx, "card-storage", "pr:1", "", "go/sand/internal/cloudrunner", "infra/rows/cloudrunner"); err != nil {
		t.Fatal(err)
	}
	if _, err := f.Until(ctx, "card-storage", "pr:2", ""); err != nil {
		t.Fatal(err)
	}
	if _, err := f.Until(ctx, "card-plain", "pr:1", ""); err != nil {
		t.Fatal(err)
	}
	if _, err := f.Until(ctx, "card-gone", "pr:1", "", "api/src"); err != nil {
		t.Fatal(err)
	}
	if _, err := f.Until(ctx, "card-done", "pr:1", "", "dashboard"); err != nil {
		t.Fatal(err)
	}
	if err := f.record(ctx, "card-done", map[string]string{"withdrawn": stamp(cardsNow)}); err != nil {
		t.Fatal(err)
	}
	got, err := f.Paths(ctx)
	want := map[string][]string{"card-storage": {"go/sand/internal/cloudrunner", "infra/rows/cloudrunner"}, "card-gone": {"api/src"}}
	if err != nil || !reflect.DeepEqual(got, want) {
		t.Fatalf("paths = %v, %v; want %v", got, err, want)
	}
}

func TestPathsReadTheDrivesLedgerByTitleOnAHostWithoutItsStateDir(t *testing.T) {
	shared := &ledgertest.Fake{}
	if err := shared.SetRow(context.Background(), "mac-ledger", cardPrefix+"card-storage", map[string]string{"until": "pr:Forge-AI/monorepo#1", pathsField: "go/sand/internal/cloudrunner"}); err != nil {
		t.Fatal(err)
	}
	f := newCards(t, &cardShell{ledgers: `[{"id":"other","title":"board cards: another-drive"},{"id":"mac-ledger","title":"board cards: brook-cci"}]`}, nil)
	f.Ledger = shared
	f.StateDir = filepath.Join(t.TempDir(), "never-made")
	got, err := f.Paths(context.Background())
	want := map[string][]string{"card-storage": {"go/sand/internal/cloudrunner"}}
	if err != nil || !reflect.DeepEqual(got, want) {
		t.Fatalf("paths = %v, %v; want %v", got, err, want)
	}
	f.shell.ledgers = ""
	if again, err := f.ledgerID(context.Background()); err != nil || again != "mac-ledger" {
		t.Fatalf("ledger = %q, %v; want the listed mac-ledger kept for the next read", again, err)
	}
}

func TestSweepWithdrawsEachCardWhoseEventHappenedAndNamesTheOutcome(t *testing.T) {
	on := &cardShell{cards: []string{"merged-pr", "open-pr", "past-time", "future-time", "closed-record", "shipped", "unshipped", "no-end"}, ancestors: map[string]bool{"0dfbd44e 0dfbd44e165e": true}}
	f := newCards(t, on, map[int]string{1: "MERGED"}, "7")
	ctx := context.Background()
	for _, pair := range [][2]string{{"merged-pr", "pr:1"}, {"open-pr", "pr:2"}, {"past-time", "at:1h"}, {"future-time", "at:3h"}, {"closed-record", "record:7"}, {"shipped", "release:api@plat-usw2-prod:0dfbd44e"}, {"unshipped", "release:api@plat-usw2-prod:1234567"}} {
		if _, err := f.Until(ctx, pair[0], pair[1], ""); err != nil {
			t.Fatal(err)
		}
	}
	f.clock = cardsNow.Add(2 * time.Hour)
	var out bytes.Buffer
	if err := f.Sweep(ctx, "board-lane", &out); err != nil {
		t.Fatal(err)
	}
	if want := []string{"merged-pr", "past-time", "closed-record", "shipped"}; !reflect.DeepEqual(on.removed, want) {
		t.Fatalf("removed = %v", on.removed)
	}
	if len(on.reasons) != len(on.removed) || slices.Contains(on.reasons, "") {
		t.Fatalf("remove-block reasons = %q, want each card's outcome for its Slack card", on.reasons)
	}
	if f.status.reads != 1 {
		t.Fatalf("status reads = %d", f.status.reads)
	}
	if !strings.Contains(f.posted[0], "Withdrew merged-pr from the owner's board: Forge-AI/monorepo#1 is merged") {
		t.Fatalf("posted = %q", f.posted[0])
	}
	if !strings.Contains(out.String(), "PROBLEM no-end: no end condition") {
		t.Fatalf("out = %s", out.String())
	}
	if known, err := f.ends(ctx); err != nil || known["merged-pr"] != nil {
		t.Fatalf("a withdrawn card stays known: %v, %v", known, err)
	}
}

func TestTheSweepReadsNoBuildLinkAsAPRAndOneUnreadableRepoStopsNoOtherCard(t *testing.T) {
	watch, gone, merged := "card-watch-unread-end", "card-land-gone-repo", "card-pick-merged"
	on := &cardShell{
		cards: []string{watch, gone, merged},
		offers: map[string][]string{
			watch:  {"Keep reading up to 5 more minutes", "Grade on the last good read", "Pass with a warning", "Leave it as it is"},
			gone:   {"Approve"},
			merged: {"Approve, land it"},
		},
		shows: map[string][]int{watch: {1}, gone: {1}, merged: {2}},
		repos: map[string]string{watch: "builds/13033", gone: "acme/gone"},
	}
	f := newCards(t, on, map[int]string{2: "MERGED"})
	f.status.missing = "acme/gone"
	ctx := context.Background()
	for _, card := range on.cards {
		if _, err := f.Until(ctx, card, "at:30d", ""); err != nil {
			t.Fatal(err)
		}
	}
	var out bytes.Buffer
	if err := f.Sweep(ctx, "board-lane", &out); err != nil {
		t.Fatal(err)
	}
	if want := []string{merged}; !reflect.DeepEqual(on.removed, want) {
		t.Fatalf("removed = %v, want %v", on.removed, want)
	}
	if slices.Contains(f.status.repos, "builds/13033") {
		t.Fatalf("PR status read %v, want no read of the Buildkite build drawn as a PR block", f.status.repos)
	}
	if !strings.Contains(out.String(), "WARNING each card waiting on a acme/gone PR stays up") || strings.Contains(out.String(), "builds/13033") {
		t.Fatalf("out = %s", out.String())
	}
}

func TestSweepNeverTakesAnUnansweredQuestionDownOnATimeAlone(t *testing.T) {
	on := &cardShell{cards: []string{"question-past", "question-merged", "question-answered", "notice-past"}, questions: map[string]bool{"question-past": true, "question-merged": true, "question-answered": true}}
	f := newCards(t, on, map[int]string{1: "MERGED"})
	ctx := context.Background()
	for _, pair := range [][2]string{{"question-past", "at:1h"}, {"question-merged", "pr:1"}, {"question-answered", "answered"}, {"notice-past", "at:1h"}} {
		if _, err := f.Until(ctx, pair[0], pair[1], ""); err != nil {
			t.Fatal(err)
		}
	}
	f.clock = cardsNow.Add(30 * 24 * time.Hour)
	var out bytes.Buffer
	if err := f.Sweep(ctx, "board-lane", &out); err != nil {
		t.Fatal(err)
	}
	if want := []string{"question-merged", "notice-past"}; !reflect.DeepEqual(on.removed, want) {
		t.Fatalf("removed = %v, want %v", on.removed, want)
	}
	if strings.Contains(out.String(), "PROBLEM") {
		t.Fatalf("out = %s, want an answered end read as an end", out.String())
	}
}

func TestSweepWithdrawsACardAskingToLandOnceEveryPRItShowsMergedOrClosedWhateverItsEnd(t *testing.T) {
	on := &cardShell{
		cards:     []string{"card-pick-merged", "card-tap-closed", "card-two-one-open", "card-pick-open", "card-no-pr"},
		questions: map[string]bool{"card-no-pr": true},
		offers:    map[string][]string{"card-pick-merged": {"Approve, land it"}, "card-tap-closed": {"Approve", "Hold"}, "card-two-one-open": {"Merge both"}, "card-pick-open": {"Yes, land it"}},
		shows:     map[string][]int{"card-pick-merged": {1}, "card-tap-closed": {2}, "card-two-one-open": {1, 3}, "card-pick-open": {3}},
	}
	f := newCards(t, on, map[int]string{1: "MERGED", 2: "CLOSED"})
	ctx := context.Background()
	for _, card := range []string{"card-pick-merged", "card-two-one-open", "card-pick-open", "card-no-pr"} {
		if _, err := f.Until(ctx, card, "answered", ""); err != nil {
			t.Fatal(err)
		}
	}
	var out bytes.Buffer
	if err := f.Sweep(ctx, "board-lane", &out); err != nil {
		t.Fatal(err)
	}
	if want := []string{"card-pick-merged", "card-tap-closed"}; !reflect.DeepEqual(on.removed, want) {
		t.Fatalf("removed = %v, want %v", on.removed, want)
	}
	if want := []string{"Forge-AI/monorepo#1 is merged", "Forge-AI/monorepo#2 is closed"}; !reflect.DeepEqual(on.reasons, want) {
		t.Fatalf("remove-block reasons = %q, want %q for each Slack card", on.reasons, want)
	}
	if f.status.reads != 1 {
		t.Fatalf("status reads = %d, want one read for every card's PRs", f.status.reads)
	}
}

func TestACardWithdrawnWhenItsPRClosedComesBackUnderItsIDWhenThePRReopens(t *testing.T) {
	card := "card-tap-closed"
	on := &cardShell{cards: []string{card}, offers: map[string][]string{card: {"Approve", "Hold"}}, shows: map[string][]int{card: {2}}}
	f := newCards(t, on, map[int]string{2: "CLOSED"})
	ctx := context.Background()
	if err := f.Sweep(ctx, "board-lane", &bytes.Buffer{}); err != nil {
		t.Fatal(err)
	}
	on.cards = nil

	f.status.states[2] = "OPEN"
	var out bytes.Buffer
	if err := f.Revive(ctx, "board-lane", &out); err != nil {
		t.Fatal(err)
	}

	if len(on.asked) != 1 || !strings.Contains(on.asked[0], `"id":"`+card+`"`) || !strings.Contains(on.asked[0], `"id":"`+card+`-choice"`) {
		t.Fatalf("asked %q, want the withdrawn card's own block, choice id and all, asked again", on.asked)
	}
	if !strings.Contains(out.String(), "REVIVED "+card+": Forge-AI/monorepo#2 is open again") {
		t.Fatalf("out = %s", out.String())
	}
	if err := f.Revive(ctx, "board-lane", &bytes.Buffer{}); err != nil || len(on.asked) != 1 {
		t.Fatalf("a second pass asked %d times, %v; want the revived card left alone", len(on.asked), err)
	}

	on.cards, f.status.states[2] = []string{card}, "CLOSED"
	if err := f.Sweep(ctx, "board-lane", &bytes.Buffer{}); err != nil {
		t.Fatal(err)
	}
	if want := []string{card, card}; !reflect.DeepEqual(on.removed, want) {
		t.Fatalf("removed = %v, want the revived card withdrawn again on its second close", on.removed)
	}
}

func TestAReleaseOrderCardShowingAMergedPRStaysUpWhileItsTapCardComesDown(t *testing.T) {
	order, tap := "card-order-51296", "card-tap-51296-af4ce96"
	on := &cardShell{
		cards: []string{order, tap},
		offers: map[string][]string{
			order: {"Start the Mac release now, with the full cut after it", "Keep the dashboard out of full cuts until a Mac release ships"},
			tap:   {"Approve", "Hold"},
		},
		shows: map[string][]int{order: {51296}, tap: {51296}},
	}
	f := newCards(t, on, map[int]string{51296: "MERGED"})
	ctx := context.Background()
	for card, until := range map[string]string{order: "at:12h", tap: "pr:Forge-AI/monorepo#51296"} {
		if _, err := f.Until(ctx, card, until, "release-6"); err != nil {
			t.Fatal(err)
		}
	}
	if err := f.Sweep(ctx, "board-refresher", &bytes.Buffer{}); err != nil {
		t.Fatal(err)
	}
	if want := []string{tap}; !reflect.DeepEqual(on.removed, want) {
		t.Fatalf("removed = %v, want only %s: %s asks about the release, which outlives the merge", on.removed, tap, order)
	}
}

func TestAWithdrawnCardPushedAgainNeedsANewEnd(t *testing.T) {
	f := newCards(t, &cardShell{cards: []string{"merged-pr"}}, map[int]string{1: "MERGED"})
	ctx := context.Background()
	if _, err := f.Until(ctx, "merged-pr", "pr:1", ""); err != nil {
		t.Fatal(err)
	}
	if err := f.Sweep(ctx, "board-lane", &bytes.Buffer{}); err != nil {
		t.Fatal(err)
	}
	got, err := f.Missing(ctx, []string{"merged-pr"})
	if err != nil || !reflect.DeepEqual(got, []string{"merged-pr"}) {
		t.Fatalf("missing = %v, %v", got, err)
	}
}

func TestAFirstDrawStandsUntilASweepWithdrawsTheCard(t *testing.T) {
	f := newCards(t, &cardShell{cards: []string{"merged-pr"}}, map[int]string{1: "MERGED"})
	ctx := context.Background()
	first := f.clock
	marked := func(want time.Time) {
		t.Helper()
		if err := f.MarkDrawn(ctx, []string{"merged-pr"}); err != nil {
			t.Fatal(err)
		}
		got, err := f.Drawn(ctx, []string{"merged-pr"})
		if err != nil || !got["merged-pr"].Equal(want) {
			t.Fatalf("drawn = %v, %v; want %s", got, err, want)
		}
	}
	marked(first)
	f.clock = first.Add(2 * time.Hour)
	if _, err := f.Until(ctx, "merged-pr", "pr:1", ""); err != nil {
		t.Fatal(err)
	}
	marked(first)
	if err := f.Sweep(ctx, "board-lane", &bytes.Buffer{}); err != nil {
		t.Fatal(err)
	}
	f.clock = first.Add(3 * time.Hour)
	marked(f.clock)
	f.clock = first.Add(5 * time.Hour)
	marked(first.Add(3 * time.Hour))
}

func TestReadingWhenACardWasDrawnWritesNothing(t *testing.T) {
	f := newCards(t, &cardShell{cards: []string{"merged-pr"}}, map[int]string{1: "MERGED"})
	ctx := context.Background()
	got, err := f.Drawn(ctx, []string{"merged-pr"})
	if err != nil || len(got) != 0 {
		t.Fatalf("drawn = %v, %v; want no time for a card nothing marked", got, err)
	}
	if _, rows, err := f.rows(ctx); err != nil || rows["merged-pr"]["drawn"] != "" {
		t.Fatalf("card row %v, %v; want the read to leave no stamp", rows["merged-pr"], err)
	}
}

func TestRetroDueTimesReadThroughTheSameCheck(t *testing.T) {
	if why := Refusal("at:90m", cardsNow, KindAt); why != "" {
		t.Fatalf("at:90m refused: %s", why)
	}
	if why := Refusal("pr:1", cardsNow, KindAt); !strings.Contains(why, "is a pr condition") {
		t.Fatalf("pr:1 = %q", why)
	}
	if why := Refusal("", cardsNow); !strings.Contains(why, "no end condition") {
		t.Fatalf("empty = %q", why)
	}
}

func TestSweepKeepsAnAnsweredCardWhoseAnswerHasNoCcnRow(t *testing.T) {
	on := &cardShell{cards: []string{"card-rowed", "card-unrowed"}, answers: map[string]bool{"card-rowed": true, "card-unrowed": true}, rowed: map[string]bool{"card-rowed": true}}
	f := newCards(t, on, map[int]string{1: "MERGED"})
	ctx := context.Background()
	for _, card := range on.cards {
		if _, err := f.Until(ctx, card, "pr:1", ""); err != nil {
			t.Fatal(err)
		}
	}
	var out bytes.Buffer
	if err := f.Sweep(ctx, "board-lane", &out); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(on.removed, []string{"card-rowed"}) {
		t.Fatalf("removed = %v", on.removed)
	}
	if !strings.Contains(out.String(), "PROBLEM card-unrowed: the owner's answer on card-unrowed-choice has no ccn answer row") {
		t.Fatalf("out = %s", out.String())
	}
}

type launchdShell struct{ checkout string }

func (s launchdShell) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	if c.Dir != s.checkout {
		return shell.Result{Code: 1}, &shell.ExitError{Argv: c.Argv, Code: 1, Stderr: "ccx: pr status: / is not a checkout of Forge-AI/monorepo"}
	}
	return shell.Result{Stdout: `[{"number":50129,"state":"OPEN"}]`}, nil
}

func TestTheSweepReadsAPRCardsEndFromTheDrivesCheckoutUnderLaunchd(t *testing.T) {
	runner := launchdShell{checkout: "/co"}
	cards := CardsOf(&app.App{Runner: runner, CCX: ccx.CLI{Runner: runner}, Now: time.Now}, drive.Entry{Checkout: "/co", StateDir: t.TempDir()}, "s1", nil)
	facts, err := cards.facts(context.Background(), map[string]End{"card-tap-50129-655dbb7": {Kind: KindPR, Value: "Forge-AI/monorepo#50129"}}, &bytes.Buffer{})
	if err != nil {
		t.Fatal(err)
	}
	if facts.PRs["Forge-AI/monorepo#50129"] != "OPEN" {
		t.Fatalf("PRs = %v, want #50129 read open", facts.PRs)
	}
}

func TestALandingPickOnACardThatLinksNoPRWarns(t *testing.T) {
	choice := `{"id":"c-choice","type":"choice","options":[{"id":"c-1","label":"Land it"},{"id":"c-2","label":"Hold"}]}`
	for name, tc := range map[string]struct {
		card string
		warn bool
	}{
		"links no PR":     {`{"id":"card-c","type":"card","title":"CI output reuse","children":[` + choice + `]}`, true},
		"links its PR":    {`{"id":"card-c","type":"card","title":"CI output reuse","children":[` + choice + `,{"id":"c-pr","type":"pr","repo":"Forge-AI/monorepo","number":50346}]}`, false},
		"no landing pick": {`{"id":"card-c","type":"card","title":"Pause invites?","children":[{"id":"c-choice","type":"choice","options":[{"id":"c-1","label":"Pause"},{"id":"c-2","label":"Keep going"}]}]}`, false},
	} {
		t.Run(name, func(t *testing.T) {
			var warned bytes.Buffer
			warnUntiedLand(&warned, []byte(tc.card))
			if got := strings.Contains(warned.String(), `pick "Land it" reads as landing a PR`); got != tc.warn {
				t.Fatalf("warned %q, want a warning %v", warned.String(), tc.warn)
			}
		})
	}
}
