package board

import (
	"bytes"
	"context"
	"errors"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ledger/ledgertest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

type wakeFixture struct {
	Waker
	bus     []client.Record
	posts   []client.Draft
	relayed []string
	warned  bytes.Buffer
	broken  error
}

func newWaker(t *testing.T) *wakeFixture {
	t.Helper()
	s, err := store.Memory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	f := &wakeFixture{}
	f.Waker = Waker{
		Lane: "board-watch",
		Cards: Cards{
			Runner: &cardShell{}, Ledger: &ledgertest.Fake{}, Bus: "brook-cci", Checkout: "/co", Session: "s1", StateDir: t.TempDir(), Store: s,
			Now: func() time.Time { return cardsNow },
		},
		Tail: func(_ context.Context, q client.Query) ([]client.Record, error) {
			return slices.DeleteFunc(slices.Clone(f.bus), func(r client.Record) bool {
				return (len(q.Lanes) > 0 && !slices.Contains(q.Lanes, r.Lane)) || (len(q.Kinds) > 0 && !slices.Contains(q.Kinds, string(r.Kind))) || r.Seq <= q.After
			}), nil
		},
		Post: func(_ context.Context, d client.Draft) (client.Record, error) {
			f.posts = append(f.posts, d)
			return client.Record{Seq: int64(len(f.posts))}, nil
		},
		Relay: func(_ context.Context, lane, text string) error {
			f.relayed = append(f.relayed, lane+": "+text)
			return f.broken
		},
	}
	f.Warn = &f.warned
	return f
}

func (f *wakeFixture) want(t *testing.T, lane store.Lane) {
	t.Helper()
	if _, err := f.Cards.Store.WantLane(lane, cardsNow); err != nil {
		t.Fatal(err)
	}
}

func (f *wakeFixture) drawnFor(t *testing.T, card, lane string) {
	t.Helper()
	if _, err := f.Cards.Until(context.Background(), card, "at:2026-11-09T08:00:00Z", lane); err != nil {
		t.Fatal(err)
	}
}

func asked(seq int64, lane, text string) client.Record {
	return client.Record{Seq: seq, Lane: lane, Kind: "ask", Text: text, At: cardsNow.Add(-time.Hour)}
}

var pick = CardAnswer{Card: "card-chat", Block: "card-chat-pick", Said: "picked Combined", Key: "board:s1:9:ab", OwnerSeq: 700, Unclaimed: true}

func TestHisAnswerWakesTheLaneNamedOnTheCardNeverTheDeskThatDrewIt(t *testing.T) {
	f := newWaker(t)
	f.drawnFor(t, "card-chat", "owner-apps")
	f.bus = []client.Record{asked(1, "other-lane", "parked on card-chat")}

	if err := f.Wake(context.Background(), pick); err != nil {
		t.Fatal(err)
	}

	if len(f.posts) != 1 || !slices.Equal(f.posts[0].To, []string{"owner-apps"}) || f.posts[0].Kind != wakeKind || f.posts[0].Re != 700 {
		t.Fatalf("posts = %+v", f.posts)
	}
	if !strings.Contains(f.posts[0].Text, "picked Combined") || f.posts[0].ID != "card-wake:board:s1:9:ab" {
		t.Fatalf("wake = %+v", f.posts[0])
	}
}

func TestACardADeskRegisteredWakesTheLaneWhoseAskNamedItEvenOnceThatAskClosed(t *testing.T) {
	f := newWaker(t)
	f.drawnFor(t, "card-chat", "board-refresher-7")
	f.bus = []client.Record{
		asked(1, "owner-apps", "to board-refresher: the options card is ready to draw: /scratch/card-chat.json"),
		asked(2, "owner-relay", "card-chat waits on him"),
		asked(3, "owner-apps-2", "a longer id, card-chat-redesign-2, is another card"),
	}
	f.bus[0].Status = "resolved"

	if err := f.Wake(context.Background(), pick); err != nil {
		t.Fatal(err)
	}

	if len(f.posts) != 1 || !slices.Equal(f.posts[0].To, []string{"owner-apps"}) {
		t.Fatalf("posts = %+v", f.posts)
	}
}

func TestALiveOrcaWorkerIsAlsoRelayedAndALostRelayLeavesTheWakeStanding(t *testing.T) {
	f := newWaker(t)
	f.want(t, store.Lane{Name: "owner-apps", State: store.LaneDesired, Placement: orcaPlacement, Brief: "/b.md"})
	f.drawnFor(t, "card-chat", "owner-apps")
	f.broken = errors.New("no orca runner")

	if err := f.Wake(context.Background(), CardAnswer{Card: "card-chat", Block: "b", Said: "wrote\nsplit  it", Key: "k"}); err != nil {
		t.Fatal(err)
	}

	if len(f.relayed) != 1 || !strings.HasPrefix(f.relayed[0], "owner-apps: WAKE card-chat") || strings.Contains(f.relayed[0], "\n") {
		t.Fatalf("relayed = %q", f.relayed)
	}
	if len(f.posts) != 1 || !strings.Contains(f.warned.String(), "no orca runner") {
		t.Fatalf("posts = %+v, warned = %q", f.posts, f.warned.String())
	}
}

func TestAWorkerOffOrcaGetsTheRecordAloneSinceTheRelayReadsOnlyOrcaLanes(t *testing.T) {
	f := newWaker(t)
	f.want(t, store.Lane{Name: "owner-apps", State: store.LaneDesired, Placement: "sprite", Brief: "/b.md"})
	f.drawnFor(t, "card-chat", "owner-apps")

	if err := f.Wake(context.Background(), pick); err != nil {
		t.Fatal(err)
	}

	if len(f.relayed) != 0 || len(f.posts) != 1 {
		t.Fatalf("relayed = %q, posts = %+v", f.relayed, f.posts)
	}
}

func TestARetiredLanesAnswerIsAddressedToItForTheSupervisorWithNoRelayAndNoNeedsLane(t *testing.T) {
	f := newWaker(t)
	f.want(t, store.Lane{Name: "owner-apps", State: store.LaneDesired, Placement: orcaPlacement, Brief: "/briefs/owner-apps.md"})
	if err := f.Cards.Store.SetLaneState("owner-apps", store.LaneRetired, cardsNow, "root", "finished"); err != nil {
		t.Fatal(err)
	}
	f.drawnFor(t, "card-chat", "owner-apps")

	if err := f.Wake(context.Background(), pick); err != nil {
		t.Fatal(err)
	}

	if len(f.posts) != 1 || !slices.Equal(f.posts[0].To, []string{"owner-apps"}) || f.posts[0].Kind != wakeKind || !strings.HasPrefix(f.posts[0].Topic, lanes.CardWakeTopic) {
		t.Fatalf("posts = %+v", f.posts)
	}
	if !strings.Contains(f.posts[0].Text, "picked Combined") || len(f.relayed) != 0 {
		t.Fatalf("wake = %+v, relayed = %q", f.posts[0], f.relayed)
	}
}

func TestTheHeirsRecordAcknowledgesAWakeAddressedToTheLaneItReplaced(t *testing.T) {
	f := newWaker(t)
	f.want(t, store.Lane{Name: "owner-apps-1", State: store.LaneDesired, Brief: "/b.md"})
	if err := f.Cards.Store.SetLaneState("owner-apps-1", store.LaneRetired, cardsNow, "owner-apps-2", ""); err != nil {
		t.Fatal(err)
	}
	f.want(t, store.Lane{Name: "owner-apps-2", State: store.LaneDesired, Brief: "/b.md"})
	f.bus = []client.Record{wakeRecord(10, "owner-apps-1", 20*time.Minute), {Seq: 11, Lane: "owner-apps-2", Kind: "report", Text: "Building card-chat.", At: cardsNow.Add(-5 * time.Minute)}}

	if err := f.Late(context.Background()); err != nil {
		t.Fatal(err)
	}

	if len(f.posts) != 0 {
		t.Fatalf("posts = %+v", f.posts)
	}
}

func TestALiveLaneWithAnUnansweredRootOrderGetsTheWakeAndNoNeedsLane(t *testing.T) {
	f := newWaker(t)
	f.want(t, store.Lane{Name: "owner-apps", State: store.LaneDesired, Placement: orcaPlacement, Brief: "/b.md"})
	if err := f.Cards.Store.SetLaneStoppedOn("owner-apps", 44); err != nil {
		t.Fatal(err)
	}
	f.drawnFor(t, "card-chat", "owner-apps")

	if err := f.Wake(context.Background(), pick); err != nil {
		t.Fatal(err)
	}

	if len(f.posts) != 1 || !slices.Equal(f.posts[0].To, []string{"owner-apps"}) || f.posts[0].Kind != wakeKind {
		t.Fatalf("posts = %+v", f.posts)
	}
	if len(f.relayed) != 1 || !strings.HasPrefix(f.relayed[0], "owner-apps: WAKE card-chat") {
		t.Fatalf("relayed = %q", f.relayed)
	}
}

func TestAHeldLaneGetsTheWakeSinceOnlyRetirementEndsALane(t *testing.T) {
	f := newWaker(t)
	f.want(t, store.Lane{Name: "owner-apps", State: store.LaneHeld, Brief: "/b.md"})
	f.drawnFor(t, "card-chat", "owner-apps")

	if err := f.Wake(context.Background(), pick); err != nil {
		t.Fatal(err)
	}

	if len(f.posts) != 1 || !slices.Equal(f.posts[0].To, []string{"owner-apps"}) || f.posts[0].Kind != wakeKind {
		t.Fatalf("posts = %+v", f.posts)
	}
}

func TestARetiredLaneWithALiveHeirWakesTheHeir(t *testing.T) {
	f := newWaker(t)
	f.want(t, store.Lane{Name: "owner-apps", State: store.LaneDesired, Brief: "/b.md"})
	f.want(t, store.Lane{Name: "owner-apps-2", State: store.LaneDesired, Brief: "/b.md"})
	if err := f.Cards.Store.SetLaneState("owner-apps", store.LaneRetired, cardsNow, "owner-apps-2", "replaced"); err != nil {
		t.Fatal(err)
	}
	f.drawnFor(t, "card-chat", "owner-apps")

	if err := f.Wake(context.Background(), pick); err != nil {
		t.Fatal(err)
	}

	if len(f.posts) != 1 || !slices.Equal(f.posts[0].To, []string{"owner-apps-2"}) {
		t.Fatalf("posts = %+v", f.posts)
	}
}

func TestAChainOfRetirementsWakesTheLiveLaneAtItsEnd(t *testing.T) {
	f := newWaker(t)
	f.want(t, store.Lane{Name: "owner-apps", State: store.LaneDesired, Brief: "/b.md"})
	f.want(t, store.Lane{Name: "owner-apps-2", State: store.LaneDesired, Brief: "/b.md"})
	f.want(t, store.Lane{Name: "owner-apps-3", State: store.LaneDesired, Brief: "/b.md"})
	for retired, heir := range map[string]string{"owner-apps": "owner-apps-2", "owner-apps-2": "owner-apps-3"} {
		if err := f.Cards.Store.SetLaneState(retired, store.LaneRetired, cardsNow, heir, "replaced"); err != nil {
			t.Fatal(err)
		}
	}
	f.drawnFor(t, "card-chat", "owner-apps")

	if err := f.Wake(context.Background(), pick); err != nil {
		t.Fatal(err)
	}

	if len(f.posts) != 1 || !slices.Equal(f.posts[0].To, []string{"owner-apps-3"}) {
		t.Fatalf("posts = %+v", f.posts)
	}
}

func TestALaneRetiredWithNoRecordedHeirWakesTheLiveLaneNumberedAfterIt(t *testing.T) {
	f := newWaker(t)
	f.want(t, store.Lane{Name: "owner-apps-1", State: store.LaneDesired, Brief: "/b.md"})
	f.want(t, store.Lane{Name: "owner-apps-2", State: store.LaneDesired, Brief: "/b.md"})
	if err := f.Cards.Store.SetLaneState("owner-apps-1", store.LaneRetired, cardsNow, "stand-down", "finished"); err != nil {
		t.Fatal(err)
	}
	f.drawnFor(t, "card-chat", "owner-apps-1")

	if err := f.Wake(context.Background(), pick); err != nil {
		t.Fatal(err)
	}

	if len(f.posts) != 1 || !slices.Equal(f.posts[0].To, []string{"owner-apps-2"}) || f.posts[0].Kind != wakeKind {
		t.Fatalf("posts = %+v", f.posts)
	}
}

func TestAPickNoLaneAskedForGoesToOwnerRelayOnlyWhenUnclaimedIsSet(t *testing.T) {
	f := newWaker(t)
	quiet := pick
	quiet.Unclaimed = false

	if err := f.Wake(context.Background(), quiet); err != nil {
		t.Fatal(err)
	}
	if len(f.posts) != 0 {
		t.Fatalf("posts = %+v", f.posts)
	}

	if err := f.Wake(context.Background(), pick); err != nil {
		t.Fatal(err)
	}
	if len(f.posts) != 1 || !slices.Equal(f.posts[0].To, []string{communicator}) || !strings.HasPrefix(f.posts[0].Text, "NEEDS-LANE card-chat") {
		t.Fatalf("posts = %+v", f.posts)
	}
}

func TestAPickTheRelayAlreadyRoutedDrawsNoNeedsLane(t *testing.T) {
	for _, tt := range []struct {
		name   string
		record client.Record
		posts  int
	}{
		{"a decide after his answer", client.Record{Seq: 701, Lane: "owner-relay", Kind: "decide", Text: "OWNER TAP card-chat: picked Combined. chat-apps: yours."}, 0},
		{"a claim after his answer", client.Record{Seq: 701, Lane: "chat-apps", Kind: "claim", Text: "Claiming card-chat for the pick."}, 0},
		{"a decide before his answer", client.Record{Seq: 699, Lane: "owner-relay", Kind: "decide", Text: "Drawing card-chat for him."}, 1},
		{"a decide naming another card", client.Record{Seq: 701, Lane: "owner-relay", Kind: "decide", Text: "OWNER TAP card-chat-2: picked Combined."}, 1},
		{"its own earlier wake", client.Record{Seq: 701, Lane: "board-watch", Kind: "decide", Text: "WAKE card-chat: the owner answered your card."}, 1},
	} {
		t.Run(tt.name, func(t *testing.T) {
			f := newWaker(t)
			f.bus = []client.Record{tt.record}

			if err := f.Wake(context.Background(), pick); err != nil {
				t.Fatal(err)
			}

			if len(f.posts) != tt.posts {
				t.Fatalf("posts = %+v", f.posts)
			}
		})
	}
}

func TestATapOnAPRCardIsItsOwnOrderAndAsksForNoLane(t *testing.T) {
	for name, tap := range map[string]string{
		"#49948": "Owner on board d50e80e8, Tap: sand list for members of 42 or more teams (#49948) (rmw2kaiat-choice): picked \u201cApprove\u201d",
		"#49830": "Owner on board d50e80e8, Tap Approve on #49830: Tap: #49830, long chats stop failing on \"longer than the model can read\" (rmw2kelye-choice): picked \u201cApprove\u201d",
		"a hold": "Owner on board d50e80e8, Tap: #49830, long chats stop failing (rmw2kelye-choice): picked \u201cHold\u201d",
	} {
		t.Run(name, func(t *testing.T) {
			f := newWaker(t)
			owner := client.Record{Seq: pick.OwnerSeq, Lane: "board-watch", Kind: ownerKind, Text: tap}
			owner.Fields.Attrs = map[string]string{"event": "choice.selected", "picked": "o1"}
			f.bus = []client.Record{owner}
			if err := f.Wake(context.Background(), pick); err != nil {
				t.Fatal(err)
			}
			if len(f.posts) != 0 {
				t.Fatalf("posts = %+v, want no lane asked for", f.posts)
			}
		})
	}
}

func TestAPickOnACardNamingNoPRStillAsksForALane(t *testing.T) {
	f := newWaker(t)
	owner := client.Record{Seq: pick.OwnerSeq, Lane: "board-watch", Kind: ownerKind, Text: "Owner on board d50e80e8, Which chat app? (card-chat-pick): picked \u201cCombined\u201d"}
	owner.Fields.Attrs = map[string]string{"event": "choice.selected", "picked": "o1"}
	f.bus = []client.Record{owner}
	if err := f.Wake(context.Background(), pick); err != nil {
		t.Fatal(err)
	}
	if len(f.posts) != 1 || !strings.HasPrefix(f.posts[0].Text, "NEEDS-LANE card-chat") {
		t.Fatalf("posts = %+v", f.posts)
	}
}

func wakeRecord(seq int64, lane string, age time.Duration) client.Record {
	var r client.Record
	r.Seq, r.Lane, r.Kind, r.Topic, r.Re, r.At = seq, "board-watch", wakeKind, wakeTopic+"card-chat", 700, cardsNow.Add(-age)
	r.Fields.Attrs = map[string]string{"session": "s1", "card": "card-chat", "block": "card-chat-pick", laneField: lane}
	return r
}

func TestAWakeUnacknowledgedTenMinutesEscalatesOnceToMetaDeskAndTheCommunicator(t *testing.T) {
	f := newWaker(t)
	f.bus = []client.Record{wakeRecord(800, "owner-apps", 11*time.Minute)}

	if err := f.Late(context.Background()); err != nil {
		t.Fatal(err)
	}

	if len(f.posts) != 1 || !slices.Equal(f.posts[0].To, []string{"meta-desk", "owner-relay"}) || f.posts[0].Kind != lateKind {
		t.Fatalf("posts = %+v", f.posts)
	}
	for _, named := range []string{"card-chat", "owner-apps"} {
		if !strings.Contains(f.posts[0].Text, named) {
			t.Fatalf("%q lacks %q", f.posts[0].Text, named)
		}
	}

	f.bus = append(f.bus, client.Record{Seq: 801, Lane: "board-watch", Kind: lateKind, Topic: f.posts[0].Topic, At: cardsNow})
	f.posts = nil
	if err := f.Late(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(f.posts) != 0 {
		t.Fatalf("escalated twice: %+v", f.posts)
	}
}

func TestEveryWakeRecordAddressesItsLaneAsNamedSinceCciRefusesAStemItCannotResolve(t *testing.T) {
	f := newWaker(t)
	f.drawnFor(t, "card-chat", "owner-apps")
	if err := f.Wake(context.Background(), pick); err != nil {
		t.Fatal(err)
	}
	if err := f.Wake(context.Background(), CardAnswer{Card: "card-none", Block: "b", Key: "k", Unclaimed: true}); err != nil {
		t.Fatal(err)
	}
	f.bus = []client.Record{wakeRecord(800, "owner-apps", 11*time.Minute)}
	if err := f.Late(context.Background()); err != nil {
		t.Fatal(err)
	}

	if len(f.posts) != 3 || slices.ContainsFunc(f.posts, func(d client.Draft) bool { return !d.Force }) {
		t.Fatalf("posts = %+v", f.posts)
	}
}

func TestAWakeUnderTenMinutesOldOrOnAnotherBoardWaits(t *testing.T) {
	f := newWaker(t)
	other := wakeRecord(801, "owner-apps", time.Hour)
	other.Fields.Attrs = map[string]string{"session": "s2", "card": "card-chat", laneField: "owner-apps"}
	f.bus = []client.Record{wakeRecord(800, "owner-apps", 9*time.Minute), other}

	if err := f.Late(context.Background()); err != nil {
		t.Fatal(err)
	}

	if len(f.posts) != 0 {
		t.Fatalf("posts = %+v", f.posts)
	}
}

func TestAWakeCarryingNoAnswerSeqIsNotAcknowledgedByAnUnrelatedRecord(t *testing.T) {
	f := newWaker(t)
	wake := wakeRecord(800, "owner-apps", 11*time.Minute)
	wake.Re = 0
	f.bus = []client.Record{wake, {Seq: 801, Lane: "owner-apps", Text: "0 failures so far"}}

	if err := f.Late(context.Background()); err != nil {
		t.Fatal(err)
	}

	if len(f.posts) != 1 {
		t.Fatalf("posts = %+v", f.posts)
	}
}

func TestAnyRecordFromTheLaneCitingTheCardOrAnswerAcknowledges(t *testing.T) {
	for name, heard := range map[string]client.Record{
		"names the card":        {Seq: 801, Lane: "owner-apps", Text: "building on card-chat now"},
		"names the block":       {Seq: 801, Lane: "owner-apps", Text: "read card-chat-pick"},
		"cites the wake":        {Seq: 801, Lane: "owner-apps", Text: "on it, per #800"},
		"cites his answer":      {Seq: 801, Lane: "owner-apps", Text: "building on 700"},
		"replies to the wake":   {Seq: 801, Lane: "owner-apps", Text: "ok", Re: 800},
		"replies to his answer": {Seq: 801, Lane: "owner-apps", Text: "ok", Re: 700},
		"under the card topic":  {Seq: 801, Lane: "owner-apps", Text: "ok", Topic: "card-chat"},
	} {
		t.Run(name, func(t *testing.T) {
			f := newWaker(t)
			f.bus = []client.Record{wakeRecord(800, "owner-apps", 11*time.Minute), heard}
			if err := f.Late(context.Background()); err != nil {
				t.Fatal(err)
			}
			if len(f.posts) != 0 {
				t.Fatalf("posts = %+v", f.posts)
			}
		})
	}
}

func TestARecordCitingNothingOrFromAnotherLaneIsNoAcknowledgement(t *testing.T) {
	for name, heard := range map[string]client.Record{
		"cites nothing":     {Seq: 801, Lane: "owner-apps", Text: "still building the other thing"},
		"another lane":      {Seq: 801, Lane: "other-lane", Text: "card-chat is theirs"},
		"a longer id":       {Seq: 801, Lane: "owner-apps", Text: "card-chat-2 is done"},
		"a number in a seq": {Seq: 801, Lane: "owner-apps", Text: "see #7000"},
	} {
		t.Run(name, func(t *testing.T) {
			f := newWaker(t)
			f.bus = []client.Record{wakeRecord(800, "owner-apps", 11*time.Minute), heard}
			if err := f.Late(context.Background()); err != nil {
				t.Fatal(err)
			}
			if len(f.posts) != 1 {
				t.Fatalf("posts = %+v", f.posts)
			}
		})
	}
}

func TestUntilKeepsTheLaneOnTheCardsRow(t *testing.T) {
	f := newWaker(t)
	f.drawnFor(t, "card-chat", "owner-apps")

	_, rows, err := f.Cards.rows(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if rows["card-chat"][laneField] != "owner-apps" {
		t.Fatalf("row = %v", rows["card-chat"])
	}
}
