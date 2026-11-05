package live

import (
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/asks"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const repo = "Forge-AI/monorepo"

var now = time.Date(2026, 11, 4, 23, 0, 0, 0, time.UTC)

type counts struct{ rows, book, left int }

func sources(rows []store.ReviewRow, cards []Card, n *counts) Sources {
	return Sources{
		Repo: repo, Cards: cards, Now: func() time.Time { return now },
		Rows: func(string) ([]store.ReviewRow, error) { n.rows++; return rows, nil },
		Book: func() (asks.Book, error) {
			n.book++
			return asks.Book{Asks: []asks.Ask{{Key: "ask/000036", Lane: "slack-bugs-1", Text: "follow up", AskedAt: now, Answered: true}}}, nil
		},
		LeftBoard: func(ids []string) (map[string]Card, error) {
			n.left++
			return map[string]Card{"card-gone": {ID: "card-gone", Title: "Followed chats?", Picked: "Read in place"}}, nil
		},
	}
}

func row(number int, ready, tapped bool, state string) store.ReviewRow {
	return store.ReviewRow{Repo: repo, Number: number, Title: "t" + strconv.Itoa(number), ReadyForTap: ready, Tapped: tapped, State: state, WaitsOn: "its parent", Hold: prrow.None}
}

func read(t *testing.T, src Sources, bindings ...Binding) []Result {
	t.Helper()
	results, err := Read(src, bindings)
	if err != nil {
		t.Fatal(err)
	}
	return results
}

func TestATappedCardNoLongerAsks(t *testing.T) {
	cards := []Card{{ID: "card-open", Title: "Which?", Options: []string{"A", "B"}}, {ID: "card-done", Title: "Which?", Picked: "B"}}
	got := read(t, sources(nil, cards, &counts{}), Binding{View: ViewGroup, Refs: []Ref{"card:card-done", "card:card-open"}})[0].Items
	if got[0].Ref != "card:card-open" || len(got[0].Actions) != 2 || got[0].Says != "Your answer" {
		t.Fatalf("an open card leads with its buttons: %+v", got[0])
	}
	if got[1].State != StateSettled || got[1].Says != "You picked B" || got[1].Actions != nil {
		t.Fatalf("an answered card reads its answer and offers nothing: %+v", got[1])
	}
}

func TestACardThatLeftTheBoardReadsItsAnswer(t *testing.T) {
	got := read(t, sources(nil, nil, &counts{}), Binding{View: ViewTable, Refs: []Ref{"card:card-gone", "card:card-never"}})[0].Items
	if got[0].Says != "You picked Read in place" {
		t.Fatalf("got %+v", got[0])
	}
	if got[1].State != StateGone || got[1].Actions != nil {
		t.Fatalf("a key resolving to nothing asks nothing: %+v", got[1])
	}
}

func TestARowSaysWhatItWaitsOnFromItsState(t *testing.T) {
	rows := []store.ReviewRow{row(1, true, false, "open"), row(2, true, true, "tapped"), row(3, false, false, "open"), row(4, false, false, "landed")}
	got := read(t, sources(rows, nil, &counts{}), Binding{View: ViewTable, Refs: []Ref{"pr:4", "pr:3", "pr:2", "pr:1"}})[0].Items
	want := []struct {
		ref, state, says string
		buttons          int
	}{{"pr:1", StateWaits, "Your approval", 2}, {"pr:3", StateBlocked, "Its parent.", 2}, {"pr:4", StateSettled, "Landed", 0}, {"pr:2", StateSettled, "You approved it", 0}}
	for i, w := range want {
		if string(got[i].Ref) != w.ref || got[i].State != w.state || got[i].Says != w.says || len(got[i].Actions) != w.buttons {
			t.Fatalf("row %d: got %+v, want %+v", i, got[i], w)
		}
	}
}

func TestAHeldRowSaysWhatHoldsItAndOffersNoApprove(t *testing.T) {
	held := row(5, false, false, "open")
	held.Hold, held.WaitsOn = "root:until #49305 lands", "held, root:until #49305 lands"
	checks := row(6, false, false, "open")
	checks.Hold, checks.WaitsOn = prrow.None, prrow.NotGraded
	got := read(t, sources([]store.ReviewRow{held, checks}, nil, &counts{}), Binding{View: ViewEmbed, Refs: []Ref{"pr:5", "pr:6"}})[0].Items
	if got[0].Actions != nil || !strings.HasPrefix(got[0].Says, "Held: ") {
		t.Fatalf("a tap under a hold never lands, so a held row asks for none: %+v", got[0])
	}
	if len(got[1].Actions) != 2 {
		t.Fatalf("a row waiting on checks keeps its Approve: %+v", got[1])
	}
}

func TestAQueryKeepsOnlyOpenRowsThatMatch(t *testing.T) {
	rows := []store.ReviewRow{row(1, true, false, "open"), row(2, true, true, "tapped"), row(3, false, false, "open"), row(4, true, false, "landed")}
	src := sources(rows, []Card{{ID: "card-open", Options: []string{"A"}}, {ID: "card-done", Picked: "A"}}, &counts{})
	got := read(t, src, Binding{View: ViewCount, Source: SourcePRs, Where: map[string]string{"waits": "you"}}, Binding{View: ViewGroup, Source: SourceWaits})
	if got[0].Total != 1 || got[0].Items[0].Ref != Ref("pr:"+repo+"#1") {
		t.Fatalf("prs waits=you: %+v", got[0])
	}
	if got[1].Total != 2 || got[1].Items[1].Ref != "card:card-open" {
		t.Fatalf("waits is his PRs and his open cards: %+v", got[1])
	}
}

func TestALimitKeepsTheTotal(t *testing.T) {
	rows := []store.ReviewRow{row(1, true, false, "open"), row(2, true, false, "open"), row(3, true, false, "open")}
	got := read(t, sources(rows, nil, &counts{}), Binding{View: ViewTable, Source: SourcePRs, Limit: 2})[0]
	if got.Total != 3 || len(got.Items) != 2 {
		t.Fatalf("got %+v", got)
	}
}

func TestOneReadPerSourceAtAnyN(t *testing.T) {
	for _, n := range []int{1, 1000} {
		var rows []store.ReviewRow
		var bindings []Binding
		for i := range n {
			rows = append(rows, row(i, true, false, "open"))
			bindings = append(bindings,
				Binding{View: ViewEmbed, Refs: []Ref{Ref("pr:" + strconv.Itoa(i)), Ref("card:card-gone-" + strconv.Itoa(i)), "ask:ask/000036"}},
				Binding{View: ViewCount, Source: SourcePRs},
			)
		}
		var seen counts
		read(t, sources(rows, nil, &seen), bindings...)
		if seen != (counts{rows: 1, book: 1, left: 1}) {
			t.Fatalf("at N=%d the sources were read %+v times", n, seen)
		}
	}
}

func TestWhatNoDrawCanRepairIsRefused(t *testing.T) {
	for name, tc := range map[string]struct {
		binding Binding
		says    string
	}{
		"view":     {Binding{View: "carousel", Source: SourcePRs}, "no view"},
		"both":     {Binding{View: ViewTable, Source: SourcePRs, Refs: []Ref{"pr:1"}}, "not both"},
		"neither":  {Binding{View: ViewTable}, "not both"},
		"source":   {Binding{View: ViewTable, Source: "bugs"}, "no source"},
		"where":    {Binding{View: ViewTable, Source: SourcePRs, Where: map[string]string{"topic": "x"}}, "filters by"},
		"column":   {Binding{View: ViewTable, Source: SourcePRs, Columns: []string{"nope"}}, "no column"},
		"link":     {Binding{View: ViewEmbed, Refs: []Ref{"record:5"}}, "link target"},
		"not a pr": {Binding{View: ViewEmbed, Refs: []Ref{"pr:abc"}}, "names no pull request"},
	} {
		if err := Check(tc.binding); err == nil || !strings.Contains(err.Error(), tc.says) {
			t.Errorf("%s: got %v", name, err)
		}
	}
}

func TestPickedReadsTheAnswerRow(t *testing.T) {
	if got := picked("picked “Read followed chats in place”\nBoard question: x"); got != "Read followed chats in place" {
		t.Fatal(got)
	}
}
