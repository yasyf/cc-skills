package landing

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx/ccxtest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/wait"
)

func TestOnlyTheOwnersOwnWordNamingThePROrdersIt(t *testing.T) {
	for _, tt := range []struct {
		name, kind, lane, text string
		want                   bool
	}{
		{"a relayed thread answer", "owner", "owner-relay", "Owner in thread: merge #7", true},
		{"a later relay", "owner", "owner-relay-2", "Owner in thread: merge #7", true},
		{"a board tap", "owner", "board-watch", "Owner on board: Land #7 now", true},
		{"a lane's own word", "owner", "root", "Enqueue #7 now", false},
		{"his word on another PR", "owner", "owner-relay", "Owner in thread: merge #70", false},
		{"a relay report naming the PR", "report", "owner-relay", "Applied his tap on #7", false},
		{"a relay report of taps owed", "report", "owner-relay", "his taps owed on #7 #8 #9", false},
		{"a relay answer naming the PR", "answer", "owner-relay", "His tap: #7 lands", false},
	} {
		t.Run(tt.name, func(t *testing.T) {
			raw, err := json.Marshal(map[string]string{"kind": tt.kind, "lane": tt.lane, "text": tt.text})
			if err != nil {
				t.Fatal(err)
			}
			var record client.Record
			if err := json.Unmarshal(raw, &record); err != nil {
				t.Fatal(err)
			}
			if got := ownerWordGap(record, 7) == ""; got != tt.want {
				t.Fatalf("got %v, want %v", got, tt.want)
			}
		})
	}
}

func TestAWordReadsWholePastItsClip(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	ctx := context.Background()
	long := "Owner in thread, verbatim:\n" + strings.Repeat("the whole stack goes now ", 30) + "\nmerge #7"
	read := func(kind string) client.Record {
		t.Helper()
		posted, err := client.Post(ctx, client.Draft{Drive: "d", Lane: "owner-relay", Kind: kind, To: []string{"root"}, Text: long})
		if err != nil {
			t.Fatal(err)
		}
		records, err := client.Among(ctx, "d", []int64{posted.Seq})
		if err != nil {
			t.Fatal(err)
		}
		if len(records) != 1 || strings.Contains(records[0].Text, "#7") {
			t.Fatalf("records = %+v, want one clipped before #7", records)
		}
		return records[0]
	}

	word := read("owner")
	report := read("report")

	if ownerWordGap(word, 7) != "" {
		t.Fatal("his word naming #7 past the clip was refused")
	}
	if ownerWordGap(word, 8) == "" {
		t.Fatal("his word on #7 ordered #8")
	}
	if ownerWordGap(report, 7) == "" {
		t.Fatal("a relay report naming #7 past the clip ordered it")
	}
}

func TestAWordNamingThePRInItsTextOrdersItWhateverItsBodySays(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	ctx := context.Background()
	body := filepath.Join(t.TempDir(), "body.txt")
	if err := os.WriteFile(body, []byte("His tap on the card. ci-speed runs lr landing order 50346 --record with this seq at the graded head."), 0o600); err != nil {
		t.Fatal(err)
	}
	read := func(text string) client.Record {
		t.Helper()
		posted, err := client.Post(ctx, client.Draft{Drive: "d", Lane: "owner-relay", Kind: "owner", To: []string{"ci-speed"}, Text: text, PRs: []string{"o/r#50346"}, Path: body})
		if err != nil {
			t.Fatal(err)
		}
		records, err := client.Among(ctx, "d", []int64{posted.Seq})
		if err != nil {
			t.Fatal(err)
		}
		if len(records) != 1 || records[0].Refs.Path == "" {
			t.Fatalf("records = %+v, want one with its body apart", records)
		}
		return records[0]
	}

	if ownerWordGap(read(`Owner: "ship #50346" #50346`), 50346) != "" {
		t.Fatal("his word naming #50346 in its text was refused for a body that names it bare")
	}
	if ownerWordGap(read(`Owner: "ship #50347" #50347`), 50346) == "" {
		t.Fatal("his word on #50347 ordered #50346")
	}
}

func TestARefusedRecordNamesTheOneToPostAndWritesNothing(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	ctx := context.Background()
	config := filepath.Join(t.TempDir(), "lr.json")
	if err := os.WriteFile(config, []byte(`{"repo":"o/r","drive":"d","checkout":"/c","author":"a","label":"l","movers":["lanes"]}`), 0o600); err != nil {
		t.Fatal(err)
	}
	owed, err := client.Post(ctx, client.Draft{Drive: "d", Lane: "owner-relay", Kind: "report", To: []string{"root"}, Text: "his taps owed on #7"})
	if err != nil {
		t.Fatal(err)
	}

	var out bytes.Buffer
	err = runOrder(ctx, &app.App{ConfigPath: config}, 7, owed.Seq, &out)

	for _, want := range []string{"it is a report", "cci post --drive d --lane owner-relay --kind owner --pr o/r#7"} {
		if err == nil || !strings.Contains(err.Error(), want) {
			t.Fatalf("err = %v, want it to name %q", err, want)
		}
	}
	written, err := client.Tail(ctx, client.Query{Drive: "d", After: owed.Seq})
	if err != nil {
		t.Fatal(err)
	}
	if len(written) != 0 {
		t.Fatalf("written = %+v", written)
	}
}

func TestAWordGivenAgainAtOneHeadIsANewOrder(t *testing.T) {
	const head = "e718a7434fa01ee9835f992a9a647a825350732c"
	t.Setenv("CCI_HOME", t.TempDir())
	ctx := context.Background()
	s, err := store.Memory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	if err := s.PutReviewRows([]store.ReviewRow{{Repo: "o/r", Number: 7, Head: head, ReadyForTap: true}}); err != nil {
		t.Fatal(err)
	}
	first, err := client.Post(ctx, client.Draft{Drive: "d", Lane: "owner-relay", Kind: "owner", Text: "Owner in thread: merge #7"})
	if err != nil {
		t.Fatal(err)
	}

	var out bytes.Buffer
	orders := bus.Bus{Drive: "d", Lane: "tap-orders"}
	if err := writeOrder(ctx, s, "o/r", orders, 7, first, "ci-speed", &out); err != nil {
		t.Fatal(err)
	}
	second, err := client.Post(ctx, client.Draft{Drive: "d", Lane: "owner-relay", Kind: "owner", Text: "Owner in thread: merge #7 after all"})
	if err != nil {
		t.Fatal(err)
	}
	if err := writeOrder(ctx, s, "o/r", orders, 7, second, "ci-speed", &out); err != nil {
		t.Fatal(err)
	}

	written, err := client.Tail(ctx, client.Query{Drive: "d", After: second.Seq})
	if err != nil {
		t.Fatal(err)
	}
	if len(written) != 1 {
		t.Fatalf("written = %+v", written)
	}
}

func TestOneWordOrdersEachPRAndEachHeadItCovers(t *testing.T) {
	const (
		first = "e718a7434fa01ee9835f992a9a647a825350732c"
		moved = "91eea694e47f88917ef2021aa5d7f7d9c72b431d"
	)
	t.Setenv("CCI_HOME", t.TempDir())
	ctx := context.Background()
	s, err := store.Memory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	word, err := client.Post(ctx, client.Draft{Drive: "d", Lane: "owner-relay", Kind: "owner", Text: "Owner in thread: merge #7 and #8"})
	if err != nil {
		t.Fatal(err)
	}
	orders := bus.Bus{Drive: "d", Lane: "tap-orders"}
	order := func(number int, head string) {
		t.Helper()
		if err := s.PutReviewRows([]store.ReviewRow{{Repo: "o/r", Number: number, Head: head, ReadyForTap: true}}); err != nil {
			t.Fatal(err)
		}
		var out bytes.Buffer
		if err := writeOrder(ctx, s, "o/r", orders, number, word, "ci-speed", &out); err != nil {
			t.Fatal(err)
		}
	}

	order(7, first)
	order(8, first)
	order(7, moved)
	order(7, moved)

	written, err := client.Tail(ctx, client.Query{Drive: "d", After: word.Seq})
	if err != nil {
		t.Fatal(err)
	}
	got := make([]string, 0, len(written))
	for _, record := range written {
		got = append(got, record.Fields.Attrs["row"]+" "+record.Fields.Attrs["head"][:7])
	}
	if want := []string{"7 e718a74", "8 e718a74", "7 91eea69"}; !slices.Equal(got, want) {
		t.Fatalf("written = %v, want %v", got, want)
	}
}

func TestAHandCarriedOrderTakesTheSameRowGateAsATap(t *testing.T) {
	const handHead = "e718a7434fa01ee9835f992a9a647a825350732c"
	for _, tt := range []struct {
		name    string
		row     store.ReviewRow
		kind    string
		refused bool
	}{
		{"a ready row", store.ReviewRow{Repo: "o/r", Number: 7, Head: handHead, ReadyForTap: true}, "owner", false},
		{"a row waiting on CI", prrow.Decide(store.ReviewRow{Repo: "o/r", Number: 7, Head: handHead, State: prrow.StateOpen, CI: prrow.CIPending, Hold: prrow.None, SupersededBy: prrow.None, ForUsers: "a user sees it", PlanRow: "plan step 1", Risk: "low"}), "owner", false},
		{"a held row", prrow.Decide(store.ReviewRow{Repo: "o/r", Number: 7, Head: handHead, State: prrow.StateOpen, Hold: "root:the cutover", SupersededBy: prrow.None}), "owner", false},
		{"a superseded row", prrow.Decide(store.ReviewRow{Repo: "o/r", Number: 7, Head: handHead, State: prrow.StateOpen, Hold: prrow.None, SupersededBy: "#8"}), bus.TapRefusedKind, true},
	} {
		t.Run(tt.name, func(t *testing.T) {
			t.Setenv("CCI_HOME", t.TempDir())
			ctx := context.Background()
			s, err := store.Memory()
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() { _ = s.Close() })
			if err := s.PutReviewRows([]store.ReviewRow{tt.row}); err != nil {
				t.Fatal(err)
			}
			word, err := client.Post(ctx, client.Draft{Drive: "d", Lane: "owner-relay", Kind: "owner", Text: "Owner in thread: merge #7"})
			if err != nil {
				t.Fatal(err)
			}

			var out bytes.Buffer
			err = writeOrder(ctx, s, "o/r", bus.Bus{Drive: "d", Lane: "tap-orders"}, 7, word, "ci-speed", &out)

			var exit *wait.ExitError
			if got := errors.As(err, &exit); got != tt.refused {
				t.Fatalf("err = %v, out = %s", err, out.String())
			}
			written, err := client.Tail(ctx, client.Query{Drive: "d", After: word.Seq})
			if err != nil {
				t.Fatal(err)
			}
			if len(written) != 1 || string(written[0].Kind) != tt.kind {
				t.Fatalf("written = %+v", written)
			}
			if tap, ordered := bus.OrderOf(written[0], "o/r"); ordered == tt.refused || !tt.refused && (tap.PR != 7 || tap.Head != handHead) {
				t.Fatalf("the landing host reads = %+v, %v", tap, ordered)
			}
			if tt.refused && !strings.Contains(out.String(), "Nothing: superseded, #8") {
				t.Fatalf("out = %s", out.String())
			}
		})
	}
}

func TestAHandCarriedOrderOnAStoreWithNoRowsYetPostsNothing(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	ctx := context.Background()
	s, err := store.Memory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	word, err := client.Post(ctx, client.Draft{Drive: "d", Lane: "owner-relay", Kind: "owner", Text: "Owner in thread: merge #7"})
	if err != nil {
		t.Fatal(err)
	}

	err = writeOrder(ctx, s, "o/r", bus.Bus{Drive: "d", Lane: "tap-orders"}, 7, word, "ci-speed", io.Discard)

	if !errors.Is(err, prrow.ErrRowsNotFolded) {
		t.Fatalf("err = %v", err)
	}
	if written, err := client.Tail(ctx, client.Query{Drive: "d", After: word.Seq}); err != nil || len(written) != 0 {
		t.Fatalf("written = %+v, %v", written, err)
	}
}

func driveWithNoLandStore(t *testing.T) {
	t.Helper()
	config := t.TempDir()
	registry := filepath.Join(config, "long-running", "drives")
	if err := os.MkdirAll(registry, 0o700); err != nil {
		t.Fatal(err)
	}
	entry, err := json.Marshal(drive.Entry{Drive: "d", CCIDrive: "d", Repo: "o/r", StateDir: t.TempDir(), Sessions: []string{"root"}})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(registry, "d.json"), entry, 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("CLAUDE_CONFIG_DIR", config)
	t.Setenv(drive.Env, "d")
	t.Setenv(lanes.LaneEnv, "sprite-lane")
	t.Setenv("CCI_HOME", t.TempDir())
}

func TestAnOrderFromABoxWithNoLandStoreGoesToTheHostAtTheFullHeadNamingItsWriter(t *testing.T) {
	const head = "2fd57ee43ba97bf1e8f71036797365920d50d1af"
	driveWithNoLandStore(t)
	ctx := context.Background()
	word, err := client.Post(ctx, client.Draft{Drive: "d", Lane: "owner-relay", Kind: "owner", Text: "Owner in thread: merge #7"})
	if err != nil {
		t.Fatal(err)
	}

	var out bytes.Buffer
	err = runOrder(ctx, &app.App{CCX: &ccxtest.Fake{Statuses: []ccx.PRStatus{{Number: 7, Head: head, State: "OPEN"}}}}, 7, word.Seq, &out)
	if err != nil {
		t.Fatalf("err = %v, out = %s", err, out.String())
	}
	written, err := client.Tail(ctx, client.Query{Drive: "d", After: word.Seq})
	if err != nil {
		t.Fatal(err)
	}
	if len(written) != 1 || written[0].Fields.Attrs[bus.WriterAttr] != "sprite-lane" {
		t.Fatalf("written = %+v", written)
	}
	if tap, ordered := bus.OrderOf(written[0], "o/r"); !ordered || tap.PR != 7 || tap.Head != head {
		t.Fatalf("the landing host reads = %+v, %v", tap, ordered)
	}
}

func TestAnOrderFromABoxWithNoLandStorePostsNothingForAPRNoLongerOpen(t *testing.T) {
	driveWithNoLandStore(t)
	ctx := context.Background()
	word, err := client.Post(ctx, client.Draft{Drive: "d", Lane: "owner-relay", Kind: "owner", Text: "Owner in thread: merge #7"})
	if err != nil {
		t.Fatal(err)
	}

	var out bytes.Buffer
	err = runOrder(ctx, &app.App{CCX: &ccxtest.Fake{Statuses: []ccx.PRStatus{{Number: 7, Head: "2fd57ee43ba97bf1e8f71036797365920d50d1af", State: "MERGED"}}}}, 7, word.Seq, &out)

	if err == nil || !strings.Contains(err.Error(), "no open PR") {
		t.Fatalf("err = %v", err)
	}
	written, err := client.Tail(ctx, client.Query{Drive: "d", After: word.Seq})
	if err != nil {
		t.Fatal(err)
	}
	if len(written) != 0 {
		t.Fatalf("written = %+v", written)
	}
}
