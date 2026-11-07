package reply

import (
	"bytes"
	"cmp"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"
	"time"
	"unicode/utf8"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/board"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/owner"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/talkboard"
)

const (
	permalink = "https://brook.slack.com/archives/D0B6Z/p1793635190051179"
	landPR    = "https://github.com/yasyf/cc-skills/pull/2196"
	posted    = `{"seq":12,"num":"#12@2479ec97"}`
)

var sentAt = time.Date(2026, 11, 3, 12, 0, 0, 0, time.UTC)

func scripted() *shelltest.Fake {
	return scriptedOver(&shelltest.Fake{})
}

func scriptedOver(fake *shelltest.Fake) *shelltest.Fake {
	fake.Reply("updated card\nsent: card ts=1793635190.051179 "+permalink+"\n", "cc-present", "update-block")
	fake.Reply(`{"channel_id":"D0B6Z","thread_ts":"1793635190.051179","ts":"1793635190.051179","permalink":"`+permalink+`"}`, "cc-slack", "dm-status")
	fake.Reply(posted, "cci", "post")
	fake.Reply(`{"doc":{}}`, "cc-present", "outcomes")
	fake.Reply(`[]`, "ccn")
	fake.Reply(`{"Self":{"DNSName":"mac.tail1.ts.net."}}`, "tailscale", "status")
	return fake
}

func fanout(t *testing.T, runner shell.Runner) Fanout {
	t.Helper()
	entry := drive.Entry{Drive: "d", CCIDrive: "dar", StateDir: t.TempDir(), Checkout: "/repo", Sessions: []string{"board-session"}}
	if err := os.WriteFile(filepath.Join(entry.StateDir, talkboard.Links), []byte("{}"), 0o600); err != nil {
		t.Fatal(err)
	}
	return Fanout{Runner: runner, Entry: entry, Now: func() time.Time { return sentAt }, Log: slog.New(slog.DiscardHandler)}
}

func calls(fake *shelltest.Fake, tool string) []shell.Command {
	return slices.DeleteFunc(fake.Calls(), func(c shell.Command) bool { return c.Argv[0] != tool || onThread(c) || c.Argv[1] == "outcomes" })
}

func onThread(c shell.Command) bool {
	return slices.Contains(c.Argv, "talk-d")
}

func threadWrites(fake *shelltest.Fake) []shell.Command {
	return slices.DeleteFunc(fake.Calls(), func(c shell.Command) bool { return !onThread(c) || c.Argv[1] == "outcomes" })
}

func flag(t *testing.T, argv []string, name string) string {
	t.Helper()
	i := slices.Index(argv, name)
	if i < 0 || i+1 == len(argv) {
		t.Fatalf("no %s in %q", name, argv)
	}
	return argv[i+1]
}

type child struct {
	ID      string `json:"id"`
	Type    string `json:"type"`
	MD      string `json:"md"`
	Repo    string `json:"repo"`
	Number  int    `json:"number"`
	Options []struct {
		Label       string `json:"label"`
		Md          string `json:"md"`
		Recommended bool   `json:"recommended"`
		Build       *Build `json:"build"`
	} `json:"options"`
}

type boardCard struct {
	ID       string  `json:"id"`
	Title    string  `json:"title"`
	Summary  string  `json:"summary"`
	Children []child `json:"children"`
}

func drawnCard(t *testing.T, c shell.Command) boardCard {
	t.Helper()
	var card boardCard
	if err := json.Unmarshal([]byte(c.Stdin), &card); err != nil {
		t.Fatalf("board card %q: %v", c.Stdin, err)
	}
	return card
}

type slackCard struct {
	Key      string `json:"key"`
	Replaces string `json:"replaces"`
	Title    string `json:"title"`
	Body     string `json:"body"`
	Links    []Link `json:"links"`
	Refs     []Link `json:"refs"`
}

func slack(t *testing.T, c shell.Command) slackCard {
	t.Helper()
	var card slackCard
	if err := json.Unmarshal([]byte(flag(t, c.Argv, "--card")), &card); err != nil {
		t.Fatal(err)
	}
	return card
}

func stored(t *testing.T, f Fanout, id string) (Reply, []Outcome) {
	t.Helper()
	data, err := os.ReadFile(filepath.Join(f.Entry.StateDir, storeDir, id+".json"))
	if err != nil {
		t.Fatal(err)
	}
	var response struct {
		Reply    Reply     `json:"reply"`
		Surfaces []Outcome `json:"surfaces"`
	}
	if err := json.Unmarshal(data, &response); err != nil {
		t.Fatal(err)
	}
	return response.Reply, response.Surfaces
}

func TestOneReplyThroughTheMCPToolReachesEverySurfaceUnderOneID(t *testing.T) {
	config := t.TempDir()
	t.Setenv("CLAUDE_CONFIG_DIR", config)
	t.Setenv(drive.Env, "d")
	f := fanout(t, nil)
	ledgerRow(t, f, store.ReviewRow{Title: "Relay fix", ForUsers: "Chats load"})
	entry, err := json.Marshal(f.Entry)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(config, "long-running", "drives"), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(config, "long-running", "drives", "d.json"), entry, 0o600); err != nil {
		t.Fatal(err)
	}
	fake := scripted()
	call, err := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": map[string]any{"name": "reply", "arguments": map[string]any{
		"title": "Land the relay fix tonight?", "md": "Its checks passed.\n\n| check | state |\n|---|---|\n| lr-go | green |",
		"picks": []string{"Land it", "Hold it"}, "recommended": "Land it", "prs": []string{landPR},
	}}})
	if err != nil {
		t.Fatal(err)
	}
	var out bytes.Buffer
	server := Command(&app.App{Runner: fake, Now: func() time.Time { return sentAt }}, verbTree(nil))
	server.SetArgs([]string{"serve"})
	server.SetIn(strings.NewReader(`{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}` + "\n" + string(call) + "\n"))
	server.SetOut(&out)
	if err := server.ExecuteContext(context.Background()); err != nil {
		t.Fatal(err)
	}

	var result struct {
		Result struct {
			IsError bool `json:"isError"`
			Content []struct {
				Text string `json:"text"`
			} `json:"content"`
		} `json:"result"`
	}
	for line := range strings.Lines(out.String()) {
		if strings.Contains(line, `"id":2`) {
			if err := json.Unmarshal([]byte(line), &result); err != nil {
				t.Fatal(err)
			}
		}
	}
	if result.Result.IsError || len(result.Result.Content) != 1 {
		t.Fatalf("tools/call = %s", out.String())
	}
	saved, err := filepath.Glob(filepath.Join(f.Entry.StateDir, storeDir, "*.json"))
	if err != nil || len(saved) != 1 {
		t.Fatalf("dashboard store holds %v, want one response", saved)
	}
	id := strings.TrimSuffix(filepath.Base(saved[0]), ".json")
	text := result.Result.Content[0].Text
	for _, want := range []string{"Its checks passed.", "| lr-go | green |", "- Land it (recommended)\n- Hold it", "Reply " + id + ";", "- board: card-" + id, "- slack: " + permalink, "- record: #12@2479ec97", "- dashboard: " + saved[0]} {
		if !strings.Contains(text, want) {
			t.Errorf("window text lacks %q:\n%s", want, text)
		}
	}
	presented := calls(fake, "cc-present")
	if len(presented) != 1 || flag(t, presented[0].Argv, "--session") != "board-session" || drawnCard(t, presented[0]).ID != "card-"+id {
		t.Fatalf("board writes = %+v, want card-%s on the drive's board", presented, id)
	}
	records := calls(fake, "cci")
	if len(records) != 1 || flag(t, records[0].Argv, "--topic") != "reply:"+id || flag(t, records[0].Argv, "--lane") != "owner-relay" || flag(t, records[0].Argv, "--pr") != landPR {
		t.Fatalf("records = %+v, want one owner-relay record on topic reply:%s naming the PR", records, id)
	}
	if !strings.HasPrefix(records[0].Stdin, "Land the relay fix tonight?\n\nIts checks passed.") {
		t.Fatalf("record body = %q, want the reply's title, then the reply", records[0].Stdin)
	}
	if strings.HasPrefix(text, "Land the relay fix tonight?") {
		t.Fatalf("window text opens with the title, so an update's dashboard link is not its first line:\n%s", text)
	}
	reply, surfaces := stored(t, f, id)
	if reply.ID != id || len(surfaces) != 4 || surfaces[1] != (Outcome{Surface: "slack", Ref: permalink}) {
		t.Fatalf("dashboard response = %+v %+v, want the reply under %s with its board, slack and record", reply, surfaces, id)
	}
	if len(calls(fake, "cc-slack")) != 0 {
		t.Fatal("a pick posted a second Slack card beside cc-present's")
	}
}

func TestASlackFailureStillReachesTheOtherSurfacesAndNamesItself(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Fail(1, "channel_not_found", "cc-slack", "dm-status")
	fake.Reply(posted, "cci", "post")
	f := fanout(t, fake)
	r := Reply{ID: "r1", Title: "The signal one-pager is live", MD: "It covers the path as built.", Links: []Link{{Label: "One-pager", URL: "https://studio.example.ts.net/one-pagers/signal/"}}}

	sent := f.Send(context.Background(), r)

	sent = slices.DeleteFunc(sent, func(o Outcome) bool { return o.Surface == "talk" })
	if len(sent) != 3 || sent[0].Surface != "slack" || !strings.Contains(sent[0].Error, "channel_not_found") {
		t.Fatalf("outcomes = %+v, want slack failed with its reason", sent)
	}
	if sent[1] != (Outcome{Surface: "record", Ref: "#12@2479ec97"}) || sent[2].Surface != "dashboard" || sent[2].Error != "" {
		t.Fatalf("outcomes = %+v, want the record and dashboard delivered", sent)
	}
	if text := receipt(r, sent); !strings.Contains(text, "- slack FAILED: ") || !strings.Contains(text, "- record: #12@2479ec97") {
		t.Fatalf("window text does not name the failed surface:\n%s", text)
	}
	if _, surfaces := stored(t, f, "r1"); surfaces[0].Error == "" {
		t.Fatalf("dashboard response %+v hides the slack failure", surfaces)
	}
}

func TestACardSlackDroppedIsToldAsAWarningAndTheReplyStillPostsOnce(t *testing.T) {
	const dropped = "Slack dropped this post's card: Message metadata was incorrectly formatted."
	fake := &shelltest.Fake{}
	fake.Reply(`{"channel_id":"D0B6Z","thread_ts":"1793635190.051179","ts":"1793635190.051179","permalink":"`+permalink+`","warning":"`+dropped+`"}`, "cc-slack", "dm-status")
	scriptedOver(fake)
	f := fanout(t, fake)
	r := Reply{ID: "r1", Title: "It landed", MD: "The PR is in.", PRs: []string{landPR}}

	sent := f.Send(context.Background(), r)

	got := surface(sent, "slack")
	if got.Ref != permalink || got.Error != "" || got.Warning != dropped {
		t.Fatalf("slack outcome = %+v, want the message posted with Slack's warning and no error", got)
	}
	text := receipt(r, sent)
	if strings.Contains(text, "FAILED") || !strings.Contains(text, "- slack: "+permalink) || !strings.Contains(text, "never resend it: "+dropped) {
		t.Fatalf("window text reads as a failed post or hides the warning:\n%s", text)
	}

	sent = f.Send(context.Background(), Reply{ID: "r1", Title: "It landed", MD: "The PR is in, edited.", PRs: []string{landPR}})

	tried := calls(fake, "cc-slack")
	if len(tried) != 2 || slack(t, tried[1]).Replaces != permalink {
		t.Fatalf("cc-slack calls = %+v, want the same id to edit the first message, never post again", tried)
	}
	if got := surface(sent, "slack"); got.Ref != permalink || got.Error != "" || got.Warning != dropped {
		t.Fatalf("edit outcome = %+v, want the edit's warning told too", got)
	}
}

func TestAPickRendersOneButtonPerOptionWithTheRecommendedOneMarked(t *testing.T) {
	fake := scripted()
	f := fanout(t, fake)
	picks := []string{"Keep one shared replica", "Give each chat its own", "Ask me after the soak"}

	f.Send(context.Background(), Reply{ID: "r2", Title: "How do we fix the relay?", MD: "Each chat keeps its own replica, or they share one.", Picks: picks, Recommended: picks[1], PRs: []string{landPR}})

	card := drawnCard(t, calls(fake, "cc-present")[0])
	choice := slices.IndexFunc(card.Children, func(c child) bool { return c.Type == "choice" })
	if choice < 0 || len(card.Children[choice].Options) != len(picks) {
		t.Fatalf("card %+v, want one choice with a button per pick", card)
	}
	for n, option := range card.Children[choice].Options {
		if option.Label != picks[n] || option.Recommended != (n == 1) {
			t.Errorf("button %d = %+v, want %q recommended=%v", n, option, picks[n], n == 1)
		}
	}
	pr := card.Children[len(card.Children)-1]
	if pr.Type != "pr" || pr.Repo != "yasyf/cc-skills" || pr.Number != 2196 {
		t.Fatalf("card's last child = %+v, want the PR card for %s", pr, landPR)
	}
}

func TestTheRecommendedPickCarriesTheBranchItIsBuiltOn(t *testing.T) {
	fake := scripted()
	f := fanout(t, fake)
	picks := []string{"Keep one shared replica", "Give each chat its own"}
	built := &Build{Repo: "yasyf/cc-skills", Branch: "relay-per-chat"}

	f.Send(context.Background(), Reply{ID: "r5", Title: "How do we fix the relay?", MD: "Each chat keeps its own replica, or they share one.", Picks: picks, Recommended: picks[1], Build: built})

	card := drawnCard(t, calls(fake, "cc-present")[0])
	choice := card.Children[slices.IndexFunc(card.Children, func(c child) bool { return c.Type == "choice" })]
	if choice.Options[0].Build != nil || choice.Options[1].Build == nil || *choice.Options[1].Build != *built {
		t.Fatalf("options %+v, want the branch on the recommended pick only", choice.Options)
	}
}

func TestABuildOnNoBranchNamePostsTheCardWithTheWarningUnderTheRecommendedPick(t *testing.T) {
	picks := []string{"Both become settings :: The server gains two switches.", "Neither :: Both stay fixed behavior."}
	for name, build := range map[string]Build{
		"words":        {Repo: "Forge-AI/monorepo", Branch: "not built yet: it would join the inbox change"},
		"two dots":     {Repo: "Forge-AI/monorepo", Branch: "inbox..settings"},
		"leading dash": {Repo: "Forge-AI/monorepo", Branch: "-inbox"},
		"lock suffix":  {Repo: "Forge-AI/monorepo", Branch: "inbox.lock"},
		"no branch":    {Repo: "Forge-AI/monorepo"},
		"no repo":      {Branch: "inbox-settings"},
	} {
		t.Run(name, func(t *testing.T) {
			r, warned := Reply{ID: "r6", Title: "Inbox settings", MD: "Two are fixed.", Picks: picks, Recommended: "Both become settings", Build: &build}.mended()
			fake := scripted()
			sent := fanout(t, fake).Send(context.Background(), r)

			card := drawnCard(t, calls(fake, "cc-present")[0])
			choice := card.Children[slices.IndexFunc(card.Children, func(c child) bool { return c.Type == "choice" })]
			got := choice.Options[0].Build
			if len(warned) != 1 || got == nil || got.Repo != "" || got.Branch != "" || !strings.Contains(got.None, "no branch is linked") {
				t.Fatalf("warned %q, recommended build %+v, want one warning and the card saying no branch is linked", warned, got)
			}
			if surface(sent, "board").Ref != "card-r6" || surface(sent, "slack").Ref != permalink {
				t.Fatalf("sent = %+v, want the card on the board and in Slack", sent)
			}
		})
	}
	both, warned := Reply{ID: "r6", Title: "t", MD: "m", Picks: picks, Recommended: "Neither", Build: &Build{Repo: "Forge-AI/monorepo", Branch: "inbox", None: "a process\npick"}}.mended()
	if len(warned) != 1 || *both.Build != (Build{None: "a process pick"}) {
		t.Fatalf("warned %q, build %+v, want the reason alone on one line", warned, both.Build)
	}
	named := Build{Repo: "yasyf/cc-skills", Branch: "yasyf/relay-per-chat"}
	kept, warned := Reply{ID: "r6", Title: "t", MD: "m", Picks: picks, Recommended: "Neither", Build: &named}.mended()
	if len(warned) != 0 || *kept.Build != named {
		t.Fatalf("warned %q, build %+v, want a real branch kept", warned, kept.Build)
	}
}

type slackGone struct {
	*shelltest.Fake
	back bool
}

func (g *slackGone) Run(ctx context.Context, c shell.Command) (shell.Result, error) {
	if c.Argv[0] == "cc-slack" && !g.back {
		return shell.Result{Code: shell.CodeMissingBinary}, fmt.Errorf("cc-slack is not on PATH and no installed plugin carries it, so install it: %w", exec.ErrNotFound)
	}
	return g.Fake.Run(ctx, c)
}

func TestASlackToolGoneForGoodSaysToSendAgainAndTheSendPostsOnce(t *testing.T) {
	fake := scriptedOver(&shelltest.Fake{})
	gone := &slackGone{Fake: fake}
	f := fanout(t, gone)
	r := Reply{ID: "r1", Title: "The line", MD: "The words.", Status: "info"}

	sent := f.Send(context.Background(), r)

	if got := surface(sent, "slack"); got.Ref != "" || !strings.Contains(got.Error, "install it") || !strings.Contains(got.Error, "send this reply again with its id") {
		t.Fatalf("slack outcome = %+v, want what to do named", got)
	}
	if text := receipt(r, sent); !strings.Contains(text, "slack FAILED: ") {
		t.Fatalf("receipt does not tell the lane: %s", text)
	}

	gone.back = true
	f.Send(context.Background(), r)
	f.Send(context.Background(), r)

	tried := calls(fake, "cc-slack")
	if len(tried) != 2 || slack(t, tried[0]).Replaces != "" || slack(t, tried[1]).Replaces != permalink {
		t.Fatalf("cc-slack calls = %+v, want one post and then an edit of it", tried)
	}
}

func TestACardCopyWithTheToolGonePromisesNoResend(t *testing.T) {
	f := fanout(t, &slackGone{Fake: &shelltest.Fake{}})

	copies := f.cardCopies(context.Background(), Reply{ID: "r1", Thread: hisThread, OpenCards: []board.OpenCard{{ID: "card-x", URL: permalink}}}, nil)

	if len(copies) != 1 || !strings.Contains(copies[0].Error, "install it") || strings.Contains(copies[0].Error, "send this reply again") {
		t.Fatalf("copies = %+v; a re-send skips card copies, so it must not promise one", copies)
	}
}

func TestAnEditUpdatesEachSurfaceInPlace(t *testing.T) {
	for name, edit := range map[string]func(Reply, string) Reply{
		"pick": func(r Reply, md string) Reply {
			r.MD, r.Picks, r.Recommended = md, []string{"Land it", "Hold it"}, "Land it"
			return r
		},
		"status": func(r Reply, md string) Reply {
			r.MD, r.Status, r.PRs = md, "done", []string{landPR}
			return r
		},
	} {
		t.Run(name, func(t *testing.T) {
			fake := scripted()
			f := fanout(t, fake)
			first := Reply{ID: "r3", Title: "The relay fix"}

			f.Send(context.Background(), edit(first, "Checks are running."))
			f.Send(context.Background(), edit(first, "Checks passed."))

			for _, c := range calls(fake, "cc-present") {
				if card := drawnCard(t, c); card.ID != "card-r3" || !strings.Contains(card.Summary, "Checks") {
					t.Errorf("board write %+v is not card-r3", card)
				}
			}
			if posted := calls(fake, "cc-slack"); len(posted) == 2 {
				if first, edit := slack(t, posted[0]), slack(t, posted[1]); first.Key != "r3" || first.Replaces != "" || edit.Replaces != permalink || edit.Key != "" {
					t.Errorf("slack cards = %+v then %+v, want the first keyed r3 and the edit replacing %s", first, edit, permalink)
				}
			}
			records := calls(fake, "cci")
			if len(records) != 2 || flag(t, records[0].Argv, "--topic") != "reply:r3" || flag(t, records[1].Argv, "--topic") != "reply:r3" || flag(t, records[0].Argv, "--id") == flag(t, records[1].Argv, "--id") {
				t.Fatalf("records = %+v, want two posts on topic reply:r3 under different keys", records)
			}
			if len(calls(fake, "cc-present"))+len(calls(fake, "cc-slack")) != 2 {
				t.Fatalf("calls = %+v, want one Slack-facing write per send", fake.Calls())
			}
			saved, err := filepath.Glob(filepath.Join(f.Entry.StateDir, storeDir, "*"))
			if err != nil || len(saved) != 1 {
				t.Fatalf("dashboard store = %v, want r3.json alone", saved)
			}
			if reply, _ := stored(t, f, "r3"); reply.MD != "Checks passed." {
				t.Fatalf("dashboard response md = %q, want the edit", reply.MD)
			}
		})
	}
}

func TestAnEditThatDropsItsPicksReplacesTheSlackCopyInPlaceAndPostsNothingNew(t *testing.T) {
	fake := scripted()
	fake.Reply("removed card-r6\nrev: 9\n", "cc-present", "remove-block")
	f := fanout(t, fake)
	asked := Reply{ID: "r6", Title: "Land the relay fix tonight?", MD: "Its checks passed.", Picks: []string{"Land it", "Hold it"}, Recommended: "Land it"}
	landed := Reply{ID: "r6", Title: "The relay fix landed", MD: "It merged.", Status: "done", PRs: []string{landPR}}

	f.Send(context.Background(), asked)
	for edit := range 2 {
		sent := f.Send(context.Background(), landed)
		if sent[0] != (Outcome{Surface: "board", Ref: "card-r6 withdrawn"}) || sent[1] != (Outcome{Surface: "slack", Ref: permalink}) {
			t.Fatalf("edit %d outcomes = %+v, want card-r6 off the board and its Slack card edited in place", edit, sent)
		}
	}

	removed := slices.DeleteFunc(calls(fake, "cc-present"), func(c shell.Command) bool { return c.Argv[1] != "remove-block" })
	if len(removed) != 1 || removed[0].Argv[2] != "card-r6" || flag(t, removed[0].Argv, "--session") != "board-session" || !strings.Contains(flag(t, removed[0].Argv, "--reason"), "The relay fix landed") {
		t.Fatalf("removals = %+v, want card-r6 removed once from the drive's board, naming the edit", removed)
	}
	posted := calls(fake, "cc-slack")
	if len(posted) != 2 {
		t.Fatalf("cc-slack calls = %+v, want each edit to redraw the Slack copy once", posted)
	}
	var card struct {
		Replaces string `json:"replaces"`
		Title    string `json:"title"`
		Body     string `json:"body"`
		Key      string `json:"key"`
		Options  []string
	}
	if err := json.Unmarshal([]byte(flag(t, posted[1].Argv, "--card")), &card); err != nil {
		t.Fatal(err)
	}
	if card.Replaces != permalink || card.Title != "The relay fix landed" || !strings.HasPrefix(card.Body, "It merged.") || card.Key != "" || len(card.Options) != 0 {
		t.Fatalf("slack edit = %+v, want the same message with the new title and body and no buttons", card)
	}
	if reply, surfaces := stored(t, f, "r6"); len(reply.Picks) != 0 || reply.MD != "It merged." || surfaces[0].Ref != "card-r6 withdrawn" {
		t.Fatalf("dashboard response = %+v %+v, want the edit with no picks", reply, surfaces)
	}
	records := calls(fake, "cci")
	if len(records) != 3 || !strings.HasPrefix(records[2].Stdin, "The relay fix landed\n\nIt merged.") {
		t.Fatalf("records = %+v, want the edit's body in the last record", records)
	}
}

func TestAnEditOfAnAnsweredCardReadsAsGoneAndStillReachesSlack(t *testing.T) {
	fake := scripted()
	fake.Fail(1, `no block "card-r7" in the current document`, "cc-present", "remove-block")
	f := fanout(t, fake)
	f.Send(context.Background(), Reply{ID: "r7", Title: "Land it?", MD: "Checks passed.", Picks: []string{"Land it", "Hold it"}, Recommended: "Land it"})

	sent := f.Send(context.Background(), Reply{ID: "r7", Title: "Landed", MD: "It merged.", Status: "done"})

	if sent[0] != (Outcome{Surface: "board", Ref: "card-r7 withdrawn"}) || sent[1] != (Outcome{Surface: "slack", Ref: permalink}) {
		t.Fatalf("outcomes = %+v, want an answered card read as gone and its Slack copy edited", sent)
	}
}

func TestAFailedWithdrawalNamesItselfAndStillRecordsTheEdit(t *testing.T) {
	fake := scripted()
	fake.Fail(1, "daemon not running", "cc-present", "remove-block")
	f := fanout(t, fake)
	f.Send(context.Background(), Reply{ID: "r7", Title: "Land it?", MD: "Checks passed.", Picks: []string{"Land it", "Hold it"}, Recommended: "Land it"})

	sent := f.Send(context.Background(), Reply{ID: "r7", Title: "Landed", MD: "It merged.", Status: "done"})

	if len(sent) != 5 || sent[0].Surface != "board" || !strings.Contains(sent[0].Error, "daemon not running") || sent[1].Surface != "slack" || !strings.Contains(sent[1].Error, "keeps its buttons") {
		t.Fatalf("outcomes = %+v, want board failed with its reason and slack naming the live buttons", sent)
	}
	if sent[2].Surface != "talk" || sent[3] != (Outcome{Surface: "record", Ref: "#12@2479ec97"}) || sent[4].Surface != "dashboard" || sent[4].Error != "" {
		t.Fatalf("outcomes = %+v, want talk attempted and the record and dashboard delivered", sent)
	}
	if posted := calls(fake, "cc-slack"); len(posted) != 0 {
		t.Fatalf("cc-slack calls = %+v, want no new Slack message", posted)
	}
}

func TestAStatusReplyListsAPRItsTextNeverNamesUnderItsText(t *testing.T) {
	fake := scripted()
	fanout(t, fake).Send(context.Background(), Reply{ID: "r5", Title: "The relay fix", MD: "Checks passed.", Status: "done", PRs: []string{landPR}})
	posted := calls(fake, "cc-slack")
	if len(posted) != 1 {
		t.Fatalf("cc-slack calls = %+v, want one card", posted)
	}
	if card := slack(t, posted[0]); card.Body != "Checks passed.\n\n- "+landPR || len(card.Refs) != 0 {
		t.Fatalf("card = %+v, want the PR in its text and no refs", card)
	}
}

func TestAPickSaysWhatPickingItDoesOnItsCard(t *testing.T) {
	asked := Reply{ID: "r8", Title: "How do we fix the relay?", MD: "Each chat keeps its own replica, or they share one.", Picks: []string{"Give each chat its own :: A chat keeps working when another's replica fails.", "Keep one shared"}, Recommended: "Give each chat its own :: A chat keeps working when another's replica fails."}
	mended, warned := asked.mended()
	if !slices.Equal(mended.Picks, []string{"Give each chat its own", "Keep one shared"}) || mended.Recommended != "Give each chat its own" {
		t.Fatalf("mended to picks %q, recommended %q; want the labels alone", mended.Picks, mended.Recommended)
	}
	if len(warned) != 1 || !strings.Contains(warned[0], `"Keep one shared" does not say what picking it does`) {
		t.Fatalf("warned %q, want the pick with no words named", warned)
	}
	fake := scripted()
	fanout(t, fake).Send(context.Background(), mended)
	card := drawnCard(t, calls(fake, "cc-present")[0])
	choice := slices.IndexFunc(card.Children, func(c child) bool { return c.Type == "choice" })
	options := card.Children[choice].Options
	if options[0].Md != "A chat keeps working when another's replica fails." || options[1].Md != "" {
		t.Fatalf("options %+v, want the first with its words and the second with none", options)
	}
	if got := mended.markdown(); !strings.Contains(got, "- Give each chat its own (recommended): A chat keeps working") || !strings.Contains(got, "- Keep one shared") {
		t.Fatalf("markdown = %q", got)
	}
}

func TestAColonPairInsideALabelIsNotASplit(t *testing.T) {
	got, _ := Reply{ID: "r9", Title: "t", MD: "m", Picks: []string{"Rename thread::chapter :: Callers move in one PR.", "Keep thread::chapter :: Nothing moves."}, Recommended: "Keep thread::chapter"}.mended()
	if !slices.Equal(got.Picks, []string{"Rename thread::chapter", "Keep thread::chapter"}) || got.Words["Keep thread::chapter"] != "Nothing moves." || got.Recommended != "Keep thread::chapter" {
		t.Fatalf("mended to %+v", got)
	}
}

func TestAMalformedReplyPostsWithAWarningInsteadOfARefusal(t *testing.T) {
	base := Reply{ID: "r4", Title: "Pick a relay", MD: "Two ways."}
	for name, c := range map[string]struct {
		in   Reply
		want func(Reply) bool
	}{
		"one pick":          {Reply{ID: base.ID, Title: base.Title, MD: base.MD, Picks: []string{"Only"}, Recommended: "Only"}, func(r Reply) bool { return len(r.Picks) == 0 && r.Recommended == "" }},
		"no recommendation": {Reply{ID: base.ID, Title: base.Title, MD: base.MD, Picks: []string{"A way", "B way"}}, func(r Reply) bool { return len(r.Picks) == 2 && r.Recommended == "" }},
		"repeated pick":     {Reply{ID: base.ID, Title: base.Title, MD: base.MD, Picks: []string{"A way", "A way"}, Recommended: "A way"}, func(r Reply) bool { return len(r.Picks) == 0 }},
		"unlabeled link":    {Reply{ID: base.ID, Title: base.Title, MD: base.MD, Links: []Link{{URL: "https://studio.ts.net/doc/"}}}, func(r Reply) bool { return len(r.Links) == 0 }},
		"file link":         {Reply{ID: base.ID, Title: base.Title, MD: base.MD, Links: []Link{{Label: "Doc", URL: "file:///tmp/doc.md"}}}, func(r Reply) bool { return len(r.Links) == 0 }},
		"not a PR":          {Reply{ID: base.ID, Title: base.Title, MD: base.MD, PRs: []string{"yasyf/cc-skills#2196"}}, func(r Reply) bool { return len(r.PRs) == 0 }},
		"made-up id":        {Reply{ID: "../r4", Title: base.Title, MD: base.MD}, func(r Reply) bool { return r.ID == "" }},
		"build, no pick":    {Reply{ID: base.ID, Title: base.Title, MD: base.MD, Build: &Build{None: "a process pick"}}, func(r Reply) bool { return r.Build == nil }},
		"bad status":        {Reply{ID: base.ID, Title: base.Title, MD: base.MD, Status: "urgent"}, func(r Reply) bool { return r.Status == "" }},
		"bad row":           {Reply{ID: base.ID, Title: base.Title, MD: base.MD, Order: "347"}, func(r Reply) bool { return r.Order == "" }},
		"lone action":       {Reply{ID: base.ID, Title: base.Title, MD: base.MD, Action: "Tap it"}, func(r Reply) bool { return r.Action == "" }},
		"untitled notebook": {Reply{ID: base.ID, Title: base.Title, MD: "See https://app.datadoghq.com/notebook/123 for it."}, func(r Reply) bool { return strings.Contains(r.MD, "notebook/123") }},
		"typeless block":    {Reply{ID: base.ID, Title: base.Title, MD: base.MD, Blocks: []json.RawMessage{json.RawMessage(`{"id":"x"}`)}}, func(r Reply) bool { return len(r.Blocks) == 0 }},
	} {
		got, warned := c.in.mended()
		if !c.want(got) || len(warned) == 0 {
			t.Errorf("%s: mended to %+v, warned %q", name, got, warned)
		}
	}
	if got, warned := base.mended(); len(warned) != 0 || got.Title != base.Title || got.MD != base.MD {
		t.Fatalf("a plain reply mended to %+v, warned %q", got, warned)
	}
	titled := Reply{ID: base.ID, Title: base.Title, MD: "See [LLM spend](https://app.datadoghq.com/notebook/123/llm-spend) for it."}
	if _, warned := titled.mended(); len(warned) != 0 {
		t.Fatalf("a titled notebook link warned %q", warned)
	}
}

func TestAReplyMissingATextPartTakesItFromTheOther(t *testing.T) {
	if got, _ := (Reply{ID: "a", Title: "t", MD: " "}).mended(); got.MD != "t" {
		t.Fatalf("no md: %+v", got)
	}
	if got, _ := (Reply{ID: "a", Title: " ", MD: "first\nsecond"}).mended(); got.Title != "first" {
		t.Fatalf("no title: %+v", got)
	}
	if got, _ := (Reply{ID: "a", Title: "Pick\na relay", MD: "m"}).mended(); got.Title != "Pick a relay" {
		t.Fatalf("two-line title: %+v", got)
	}
	if got, _ := (Reply{ID: "a", MD: " "}).mended(); got.MD != "" {
		t.Fatalf("nothing to say: %+v", got)
	}
	if got, _ := (Reply{ID: "a", Title: "t", MD: "m", Order: "m1", Action: "Tap it\nThen this"}).mended(); got.Action != "Tap it Then this" {
		t.Fatalf("two-line action: %+v", got)
	}
}

func TestATitleLedPastTheLimitIsShortenedAndStillPosts(t *testing.T) {
	long := strings.TrimSpace(strings.Repeat("word ", 27))
	mended, _ := Reply{ID: "a", Title: long, MD: "m", Order: "m1", Action: "Tap one of these below please", Picks: []string{"A", "B"}, Recommended: "A"}.mended()
	if got := mended.led().Title; utf8.RuneCountInString(got) > maxTitle || got == long {
		t.Fatalf("title %q, want it led and at most %d characters", got, maxTitle)
	}
}

func TestAnActionWithALongTitlePostsThroughTheTool(t *testing.T) {
	title := strings.TrimSpace(strings.Repeat("word ", 19)) + " up"
	if len(title) != 97 {
		t.Fatalf("title is %d characters, want 97", len(title))
	}
	for name, tc := range map[string]struct {
		action string
		whole  bool
	}{
		"a short action keeps the whole title": {"Read the plan", true},
		"a long action shortens it":            {strings.Repeat("Read the plan in the doc ", 4), false},
	} {
		t.Run(name, func(t *testing.T) {
			config := t.TempDir()
			t.Setenv("CLAUDE_CONFIG_DIR", config)
			t.Setenv(drive.Env, "d")
			f := fanout(t, nil)
			ledgerHolds(t, f.Entry, "| m543 | Nov 4 2:29 PM | where is it |  | open |")
			entry, err := json.Marshal(f.Entry)
			if err != nil {
				t.Fatal(err)
			}
			if err := os.MkdirAll(filepath.Join(config, "long-running", "drives"), 0o750); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(filepath.Join(config, "long-running", "drives", "d.json"), entry, 0o600); err != nil {
				t.Fatal(err)
			}
			args, err := json.Marshal(map[string]any{"title": title, "md": "The body.", "order": "m543", "action": tc.action})
			if err != nil {
				t.Fatal(err)
			}
			fake := scripted()
			text, failed := Tool(&app.App{Runner: fake, Now: func() time.Time { return sentAt }}).Handler(context.Background(), args, func(string) {})
			if failed || strings.Contains(text, "Warning") {
				t.Fatalf("reply = %q, failed %v; want it posted with no warning", text, failed)
			}
			records := calls(fake, "cci")
			if len(records) != 1 {
				t.Fatalf("cci calls = %d, want the one record", len(records))
			}
			posted, _, _ := strings.Cut(records[0].Stdin, "\n")
			if !strings.HasPrefix(posted, "Read the plan") || utf8.RuneCountInString(posted) > maxTitle {
				t.Fatalf("title %q does not lead with the action inside %d characters", posted, maxTitle)
			}
			if strings.Contains(posted, title) != tc.whole {
				t.Fatalf("title %q: carries the whole title = %v, want %v", posted, !tc.whole, tc.whole)
			}
		})
	}
}

func TestOnlyAReplyTiedToNothingWarnsThatItNamesNoOrder(t *testing.T) {
	for name, tc := range map[string]struct {
		fields map[string]any
		warns  bool
	}{
		"names nothing":         {map[string]any{}, true},
		"names its row":         {map[string]any{"order": "m543"}, false},
		"names its line":        {map[string]any{"on": "line:m543"}, false},
		"says he asked nothing": {map[string]any{"unprompted": true}, false},
	} {
		t.Run(name, func(t *testing.T) {
			config := t.TempDir()
			t.Setenv("CLAUDE_CONFIG_DIR", config)
			t.Setenv(drive.Env, "d")
			f := fanout(t, nil)
			ledgerHolds(t, f.Entry, "| m543 | Nov 4 2:29 PM | where is it |  | open |")
			entry, err := json.Marshal(f.Entry)
			if err != nil {
				t.Fatal(err)
			}
			if err := os.MkdirAll(filepath.Join(config, "long-running", "drives"), 0o750); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(filepath.Join(config, "long-running", "drives", "d.json"), entry, 0o600); err != nil {
				t.Fatal(err)
			}
			tc.fields["title"], tc.fields["md"] = "The fix is live", "The body."
			args, err := json.Marshal(tc.fields)
			if err != nil {
				t.Fatal(err)
			}
			text, failed := Tool(&app.App{Runner: scripted(), Now: func() time.Time { return sentAt }}).Handler(context.Background(), args, func(string) {})
			if failed || strings.Contains(text, "names no order") != tc.warns {
				t.Fatalf("reply = %q, failed %v; want posted, warns = %v", text, failed, tc.warns)
			}
		})
	}
}

func TestAnExplainMoreThatIsOnlyALinkStaysRefused(t *testing.T) {
	base := Reply{ID: "r4", Title: "Pick a relay", MD: "Two ways."}
	for name, r := range map[string]Reply{
		"a pick explained by a link only": {ID: base.ID, Title: base.Title, MD: "see https://docs.poetic.design/one-pagers/x/", Picks: []string{"A way", "B way"}, Recommended: "A way"},
		"an Explain more that is a link":  {ID: base.ID, Title: base.Title, MD: "Two ways.\n\n**Explain more:** https://docs.poetic.design/one-pagers/x/"},
	} {
		if err := r.bareExplainMore(); err == nil {
			t.Errorf("%s: passed %+v", name, r)
		}
	}
	if err := base.bareExplainMore(); err != nil {
		t.Fatalf("plain reply refused: %v", err)
	}
	linked := Reply{ID: base.ID, Title: base.Title, MD: "Stored in the background.\n\nhttps://docs.poetic.design/one-pagers/x/"}
	if err := linked.bareExplainMore(); err != nil {
		t.Fatalf("a done reply naming its page refused: %v", err)
	}
}

func TestASlackBodyGoesWholeAsMarkdownForCCSlackToRender(t *testing.T) {
	fake := scripted()
	md := "## Relay\n**Fixed** in [the PR](https://github.com/yasyf/cc-skills/pull/2196).\n\n| check | state |\n|---|---|\n| lr-go | green |\n" + strings.Repeat("more words ", 500) + "end"
	fanout(t, fake).Send(context.Background(), Reply{ID: "long", Title: "t", MD: md, Status: "done"})
	if body := slack(t, calls(fake, "cc-slack")[0]).Body; body != md {
		t.Fatalf("slack body = %q, want the whole reply, unrendered and uncut", body)
	}
}

func TestAHiddenCiteStaysOnTheRecordAndOffEverySurfaceHeReads(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply(posted, "cci", "post")
	f := fanout(t, fake)
	r := Reply{ID: "r1", Title: "Why the run crashed", MD: "The runner reads only migration 0. <!-- cci #384714 -->", Links: []Link{{Label: "page", URL: "https://x"}}}

	f.Send(context.Background(), r)

	for _, call := range calls(fake, "cc-slack") {
		if strings.Contains(strings.Join(call.Argv, " "), "384714") {
			t.Fatalf("a cite reached Slack: %q", call.Argv)
		}
	}
	record := calls(fake, "cci")
	if len(record) != 1 || !strings.Contains(record[0].Stdin, "384714") {
		t.Fatalf("the record lost its cite: %+v", record)
	}
}

func TestAnAnswerToHisThreadReplyPostsInThatThreadAsWholeText(t *testing.T) {
	fake := scripted()
	long := strings.Repeat("a", 2000) + "\n\n" + strings.Repeat("b", 2000)
	r := Reply{ID: "r6", Title: "The relay lane is half done", MD: long, PRs: []string{landPR}, Links: []Link{{Label: "One-pager", URL: "https://studio.example.ts.net/one-pagers/relay/"}}, Thread: "1793684147.131059"}

	sent := fanout(t, fake).Send(context.Background(), r)

	posted := calls(fake, "cc-slack")
	if len(posted) != 1 || flag(t, posted[0].Argv, "--thread") != r.Thread || slices.Contains(posted[0].Argv, "--card") {
		t.Fatalf("cc-slack calls = %+v, want one text post under thread %s", posted, r.Thread)
	}
	want := "*The relay lane is half done*\n" + long + "\n\n- " + landPR + "\n<https://studio.example.ts.net/one-pagers/relay/|One-pager>"
	if text := posted[0].Argv[len(posted[0].Argv)-1]; text != want {
		t.Fatalf("thread text = %q, want the title, the whole reply, its PR and its link", text)
	}
	if surface(sent, "slack") != (Outcome{Surface: "slack", Ref: permalink}) {
		t.Fatalf("outcomes = %+v, want the thread reply's permalink", sent)
	}
}

func TestAPlainAnswerToHisThreadStillReachesTheThread(t *testing.T) {
	fake := scripted()
	fanout(t, fake).Send(context.Background(), Reply{ID: "r7", Title: "Yes, it reached the board", MD: "It is the third card.", Thread: "1793684147.131059"})
	if posted := calls(fake, "cc-slack"); len(posted) != 1 || flag(t, posted[0].Argv, "--thread") != "1793684147.131059" {
		t.Fatalf("cc-slack calls = %+v, want the answer in his thread", posted)
	}
	fake = scripted()
	fanout(t, fake).Send(context.Background(), Reply{ID: "r8", Title: "Yes, it reached the board", MD: "It is the third card."})
	if posted := calls(fake, "cc-slack"); len(posted) != 0 {
		t.Fatalf("cc-slack calls = %+v, want an answer to a chat ask kept off Slack", posted)
	}
}

func TestACardCarriesItsLeadParagraphOnce(t *testing.T) {
	fake := scripted()
	f := fanout(t, fake)

	f.Send(context.Background(), Reply{ID: "r8", Title: "Land it?", MD: "Checks passed.\n\nIt ships tonight.", Picks: []string{"Land it", "Hold it"}, Recommended: "Land it"})
	f.Send(context.Background(), Reply{ID: "r9", Title: "Hold it?", MD: "Checks passed.", Picks: []string{"Land it", "Hold it"}, Recommended: "Land it"})

	sent := calls(fake, "cc-present")
	card := drawnCard(t, sent[0])
	if card.Summary != "Checks passed." || card.Children[0].Type != "markdown" || card.Children[0].MD != "It ships tonight." {
		t.Fatalf("card %+v, want the lead in the summary and the rest in the markdown", card)
	}
	if card := drawnCard(t, sent[1]); card.Summary != "Checks passed." || card.Children[0].Type != "choice" {
		t.Fatalf("card %+v, want a one-paragraph reply in the summary with no markdown child", card)
	}
}

func TestAReplyLandsOnTheThreadBeneathTheLastNoteAndStaysThereWhenEdited(t *testing.T) {
	fake := scripted()
	f := fanout(t, fake)
	notes := filepath.Join(f.Entry.StateDir, "owner-relay", "talk-notes")
	if err := os.MkdirAll(filepath.Dir(notes), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(notes, []byte("n1\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	first := Reply{ID: "r9", Title: "Gateway", MD: "It is healthy.", PRs: []string{landPR}}
	f.Send(context.Background(), first)
	if err := os.WriteFile(notes, []byte("n1\nn2\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	first.MD = "It is healthy again."
	f.Send(context.Background(), first)

	writes := threadWrites(fake)
	if len(writes) != 2 {
		t.Fatalf("thread writes = %+v, want one per send", writes)
	}
	for n, want := range []string{"It is healthy.", "It is healthy again."} {
		var card struct {
			ID       string  `json:"id"`
			Re       string  `json:"re"`
			Mirror   bool    `json:"mirror"`
			Children []child `json:"children"`
		}
		if err := json.Unmarshal([]byte(writes[n].Stdin), &card); err != nil {
			t.Fatal(err)
		}
		if card.ID != "reply-r9" || card.Re != "n1" || card.Mirror || card.Children[0].MD != want {
			t.Errorf("write %d = %+v, want reply-r9 beneath n1, off Slack, reading %q", n, card, want)
		}
		if card.Children[1].Type != "pr" || card.Children[1].Number != 2196 {
			t.Errorf("write %d children = %+v, want the pull request as a block", n, card.Children)
		}
	}
}

func TestAThreadFailureIsNamedAndTheOtherSurfacesStillGo(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Fail(1, "no artifact", "cc-present", "update-block")
	fake.Reply(`{"doc":{}}`, "cc-present", "outcomes")
	fake.Reply(posted, "cci", "post")
	f := fanout(t, fake)
	sent := f.Send(context.Background(), Reply{ID: "r1", Title: "Done", MD: "Done."})
	byName := map[string]Outcome{}
	for _, o := range sent {
		byName[o.Surface] = o
	}
	if byName["talk"].Error == "" || byName["record"].Ref == "" || byName["dashboard"].Error != "" {
		t.Fatalf("sent = %+v, want talk named as failed and record and dashboard delivered", sent)
	}
}

func TestAReplyToAnOrderLeadsWithHisOneActionAndOtherwiseReadsAsItsOwnText(t *testing.T) {
	for _, status := range []string{"", done, progress} {
		in := Reply{Title: "Stored in the background", MD: "A parse is stored only for a chat being opened.", Order: "m347", Status: status}
		if got := in.led(); !reflect.DeepEqual(got, in) {
			t.Fatalf("a %q reply asking nothing of him changed: %+v", status, got)
		}
	}
	acting := Reply{Title: "Storage", MD: "It cannot write the new store.", Order: "m347", Action: "Pick where it stores on your board."}.led()
	if !strings.HasPrefix(acting.MD, "Pick where it stores on your board.\n\nIt cannot") || acting.Status != needsYou {
		t.Fatalf("action reply = %q, status %q", acting.MD, acting.Status)
	}
	picking := Reply{Title: "Storage", MD: "Two places.", Order: "m347", Picks: []string{"A", "B"}, Recommended: "A"}.led()
	if picking.Title != "Storage" || picking.MD != "Two places." || picking.Status != needsYou {
		t.Fatalf("a reply with picks = %q, %q, status %q, want its own words waiting on him", picking.Title, picking.MD, picking.Status)
	}
	twice := Reply{Title: "Pick a store: storage", MD: "Pick a store. It cannot write the new one.", Order: "m347", Action: "Pick a store"}.led()
	if twice.Title != "Pick a store: storage" || twice.MD != "Pick a store. It cannot write the new one." {
		t.Fatalf("a lead the lane wrote doubled: %q, %q", twice.Title, twice.MD)
	}
	plain := Reply{Title: "A status line", MD: "Nothing he ordered."}.led()
	if plain.Title != "A status line" || plain.MD != "Nothing he ordered." {
		t.Fatalf("a reply naming no order changed: %q, %q", plain.Title, plain.MD)
	}
}

func ledgered(t *testing.T, f Fanout) string {
	t.Helper()
	path := owner.Ledger(f.Entry.StateDir)
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("| id | time | message | routed | state |\n|---|---|---|---|---|\n| m447 | Nov 3 10:55 PM | make the routed reply state |  | open |\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestAProgressReplyToHisOrderWritesTheLedgerRowAndPostsAtHisDMRoot(t *testing.T) {
	fake := scripted()
	f := fanout(t, fake)
	path := ledgered(t, f)
	sent := f.Send(context.Background(), Reply{ID: "rp", Title: "Vector search", MD: "tp-desc-vector-1 is designing it.", Status: progress, Order: "m447", By: "11:30 PM", Routed: 400803}.led())
	if got := calls(fake, "cc-slack"); len(got) != 1 || slices.Contains(got[0].Argv, "--thread") {
		t.Fatalf("cc-slack calls = %+v, want one post at his DM root", got)
	}
	ledger, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(ledger), "| m447 | Nov 3 10:55 PM | make the routed reply state | 400803 | due Nov 3 11:30 PM |") {
		t.Fatalf("ledger = %q", ledger)
	}
	if got := surface(sent, "ledger"); got.Ref != "m447 due Nov 3 11:30 PM" || got.Error != "" {
		t.Fatalf("ledger outcome = %+v", got)
	}
	if surface(sent, "record").Ref == "" || surface(sent, "dashboard").Ref == "" {
		t.Fatalf("the record and dashboard surfaces dropped: %+v", sent)
	}
}

func TestAnInfoReplyToHisOrderReachesSlackAndMarksTheRowResponded(t *testing.T) {
	fake := scripted()
	f := fanout(t, fake)
	path := ledgered(t, f)
	sent := f.Send(context.Background(), Reply{ID: "ri", Title: "Vector search", MD: "It is a design, not a build.", Status: info, Order: "m447"}.led())
	if got := calls(fake, "cc-slack"); len(got) != 1 {
		t.Fatalf("cc-slack calls = %+v, want his answer on Slack", got)
	}
	if surface(sent, "slack").Ref != permalink {
		t.Fatalf("outcomes = %+v, want the card's permalink", sent)
	}
	if ledger, _ := os.ReadFile(path); !strings.Contains(string(ledger), "|  | responded |") {
		t.Fatalf("ledger = %q", ledger)
	}
}

func TestByAndRoutedOffAQuietOrderReplyAreLeftOffWithAWarning(t *testing.T) {
	for name, r := range map[string]Reply{
		"no order":  {ID: "a", Title: "t", MD: "m", Status: progress, By: "11:30 PM"},
		"done":      {ID: "a", Title: "t", MD: "m", Status: "done", Order: "m1", Routed: 7},
		"incident":  {ID: "a", Title: "t", MD: "m", Status: "incident", Order: "m1", Routed: 7},
		"with pick": {ID: "a", Title: "t", MD: "m", Status: progress, Order: "m1", By: "11:30 PM", Picks: []string{"A", "B"}, Recommended: "A"},
	} {
		got, warned := r.mended()
		if got.By != "" || got.Routed != 0 || len(warned) == 0 {
			t.Errorf("%s: mended to %+v, warned %q", name, got, warned)
		}
	}
	for _, status := range []string{progress, info} {
		r := Reply{ID: "a", Title: "t", MD: "m", Status: status, Order: "m1", By: "11:30 PM", Routed: 7}
		if got, warned := r.mended(); got.By == "" || got.Routed == 0 || len(warned) != 0 {
			t.Fatalf("a %s reply mended to %+v, warned %q", status, got, warned)
		}
	}
}

func TestLiveBlocksLandOnTheThreadAndTheirActionsPostToTalk(t *testing.T) {
	fake := scripted()
	f := fanout(t, fake)
	table := json.RawMessage(`{"id":"anything","type":"table","columns":[{"key":"team","label":"Team"}],"rows":[{"team":"AIG"}],"rowKey":"team","actions":[{"id":"why","label":"Why?"}]}`)
	f.Send(context.Background(), Reply{ID: "r7", Title: "Indexing", MD: "Caught up.", Blocks: []json.RawMessage{table}})

	var card struct {
		Children []struct {
			ID      string `json:"id"`
			Type    string `json:"type"`
			Actions []struct {
				Target string `json:"target"`
			} `json:"actions"`
		} `json:"children"`
	}
	writes := threadWrites(fake)
	if len(writes) != 1 {
		t.Fatalf("thread writes = %+v, want one", writes)
	}
	if err := json.Unmarshal([]byte(writes[0].Stdin), &card); err != nil {
		t.Fatal(err)
	}
	if len(card.Children) != 2 || card.Children[1].Type != "table" || card.Children[1].ID != "reply-r7-b1" {
		t.Fatalf("children = %+v, want the markdown, then the table under an id of its own", card.Children)
	}
	if card.Children[1].Actions[0].Target != "talk" {
		t.Fatalf("action = %+v, want its press to reach the Talk section, where only named devices post", card.Children[1].Actions[0])
	}
	if strings.Contains(calls(fake, "cci")[0].Stdin, "anything") {
		t.Fatal("the record carried the block, want the reply's prose alone")
	}
}

func TestADriveWithNoTalkBoardSkipsTheThread(t *testing.T) {
	fake := scripted()
	f := fanout(t, fake)
	if err := os.Remove(filepath.Join(f.Entry.StateDir, talkboard.Links)); err != nil {
		t.Fatal(err)
	}
	sent := f.Send(context.Background(), Reply{ID: "r9", Title: "T", MD: "M"})
	if len(threadWrites(fake)) != 0 || surface(sent, "talk").Surface != "" {
		t.Fatalf("thread writes %+v and outcomes %+v, want nothing on a board that was never opened", threadWrites(fake), sent)
	}
}

func ledgerRow(t *testing.T, f Fanout, row store.ReviewRow) {
	t.Helper()
	if err := os.WriteFile(filepath.Join(f.Entry.StateDir, "lr.json"), []byte(`{"repo":"yasyf/cc-skills","drive":"d","checkout":"/repo","author":"a","label":"l","movers":["items"]}`), 0o600); err != nil {
		t.Fatal(err)
	}
	s, err := store.Open(f.Entry.Store())
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = s.Close() }()
	row.Repo, row.Number, row.Head = "yasyf/cc-skills", 2196, "e718a7434fa01ee9835f992a9a647a825350732c"
	row.ReadHead = cmp.Or(row.ReadHead, row.Head)
	if err := s.PutReviewRows([]store.ReviewRow{row}); err != nil {
		t.Fatal(err)
	}
}

func TestAReplyNamingAPortOpensWithItsSource(t *testing.T) {
	const opening = "Port, old tree to new: dev [#2190](https://github.com/yasyf/cc-skills/pull/2190)\n\n"
	f := fanout(t, scripted())
	ledgerRow(t, f, store.ReviewRow{Title: "Relay fix"})
	s, err := store.Open(f.Entry.Store())
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = s.Close() }()
	if _, err := s.PutFact("yasyf/cc-skills", 2196, "e718a7434fa01ee9835f992a9a647a825350732c", gates.PortOfAttr, "2190", sentAt); err != nil {
		t.Fatal(err)
	}
	for name, tc := range map[string]struct {
		reply Reply
		want  string
	}{
		"named in the text":                        {Reply{Title: "Relay fix", MD: "#2196 is green."}, opening + "#2196 is green."},
		"named in prs":                             {Reply{Title: "Relay fix", MD: "It is green.", PRs: []string{landPR}}, opening + "It is green."},
		"named twice opens once":                   {Reply{Title: "Relay fix", MD: "#2196 is green. #2196 lands."}, opening + "#2196 is green. #2196 lands."},
		"its text already opens":                   {Reply{Title: "Relay fix", MD: "Port, old tree to new: dev #2190: #2196 is green."}, "Port, old tree to new: dev #2190: #2196 is green."},
		"its title already opens":                  {Reply{Title: "port, old tree to new: dev [#2190](https://github.com/yasyf/cc-skills/pull/2190): relay fix", MD: "#2196 is green."}, "#2196 is green."},
		"no port named":                            {Reply{Title: "Relay fix", MD: "#2197 is green."}, "#2197 is green."},
		"a list of PRs names each in its own line": {Reply{Title: "Relay fixes", MD: "#2196 is green.\n#2197 is green."}, "#2196 is green.\n#2197 is green."},
		"another repo's number":                    {Reply{Title: "Relay fix", MD: "Forge-AI/monorepo#2196 is green."}, "Forge-AI/monorepo#2196 is green."},
	} {
		t.Run(name, func(t *testing.T) {
			got, warning := f.withPortOpening(tc.reply)
			if warning != "" || got.MD != tc.want {
				t.Fatalf("md %q, warning %q, want %q", got.MD, warning, tc.want)
			}
		})
	}
}

func TestAPRCardMissingALedgerFieldPostsAndWarnsInOneLine(t *testing.T) {
	for name, r := range map[string]Reply{
		"pick":   {ID: "r1", Title: "Land the relay fix?", MD: "Checks passed.", Picks: []string{"Land", "Hold"}, Recommended: "Land", PRs: []string{landPR}},
		"status": {ID: "r1", Title: "The relay fix", MD: "Checks passed.", Status: "info", PRs: []string{landPR}},
	} {
		t.Run(name, func(t *testing.T) {
			fake := scripted()
			f := fanout(t, fake)
			ledgerRow(t, f, store.ReviewRow{ReadyForTap: true, ForUsers: "Chats load"})
			got, warning, _ := f.withPRCards(context.Background(), r)
			if !strings.Contains(warning, "the plan row, the risk") || strings.Contains(warning, "what it changes") || strings.Contains(warning, "\n") {
				t.Fatalf("warning = %q, want one line naming the two missing fields", warning)
			}
			sent := f.Send(context.Background(), got)
			if out := surface(sent, "slack"); out.Error != "" || out.Ref != permalink {
				t.Fatalf("slack outcome = %+v, want the card posted", out)
			}
			for _, call := range fake.Calls() {
				if strings.Contains(strings.Join(call.Argv, " ")+call.Stdin, "missing:") {
					t.Fatalf("a surface got a placeholder: %q %q", call.Argv, call.Stdin)
				}
			}
		})
	}
}

func TestAPRCardMissingItsLedgerFieldsTakesThemFromItsPRBody(t *testing.T) {
	fake := scripted()
	fake.Reply(`{"title":"relay: fix the reconnect","body":"## Summary\n\nChats reconnect on their own after a network drop.\n\n## Risk profile\n\n- **Availability:** one retry loop.\n"}`, "gh", "pr", "view")
	f := fanout(t, fake)
	ledgerRow(t, f, store.ReviewRow{ReadyForTap: true})
	r, _, err := f.withPRCards(context.Background(), Reply{ID: "r1", Title: "Land the relay fix?", MD: "Checks passed.", Picks: []string{"Land", "Hold"}, Recommended: "Land", PRs: []string{landPR}})
	if err != nil || len(r.PRCards) != 1 || r.PRCards[0].Title != "relay: fix the reconnect" || r.PRCards[0].Risk != "One retry loop." {
		t.Fatalf("cards = %+v, err %v; want the title and risk from the PR body", r.PRCards, err)
	}
}

func TestAPRCardWithNothingInItsRowOrItsBodyIsRefused(t *testing.T) {
	fake := scripted()
	fake.Reply(`{"title":"relay: fix the reconnect","body":"Fixes the reconnect."}`, "gh", "pr", "view")
	f := fanout(t, fake)
	ledgerRow(t, f, store.ReviewRow{ReadyForTap: true})
	_, _, err := f.withPRCards(context.Background(), Reply{ID: "r1", Title: "Land the relay fix?", MD: "Checks passed.", Picks: []string{"Land", "Hold"}, Recommended: "Land", PRs: []string{landPR}})
	if err == nil || !strings.HasPrefix(err.Error(), "EMPTY PR CARD: "+landPR) {
		t.Fatalf("err = %v, want the empty card refused", err)
	}
}

func TestAMissingLineIsLeftOffThePRChild(t *testing.T) {
	fake := scripted()
	f := fanout(t, fake)
	ledgerRow(t, f, store.ReviewRow{ReadyForTap: true, ForUsers: "Chats load"})
	r, _, _ := f.withPRCards(context.Background(), Reply{ID: "r1", Title: "Land the relay fix?", MD: "Checks passed.", Picks: []string{"Land", "Hold"}, Recommended: "Land", PRs: []string{landPR}})
	f.Send(context.Background(), r)
	var card struct {
		Children []map[string]any `json:"children"`
	}
	if err := json.Unmarshal([]byte(calls(fake, "cc-present")[0].Stdin), &card); err != nil {
		t.Fatal(err)
	}
	pr := card.Children[len(card.Children)-1]
	summary := pr["summary"].(string)
	if _, risk := pr["risk"]; risk || pr["change"] != "Chats load" || !strings.HasPrefix(summary, "**What changes:** Chats load") || strings.Contains(summary, "**Risk:**") {
		t.Fatalf("pr child = %+v, want only the line it has", pr)
	}
}

func TestAPRCardCarriesItsLedgerRowToEverySurface(t *testing.T) {
	fake := scripted()
	f := fanout(t, fake)
	ledgerRow(t, f, store.ReviewRow{ReadyForTap: true, ForUsers: "Chats load", PlanRow: "Nothing differed", Risk: "- **Availability:** chats may reconnect late.", WaitsOn: "Your tap", Title: "Relay fix"})
	r, warning, _ := f.withPRCards(context.Background(), Reply{ID: "r1", Title: "Land the relay fix?", MD: "Checks passed.", Picks: []string{"Land", "Hold"}, Recommended: "Land", PRs: []string{landPR}})
	if warning != "" {
		t.Fatalf("warning = %q on a PR with every line", warning)
	}
	f.Send(context.Background(), r)
	var card struct {
		Children []map[string]any `json:"children"`
	}
	if err := json.Unmarshal([]byte(calls(fake, "cc-present")[0].Stdin), &card); err != nil {
		t.Fatal(err)
	}
	pr := card.Children[len(card.Children)-1]
	if pr["type"] != "pr" || pr["change"] != "Chats load" || !strings.Contains(pr["summary"].(string), "**Status:** CI ") || pr["review"] != nil || pr["description"] != nil || pr["blocker"] != "Your tap" || pr["title"] != "Relay fix" || !strings.Contains(pr["summary"].(string), "**Risk:** Chats may reconnect late.") {
		t.Fatalf("pr child = %+v, want its lines for Slack and for the board", pr)
	}
}

type ccPresentPR struct {
	ID      string          `json:"id"`
	Type    string          `json:"type"`
	Repo    string          `json:"repo"`
	Number  int             `json:"number"`
	Title   string          `json:"title,omitempty"`
	Summary string          `json:"summary,omitempty"`
	Files   []string        `json:"files,omitempty"`
	Head    string          `json:"head,omitempty"`
	Change  string          `json:"change,omitempty"`
	Plan    string          `json:"plan,omitempty"`
	Risk    string          `json:"risk,omitempty"`
	Blocker string          `json:"blocker,omitempty"`
	Row     json.RawMessage `json:"row,omitempty"`
}

func TestAPRChildDecodesUnderCCPresentsFieldsAndCarriesNoLedgerRow(t *testing.T) {
	child := Reply{PRCards: []prCard{{URL: landPR, Head: "abc1234", Title: "Relay fix", Review: "CI green", Lines: Lines{"Chats load", "Nothing differed", "Low", "Your tap"}}}}.prChild("r1", landPR)
	raw, err := json.Marshal(child)
	if err != nil {
		t.Fatal(err)
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	var pr ccPresentPR
	if err := decoder.Decode(&pr); err != nil {
		t.Fatalf("cc-present refuses %s: %v", raw, err)
	}
	if len(pr.Row) != 0 {
		t.Fatalf("child = %s, want no ledger row now cards draw from their lines", raw)
	}
}

func TestAnOrderNamesARowOrASubRow(t *testing.T) {
	for order, kept := range map[string]bool{"m703": true, "m703.2": true, "m703.2.1": false, "703": false} {
		got, _ := Reply{ID: "a", Title: "t", MD: "m", Order: order}.mended()
		if (got.Order == order) != kept {
			t.Errorf("order %q mended to %q, kept should be %v", order, got.Order, kept)
		}
	}
}

var inCI = store.ReviewRow{Title: "Relay fix", ForUsers: "Chats load", PlanRow: "Nothing differed", Risk: "Low", State: prrow.StateOpen, Hold: prrow.None, SupersededBy: prrow.None, CI: prrow.CIPending, WaitsOn: "CI is running or not read yet at this head"}

func TestAListedPRDrawsItsApproveInCIAndALinkOnlyWhenItCannotBeApproved(t *testing.T) {
	r := Reply{ID: "r2", Title: "The relay fix", MD: "m", Status: info, PRs: []string{landPR}}
	f := fanout(t, scripted())
	if _, warning, _ := f.withPRCards(context.Background(), r); !strings.HasPrefix(warning, "no row in the PR ledger, so no Approve and he sees a link: "+landPR) {
		t.Fatalf("no row warned %q", warning)
	}
	ledgerRow(t, f, inCI)
	if _, warning, _ := f.withPRCards(context.Background(), r); warning != "" {
		t.Fatalf("a PR in CI warned %q, want its Approve drawn with nothing to say", warning)
	}
	if flags := r.tapFlags(); !slices.Equal(flags, []string{"--tap-pr", landPR}) {
		t.Fatalf("tap flags = %q, want the PR in CI asked for its Approve", flags)
	}
	superseded := inCI
	superseded.SupersededBy = "#2197"
	ledgerRow(t, f, superseded)
	if _, warning, _ := f.withPRCards(context.Background(), r); warning != "" {
		t.Fatalf("a superseded PR warned %q, want a link and nothing to say", warning)
	}
}

func TestEveryOpenPRANoPickReplyNamesJoinsPRsForItsApprove(t *testing.T) {
	landed := inCI
	landed.State = prrow.StateLanded
	tapped := inCI
	tapped.Tapped = true
	named := Reply{ID: "r1", Title: "Stuck Working tab", MD: "[#2196](" + landPR + ") makes the dashboard reread once the stream catches up; it is in CI.", Status: info}
	for name, tc := range map[string]struct {
		row   store.ReviewRow
		reply Reply
		prs   []string
	}{
		"in CI, named in md":          {inCI, named, []string{landPR}},
		"named by its bare number":    {inCI, Reply{Title: "Relay", MD: "#2196 is in CI."}, []string{landPR}},
		"already listed":              {inCI, Reply{Title: "Relay", MD: "#2196 is in CI.", PRs: []string{landPR}}, []string{landPR}},
		"landed":                      {landed, named, nil},
		"already tapped":              {tapped, named, nil},
		"named only in a hidden cite": {inCI, Reply{Title: "Relay", MD: "It is fixed. <!-- #2196 -->"}, nil},
		"a question with picks":       {inCI, Reply{Title: "Which fix?", MD: "#2196 is in CI.", Picks: []string{"A", "B"}}, nil},
		"closed before its row read":  {inCI, Reply{Title: "Gate PR closed", MD: "Closing #2196 unlanded; the hold stays. Nothing to tap.\n\n- " + landPR}, nil},
		"said closed":                 {inCI, Reply{Title: "Relay", MD: "#2196 is closed."}, nil},
		"said withdrawn":              {inCI, Reply{Title: "Relay", MD: "[#2196](" + landPR + ") is withdrawn from your queue."}, nil},
		"said not landing":            {inCI, Reply{Title: "Relay", MD: "#2196 is not landing; the old tree keeps it."}, nil},
		"another PR said closed":      {inCI, Reply{Title: "Relay", MD: "#2196 is in CI. #2190 is closed."}, []string{landPR}},
	} {
		t.Run(name, func(t *testing.T) {
			f := fanout(t, scripted())
			ledgerRow(t, f, tc.row)
			r, warning := f.withNamedPRs(tc.reply)
			if !slices.Equal(r.PRs, tc.prs) || warning != "" {
				t.Fatalf("prs %q, warning %q; want %q", r.PRs, warning, tc.prs)
			}
		})
	}
}

func TestAPRNotWaitingIsOnlyAMentionAndANoPRReplyReadsNothing(t *testing.T) {
	fake := scripted()
	f := fanout(t, fake)
	ledgerRow(t, f, store.ReviewRow{ReadyForTap: true, Tapped: true, ForUsers: "Chats load", PlanRow: "Nothing differed", Risk: "Low"})
	mention, warning, _ := f.withPRCards(context.Background(), Reply{ID: "r2", Title: "The relay fix landed", MD: "m", Status: "done", PRs: []string{landPR}})
	if warning != "" || len(mention.PRCards) != 0 {
		t.Fatalf("withPRCards = %+v, %q, want a PR nothing waits on left a plain mention", mention.PRCards, warning)
	}
	if plain, warning, _ := f.withPRCards(context.Background(), Reply{ID: "r3", Title: "t", MD: "m"}); warning != "" || plain.PRCards != nil {
		t.Fatalf("a reply naming no PR read the store: %+v, %q", plain, warning)
	}
	unrowed, warning, _ := f.withPRCards(context.Background(), Reply{ID: "r4", Title: "Land it?", MD: "m", Picks: []string{"Land", "Hold"}, Recommended: "Land", PRs: []string{landPR + "0"}})
	if !strings.Contains(warning, "no row in the PR ledger") || len(unrowed.PRCards) != 1 || unrowed.PRCards[0].Change != "" {
		t.Fatalf("withPRCards = %+v, %q, want a PR with no ledger row warned on and drawn bare", unrowed.PRCards, warning)
	}
}

func TestAReplyNamesARegisteredNumberedLaneByItsAliasOnEverySurface(t *testing.T) {
	fake := scripted()
	f := fanout(t, fake)
	s, err := store.Open(f.Entry.Store())
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.WantLane(store.Lane{Name: "tap-rows-1", Brief: "/b/tap.md", Role: "lane", State: store.LaneDesired}, sentAt); err != nil {
		t.Fatal(err)
	}
	if err := s.Close(); err != nil {
		t.Fatal(err)
	}

	sent := f.Send(context.Background(), Reply{
		ID: "r9", Title: "tap-rows-1 is done", MD: "Ask tap-rows-1. See <https://example.com/tap-rows-1> and tap-rows-9.", Status: info,
		Picks: []string{"Keep tap-rows-1", "Stop tap-rows-1 now"}, Recommended: "Keep tap-rows-1",
	})

	reply, _ := stored(t, f, "r9")
	if reply.Title != "tap-rows is done" || reply.MD != "Ask tap-rows. See <https://example.com/tap-rows-1> and tap-rows-9." {
		t.Fatalf("title %q, md %q", reply.Title, reply.MD)
	}
	if !slices.Equal(reply.Picks, []string{"Keep tap-rows", "Stop tap-rows now"}) || reply.Recommended != "Keep tap-rows" {
		t.Fatalf("picks %q, recommended %q", reply.Picks, reply.Recommended)
	}
	for _, o := range sent {
		if o.Error != "" {
			t.Fatalf("%s: %s", o.Surface, o.Error)
		}
	}
	if post := calls(fake, "cci"); len(post) != 1 || strings.Contains(post[0].Stdin, "Ask tap-rows-1") {
		t.Fatalf("record %v", post)
	}
}

func TestPicksTheAliasWouldMakeIdenticalKeepTheirNumberedNames(t *testing.T) {
	f := fanout(t, scripted())
	s, err := store.Open(f.Entry.Store())
	if err != nil {
		t.Fatal(err)
	}
	if err := s.PutLanes([]store.Lane{{Name: "desk-1", Brief: "/b/1.md", State: store.LaneRetired}, {Name: "desk-2", Brief: "/b/2.md", State: store.LaneDesired}}, sentAt); err != nil {
		t.Fatal(err)
	}
	if err := s.Close(); err != nil {
		t.Fatal(err)
	}

	f.Send(context.Background(), Reply{ID: "r10", Title: "Which?", MD: "Which?", Picks: []string{"Keep desk-1", "Keep desk-2"}, Recommended: "Keep desk-2"})

	if reply, _ := stored(t, f, "r10"); !slices.Equal(reply.Picks, []string{"Keep desk-1", "Keep desk-2"}) || reply.Recommended != "Keep desk-2" {
		t.Fatalf("picks %q, recommended %q", reply.Picks, reply.Recommended)
	}
}

func TestAStoreThatDoesNotOpenPostsTheReplyAsWritten(t *testing.T) {
	f := fanout(t, scripted())
	if err := os.Mkdir(f.Entry.Store(), 0o750); err != nil {
		t.Fatal(err)
	}

	f.Send(context.Background(), Reply{ID: "r11", Title: "tap-rows-1", MD: "tap-rows-1"})

	if reply, _ := stored(t, f, "r11"); reply.MD != "tap-rows-1" {
		t.Fatalf("md %q", reply.MD)
	}
}

func TestEachReplyStoresTheTypeItsContentGivesUnlessItNamesOne(t *testing.T) {
	cases := []struct {
		name string
		in   Reply
		want string
	}{
		{"plain", Reply{}, typeExplanation},
		{"info", Reply{Status: info}, typeExplanation},
		{"progress", Reply{Status: progress}, typeUpdate},
		{"needs him", Reply{Status: needsYou}, typeForYou},
		{"incident", Reply{Status: incident}, incident},
		{"done", Reply{Status: done}, done},
		{"picks win over status", Reply{Status: needsYou, Picks: []string{"Yes", "No"}}, typeQuestion},
		{"named wins over content", Reply{Status: progress, Type: done}, done},
	}
	for _, c := range cases {
		fake := scripted()
		f := fanout(t, fake)
		c.in.ID, c.in.Title, c.in.MD = "typed", "A title", "The words"
		f.Send(context.Background(), c.in)
		if got, _ := stored(t, f, "typed"); got.Type != c.want {
			t.Errorf("%s: stored type %q, want %q", c.name, got.Type, c.want)
		}
	}
}

func TestATypeOutsideTheListIsLeftOffSoTheContentDecides(t *testing.T) {
	got, warned := Reply{ID: "a", Title: "t", MD: "m", Status: done, Type: "memo"}.mended()
	if got.Type != "" || len(warned) != 1 || !strings.Contains(warned[0], `type "memo"`) {
		t.Fatalf("got type %q, warned %v", got.Type, warned)
	}
}

const drawnDiagram = `{"arch":1,"name":"relay","title":"The relay","nodes":[{"id":"a","kind":"service","name":"A","role":"sends"},{"id":"b","kind":"service","name":"B","role":"takes"}],"edges":[{"from":"a","to":"b"}]}`

const (
	cardMessage = "https://brook.slack.com/archives/D0B6Z/p1793742129719979"
	evictionAsk = "Where should storage eviction start?"
)

func boardWith(answers string) string {
	return `{"doc":{"blocks":[` +
		`{"id":"card-storage-eviction","type":"card","title":"` + evictionAsk + `","slack":{"url":"` + cardMessage + `"},"children":[{"id":"evict-pick","type":"choice"}]},` +
		`{"id":"card-storage-eviction-window","type":"card","title":"How long is the window?","slack":{"url":"https://brook.slack.com/archives/D0B6Z/p2"},"children":[{"id":"window-pick","type":"choice"}]},` +
		`{"id":"card-never-sent","type":"card","title":"Never reached Slack","children":[{"id":"unsent-pick","type":"choice"}]}` +
		`]},"interactions":{"choices":` + answers + `}}`
}

func linkedMD(t *testing.T, r Reply, answers string) string {
	t.Helper()
	fake := &shelltest.Fake{}
	fake.Reply(boardWith(answers), "cc-present", "outcomes")
	fake.Reply(`[]`, "ccn")
	got, warning := fanout(t, fake).withCardLinks(context.Background(), r)
	if warning != "" {
		t.Fatalf("warning = %q", warning)
	}
	return got.MD
}

func TestAnOpenCardTheReplyNamesLinksByItsTypedKey(t *testing.T) {
	typed := "(card:card-storage-eviction)"
	linked := "[" + evictionAsk + "]" + typed
	for name, tc := range map[string]struct{ md, want string }{
		"its id":           {"Waiting on card-storage-eviction now.", "Waiting on " + linked + " now."},
		"its id in code":   {"Waiting on `card-storage-eviction` now.", "Waiting on " + linked + " now."},
		"its words":        {"Card storage eviction if it needs you.", "[Card storage eviction]" + typed + " if it needs you."},
		"its words after":  {"The storage eviction card needs you.", "The [storage eviction card]" + typed + " needs you."},
		"its title":        {"Asked: where should storage eviction start?", "Asked: [where should storage eviction start?]" + typed},
		"a longer name":    {"Waiting on card-storage-eviction-window.", "Waiting on [How long is the window?](card:card-storage-eviction-window)."},
		"its Slack link":   {"See [the card](" + cardMessage + ") for storage eviction card.", "See [the card]" + typed + " for storage eviction card."},
		"already typed":    {"See [the card]" + typed + " for storage eviction card.", "See [the card]" + typed + " for storage eviction card."},
		"no card":          {"Storage eviction merged.", "Storage eviction merged."},
		"a card off Slack": {"Waiting on card-never-sent.", "Waiting on card-never-sent."},
	} {
		t.Run(name, func(t *testing.T) {
			if got := linkedMD(t, Reply{ID: "r1", MD: tc.md}, "{}"); got != tc.want {
				t.Fatalf("md = %q, want %q", got, tc.want)
			}
		})
	}
}

func TestACardLinkIsTheSlackMessageOnlyInSlack(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply(boardWith("{}"), "cc-present", "outcomes")
	fake.Reply(`{"permalink":"https://brook.slack.com/archives/D0B6Z/p3"}`, "cc-slack", "card-copy")
	scriptedOver(fake)
	f := fanout(t, fake)
	r, warning := f.withCardLinks(context.Background(), Reply{ID: "r1", Title: "Where things stand", MD: "Waiting on card-storage-eviction now.", Status: "info"})
	if warning != "" {
		t.Fatal(warning)
	}
	sent := f.Send(context.Background(), r)
	var card string
	for _, call := range calls(fake, "cc-slack") {
		if call.Argv[1] == "dm-status" {
			card = call.Argv[3]
		}
	}
	if !strings.Contains(card, "]("+cardMessage+")") || strings.Contains(card, "card:") {
		t.Fatalf("Slack card = %s, want the card linked to its Slack message", card)
	}
	stored, err := os.ReadFile(surface(sent, "dashboard").Ref)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(stored), "(card:card-storage-eviction)") || strings.Contains(string(stored), cardMessage) {
		t.Fatalf("dashboard copy = %s, want the typed link and no Slack address", stored)
	}
}

func TestAnUnknownCardLinkDrawsItsWordsInSlack(t *testing.T) {
	r := Reply{OpenCards: []board.OpenCard{{ID: "card-a", Title: "Card A", URL: cardMessage}}}
	if got := r.inSlack("[the gone one](card:card-b) and [](card:card-a)"); got != "the gone one and [Card A]("+cardMessage+")" {
		t.Fatalf("inSlack = %q", got)
	}
}

func TestACardHeAnsweredOrThisReplyDrawsTakesNoLink(t *testing.T) {
	md := "Waiting on card-storage-eviction."
	if got := linkedMD(t, Reply{ID: "r1", MD: md}, `{"evict-pick":{"optionIds":["a"]}}`); got != md {
		t.Fatalf("an answered card was linked: %q", got)
	}
	if got := linkedMD(t, Reply{ID: "storage-eviction", MD: md}, "{}"); got != md {
		t.Fatalf("a card was linked to itself: %q", got)
	}
}

func TestABoardThatDoesNotReadPostsTheReplyWithAWarning(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Fail(1, "daemon not running", "cc-present", "outcomes")
	r, warning := fanout(t, fake).withCardLinks(context.Background(), Reply{ID: "r1", MD: "card-storage-eviction"})
	if r.MD != "card-storage-eviction" || !strings.Contains(warning, "daemon not running") {
		t.Fatalf("md = %q, warning = %q", r.MD, warning)
	}
}

func TestAnInfoReplyWritesItsClockAndRoutedSeqToTheLedgerRow(t *testing.T) {
	fake := scripted()
	f := fanout(t, fake)
	path := ledgered(t, f)
	r, warned := Reply{ID: "ri", Title: "Vector search", MD: "It went to the design lane.", Status: info, Order: "m447", By: "11:30 PM", Routed: 400803, Thread: "1793684147.131059"}.mended()
	if len(warned) != 0 {
		t.Fatalf("warned %q", warned)
	}
	sent := f.Send(context.Background(), r)
	if got := surface(sent, "ledger"); got.Ref != "m447 due Nov 3 11:30 PM" {
		t.Fatalf("ledger outcome = %+v", got)
	}
	if ledger, _ := os.ReadFile(path); !strings.Contains(string(ledger), "due Nov 3 11:30 PM") {
		t.Fatalf("ledger = %q", ledger)
	}
}

func TestAProgressReplyInHisThreadAnswersThereAndKeepsTheLedgerRow(t *testing.T) {
	fake := scripted()
	f := fanout(t, fake)
	path := ledgered(t, f)
	sent := f.Send(context.Background(), Reply{ID: "rp", Title: "Vector search", MD: "It went to [the design lane](https://x.test/d).", Status: progress, Order: "m447", By: "11:30 PM", Routed: 400803, Thread: "1793684147.131059"}.led())
	posted := calls(fake, "cc-slack")
	if len(posted) != 1 || flag(t, posted[0].Argv, "--thread") != "1793684147.131059" {
		t.Fatalf("cc-slack calls = %+v, want one post in his thread", posted)
	}
	if text := posted[0].Argv[len(posted[0].Argv)-1]; text != "*Vector search*\nIt went to <https://x.test/d|the design lane>." {
		t.Fatalf("thread text = %q", text)
	}
	if got := surface(sent, "ledger"); got.Ref != "m447 due Nov 3 11:30 PM" {
		t.Fatalf("ledger outcome = %+v", got)
	}
	if ledger, _ := os.ReadFile(path); !strings.Contains(string(ledger), "due Nov 3 11:30 PM") {
		t.Fatalf("ledger = %q", ledger)
	}
	if len(threadWrites(fake)) != 0 {
		t.Fatalf("a progress reply reached Talk: %+v", threadWrites(fake))
	}
}

func TestAPickReplyInHisThreadPutsAWorkingCopyOfItsCardThere(t *testing.T) {
	fake := scripted()
	fake.Reply(`{"permalink":"https://brook.slack.com/archives/D0B6Z/p3"}`, "cc-slack", "card-copy")
	sent := fanout(t, fake).Send(context.Background(), Reply{ID: "r9", Title: "Land the relay fix?", MD: "Its checks passed.", Picks: []string{"Land", "Hold"}, Recommended: "Land", Thread: "1793684147.131059"})
	posted := calls(fake, "cc-slack")
	if len(posted) != 1 || !slices.Equal(posted[0].Argv, []string{"cc-slack", "card-copy", "--url", permalink, "--thread", "1793684147.131059"}) {
		t.Fatalf("cc-slack calls = %+v, want only the card copied into his thread, never its text", posted)
	}
	if surface(sent, "slack").Ref != permalink || surface(sent, "card copy").Ref == "" || surface(sent, "slack thread").Surface != "" {
		t.Fatalf("outcomes = %+v, want the card and its copy", sent)
	}
}

func TestACopyThatCannotPostPostsNothingInItsPlaceAndReportsWhy(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Fail(1, "that card has no answer left to tap", "cc-slack", "card-copy")
	sent := fanout(t, scriptedOver(fake)).Send(context.Background(), Reply{ID: "r9", Title: "Land the relay fix?", MD: "**Its** checks passed.", Picks: []string{"Land", "Hold"}, Recommended: "Land", Thread: "1793684147.131059"})

	if posted := calls(fake, "cc-slack"); len(posted) != 1 || posted[0].Argv[1] != "card-copy" {
		t.Fatalf("cc-slack calls = %+v, want the copy alone and no link asking him to answer a card he cannot", posted)
	}
	if copied := surface(sent, "card copy"); !strings.Contains(copied.Error, "no answer left") || copied.Ref != "" {
		t.Fatalf("card copy = %+v, want the failure and nothing posted", copied)
	}
}

func TestAnEditThatDropsPicksInHisThreadStaysOnTheCard(t *testing.T) {
	fake := scripted()
	fake.Reply("removed card-r10\nrev: 9\n", "cc-present", "remove-block")
	f := fanout(t, fake)
	f.Send(context.Background(), Reply{ID: "r10", Title: "Land it?", MD: "Checks passed.", Picks: []string{"Land", "Hold"}, Recommended: "Land"})
	before := len(calls(fake, "cc-slack"))
	f.Send(context.Background(), Reply{ID: "r10", Title: "Landed", MD: "Withdrawn.", Thread: "1793684147.131059"})
	after := calls(fake, "cc-slack")[before:]
	if len(after) != 1 || slices.Contains(after[0].Argv, "--thread") {
		t.Fatalf("cc-slack calls = %+v, want the card edited in place and no thread post", after)
	}
}

const (
	hisThread   = "1793804574.412309"
	threadCopy  = "https://brook.slack.com/archives/D0B6Z/p1793820906093619?thread_ts=" + hisThread
	cardCopy    = "https://brook.slack.com/archives/D0B6Z/p1793820000000100"
	missingEdit = "message_not_found"
)

func postedInThread(fake *shelltest.Fake, ref string) {
	fake.Reply(`{"channel_id":"D0B6Z","thread_ts":"`+hisThread+`","ts":"1793820906.093619","permalink":"`+ref+`"}`, "cc-slack", "dm-status")
	fake.Reply(posted, "cci", "post")
	fake.Reply(`{"doc":{}}`, "cc-present", "outcomes")
}

func threadedFake() *shelltest.Fake {
	fake := &shelltest.Fake{}
	fake.Reply(`{"permalink":"https://brook.slack.com/archives/D0B6Z/p3"}`, "cc-slack", "card-copy")
	fake.Reply("updated card\nsent: card ts=1793820000.000100 "+cardCopy+"\n", "cc-present", "update-block")
	fake.Reply("removed\nrev: 9\n", "cc-present", "remove-block")
	postedInThread(fake, threadCopy)
	return fake
}

func TestAThreadedReplyWithItsIDEditsItsMessageAndPostsNothing(t *testing.T) {
	fake := threadedFake()
	f := fanout(t, fake)
	f.Send(context.Background(), Reply{ID: "r1", Title: "205 s vs 28 s", MD: "Both numbers are right.", Thread: hisThread})
	sent := f.Send(context.Background(), Reply{ID: "r1", Title: "Done: 205 s vs 28 s", MD: "A run, then a pause.", Status: info, Order: "m671", Thread: hisThread})

	posted := calls(fake, "cc-slack")
	if len(posted) != 2 || flag(t, posted[0].Argv, "--thread") != hisThread {
		t.Fatalf("cc-slack calls = %+v, want one thread post and one edit", posted)
	}
	if slices.Contains(posted[1].Argv, "--thread") {
		t.Fatalf("the edit %q posted under the thread", posted[1].Argv)
	}
	edit := slack(t, posted[1])
	if edit.Replaces != threadCopy || edit.Key != "" || edit.Title != "Done: 205 s vs 28 s" || edit.Body != "A run, then a pause." {
		t.Fatalf("edit = %+v, want the new words on %s", edit, threadCopy)
	}
	if got := surface(sent, "slack"); got != (Outcome{Surface: "slack", Ref: threadCopy}) {
		t.Fatalf("slack outcome = %+v, want the same message", got)
	}
}

func TestAnEditThatNamesNoThreadStillEditsTheThreadedMessage(t *testing.T) {
	fake := threadedFake()
	f := fanout(t, fake)
	f.Send(context.Background(), Reply{ID: "r1", Title: "t", MD: "first", Thread: hisThread})
	f.Send(context.Background(), Reply{ID: "r1", Title: "t", MD: "second"})

	posted := calls(fake, "cc-slack")
	if len(posted) != 2 || slack(t, posted[1]).Replaces != threadCopy {
		t.Fatalf("cc-slack calls = %+v, want the edit on %s", posted, threadCopy)
	}
}

func TestARootReplyAnsweredInAThreadLaterEditsAtTheRoot(t *testing.T) {
	fake := scripted()
	f := fanout(t, fake)
	f.Send(context.Background(), Reply{ID: "r2", Title: "t", MD: "first", Status: info})
	f.Send(context.Background(), Reply{ID: "r2", Title: "t", MD: "second", Status: info, Order: "m9", Thread: hisThread})

	posted := calls(fake, "cc-slack")
	if len(posted) != 2 || slices.Contains(posted[1].Argv, "--thread") || slack(t, posted[1]).Replaces != permalink {
		t.Fatalf("cc-slack calls = %+v, want the root card edited and nothing in the thread", posted)
	}
}

func TestAnIDWithNoPostedMessagePostsOnce(t *testing.T) {
	for name, r := range map[string]Reply{
		"thread": {ID: "fresh", Title: "t", MD: "m", Thread: hisThread},
		"root":   {ID: "fresh", Title: "t", MD: "m", Status: info},
	} {
		t.Run(name, func(t *testing.T) {
			fake := scripted()
			sent := fanout(t, fake).Send(context.Background(), r)
			posted := calls(fake, "cc-slack")
			if len(posted) != 1 || surface(sent, "slack").Ref != permalink {
				t.Fatalf("cc-slack calls = %+v and outcomes %+v, want one post", posted, sent)
			}
			if r.Thread == "" && slack(t, posted[0]).Replaces != "" {
				t.Fatalf("the first post %+v replaces a message that does not exist", slack(t, posted[0]))
			}
		})
	}
}

func TestAFailedEditSaysSoPostsNothingAndTheNextEditStillFindsTheMessage(t *testing.T) {
	healthy := threadedFake()
	f := fanout(t, healthy)
	f.Send(context.Background(), Reply{ID: "r1", Title: "t", MD: "first", Thread: hisThread})

	failing := &shelltest.Fake{}
	failing.Fail(1, missingEdit, "cc-slack", "dm-status")
	postedInThread(failing, threadCopy)
	f.Runner = failing
	sent := f.Send(context.Background(), Reply{ID: "r1", Title: "t", MD: "second", Thread: hisThread})

	got := surface(sent, "slack")
	if !strings.Contains(got.Error, missingEdit) || !strings.Contains(got.Error, "posted nothing new") || got.Ref != threadCopy {
		t.Fatalf("slack outcome = %+v, want the failure named and the message kept", got)
	}
	tried := calls(failing, "cc-slack")
	if len(tried) != 1 || slices.Contains(tried[0].Argv, "--thread") {
		t.Fatalf("cc-slack calls = %+v, want one edit attempt and no post", tried)
	}
	if !strings.Contains(receipt(Reply{ID: "r1", MD: "m"}, sent), "slack FAILED: did not edit") {
		t.Fatalf("receipt does not tell the lane: %s", receipt(Reply{ID: "r1", MD: "m"}, sent))
	}

	f.Runner = healthy
	f.Send(context.Background(), Reply{ID: "r1", Title: "t", MD: "third", Thread: hisThread})
	if last := calls(healthy, "cc-slack"); len(last) != 2 || slack(t, last[1]).Replaces != threadCopy {
		t.Fatalf("cc-slack calls = %+v, want the retry on the first message", last)
	}
}

func TestAProgressReplyWithItsIDEditsTheMessageItPosted(t *testing.T) {
	fake := scripted()
	f := fanout(t, fake)
	ledgered(t, f)
	f.Send(context.Background(), Reply{ID: "r4", Title: "t", MD: "first", Status: info})
	sent := f.Send(context.Background(), Reply{ID: "r4", Title: "t", MD: "under way", Status: progress, Order: "m447"})

	posted := calls(fake, "cc-slack")
	if len(posted) != 2 || slack(t, posted[1]).Replaces != permalink {
		t.Fatalf("cc-slack calls = %+v, want the message edited", posted)
	}
	if surface(sent, "ledger").Surface == "" {
		t.Fatalf("outcomes = %+v, want the ledger row still written", sent)
	}
}

func TestAProgressReplyWithNoMessageAndNoThreadPostsAtHisDMRoot(t *testing.T) {
	fake := scripted()
	f := fanout(t, fake)
	ledgered(t, f)
	f.Send(context.Background(), Reply{ID: "r5", Title: "t", MD: "under way", Status: progress, Order: "m447"})
	if posted := calls(fake, "cc-slack"); len(posted) != 1 {
		t.Fatalf("cc-slack calls = %+v, want one", posted)
	}
}

func TestAThreadReplyTurnsMarkdownBoldIntoSlackBold(t *testing.T) {
	fake := threadedFake()
	f := fanout(t, fake)
	f.Send(context.Background(), Reply{ID: "r7", Title: "t", MD: "**Fixed.** It ships tonight.", Status: info, Thread: hisThread})
	posted := calls(fake, "cc-slack")
	if len(posted) == 0 || !strings.Contains(strings.Join(posted[0].Argv, " "), "*Fixed.* It ships tonight.") || strings.Contains(strings.Join(posted[0].Argv, " "), "**") {
		t.Fatalf("cc-slack calls = %+v, want Slack bold", posted)
	}
}

func TestAPickReplyInHisThreadEditsOnlyItsCardAcrossEdits(t *testing.T) {
	fake := threadedFake()
	f := fanout(t, fake)
	ask := Reply{ID: "r6", Title: "Land?", MD: "Checks passed.", Picks: []string{"Land", "Hold"}, Recommended: "Land", Thread: hisThread}
	f.Send(context.Background(), ask)
	ask.MD = "Checks passed again."
	sent := f.Send(context.Background(), ask)

	posted := calls(fake, "cc-slack")
	if len(posted) != 1 || posted[0].Argv[1] != "card-copy" {
		t.Fatalf("cc-slack calls = %+v, want one copy of the card and no thread post", posted)
	}
	if surface(sent, "slack").Ref != cardCopy || surface(sent, "slack thread").Surface != "" {
		t.Fatalf("outcomes = %+v, want the card alone", sent)
	}

	f.Send(context.Background(), Reply{ID: "r6", Title: "Landed", MD: "It merged.", Thread: hisThread})
	posted = calls(fake, "cc-slack")[1:]
	if len(posted) != 1 || slack(t, posted[0]).Replaces != cardCopy {
		t.Fatalf("cc-slack calls = %+v, want the card edited and nothing posted", posted)
	}
}

func replacedBy(t *testing.T, c shell.Command) string {
	t.Helper()
	var card struct {
		Replaces string `json:"replaces"`
	}
	if err := json.Unmarshal([]byte(c.Stdin), &card); err != nil {
		t.Fatal(err)
	}
	return card.Replaces
}

func TestAMessageThatGainsPicksBecomesItsCardAndPostsNothing(t *testing.T) {
	fake := threadedFake()
	f := fanout(t, fake)
	f.Send(context.Background(), Reply{ID: "r8", Title: "t", MD: "first", Thread: hisThread})
	ask := Reply{ID: "r8", Title: "Land?", MD: "Pick one.", Picks: []string{"Land", "Hold"}, Recommended: "Land", Thread: hisThread}
	sent := f.Send(context.Background(), ask)

	drawn := calls(fake, "cc-present")
	if len(drawn) != 1 || replacedBy(t, drawn[0]) != threadCopy {
		t.Fatalf("board writes = %+v, want the card to replace %s", drawn, threadCopy)
	}
	if posted := calls(fake, "cc-slack"); len(posted) != 1 {
		t.Fatalf("cc-slack calls = %+v, want only the first post", posted)
	}
	if surface(sent, "slack").Ref != cardCopy || surface(sent, "slack thread").Surface != "" {
		t.Fatalf("outcomes = %+v, want the one card and no thread copy", sent)
	}

	ask.MD = "Pick one, please."
	f.Send(context.Background(), ask)
	if drawn = calls(fake, "cc-present"); len(drawn) != 2 || replacedBy(t, drawn[1]) != cardCopy {
		t.Fatalf("board writes = %+v, want the next edit to stay on %s", drawn, cardCopy)
	}
	if posted := calls(fake, "cc-slack"); len(posted) != 1 {
		t.Fatalf("cc-slack calls = %+v, want nothing new on the next edit", posted)
	}
}

func TestANewPickReplyReplacesNothing(t *testing.T) {
	fake := threadedFake()
	fanout(t, fake).Send(context.Background(), Reply{ID: "r17", Title: "Land?", MD: "Pick one.", Picks: []string{"Land", "Hold"}, Recommended: "Land"})
	if got := replacedBy(t, calls(fake, "cc-present")[0]); got != "" {
		t.Fatalf("a new card replaces %q", got)
	}
}

func TestAWithdrawnCardAskedAgainLeavesTheRedrawnCardItsButtons(t *testing.T) {
	fake := threadedFake()
	f := fanout(t, fake)
	ask := Reply{ID: "r9", Title: "Land?", MD: "Checks passed.", Picks: []string{"Land", "Hold"}, Recommended: "Land"}
	f.Send(context.Background(), ask)
	f.Send(context.Background(), Reply{ID: "r9", Title: "Held", MD: "Held for now."})
	before := len(calls(fake, "cc-slack"))
	sent := f.Send(context.Background(), ask)

	if after := calls(fake, "cc-slack")[before:]; len(after) != 0 {
		t.Fatalf("cc-slack calls = %+v, want cc-present alone to redraw the card, since a replacing card has no buttons", after)
	}
	if surface(sent, "slack").Ref != cardCopy {
		t.Fatalf("outcomes = %+v, want the redrawn card", sent)
	}
}

func TestACardThatNeverReachedSlackPostsOnceWhenEdited(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Fail(1, "daemon not running", "cc-present", "update-block")
	fake.Reply("removed\nrev: 9\n", "cc-present", "remove-block")
	postedInThread(fake, threadCopy)
	f := fanout(t, fake)
	f.Send(context.Background(), Reply{ID: "r11", Title: "Land?", MD: "Checks passed.", Picks: []string{"Land", "Hold"}, Recommended: "Land"})
	sent := f.Send(context.Background(), Reply{ID: "r11", Title: "Landed", MD: "It merged.", Thread: hisThread})

	posted := calls(fake, "cc-slack")
	if len(posted) != 1 || flag(t, posted[0].Argv, "--thread") != hisThread || surface(sent, "slack").Ref != threadCopy {
		t.Fatalf("cc-slack calls = %+v and outcomes %+v, want one post", posted, sent)
	}
}

func TestAFailedBoardUpdateKeepsTheCardItAlreadyPosted(t *testing.T) {
	fake := threadedFake()
	f := fanout(t, fake)
	ask := Reply{ID: "r12", Title: "Land?", MD: "Checks passed.", Picks: []string{"Land", "Hold"}, Recommended: "Land"}
	f.Send(context.Background(), ask)

	failing := &shelltest.Fake{}
	failing.Fail(1, "daemon not running", "cc-present", "update-block")
	postedInThread(failing, threadCopy)
	f.Runner = failing
	f.Send(context.Background(), ask)

	f.Runner = fake
	f.Send(context.Background(), Reply{ID: "r12", Title: "Landed", MD: "It merged."})
	if posted := calls(fake, "cc-slack"); len(posted) != 1 || slack(t, posted[0]).Replaces != cardCopy {
		t.Fatalf("cc-slack calls = %+v, want the first card edited", posted)
	}
}

func TestAnEditOfACardHeAnsweredSaysWhySlackKeepsWhatHeAnswered(t *testing.T) {
	fake := threadedFake()
	f := fanout(t, fake)
	ask := Reply{ID: "r13", Title: "Land?", MD: "Checks passed.", Picks: []string{"Land", "Hold"}, Recommended: "Land"}
	f.Send(context.Background(), ask)

	answered := &shelltest.Fake{}
	answered.Reply("updated card\n", "cc-present", "update-block")
	postedInThread(answered, threadCopy)
	f.Runner = answered
	ask.MD = "Checks passed, and a bug is new."
	got := surface(f.Send(context.Background(), ask), "slack")

	if got.Ref != cardCopy || !strings.Contains(got.Error, "already answered") {
		t.Fatalf("slack outcome = %+v, want the posted card kept and the reason named", got)
	}
	if posted := calls(answered, "cc-slack"); len(posted) != 0 {
		t.Fatalf("cc-slack calls = %+v, want Slack left as he answered it", posted)
	}
}

func TestAnEditOfACardNeverStartsAThreadCopy(t *testing.T) {
	fake := threadedFake()
	f := fanout(t, fake)
	ask := Reply{ID: "r14", Title: "Land?", MD: "Checks passed.", Picks: []string{"Land", "Hold"}, Recommended: "Land"}
	f.Send(context.Background(), ask)

	ask.MD, ask.Thread = "Checks passed again.", hisThread
	sent := f.Send(context.Background(), ask)

	if posted := calls(fake, "cc-slack"); len(posted) != 0 {
		t.Fatalf("cc-slack calls = %+v, want the card redrawn by cc-present and nothing posted", posted)
	}
	if surface(sent, "slack").Ref != cardCopy {
		t.Fatalf("outcomes = %+v, want the card", sent)
	}
}

func TestAFailedBoardAndTalkOnAnEditFallThroughToNoPost(t *testing.T) {
	for name, first := range map[string]Reply{
		"card alone":              {ID: "r15", Title: "Land?", MD: "Checks passed.", Picks: []string{"Land", "Hold"}, Recommended: "Land"},
		"card with a thread post": {ID: "r15", Title: "Land?", MD: "Checks passed.", Picks: []string{"Land", "Hold"}, Recommended: "Land", Thread: hisThread},
	} {
		t.Run(name, func(t *testing.T) {
			f := fanout(t, threadedFake())
			f.Send(context.Background(), first)

			failing := &shelltest.Fake{}
			failing.Fail(1, `decode pr block: json: unknown field "description"`, "cc-present", "update-block")
			postedInThread(failing, threadCopy)
			f.Runner = failing
			edit := first
			edit.MD, edit.Thread = "Checks passed again.", hisThread
			sent := f.Send(context.Background(), edit)

			if got := surface(sent, "board"); !strings.Contains(got.Error, "unknown field") {
				t.Fatalf("board outcome = %+v, want the failure returned to the caller", got)
			}
			if got := surface(sent, "talk"); !strings.Contains(got.Error, "unknown field") {
				t.Fatalf("talk outcome = %+v, want the failure returned to the caller", got)
			}
			for _, c := range calls(failing, "cc-slack") {
				if slices.Contains(c.Argv, "--thread") || slack(t, c).Replaces != threadCopy {
					t.Fatalf("cc-slack call %q created a message", c.Argv)
				}
			}
		})
	}
}

func TestAPickReplyWhoseBoardFailedPostsNoButtonlessCopyInHisThread(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Fail(1, "daemon not running", "cc-present", "update-block")
	postedInThread(fake, threadCopy)
	sent := fanout(t, fake).Send(context.Background(), Reply{ID: "r16", Title: "Land?", MD: "Checks passed.", Picks: []string{"Land", "Hold"}, Recommended: "Land", Thread: hisThread})

	if posted := calls(fake, "cc-slack"); len(posted) != 0 {
		t.Fatalf("cc-slack calls = %+v, want no copy without its buttons", posted)
	}
	if got := surface(sent, "board"); !strings.Contains(got.Error, "daemon not running") {
		t.Fatalf("board outcome = %+v, want the failure returned to the caller", got)
	}
}

func TestAQuestionNamingAPRHeCanApproveWithoutListingItWarns(t *testing.T) {
	ready := store.ReviewRow{ReadyForTap: true}
	ask := []string{"Hold", "Go"}
	for name, tc := range map[string]struct {
		row   store.ReviewRow
		reply Reply
		warns bool
	}{
		"by number":                          {ready, Reply{MD: "#2196 is ready.", Picks: ask}, true},
		"in the title":                       {ready, Reply{Title: "PR 2196 is ready", MD: "It passed.", Picks: ask}, true},
		"by its repo":                        {ready, Reply{MD: "cc-skills#2196 is ready.", Picks: ask}, true},
		"by a link in the text":              {ready, Reply{MD: "<https://github.com/YASYF/cc-skills/pull/2196|#2196> is ready.", Picks: ask}, true},
		"in CI":                              {inCI, Reply{MD: "Hold for #2196?", Picks: ask}, true},
		"listed in prs":                      {ready, Reply{MD: "#2196 is ready.", PRs: []string{landPR}, Picks: ask}, false},
		"with no picks, which lists it":      {ready, Reply{MD: "#2196 is ready."}, false},
		"tapped already, with no picks":      {store.ReviewRow{ReadyForTap: true, Tapped: true}, Reply{MD: "#2196 is ready."}, false},
		"one he cannot approve":              {store.ReviewRow{WaitsOn: "CI"}, Reply{MD: "Hold for #2196?", Picks: ask}, false},
		"another repo's PR with that number": {ready, Reply{MD: "monorepo#2196 is ready.", Picks: ask}, false},
		"another repo's link":                {ready, Reply{MD: "https://github.com/yasyf/cc-slack/pull/2196 is ready.", Picks: ask}, false},
		"a number in no ledger row":          {ready, Reply{MD: "#50026 is ready.", Picks: ask}, false},
		"a URL fragment":                     {ready, Reply{MD: "See https://example.com/#2196 for why.", Picks: ask}, false},
		"no PR":                              {ready, Reply{MD: "All quiet.", Picks: ask}, false},
	} {
		t.Run(name, func(t *testing.T) {
			f := fanout(t, scripted())
			ledgerRow(t, f, tc.row)
			warning := f.tapWarning(tc.reply)
			if tc.warns != strings.Contains(warning, landPR) || tc.warns != (warning != "") {
				t.Fatalf("warning = %q, want warns %v", warning, tc.warns)
			}
		})
	}
}

func TestAPRHeCanApproveNamedTwiceIsNamedOnceInTheWarning(t *testing.T) {
	f := fanout(t, scripted())
	ledgerRow(t, f, store.ReviewRow{ReadyForTap: true})
	if warning := f.tapWarning(Reply{MD: "#2196 is ready. Tap #2196.", Picks: []string{"Hold", "Go"}}); strings.Count(warning, landPR) != 1 {
		t.Fatalf("warning = %q, want %s once", warning, landPR)
	}
}

func TestAPickHisAnswerSettlesIsNotLinkedAndNamedInTheWarning(t *testing.T) {
	answers := `[{"id":"29b2e59aaaa","title":"Storage eviction: where to start","body":"Storage eviction should start with the oldest chats.","updated_at":"2026-11-04T09:00:00Z"}]`
	fake := &shelltest.Fake{}
	fake.Reply(boardWith("{}"), "cc-present", "outcomes")
	fake.Reply(answers, "ccn", "-R", "/repo", "answer", "list")
	f := fanout(t, fake)
	calls := 0
	f.Judge = func(_ context.Context, _, _, _ string) (*board.Judgment, error) {
		calls++
		return &board.Judgment{Verdict: "yes", Sentence: "He said at night."}, nil
	}
	r := Reply{ID: "r1", MD: "Waiting on card-storage-eviction now."}
	got, warning := f.withCardLinks(context.Background(), r)
	if got.MD != r.MD || !strings.Contains(warning, "card-storage-eviction is not linked") || !strings.Contains(warning, "He said at night") {
		t.Fatalf("md = %q, warning %q; want the card unlinked and his answer named", got.MD, warning)
	}
	if calls == 0 {
		t.Fatal("the judge never read his answer")
	}
}

func TestAReplyNamingNoCardReadsNoAnswers(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply(boardWith("{}"), "cc-present", "outcomes")
	f := fanout(t, fake)
	f.Judge = func(_ context.Context, _, _, _ string) (*board.Judgment, error) {
		t.Fatal("judged a card the reply does not name")
		return nil, nil
	}
	got, warning := f.withCardLinks(context.Background(), Reply{ID: "r1", MD: "All quiet."})
	if got.MD != "All quiet." || warning != "" {
		t.Fatalf("md = %q, warning %q", got.MD, warning)
	}
	if len(calls(fake, "ccn")) != 0 {
		t.Fatal("read his answers with no card named")
	}
}

func TestAReplyNamingAnOpenPickPutsAWorkingCopyInItsThread(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply(boardWith("{}"), "cc-present", "outcomes")
	fake.Reply(`{"permalink":"https://brook.slack.com/archives/D0B6Z/p3"}`, "cc-slack", "card-copy")
	scriptedOver(fake)
	f := fanout(t, fake)
	r, warning := f.withCardLinks(context.Background(), Reply{ID: "r1", Title: "Where things stand", MD: "Waiting on card-storage-eviction now.", Status: "info"})
	if warning != "" {
		t.Fatal(warning)
	}
	sent := f.Send(context.Background(), r)
	if copied := surface(sent, "card copy"); copied.Ref != "https://brook.slack.com/archives/D0B6Z/p3" {
		t.Fatalf("card copy = %+v in %+v", copied, sent)
	}
	var argv []string
	for _, call := range calls(fake, "cc-slack") {
		if call.Argv[1] == "card-copy" {
			argv = call.Argv
		}
	}
	if !slices.Equal(argv, []string{"cc-slack", "card-copy", "--url", cardMessage, "--thread", "1793635190.051179"}) {
		t.Fatalf("argv = %q, want the card copied under the reply it was named in", argv)
	}
	sent = f.Send(context.Background(), r)
	if copied := surface(sent, "card copy"); copied.Surface != "" {
		t.Fatalf("an edit copied the card again: %+v", copied)
	}
}

func TestACardCopyThatFailsIsNamedAndTheOtherSurfacesStillGo(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply(boardWith("{}"), "cc-present", "outcomes")
	fake.Fail(1, "the card has no answer left", "cc-slack", "card-copy")
	scriptedOver(fake)
	f := fanout(t, fake)
	r, _ := f.withCardLinks(context.Background(), Reply{ID: "r1", Title: "Where things stand", MD: "Waiting on card-storage-eviction now.", Status: "info"})
	sent := f.Send(context.Background(), r)
	if copied := surface(sent, "card copy"); !strings.Contains(copied.Error, "no answer left") {
		t.Fatalf("card copy = %+v", copied)
	}
	if surface(sent, "record").Ref == "" || surface(sent, "dashboard").Ref == "" {
		t.Fatalf("a failed copy stopped the rest: %+v", sent)
	}
}

func TestAPickHisAnswerSettlesGetsNoCopy(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply(boardWith("{}"), "cc-present", "outcomes")
	fake.Reply(`[{"id":"29b2e59aaaa","title":"Storage eviction: where to start","body":"Storage eviction should start with the oldest chats.","updated_at":"2026-11-04T09:00:00Z"}]`, "ccn", "-R", "/repo", "answer", "list")
	f := fanout(t, fake)
	f.Judge = func(_ context.Context, _, _, _ string) (*board.Judgment, error) {
		return &board.Judgment{Verdict: "yes", Sentence: "He said the oldest chats."}, nil
	}
	r, _ := f.withCardLinks(context.Background(), Reply{ID: "r1", Title: "Where things stand", MD: "Waiting on card-storage-eviction now.", Status: "info"})
	if len(r.OpenCards) != 0 {
		t.Fatalf("open cards = %+v, want the settled card left out", r.OpenCards)
	}
}

func TestOnlyAPickFreeReplyAsksForTheTapCardsOfItsPRs(t *testing.T) {
	for name, tc := range map[string]struct {
		reply Reply
		want  []string
	}{
		"a status card":  {Reply{ID: "r1", Title: "t", MD: "m", Status: info, PRs: []string{landPR}}, []string{"--tap-pr", landPR}},
		"his thread":     {Reply{ID: "r1", Title: "t", MD: "m", Thread: "1793684147.131059", PRs: []string{landPR}}, []string{"--tap-pr", landPR}},
		"a link in text": {Reply{ID: "r1", Title: "t", MD: landPR + " is ready.", Status: info}, nil},
	} {
		t.Run(name, func(t *testing.T) {
			fake := scripted()
			fanout(t, fake).Send(context.Background(), tc.reply)
			var got []string
			for _, call := range calls(fake, "cc-slack") {
				if i := slices.Index(call.Argv, "--tap-pr"); i >= 0 {
					got = call.Argv[i : i+2]
				}
			}
			if !slices.Equal(got, tc.want) {
				t.Fatalf("tap flags = %q, want %q", got, tc.want)
			}
		})
	}
	fake := scripted()
	fake.Reply(`{"permalink":"https://brook.slack.com/archives/D0B6Z/p3"}`, "cc-slack", "card-copy")
	fanout(t, fake).Send(context.Background(), Reply{ID: "r2", Title: "t", MD: "m", Picks: []string{"Land", "Hold"}, Recommended: "Land", Thread: "1793684147.131059", PRs: []string{landPR}})
	for _, call := range calls(fake, "cc-slack") {
		if slices.Contains(call.Argv, "--tap-pr") {
			t.Fatalf("a pick reply asked for a tap card: %q; its pick card is the offer", call.Argv)
		}
	}
}

func TestEditingATapNoticeAsksForItsTapCardsAgain(t *testing.T) {
	fake := scripted()
	f := fanout(t, fake)
	notice := Reply{ID: "r1", Title: "t", MD: "m", Status: info, PRs: []string{landPR}}
	f.Send(context.Background(), notice)
	notice.MD = "m, now at its new head"
	f.Send(context.Background(), notice)
	posts := slices.DeleteFunc(calls(fake, "cc-slack"), func(call shell.Command) bool { return call.Argv[1] != "dm-status" })
	edit := posts[len(posts)-1].Argv
	if len(posts) != 2 || !strings.Contains(flag(t, edit, "--card"), `"replaces"`) || flag(t, edit, "--tap-pr") != landPR {
		t.Fatalf("dm-status calls = %+v, want the edit to replace the notice and carry %s's tap card again", posts, landPR)
	}
}

func TestAPickNamingAPRThatLandsOnHisWordWarns(t *testing.T) {
	for name, tc := range map[string]struct {
		reply Reply
		warns bool
	}{
		"a pick naming it":    {Reply{MD: "Land #2196?", Picks: []string{"Land", "Hold"}}, true},
		"a status mentioning": {Reply{MD: "#2196 lands on your word."}, false},
	} {
		t.Run(name, func(t *testing.T) {
			f := fanout(t, scripted())
			ledgerRow(t, f, store.ReviewRow{WaitsOn: prrow.PortLands})
			warning := f.tapWarning(tc.reply)
			if tc.warns != strings.Contains(warning, "standing word") || tc.warns != strings.Contains(warning, landPR) {
				t.Fatalf("warning = %q, want warns %v", warning, tc.warns)
			}
		})
	}
}

func TestAReplyAskingAboutAPRHeAlreadyTappedWarns(t *testing.T) {
	tapped := store.ReviewRow{ReadyForTap: true, Tapped: true}
	for name, tc := range map[string]struct {
		reply Reply
		warns bool
	}{
		"a pick naming it":      {Reply{MD: "Land #2196?", Picks: []string{"Land", "Hold"}}, true},
		"a pick listing it":     {Reply{MD: "Land it?", Picks: []string{"Land", "Hold"}, PRs: []string{landPR}}, true},
		"a status mentioning":   {Reply{MD: "You approved #2196, and it lands next."}, false},
		"another repo's number": {Reply{MD: "Land monorepo#2196?", Picks: []string{"Land", "Hold"}}, false},
	} {
		t.Run(name, func(t *testing.T) {
			f := fanout(t, scripted())
			ledgerRow(t, f, tapped)
			warning := f.tapWarning(tc.reply)
			if tc.warns != strings.Contains(warning, "already tapped") || tc.warns != strings.Contains(warning, landPR) {
				t.Fatalf("warning = %q, want warns %v", warning, tc.warns)
			}
		})
	}
}

func TestALongPickKeepsItsWholeLabelAndItsEffect(t *testing.T) {
	long := strings.TrimSpace(strings.Repeat("Keep the chapter shape as it is ", 8))
	r, warned := Reply{Title: "Which?", MD: "m", Picks: []string{long + " :: It stays.", "Hold :: It waits."}, Recommended: "Hold"}.mended()
	if len(warned) != 0 {
		t.Fatalf("warned %q", warned)
	}
	if len([]rune(r.Picks[0])) > maxPick {
		t.Fatalf("label %q runs past %d", r.Picks[0], maxPick)
	}
	if r.Words[r.Picks[0]] != long+" It stays." {
		t.Fatalf("words = %q, want the whole label before the effect", r.Words[r.Picks[0]])
	}
	exact := strings.Repeat("x", 150)
	if r, _ := (Reply{Title: "Which?", MD: "m", Picks: []string{exact + " :: A.", "No :: B."}}).mended(); r.Picks[0] != exact || r.Words[exact] != "A." {
		t.Fatalf("a label of 150 characters changed: %q, %q", r.Picks[0], r.Words[exact])
	}
}

func TestAnApprovingPickTheCardCannotTieToOnePRAtOneHeadWarnsOnTheCardAndInTheResult(t *testing.T) {
	const otherPR = "https://github.com/yasyf/cc-skills/pull/2197"
	for name, tc := range map[string]struct {
		reply Reply
		warns bool
	}{
		"one PR with a row":          {Reply{MD: "Land it?", Picks: []string{"Approve, land it", "Hold"}, PRs: []string{landPR}}, false},
		"a PR with no row":           {Reply{MD: "Land it?", Picks: []string{"Approve, land it", "Hold"}, PRs: []string{otherPR}}, true},
		"two PRs":                    {Reply{MD: "Land both?", Picks: []string{"Land both", "Hold"}, PRs: []string{landPR, otherPR}}, true},
		"no PR listed":               {Reply{MD: "Land #2196?", Picks: []string{"Land it now", "Hold"}}, true},
		"a pick that approves no PR": {Reply{MD: "Which rule?", Picks: []string{"Keep the 30-day rule", "Drop it"}}, false},
	} {
		t.Run(name, func(t *testing.T) {
			f := fanout(t, scripted())
			ledgerRow(t, f, store.ReviewRow{ReadyForTap: true, ForUsers: "Chats load"})
			r, _ := tc.reply.mended()
			r, _, _ = f.withPRCards(context.Background(), r)
			got, warning := r.untiedApprovals()
			if tc.warns != strings.HasPrefix(warning, "APPROVES NOTHING") || tc.warns != strings.Contains(got.Words[got.Picks[0]], untiedPick) {
				t.Fatalf("warning = %q, words = %q, want warns %v", warning, got.Words, tc.warns)
			}
			if strings.Contains(got.Words[got.Picks[1]], untiedPick) {
				t.Fatalf("words = %q, want only the approving pick marked", got.Words)
			}
		})
	}
}

func TestAReplyNamingAnOpenBugCarriesItsFixPRsTapCard(t *testing.T) {
	fix := store.ReviewRow{Title: "Run a subagent's tools once", ForUsers: "Opening a running subagent's tab no longer runs its tools twice.", Description: "Fixes item 7.", ReadyForTap: true, State: prrow.StateOpen}
	fake := scripted()
	f := fanout(t, fake)
	ledgerRow(t, f, fix)
	var evidence []string
	f.Judge = func(_ context.Context, kind, said, _ string) (*board.Judgment, error) {
		evidence = append(evidence, kind+": "+said)
		return &board.Judgment{Verdict: "yes"}, nil
	}
	r, warning := f.withBugFixes(context.Background(), Reply{ID: "r1", Title: "FDE invites", MD: "Anubhav is inviting FDEs while the double-run bug is open.", Status: "info"})
	if warning != "" || !slices.Equal(r.PRs, []string{landPR}) {
		t.Fatalf("prs %q, warning %q, want the fix PR listed with no warning", r.PRs, warning)
	}
	if len(evidence) != 1 || !strings.HasPrefix(evidence[0], namesBugKind+": ") || !strings.Contains(evidence[0], fix.ForUsers) {
		t.Fatalf("judge asked %q, want one %s question carrying what the fix changes", evidence, namesBugKind)
	}
	f.Send(context.Background(), r)
	dm := calls(fake, "cc-slack")
	if len(dm) != 1 || flag(t, dm[0].Argv, "--tap-pr") != landPR {
		t.Fatalf("cc-slack calls %+v, want one card carrying the fix PR's tap card", dm)
	}
}

func TestABugsFixPRIsDrawnOrWarnedOnAsTheToolCanResolveIt(t *testing.T) {
	ready := store.ReviewRow{Title: "Run a subagent's tools once", ForUsers: "Tools run once.", Description: "Fixes item 7.", ReadyForTap: true, State: prrow.StateOpen}
	waiting := ready
	waiting.ReadyForTap, waiting.WaitsOn = false, "CI is running at this head"
	tapped := ready
	tapped.Tapped = true
	about := Reply{ID: "r1", Title: "FDE invites", MD: "The double-run bug is open.", Status: "info"}
	asking := about
	asking.Picks, asking.Recommended = []string{"Pause invites", "Keep inviting"}, "Pause invites"
	approving := asking
	approving.Picks, approving.Recommended = []string{"Land", "Hold"}, "Land"
	byItem := about
	byItem.MD = "Item 7 is open."
	listed := about
	listed.PRs = []string{landPR}
	for name, tc := range map[string]struct {
		row     store.ReviewRow
		reply   Reply
		verdict string
		failed  error
		prs     []string
		warning string
		judged  bool
	}{
		"named by its item, with no judge": {ready, byItem, "", nil, []string{landPR}, "", false},
		"named in words":                   {ready, about, "yes", nil, []string{landPR}, "", true},
		"not named":                        {ready, about, "no", nil, nil, "", true},
		"maybe named":                      {ready, about, "unsure", nil, nil, "this may name a bug these PRs fix; if it does, edit the reply with them in prs: " + landPR, true},
		"judge down":                       {ready, about, "", errors.New("timed out"), nil, "the judge did not read, so a bug this names in words may lack its fix PR's tap card: timed out", true},
		"in CI, with no picks":             {waiting, about, "yes", nil, []string{landPR}, "", true},
		"not ready, on a pick card":        {waiting, asking, "yes", nil, []string{landPR}, "", true},
		"beside an approving pick":         {ready, approving, "yes", nil, nil, "this names a bug whose fix PR waits on his tap, and its card beside an approving pick would tie that pick to no one PR, so send the fix in its own reply with it in prs: " + landPR, true},
		"already listed":                   {ready, listed, "", nil, []string{landPR}, "", false},
		"already tapped":                   {tapped, about, "", nil, nil, "", false},
	} {
		t.Run(name, func(t *testing.T) {
			f := fanout(t, scripted())
			ledgerRow(t, f, tc.row)
			judged := false
			f.Judge = func(context.Context, string, string, string) (*board.Judgment, error) {
				judged = true
				if tc.failed != nil {
					return nil, tc.failed
				}
				return &board.Judgment{Verdict: tc.verdict}, nil
			}
			r, warning := f.withBugFixes(context.Background(), tc.reply)
			if !slices.Equal(r.PRs, tc.prs) || warning != tc.warning || judged != tc.judged {
				t.Fatalf("prs %q, warning %q, judged %v; want %q, %q, %v", r.PRs, warning, judged, tc.prs, tc.warning, tc.judged)
			}
		})
	}
}

func TestAnApprovingPickDrawsTheOnePRItsWordsName(t *testing.T) {
	land := []string{"Land", "Hold"}
	for name, tc := range map[string]struct {
		reply Reply
		prs   []string
	}{
		"one ledger PR named":        {Reply{Title: "Land the relay fix?", MD: "#2196 is green.", Picks: land}, []string{landPR}},
		"a PR with no ledger row":    {Reply{Title: "Land the relay fix?", MD: "#2197 is green.", Picks: land}, nil},
		"two PRs named":              {Reply{Title: "Land the relay fixes?", MD: "#2196 and #2197 are green.", Picks: land}, nil},
		"prs already listed":         {Reply{Title: "Land it?", MD: "#2196 is green.", Picks: land, PRs: []string{landPR + "0"}}, []string{landPR + "0"}},
		"no approving pick":          {Reply{Title: "Pause invites?", MD: "#2196 is green.", Picks: []string{"Pause", "Keep going"}}, nil},
		"another repo's same number": {Reply{Title: "Land it?", MD: "Forge-AI/monorepo#2196 is green.", Picks: land}, nil},
	} {
		t.Run(name, func(t *testing.T) {
			f := fanout(t, scripted())
			ledgerRow(t, f, store.ReviewRow{Title: "Relay fix", ReadyForTap: true})
			r, warning := f.withApprovedPR(tc.reply)
			if warning != "" || !slices.Equal(r.PRs, tc.prs) {
				t.Fatalf("prs %q, warning %q, want %q", r.PRs, warning, tc.prs)
			}
		})
	}
}

func TestAReplyAskingAboutAnExemptPRNeverSaysHeTappedIt(t *testing.T) {
	pick := Reply{MD: "Keep #2196?", Picks: []string{"Keep", "Revert"}}
	for name, tc := range map[string]struct {
		row   store.ReviewRow
		warns bool
	}{
		"exempt and landing next": {store.ReviewRow{State: prrow.StateExempt, WaitsOn: "Exempt as regression; the landing desk lands it next."}, true},
		"exempt and landed":       {store.ReviewRow{State: prrow.StateLanded, WaitsOn: "Nothing: it landed."}, false},
	} {
		t.Run(name, func(t *testing.T) {
			f := fanout(t, scripted())
			ledgerRow(t, f, tc.row)
			warning := f.tapWarning(pick)
			if strings.Contains(warning, "already tapped") || tc.warns != strings.Contains(warning, "lands exempt") {
				t.Fatalf("warning = %q, want warns %v", warning, tc.warns)
			}
		})
	}
}

func TestADocMergePickCarriesTheDocsPage(t *testing.T) {
	merge := Reply{
		Title:       "SandSQL sharding doc, revision 4: merge it?",
		MD:          "SandSQL sharding doc, one pick needed. Revision 4 is on [design-docs#332](https://github.com/Forge-AI/design-docs/pull/332) with green checks. It is a new doc, so your standing grant for doc revisions does not cover it and it waits on your word. <!-- cci #499307 -->",
		Picks:       []string{"Merge the doc :: The lane merges revision 4 and you get the rendered page link.", "Not yet :: The doc stays open for your comments, and nothing merges."},
		Recommended: "Merge the doc",
	}
	page := []Link{{Label: "Read revision 4", URL: "https://docs.poetic.design/design-docs/sandsql-per-database-sharding/"}}
	for name, tc := range map[string]struct {
		reply   Reply
		refused bool
	}{
		"the m1270 card, its PR link alone":    {merge, true},
		"the same card with its Pages link":    {Reply{Title: merge.Title, MD: merge.MD, Picks: merge.Picks, Links: page}, false},
		"its Pages link only in a hidden cite": {Reply{Title: merge.Title, MD: merge.MD + " <!-- " + page[0].URL + " -->", Picks: merge.Picks}, true},
		"the same words with no pick to tap":   {Reply{Title: merge.Title, MD: merge.MD}, false},
		"a code PR's merge pick":               {Reply{Title: "Merge #51418?", MD: "[#51418](https://github.com/Forge-AI/monorepo/pull/51418) is green.", Picks: merge.Picks}, false},
	} {
		t.Run(name, func(t *testing.T) {
			err := tc.reply.unreadAsk()
			if tc.refused != (err != nil) || tc.refused && !strings.HasPrefix(err.Error(), "NO DOC PAGE") {
				t.Fatalf("refusal = %v, want refused %v", err, tc.refused)
			}
		})
	}
}

func TestAKeyPickCitesTheCodeThatReadsTheKey(t *testing.T) {
	mint := Reply{
		Title: "Tailscale API key for cc-remote is invalid",
		MD:    "Remote codex launches fail: the Tailscale API answered '500 invalid key'.",
		Picks: []string{"New key is in place :: You mint a new auth key and the lane re-runs the prepare.", "Run codex workers locally :: Until a new key lands."},
	}
	for name, tc := range map[string]struct {
		reply   Reply
		refused bool
	}{
		"the Nov 1 tailnet key pick":           {mint, true},
		"the same pick citing the enroll code": {Reply{Title: mint.Title, MD: mint.MD + " cc-remote reads it at internal/tailnet/enroll.go:88.", Picks: mint.Picks}, false},
		"its code line only in a hidden cite":  {Reply{Title: mint.Title, MD: mint.MD + " <!-- internal/tailnet/enroll.go:88 -->", Picks: mint.Picks}, true},
		"the same words with no pick to tap":   {Reply{Title: mint.Title, MD: mint.MD}, false},
	} {
		t.Run(name, func(t *testing.T) {
			err := tc.reply.unreadAsk()
			if tc.refused != (err != nil) || tc.refused && !strings.HasPrefix(err.Error(), "UNREAD CREDENTIAL") {
				t.Fatalf("refusal = %v, want refused %v", err, tc.refused)
			}
		})
	}
}

func TestAReplyCarryingATagFragmentIsRefused(t *testing.T) {
	for name, tc := range map[string]struct {
		reply   Reply
		refused bool
	}{
		"a typed closing title tag":     {Reply{Title: "Sprites are back</title>", MD: "Fixed."}, true},
		"an opening md tag in the body": {Reply{Title: "Sprites", MD: "<md>Fixed."}, true},
		"a Slack mention and link":      {Reply{Title: "Sprites", MD: "<@U123> see <https://example.com|the run>."}, false},
		"a hidden cite":                 {Reply{Title: "Sprites", MD: "Fixed. <!-- enroll.go:88 -->"}, false},
		"a comparison":                  {Reply{Title: "Sprites", MD: "Fewer than 3 < 5 failures."}, false},
	} {
		t.Run(name, func(t *testing.T) {
			err := tc.reply.strayMarkup()
			if tc.refused != (err != nil) || tc.refused && !strings.HasPrefix(err.Error(), "STRAY MARKUP") {
				t.Fatalf("refusal = %v, want refused %v", err, tc.refused)
			}
		})
	}
}

func TestAReplyAskingForHisTapWithNothingToTapIsRefused(t *testing.T) {
	ready := store.ReviewRow{ReadyForTap: true}
	for name, tc := range map[string]struct {
		row     store.ReviewRow
		reply   Reply
		refused bool
	}{
		"a fix waiting on his tap, with no prs":         {ready, Reply{Title: "Dashboard serve flake", MD: "#2196 is green and waits on your tap."}, true},
		"a list of PRs waiting on another card":         {ready, Reply{Title: "Six PRs wait only on your answer to a card already on your board", MD: "#2196 and five more."}, true},
		"a PR proven green, listed but tapped":          {store.ReviewRow{ReadyForTap: true, Tapped: true}, Reply{Title: "Fork side-chat bug", MD: "#2196 is proven green and waits on your approval.", PRs: []string{landPR}}, true},
		"a count whose fixes wait on him, PR not ready": {store.ReviewRow{WaitsOn: "CI"}, Reply{Title: "Brittleness, the count", MD: "Three fixes are ready for your approval.", PRs: []string{landPR}}, true},
		"a title that opens with Tap":                   {ready, Reply{Title: "Tap: #2196, approval cards work again", MD: "It passed."}, true},
		"a ready PR listed in prs":                      {ready, Reply{Title: "Tap: #2196", MD: "It passed.", PRs: []string{landPR}}, false},
		"a question with picks":                         {ready, Reply{Title: "Which fix?", MD: "Both wait on your answer.", Picks: []string{"A", "B"}}, false},
		"his answer recorded":                           {ready, Reply{Title: "Port, old tree to new: dev #2196", MD: "Your pick is recorded, and the port state follows."}, false},
		"a PR in CI listed in prs":                      {inCI, Reply{Title: "Stuck Working tab", MD: "#2196 is in CI and waits on your tap.", PRs: []string{landPR}}, false},
		"a PR in the queue on his approval":             {ready, Reply{Title: "#2196 is in the merge queue on your approval", MD: "It lands next."}, false},
		"a wait on something else, said plainly":        {store.ReviewRow{WaitsOn: "CI"}, Reply{Title: "#2196 waits on CI", MD: "It needs a green build first."}, false},
		"a hidden cite that reads as an ask":            {ready, Reply{Title: "Fix is live", MD: "It is live. <!-- waits on your tap -->"}, false},
		"the refusal described, asking for approval":    {ready, Reply{Title: "Buttons on approval messages: the refusal is live in this session", MD: "Buttons on approval messages: the fix is live here, and I was wrong at 1:27 PM to say it had not reached this session. Since 1:10 PM a message from me that asks for your approval with no button is refused before it posts, and it ran on my 1:23, 1:24 and 1:28 PM cards."}, false},
		"the refusal described, waiting on him":         {ready, Reply{Title: "Messages with nothing to tap are now refused before they post", MD: "The fix you asked for here is live in this session, and I was wrong at 1:27 PM to say it had not reached it. Since 1:10 PM a message that reads as waiting on you and carries no button is refused before it posts. I just saw it refuse one of mine at 1:33 PM."}, false},
		"an ask beside a described refusal":             {ready, Reply{Title: "Refusal is live", MD: "A message with no button is refused now. #2196 is green and needs your tap."}, true},
		"an order to tap a button elsewhere":            {ready, Reply{Title: "Fork side-chat bug", MD: "It passed. Tap the button on the card above."}, true},
	} {
		t.Run(name, func(t *testing.T) {
			f := fanout(t, scripted())
			ledgerRow(t, f, tc.row)
			err := f.nothingToTap(tc.reply)
			if tc.refused != (err != nil) || tc.refused && !strings.HasPrefix(err.Error(), "NOTHING TO TAP") {
				t.Fatalf("refusal = %v, want refused %v", err, tc.refused)
			}
		})
	}
}

func TestAnApproveCardOnAPRHeAlreadyTappedIsRefused(t *testing.T) {
	for name, tc := range map[string]struct {
		row     store.ReviewRow
		reply   Reply
		refused bool
	}{
		"#52229's tap carried to a new head": {store.ReviewRow{ReadyForTap: true, Tapped: true}, Reply{Title: "Tap: #2196", MD: "It passed.", Picks: []string{"Approve", "Hold"}, PRs: []string{landPR}}, true},
		"an untapped PR waiting on his tap":  {store.ReviewRow{ReadyForTap: true}, Reply{Title: "Tap: #2196", MD: "It passed.", Picks: []string{"Approve", "Hold"}, PRs: []string{landPR}}, false},
		"a question on a tapped PR, no land": {store.ReviewRow{Tapped: true}, Reply{Title: "Which follow-up?", MD: "Two ways on.", Picks: []string{"Split it", "Keep it"}, PRs: []string{landPR}}, false},
	} {
		t.Run(name, func(t *testing.T) {
			f := fanout(t, scripted())
			ledgerRow(t, f, tc.row)
			err := f.approvesTapped(tc.reply)
			if tc.refused != (err != nil) || tc.refused && !strings.HasPrefix(err.Error(), "ALREADY TAPPED") {
				t.Fatalf("refusal = %v, want refused %v", err, tc.refused)
			}
		})
	}
}

func TestAPickCardNamesHisNearestRulingsSoAPickHeSettledComesOff(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply("- 1026183 Prompt rows: keep the judge's prompt as written\n> his words\n- 1026190 Prompt edits go through the eval\n", "/claude/rulings.py", "match")
	f := fanout(t, fake)
	f.Rulings = "/claude/rulings.py"

	warning := f.rulingsWarning(context.Background(), Reply{Title: "Rewrite the judge prompt?", Picks: []string{"Rewrite it", "Keep it"}})

	if want := "his rulings nearest this question are 1026183 Prompt rows: keep the judge's prompt as written; 1026190 Prompt edits go through the eval; drop any pick one of them already settles"; warning != want {
		t.Fatalf("warning = %q, want %q", warning, want)
	}
	run := fake.Calls()[0]
	if want := []string{"/claude/rulings.py", "match", "--drive", "d", "--repo", "/repo", "-k", "3"}; !slices.Equal(run.Argv, want) || run.Stdin != "Rewrite the judge prompt?\nRewrite it\nKeep it" {
		t.Fatalf("ran %q with %q", run.Argv, run.Stdin)
	}
	if warning := f.rulingsWarning(context.Background(), Reply{Title: "Landed"}); warning != "" || len(fake.Calls()) != 1 {
		t.Fatalf("a reply with no picks warned %q and ran %d commands", warning, len(fake.Calls()))
	}
}
