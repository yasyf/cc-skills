package explain

import (
	"bytes"
	"context"
	"errors"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/launchd"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

const sample = "# How a lane resumes\n\nA lane picks up from its newest handoff.\n\n## The handoff\n\nEach handoff names the **next step**.\n\n```mermaid\nflowchart LR\n  lane --> handoff\n```\n"

type host struct{ dirs []string }

func (h *host) Serve(_ context.Context, dir string) (string, error) {
	h.dirs = append(h.dirs, dir)
	return "https://mac.tail1.ts.net:8643/", nil
}

type told struct{ notices []Notice }

func (t *told) Announce(_ context.Context, n Notice) error {
	t.notices = append(t.notices, n)
	return nil
}

type posts struct{ drafts []client.Draft }

func (p *posts) Post(_ context.Context, d client.Draft) (client.Record, error) {
	p.drafts = append(p.drafts, d)
	return client.Record{}, nil
}

func ran(calls []shell.Command, prefix ...string) bool {
	return slices.ContainsFunc(calls, func(c shell.Command) bool {
		return len(c.Argv) >= len(prefix) && slices.Equal(c.Argv[:len(prefix)], prefix)
	})
}

func TestExplainCommitsAKitOnePagerWithItsDiagramOnTheExplainersBranchAndAnnouncesItsTailnetLink(t *testing.T) {
	root, dir := t.TempDir(), filepath.Join(t.TempDir(), "explainers")
	git := &shelltest.Fake{}
	git.Fail(1, "", "git", "-C", root, "rev-parse")
	git.Reply("?? one-pagers/lane-resume/index.html\n", "git", "-C", dir, "status")
	git.Reply("", "git")
	served, owner := &host{}, &told{}

	link, err := Explain(context.Background(), Checkout{Runner: git, Root: root, Dir: dir}, served, owner, "Lane resume", []byte(sample), time.Date(2026, 11, 3, 11, 10, 0, 0, time.UTC))
	if err != nil {
		t.Fatal(err)
	}

	if link != "https://mac.tail1.ts.net:8643/one-pagers/lane-resume/" {
		t.Fatalf("link %q", link)
	}
	if !slices.Equal(served.dirs, []string{dir}) {
		t.Fatalf("served %v", served.dirs)
	}
	if want := []Notice{{Title: "How a lane resumes", Lede: "A lane picks up from its newest handoff.", Link: link}}; !slices.Equal(owner.notices, want) {
		t.Fatalf("announced %v", owner.notices)
	}
	body, err := os.ReadFile(filepath.Join(dir, "one-pagers", "lane-resume", "index.html"))
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{
		"<title>How a lane resumes</title>",
		`<meta name="description" content="A lane picks up from its newest handoff.">`,
		`<meta name="updated" content="2026-11-03">`,
		`<link rel="stylesheet" href="../kit/one-pager.css">`,
		`<script type="module" src="../kit/one-pager.js"></script>`,
		`<li><a href="#the-handoff">The handoff</a></li>`,
		`<section class="op-panel" id="the-handoff">`,
		"<p>Each handoff names the <strong>next step</strong>.</p>",
		"<pre class=\"mermaid\">flowchart LR\n  lane --&gt; handoff\n</pre>",
	} {
		if !strings.Contains(string(body), want) {
			t.Errorf("page lacks %q:\n%s", want, body)
		}
	}
	calls := git.Calls()
	if !ran(calls, "git", "-C", root, "worktree", "add", "-b", Branch, dir, "origin/main") || !ran(calls, "git", "-C", dir, "commit") {
		t.Fatalf("calls %v", calls)
	}
}

func TestTailnetServesTheWorktreeOnLoopbackBehindTailscaleServeAndReturnsItsHTTPSURLOnceItAnswers(t *testing.T) {
	run := &shelltest.Fake{}
	run.Reply("", "tailscale", "serve")
	run.Reply(`{"Self": {"DNSName": "mac.tail1.ts.net."}}`, "tailscale", "status")
	run.Fail(113, "", "launchctl", "print")
	run.Reply("", "launchctl", "bootstrap")
	home := t.TempDir()
	var asked []string
	tailnet := Tailnet{
		Runner:  run,
		Agents:  launchd.Launchd{Runner: run, Home: home, UID: 501, Sleep: func(time.Duration) {}, Out: &bytes.Buffer{}},
		Binary:  "/claude/long-running/current/bin/lr",
		Log:     "/claude/long-running/explain.log",
		Answers: func(url string) bool { asked = append(asked, url); return len(asked) == 2 },
	}

	url, err := tailnet.Serve(context.Background(), "/claude/long-running/explainers")
	if err != nil {
		t.Fatal(err)
	}

	if url != "https://mac.tail1.ts.net:8643/" || len(asked) != 2 {
		t.Fatalf("url %q asked %v", url, asked)
	}
	if !ran(run.Calls(), "tailscale", "serve", "--bg", "--https=8643", "http://127.0.0.1:8642") {
		t.Fatalf("calls %v", run.Calls())
	}
	plist, err := os.ReadFile(filepath.Join(home, "Library", "LaunchAgents", Label+".plist"))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(plist), "<string>/claude/long-running/current/bin/lr</string>\n\t\t<string>explain</string>\n\t\t<string>serve</string>\n\t\t<string>--dir</string>\n\t\t<string>/claude/long-running/explainers</string>\n\t\t<string>--addr</string>\n\t\t<string>127.0.0.1:8642</string>") {
		t.Fatalf("plist:\n%s", plist)
	}
}

func TestAnnouncingAPageTellsOwnerRelayAndCardsTheOwnersDMAndAFailedCardRaisesABlocker(t *testing.T) {
	notice := Notice{Title: "How a lane resumes", Lede: "A lane picks up from its newest handoff.", Link: "https://mac.tail1.ts.net:8643/one-pagers/lane-resume/"}
	slack := &shelltest.Fake{}
	slack.Reply(`{"ok": true}`, "cc-slack", "dm-status")
	sent := &posts{}

	if err := (Owner{Runner: slack, Post: sent.Post, Drive: "d1", Lane: "signal-1"}).Announce(context.Background(), notice); err != nil {
		t.Fatal(err)
	}

	if len(sent.drafts) != 1 || sent.drafts[0].Kind != "report" || !slices.Equal(sent.drafts[0].To, []string{"owner-relay"}) || !strings.Contains(sent.drafts[0].Text, notice.Link) {
		t.Fatalf("posted %+v", sent.drafts)
	}
	card := slack.Calls()[0].Argv
	if len(card) != 4 || card[2] != "--card" || card[3] != `{"body":"A lane picks up from its newest handoff.","links":[{"label":"Open the one-pager","url":"https://mac.tail1.ts.net:8643/one-pagers/lane-resume/"}],"title":"How a lane resumes"}` {
		t.Fatalf("card %v", card)
	}

	down := &shelltest.Fake{}
	down.Fail(1, "cc-slack: daemon not running", "cc-slack", "dm-status")
	sent = &posts{}

	err := (Owner{Runner: down, Post: sent.Post, Drive: "d1", Lane: "owner-relay"}).Announce(context.Background(), notice)

	var exit *shell.ExitError
	if !errors.As(err, &exit) || !strings.Contains(err.Error(), notice.Link) {
		t.Fatalf("err %v", err)
	}
	if len(sent.drafts) != 1 || sent.drafts[0].Kind != "blocker" || !slices.Equal(sent.drafts[0].To, []string{"root"}) || !strings.Contains(sent.drafts[0].Text, notice.Link) {
		t.Fatalf("posted %+v", sent.drafts)
	}
}
