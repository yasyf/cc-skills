package landing_test

import (
	"context"
	"encoding/json"
	"fmt"
	"reflect"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/board"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/landing"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

const (
	tapThread  = "1792842193.708029"
	tapSession = "s-root"
	tapPR      = 48165
)

type tapBus struct {
	drawn     []landing.TapDrawn
	writes    []board.TapCard
	blockers  []landing.TapHead
	said      []string
	drawReads int
}

func (b *tapBus) Drawn(_ context.Context, after int64, _ time.Time) ([]landing.TapDrawn, error) {
	b.drawReads++
	fresh := []landing.TapDrawn{}
	for _, record := range b.drawn {
		if record.Seq > after {
			fresh = append(fresh, record)
		}
	}
	return fresh, nil
}

func (b *tapBus) Write(_ context.Context, session string, card board.TapCard) error {
	if session != tapSession {
		return fmt.Errorf("wrote to session %q", session)
	}
	b.writes = append(b.writes, card)
	return nil
}

func (b *tapBus) Blocker(_ context.Context, head landing.TapHead, text string) error {
	b.blockers = append(b.blockers, head)
	b.said = append(b.said, text)
	return nil
}

func (b *tapBus) card(seq int64, number int, sha, permalink string) {
	b.drawn = append(b.drawn, landing.TapDrawn{Seq: seq, Links: []board.TapLink{{PR: number, Head: sha, Permalink: permalink}}})
}

type tapRig struct {
	tapper    landing.Tapper
	bus       *tapBus
	pulls     []board.Pull
	pullReads int
}

func newTapRig(t *testing.T, pulls ...board.Pull) *tapRig {
	t.Helper()
	rig := &tapRig{bus: &tapBus{}, pulls: pulls}
	rig.tapper = landing.Tapper{
		Store: newStore(t), Bus: rig.bus, Repo: repo, Thread: tapThread,
		Pulls: func(context.Context) ([]board.Pull, error) {
			rig.pullReads++
			return rig.pulls, nil
		},
	}
	return rig
}

func (r *tapRig) ask(t *testing.T, at time.Time, ask landing.TapAsk) landing.TapReport {
	t.Helper()
	ask.Session = tapSession
	report, err := r.tapper.Ask(context.Background(), ask, at)
	if err != nil {
		t.Fatal(err)
	}
	return report
}

func tapPull(number int, sha, base string) board.Pull {
	return board.Pull{
		Number: number, Head: sha, Base: base, Branch: fmt.Sprintf("b/%d", number),
		Title: fmt.Sprintf("api: ✨ change %d", number), Body: "## Summary\n\nThe change.\n\n## Risk profile\n\nSmall.",
	}
}

func waiting(heads ...landing.TapHead) landing.TapAsk {
	return landing.TapAsk{Waiting: heads}
}

func rowNumbers(card board.TapCard) []int {
	numbers := []int{}
	for _, block := range card.Children {
		for _, group := range block.Groups {
			for _, row := range group.PRs {
				numbers = append(numbers, row.Number)
			}
		}
	}
	slices.Sort(numbers)
	return numbers
}

func TestAWaitingPRGetsItsCardInThePassThatMarksItWaiting(t *testing.T) {
	rig := newTapRig(t, tapPull(tapPR, head, "dev"), tapPull(2, moved, "dev"))
	report := rig.ask(t, t0, waiting(landing.TapHead{PR: tapPR, Head: head}))
	if len(rig.bus.writes) != 1 {
		t.Fatalf("writes = %d, want one in this pass", len(rig.bus.writes))
	}
	if got := rowNumbers(rig.bus.writes[0]); !slices.Equal(got, []int{tapPR}) {
		t.Fatalf("rows = %v", got)
	}
	if block := rig.bus.writes[0].Children[0]; block.Slack != tapThread || block.Repo != repo {
		t.Fatalf("block = %+v", block)
	}
	if report.Drew != 1 || len(report.Missing) != 0 {
		t.Fatalf("report = %+v", report)
	}
}

func TestAWaitingPRWithACardAtItsHeadIsClean(t *testing.T) {
	rig := newTapRig(t, tapPull(tapPR, head, "dev"))
	ask := waiting(landing.TapHead{PR: tapPR, Head: head})
	rig.ask(t, t0, ask)
	rig.bus.card(1, tapPR, head, "https://slack/p1")
	report := rig.ask(t, t0.Add(time.Hour), ask)
	if len(rig.bus.blockers) != 0 || len(report.Missing) != 0 {
		t.Fatalf("blockers = %v, report = %+v", rig.bus.blockers, report)
	}
	cards, err := rig.tapper.Store.Facts(repo, tapPR, head)
	if err != nil {
		t.Fatal(err)
	}
	if cards[landing.SlackCardFact] != "https://slack/p1" {
		t.Fatalf("facts = %v, want the permalink recorded against the PR and head", cards)
	}
}

func TestAWaitingPRWithNoCardRaisesOneBlockerNotOnePerPass(t *testing.T) {
	rig := newTapRig(t, tapPull(tapPR, head, "dev"))
	ask := waiting(landing.TapHead{PR: tapPR, Head: head})
	rig.ask(t, t0, ask)
	if len(rig.bus.blockers) != 0 {
		t.Fatalf("raised before the pass that owed the card ended: %v", rig.bus.blockers)
	}
	rig.ask(t, t0.Add(4*time.Minute), ask)
	rig.ask(t, t0.Add(8*time.Minute), ask)
	if want := []landing.TapHead{{PR: tapPR, Head: head}}; !reflect.DeepEqual(rig.bus.blockers, want) {
		t.Fatalf("blockers = %v, want %v", rig.bus.blockers, want)
	}
	if said := rig.bus.said[0]; !strings.Contains(said, fmt.Sprintf("#%d at %s", tapPR, head[:7])) || !strings.Contains(said, "draw-taps") {
		t.Fatalf("the blocker names no PR, head or step: %q", said)
	}
}

func TestANewHeadWithNoCardRaisesAgainEvenWithACardAtTheOldHead(t *testing.T) {
	rig := newTapRig(t, tapPull(tapPR, head, "dev"))
	rig.ask(t, t0, waiting(landing.TapHead{PR: tapPR, Head: head}))
	rig.bus.card(1, tapPR, head, "https://slack/p1")
	rig.ask(t, t0.Add(4*time.Minute), waiting(landing.TapHead{PR: tapPR, Head: head}))
	rig.pulls = []board.Pull{tapPull(tapPR, moved, "dev")}
	next := waiting(landing.TapHead{PR: tapPR, Head: moved})
	rig.ask(t, t0.Add(5*time.Minute), next)
	if len(rig.bus.writes) != 2 || rig.bus.writes[1].Children[0].Groups[0].PRs[0].Head != moved {
		t.Fatalf("writes = %+v, want the new head drawn", rig.bus.writes)
	}
	rig.ask(t, t0.Add(9*time.Minute), next)
	if want := []landing.TapHead{{PR: tapPR, Head: moved}}; !reflect.DeepEqual(rig.bus.blockers, want) {
		t.Fatalf("blockers = %v, want %v", rig.bus.blockers, want)
	}
}

func TestATappedPRIsNotFlaggedAndLeavesTheCard(t *testing.T) {
	rig := newTapRig(t, tapPull(tapPR, head, "dev"))
	rig.ask(t, t0, waiting(landing.TapHead{PR: tapPR, Head: head}))
	rig.ask(t, t0.Add(4*time.Minute), landing.TapAsk{Tapped: []int{tapPR}})
	if len(rig.bus.blockers) != 0 {
		t.Fatalf("blockers = %v", rig.bus.blockers)
	}
	if len(rig.bus.writes) != 2 || len(rig.bus.writes[1].Children) != 0 {
		t.Fatalf("writes = %+v, want an empty card that takes it down", rig.bus.writes)
	}
}

func TestAnUnchangedPassWritesNothingAndReadsNoPulls(t *testing.T) {
	rig := newTapRig(t, tapPull(tapPR, head, "dev"))
	ask := waiting(landing.TapHead{PR: tapPR, Head: head})
	rig.ask(t, t0, ask)
	rig.ask(t, t0.Add(time.Minute), ask)
	if len(rig.bus.writes) != 1 || rig.pullReads != 1 {
		t.Fatalf("writes = %d, pull reads = %d", len(rig.bus.writes), rig.pullReads)
	}
}

func TestAChildWaitsWhileItsParentCannotLandAndSaysSo(t *testing.T) {
	parent, child := tapPull(10, head, "dev"), tapPull(11, moved, "b/10")
	rig := newTapRig(t, parent, child)
	ask := waiting(landing.TapHead{PR: 11, Head: moved})
	report := rig.ask(t, t0, ask)
	if want := []landing.TapParked{{PR: 11, Head: moved, Below: 10}}; !reflect.DeepEqual(report.Parked, want) {
		t.Fatalf("parked = %+v", report.Parked)
	}
	if len(rig.bus.writes) != 0 {
		t.Fatalf("writes = %+v", rig.bus.writes)
	}
	rig.ask(t, t0.Add(4*time.Minute), ask)
	if len(rig.bus.blockers) != 0 {
		t.Fatalf("a parked child raised %v", rig.bus.blockers)
	}
	ask.Tapped = []int{10}
	rig.ask(t, t0.Add(5*time.Minute), ask)
	if len(rig.bus.writes) != 1 || !strings.Contains(rig.bus.writes[0].Children[0].Groups[0].PRs[0].Blocker, "#10") {
		t.Fatalf("writes = %+v, want the child drawn, naming its parent", rig.bus.writes)
	}
}

func TestAChildAboveAParkedParentIsParkedToo(t *testing.T) {
	rig := newTapRig(t, tapPull(10, head, "dev"), tapPull(11, moved, "b/10"), tapPull(12, "1234567", "b/11"))
	report := rig.ask(t, t0, waiting(landing.TapHead{PR: 11, Head: moved}, landing.TapHead{PR: 12, Head: "1234567"}))
	if len(rig.bus.writes) != 0 || len(report.Parked) != 2 {
		t.Fatalf("writes = %+v, parked = %+v", rig.bus.writes, report.Parked)
	}
}

func TestAStackDrawsBottomFirstWhenEveryPieceWaits(t *testing.T) {
	rig := newTapRig(t, tapPull(10, head, "dev"), tapPull(11, moved, "b/10"))
	rig.ask(t, t0, waiting(landing.TapHead{PR: 11, Head: moved}, landing.TapHead{PR: 10, Head: head}))
	group := rig.bus.writes[0].Children[0].Groups[0]
	if group.ID != "stack-10" || group.PRs[0].Number != 10 || group.PRs[1].Number != 11 {
		t.Fatalf("group = %+v", group)
	}
}

func TestARowOnAnotherSlackBoundBlockStandsAsTheCard(t *testing.T) {
	rig := newTapRig(t, tapPull(tapPR, head, "dev"))
	ask := waiting(landing.TapHead{PR: tapPR, Head: head})
	ask.Bound = []landing.BoundRow{{PR: tapPR, Head: head, Block: "merge-prs", Thread: tapThread}}
	rig.ask(t, t0, ask)
	rig.ask(t, t0.Add(4*time.Minute), ask)
	if len(rig.bus.writes) != 0 || len(rig.bus.blockers) != 0 {
		t.Fatalf("writes = %+v, blockers = %v", rig.bus.writes, rig.bus.blockers)
	}
	facts, err := rig.tapper.Store.Facts(repo, tapPR, head)
	if err != nil {
		t.Fatal(err)
	}
	if facts[landing.SlackCardFact] != "thread "+tapThread {
		t.Fatalf("facts = %v", facts)
	}
}

func TestItsOwnRowOnTheBoundBlockIsNoEvidenceOfASlackCard(t *testing.T) {
	rig := newTapRig(t, tapPull(tapPR, head, "dev"))
	ask := waiting(landing.TapHead{PR: tapPR, Head: head})
	ask.Bound = []landing.BoundRow{{PR: tapPR, Head: head, Block: board.TapBlockID, Thread: tapThread}}
	rig.ask(t, t0, ask)
	rig.ask(t, t0.Add(4*time.Minute), ask)
	if len(rig.bus.blockers) != 1 {
		t.Fatalf("blockers = %v, want one: the row reached the board, no card reached Slack", rig.bus.blockers)
	}
}

func TestAHeadGitHubHasMovedPastIsNeitherDrawnNorFlagged(t *testing.T) {
	rig := newTapRig(t, tapPull(tapPR, moved, "dev"))
	ask := waiting(landing.TapHead{PR: tapPR, Head: head})
	rig.ask(t, t0, ask)
	rig.ask(t, t0.Add(4*time.Minute), ask)
	if len(rig.bus.writes) != 0 || len(rig.bus.blockers) != 0 {
		t.Fatalf("writes = %+v, blockers = %v", rig.bus.writes, rig.bus.blockers)
	}
}

func TestAPassCostsTheSameReadsAndOneWriteAtOneAndAThousandPRs(t *testing.T) {
	for _, n := range []int{1, 1000} {
		pulls := make([]board.Pull, 0, n)
		heads := make([]landing.TapHead, 0, n)
		for number := 1; number <= n; number++ {
			pulls = append(pulls, tapPull(number, head, "dev"))
			heads = append(heads, landing.TapHead{PR: number, Head: head})
		}
		rig := newTapRig(t, pulls...)
		rig.ask(t, t0, waiting(heads...))
		if rig.pullReads != 1 || rig.bus.drawReads != 1 || len(rig.bus.writes) != 1 {
			t.Fatalf("n=%d: pull reads %d, record reads %d, writes %d", n, rig.pullReads, rig.bus.drawReads, len(rig.bus.writes))
		}
		if got := rowNumbers(rig.bus.writes[0]); len(got) != n {
			t.Fatalf("n=%d: %d rows", n, len(got))
		}
	}
}

func TestDrawTapsRecordsEachRowsPermalinkAndSkipsAGroupThatPostedNothing(t *testing.T) {
	card := board.BuildTapCard(repo, tapThread, []board.TapWait{
		{Pull: board.Pull{Number: 1, Title: "api: ✨ one", Body: ""}, Head: head},
		{Pull: board.Pull{Number: 2, Title: "sand: ✨ two", Body: ""}, Head: moved},
	})
	runner := &shelltest.Fake{}
	runner.Reply("url: http://board\nslack: scope-api https://slack/p1\nslack: scope-sand nothing open, never posted\n", "cc-present", "update-block")
	links, said, err := landing.DrawTaps(context.Background(), runner, tapSession, card)
	if err != nil {
		t.Fatal(err)
	}
	if want := []board.TapLink{{PR: 1, Head: head, Permalink: "https://slack/p1"}}; !reflect.DeepEqual(links, want) {
		t.Fatalf("links = %+v", links)
	}
	if said != "drew 2 rows, 1 with a Slack card" {
		t.Fatalf("said = %q", said)
	}
	call := runner.Calls()[0]
	if !slices.Equal(call.Argv, []string{"cc-present", "update-block", "-", "--round", "current", "--session", tapSession}) {
		t.Fatalf("argv = %v", call.Argv)
	}
	var sent board.TapCard
	if err := json.Unmarshal([]byte(call.Stdin), &sent); err != nil || sent.ID != board.TapCardID {
		t.Fatalf("stdin = %s, %v", call.Stdin, err)
	}
}

func TestDrawTapsSurfacesAFailedPush(t *testing.T) {
	card := board.BuildTapCard(repo, tapThread, []board.TapWait{{Pull: board.Pull{Number: 1, Title: "api: ✨ one"}, Head: head}})
	runner := &shelltest.Fake{}
	runner.Fail(1, "group api: slack refused", "cc-present", "update-block")
	links, _, err := landing.DrawTaps(context.Background(), runner, tapSession, card)
	if err == nil {
		t.Fatalf("want the failure surfaced, got links %+v", links)
	}
}

func TestDrawTapsTakesAnEmptyCardDown(t *testing.T) {
	runner := &shelltest.Fake{}
	runner.Reply("", "cc-present", "remove-block")
	links, _, err := landing.DrawTaps(context.Background(), runner, tapSession, board.BuildTapCard(repo, tapThread, nil))
	if err != nil || len(links) != 0 {
		t.Fatalf("links = %+v, err = %v", links, err)
	}
	if call := runner.Calls()[0]; !slices.Equal(call.Argv, []string{"cc-present", "remove-block", board.TapCardID, "--session", tapSession}) {
		t.Fatalf("argv = %v", call.Argv)
	}
}
