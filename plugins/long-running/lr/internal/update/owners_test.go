package update

import (
	"context"
	"fmt"
	"slices"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/scaletest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func TestOwnersReadASpriteAliasRowAndALaneQuietOverAnHourAsLiveAndNeverAMention(t *testing.T) {
	lanes := []Lane{
		{Name: "spa-edge-role", State: "unregistered", PRs: []int{50150, 50212}},
		heard("spa-edge-role-1", "desired", 2),
		heard("ship-2", "desired", 90),
		heard("stale-1", "desired", 300),
		heard("dd-331634912-fix", "desired", 1),
	}
	records := []Record{
		forPR(record(1, "ship-2", "head", "HEAD #48401 abc", 100), 48401),
		forPR(record(3, "spa-edge-role-1", "head", "HEAD #50150 aaa", 1), 50150),
		forPR(record(2, "stale-1", "head", "HEAD #7 def", 300), 7),
		forPR(record(5, "dd-331634912-fix", "head", "HEAD #51586 d43f890: waits for #48401 and #7", 1), 51586),
		{Seq: 4465, Drive: testDrive, Lane: "spa-edge-role", Kind: "claim", Text: "live on Sprite pool-7yt, owns #50212 and #50150", PRs: []string{"Forge-AI/monorepo#50212"}, At: ago(140), To: []string{root}},
	}
	sources := src(lanes, records, nil, nil)
	sources.PRRows = func() ([]store.ReviewRow, error) {
		return []store.ReviewRow{
			{Number: 50212, State: "open", Title: "the edge role"},
			{Number: 50150, Lane: "spa-edge-role", State: "open", Title: "the edge role's first half"},
			{Number: 48401, Lane: "ship-2", State: "open", Title: "the readers"},
			{Number: 7, Lane: "stale-1", State: "open", Title: "the stale one"},
			{Number: 6, Lane: "ship-2", State: "landed", Title: "landed already"},
		}, nil
	}
	got, err := ReadOwners(context.Background(), sources, now)
	if err != nil {
		t.Fatal(err)
	}
	want := map[string][]string{"50212": {"spa-edge-role"}, "50150": {"spa-edge-role"}, "48401": {"ship"}, "7": {}}
	if fmt.Sprint(got.PRs) != fmt.Sprint(want) {
		t.Fatalf("owners = %v, want %v: each owner by alias: the Sprite's alias row and its numbered lane's head own theirs once, the lane quiet for 90 minutes owns its PR, a lane quiet for five hours owns nothing, and a head that mentions a PR owns only its own", got.PRs, want)
	}
	if !slices.Equal(got.Live, []string{"dd-331634912-fix", "ship", "ship-2", "spa-edge-role", "spa-edge-role-1"}) {
		t.Fatalf("live = %v, want each lane heard within two hours and the alias its Sprite lane answers for", got.Live)
	}
}

func TestOwnersReadEachSourceOnceAtOnePRAsAtMany(t *testing.T) {
	reads := func(n int) (map[string]int, Owners) {
		counted := map[string]int{}
		lanes := make([]Lane, 0, n)
		rows := make([]store.ReviewRow, 0, n)
		for i := range n {
			lane := heard(fmt.Sprintf("ship-%d", i+1), "desired", 5)
			lanes = append(lanes, lane)
			rows = append(rows, store.ReviewRow{Number: 50000 + i, Lane: lane.Name, State: "open"})
		}
		sources := src(lanes, nil, nil, nil)
		lanesOf, recordsOf := sources.Lanes, sources.Records
		sources.Lanes = func(ctx context.Context) ([]Lane, error) { counted["lanes"]++; return lanesOf(ctx) }
		sources.Records = func(ctx context.Context, since time.Time, kinds ...string) ([]Record, error) {
			counted["records"]++
			return recordsOf(ctx, since, kinds...)
		}
		sources.PRRows = func() ([]store.ReviewRow, error) { counted["rows"]++; return rows, nil }
		found, err := ReadOwners(context.Background(), sources, now)
		if err != nil {
			t.Fatal(err)
		}
		return counted, found
	}
	many := scaletest.N()
	one, _ := reads(1)
	at, found := reads(many)
	if fmt.Sprint(one) != fmt.Sprint(at) || fmt.Sprint(one) != "map[lanes:1 records:1 rows:1]" {
		t.Fatalf("source reads at 1 PR = %v, at %d = %v; want one read of each at both", one, many, at)
	}
	if len(found.PRs) != many || len(found.Live) != many+1 {
		t.Fatalf("at %d: %d PRs and %d live names, want every lane and their one alias", many, len(found.PRs), len(found.Live))
	}
}
