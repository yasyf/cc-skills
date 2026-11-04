package reply

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"
	"unicode/utf8"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/owner"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/talkboard"
)

const (
	permalink = "https://brook.slack.com/archives/D0B6Z/p1793635190051179"
	landPR    = "https://github.com/yasyf/cc-skills/pull/2196"
	posted    = `{"seq":12,"num":"#12@2479ec97"}`
)

var sentAt = time.Date(2026, 11, 3, 12, 0, 0, 0, time.UTC)

func scripted() *shelltest.Fake {
	fake := &shelltest.Fake{}
	fake.Reply("updated card\nsent: card ts=1793635190.051179 "+permalink+"\n", "cc-present", "update-block")
	fake.Reply(`{"channel_id":"D0B6Z","thread_ts":"1793635190.051179","ts":"1793635190.051179","permalink":"`+permalink+`"}`, "cc-slack", "dm-status")
	fake.Reply(posted, "cci", "post")
	return fake
}

func fanout(t *testing.T, runner shell.Runner) Fanout {
	t.Helper()
	entry := drive.Entry{Drive: "d", CCIDrive: "dar", StateDir: t.TempDir(), Checkout: "/repo", Sessions: []string{"board-session"}}
	if err := os.WriteFile(filepath.Join(entry.StateDir, talkboard.Links), []byte("{}"), 0o600); err != nil {
		t.Fatal(err)
	}
	return Fanout{Runner: runner, Entry: entry, Now: func() time.Time { return sentAt }}
}

func calls(fake *shelltest.Fake, tool string) []shell.Command {
	return slices.DeleteFunc(fake.Calls(), func(c shell.Command) bool { return c.Argv[0] != tool || onThread(c) })
}

func onThread(c shell.Command) bool {
	return slices.Contains(c.Argv, "talk-d")
}

func threadWrites(fake *shelltest.Fake) []shell.Command {
	return slices.DeleteFunc(fake.Calls(), func(c shell.Command) bool { return !onThread(c) })
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

func board(t *testing.T, c shell.Command) boardCard {
	t.Helper()
	var card boardCard
	if err := json.Unmarshal([]byte(c.Stdin), &card); err != nil {
		t.Fatalf("board card %q: %v", c.Stdin, err)
	}
	return card
}

type slackCard struct {
	Key   string `json:"key"`
	Title string `json:"title"`
	Body  string `json:"body"`
	Links []Link `json:"links"`
	Refs  []Link `json:"refs"`
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
	if len(presented) != 1 || flag(t, presented[0].Argv, "--session") != "board-session" || board(t, presented[0]).ID != "card-"+id {
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

func TestAPickRendersOneButtonPerOptionWithTheRecommendedOneMarked(t *testing.T) {
	fake := scripted()
	f := fanout(t, fake)
	picks := []string{"Keep one shared replica", "Give each chat its own", "Ask me after the soak"}

	f.Send(context.Background(), Reply{ID: "r2", Title: "How do we fix the relay?", MD: "Each chat keeps its own replica, or they share one.", Picks: picks, Recommended: picks[1], PRs: []string{landPR}})

	card := board(t, calls(fake, "cc-present")[0])
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

	card := board(t, calls(fake, "cc-present")[0])
	choice := card.Children[slices.IndexFunc(card.Children, func(c child) bool { return c.Type == "choice" })]
	if choice.Options[0].Build != nil || choice.Options[1].Build == nil || *choice.Options[1].Build != *built {
		t.Fatalf("options %+v, want the branch on the recommended pick only", choice.Options)
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
				if card := board(t, c); card.ID != "card-r3" || !strings.Contains(card.Summary, "Checks") {
					t.Errorf("board write %+v is not card-r3", card)
				}
			}
			for _, c := range calls(fake, "cc-slack") {
				if card := slack(t, c); card.Key != "r3" {
					t.Errorf("slack card key = %q, want r3 so cc-slack redraws the card", card.Key)
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

func TestAnEditThatDropsItsPicksTakesTheCardOffAndPostsNothingNew(t *testing.T) {
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
	if posted := calls(fake, "cc-slack"); len(posted) != 0 {
		t.Fatalf("cc-slack calls = %+v, want none: the edit posted a new Slack message beside the picks card", posted)
	}
	if reply, surfaces := stored(t, f, "r6"); len(reply.Picks) != 0 || reply.MD != "It merged." || surfaces[0].Ref != "card-r6 withdrawn" {
		t.Fatalf("dashboard response = %+v %+v, want the edit with no picks", reply, surfaces)
	}
	records := calls(fake, "cci")
	if len(records) != 3 || !strings.HasPrefix(records[2].Stdin, "The relay fix landed\n\nIt merged.") {
		t.Fatalf("records = %+v, want the edit's body in the last record", records)
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

func TestAStatusReplyNamesItsPRsInTheSlackCardTextForCCSlackToLinkAndCard(t *testing.T) {
	fake := scripted()
	fanout(t, fake).Send(context.Background(), Reply{ID: "r5", Title: "The relay fix", MD: "Checks passed.", Status: "done", PRs: []string{landPR}})
	posted := calls(fake, "cc-slack")
	if len(posted) != 1 {
		t.Fatalf("cc-slack calls = %+v, want one card", posted)
	}
	if card := slack(t, posted[0]); card.Body != "Checks passed.\n"+landPR || len(card.Refs) != 0 {
		t.Fatalf("card = %+v, want the PR in its text and no refs", card)
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
	for name, in := range map[string]Reply{
		"action": {ID: "a", Title: long, MD: "m", Order: "m1", Action: "Tap one of these below please", Picks: []string{"A", "B"}, Recommended: "A"},
		"done":   {ID: "a", Title: long, MD: "m", Order: "m1", Status: "done"},
	} {
		mended, _ := in.mended()
		if got := mended.led().Title; utf8.RuneCountInString(got) > maxTitle || got == long {
			t.Errorf("%s: title %q, want it led and at most %d characters", name, got, maxTitle)
		}
	}
}

func TestAnExplainMoreThatIsOnlyALinkStaysRefused(t *testing.T) {
	base := Reply{ID: "r4", Title: "Pick a relay", MD: "Two ways."}
	for name, r := range map[string]Reply{
		"a pick explained by a link only": {ID: base.ID, Title: base.Title, MD: "Tap one below.\n\nsee https://docs.poetic.design/one-pagers/x/", Action: "Tap one below", Picks: []string{"A way", "B way"}, Recommended: "A way"},
		"an Explain more that is a link":  {ID: base.ID, Title: base.Title, MD: "Two ways.\n\n**Explain more:** https://docs.poetic.design/one-pagers/x/"},
	} {
		if err := r.bareExplainMore(); err == nil {
			t.Errorf("%s: passed %+v", name, r)
		}
	}
	if err := base.bareExplainMore(); err != nil {
		t.Fatalf("plain reply refused: %v", err)
	}
	linked := Reply{ID: base.ID, Title: base.Title, MD: "Done, nothing for you.\n\nhttps://docs.poetic.design/one-pagers/x/"}
	if err := linked.bareExplainMore(); err != nil {
		t.Fatalf("a done reply naming its page refused: %v", err)
	}
}

func TestSlackBodiesTurnMarkdownIntoMrkdwn(t *testing.T) {
	md := "## Relay\n**Fixed** in [the PR](https://github.com/yasyf/cc-skills/pull/2196).\n\n| check | state |\n|---|---|\n| lr-go | green |\nDone."
	want := "*Relay*\n*Fixed* in <https://github.com/yasyf/cc-skills/pull/2196|the PR>.\n\n```\n| check | state |\n|---|---|\n| lr-go | green |\n```\nDone."
	if got := mrkdwn(md); got != want {
		t.Fatalf("mrkdwn =\n%s\nwant\n%s", got, want)
	}
	long := strings.Repeat("a", 2000) + "\n\n" + strings.Repeat("b", 2000)
	if got := fitted(long, maxSlackBody); got != strings.Repeat("a", 2000)+"…" {
		t.Fatalf("fitted kept %d runes, want the first paragraph and an ellipsis", len([]rune(got)))
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
	want := "*The relay lane is half done*\n" + long + "\n" + landPR + "\n<https://studio.example.ts.net/one-pagers/relay/|One-pager>"
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
	card := board(t, sent[0])
	if card.Summary != "Checks passed." || card.Children[0].Type != "markdown" || card.Children[0].MD != "It ships tonight." {
		t.Fatalf("card %+v, want the lead in the summary and the rest in the markdown", card)
	}
	if card := board(t, sent[1]); card.Summary != "Checks passed." || card.Children[0].Type != "choice" {
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

func TestAReplyToAnOrderLeadsWithDoneOrHisOneActionWhateverTheLaneWrote(t *testing.T) {
	done := Reply{Title: "Stored in the background", MD: "A parse is stored only for a chat being opened.", Order: "m347"}.led()
	if !strings.HasPrefix(done.Title, "Done, nothing for you: ") || !strings.HasPrefix(done.MD, "Done, nothing for you.\n\nA parse") {
		t.Fatalf("done reply = %q, %q", done.Title, done.MD)
	}
	if done.Status != "" {
		t.Fatalf("status = %q, want none: a done reply asks nothing of him", done.Status)
	}
	acting := Reply{Title: "Storage", MD: "It cannot write the new store.", Order: "m347", Action: "Pick where it stores on your board."}.led()
	if !strings.HasPrefix(acting.MD, "Pick where it stores on your board.\n\nIt cannot") || acting.Status != needsYou {
		t.Fatalf("action reply = %q, status %q", acting.MD, acting.Status)
	}
	picking := Reply{Title: "Storage", MD: "Two places.", Order: "m347", Picks: []string{"A", "B"}, Recommended: "A"}.led()
	if !strings.HasPrefix(picking.MD, pickLead) || picking.Action != pickLead {
		t.Fatalf("a reply with picks leads %q and stores action %q: done would be false", picking.MD, picking.Action)
	}
	twice := Reply{Title: "Done, nothing for you: stored", MD: "Done, nothing for you. It is stored.", Order: "m347"}.led()
	if twice.Title != "Done, nothing for you: stored" || twice.MD != "Done, nothing for you. It is stored." {
		t.Fatalf("a lead the lane wrote doubled: %q, %q", twice.Title, twice.MD)
	}
	plain := Reply{Title: "A status line", MD: "Nothing he ordered."}.led()
	if plain.Title != "A status line" || plain.MD != "Nothing he ordered." {
		t.Fatalf("a reply naming no order changed: %q, %q", plain.Title, plain.MD)
	}
}

func TestALongSlackCardLinksTheWholeReplyOnTheDashboardAndNeverJustStops(t *testing.T) {
	fake := scripted()
	f := fanout(t, fake)
	if err := os.WriteFile(filepath.Join(f.Entry.StateDir, "dashboard.json"), []byte(`{"host":"100.1.2.3","port":61210}`), 0o600); err != nil {
		t.Fatal(err)
	}
	long := strings.Repeat("a", 2000) + "\n\n" + strings.Repeat("b", 2000)
	f.Send(context.Background(), Reply{ID: "long", Title: "t", MD: long, Status: "done"})
	card := flag(t, calls(fake, "cc-slack")[0].Argv, "--card")
	var sent struct {
		Body string `json:"body"`
	}
	if err := json.Unmarshal([]byte(card), &sent); err != nil {
		t.Fatal(err)
	}
	if !strings.HasSuffix(sent.Body, "<http://100.1.2.3:61210/#/replies|Read the rest on your dashboard>") || len([]rune(sent.Body)) > maxSlackBody {
		t.Fatalf("slack body ends %q at %d runes", sent.Body[len(sent.Body)-80:], len([]rune(sent.Body)))
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

func TestAProgressReplyToHisOrderWritesTheLedgerRowAndPostsNothingToSlack(t *testing.T) {
	fake := scripted()
	f := fanout(t, fake)
	path := ledgered(t, f)
	sent := f.Send(context.Background(), Reply{ID: "rp", Title: "Vector search", MD: "tp-desc-vector-1 is designing it.", Status: progress, Order: "m447", By: "11:30 PM", Routed: 400803}.led())
	if got := calls(fake, "cc-slack"); len(got) != 0 {
		t.Fatalf("a progress reply reached Slack: %q", got[0].Argv)
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

func TestAnInfoReplyToHisOrderMarksTheRowRespondedAndPostsNothingToSlack(t *testing.T) {
	fake := scripted()
	f := fanout(t, fake)
	path := ledgered(t, f)
	f.Send(context.Background(), Reply{ID: "ri", Title: "Vector search", MD: "It is a design, not a build.", Status: info, Order: "m447", Thread: "1793635190.051179"}.led())
	if got := calls(fake, "cc-slack"); len(got) != 0 {
		t.Fatalf("an info reply reached Slack: %q", got[0].Argv)
	}
	if ledger, _ := os.ReadFile(path); !strings.Contains(string(ledger), "|  | responded |") {
		t.Fatalf("ledger = %q", ledger)
	}
}

func TestAProgressReplyIsNotLedAsDone(t *testing.T) {
	r := Reply{Title: "Vector search", MD: "Designing.", Status: progress, Order: "m447"}.led()
	if strings.Contains(r.Title+r.MD, doneLead) {
		t.Fatalf("progress reply led as done: %q, %q", r.Title, r.MD)
	}
}

func TestByAndRoutedOffAQuietOrderReplyAreLeftOffWithAWarning(t *testing.T) {
	for name, r := range map[string]Reply{
		"no order":  {ID: "a", Title: "t", MD: "m", Status: progress, By: "11:30 PM"},
		"done":      {ID: "a", Title: "t", MD: "m", Status: "done", Order: "m1", Routed: 7},
		"with pick": {ID: "a", Title: "t", MD: "m", Status: progress, Order: "m1", By: "11:30 PM", Picks: []string{"A", "B"}, Recommended: "A"},
	} {
		got, warned := r.mended()
		if got.By != "" || got.Routed != 0 || len(warned) == 0 {
			t.Errorf("%s: mended to %+v, warned %q", name, got, warned)
		}
	}
	quiet := Reply{ID: "a", Title: "t", MD: "m", Status: progress, Order: "m1", By: "11:30 PM", Routed: 7}
	if got, warned := quiet.mended(); got.By == "" || got.Routed == 0 || len(warned) != 0 {
		t.Fatalf("a quiet reply mended to %+v, warned %q", got, warned)
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
