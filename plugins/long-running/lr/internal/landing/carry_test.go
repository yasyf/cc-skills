package landing_test

import (
	"context"
	"encoding/json"
	"os"
	"testing"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/landing"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
)

const (
	tappedAt = "a28ab62"
	restack  = "ff335d010b97913905953a4b5028bb2c7d6721ee"
)

func TestTheOwnersNov2TapOn46107CarriesToItsSameDiffRestackOnTheSessionHeTappedInNamingItsProof(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	ctx := context.Background()
	data, err := os.ReadFile("testdata/tap-46107.json")
	if err != nil {
		t.Fatal(err)
	}
	var tap client.Record
	if err := json.Unmarshal(data, &tap); err != nil {
		t.Fatal(err)
	}
	if _, err := client.Post(ctx, client.Draft{Drive: "d", Lane: tap.Lane, Kind: "owner", Text: "owner tap", Topic: tap.Topic, PRs: []string{monorepo + "#46107"}, Attrs: tap.Fields.Attrs}); err != nil {
		t.Fatal(err)
	}
	carried := machine.Facts{Repo: monorepo, Number: 46107, Head: restack, Tap: machine.TapTapped, TapFrom: tappedAt, CarriedBy: "same-diff"}

	if err := landing.CarryTaps(ctx, "d", []machine.Facts{carried}); err != nil {
		t.Fatal(err)
	}

	writes, err := client.Tail(ctx, client.Query{Drive: "d", Kinds: []string{"note"}, Lanes: []string{"supervisor"}})
	if err != nil {
		t.Fatal(err)
	}
	if len(writes) != 1 {
		t.Fatalf("writes = %+v, want one", writes)
	}
	attrs := writes[0].Fields.Attrs
	if writes[0].Topic != "board-write" || attrs["op"] != "carry" || attrs["session"] != tap.Fields.Attrs["session"] || attrs["block"] != "merge-prs" || attrs["row"] != "46107" || attrs["head"] != restack || attrs["from"] != tappedAt || attrs["proof"] != "same-diff" {
		t.Fatalf("write = topic %q attrs %v", writes[0].Topic, attrs)
	}
	if attrs["note"] != "tap carried: the PR's own diff is the same as at the tapped head" {
		t.Fatalf("note = %q", attrs["note"])
	}
}

func TestTapCarriedSaysHisTapCountsOnceForThePR(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	ctx := context.Background()
	carried := machine.Facts{Repo: monorepo, Number: 46107, Head: restack, Tap: machine.TapTapped, TapFrom: tappedAt, CarriedBy: gates.ByTap}

	if err := landing.CarryTaps(ctx, "d", []machine.Facts{carried}); err != nil {
		t.Fatal(err)
	}

	writes, err := client.Tail(ctx, client.Query{Drive: "d", Kinds: []string{"note"}, Lanes: []string{"supervisor"}})
	if err != nil {
		t.Fatal(err)
	}
	want := "TAP-CARRIED #46107 from a28ab62 to ff335d010b by tap: his tap counts once for the PR, and no push since changed it fundamentally"
	if len(writes) != 1 || writes[0].Text != want || writes[0].Fields.Attrs["proof"] != gates.ByTap {
		t.Fatalf("writes = %+v, want one reading %q", writes, want)
	}
}
