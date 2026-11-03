package landing_test

import (
	"context"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/landing"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func TestOnlyABlockerOnAHeadThePRHasLeftIsResolved(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	ctx := context.Background()
	s, err := store.Memory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	old, current := strings.Repeat("a", 40), strings.Repeat("b", 40)
	for number, pr := range map[int]store.PR{
		1: {Head: current, State: machine.StateRed},
		2: {Head: current, State: machine.StateRed},
		3: {Head: old, State: machine.StateLanded},
		4: {Head: current, State: machine.StateLanded},
	} {
		pr.Repo, pr.Number, pr.Base, pr.ReadAt = monorepo, number, "dev", time.Now()
		if err := s.PutPR(pr); err != nil {
			t.Fatal(err)
		}
	}
	blockers := []struct{ lane, id string }{
		{"supervisor", "build-failed:1:" + old},
		{"supervisor", "build-failed:2:" + current},
		{"supervisor", "land:3:" + old},
		{"supervisor", "release-held:4:release"},
		{"some-lane", "land:1:" + old},
	}
	for _, blocker := range blockers {
		if _, err := client.Post(ctx, client.Draft{Drive: "d", Lane: blocker.lane, Kind: "blocker", To: []string{"root"}, ID: blocker.id, Text: blocker.id}); err != nil {
			t.Fatal(err)
		}
	}
	outlived := landing.Outlived{Store: s, Drive: "d", Repo: monorepo}

	for range 2 {
		if err := outlived.Pass(ctx); err != nil {
			t.Fatal(err)
		}
	}

	open, err := client.Open(ctx, "d", "blocker")
	if err != nil {
		t.Fatal(err)
	}
	var left []string
	for _, record := range open {
		left = append(left, record.ID)
	}
	slices.Sort(left)
	want := []string{"build-failed:2:" + current, "land:1:" + old, "release-held:4:release"}
	if !slices.Equal(left, want) {
		t.Fatalf("left open %q, want %q", left, want)
	}
	notes, err := client.Tail(ctx, client.Query{Drive: "d", Kinds: []string{"note"}})
	if err != nil {
		t.Fatal(err)
	}
	if len(notes) != 2 || notes[0].To[0] != "root" || !strings.Contains(notes[0].Text+notes[1].Text, "#1 has since landed, closed or moved past aaaaaaaaaa") {
		t.Fatalf("%+v", notes)
	}
}
