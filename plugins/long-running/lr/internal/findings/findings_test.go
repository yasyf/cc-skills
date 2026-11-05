package findings

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ledger"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ledger/ledgertest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/owner"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/scaletest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const ledgerID = "l1"

type fakeBus struct {
	records []client.Record
	queries []client.Query
}

func (f *fakeBus) Tail(_ context.Context, q client.Query) ([]client.Record, error) {
	f.queries = append(f.queries, q)
	var out []client.Record
	for _, record := range f.records {
		if record.Seq > q.After {
			out = append(out, record)
		}
	}
	return out, nil
}

func (f *fakeBus) Among(_ context.Context, _ string, seqs []int64) ([]client.Record, error) {
	var out []client.Record
	for _, record := range f.records {
		for _, seq := range seqs {
			if record.Seq == seq {
				out = append(out, record)
			}
		}
	}
	return out, nil
}

func (f *fakeBus) add(seq int64, kind, lane, text string, change ...func(*client.Record)) {
	record := client.Record{Seq: seq, Lane: lane, Text: text, At: time.Date(2026, 11, 3, 18, 5, 35, 400_000_000, time.UTC)}
	setKind(&record.Kind, kind)
	for _, apply := range change {
		apply(&record)
	}
	f.records = append(f.records, record)
}

func setKind[K ~string](target *K, kind string) { *target = K(kind) }

type countedStore struct {
	*ledgertest.Fake
	reads, writes int
}

func (c *countedStore) Rows(ctx context.Context, id string) ([]ledger.Row, error) {
	c.reads++
	return c.Fake.Rows(ctx, id)
}

func (c *countedStore) Sync(ctx context.Context, id string, rows []ledger.Row) error {
	c.writes++
	return c.Fake.Sync(ctx, id, rows)
}

type world struct {
	t     *testing.T
	book  Book
	bus   *fakeBus
	store *countedStore
	shell *shelltest.Fake
}

func newWorld(t *testing.T, cursor int64) *world {
	t.Helper()
	state := t.TempDir()
	data, err := json.Marshal(saved{Ledger: ledgerID, Cursor: cursor})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(state, stateFile), data, 0o600); err != nil {
		t.Fatal(err)
	}
	fake := &shelltest.Fake{}
	fake.Reply(`[{"tags":["board-answer","board-block:card-x-pick","board-card:card-x"]},{"tags":["board-block:work-a","board-card:card-work-picks"]}]`, "ccn", "-R", "/co", "answer", "list")
	store, bus := &countedStore{Fake: &ledgertest.Fake{}}, &fakeBus{}
	return &world{t, Book{drive.Entry{Drive: "d", CCIDrive: "d", StateDir: state, Checkout: "/co"}, fake, store, bus, func() time.Time { return time.Date(2026, 11, 4, 0, 0, 0, 0, time.UTC) }}, bus, store, fake}
}

func (w *world) row(key, seq, kind, box, pr string) {
	w.t.Helper()
	fields := Fields{"seq": seq, "kind": kind, "card": box, "pr": pr, "state": stateOpen, "by": "", "owner": coordinator, "sweep": "owner", "at": "2026-11-03T18:05:35.4Z", "text": "t"}
	if err := w.store.SetRow(context.Background(), ledgerID, key, fields); err != nil {
		w.t.Fatal(err)
	}
}

func (w *world) sync(since string) Rows {
	w.t.Helper()
	rows, err := w.book.Sync(context.Background(), since)
	if err != nil {
		w.t.Fatal(err)
	}
	return rows
}

func (w *world) stored(key string) Fields {
	w.t.Helper()
	rows, err := w.store.Rows(context.Background(), ledgerID)
	if err != nil {
		w.t.Fatal(err)
	}
	for _, row := range rows {
		if row.Key == key {
			return row.Fields
		}
	}
	w.t.Fatalf("no row %s in %+v", key, rows)
	return nil
}

func (w *world) expect(key, state, by string) {
	w.t.Helper()
	row := w.stored(key)
	if row["state"] != state || row["by"] != by {
		w.t.Fatalf("%s = %s by %q, want %s by %q", key, row["state"], row["by"], state, by)
	}
}

func TestAPickSettlesOnItsNameInAnEndingRecord(t *testing.T) {
	cases := []struct {
		name, kind, lane, text string
		state, by              string
	}{
		{"head names the card, not the box", "head", "lane-a", "HEAD #1 abc card-x.", stateFixed, "20"},
		{"landed names the box", "landed", "lane-a", "merged card-x-pick", stateFixed, "20"},
		{"claim names the card", "claim", "lane-a", "lane-a claims card-x", stateFixed, "20"},
		{"claim names the seq", "claim", "lane-a", "claiming #10 now", stateFixed, "20"},
		{"root's decide assigns it", "decide", "root", "lane=lane-a claims card-x", stateFixed, "20"},
		{"another lane's decide only relays", "decide", "owner-relay", "owner tap on card-x", stateOpen, ""},
		{"an owed decide does not end it", "decide", "owed-picks-1", "card-x is still owed", stateOpen, ""},
		{"a report names it with no proof", "report", "lane-a", "looking at card-x", stateOpen, ""},
		{"a longer card is a different card", "head", "lane-a", "HEAD card-x-2", stateOpen, ""},
		{"an opened record routes it", "opened", "lane-b", "opened for card-x", stateRouted, "20"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			w := newWorld(t, 15)
			w.row("pick/10", "10", kindPick, "card-x-pick", "")
			w.bus.add(20, c.kind, c.lane, c.text)
			w.sync("")
			w.expect("pick/10", c.state, c.by)
		})
	}
}

func TestACardNameEndsEveryBoxOnItAndOnlyThoseBoxes(t *testing.T) {
	w := newWorld(t, 15)
	w.row("pick/10", "10", kindPick, "work-a", "")
	w.row("pick/11", "11", kindPick, "work-b", "")
	w.bus.add(20, "claim", "lane-a", "lane-a claims card-work-picks")
	w.sync("")
	w.expect("pick/10", stateFixed, "20")
	w.expect("pick/11", stateOpen, "")
}

func TestADeliveredReportEndsThePickItsLedgerRowCameFromAndARetractionReopensIt(t *testing.T) {
	w := newWorld(t, 15)
	ledgerPath := owner.Ledger(w.book.Entry.StateDir)
	if err := os.MkdirAll(filepath.Dir(ledgerPath), 0o750); err != nil {
		t.Fatal(err)
	}
	line := "| m123 | Nov 3 10:05 AM | Owner on board d50e80e8, Cut them?, Say. (card-x-pick): picked “Cut” | 10 | addressed |\n| m124 | Nov 3 10:06 AM | a plain order | | open |\n"
	if err := os.WriteFile(ledgerPath, []byte(line), 0o600); err != nil {
		t.Fatal(err)
	}
	w.row("pick/10", "10", kindPick, "card-x-pick", "")
	w.row("pick/11", "11", kindPick, "other-pick", "")
	attr := func(name, value string) func(*client.Record) {
		return func(r *client.Record) { r.Fields.Attrs = map[string]string{name: value} }
	}
	w.bus.add(20, "report", "lane-a", "carried out, no names here", attr("delivered", "owner:m123"))
	w.sync("")
	w.expect("pick/10", stateFixed, "20")
	w.expect("pick/11", stateOpen, "")

	w.bus.add(21, "correction", "lane-a", "my error", attr("undelivered", "owner:m123"))
	w.sync("")
	w.expect("pick/10", stateOpen, "")
}

func TestAFindingEndsOnlyOnAHeadOrLandingNamingItsPROrSeq(t *testing.T) {
	cases := []struct {
		name, kind, text string
		change           []func(*client.Record)
		state, by        string
	}{
		{"head names the PR", "head", "HEAD #77 abc", nil, stateFixed, "20"},
		{"landed names the seq", "landed", "fixes #10", nil, stateFixed, "20"},
		{"a PR ref counts", "landed", "merged", []func(*client.Record){func(r *client.Record) {
			if err := json.Unmarshal([]byte(`{"prs":["o/r#77"]}`), &r.Refs); err != nil {
				t.Fatal(err)
			}
		}}, stateFixed, "20"},
		{"a claim is not a fix", "claim", "claiming #77", nil, stateOpen, ""},
		{"a decide is not a fix", "decide", "accepted #77", nil, stateOpen, ""},
		{"an answer routes it", "answer", "see above", []func(*client.Record){func(r *client.Record) { r.Re = 10 }}, stateRouted, "20"},
		{"a resolve routes it", "note", "done with it", []func(*client.Record){func(r *client.Record) { r.Resolves = 10 }}, stateRouted, "20"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			w := newWorld(t, 15)
			w.row("finding/10", "10", kindFinding, "", "77")
			w.bus.add(20, c.kind, "root", c.text, c.change...)
			w.sync("")
			w.expect("finding/10", c.state, c.by)
		})
	}
}

func TestSyncOpensEachNewFindingAndPickWithTheLedgersFields(t *testing.T) {
	w := newWorld(t, 5)
	w.bus.add(6, "defect", "red-sweep", "[finding:red-sweep] #42 a.go:7 major owner lane-a: broken\nthing")
	w.bus.add(7, "defect", "red-sweep", "[finding:red-sweep] pr#43 needs a look", func(r *client.Record) { r.To = []string{"root", "lane-b"} })
	w.bus.add(8, "owner", "board-watch", "Owner on board d50e80e8, Cut them?, Say. (card-x-pick): picked “Cut”")
	w.bus.add(9, "defect", "lane-a", "a defect with no finding tag")
	w.sync("")

	if got := w.stored("finding/6"); got["pr"] != "42" || got["line"] != "a.go:7" || got["verdict"] != "major" || got["owner"] != "lane-a" || got["text"] != "broken\nthing" || got["sweep"] != "red-sweep" || got["kind"] != kindFinding || got["at"] != "2026-11-03T18:05:35.4Z" {
		t.Fatalf("finding/6 = %v", got)
	}
	if got := w.stored("finding/7"); got["pr"] != "43" || got["owner"] != "lane-b" || got["text"] != "pr#43 needs a look" {
		t.Fatalf("finding/7 = %v", got)
	}
	if got := w.stored("pick/8"); got["card"] != "card-x-pick" || got["text"] != "Cut them?, Say.: Cut" || got["owner"] != coordinator || got["verdict"] != "pick" || got["state"] != stateOpen {
		t.Fatalf("pick/8 = %v", got)
	}
}

func TestALaterPickReplacesALiveRowsAnswerAndOpensANewRowOnceItEnded(t *testing.T) {
	w := newWorld(t, 5)
	w.bus.add(6, "owner", "board-watch", "Owner on board d1, Q (card-x-pick): picked “first”")
	w.bus.add(7, "owner", "board-watch", "Owner on board d1, Q (card-x-pick): wrote “second”")
	w.sync("")
	if got := w.stored("pick/6"); got["seq"] != "7" || got["text"] != "Q: second" {
		t.Fatalf("pick/6 = %v", got)
	}

	w.bus.add(8, "landed", "lane-a", "carried out card-x")
	w.bus.add(9, "owner", "board-watch", "Owner on board d1, Q (card-x-pick): picked “third”")
	w.sync("")
	w.expect("pick/6", stateFixed, "8")
	w.expect("pick/9", stateOpen, "")
}

func TestReadingAnOlderWindowSettlesLiveRowsAndOpensNothingBehindTheCursor(t *testing.T) {
	w := newWorld(t, 100)
	w.row("pick/10", "10", kindPick, "card-x-pick", "")
	if err := w.store.SetRow(context.Background(), ledgerID, "pick/30", Fields{"seq": "40", "kind": kindPick, "card": "other-pick", "state": stateClosed, "by": "by hand"}); err != nil {
		t.Fatal(err)
	}
	w.bus.add(20, "claim", "lane-a", "lane-a claims card-x")
	w.bus.add(35, "owner", "board-watch", "Owner on board d1, Q (other-pick): picked “a replaced answer”")
	w.sync("5")
	w.expect("pick/10", stateFixed, "20")
	w.expect("pick/30", stateClosed, "by hand")
	rows, err := w.store.Rows(context.Background(), ledgerID)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 2 {
		t.Fatalf("rows = %+v", rows)
	}
	state, err := w.book.load()
	if err != nil {
		t.Fatal(err)
	}
	if state.Cursor != 100 {
		t.Fatalf("cursor = %d", state.Cursor)
	}
}

func TestTheNextPassReadsFromTheNewestRecordItSaw(t *testing.T) {
	w := newWorld(t, 5)
	w.bus.add(6, "note", "lane-a", "nothing")
	w.sync("")
	w.sync("")
	if len(w.bus.queries) != 2 || w.bus.queries[1].After != 6 {
		t.Fatalf("queries = %+v", w.bus.queries)
	}
}

func TestAPassReadsAndWritesOnceHoweverManyRowsAreLive(t *testing.T) {
	type counts struct{ tails, rowReads, rowWrites, answers int }
	pass := func(n int) counts {
		w := newWorld(t, 5)
		for i := range n {
			seq := strconv.Itoa(i + 1)
			w.row("pick/"+seq, seq, kindPick, "box-"+seq, "")
		}
		for i := range n {
			w.bus.add(int64(1000+i), "head", "lane-a", fmt.Sprintf("HEAD names box-%d", i+1))
		}
		w.sync("")
		w.expect("pick/"+strconv.Itoa(n), stateFixed, strconv.Itoa(1000+n-1))
		got := counts{tails: len(w.bus.queries), rowReads: w.store.reads - 1, rowWrites: w.store.writes}
		for _, call := range w.shell.Calls() {
			if strings.Contains(strings.Join(call.Argv, " "), "answer list") {
				got.answers++
			}
		}
		return got
	}
	want := counts{tails: 1, rowReads: 1, rowWrites: 1, answers: 1}
	if one, many := pass(1), pass(scaletest.N()); one != want || many != want {
		t.Fatalf("n=1: %+v; n=%d: %+v; want %+v", one, scaletest.N(), many, want)
	}
}

func TestAPickIsCitedByAnyRecordNamingItFromAnyoneButTheBoardWatcher(t *testing.T) {
	cases := []struct {
		name, kind, lane, text string
		seq                    int64
		cited                  bool
	}{
		{"a lane's report names the card, as 422041 did", "report", "storage-eviction-apply-1", "Applying card-x: checking whether each head does what his pick says.", 20, true},
		{"a claim names the box", "claim", "lane-a", "claiming card-x-pick", 20, true},
		{"a note names the seq with its mark", "note", "lane-a", "on #10 now", 20, true},
		{"an answer names the bare seq", "answer", "owner-relay", "his pick (10) is held", 20, true},
		{"the board watcher's own repost does not", "ask", "board-watch", "UNCITED owner pick #10: nothing names card-x-pick", 20, false},
		{"a longer card is a different card", "report", "lane-a", "card-x-2 moved", 20, false},
		{"a record before his pick does not", "report", "lane-a", "drawn as card-x", 5, false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			w := newWorld(t, 100)
			w.bus.add(c.seq, c.kind, c.lane, c.text)
			found, err := w.book.Cited(context.Background(), map[int64]string{10: "card-x-pick"})
			if err != nil {
				t.Fatal(err)
			}
			if got := len(found) == 1 && found[0] == 10; got != c.cited {
				t.Fatalf("cited = %v, want %v", found, c.cited)
			}
		})
	}
}

func TestCitedReadsRecordsOnceHoweverManyPicksItIsAskedAbout(t *testing.T) {
	reads := func(n int) int {
		w := newWorld(t, 5)
		picks := make(map[int64]string, n)
		for i := range n {
			seq := int64(i + 1)
			picks[seq] = fmt.Sprintf("box-%d", seq)
			w.bus.add(int64(5000+i), "report", "lane-a", fmt.Sprintf("working on box-%d", seq))
		}
		found, err := w.book.Cited(context.Background(), picks)
		if err != nil {
			t.Fatal(err)
		}
		if len(found) != n {
			t.Fatalf("cited %d of %d", len(found), n)
		}
		return len(w.bus.queries)
	}
	if one, many := reads(1), reads(scaletest.N()); one != 1 || many != 1 {
		t.Fatalf("reads: n=1 %d, n=%d %d", one, scaletest.N(), many)
	}
}

func TestALandPickIsCarriedOnceItsRowReadsTappedLandedOrAtANewerHead(t *testing.T) {
	picked := time.Date(2026, 11, 5, 11, 53, 50, 0, time.UTC)
	cases := []struct {
		name    string
		row     store.ReviewRow
		carried bool
	}{
		{"his tap in the row, as #50690 read for 471534", store.ReviewRow{Head: "abc1234", Tapped: true, TappedAt: store.Stamp(picked)}, true},
		{"a row not tapped", store.ReviewRow{Head: "abc1234"}, false},
		{"a tap from before the pick", store.ReviewRow{Head: "abc1234", Tapped: true, TappedAt: store.Stamp(picked.Add(-time.Hour))}, false},
		{"its PR merged on the tap, as #51297 did for 494784", store.ReviewRow{Head: "abc1234", State: prrow.StateLanded, LandedAt: store.Stamp(picked.Add(-14 * time.Minute))}, true},
		{"a newer head on its row, as #50584 had for 486606", store.ReviewRow{Head: "def5678", HeadAt: store.Stamp(picked.Add(time.Hour))}, true},
		{"a row behind the tapped head", store.ReviewRow{Head: "0ld0000", HeadAt: store.Stamp(picked.Add(-time.Hour))}, false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			w := newWorld(t, 100)
			w.book.Entry.Repo = "acme/app"
			w.bus.add(10, "owner", "board-watch", "Owner on board d1, Approve #7? (card-x-pick): picked “Approve, land it” at acme/app#7 abc1234", func(r *client.Record) {
				r.At = picked
				r.Fields.Attrs = map[string]string{"event": "choice.selected", "block": "card-x-pick", "picked": "o1", "heads": "acme/app#7=abc1234"}
			})
			s, err := store.Open(w.book.Entry.Store())
			if err != nil {
				t.Fatal(err)
			}
			row := c.row
			row.Repo, row.Number = "acme/app", 7
			if err := s.Update(func(tx *store.Tx) error { return tx.PutReviewRows([]store.ReviewRow{row}) }); err != nil {
				t.Fatal(err)
			}
			if err := s.Close(); err != nil {
				t.Fatal(err)
			}
			found, err := w.book.Cited(context.Background(), map[int64]string{10: "card-x-pick"})
			if err != nil {
				t.Fatal(err)
			}
			if got := len(found) == 1 && found[0] == 10; got != c.carried {
				t.Fatalf("carried = %v, want %v", found, c.carried)
			}
		})
	}
}

func TestTrackAddsAnOlderRecordSettledAgainstTheRecordsAfterIt(t *testing.T) {
	w := newWorld(t, 100)
	w.bus.add(6, "owner", "board-watch", "Owner on board d1, Q (card-x-pick): picked “a”")
	w.bus.add(7, "claim", "lane-a", "lane-a claims card-x")
	if err := w.book.Track(context.Background(), []int64{6}); err != nil {
		t.Fatal(err)
	}
	w.expect("pick/6", stateFixed, "7")
	w.bus.add(8, "note", "lane-a", "nothing")
	if err := w.book.Track(context.Background(), []int64{8}); err == nil {
		t.Fatal("tracking a record that is neither a finding nor a pick must fail")
	}
}

func TestCloseEndsOnlyTheNamedRowsAndLiveListsWhatStands(t *testing.T) {
	w := newWorld(t, 100)
	w.row("pick/10", "10", kindPick, "card-x-pick", "")
	w.row("pick/11", "11", kindPick, "other-pick", "")
	if err := w.book.Close(context.Background(), "carried out, m123", []int64{10}); err != nil {
		t.Fatal(err)
	}
	w.expect("pick/10", stateClosed, "carried out, m123")
	listed := Live(w.sync(""), "", "")
	if len(listed) != 1 || listed[0]["seq"] != "11" {
		t.Fatalf("live = %v", listed)
	}
}

func TestPrintingRowsIsOneJSONRowPerLineOrGroupedBySweep(t *testing.T) {
	rows := []Fields{
		{"seq": "1", "sweep": "a", "state": stateOpen, "at": "2026-11-03T18:05:35.4Z", "pr": "9", "line": "x.go:1", "owner": "l", "text": "t"},
		{"seq": "2", "sweep": "a", "state": stateRouted, "at": "2026-11-03T18:05:35.4Z", "pr": "", "line": "", "owner": "l", "text": "u"},
	}
	var text, encoded strings.Builder
	if err := printRows(&text, rows, false); err != nil {
		t.Fatal(err)
	}
	if err := printRows(&encoded, rows, true); err != nil {
		t.Fatal(err)
	}
	if want := "a:\n  #1 open Nov 3 10:05 AM #9 x.go:1 owner l: t\n  #2 routed Nov 3 10:05 AM owner l: u\n"; text.String() != want {
		t.Fatalf("text = %q", text.String())
	}
	if lines := strings.Split(strings.TrimSpace(encoded.String()), "\n"); len(lines) != 2 || !strings.Contains(lines[0], `"seq":"1"`) {
		t.Fatalf("json = %q", encoded.String())
	}
}
