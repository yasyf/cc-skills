package landing_test

import (
	"context"
	"encoding/json"
	"os"
	"reflect"
	"strings"
	"testing"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/landing"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/pass"
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

	if err := landing.Announce(ctx, "d", "dev", landings, route); err != nil {
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

	if err := landing.Announce(ctx, "d", "dev", landings, func(string) string { return "root" }); err != nil {
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
