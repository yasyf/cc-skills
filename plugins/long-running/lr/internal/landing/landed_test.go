package landing_test

import (
	"context"
	"encoding/json"
	"os"
	"reflect"
	"slices"
	"strings"
	"testing"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/landing"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/pass"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const monorepo = "Forge-AI/monorepo"

func TestTheNov3LandingsReachEachOwnerOnceAndSkipOneAlreadySaid(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	ctx := context.Background()
	data, err := os.ReadFile("testdata/landed-nov3.json")
	if err != nil {
		t.Fatal(err)
	}
	var rows []client.Record
	if err := json.Unmarshal(data, &rows); err != nil {
		t.Fatal(err)
	}
	said := rows[0]
	_, text, found := strings.Cut(said.Text, "LANDED")
	if !found {
		t.Fatal(said.Text)
	}
	if _, err := client.Post(ctx, client.Draft{Drive: "d", Lane: said.Lane, Kind: "landed", Text: "LANDED" + text, PRs: []string{monorepo + "#48897"}}); err != nil {
		t.Fatal(err)
	}
	route := func(desk string) string { return desk + "-6" }
	landings := []pass.Landing{
		{Facts: machine.Facts{Repo: monorepo, Number: 48897, Head: "1c40b68010", Lane: "test-fix-ssql-supervisor-3"}, LastOpen: said.At.AddDate(0, 0, -1)},
		{Facts: machine.Facts{Repo: monorepo, Number: 48144, Head: "0eab10ae6a", Lane: "test-fix-ssql-supervisor-3"}, LastOpen: said.At.AddDate(0, 0, -1)},
		{Facts: machine.Facts{Repo: monorepo, Number: 48347, Head: "50db257", Lane: ""}, LastOpen: said.At.AddDate(0, 0, -1)},
	}

	if err := landing.Announce(ctx, storeWith(t, "test-fix-ssql-supervisor-3"), "d", "dev", landings, route); err != nil {
		t.Fatal(err)
	}

	posted, err := client.Tail(ctx, client.Query{Drive: "d", Kinds: []string{"landed"}, Lanes: []string{"supervisor"}})
	if err != nil {
		t.Fatal(err)
	}
	if len(posted) != 2 {
		t.Fatalf("posted %d records, want one each for #48144 and #48347: %+v", len(posted), posted)
	}
	if got, want := posted[0].Text, "LANDED #48144 at 0eab10ae6a: merged into dev"; got != want {
		t.Fatalf("text = %q, want %q", got, want)
	}
	if want := []string{"test-fix-ssql-supervisor-3", "board-refresher-6"}; !reflect.DeepEqual(posted[0].To, want) {
		t.Fatalf("to = %v, want %v", posted[0].To, want)
	}
	if want := []string{"root", "board-refresher-6"}; !reflect.DeepEqual(posted[1].To, want) {
		t.Fatalf("a PR no lane owns reaches root: to = %v, want %v", posted[1].To, want)
	}
}

func TestALandedRecordSkipsABoardDeskWithNoLiveLaneInsteadOfTellingRootEachTime(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	ctx := context.Background()
	landings := []pass.Landing{{Facts: machine.Facts{Repo: monorepo, Number: 7, Head: "e718a7434f", Lane: "lane-7"}, LastOpen: t0}}

	if err := landing.Announce(ctx, storeWith(t, "lane-7"), "d", "dev", landings, func(string) string { return "root" }); err != nil {
		t.Fatal(err)
	}

	posted, err := client.Tail(ctx, client.Query{Drive: "d", Kinds: []string{"landed"}})
	if err != nil {
		t.Fatal(err)
	}
	if len(posted) != 1 || !reflect.DeepEqual(posted[0].To, []string{"lane-7"}) {
		t.Fatalf("posted = %+v, want one record to lane-7", posted)
	}
}

func TestALandedRecordForARetiredLaneReachesTheLaneThatSucceededIt(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	ctx := context.Background()
	s := newStore(t)
	retire(t, s, "release-5", "release-6")
	landings := []pass.Landing{{Facts: machine.Facts{Repo: monorepo, Number: 48277, Head: "e718a7434f", Lane: "release-5"}, LastOpen: t0}}

	if err := landing.Announce(ctx, s, "d", "dev", landings, func(string) string { return "root" }); err != nil {
		t.Fatal(err)
	}

	posted, err := client.Tail(ctx, client.Query{Drive: "d", Kinds: []string{"landed"}})
	if err != nil {
		t.Fatal(err)
	}
	if len(posted) != 1 || !reflect.DeepEqual(posted[0].To, []string{"release-6"}) {
		t.Fatalf("posted = %+v, want one record to release-6", posted)
	}
}

func TestAMergeOutsideTheQueueWarnsOwnerRelayOnceUnlessHisWordCoversThatHead(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	ctx := context.Background()
	s := storeWith(t, "lane-7")
	merged := func(number int, head, by string, word func(*machine.Facts)) pass.Landing {
		f := machine.Facts{Repo: monorepo, Number: number, Head: head, Lane: "lane-7", Merged: true, MergedBy: by}
		word(&f)
		return pass.Landing{Facts: f, LastOpen: t0}
	}
	none := func(*machine.Facts) {}
	if _, err := s.PutFact(monorepo, 6, "6666666666aa", "incident-squash", "merged", t0); err != nil {
		t.Fatal(err)
	}
	landings := []pass.Landing{
		merged(41, "4141414141dd", "someone", none),
		merged(1, "1111111111aa", "trunk-io", none),
		merged(2, "2222222222aa", "someone", func(f *machine.Facts) { f.Tap = machine.TapTapped }),
		merged(3, "3333333333aa", "someone", func(f *machine.Facts) { f.Tap = machine.TapConditional }),
		merged(4, "4444444444aa", "someone", func(f *machine.Facts) { f.Exempt = true }),
		merged(5, "5555555555aa", "someone", func(f *machine.Facts) { f.StandingWord = gates.FlakeFixClass }),
		merged(6, "6666666666aa", "someone", none),
		merged(7, "7777777777aa", "", none),
	}

	for range 2 {
		if err := landing.Announce(ctx, s, "d", "dev", landings, func(string) string { return "root" }); err != nil {
			t.Fatal(err)
		}
	}

	posted, err := client.Tail(ctx, client.Query{Drive: "d", Kinds: []string{"report"}, Lanes: []string{"supervisor"}})
	if err != nil {
		t.Fatal(err)
	}
	want := "MERGED-OUTSIDE-QUEUE #41 at 4141414141: someone merged it outside the merge queue, and the drive holds no owner tap, go, standing word or incident squash for that head"
	if len(posted) != 1 || posted[0].Text != want || !slices.Equal(posted[0].To, []string{"owner-relay"}) {
		t.Fatalf("posted = %+v, want one warning to owner-relay for #41 alone", posted)
	}
}

func TestAPersonsUntappedQueueEntryWarnsOwnerRelayOncePerHead(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	ctx := context.Background()
	queued := func(number int, word func(*machine.Facts)) machine.Facts {
		f := machine.Facts{Repo: monorepo, Number: number, Head: "c04fc7c91aaa", Lane: "lane-7", Queue: machine.QueueQueued, QueuedHead: "c04fc7c91aaa", PersonQueued: true}
		word(&f)
		return f
	}
	snapshot := []machine.Facts{
		queued(41, func(*machine.Facts) {}),
		queued(42, func(f *machine.Facts) { f.PersonQueued = false }),
		queued(43, func(f *machine.Facts) { f.Tap = machine.TapTapped }),
		queued(44, func(f *machine.Facts) { f.Hold = machine.MoverRoot }),
		queued(45, func(f *machine.Facts) { f.QueuedHead = "0ld0ld0ld0ld" }),
	}

	for range 2 {
		if err := landing.WarnPersonQueued(ctx, "d", snapshot); err != nil {
			t.Fatal(err)
		}
	}

	posted, err := client.Tail(ctx, client.Query{Drive: "d", Kinds: []string{"report"}, Lanes: []string{"supervisor"}})
	if err != nil {
		t.Fatal(err)
	}
	if len(posted) != 1 || !strings.HasPrefix(posted[0].Text, "PERSON-QUEUED #41 at c04fc7c91a:") || !slices.Equal(posted[0].To, []string{"owner-relay"}) {
		t.Fatalf("posted = %+v, want one warning to owner-relay for #41 alone", posted)
	}
}

func storeWith(t *testing.T, running ...string) *store.Store {
	t.Helper()
	s := newStore(t)
	for _, lane := range running {
		if _, err := s.WantLane(store.Lane{Name: lane, State: store.LaneDesired}, t0); err != nil {
			t.Fatal(err)
		}
	}
	return s
}

func TestAQueueAnswerForAStoppedLaneReachesRootAndARefusalSaysUnowned(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	ctx := context.Background()
	s := storeWith(t, "ssql-4")
	if err := s.SetLaneState("ssql-4", store.LaneRetired, t0, "", ""); err != nil {
		t.Fatal(err)
	}
	f := machine.Facts{Repo: monorepo, Number: 48476, Head: "9b837a86bbcc", Lane: "ssql-4"}

	if err := landing.Settle(ctx, s, "d", []landing.Said{
		{f, "QUEUED", "REFUSED", "exit 1: enqueued nothing: no approved green bottom prefix", false},
		{f, "QUEUED", "REFUSED", "exit 0: #48476 QUEUED 9b837a86bb native stack covers #48476", false},
	}); err != nil {
		t.Fatal(err)
	}

	posted, err := client.Tail(ctx, client.Query{Drive: "d", Kinds: []string{"report"}, Lanes: []string{"supervisor"}})
	if err != nil {
		t.Fatal(err)
	}
	said := make([]string, 0, len(posted))
	for _, record := range posted {
		said = append(said, record.To[0]+": "+record.Text)
	}
	want := []string{
		"root: UNOWNED, ssql-4 is not running: REFUSED #48476 at 9b837a86bb: exit 1: enqueued nothing: no approved green bottom prefix",
		"root: QUEUED #48476 at 9b837a86bb: exit 0: #48476 QUEUED 9b837a86bb native stack covers #48476",
	}
	if !slices.Equal(said, want) {
		t.Fatalf("got %q, want %q", said, want)
	}
}

func retire(t *testing.T, s *store.Store, name, successor string) {
	t.Helper()
	for _, lane := range []string{name, successor} {
		if _, err := s.WantLane(store.Lane{Name: lane, State: store.LaneDesired}, t0); err != nil {
			t.Fatal(err)
		}
	}
	if err := s.SetLaneState(name, store.LaneRetired, t0, successor, ""); err != nil {
		t.Fatal(err)
	}
}

func TestEachHeadCarriesOneRecordOfWhatTheQueueSaid(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	ctx := context.Background()
	queued := machine.Facts{Repo: monorepo, Number: 48897, Head: "1c40b68010aa", Lane: "ssql-3"}
	refused := machine.Facts{Repo: monorepo, Number: 48476, Head: "9b837a86bbcc", Lane: "ssql-4"}
	unowned := machine.Facts{Repo: monorepo, Number: 48165, Head: "dde89028ddee"}
	answers := []landing.Said{
		{queued, "QUEUED", "REFUSED", "exit 0: #48897 QUEUED 1c40b68010 native stack covers #48897", false},
		{refused, "QUEUED", "REFUSED", "exit 1: enqueued nothing: no approved green bottom prefix", false},
		{unowned, "QUEUED", "REFUSED", "timed out after 300s", false},
		{refused, "DEQUEUED", "DEQUEUE-REFUSED", "an open hold names it; the queue said exit 0: #48476 DEQUEUED 9b837a86bb", false},
	}

	for range 2 {
		if err := landing.Settle(ctx, storeWith(t, "ssql-3", "ssql-4"), "d", answers); err != nil {
			t.Fatal(err)
		}
	}

	posted, err := client.Tail(ctx, client.Query{Drive: "d", Kinds: []string{"report"}, Lanes: []string{"supervisor"}})
	if err != nil {
		t.Fatal(err)
	}
	said := make([]string, 0, len(posted))
	for _, record := range posted {
		said = append(said, record.To[0]+": "+record.Text)
	}
	want := []string{
		"ssql-3: QUEUED #48897 at 1c40b68010: exit 0: #48897 QUEUED 1c40b68010 native stack covers #48897",
		"ssql-4: REFUSED #48476 at 9b837a86bb: exit 1: enqueued nothing: no approved green bottom prefix",
		"root: REFUSED #48165 at dde89028dd: timed out after 300s",
		"ssql-4: DEQUEUED #48476 at 9b837a86bb: an open hold names it; the queue said exit 0: #48476 DEQUEUED 9b837a86bb",
	}
	if !slices.Equal(said, want) {
		t.Fatalf("got %q, want %q", said, want)
	}
}

func TestAnUntappedDequeueIsABlockerToItsLaneOwnerRelayAndTheMetaDesk(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	ctx := context.Background()
	s := storeWith(t, "meta-desk-3", "ssql-5")
	if err := s.SetLaneRole("meta-desk-3", "meta"); err != nil {
		t.Fatal(err)
	}
	untapped := machine.Facts{Repo: monorepo, Number: 46927, Head: "4a9c2e71d0bb", QueuedHead: "4a9c2e71d0bb", Queue: machine.QueueQueued, Lane: "ssql-5"}
	answer := landing.Said{Facts: untapped, Accepted: "UNTAPPED-DEQUEUED", Refused: "UNTAPPED-DEQUEUE-REFUSED", Text: "the queue said exit 0: #46927 DEQUEUED", Alarm: true}

	if err := landing.Settle(ctx, s, "d", []landing.Said{answer}); err != nil {
		t.Fatal(err)
	}

	posted, err := client.Tail(ctx, client.Query{Drive: "d", Kinds: []string{"blocker"}, Lanes: []string{"supervisor"}})
	if err != nil {
		t.Fatal(err)
	}
	if len(posted) != 1 || !slices.Equal(posted[0].To, []string{"ssql-5", "owner-relay", "meta-desk-3"}) || !strings.HasPrefix(posted[0].Text, "UNTAPPED-DEQUEUED #46927") {
		t.Fatalf("posted = %+v", posted)
	}

	answer.Text = "the queue said exit 0: #46927 DEQUEUED again"
	if err := landing.Settle(ctx, s, "d", []landing.Said{answer}); err != nil {
		t.Fatal(err)
	}
	again, err := client.Tail(ctx, client.Query{Drive: "d", Kinds: []string{"blocker"}, Lanes: []string{"supervisor"}})
	if err != nil {
		t.Fatal(err)
	}
	if len(again) != 2 {
		t.Fatalf("a second dequeue at the same head posted nothing: %+v", again)
	}
}
