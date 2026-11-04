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
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
)

const (
	surfacePR  = 4242
	landOnCard = "Owner on board b, Land #4242? (c): picked “Land #4242 now”"
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

func surfaceHarness(t *testing.T) harness {
	t.Helper()
	return newHarness(t, []recordedPR{{number: surfacePR, head: surfaceHead, login: "drive-bot"}}, status(surfacePR, func(r *statusRow) { r.Head = surfaceHead }))
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

func TestAShortHeadOnAPRThatMovedOrdersTheHeadHeTappedNotItsNewOne(t *testing.T) {
	h := newHarness(t, []recordedPR{{number: surfacePR, head: head, login: "drive-bot"}}, status(surfacePR))
	h.runner.Reply(surfaceHead+"\n", "ccx", "vcs", "gh", "--max-age", "0s", "--", "api", "repos/"+repo+"/commits/abc1234")
	ownerRecord(t, "Owner on board b, merge-prs: 4242 approved at abc1234", map[string]string{"event": "triage.decided", "block": "merge-prs", "row": "4242", "verdict": "approved", "head": "abc1234"})

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
	record := ownerRecord(t, landOnCard, pickAttrs)
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
	ownerRecord(t, landOnCard, pickAttrs)
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
