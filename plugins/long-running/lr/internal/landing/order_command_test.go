package landing

import (
	"bytes"
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/wait"
)

func TestOnlyTheOwnersOwnWordNamingThePROrdersIt(t *testing.T) {
	for _, tt := range []struct {
		name, lane, text string
		want             bool
	}{
		{"a relayed thread answer", "owner-relay", "Owner in thread: merge #7", true},
		{"a later relay", "owner-relay-2", "Owner in thread: merge #7", true},
		{"a board tap", "board-watch", "Owner on board: Land #7 now", true},
		{"a lane's own word", "root", "Enqueue #7 now", false},
		{"his word on another PR", "owner-relay", "Owner in thread: merge #70", false},
	} {
		t.Run(tt.name, func(t *testing.T) {
			if got := holdsOwnerWord(client.Record{Lane: tt.lane, Text: tt.text}, 7); got != tt.want {
				t.Fatalf("got %v, want %v", got, tt.want)
			}
		})
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
		{"a ready row", store.ReviewRow{Repo: "o/r", Number: 7, Head: handHead, ReadyForTap: true}, "lr.tap", false},
		{"a held row", store.ReviewRow{Repo: "o/r", Number: 7, Head: handHead, WaitsOn: "held by root"}, bus.TapRefusedKind, true},
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
			err = writeOrder(ctx, s, "o/r", bus.Bus{Drive: "d", Lane: "tap-orders"}, 7, word, &out)

			var exit *wait.ExitError
			if got := errors.As(err, &exit); got != tt.refused {
				t.Fatalf("err = %v, out = %s", err, out.String())
			}
			written, err := client.Tail(ctx, client.Query{Drive: "d", Kinds: []string{"lr.tap", bus.TapRefusedKind}})
			if err != nil {
				t.Fatal(err)
			}
			if len(written) != 1 || string(written[0].Kind) != tt.kind {
				t.Fatalf("written = %+v", written)
			}
			if tt.refused && !strings.Contains(out.String(), "held by root") {
				t.Fatalf("out = %s", out.String())
			}
		})
	}
}
