package lanes

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/askdesk"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

type handoffRun struct {
	commands   []shell.Command
	refuse     string
	failDocAdd int
}

func (h *handoffRun) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	h.commands = append(h.commands, c)
	if h.refuse != "" && c.Argv[0] == h.refuse {
		return shell.Result{}, errors.New(h.refuse + " refused")
	}
	if h.failDocAdd > 0 && c.Argv[0] == "ccn" {
		h.failDocAdd--
		return shell.Result{}, errors.New("update refs failed")
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

	env := handoffEnv(bus, run)
	id, err := env.WriteHandoff(context.Background(), io.Discard, env.storeRegistry(s, lane2), lane2, "/work/lane", "/projects/x.jsonl")

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

func TestAHandoffNamesOtherLanesByAliasAndKeepsItsOwnBinding(t *testing.T) {
	s := memory(t)
	parkedLane(t, s)
	if _, err := s.WantLane(store.Lane{Name: "meta-desk-3", Brief: "/briefs/meta.md", Role: "lane", State: store.LaneDesired}, t0); err != nil {
		t.Fatal(err)
	}
	bus := &fakeBus{
		tail: []client.Record{{Seq: 7, Lane: lane2, Kind: "report", At: t0, Text: "asked meta-desk-3 about it"}},
		open: []client.Record{{Seq: 9, Lane: "meta-desk-3", Kind: "ask", To: []string{lane2}, Text: "does meta-desk-3 own it?"}},
	}

	run := &handoffRun{}
	env := handoffEnv(bus, run)
	if _, err := env.WriteHandoff(context.Background(), io.Discard, env.storeRegistry(s, lane2), lane2, "/work/lane", "/projects/x.jsonl"); err != nil {
		t.Fatal(err)
	}

	var doc shell.Command
	for _, c := range run.commands {
		if c.Argv[0] == "ccn" {
			doc = c
		}
	}
	for _, want := range []string{"# conflicts-2 handoff", "#9 ASK from meta-desk: does meta-desk own it?", "asked meta-desk about it"} {
		contains(t, doc.Stdin, want)
	}
	if strings.Contains(doc.Stdin, "meta-desk-3") {
		t.Fatal(doc.Stdin)
	}
}

func TestACompactionWhoseHandoffCannotBeWrittenLeavesABlocker(t *testing.T) {
	for _, tool := range []string{"ccn", "cci", "git"} {
		t.Run(tool, func(t *testing.T) {
			s := memory(t)
			parkedLane(t, s)
			bus := &fakeBus{}

			env := handoffEnv(bus, &handoffRun{refuse: tool})
			var out bytes.Buffer
			_, err := env.WriteHandoff(context.Background(), &out, env.storeRegistry(s, lane2), lane2, "/work/lane", "")

			if err == nil {
				t.Fatal("no error")
			}
			equal(t, len(bus.posted), 1)
			equal(t, bus.posted[0].Kind, "blocker")
			equal(t, bus.posted[0].Lane, lane2)
			contains(t, bus.posted[0].Text, "compacted with no handoff")
			equal(t, out.String(), "blocker #1\n")
		})
	}
}

type registryDesk struct {
	asked    client.Draft
	registry string
}

func (d *registryDesk) Post(_ context.Context, draft client.Draft) (client.Record, error) {
	d.asked = draft
	return client.Record{Seq: 41}, nil
}

func (d *registryDesk) Follow(_ context.Context, _ client.Watch, emit func(client.Record) error) error {
	answer := client.Record{Seq: 42, Resolves: 41, Text: "lr lanes handoff-registry: 1 lines, exit 0"}
	answer.Refs.Path = d.registry
	return emit(answer)
}

func TestASpriteHandoffReadsItsRowFromTheStoreDeskAndStillWritesTheDoc(t *testing.T) {
	answer, err := json.Marshal(handoffRegistry{Brief: "/briefs/conflicts-2.md", PRs: []int{46475, 47001}, Aliases: Aliases{"meta-desk-3": "meta-desk"}, NoticeTo: Root})
	if err != nil {
		t.Fatal(err)
	}
	desk := &registryDesk{registry: filepath.Join(t.TempDir(), "registry.json")}
	if err := os.WriteFile(desk.registry, answer, 0o600); err != nil {
		t.Fatal(err)
	}
	bus := &fakeBus{open: []client.Record{{Seq: 9, Lane: "meta-desk-3", Kind: "ask", To: []string{lane2}, Text: "does meta-desk-3 own it?"}}}
	run := &handoffRun{}
	var out bytes.Buffer

	id, err := handoffEnv(bus, run).WriteHandoff(context.Background(), &out, func(ctx context.Context, named []string) (handoffRegistry, error) {
		return askRegistry(ctx, desk, testDrive, lane2, named, time.Minute)
	}, lane2, "/work/lane", "")

	if err != nil || id != "abc1234" || out.String() != "abc1234\n" {
		t.Fatalf("wrote %q, printed %q, %v", id, out.String(), err)
	}
	var asked askdesk.Asked
	if err := json.Unmarshal([]byte(desk.asked.Text), &asked); err != nil {
		t.Fatal(err)
	}
	equal(t, asked, askdesk.Asked{Verb: askdesk.HandoffRegistryVerb, Args: []string{"--", lane2, "meta-desk-3"}, Lane: lane2})
	equal(t, desk.asked.To, []string{askdesk.StoreDesk})
	var doc shell.Command
	for _, c := range run.commands {
		if c.Argv[0] == "ccn" {
			doc = c
		}
	}
	for _, want := range []string{"/briefs/conflicts-2.md", "#46475 #47001", "#9 ASK from meta-desk: does meta-desk own it?", "## Worktree /work/lane", "M fold.go"} {
		contains(t, doc.Stdin, want)
	}
	equal(t, len(bus.posted), 0)
}

type silentDesk struct{}

func (silentDesk) Post(context.Context, client.Draft) (client.Record, error) {
	return client.Record{Seq: 41}, nil
}

func (silentDesk) Follow(ctx context.Context, _ client.Watch, _ func(client.Record) error) error {
	<-ctx.Done()
	return nil
}

func TestASpriteHandoffWhoseStoreDeskNeverAnswersPostsItsBlockerByTheDeskWait(t *testing.T) {
	bus := &fakeBus{}
	run := &handoffRun{}
	var out bytes.Buffer

	_, err := handoffEnv(bus, run).WriteHandoff(context.Background(), &out, func(ctx context.Context, named []string) (handoffRegistry, error) {
		return askRegistry(ctx, silentDesk{}, testDrive, lane2, named, 10*time.Millisecond)
	}, lane2, "/work/lane", "")

	if err == nil || !strings.Contains(err.Error(), "no answer from the Mac's store-desk to conflicts-2's ask") || strings.Contains(err.Error(), "supervisor") {
		t.Fatalf("got %v, want the unanswered ask", err)
	}
	equal(t, len(bus.posted), 1)
	equal(t, bus.posted[0].To, []string{Root})
	contains(t, bus.posted[0].Text, "compacted with no handoff")
	equal(t, out.String(), "blocker #1\n")
	equal(t, len(run.commands), 0)
}

func TestAHandoffRegistryAnswerIsTheSameSizeAtOneLaneAndAHundred(t *testing.T) {
	sizes := map[int]int{}
	for _, n := range []int{1, 100} {
		s := memory(t)
		parkedLane(t, s)
		for i := range n {
			if _, err := s.WantLane(store.Lane{Name: fmt.Sprintf("other-%d", i), Brief: "/briefs/other.md", Role: "lane", State: store.LaneDesired}, t0); err != nil {
				t.Fatal(err)
			}
		}

		registry, err := handoffEnv(&fakeBus{}, &handoffRun{}).storeRegistry(s, lane2)(context.Background(), []string{"other-0", "pool-7"})
		if err != nil {
			t.Fatal(err)
		}
		answer, err := json.Marshal(registry)
		if err != nil {
			t.Fatal(err)
		}

		equal(t, registry.Aliases, Aliases{"other-0": "other"})
		sizes[n] = len(answer)
	}
	equal(t, sizes[100], sizes[1])
}

func TestAHandoffRetriesACcnDocAddThatFailsOnce(t *testing.T) {
	s := memory(t)
	parkedLane(t, s)
	bus := &fakeBus{}

	env := handoffEnv(bus, &handoffRun{failDocAdd: 1})
	id, err := env.WriteHandoff(context.Background(), io.Discard, env.storeRegistry(s, lane2), lane2, "/work/lane", "")
	if err != nil {
		t.Fatal(err)
	}
	if id == "" {
		t.Fatal("no doc id")
	}
	for _, posted := range bus.posted {
		if posted.Kind == "blocker" {
			t.Fatalf("posted a blocker: %s", posted.Text)
		}
	}
}

func TestADeskHandoffCarriesTheOpenOwnerOrdersAndAWorkerHandoffDoesNot(t *testing.T) {
	for _, role := range []string{"desk", "lane"} {
		t.Run(role, func(t *testing.T) {
			s := memory(t)
			if _, err := s.WantLane(store.Lane{Name: "meta-desk-3", Brief: "/briefs/meta-desk-3.md", Model: "opus", Effort: "xhigh", Role: role, State: store.LaneDesired}, t0); err != nil {
				t.Fatal(err)
			}
			run := &handoffRun{}
			env := handoffEnv(&fakeBus{}, run)
			env.State = ownerLedger(t, 1)

			if _, err := env.WriteHandoff(context.Background(), io.Discard, env.storeRegistry(s, "meta-desk-3"), "meta-desk-3", "", ""); err != nil {
				t.Fatal(err)
			}

			doc := run.commands[0]
			equal(t, doc.Argv[0], "ccn")
			equal(t, strings.Contains(doc.Stdin, "## Open owner orders\n\nNo live lane, open PR or landed record delivers these yet (`lr owner audit`). Carry each until one does.\n\n- m1 Nov 3 4:50 AM: build order 1\n"), role == "desk")
		})
	}
}

func TestAHandoffNamesItsLiveTasksAndSurvivesAMissingTranscript(t *testing.T) {
	now := time.Date(2026, 11, 3, 23, 0, 0, 0, time.UTC)
	line := `{"type":"assistant","session_id":"p","timestamp":"2026-11-03T22:59:00Z","message":{"content":[{"type":"tool_use","id":"m1","name":"Monitor","input":{"command":"tail -f ci.log","description":"ci log","timeout_ms":1800000}}]}}`
	path := filepath.Join(t.TempDir(), "session.jsonl")
	if err := os.WriteFile(path, []byte(line+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if got := liveTasksSection(path, now); !strings.Contains(got, "tail -f ci.log") {
		t.Fatalf("section = %q, want the Monitor's command", got)
	}
	if got := liveTasksSection(path+".gone", now); !strings.Contains(got, "Could not read them") {
		t.Fatalf("section = %q, want the handoff to say it could not read the transcript", got)
	}
}
