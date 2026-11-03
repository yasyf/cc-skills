package lanes

import (
	"context"
	"errors"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

type handoffRun struct {
	commands []shell.Command
	refuse   string
}

func (h *handoffRun) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	h.commands = append(h.commands, c)
	if h.refuse != "" && c.Argv[0] == h.refuse {
		return shell.Result{}, errors.New(h.refuse + " refused")
	}
	switch c.Argv[0] {
	case "ccn":
		return shell.Result{Stdout: `{"id":"abc1234"}`}, nil
	case "git":
		return shell.Result{Stdout: "## quiet-lanes-3\n M fold.go\n"}, nil
	}
	return shell.Result{}, nil
}

func handoffEnv(bus *fakeBus, run *handoffRun) Env {
	return Env{Bus: bus, Runner: run, Config: config.Config{Drive: testDrive, Checkout: testCheckout}, Now: func() time.Time { return t0 }}
}

func parkedLane(t *testing.T, s *store.Store) {
	t.Helper()
	if _, err := s.WantLane(store.Lane{Name: lane2, Brief: "/briefs/conflicts-2.md", Model: "opus", Effort: "xhigh", Role: "lane", State: store.LaneDesired, PRs: []int{46475, 47001}}, t0); err != nil {
		t.Fatal(err)
	}
}

func TestACompactionHandoffCarriesEveryPROpenAskAndRecordAndSetsTheRow(t *testing.T) {
	s := memory(t)
	parkedLane(t, s)
	bus := &fakeBus{
		tail: []client.Record{{Seq: 7, Lane: lane2, Kind: "report", At: t0, Text: "pushed the branch"}},
		open: []client.Record{{Seq: 9, Lane: "meta-desk-1", Kind: "ask", To: []string{lane2}, Text: "which head?"}, {Seq: 10, Lane: "meta-desk-1", Kind: "ask", To: []string{"other"}, Text: "not yours"}},
	}
	run := &handoffRun{}

	id, err := handoffEnv(bus, run).WriteHandoff(context.Background(), s, lane2, "/work/lane", "/projects/x.jsonl")

	if err != nil || id != "abc1234" {
		t.Fatal(id, err)
	}
	var doc, post shell.Command
	for _, c := range run.commands {
		switch c.Argv[0] {
		case "ccn":
			doc = c
		case "cci":
			post = c
		}
	}
	for _, want := range []string{"/briefs/conflicts-2.md", "#46475 #47001", "/projects/x.jsonl", "#9 ASK from meta-desk-1: which head?", "#7 REPORT", "pushed the branch", "## Worktree /work/lane", "M fold.go"} {
		contains(t, doc.Stdin, want)
	}
	if strings.Contains(doc.Stdin, "not yours") {
		t.Fatal(doc.Stdin)
	}
	equal(t, doc.Argv[:3], []string{"ccn", "-R", "/work/lane"})
	if !slices.Contains(post.Argv, "--ccn") || !slices.Contains(post.Argv, "abc1234") || !slices.Contains(post.Argv, "handoff") {
		t.Fatal(post.Argv)
	}
	equal(t, len(bus.posted), 0)

	f := newFake(t, []Record{rec(1, lane2, "handoff", t0, "conflicts-2 wrote its handoff before compacting: doc abc1234").withRefs(Refs{CCN: "abc1234"})}, nil)
	tick(t, s, f, t0)
	lane := row(t, s, lane2)
	equal(t, []any{lane.Handoff, lane.State}, []any{"abc1234", store.LaneDesired})
}

func TestACompactionWhoseHandoffCannotBeWrittenLeavesABlocker(t *testing.T) {
	for _, tool := range []string{"ccn", "cci", "git"} {
		t.Run(tool, func(t *testing.T) {
			s := memory(t)
			parkedLane(t, s)
			bus := &fakeBus{}

			_, err := handoffEnv(bus, &handoffRun{refuse: tool}).WriteHandoff(context.Background(), s, lane2, "/work/lane", "")

			if err == nil {
				t.Fatal("no error")
			}
			equal(t, len(bus.posted), 1)
			equal(t, bus.posted[0].Kind, "blocker")
			equal(t, bus.posted[0].Lane, lane2)
			contains(t, bus.posted[0].Text, "compacted with no handoff")
		})
	}
}
