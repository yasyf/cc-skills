package lanes

import (
	"fmt"
	"slices"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/scaletest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	blockedRepo  = "o/r"
	blockingPR   = 51849
	blockingHead = "c059c6accbef800640c08e9ad645fa4a626c0977"
	pushedHead   = "d035cbe1f7a1e04fd2ce0d5b7c4fa5e1c2a9b3d4"
	ownerOfPRs   = "owner of signal-storage-retro-2's open PRs until each lands"
)

func gitHubRead(seq int64, at time.Time, state, ci, head string) Record {
	read := rec(seq, "pr-read", "github", at, "sandsql-storage: settle cleanup only for its current revision", Self).withRefs(prs(blockingPR))
	read.Fields.Attrs = map[string]string{"record": "pr.read", "repo": blockedRepo, "state": state, "ci": ci, "head": head}
	return read
}

func blockingRow(t *testing.T, s *store.Store) {
	t.Helper()
	if err := s.PutReviewRows([]store.ReviewRow{{Repo: blockedRepo, Number: blockingPR, State: prrow.StateOpen}}); err != nil {
		t.Fatal(err)
	}
}

func passesOver(t *testing.T, s *store.Store, f *fake, from time.Time, span time.Duration, names ...string) {
	t.Helper()
	for at := from; at.Before(from.Add(span)); at = at.Add(10 * time.Minute) {
		f.peers = make([]Row, 0, len(names))
		for _, name := range names {
			f.peers = append(f.peers, onRoster(who(name, true, at)))
		}
		tick(t, s, f, at)
	}
}

func TestALaneBlockedOnAnotherDrivePRDrawsNoClockWakeAndOneWakeWhenThatPRChanges(t *testing.T) {
	changed := t0.Add(150 * time.Minute)
	cases := map[string]struct {
		change Record
		says   string
	}{
		"lands":           {rec(5, Self, kindLanded, changed, "LANDED #51849 at c059c6a: merged into dev", Root).withRefs(prs(blockingPR)), "supervisor's landed fired pr:51849, the gate you parked on"},
		"goes red":        {gitHubRead(5, changed, prrow.StateOpen, prrow.CIRed, pushedHead), "GitHub's read of #51849 (open, CI red) fired pr:51849, the gate you parked on"},
		"closes unmerged": {gitHubRead(5, changed, prrow.StateClosed, prrow.CIGreen, blockingHead), "GitHub's read of #51849 (closed, CI green) fired pr:51849, the gate you parked on"},
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			s := memory(t)
			f := adopted(t, s, writeBrief(t), "", 49305)
			f.deps.Repo = blockedRepo
			if err := s.SetLaneStanding(lane2, ownerOfPRs); err != nil {
				t.Fatal(err)
			}
			blockingRow(t, s)
			f.states = map[int]string{49305: Broken}
			f.feed = append(f.feed,
				gitHubRead(2, t0, prrow.StateOpen, prrow.CIGreen, blockingHead),
				rec(3, lane2, "blocked", t0, "Unchanged: #51849 is still open, so #49305 has nothing to push, as #539063 said.").withRefs(prs(49305)),
			)
			f.wakes, f.pages = nil, nil

			passesOver(t, s, f, t0, 2*time.Hour, lane2)
			equal(t, row(t, s, lane2).Awaits, "pr:49305 pr:51849")

			f.feed = append(f.feed, gitHubRead(4, t0.Add(2*time.Hour), prrow.StateOpen, prrow.CIGreen, blockingHead))
			passesOver(t, s, f, t0.Add(2*time.Hour), 30*time.Minute, lane2)
			equal(t, len(f.wakes), 0)

			f.feed = append(f.feed, c.change)
			passesOver(t, s, f, changed, time.Hour, lane2)
			equal(t, f.wakes, []string{lane2})
			contains(t, pageText(f.pages[0], "why"), c.says)
		})
	}
}

func TestABlockedRecordGatesTheOwnPRsItNamesInTextAndNoPRGitHubAlreadyReadClosed(t *testing.T) {
	cases := map[string]struct {
		read   Record
		awaits string
	}{
		"open":   {gitHubRead(2, t0, prrow.StateOpen, prrow.CIGreen, blockingHead), "pr:49305 pr:51849"},
		"closed": {gitHubRead(2, t0, prrow.StateClosed, prrow.CIGreen, blockingHead), ""},
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			s := memory(t)
			f := adopted(t, s, writeBrief(t), "", 49305)
			f.deps.Repo = blockedRepo
			if err := s.SetLaneStanding(lane2, ownerOfPRs); err != nil {
				t.Fatal(err)
			}
			blockingRow(t, s)
			f.feed = append(f.feed, c.read, rec(3, lane2, "blocked", t0, "#49305 waits on #51849"))
			f.peers = []Row{onRoster(who(lane2, true, t0))}

			tick(t, s, f, t0)

			equal(t, row(t, s, lane2).Awaits, c.awaits)
		})
	}
}

func TestABlockedRecordParksOnlyTheOwnPRsInItsRefsAndFoldsItsTextOnlyWithNoRefs(t *testing.T) {
	cases := map[string]struct {
		refs   Refs
		awaits string
	}{
		"refs name one own PR": {prs(49305), "pr:49305 pr:51849"},
		"no refs":              {Refs{}, "pr:49305 pr:51995 pr:51849"},
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			s := memory(t)
			f := adopted(t, s, writeBrief(t), "", 49305, 51995)
			f.deps.Repo = blockedRepo
			if err := s.SetLaneStanding(lane2, ownerOfPRs); err != nil {
				t.Fatal(err)
			}
			blockingRow(t, s)
			f.feed = append(f.feed,
				gitHubRead(2, t0, prrow.StateOpen, prrow.CIGreen, blockingHead),
				rec(3, lane2, "blocked", t0, "#49305 waits on #51849; #51995 waits only on its CI").withRefs(c.refs),
			)
			f.peers = []Row{onRoster(who(lane2, true, t0))}

			tick(t, s, f, t0)

			equal(t, row(t, s, lane2).Awaits, c.awaits)
		})
	}
}

func TestBlockedLanesWakeOncePerBlockingPRChangeAndNeverOnAClockAtOneLaneAndAtMany(t *testing.T) {
	counts := func(n int) [2][]int {
		s := memory(t)
		f := adopted(t, s, writeBrief(t), "")
		f.deps.Repo = blockedRepo
		blockingRow(t, s)
		f.feed = append(f.feed, gitHubRead(2, t0, prrow.StateOpen, prrow.CIGreen, blockingHead))
		names := make([]string, n)
		for i := range n {
			names[i] = fmt.Sprintf("blocked%03d", i)
			own := 50000 + i
			if _, err := s.WantLane(store.Lane{Name: names[i], Brief: "/briefs/x.md", Model: "opus", Effort: "xhigh", Role: "lane", State: store.LaneDesired, PRs: []int{own}, Standing: ownerOfPRs}, t0.Add(-time.Hour)); err != nil {
				t.Fatal(err)
			}
			f.states[own] = Broken
			f.feed = append(f.feed, rec(int64(3+i), names[i], "blocked", t0, "waits on #51849").withRefs(prs(own)))
		}
		f.wakes = nil
		perLane := func() []int {
			woken := make([]int, n)
			for i, name := range names {
				woken[i] = len(slices.DeleteFunc(slices.Clone(f.wakes), func(lane string) bool { return lane != name }))
			}
			slices.Sort(woken)
			return slices.Compact(woken)
		}

		passesOver(t, s, f, t0, 2*time.Hour, names...)
		clock := perLane()
		f.feed = append(f.feed, rec(int64(3+n), Self, kindLanded, t0.Add(2*time.Hour), "LANDED #51849 at c059c6a: merged into dev", Root).withRefs(prs(blockingPR)))
		passesOver(t, s, f, t0.Add(2*time.Hour), time.Hour, names...)
		return [2][]int{clock, perLane()}
	}

	one := counts(1)
	equal(t, one, [2][]int{{0}, {1}})
	equal(t, counts(scaletest.N()), one)
}
