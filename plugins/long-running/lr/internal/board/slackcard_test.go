package board

import (
	"context"
	"encoding/json"
	"fmt"
	"maps"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/pass"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func TestAnOwnerMergeRowIsTheFullCardWithApproveAndHold(t *testing.T) {
	card, ok := Card(repo, rows(t, withWords(t), facts(nil)), number)
	if !ok {
		t.Fatal("an owner merge row drew no card")
	}
	want := SlackCard{
		Status: "needs_you", Title: "Approve #45583 at e718a74?",
		PRs: []SlackPRRow{{
			URL: "https://github.com/org/repo/pull/45583", Head: head, Review: "every other gate is met",
			Change: "chats load faster", Plan: "nothing differed", Risk: "one cache key", Blocks: "your approval",
		}},
		Options: []string{"Approve", "Hold"}, Recommended: "Approve", Waits: MergeWaits(number, head),
	}
	if !reflect.DeepEqual(card, want) {
		t.Fatalf("card = %+v, want %+v", card, want)
	}
}

func TestAPRWithNoOwnerRowDrawsNoCard(t *testing.T) {
	for name, found := range map[string][]Row{
		"another PR":       rows(t, withWords(t), facts(nil)),
		"already tapped":   rows(t, withWords(t), facts(func(f *machine.Facts) { f.Tap = machine.TapTapped })),
		"at an older head": rows(t, withWords(t), facts(func(f *machine.Facts) { f.Head = moved })),
	} {
		t.Run(name, func(t *testing.T) {
			pr := number
			if name == "another PR" {
				pr = number + 1
			}
			if card, ok := Card(repo, found, pr); ok {
				t.Fatalf("drew %+v", card)
			}
		})
	}
}

func TestMergeOnTheCardTapsTheRowAndHoldHoldsIt(t *testing.T) {
	s, hooks := withItem(t), &recorded{}
	if err := Respond(s, repo, hooks.hooks(), rows(t, s, facts(nil)), number, MergeWaits(number, head), "Hold", t0); err != nil {
		t.Fatal(err)
	}
	if want := []Key{{number, head}}; !reflect.DeepEqual(hooks.holds, want) || len(hooks.taps) != 0 {
		t.Fatalf("holds = %+v, taps = %+v", hooks.holds, hooks.taps)
	}
	if err := Respond(s, repo, hooks.hooks(), rows(t, s, facts(nil)), number, MergeWaits(number, head), "Merge", t0); err != nil {
		t.Fatal(err)
	}
	if want := []TapRecord{{number, head, "owner", machine.TapTapped}}; !reflect.DeepEqual(hooks.taps, want) {
		t.Fatalf("taps = %+v", hooks.taps)
	}
}

func TestACardAnswerForAMovedHeadOrAnotherLabelIsRefused(t *testing.T) {
	s, hooks := withItem(t), &recorded{}
	found := rows(t, s, facts(nil))
	for label, said := range map[string]string{"Approve": "moved to e718a7434fa0", "Merge": "moved to e718a7434fa0", "Ship it": `not "Ship it"`} {
		err := Respond(s, repo, hooks.hooks(), found, number, MergeWaits(number, moved), label, t0)
		if err == nil || !strings.Contains(err.Error(), said) {
			t.Fatalf("%s: err = %v, want it to say %q", label, err, said)
		}
	}
	if len(hooks.taps)+len(hooks.holds) != 0 {
		t.Fatalf("taps = %+v, holds = %+v", hooks.taps, hooks.holds)
	}
}

func TestABareNumberNamesEveryDrivePRHoldingItAndNoOther(t *testing.T) {
	s := withItem(t)
	for _, pr := range []store.PR{{Repo: "yasyf/cc-skills", Number: 2475}, {Repo: "yasyf/cc-slack", Number: 66}, {Repo: "Forge-AI/monorepo", Number: 66}, {Repo: "Forge-AI/monorepo", Number: 49032}} {
		if err := s.PutPR(store.PR{Repo: pr.Repo, Number: pr.Number, Head: head, Base: "main", Lane: "lane-7", State: machine.StateGrading, ReadAt: t0}); err != nil {
			t.Fatal(err)
		}
	}
	held, err := Holders(s, []int{2475, 66, 7}, []SlackPR{{"yasyf/cc-skills", 2475}})
	if err != nil {
		t.Fatal(err)
	}
	want := []SlackPR{{"yasyf/cc-skills", 2475}, {"Forge-AI/monorepo", 66}, {"yasyf/cc-slack", 66}}
	if !reflect.DeepEqual(held, want) {
		t.Fatalf("held = %+v, want %+v", held, want)
	}
}

func TestACardReadsTheNamedDriveAndSkipsOneThatEnded(t *testing.T) {
	home := t.TempDir()
	t.Setenv("CLAUDE_CONFIG_DIR", home)
	registry := filepath.Join(home, "long-running", "drives")
	running := filepath.Join(home, "running")
	for _, dir := range []string{registry, running} {
		if err := os.MkdirAll(dir, 0o700); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.WriteFile(filepath.Join(running, configFile), []byte("{}"), 0o600); err != nil {
		t.Fatal(err)
	}
	for name, state := range map[string]string{"ended": filepath.Join(home, "ended"), "running": running} {
		entry := fmt.Sprintf(`{"drive":%q,"state_dir":%q,"sessions":["s"]}`, name, state)
		if err := os.WriteFile(filepath.Join(registry, name+".json"), []byte(entry), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	for name, want := range map[string]string{"": "running", "ended": "ended"} {
		found, err := cardDrives(name)
		if err != nil {
			t.Fatal(err)
		}
		if len(found) != 1 || found[0].Drive != want {
			t.Fatalf("cardDrives(%q) = %+v, want only %s", name, found, want)
		}
	}
}

type countedShell struct{ calls int }

func (c *countedShell) Run(context.Context, shell.Command) (shell.Result, error) {
	c.calls++
	return shell.Result{}, fmt.Errorf("the card ran a command")
}

func passed(t *testing.T, s *store.Store, snapshot map[int]machine.Facts) {
	t.Helper()
	found := slices.Collect(maps.Values(snapshot))
	if _, err := pass.Run(s, found, t0, func([]string, bus.Body) error { return nil }, nil, func(string) string { return "" }, func(string) (string, error) { return "", nil }); err != nil {
		t.Fatal(err)
	}
}

func TestACardFromTheStoreIsTheCardTheLiveReadDraws(t *testing.T) {
	for name, change := range map[string]func(*machine.Facts){
		"untapped":           nil,
		"tap given":          func(f *machine.Facts) { f.Tap, f.Condition = machine.TapConditional, "only if covered" },
		"unsafe on dev":      func(f *machine.Facts) { f.UnsafeWait = "do not merge until the cutover" },
		"lane the pass read": func(f *machine.Facts) { f.Lane = "lane-9" },
	} {
		t.Run(name, func(t *testing.T) {
			s, snapshot := withWords(t), facts(change)
			passed(t, s, snapshot)
			want, ok := Card(repo, rows(t, s, snapshot), number)
			if !ok {
				t.Fatal("the live read drew no card")
			}
			stored, err := StoredRows(s, repo, []int{number})
			if err != nil {
				t.Fatal(err)
			}
			if got, ok := Card(repo, stored, number); !ok || !reflect.DeepEqual(got, want) {
				t.Fatalf("stored card = %+v, want %+v", got, want)
			}
		})
	}
}

func TestATapOnTheCardTakesItOffBeforeTheNextPass(t *testing.T) {
	s, hooks := withWords(t), &recorded{}
	passed(t, s, facts(nil))
	found, err := StoredRows(s, repo, []int{number})
	if err != nil {
		t.Fatal(err)
	}
	if err := Respond(s, repo, hooks.hooks(), found, number, MergeWaits(number, head), "Merge", t0); err != nil {
		t.Fatal(err)
	}
	if again, err := StoredRows(s, repo, []int{number}); err != nil || len(again) != 0 {
		t.Fatalf("rows after his tap = %+v, %v", again, err)
	}
}

func TestTheCardReadsNothingButTheStoreAtOneAndAThousandPRs(t *testing.T) {
	for _, n := range []int{1, 1000} {
		t.Run(strconv.Itoa(n), func(t *testing.T) {
			home := t.TempDir()
			t.Setenv("CLAUDE_CONFIG_DIR", home)
			registry, state := filepath.Join(home, "long-running", "drives"), filepath.Join(home, "running")
			for _, dir := range []string{registry, state} {
				if err := os.MkdirAll(dir, 0o700); err != nil {
					t.Fatal(err)
				}
			}
			cfg := fmt.Sprintf(`{"repo":%q,"drive":"running","checkout":"/c","author":"a","label":"l","movers":["lanes"]}`, repo)
			entry := fmt.Sprintf(`{"drive":"running","repo":%q,"state_dir":%q,"sessions":["s"]}`, repo, state)
			for path, body := range map[string]string{filepath.Join(state, configFile): cfg, filepath.Join(registry, "running.json"): entry} {
				if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
					t.Fatal(err)
				}
			}
			s, err := store.Open(filepath.Join(state, "lr.sqlite"))
			if err != nil {
				t.Fatal(err)
			}
			snapshot := map[int]machine.Facts{}
			asked := make([]SlackPR, n)
			for i := range n {
				f := facts(nil)[number]
				f.Number = number + i
				snapshot[f.Number], asked[i] = f, SlackPR{repo, f.Number}
			}
			passed(t, s, snapshot)
			if err := s.Close(); err != nil {
				t.Fatal(err)
			}
			counted := &countedShell{}
			ask, err := json.Marshal(map[string]any{"prs": asked})
			if err != nil {
				t.Fatal(err)
			}
			var out strings.Builder
			cmd := Command(&app.App{Runner: counted, CCX: ccx.CLI{Runner: counted}, Now: func() time.Time { return t0 }})
			cmd.SetArgs([]string{"card", "--drive", "running", "--lane", "board-watch"})
			cmd.SetIn(strings.NewReader(string(ask)))
			cmd.SetOut(&out)
			if err := cmd.Execute(); err != nil {
				t.Fatal(err)
			}
			var answer struct {
				Cards []SlackPRCard `json:"cards"`
			}
			if err := json.Unmarshal([]byte(out.String()), &answer); err != nil {
				t.Fatal(err)
			}
			if len(answer.Cards) != n || counted.calls != 0 {
				t.Fatalf("%d cards for %d PRs with %d commands run, want %d and none", len(answer.Cards), n, counted.calls, n)
			}
		})
	}
}

func TestASlackMergeCardTapPostsTheLandOrderTheBoardRecorderPosts(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	ctx := context.Background()

	if err := postHooks(ctx, "d", "pr-cards", repo).Tap(TapRecord{number, head, byOwner, machine.TapTapped}); err != nil {
		t.Fatal(err)
	}

	written, err := client.Tail(ctx, client.Query{Drive: "d", Kinds: []string{"lr.tap"}})
	if err != nil {
		t.Fatal(err)
	}
	if len(written) != 1 || !strings.HasPrefix(written[0].Text, fmt.Sprintf("tap #%d ", number)) {
		t.Fatalf("orders = %+v", written)
	}
}
