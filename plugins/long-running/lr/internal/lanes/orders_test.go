package lanes

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/owner"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/scaletest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const ordersRepo = "o/r"

func ownerLedger(t *testing.T, rows int) string {
	t.Helper()
	state := t.TempDir()
	var ledger strings.Builder
	ledger.WriteString("| id | time | message | routed | state |\n|---|---|---|---|---|\n")
	for i := 1; i <= rows; i++ {
		fmt.Fprintf(&ledger, "| m%d | Nov 3 4:50 AM | build order %d |  | addressed |\n", i, i)
	}
	if err := os.MkdirAll(filepath.Dir(owner.Ledger(state)), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(owner.Ledger(state), []byte(ledger.String()), 0o600); err != nil {
		t.Fatal(err)
	}
	return state
}

func carrier(t *testing.T, s *store.Store, name, orders string, state store.LaneState, prs ...int) {
	t.Helper()
	laneSaying(t, s, name, "Assigned orders: "+orders, state, prs...)
}

func laneSaying(t *testing.T, s *store.Store, name, lines string, state store.LaneState, prs ...int) {
	t.Helper()
	lane := storeLane(name)
	lane.Brief = filepath.Join(t.TempDir(), name+".md")
	if err := os.WriteFile(lane.Brief, []byte("# "+name+"\n"+lines+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := s.WantLane(lane, t0); err != nil {
		t.Fatal(err)
	}
	if state != store.LaneDesired {
		if err := s.SetLaneState(name, state, t0, "done", ""); err != nil {
			t.Fatal(err)
		}
	}
	if err := s.SetLanePRs(name, prs); err != nil {
		t.Fatal(err)
	}
}

func TestTheSupervisorsOwnReportIsNoClaimOnTheRowsItNames(t *testing.T) {
	s := memory(t)
	state := ownerLedger(t, 2)
	bus := &fakeBus{tail: []client.Record{
		{Lane: Self, Kind: "report", Text: "2 owner asks newly open: owner:m1, owner:m2."},
		{Lane: "install-desk-1", Kind: "report", Text: "owner:m2 installed"},
	}}

	orders, err := OpenOrders(context.Background(), bus, s, testDrive, ordersRepo, state)
	if err != nil {
		t.Fatal(err)
	}
	if got := owner.Only(orders, owner.Open); len(got) != 1 || got[0].ID != "m1" || got[0].Lane != "" {
		t.Fatalf("open = %+v, want m1 alone, with no lane", got)
	}
	if got := owner.Only(orders, owner.InProgress); len(got) != 1 || got[0].ID != "m2" || got[0].Lane != "install-desk-1" {
		t.Fatalf("in progress = %+v, want m2 on the lane that named it", got)
	}
}

func TestOpenOrdersListsEveryOrderNoLaneOrOpenPRCarriesAndADoneRecordLeavesItInProgress(t *testing.T) {
	s := memory(t)
	state := ownerLedger(t, 5)
	carrier(t, s, "live-1", "owner:m1", store.LaneDesired)
	carrier(t, s, "queued-1", "owner:m2", store.LaneRetired, 48001)
	carrier(t, s, "merged-1", "owner:m3", store.LaneRetired, 48002)
	for number, state := range map[int]machine.State{48001: machine.StateQueued, 48002: machine.StateLanded} {
		if err := s.PutPR(store.PR{Repo: ordersRepo, Number: number, Lane: "x", State: state, ReadAt: t0}); err != nil {
			t.Fatal(err)
		}
	}
	bus := &fakeBus{tail: []client.Record{{Lane: "install-desk-1", Kind: "done", Text: "owner:m5 installed and seen working"}}}

	orders, err := OpenOrders(context.Background(), bus, s, testDrive, ordersRepo, state)
	if err != nil {
		t.Fatal(err)
	}

	equal(t, owner.Tokens(orders), "owner:m3, owner:m4, owner:m5")
	equal(t, []string{orders[0].Status, orders[1].Status, orders[2].Status}, []string{owner.Open, owner.Open, owner.InProgress})
	equal(t, bus.query, client.Query{Drive: testDrive, Kinds: []string{"landed", "done", "report"}})
}

func TestARowALiveLaneOnlyQuotesStaysOpenWithNoLane(t *testing.T) {
	s := memory(t)
	state := ownerLedger(t, 2)
	laneSaying(t, s, "quoting-1", "His owner:m1 ruling is background.\n\nOwner orders: owner:m1 owner:m2", store.LaneDesired)
	carrier(t, s, "assigned-1", "owner:m2", store.LaneDesired)

	orders, err := OpenOrders(context.Background(), &fakeBus{}, s, testDrive, ordersRepo, state)
	if err != nil {
		t.Fatal(err)
	}

	equal(t, owner.Tokens(orders), "owner:m1")
	equal(t, []string{orders[0].Status, orders[0].Lane}, []string{owner.Open, ""})
}

func TestOpenOrdersReadsTheBusOnceWhateverTheLedgerHolds(t *testing.T) {
	for _, rows := range []int{1, scaletest.N()} {
		s := memory(t)
		bus := &fakeBus{}

		orders, err := OpenOrders(context.Background(), bus, s, testDrive, ordersRepo, ownerLedger(t, rows))
		if err != nil {
			t.Fatal(err)
		}

		equal(t, len(orders), rows)
		equal(t, bus.tails, 1)
	}
}

func TestADriveWithNoLedgerYetHasNoOpenOrders(t *testing.T) {
	bus := &fakeBus{}

	orders, err := OpenOrders(context.Background(), bus, memory(t), testDrive, ordersRepo, t.TempDir())

	if err != nil || orders != nil || bus.tails != 0 {
		t.Fatalf("orders %v, err %v, tails %d", orders, err, bus.tails)
	}
}
