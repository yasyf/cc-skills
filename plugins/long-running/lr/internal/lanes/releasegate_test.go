package lanes

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"os"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bkread"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const gatedRelease = "releases/2026-11-02/12192"

var (
	gateReached = time.Date(2026, 11, 2, 18, 32, 0, 0, pacificZone)
	ownerOrder  = time.Date(2026, 11, 2, 18, 36, 0, 0, pacificZone)
)

func orderRecord(t *testing.T, seq int64, lane string, at time.Time, attrs string) client.Record {
	t.Helper()
	var row client.Record
	body := fmt.Sprintf(`{"seq":%d,"lane":%q,"kind":"release","at":%q,"text":"12192 started","fields":{"attrs":%s}}`, seq, lane, at.Format(time.RFC3339), attrs)
	if err := json.Unmarshal([]byte(body), &row); err != nil {
		t.Fatal(err)
	}
	return row
}

func gateJSON(state string, at time.Time) string {
	return fmt.Sprintf(`{"record":{"hold":{"job":"job-1"}},"build":{"state":"running","gate":{"state":%q,"at":%q}}}`, state, at.Format(time.RFC3339))
}

func newReleaseGates(status *string, rows ...client.Record) (*ReleaseGates, *fakeBus) {
	bus := &fakeBus{tail: rows}
	env := Env{Bus: bus, Config: config.Config{Drive: testDrive, Checkout: "/checkout"}, Runner: scripted(func(argv []string) (shell.Result, error) {
		if argv[0] == tokenTool {
			return shell.Result{Stdout: "read-token\n"}, nil
		}
		return shell.Result{Stdout: *status}, nil
	})}
	return &ReleaseGates{Env: env, ReleaseCLI: []string{"tools/ci", "release"}, Token: bkread.Token{Command: []string{tokenTool}}, Log: slog.Default()}, bus
}

func pass(t *testing.T, check *ReleaseGates, at time.Time) {
	t.Helper()
	if err := check.Check(context.Background(), at); err != nil {
		t.Fatal(err)
	}
}

func TestARelease12192StillAtItsGateFiveMinutesAfterHisOrderDrawsOneBlockerNamingReleaseGateAndLane(t *testing.T) {
	status := gateJSON(gateWaiting, gateReached)
	attrs := fmt.Sprintf(`{"release":%q,"order":"d847000"}`, gatedRelease)
	check, bus := newReleaseGates(&status, orderRecord(t, 1, "release-5", ownerOrder, attrs))

	pass(t, check, ownerOrder.Add(gateGrace-time.Minute))
	equal(t, len(bus.posted), 0)

	pass(t, check, ownerOrder.Add(gateGrace))
	equal(t, len(bus.posted), 1)
	blocker := bus.posted[0]
	equal(t, blocker.Kind, blockerKind)
	equal(t, blocker.To, []string{Root})
	equal(t, blocker.ID, "release-gate:"+gatedRelease+":job-1")
	for _, named := range []string{gatedRelease, "approve gate (job job-1) for 9 min", "past owner order d847000;", "release-5 approves it now", "tools/ci release approve " + gatedRelease} {
		if !strings.Contains(blocker.Text, named) {
			t.Fatalf("%q does not name %q", blocker.Text, named)
		}
	}

	pass(t, check, ownerOrder.Add(gateGrace+time.Minute))
	equal(t, len(bus.posted), 1)
}

func TestAReleaseApprovedFourMinutesAfterHisOrderDrawsNoBlocker(t *testing.T) {
	status := gateJSON(gateWaiting, gateReached)
	attrs := fmt.Sprintf(`{"release":%q,"order":"d847000"}`, gatedRelease)
	check, bus := newReleaseGates(&status, orderRecord(t, 1, "release-5", ownerOrder, attrs))

	pass(t, check, ownerOrder.Add(3*time.Minute))
	status = gateJSON("approved", ownerOrder.Add(4*time.Minute))
	pass(t, check, ownerOrder.Add(4*time.Minute))
	pass(t, check, ownerOrder.Add(gateGrace))
	equal(t, len(bus.posted), 0)
}

func TestAGateReachedAfterHisOrderGetsItsFiveMinutesFromTheGate(t *testing.T) {
	reached := ownerOrder.Add(10 * time.Minute)
	status := gateJSON(gateWaiting, reached)
	attrs := fmt.Sprintf(`{"release":%q,"order":"d847000"}`, gatedRelease)
	check, bus := newReleaseGates(&status, orderRecord(t, 1, "release-5", ownerOrder, attrs))

	pass(t, check, reached.Add(time.Minute))
	equal(t, len(bus.posted), 0)

	pass(t, check, reached.Add(gateGrace))
	equal(t, len(bus.posted), 1)
}

func TestTheOrderTimeTheRecordSaysAndTheLaneItNamesBeatItsPostTimeAndWriter(t *testing.T) {
	status := gateJSON(gateWaiting, gateReached)
	attrs := fmt.Sprintf(`{"release":%q,"order":"d847000","ordered_at":%q,"lane":"release-5"}`, gatedRelease, ownerOrder.Format(time.RFC3339))
	check, bus := newReleaseGates(&status, orderRecord(t, 1, "root", ownerOrder.Add(10*time.Minute), attrs))

	pass(t, check, ownerOrder.Add(gateGrace))
	equal(t, len(bus.posted), 1)
	if !strings.Contains(bus.posted[0].Text, "release-5 approves it now") {
		t.Fatal(bus.posted[0].Text)
	}
}

func TestAReleaseStillPlanningOrARecordNamingNoOrderDrawsNoBlocker(t *testing.T) {
	status := gateJSON("planning", gateReached)
	named := fmt.Sprintf(`{"release":%q,"order":"d847000"}`, gatedRelease)
	check, bus := newReleaseGates(&status, orderRecord(t, 1, "release-5", ownerOrder, named), orderRecord(t, 2, "release-5", ownerOrder, `{"release":"releases/2026-11-02/1"}`))

	pass(t, check, ownerOrder.Add(time.Hour))
	equal(t, len(bus.posted), 0)
	equal(t, len(check.orders), 1)
}

func TestAStopOutsideHisOrderDrawsOneBlockerAndAnEndedBuildLeavesTheWatch(t *testing.T) {
	status := `{"record":{"failures":[{"stack":"api/plat-usw2-prod","job":"https://b/1#a","cause":"exit 1"},{"unlisted":true,"stack":"router/plat-usw2-prod","job":"https://b/1#r","cause":"its plan deletes m3"}]},"build":{"state":"failing","gate":{"state":"approved"}}}`
	attrs := fmt.Sprintf(`{"release":%q,"order":"d847000"}`, gatedRelease)
	check, bus := newReleaseGates(&status, orderRecord(t, 1, "release-5", ownerOrder, attrs))

	pass(t, check, ownerOrder.Add(time.Minute))
	pass(t, check, ownerOrder.Add(2*time.Minute))
	equal(t, len(bus.posted), 1)
	equal(t, bus.posted[0].ID, "release-order-stop/"+gatedRelease+"/router/plat-usw2-prod")
	equal(t, bus.posted[0].Text, "RELEASE-STOP "+gatedRelease+": router/plat-usw2-prod stopped outside owner order d847000: its plan deletes m3; job https://b/1#r")
	equal(t, len(check.orders), 1)

	status = `{"record":{},"build":{"state":"failed","gate":{"state":"approved"}}}`
	pass(t, check, ownerOrder.Add(3*time.Minute))
	equal(t, len(check.orders), 0)
}

const cutAnswer = "1c487a5"

func release12070(t *testing.T) (order, report client.Record) {
	t.Helper()
	data, err := os.ReadFile("testdata/release-12070.json")
	if err != nil {
		t.Fatal(err)
	}
	var rows []client.Record
	if err := json.Unmarshal(data, &rows); err != nil {
		t.Fatal(err)
	}
	rows[0].Fields.Attrs = map[string]string{"order": cutAnswer, "lane": "release-5"}
	return rows[0], rows[1]
}

func TestRelease12070sOrderDrawsOneCutSilentFiveMinutesOnWhileNoReleaseRecordCarriesIt(t *testing.T) {
	order, report := release12070(t)
	status := ""
	check, bus := newReleaseGates(&status, order, report)

	pass(t, check, order.At.Add(gateGrace-time.Minute))
	equal(t, len(bus.posted), 0)

	pass(t, check, order.At.Add(gateGrace))
	equal(t, len(bus.posted), 1)
	blocker := bus.posted[0]
	equal(t, blocker.Kind, blockerKind)
	equal(t, blocker.To, []string{Root})
	equal(t, blocker.ID, "cut-silent:"+cutAnswer)
	for _, named := range []string{"CUT-SILENT release-5", "owner order " + cutAnswer, "5 minutes", "tools/ci release start --manual <component> --order " + cutAnswer, "resolve #333545"} {
		if !strings.Contains(blocker.Text, named) {
			t.Fatalf("%q does not name %q", blocker.Text, named)
		}
	}

	pass(t, check, order.At.Add(gateGrace+time.Minute))
	equal(t, len(bus.posted), 1)
}

func TestAnOrderedCutStopsAtItsReleaseARefusalOrRootsResolve(t *testing.T) {
	order, _ := release12070(t)
	started := 2 * time.Minute
	carried := orderRecord(t, 333600, "release-5", order.At.Add(started), fmt.Sprintf(`{"release":%q,"order":%q}`, gatedRelease, cutAnswer))
	refused := client.Record{Seq: 333600, Lane: "release-5", Kind: "refuse", At: order.At.Add(started), Text: "tools/ci refused the start: InWatch"}
	resolved := client.Record{Seq: 333600, Lane: Root, Kind: "answer", At: order.At.Add(started), Resolves: order.Seq, Text: "that order stood down"}
	otherLane := client.Record{Seq: 333600, Lane: "release-6", Kind: "refuse", At: order.At.Add(started), Text: "not this order"}
	cases := []struct {
		name   string
		row    client.Record
		silent bool
	}{
		{"a release record carrying his order", carried, false},
		{"the lane refused", refused, false},
		{"root resolved the order", resolved, false},
		{"another lane's refusal", otherLane, true},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			status := gateJSON(gateWaiting, order.At.Add(gateGrace+time.Hour))
			check, bus := newReleaseGates(&status, order, c.row)

			pass(t, check, order.At.Add(gateGrace))

			silent := slices.ContainsFunc(bus.posted, func(d client.Draft) bool { return strings.HasPrefix(d.ID, "cut-silent:") })
			equal(t, silent, c.silent)
		})
	}
}

func TestAnOrderNoRootPostedOrNamingNoLaneOpensNoCut(t *testing.T) {
	order, _ := release12070(t)
	noLane := order
	noLane.Fields.Attrs = map[string]string{"order": cutAnswer}
	fromLane := order
	fromLane.Lane = "release-5"
	for _, row := range []client.Record{noLane, fromLane} {
		status := ""
		check, bus := newReleaseGates(&status, row)

		pass(t, check, order.At.Add(time.Hour))

		equal(t, len(bus.posted), 0)
	}
}
