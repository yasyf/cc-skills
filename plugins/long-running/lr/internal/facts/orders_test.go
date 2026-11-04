package facts_test

import (
	"context"
	"crypto/rand"
	"encoding/json"
	"errors"
	"os"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/facts"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	surfacePR  = 4242
	landOnCard = "Owner on board b, Land #4242? (c): picked “Land #4242 now”"
	holdOnCard = "Owner on board b, Land #4242? (c): picked “Hold #4242”"
)

var (
	surfaceHead = "abc1234" + strings.Repeat("0", 33)
	pickAttrs   = map[string]string{"event": "choice.selected"}
)

type tapSurface struct {
	Surface string `json:"surface"`
	Record  struct {
		Text  string            `json:"text"`
		Attrs map[string]string `json:"attrs"`
	} `json:"record"`
	Value string `json:"value"`
}

type statusDown struct {
	ccx.Client
	down bool
}

func (s *statusDown) PRStatus(ctx context.Context, repo string, numbers []int) ([]ccx.PRStatus, error) {
	if s.down {
		return nil, errors.New("the status read is down")
	}
	return s.Client.PRStatus(ctx, repo, numbers)
}

func tapSurfaces(t *testing.T) []tapSurface {
	t.Helper()
	raw, err := os.ReadFile("../bus/testdata/tap_surfaces.json")
	if err != nil {
		t.Fatal(err)
	}
	var surfaces []tapSurface
	if err := json.Unmarshal(raw, &surfaces); err != nil {
		t.Fatal(err)
	}
	return surfaces
}

func unreadHarness(t *testing.T) harness {
	t.Helper()
	return newHarness(t, []recordedPR{{number: surfacePR, head: surfaceHead, login: "drive-bot", unopened: true}}, status(surfacePR, func(r *statusRow) { r.Head = surfaceHead }))
}

func surfaceHarness(t *testing.T) harness {
	t.Helper()
	h := unreadHarness(t)
	drawReady(t, h.store, surfacePR, surfaceHead)
	return h
}

func drawReady(t *testing.T, s *store.Store, number int, at string) {
	t.Helper()
	if err := s.PutReviewRows([]store.ReviewRow{{Repo: repo, Number: number, Head: at, ReadyForTap: true}}); err != nil {
		t.Fatal(err)
	}
}

func drawLandable(t *testing.T, s *store.Store, number int, at string) {
	t.Helper()
	if err := s.PutReviewRows([]store.ReviewRow{{Repo: repo, Number: number, Head: at, State: prrow.StateOpen, ForUsers: "a user sees it", PlanLine: "plan step 1", Risk: "low"}}); err != nil {
		t.Fatal(err)
	}
	for kind, value := range map[string]string{"read-ci": prrow.CIGreen, "read-approvers": "reviewer", "read-mergeable": string(machine.MergeableClean)} {
		if _, err := s.PutFact(repo, number, at, kind, value, time.Now()); err != nil {
			t.Fatal(err)
		}
	}
}

func drawWaiting(t *testing.T, s *store.Store, number int, at, waits string) {
	t.Helper()
	if err := s.PutReviewRows([]store.ReviewRow{{Repo: repo, Number: number, Head: at, WaitsOn: waits}}); err != nil {
		t.Fatal(err)
	}
}

func refusals(t *testing.T) []client.Record {
	t.Helper()
	written, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{bus.TapRefusedKind}})
	if err != nil {
		t.Fatal(err)
	}
	return written
}

func ownerRecord(t *testing.T, text string, attrs map[string]string) client.Record {
	t.Helper()
	record, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: "board-watch", Kind: "owner", ID: "board:" + rand.Text(), Text: text, Attrs: attrs})
	if err != nil {
		t.Fatal(err)
	}
	return record
}

func orders(t *testing.T) []client.Record {
	t.Helper()
	written, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{"lr.tap"}})
	if err != nil {
		t.Fatal(err)
	}
	return written
}

func writeOrders(t *testing.T, h harness) {
	t.Helper()
	if err := h.source.WriteOrders(context.Background()); err != nil {
		t.Fatal(err)
	}
}

func TestATapOnEachSurfaceWritesTheOneLandOrder(t *testing.T) {
	for _, surface := range tapSurfaces(t) {
		t.Run(surface.Surface, func(t *testing.T) {
			h := surfaceHarness(t)
			ownerRecord(t, surface.Record.Text, surface.Record.Attrs)

			row := h.snapshot(t)[surfacePR]

			if string(row.Tap) != surface.Value {
				t.Fatalf("tap = %q, want %q", row.Tap, surface.Value)
			}
			if written := orders(t); (surface.Value != "") != (len(written) == 1) {
				t.Fatalf("orders = %+v", written)
			}
		})
	}
}

func TestAShortHeadHoldOnAPRThatMovedOrdersTheHeadHeHeldNotItsNewOne(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: surfacePR, head: head, login: "drive-bot"}}, status(surfacePR))
	h.runner.Reply(surfaceHead+"\n", "ccx", "vcs", "gh", "--max-age", "0s", "--", "api", "repos/"+repo+"/commits/abc1234")
	ownerRecord(t, "Owner on board b, merge-prs: 4242 rejected at abc1234", map[string]string{"event": "triage.decided", "block": "merge-prs", "row": "4242", "verdict": "rejected", "head": "abc1234"})

	writeOrders(t, h)

	written := orders(t)
	if len(written) != 1 || !strings.Contains(written[0].Text, surfaceHead[:12]) {
		t.Fatalf("orders = %+v", written)
	}
}

func TestATapUndoneAndGivenAgainAtOneHeadStands(t *testing.T) {
	h := surfaceHarness(t)
	row := func(verdict string) map[string]string {
		return map[string]string{"event": "triage.decided", "block": "merge-prs", "row": "4242", "verdict": verdict, "head": "abc1234"}
	}
	ownerRecord(t, "Owner on board b, merge-prs: 4242 approved at abc1234", row("approved"))
	ownerRecord(t, "Owner on board b, merge-prs: 4242 cleared at abc1234", row("cleared"))
	ownerRecord(t, "Owner on board b, merge-prs: 4242 approved at abc1234", row("approved"))

	got := h.snapshot(t)[surfacePR]

	if got.Tap != machine.TapTapped || len(orders(t)) != 3 {
		t.Fatalf("tap = %q, orders = %+v", got.Tap, orders(t))
	}
}

func TestAQueuedPRHeTappedOnACardIsNotPulled(t *testing.T) {
	h := surfaceHarness(t)
	ownerRecord(t, landOnCard, pickAttrs)

	row := h.snapshot(t)[surfacePR]
	row.Queue, row.QueuedHead = machine.QueueQueued, row.Head

	if got := machine.Classify(row); got.Action == machine.ActionDequeue {
		t.Fatalf("a PR with his land order would be dequeued: %+v", got)
	}
}

func TestAPickOrderedOnceIsNotWrittenAgainAtALaterHead(t *testing.T) {
	h := surfaceHarness(t)
	ownerRecord(t, landOnCard, pickAttrs)
	post(t, "pr-cards", bus.Tap{PR: surfacePR, Head: head2, By: bus.OwnerBy, Value: "tapped"})

	writeOrders(t, h)

	for _, written := range orders(t) {
		if strings.Contains(written.Text, surfaceHead[:12]) {
			t.Fatalf("wrote a second order: %s", written.Text)
		}
	}
}

func TestAPickItsBoardCouldNotLabelEscalatesOnceAndWritesNoOrder(t *testing.T) {
	h := surfaceHarness(t)
	ownerRecord(t, "Owner on board b, c: picked option c-1", map[string]string{"event": "choice.selected", "block": "c", "picked": "c-1"})

	writeOrders(t, h)
	writeOrders(t, h)

	found, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{"blocker"}, Lanes: []string{facts.OrderLane}})
	if err != nil {
		t.Fatal(err)
	}
	if len(found) != 1 || !slices.Contains(found[0].To, "meta-desk") || !strings.Contains(found[0].Text, "c-1") || len(orders(t)) != 0 {
		t.Fatalf("escalated = %+v, orders = %+v", found, orders(t))
	}
}

func TestATapWithNoOrderAfterAMinuteEscalatesOnceAndBlocksNothing(t *testing.T) {
	h := surfaceHarness(t)
	h.source.CCX = &statusDown{Client: h.source.CCX, down: true}
	record := ownerRecord(t, holdOnCard, pickAttrs)
	escalated := func() []client.Record {
		found, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{"blocker"}, Lanes: []string{facts.OrderLane}})
		if err != nil {
			t.Fatal(err)
		}
		return found
	}

	h.source.Now = func() time.Time { return record.At.Add(facts.OrderGrace - time.Second) }
	writeOrders(t, h)
	if found := escalated(); len(found) != 0 {
		t.Fatalf("escalated inside the minute: %+v", found)
	}

	h.source.Now = func() time.Time { return record.At.Add(facts.OrderGrace + time.Second) }
	writeOrders(t, h)
	writeOrders(t, h)

	found := escalated()
	if len(found) != 1 || !slices.Contains(found[0].To, "meta-desk") || !slices.Contains(found[0].To, "owner-relay") {
		t.Fatalf("escalated = %+v", found)
	}
}

func TestAFailedOrderIsRetriedRatherThanLost(t *testing.T) {
	h := surfaceHarness(t)
	reader := &statusDown{Client: h.source.CCX, down: true}
	h.source.CCX = reader
	ownerRecord(t, holdOnCard, pickAttrs)
	writeOrders(t, h)
	if written := orders(t); len(written) != 0 {
		t.Fatalf("orders = %+v", written)
	}
	reader.down = false

	writeOrders(t, h)

	if written := orders(t); len(written) != 1 {
		t.Fatalf("orders = %+v", written)
	}
}

func TestATapOnARowNotReadyIsRefusedWithTheReasonAndNeverOrdered(t *testing.T) {
	for _, c := range []struct{ name, waits string }{
		{"held", "held by root"},
		{"superseded", "superseded by #4243"},
		{"ungraded", "not graded"},
		{"stacked on a parent not landed", "the change below it, #4241"},
	} {
		t.Run(c.name, func(t *testing.T) {
			h := unreadHarness(t)
			drawWaiting(t, h.store, surfacePR, surfaceHead, c.waits)
			ownerRecord(t, landOnCard, pickAttrs)

			writeOrders(t, h)
			writeOrders(t, h)

			refused := refusals(t)
			if written := orders(t); len(written) != 0 || len(refused) != 1 {
				t.Fatalf("orders = %+v, refusals = %+v", written, refused)
			}
			if attrs := refused[0].Fields.Attrs; attrs["reason"] != c.waits || attrs["head"] != surfaceHead {
				t.Fatalf("refusal = %+v", refused[0])
			}
			if row := h.snapshot(t)[surfacePR]; row.Tapped() || row.TapRefused != c.waits {
				t.Fatalf("row = %+v", row)
			}
			if drawn, found, err := h.store.ReviewRow(repo, surfacePR); err != nil || !found || drawn.TapRefused != c.waits {
				t.Fatalf("the stored row = %+v, %v, %v", drawn, found, err)
			}
		})
	}
}

func TestATapRefusedWhileHeldDoesNotLandWhenTheRowLaterTurnsReady(t *testing.T) {
	h := unreadHarness(t)
	drawWaiting(t, h.store, surfacePR, surfaceHead, "held by root")
	ownerRecord(t, landOnCard, pickAttrs)
	writeOrders(t, h)

	drawReady(t, h.store, surfacePR, surfaceHead)
	writeOrders(t, h)

	row := h.snapshot(t)[surfacePR]
	if written := orders(t); len(written) != 0 || row.Tapped() {
		t.Fatalf("orders = %+v, row = %+v", written, row)
	}
	if verdict := machine.Classify(row); verdict.Action == machine.ActionEnqueue {
		t.Fatalf("verdict = %+v", verdict)
	}
}

func TestATapGivenAgainOnceTheRowIsReadyWritesTheOrder(t *testing.T) {
	h := unreadHarness(t)
	drawWaiting(t, h.store, surfacePR, surfaceHead, "held by root")
	ownerRecord(t, landOnCard, pickAttrs)
	writeOrders(t, h)

	drawReady(t, h.store, surfacePR, surfaceHead)
	ownerRecord(t, landOnCard+" again", pickAttrs)
	writeOrders(t, h)

	if written := orders(t); len(written) != 1 {
		t.Fatalf("orders = %+v", written)
	}
	if row := h.snapshot(t)[surfacePR]; !row.Tapped() {
		t.Fatalf("row = %+v", row)
	}
}

func TestATapAtAHeadTheRowMovedOffIsRefusedNamingBothHeads(t *testing.T) {
	h := unreadHarness(t)
	drawReady(t, h.store, surfacePR, head)
	ownerRecord(t, "Owner on board b, merge-prs: 4242 approved at abc1234", map[string]string{"event": "triage.decided", "block": "merge-prs", "row": "4242", "verdict": "approved", "head": "abc1234"})

	writeOrders(t, h)

	refused := refusals(t)
	if written := orders(t); len(written) != 0 || len(refused) != 1 || !strings.Contains(refused[0].Fields.Attrs["reason"], head[:7]) || !strings.Contains(refused[0].Fields.Attrs["reason"], "abc1234") {
		t.Fatalf("orders = %+v, refusals = %+v", written, refused)
	}
}

func TestATapOnAPRWithNoRowIsRefused(t *testing.T) {
	h := unreadHarness(t)
	ownerRecord(t, landOnCard, pickAttrs)

	writeOrders(t, h)

	refused := refusals(t)
	if written := orders(t); len(written) != 0 || len(refused) != 1 || refused[0].Fields.Attrs["reason"] != gates.NoRow {
		t.Fatalf("orders = %+v, refusals = %+v", written, refused)
	}
}

func TestAHoldIsWrittenWhateverTheRowSays(t *testing.T) {
	h := unreadHarness(t)
	drawWaiting(t, h.store, surfacePR, surfaceHead, "not graded")
	ownerRecord(t, holdOnCard, pickAttrs)

	writeOrders(t, h)

	if written, refused := orders(t), refusals(t); len(written) != 1 || len(refused) != 0 {
		t.Fatalf("orders = %+v, refusals = %+v", written, refused)
	}
}

func TestOnlyTheLandingHostAnswersATapAndEveryHostFoldsItsRefusal(t *testing.T) {
	h := unreadHarness(t)
	drawWaiting(t, h.store, surfacePR, surfaceHead, "held by root")
	ownerRecord(t, landOnCard, pickAttrs)

	h.source.Reads = false
	writeOrders(t, h)
	if written, refused := orders(t), refusals(t); len(written) != 0 || len(refused) != 0 {
		t.Fatalf("a host with no landing mover answered: orders = %+v, refusals = %+v", written, refused)
	}

	h.source.Reads = true
	writeOrders(t, h)
	h.source.Reads = false
	if row := h.snapshot(t)[surfacePR]; row.TapRefused != "held by root" {
		t.Fatalf("row = %+v", row)
	}
}

func openItem(t *testing.T, h harness) {
	t.Helper()
	if _, err := h.store.OpenItem(store.NewItem{Kind: "enqueue", Repo: repo, Number: surfacePR, Head: surfaceHead, Mover: machine.MoverSupervisor, Deadline: time.Hour}, time.Now()); err != nil {
		t.Fatal(err)
	}
}

func TestATappedPRWithNoReviewRowIsHeld(t *testing.T) {
	h := unreadHarness(t)
	openItem(t, h)
	post(t, "pr-cards", bus.Tap{PR: surfacePR, Head: surfaceHead, By: bus.OwnerBy, Value: "tapped"})
	post(t, "finder-desk", bus.Grade{PR: surfacePR, Head: surfaceHead, Grader: "finder", Verdict: "clear"})
	post(t, "fidelity-desk", bus.Grade{PR: surfacePR, Head: surfaceHead, Grader: "fidelity", Verdict: "pass"})

	row := h.snapshot(t)[surfacePR]
	if row.RowWait != gates.NoRow || row.Landable() || machine.Classify(row).Action == machine.ActionEnqueue {
		t.Fatalf("row = %+v, verdict = %+v", row, machine.Classify(row))
	}
}

func TestALandableRowIsReadyAfterTheFoldAndATapOnItLands(t *testing.T) {
	h := unreadHarness(t)
	drawLandable(t, h.store, surfacePR, surfaceHead)
	post(t, "finder-desk", bus.Grade{PR: surfacePR, Head: surfaceHead, Grader: "finder", Verdict: "clear"})
	post(t, "fidelity-desk", bus.Grade{PR: surfacePR, Head: surfaceHead, Grader: "fidelity", Verdict: "pass"})
	h.snapshot(t)
	ownerRecord(t, landOnCard, pickAttrs)

	writeOrders(t, h)
	row := h.snapshot(t)[surfacePR]

	if written := orders(t); len(written) != 1 || !row.Tapped() || row.RowWait != "" || machine.Classify(row).Action != machine.ActionEnqueue {
		t.Fatalf("orders = %+v, row = %+v, verdict = %+v", written, row, machine.Classify(row))
	}
}

func TestAnExemptPRWithNoReviewRowStillLands(t *testing.T) {
	h := unreadHarness(t)
	openItem(t, h)
	post(t, "finder-desk", bus.Grade{PR: surfacePR, Head: surfaceHead, Grader: "finder", Verdict: "clear"})
	post(t, "fidelity-desk", bus.Grade{PR: surfacePR, Head: surfaceHead, Grader: "fidelity", Verdict: "pass"})
	post(t, "supervisor", bus.Tap{PR: surfacePR, Head: surfaceHead, By: "rule", Value: "tapped", Rule: "test-only"})

	row := h.snapshot(t)[surfacePR]

	if !row.Exempt || row.RowWait != gates.NoRow || !row.Landable() {
		t.Fatalf("row = %+v", row)
	}
}
