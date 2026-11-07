package board

import (
	"context"
	"slices"
	"strings"
	"testing"

	"github.com/yasyf/cc-inbox/client"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

type correctedFixture struct {
	*cardsFixture
	bus   []client.Record
	posts []client.Draft
}

func correctedBoard(t *testing.T, onBoard ...string) *correctedFixture {
	t.Helper()
	s, err := store.Memory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	f := &correctedFixture{cardsFixture: newCards(t, &cardShell{cards: onBoard}, nil)}
	f.Store = s
	f.Tail = func(_ context.Context, q client.Query) ([]client.Record, error) {
		return slices.DeleteFunc(slices.Clone(f.bus), func(r client.Record) bool { return r.Seq <= q.After || !slices.Contains(q.Kinds, string(r.Kind)) }), nil
	}
	f.Among = func(_ context.Context, _ string, seqs []int64) ([]client.Record, error) {
		return slices.DeleteFunc(slices.Clone(f.bus), func(r client.Record) bool { return !slices.Contains(seqs, r.Seq) }), nil
	}
	f.Post = func(_ context.Context, d client.Draft) (client.Record, error) {
		f.posts = append(f.posts, d)
		return client.Record{Seq: int64(len(f.posts))}, nil
	}
	return f
}

func TestACorrectionWhoseThreadReachesTheAskACardWasDrawnFromFlagsItStaleToTheCommunicatorOnce(t *testing.T) {
	ctx := context.Background()
	f := correctedBoard(t, "card-rmw3iiw53", "card-other")
	for card, ask := range map[string]int64{"card-rmw3iiw53": 471222, "card-other": 470500, "card-gone": 471222} {
		if err := f.RecordAsk(ctx, card, ask); err != nil {
			t.Fatal(err)
		}
	}
	f.bus = []client.Record{
		{Seq: 469930, Lane: "monitor-flap-fix-1", Kind: "report", Text: "#50930 is up"},
		{Seq: 471222, Lane: "monitor-flap-fix-1", Kind: "ask", Re: 469930, Text: "For his card, #50930: a person is paged only at 3 callers or 50 400s in thirty minutes."},
		{Seq: 471421, Lane: "owner-relay", Kind: "answer", Resolves: 471222, Text: "His card for the #50930 risk is up as card-rmw3iiw53."},
		{Seq: 472840, Lane: "finder-desk", Kind: "review", Text: "the provider-400 monitor never pages @oncall-platform"},
		{Seq: 472983, Lane: "monitor-flap-fix-1", Kind: "report", Re: 472840, Text: "It now pages @oncall-platform."},
		{Seq: 474081, Lane: "fidelity-pipeline", Kind: "review", Text: "a monitor pages only on a row he approves"},
		{Seq: 474186, Lane: "monitor-flap-fix-1", Kind: "correction", Re: 471421, Text: "owner-relay: please correct card-rmw3iiw53: the monitor posts to #alerts-api and pages nobody."},
		{Seq: 474187, Lane: "monitor-flap-fix-1", Kind: "correction", Re: 474081, Text: "the monitor posts to #alerts-api and pages nobody."},
		{Seq: 474188, Lane: "monitor-flap-fix-1", Kind: "correction", Re: 472983, Text: "the monitor posts to #alerts-api and pages nobody."},
	}

	for range 2 {
		if err := f.Corrected(ctx, "supervisor"); err != nil {
			t.Fatal(err)
		}
	}

	if len(f.posts) != 1 {
		t.Fatalf("posts = %+v", f.posts)
	}
	flagged := f.posts[0]
	if flagged.Kind != askKind || !slices.Equal(flagged.To, []string{communicator}) || flagged.Re != 474186 || flagged.ID != "stale-card:card-rmw3iiw53:474186" || flagged.Topic != "stale-card:card-rmw3iiw53" {
		t.Fatalf("flag = %+v", flagged)
	}
	if !strings.HasPrefix(flagged.Text, "STALE card-rmw3iiw53 on board s1:") || !strings.Contains(flagged.Text, "#471222") {
		t.Fatalf("flag text = %q", flagged.Text)
	}

	f.bus = append(f.bus,
		client.Record{Seq: 474300, Lane: "monitor-flap-fix-1", Kind: "head", Re: 471222, Text: "HEAD #50930 32023834eca5"},
		client.Record{Seq: 474301, Lane: "monitor-flap-fix-1", Kind: "head", Re: 474301, Text: "HEAD #50931 answering itself"},
	)
	if err := f.Corrected(ctx, "supervisor"); err != nil {
		t.Fatal(err)
	}
	if len(f.posts) != 2 || f.posts[1].ID != "stale-card:card-rmw3iiw53:474300" {
		t.Fatalf("posts after a new head = %+v", f.posts)
	}
}

func TestAnAskStillReadsAsDrawnOnceItsCardIsWithdrawn(t *testing.T) {
	f := newCards(t, &cardShell{}, nil)
	ctx := context.Background()
	if err := f.RecordAsk(ctx, "card-a", 559601); err != nil {
		t.Fatal(err)
	}
	if err := f.record(ctx, "card-a", map[string]string{"withdrawn": stamp(cardsNow)}); err != nil {
		t.Fatal(err)
	}
	drawn, err := f.DrawnAsks(ctx)
	if err != nil || len(drawn) != 1 || !drawn[559601] {
		t.Fatalf("drawn = %v, %v, want ask 559601 drawn", drawn, err)
	}
}

func TestAHeadFlagsACardStaleOnlyWhenItAnswersTheCardsSourceOrCarriesItsTopic(t *testing.T) {
	ctx := context.Background()
	f := correctedBoard(t, "card-rmw5yuqv3")
	if err := f.RecordAsk(ctx, "card-rmw5yuqv3", 576900); err != nil {
		t.Fatal(err)
	}
	f.bus = []client.Record{
		{Seq: 576900, Lane: "owner-relay", Kind: "ask", Topic: "slack-grant", Text: "Grant the Slack label?"},
		{Seq: 577100, Lane: "owner-relay", Kind: "answer", Re: 576900, Text: "Root has had no coordinator since 8:38 PM."},
		{Seq: 577274, Lane: "meta-desk", Kind: "head", Re: 577100, Text: "HEAD #3775 relaunches root"},
		{Seq: 577430, Lane: "meta-desk", Kind: "head", Re: 577274, Text: "HEAD #3776 escalates orphan orders"},
		{Seq: 577500, Lane: "meta-desk", Kind: "head", Re: 576900, Text: "HEAD #3777 grants the label"},
		{Seq: 577501, Lane: "meta-desk", Kind: "head", Topic: "slack-grant", Text: "HEAD #3778 renders the grant"},
	}

	if err := f.Corrected(ctx, "supervisor"); err != nil {
		t.Fatal(err)
	}

	flagged := make([]string, 0, len(f.posts))
	for _, post := range f.posts {
		flagged = append(flagged, post.ID)
	}
	if want := []string{"stale-card:card-rmw5yuqv3:577500", "stale-card:card-rmw5yuqv3:577501"}; !slices.Equal(flagged, want) {
		t.Fatalf("flagged = %v, want %v", flagged, want)
	}
}
