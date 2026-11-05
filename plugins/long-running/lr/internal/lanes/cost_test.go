package lanes

import (
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const mediumDesk = "fidelity-desk-29"

func TestASuccessorTakesTheDeskTablesCostAndALaneAboveHighDropsToHigh(t *testing.T) {
	plain := writeBrief(t)
	picked := filepath.Join(t.TempDir(), "picked.md")
	if err := os.WriteFile(picked, []byte("# brief\nOwner pick: owner:m836\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, tt := range []struct {
		name string
		old  store.Lane
		want Cost
	}{
		{"the fidelity desk leaves xhigh for the table", store.Lane{Name: mediumDesk, Model: "opus", Effort: "xhigh", Brief: plain}, Cost{"opus", "medium"}},
		{"its shard does too", store.Lane{Name: "fidelity-shard-5", Model: "opus", Effort: "xhigh", Brief: plain}, Cost{"opus", "medium"}},
		{"a desk's first successor reads the same row", store.Lane{Name: "board-refresher", Model: "opus", Effort: "high", Brief: plain}, Cost{"opus", "medium"}},
		{"a sonnet desk takes its own row", store.Lane{Name: "alerts-desk-3", Model: "sonnet", Effort: "xhigh", Brief: plain}, Cost{"sonnet", "low"}},
		{"the table beats a pick the brief carried", store.Lane{Name: "red-sweep-4", Model: "opus", Effort: "xhigh", Brief: picked}, Cost{"opus", "medium"}},
		{"a desk on another model than its row keeps that model", store.Lane{Name: "drive-watchdog-4", Model: "sonnet", Effort: "low", Brief: plain}, Cost{"sonnet", "low"}},
		{"the watchdog is an opus desk and reads the table", store.Lane{Name: "drive-watchdog-4", Model: "opus", Effort: "high", Brief: plain}, Cost{"opus", "medium"}},
		{"a lane above high drops to high", store.Lane{Name: lane2, Model: "opus", Effort: "xhigh", Brief: plain}, Cost{"opus", "high"}},
		{"max drops too", store.Lane{Name: lane2, Model: "opus", Effort: "max", Brief: plain}, Cost{"opus", "high"}},
		{"a sonnet lane keeps its model", store.Lane{Name: lane2, Model: "sonnet", Effort: "xhigh", Brief: plain}, Cost{"sonnet", "high"}},
		{"a lane under high keeps its effort", store.Lane{Name: lane2, Model: "sonnet", Effort: "medium", Brief: plain}, Cost{"sonnet", "medium"}},
		{"his pick keeps xhigh", store.Lane{Name: lane2, Model: "opus", Effort: "xhigh", Brief: picked}, Cost{"opus", "xhigh"}},
		{"the codex incident route keeps its tier", store.Lane{Name: "dev-red-fix", Model: "incident", Effort: "xhigh", Brief: plain}, Cost{"incident", "xhigh"}},
		{"the opus incident route keeps its tier", store.Lane{Name: "dev-red-fix", Model: "incident-opus", Effort: "xhigh", Brief: plain}, Cost{"incident-opus", "xhigh"}},
		{"the communicator is exempt", store.Lane{Name: OwnerRelay, Model: "opus", Effort: "xhigh", Brief: plain}, Cost{"opus", "xhigh"}},
	} {
		t.Run(tt.name, func(t *testing.T) {
			equal(t, successorCost(tt.old), tt.want)
		})
	}
}

func adoptedDesk(t *testing.T, s *store.Store) *fake {
	t.Helper()
	f := newFake(t, []Record{spawned(1, mediumDesk, writeBrief(t), t0.Add(-time.Hour), map[string]string{"model": "opus", "effort": "xhigh"})}, []Row{who(mediumDesk, true, t0.Add(-mins(1)))})
	tick(t, s, f, t0.Add(-mins(1)))
	if err := s.SetLaneHandoff(mediumDesk, "9f3c2e1", time.Time{}); err != nil {
		t.Fatal(err)
	}
	if err := s.SetLanePRs(mediumDesk, []int{46001}); err != nil {
		t.Fatal(err)
	}
	return f
}

func TestAStoppedDeskRelaunchesAtTheDeskTablesCostNotAtItsPredecessors(t *testing.T) {
	s := memory(t)
	f := adoptedDesk(t, s)
	f.peers = []Row{who(mediumDesk, false, t0.Add(-mins(20)))}

	tick(t, s, f, t0)

	equal(t, f.launches, []string{"fidelity-desk-30"})
	equal(t, f.costs["fidelity-desk-30"], Cost{"opus", "medium"})
	relaunched := row(t, s, "fidelity-desk-30")
	equal(t, Cost{relaunched.Model, relaunched.Effort}, Cost{"opus", "medium"})
}

func TestADeskStoodDownForASuccessorRecordsTheDeskTablesCost(t *testing.T) {
	s := memory(t)
	f := adoptedDesk(t, s)
	f.feed = append(f.feed, ask(2, "stand-down", mediumDesk, t0, nil, "", map[string]string{"successor": "fidelity-desk-30", "last-read": "1"}))
	f.peers = []Row{who("fidelity-desk-30", true, t0)}

	tick(t, s, f, t0)

	successor := row(t, s, "fidelity-desk-30")
	equal(t, Cost{successor.Model, successor.Effort}, Cost{"opus", "medium"})
}

func TestAStoppedXhighLaneRelaunchesAtHighSoTheRunnerTakesIt(t *testing.T) {
	s, brief := memory(t), writeBrief(t)
	f := adopted(t, s, brief, "9f3c2e1", 46001)
	f.peers = []Row{who(lane2, false, t0.Add(-mins(20)))}

	tick(t, s, f, t0)

	equal(t, f.costs["conflicts-3"], Cost{"opus", "high"})
}
