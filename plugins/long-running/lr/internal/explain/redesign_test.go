package explain

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/board"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ledger/ledgertest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/orca"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

const (
	redesignDrive = "d1"
	repo          = "yasyf/cc-skills"
	correction    = "# The landing queue\n\nIt optimizes before it is correct.\n\n## What he corrected\n\nCorrectness comes first.\n"
)

type ownersBoard struct {
	cards   []string
	removed []string
}

func (b *ownersBoard) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	argv := strings.Join(c.Argv, " ")
	switch {
	case strings.Contains(argv, "ledger list --json"):
		return shell.Result{Stdout: "[]"}, nil
	case strings.Contains(argv, "ledger add"):
		return shell.Result{Stdout: `{"id":"c4rd5"}`}, nil
	case strings.Contains(argv, "outcomes --all"):
		blocks := make([]map[string]string, 0, len(b.cards))
		for _, card := range b.cards {
			blocks = append(blocks, map[string]string{"id": card, "type": "card"})
		}
		encoded, err := json.Marshal(map[string]any{"doc": map[string]any{"blocks": blocks}})
		return shell.Result{Stdout: string(encoded)}, err
	case strings.Contains(argv, "outcomes --block"):
		return shell.Result{Stdout: `{"answers":[]}`}, nil
	case strings.Contains(argv, "remove-block"):
		b.removed = append(b.removed, c.Argv[2])
		b.cards = slices.DeleteFunc(b.cards, func(card string) bool { return card == c.Argv[2] })
		return shell.Result{}, nil
	}
	return shell.Result{}, fmt.Errorf("unexpected call %s", argv)
}

type designLanes struct {
	registered map[string]bool
	orders     []orca.Order
}

func (l *designLanes) Registered(lane string) (bool, error) { return l.registered[lane], nil }

func (l *designLanes) Launch(_ context.Context, order orca.Order) (string, error) {
	l.orders = append(l.orders, order)
	l.registered[order.Lane] = true
	return launched, nil
}

type redesignFixture struct {
	board     *ownersBoard
	lanes     *designLanes
	owner     *told
	withdrawn []string
	dir       string
	state     string
}

func (f *redesignFixture) run(t *testing.T, pageStatus string, prs ...string) string {
	t.Helper()
	git := &shelltest.Fake{}
	git.Reply(pageStatus, "git", "-C", f.dir, "status")
	git.Reply("", "git")
	cards := board.Cards{
		Runner: f.board, Ledger: &ledgertest.Fake{}, Bus: redesignDrive, Checkout: "/co", Session: "s1", StateDir: f.state,
		Now: func() time.Time { return time.Date(2026, 11, 3, 5, 0, 0, 0, time.UTC) },
		Withdraw: func(_ context.Context, _, _, text string) error {
			f.withdrawn = append(f.withdrawn, text)
			return nil
		},
	}
	var out bytes.Buffer
	err := Redesign{
		Board: cards, Checkout: Checkout{Runner: git, Remote: "https://github.com/acme/design-docs.git", Dir: f.dir}, Host: &host{}, Owner: f.owner, Lanes: f.lanes,
		Drive: redesignDrive, Lane: "owner-relay", StateDir: f.state, Out: &out,
	}.Run(context.Background(), "Landing queue", []string{"queue-batching", "queue-retries"}, prs, []byte(correction), time.Date(2026, 11, 3, 5, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatal(err)
	}
	return out.String()
}

func heldPRs(t *testing.T) []int {
	t.Helper()
	open, err := client.Open(context.Background(), redesignDrive, "hold")
	if err != nil {
		t.Fatal(err)
	}
	held := make([]int, 0, len(open))
	for _, record := range open {
		held = append(held, bus.HeldBy(record, repo)...)
	}
	slices.Sort(held)
	return held
}

func TestRedesignWithdrawsTheSubsystemsCardsHoldsItsPRsHostsThePageAndOpensItsDesignLaneOnceAndLeavesTheRestAlone(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	elsewhere, err := client.Post(context.Background(), client.Draft{Drive: redesignDrive, Lane: "root", Kind: "hold", PRs: []string{repo + "#99"}, Text: "hold #99: waits on a release"})
	if err != nil {
		t.Fatal(err)
	}
	f := &redesignFixture{
		board: &ownersBoard{cards: []string{"queue-batching", "queue-retries", "dashboard-colors"}},
		lanes: &designLanes{registered: map[string]bool{}}, owner: &told{},
		dir: filepath.Join(t.TempDir(), "explainers"), state: t.TempDir(),
	}

	first := f.run(t, "?? one-pagers/landing-queue-redesign/index.html\n", repo+"#41", repo+"#42")

	if !slices.Equal(f.board.removed, []string{"queue-batching", "queue-retries"}) || !slices.Equal(f.board.cards, []string{"dashboard-colors"}) {
		t.Fatalf("removed %v, left %v", f.board.removed, f.board.cards)
	}
	if len(f.withdrawn) != 2 || !strings.Contains(f.withdrawn[0], "Withdrew queue-batching from the owner's board: the owner sent Landing queue back for a redesign from scratch.") {
		t.Fatalf("withdrawn %q", f.withdrawn)
	}
	if got := heldPRs(t); !slices.Equal(got, []int{41, 42, 99}) {
		t.Fatalf("held %v", got)
	}
	if len(f.owner.notices) != 1 || f.owner.notices[0].Link != "https://mac.tail1.ts.net:8665/one-pagers/landing-queue-redesign/" {
		t.Fatalf("announced %+v", f.owner.notices)
	}
	if len(f.lanes.orders) != 1 || f.lanes.orders[0].Lane != "landing-queue-design-1" || f.lanes.orders[0].Model != designer {
		t.Fatalf("orders %+v", f.lanes.orders)
	}
	brief, err := os.ReadFile(f.lanes.orders[0].Brief)
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"Register slice: lane=landing-queue-design-1 step=all", "Scale: ", "https://mac.tail1.ts.net:8665/one-pagers/landing-queue-redesign/", "- queue-retries", "- yasyf/cc-skills#42, held by #"} {
		if !strings.Contains(string(brief), want) {
			t.Errorf("brief lacks %q:\n%s", want, brief)
		}
	}
	for _, want := range []string{"WITHDRAWN queue-batching", "HELD yasyf/cc-skills#41 by #", "HOSTED https://", "OPENED landing-queue-design-1"} {
		if !strings.Contains(first, want) {
			t.Errorf("first run printed no %q:\n%s", want, first)
		}
	}

	again := f.run(t, "", repo+"#41", repo+"#42")

	if len(f.board.removed) != 2 || len(f.withdrawn) != 2 || len(f.owner.notices) != 1 || len(f.lanes.orders) != 1 {
		t.Fatalf("a rerun changed something: removed %v, withdrawn %d, announced %d, launched %d", f.board.removed, len(f.withdrawn), len(f.owner.notices), len(f.lanes.orders))
	}
	if got := heldPRs(t); !slices.Equal(got, []int{41, 42, 99}) {
		t.Fatalf("held after a rerun %v", got)
	}
	for _, want := range []string{"UNCHANGED queue-batching: not on the owner's board", "UNCHANGED yasyf/cc-skills#41: held by #", "UNCHANGED page https://", "UNCHANGED landing-queue-design-1"} {
		if !strings.Contains(again, want) {
			t.Errorf("rerun printed no %q:\n%s", want, again)
		}
	}
	if open, err := client.Open(context.Background(), redesignDrive, "hold"); err != nil || !slices.ContainsFunc(open, func(r client.Record) bool { return r.Seq == elsewhere.Seq }) {
		t.Fatalf("the other subsystem's hold %v: %v", open, err)
	}
}

func TestARedesignHoldStopsALandingUntilALiftNamesItAndARerunNeverHoldsItAgain(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	f := &redesignFixture{
		board: &ownersBoard{}, lanes: &designLanes{registered: map[string]bool{}}, owner: &told{},
		dir: filepath.Join(t.TempDir(), "explainers"), state: t.TempDir(),
	}
	f.run(t, "?? one-pagers/landing-queue-redesign/index.html\n", repo+"#41")
	open, err := client.Open(context.Background(), redesignDrive, "hold")
	if err != nil || len(open) != 1 {
		t.Fatalf("open holds %v: %v", open, err)
	}
	if !slices.Equal(bus.HeldBy(open[0], repo), []int{41}) {
		t.Fatalf("the hold names %v, so the landing reader sees no hold on #41", bus.HeldBy(open[0], repo))
	}

	if _, err := client.Post(context.Background(), client.Draft{Drive: redesignDrive, Lane: "root", Kind: "lift", Re: open[0].Seq, Text: "the owner approved the new design"}); err != nil {
		t.Fatal(err)
	}
	again := f.run(t, "", repo+"#41")

	if got := heldPRs(t); len(got) != 0 {
		t.Fatalf("held after the lift and a rerun: %v", got)
	}
	if !strings.Contains(again, "UNCHANGED yasyf/cc-skills#41: its redesign hold "+open[0].Name()+" was lifted") {
		t.Fatalf("rerun printed:\n%s", again)
	}
}
