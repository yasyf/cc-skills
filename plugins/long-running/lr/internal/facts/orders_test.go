package facts_test

import (
	"context"
	"crypto/rand"
	"encoding/json"
	"errors"
	"os"
	"slices"
	"strconv"
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

func drawFilled(t *testing.T, s *store.Store, number int, at string) {
	t.Helper()
	if err := s.PutReviewRows([]store.ReviewRow{{Repo: repo, Number: number, Head: at, State: prrow.StateOpen, ForUsers: "a user sees it", PlanLine: "plan step 1", Risk: "low"}}); err != nil {
		t.Fatal(err)
	}
}

func drawLandable(t *testing.T, s *store.Store, number int, at string) {
	t.Helper()
	drawFilled(t, s, number, at)
	for kind, value := range map[string]string{"read-ci": prrow.CIGreen, "read-approvers": "reviewer", "read-mergeable": string(machine.MergeableClean)} {
		if _, err := s.PutFact(repo, number, at, kind, value, time.Now()); err != nil {
			t.Fatal(err)
		}
	}
}

const heldByRoot = "held, root:the cutover"

func drawWaiting(t *testing.T, s *store.Store, number int, at string, waits func(*store.ReviewRow)) {
	t.Helper()
	row := store.ReviewRow{
		Repo: repo, Number: number, Head: at, ReadHead: at, State: prrow.StateOpen, CI: prrow.CIGreen, Approvers: "reviewer", Grade: prrow.GradePass, GradeFindings: prrow.None,
		Mergeable: string(machine.MergeableClean), Queue: string(machine.QueueNone), Hold: prrow.None, SupersededBy: prrow.None, Parent: prrow.None, ParentState: prrow.None,
		ForUsers: "a user sees it", PlanRow: "plan step 1", Risk: "low",
	}
	waits(&row)
	if err := s.PutReviewRows([]store.ReviewRow{prrow.Decide(row)}); err != nil {
		t.Fatal(err)
	}
}

func heldRow(r *store.ReviewRow) { r.Hold = "root:the cutover" }

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

func TestALandPickNamingSeveralPRsEscalatesOnceAndOtherPicksDoNot(t *testing.T) {
	h := surfaceHarness(t)
	ownerRecord(t, "Owner on board b, Should next notice a stopped devhost by holding a connection? (no-pr): picked “Land the held connection”", map[string]string{"event": "choice.selected", "block": "no-pr", "picked": "no-pr-1"})
	ownerRecord(t, "Owner on board b, Land #4242 or #4243? (two-prs): picked “Land both”", map[string]string{"event": "choice.selected", "block": "two-prs", "picked": "two-prs-1"})
	ownerRecord(t, "Owner on board b, Which? (not-land): picked “Keep the 30-day rule”", map[string]string{"event": "choice.selected", "block": "not-land", "picked": "not-land-1"})

	writeOrders(t, h)
	writeOrders(t, h)

	found, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{"blocker"}, Lanes: []string{facts.OrderLane}})
	if err != nil {
		t.Fatal(err)
	}
	said := func(block string) bool {
		return slices.ContainsFunc(found, func(r client.Record) bool { return strings.Contains(r.Text, "("+block+")") })
	}
	if len(found) != 1 || said("no-pr") || !said("two-prs") || len(orders(t)) != 0 {
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

func TestATapOnARowWhoseStandingRefusesItIsRefusedWithTheReasonAndNeverOrdered(t *testing.T) {
	for _, c := range []struct {
		name   string
		change func(*store.ReviewRow)
		waits  string
	}{
		{"held", heldRow, heldByRoot},
		{"superseded", func(r *store.ReviewRow) { r.SupersededBy = "#4243" }, "Nothing: superseded, #4243"},
	} {
		t.Run(c.name, func(t *testing.T) {
			h := unreadHarness(t)
			drawWaiting(t, h.store, surfacePR, surfaceHead, c.change)
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

func TestATapOnALandedRowPostsNoOrderNoRefusalAndNoBlocker(t *testing.T) {
	h := unreadHarness(t)
	drawWaiting(t, h.store, surfacePR, surfaceHead, func(r *store.ReviewRow) { r.State = prrow.StateLanded })
	ownerRecord(t, landOnCard, pickAttrs)

	writeOrders(t, h)

	told, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{"blocker"}, Lanes: []string{facts.OrderLane}})
	if err != nil {
		t.Fatal(err)
	}
	if written, refused := orders(t), refusals(t); len(written)+len(refused)+len(told) != 0 {
		t.Fatalf("orders = %+v, refusals = %+v, told = %+v", written, refused, told)
	}
}

func TestARefusedTapTellsTheRelayWhatHoldsItOnce(t *testing.T) {
	h := unreadHarness(t)
	drawWaiting(t, h.store, surfacePR, surfaceHead, heldRow)
	ownerRecord(t, landOnCard, pickAttrs)

	writeOrders(t, h)
	writeOrders(t, h)

	told, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{"blocker"}, Lanes: []string{facts.OrderLane}})
	if err != nil {
		t.Fatal(err)
	}
	if len(told) != 1 || !slices.Contains(told[0].To, "owner-relay") || !strings.Contains(told[0].Text, heldByRoot) || !strings.Contains(told[0].Text, "#4242") {
		t.Fatalf("told = %+v", told)
	}
}

func TestHisApprovalOnARowStillWaitingOnACheckIsOrderedAtThatHead(t *testing.T) {
	for _, c := range []struct {
		name   string
		change func(*store.ReviewRow)
	}{
		{"fidelity missing", func(r *store.ReviewRow) { r.Grade, r.GradeFindings = prrow.GradeFail, "fidelity: missing" }},
		{"ungraded", func(r *store.ReviewRow) { r.Grade = prrow.GradePending }},
		{"CI running", func(r *store.ReviewRow) { r.CI = prrow.CIPending }},
		{"stacked on a parent not landed", func(r *store.ReviewRow) { r.Parent, r.ParentState = "#4241", prrow.ParentOpen }},
	} {
		t.Run(c.name, func(t *testing.T) {
			h := unreadHarness(t)
			drawWaiting(t, h.store, surfacePR, surfaceHead, c.change)
			ownerRecord(t, landOnCard, pickAttrs)

			writeOrders(t, h)

			if written, refused := orders(t), refusals(t); len(written) != 1 || len(refused) != 0 || !strings.Contains(written[0].Text, surfaceHead[:12]) {
				t.Fatalf("orders = %+v, refusals = %+v", written, refused)
			}
			if row := h.snapshot(t)[surfacePR]; !row.Tapped() {
				t.Fatalf("row = %+v", row)
			}
		})
	}
}

func TestAnApprovingPickOnAReplyMarksTheRowTappedAtItsHeadAndTheQueueKeepsIt(t *testing.T) {
	h := unreadHarness(t)
	drawWaiting(t, h.store, surfacePR, surfaceHead, func(r *store.ReviewRow) { r.Grade, r.GradeFindings = prrow.GradeFail, "fidelity: missing" })
	ownerRecord(t, "Owner on board b, Approve #4242, compaction by fork (r1-choice): picked “Approve, land it” at "+repo+"#4242 "+surfaceHead, map[string]string{
		"event": "choice.selected", "block": "r1-choice", "picked": "r1-1", "heads": repo + "#4242=" + surfaceHead,
	})

	writeOrders(t, h)
	row := h.snapshot(t)[surfacePR]
	row.Queue, row.QueuedHead = machine.QueueQueued, row.Head

	if !row.Tapped() || row.Head != surfaceHead {
		t.Fatalf("row = %+v", row)
	}
	if got := machine.Classify(row); got.Action == machine.ActionDequeue {
		t.Fatalf("the landing pass would pull his approved PR from the queue: %+v", got)
	}
}

func TestATapRefusedWhileHeldDoesNotLandWhenTheRowLaterTurnsReady(t *testing.T) {
	h := unreadHarness(t)
	drawWaiting(t, h.store, surfacePR, surfaceHead, heldRow)
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
	drawWaiting(t, h.store, surfacePR, surfaceHead, heldRow)
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

func TestATapAtAHeadTheRowMovedOffIsOrderedThereForTheCarryRule(t *testing.T) {
	h := unreadHarness(t)
	drawReady(t, h.store, surfacePR, head)
	ownerRecord(t, "Owner on board b, merge-prs: 4242 approved at "+surfaceHead[:12], map[string]string{"event": "triage.decided", "block": "merge-prs", "row": "4242", "verdict": "approved", "head": surfaceHead})

	writeOrders(t, h)

	if written, refused := orders(t), refusals(t); len(written) != 1 || len(refused) != 0 || !strings.Contains(written[0].Text, surfaceHead[:12]) {
		t.Fatalf("orders = %+v, refusals = %+v", written, refused)
	}
}

func TestATapOnAPRWithNoRowIsRefused(t *testing.T) {
	h := unreadHarness(t)
	drawReady(t, h.store, surfacePR+1, surfaceHead)
	ownerRecord(t, landOnCard, pickAttrs)

	writeOrders(t, h)

	refused := refusals(t)
	if written := orders(t); len(written) != 0 || len(refused) != 1 || refused[0].Fields.Attrs["reason"] != gates.NoRow {
		t.Fatalf("orders = %+v, refusals = %+v", written, refused)
	}
}

func TestAHoldIsWrittenWhateverTheRowSays(t *testing.T) {
	h := unreadHarness(t)
	drawWaiting(t, h.store, surfacePR, surfaceHead, func(r *store.ReviewRow) { r.Grade = prrow.GradePending })
	ownerRecord(t, holdOnCard, pickAttrs)

	writeOrders(t, h)

	if written, refused := orders(t), refusals(t); len(written) != 1 || len(refused) != 0 {
		t.Fatalf("orders = %+v, refusals = %+v", written, refused)
	}
}

func TestOnlyTheLandingHostAnswersATapAndEveryHostFoldsItsRefusal(t *testing.T) {
	h := unreadHarness(t)
	drawWaiting(t, h.store, surfacePR, surfaceHead, heldRow)
	ownerRecord(t, landOnCard, pickAttrs)

	h.source.Reads = false
	writeOrders(t, h)
	if written, refused := orders(t), refusals(t); len(written) != 0 || len(refused) != 0 {
		t.Fatalf("a host with no landing mover answered: orders = %+v, refusals = %+v", written, refused)
	}

	h.source.Reads = true
	writeOrders(t, h)
	h.source.Reads = false
	if row := h.snapshot(t)[surfacePR]; row.TapRefused != heldByRoot {
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

func TestThePassReadsTheRowItJustFoldedSoAPRThatTurnedReadyIsNotHeldAPass(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: surfacePR, head: surfaceHead, login: "drive-bot", approvers: []string{"reviewer"}}}, status(surfacePR, func(r *statusRow) { r.Head = surfaceHead }))
	drawFilled(t, h.store, surfacePR, surfaceHead)
	post(t, "finder-desk", bus.Grade{PR: surfacePR, Head: surfaceHead, Grader: "finder", Verdict: "clear"})
	post(t, "fidelity-desk", bus.Grade{PR: surfacePR, Head: surfaceHead, Grader: "fidelity", Verdict: "pass"})

	snapshot, err := h.source.Snapshot(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(snapshot) != 1 || snapshot[0].RowWait == "" {
		t.Fatalf("before the pass's own read: %+v", snapshot)
	}

	if err := h.source.WriteRows(context.Background(), snapshot); err != nil {
		t.Fatal(err)
	}

	if snapshot[0].RowWait != "" {
		t.Fatalf("after the pass's own read: %+v", snapshot[0])
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

func holdUntilTap(t *testing.T, text string) client.Record {
	t.Helper()
	record, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: "root", Kind: "hold", Text: text, PRs: []string{strconv.Itoa(surfacePR)}, Attrs: map[string]string{"until": "tap"}})
	if err != nil {
		t.Fatal(err)
	}
	time.Sleep(5 * time.Millisecond)
	return record
}

func tapAt(t *testing.T, at string) client.Record {
	t.Helper()
	return ownerRecord(t, "Owner: approved", map[string]string{"row": strconv.Itoa(surfacePR), "verdict": "approved", "head": at})
}

func openHoldSeqs(t *testing.T) []int64 {
	t.Helper()
	open, err := client.Open(context.Background(), "d", "hold")
	if err != nil {
		t.Fatal(err)
	}
	seqs := make([]int64, 0, len(open))
	for _, record := range open {
		seqs = append(seqs, record.Seq)
	}
	return seqs
}

func TestHisTapAtThatHeadEndsAnUntilTapHoldAndWritesTheLandOrder(t *testing.T) {
	h := surfaceHarness(t)
	held := holdUntilTap(t, "hold #4242 his approval was at an older head")
	tapAt(t, surfaceHead)

	writeOrders(t, h)
	writeOrders(t, h)

	if written := orders(t); len(written) != 1 || !strings.Contains(written[0].Text, surfaceHead[:12]) {
		t.Fatalf("orders = %+v", written)
	}
	if open := openHoldSeqs(t); len(open) != 0 {
		t.Fatalf("hold #%d is still open: %v", held.Seq, open)
	}
	ended, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{"note"}})
	if err != nil {
		t.Fatal(err)
	}
	if len(ended) != 1 || ended[0].Resolves != held.Seq || !strings.Contains(ended[0].Text, "#4242") {
		t.Fatalf("ending records = %+v", ended)
	}
}

func TestATapAtAnotherHeadStandsThereAndLeavesAnUntilTapHoldOpen(t *testing.T) {
	h := surfaceHarness(t)
	held := holdUntilTap(t, "hold #4242 his approval was at an older head")
	tapAt(t, strings.Repeat("d", 40))

	writeOrders(t, h)

	if written := orders(t); len(written) != 1 || !strings.Contains(written[0].Text, strings.Repeat("d", 12)) {
		t.Fatalf("orders = %+v", written)
	}
	if open := openHoldSeqs(t); !slices.Equal(open, []int64{held.Seq}) {
		t.Fatalf("open holds = %v, want #%d", open, held.Seq)
	}
}

func TestATapOlderThanAnUntilTapHoldLeavesItOpen(t *testing.T) {
	h := surfaceHarness(t)
	tapAt(t, surfaceHead)
	time.Sleep(5 * time.Millisecond)
	held := holdUntilTap(t, "hold #4242 written before the pass read his tap")

	writeOrders(t, h)

	if open := openHoldSeqs(t); !slices.Equal(open, []int64{held.Seq}) {
		t.Fatalf("open holds = %v, want #%d", open, held.Seq)
	}
}

func TestHisTapLeavesAnOrdinaryHoldOpenAndAHoldOnSeveralPRsIsOrdinary(t *testing.T) {
	for name, draft := range map[string]client.Draft{
		"no until":    {Text: "hold #4242 the cutover", PRs: []string{strconv.Itoa(surfacePR)}},
		"several PRs": {Text: "hold #4242 and its neighbour", PRs: []string{strconv.Itoa(surfacePR), "4243"}, Attrs: map[string]string{"until": "tap"}},
	} {
		t.Run(name, func(t *testing.T) {
			h := surfaceHarness(t)
			draft.Drive, draft.Lane, draft.Kind = "d", "root", "hold"
			held, err := client.Post(context.Background(), draft)
			if err != nil {
				t.Fatal(err)
			}
			time.Sleep(5 * time.Millisecond)
			tapAt(t, surfaceHead)

			writeOrders(t, h)

			if open := openHoldSeqs(t); !slices.Equal(open, []int64{held.Seq}) {
				t.Fatalf("open holds = %v, want #%d", open, held.Seq)
			}
		})
	}
}

func TestAnUntilTapHoldKeepsItsRowForHisTapAndStopsAnOlderTapLanding(t *testing.T) {
	h := surfaceHarness(t)
	if _, err := h.store.PutFact(repo, surfacePR, surfaceHead, "tap", string(machine.TapTapped), time.Now()); err != nil {
		t.Fatal(err)
	}
	holdUntilTap(t, "hold #4242 his approval was at an older head")

	f := h.snapshot(t)[surfacePR]

	if !f.HoldAwaitsTap || f.Hold == "" {
		t.Fatalf("facts = %+v, want an until=tap hold", f)
	}
	row, _, err := h.store.ReviewRow(repo, surfacePR)
	if err != nil {
		t.Fatal(err)
	}
	if row.Hold != prrow.None || row.Tapped || !strings.HasPrefix(row.AwaitsTap, "his approval was at an older head (record #") {
		t.Fatalf("row = %+v", row)
	}
}

func TestAnUntilTapHoldBesideAnOrdinaryHoldReadsAsHeld(t *testing.T) {
	h := surfaceHarness(t)
	holdUntilTap(t, "hold #4242 his approval was at an older head")
	if _, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: "root", Kind: "hold", Text: "hold #4242 the cutover has not run", PRs: []string{strconv.Itoa(surfacePR)}}); err != nil {
		t.Fatal(err)
	}

	f := h.snapshot(t)[surfacePR]

	if f.HoldAwaitsTap || f.Hold != machine.MoverRoot {
		t.Fatalf("facts = %+v, want the ordinary hold", f)
	}
	row, _, err := h.store.ReviewRow(repo, surfacePR)
	if err != nil {
		t.Fatal(err)
	}
	if row.ReadyForTap || !strings.Contains(row.WaitsOn, "the cutover has not run") {
		t.Fatalf("row = %+v, want held and naming the ordinary hold", row)
	}
}

func TestAHeadlessTapRetriedAfterItsOrderStillEndsTheHold(t *testing.T) {
	h := surfaceHarness(t)
	held := holdUntilTap(t, "hold #4242 his approval was at an older head")
	word := ownerRecord(t, "Owner: approved", map[string]string{"row": strconv.Itoa(surfacePR), "verdict": "approved"})
	orders := bus.Bus{Drive: "d", Lane: facts.OrderLane}
	if _, err := orders.PostOrder(context.Background(), bus.Tap{PR: surfacePR, Head: surfaceHead, By: bus.OwnerBy, Value: "tapped"}, word); err != nil {
		t.Fatal(err)
	}

	writeOrders(t, h)

	if open := openHoldSeqs(t); len(open) != 0 {
		t.Fatalf("hold #%d is still open: %v", held.Seq, open)
	}
}

func TestALaterRootGoLeavesAnUntilTapHoldStanding(t *testing.T) {
	h := surfaceHarness(t)
	holdUntilTap(t, "hold #4242 his approval was at an older head")
	goRecord(t, "root", "go #4242 at abc1234")

	if f := h.snapshot(t)[surfacePR]; !f.HoldAwaitsTap || f.Hold == "" {
		t.Fatalf("facts = %+v, want the hold standing", f)
	}
}

func TestAnUntilTapHoldThatArrivesAfterTheSnapshotStillStopsItsPass(t *testing.T) {
	h := surfaceHarness(t)
	snapshot := []machine.Facts{h.snapshot(t)[surfacePR]}
	if snapshot[0].Hold != "" {
		t.Fatalf("facts = %+v, want no hold yet", snapshot[0])
	}
	holdUntilTap(t, "hold #4242 his approval was at an older head")

	if err := h.source.WriteRows(context.Background(), snapshot); err != nil {
		t.Fatal(err)
	}

	if !snapshot[0].HoldAwaitsTap || snapshot[0].Hold == "" {
		t.Fatalf("facts = %+v, want the new hold", snapshot[0])
	}
}
