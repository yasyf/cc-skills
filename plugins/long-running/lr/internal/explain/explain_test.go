package explain

import (
	"bytes"
	"context"
	"encoding/base64"
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

const sampleDiagram = `{"arch":1,"name":"Lane resume","title":"A lane resumes from its handoff","nodes":[{"id":"lane","kind":"worker","name":"Lane","role":"Does the work"},{"id":"handoff","kind":"store","medium":"disk","name":"Handoff","role":"Names the next step"}],"edges":[{"from":"lane","to":"handoff"}]}`

const sample = "# How a lane resumes\n\nA lane picks up from its newest handoff.\n\n## The handoff\n\nEach handoff names the **next step**.\n\n```arch\n" + sampleDiagram + "\n```\n"

type host struct{ dirs []string }

func (h *host) Serve(_ context.Context, dir string) (string, error) {
	h.dirs = append(h.dirs, dir)
	return "https://mac.tail1.ts.net:8665/", nil
}

type told struct {
	notices  []Notice
	failures []error
}

func (t *told) Announce(_ context.Context, n Notice) error {
	t.notices = append(t.notices, n)
	return nil
}

func (t *told) Failed(_ context.Context, _ string, cause error) error {
	t.failures = append(t.failures, cause)
	return cause
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
	dir := filepath.Join(t.TempDir(), "explainers")
	const remote = "https://github.com/acme/design-docs.git"
	git := &shelltest.Fake{}
	git.Reply("?? one-pagers/lane-resume/index.html\n", "git", "-C", dir, "status")
	git.Reply("", "git")
	served, owner := &host{}, &told{}

	link, err := Explain(context.Background(), Checkout{Runner: git, Remote: remote, Dir: dir}, served, owner, "Lane resume", []byte(sample), time.Date(2026, 11, 3, 11, 10, 0, 0, time.UTC))
	if err != nil {
		t.Fatal(err)
	}

	if link != "https://mac.tail1.ts.net:8665/one-pagers/lane-resume/" {
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
		`<script type="module" src="../../architecture/kit/arch-doc.js"></script>`,
		`<arch-doc src="data:application/json;base64,` + base64.StdEncoding.EncodeToString([]byte(sampleDiagram+"\n")) + `" fill></arch-doc>`,
	} {
		if !strings.Contains(string(body), want) {
			t.Errorf("page lacks %q:\n%s", want, body)
		}
	}
	calls := git.Calls()
	if !ran(calls, "git", "clone", "--quiet", "--depth", "1", "--branch", "main", remote, dir) || !ran(calls, "git", "-C", dir, "switch", "--quiet", "-c", Branch) || !ran(calls, "git", "-C", dir, "commit") {
		t.Fatalf("calls %v", calls)
	}
}

func TestTheCheckoutLivesUnderTheConfiguredWorktreeRootAndOtherwiseUnderClaudesOwn(t *testing.T) {
	const want = "https://github.com/acme/design-docs.git"
	for _, tc := range []struct {
		name string
		git  func(*shelltest.Fake)
		dir  string
	}{
		{"configured", func(f *shelltest.Fake) { f.Reply("/pool/worktrees\n", "git", "config", "ccx.worktreeRoot") }, "/pool/worktrees/design-docs/explainers"},
		{"unset", func(f *shelltest.Fake) { f.Fail(1, "", "git", "config", "ccx.worktreeRoot") }, "/claude/worktrees/design-docs/explainers"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			git := &shelltest.Fake{}
			tc.git(git)

			checkout, err := Locate(context.Background(), git, "acme/monorepo", "/claude")
			if err != nil {
				t.Fatal(err)
			}

			if checkout.Dir != tc.dir || checkout.Remote != want {
				t.Fatalf("checkout %+v", checkout)
			}
		})
	}

	git := &shelltest.Fake{}
	git.Fail(128, "fatal: bad config", "git", "config", "ccx.worktreeRoot")
	if _, err := Locate(context.Background(), git, "acme/monorepo", "/claude"); err == nil {
		t.Fatal("a broken git config located a checkout")
	}
}

func TestFirstUseClonesTheExplainersCheckoutItselfAndCommitsThePageOnItsBranch(t *testing.T) {
	for key, value := range map[string]string{"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.DevNull, "GIT_CONFIG_NOSYSTEM": "1"} {
		t.Setenv(key, value)
	}
	origin := t.TempDir()
	runner := shell.Exec{}
	in := func(dir string, args ...string) string {
		t.Helper()
		result, err := runner.Run(context.Background(), shell.Command{Argv: append([]string{"git", "-C", dir}, args...)})
		if err != nil {
			t.Fatal(err)
		}
		return strings.TrimSpace(result.Stdout)
	}
	in(origin, "init", "--quiet", "-b", "main")
	kit := filepath.Join(origin, "one-pagers", "kit", "one-pager.css")
	if err := os.MkdirAll(filepath.Dir(kit), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(kit, []byte("body{}"), 0o600); err != nil {
		t.Fatal(err)
	}
	in(origin, "add", ".")
	in(origin, "commit", "--quiet", "-m", "kit")
	dir := filepath.Join(t.TempDir(), "pool", "design-docs", "explainers")
	checkout := Checkout{Runner: runner, Remote: "file://" + origin, Dir: dir}

	changed, err := checkout.Write(context.Background(), "Lane resume", "lane-resume", []byte("<html>"))
	if err != nil || !changed {
		t.Fatalf("changed %v, err %v", changed, err)
	}

	if branch := in(dir, "branch", "--show-current"); branch != Branch {
		t.Fatalf("branch %q", branch)
	}
	if committed := in(dir, "ls-files", "one-pagers"); committed != "one-pagers/kit/one-pager.css\none-pagers/lane-resume/index.html" {
		t.Fatalf("committed %q", committed)
	}
	if status := in(dir, "status", "--porcelain"); status != "" {
		t.Fatalf("status %q", status)
	}
	if changed, err := checkout.Write(context.Background(), "Lane resume", "lane-resume", []byte("<html>")); err != nil || changed {
		t.Fatalf("second write changed %v, err %v", changed, err)
	}
}

func TestAPageThatCannotBeHostedFilesADefectToTheMetaDeskAndFailsTheVerb(t *testing.T) {
	git := &shelltest.Fake{}
	git.Fail(128, "fatal: repository not found", "git", "clone")
	sent := &posts{}
	owner := Owner{Post: sent.Post, Desk: func() (string, error) { return "meta-desk-2", nil }, Drive: "d1", Lane: "owner-relay"}
	checkout := Checkout{Runner: git, Remote: "https://github.com/acme/design-docs.git", Dir: filepath.Join(t.TempDir(), "explainers")}

	link, err := Explain(context.Background(), checkout, &host{}, owner, "Lane resume", []byte(sample), time.Now())

	var exit *shell.ExitError
	if link != "" || !errors.As(err, &exit) {
		t.Fatalf("link %q, err %v", link, err)
	}
	if len(sent.drafts) != 1 {
		t.Fatalf("posted %+v", sent.drafts)
	}
	filed := sent.drafts[0]
	if filed.Kind != "defect" || filed.Topic != Topic || !slices.Equal(filed.To, []string{"meta-desk-2"}) || !strings.Contains(filed.Text, "repository not found") {
		t.Fatalf("filed %+v", filed)
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

	if url != "https://mac.tail1.ts.net:8665/" || len(asked) != 2 {
		t.Fatalf("url %q asked %v", url, asked)
	}
	if !ran(run.Calls(), "tailscale", "serve", "--bg", "--https=8665", "http://127.0.0.1:8664") {
		t.Fatalf("calls %v", run.Calls())
	}
	plist, err := os.ReadFile(filepath.Join(home, "Library", "LaunchAgents", Label+".plist"))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(plist), "<string>/claude/long-running/current/bin/lr</string>\n\t\t<string>explain</string>\n\t\t<string>serve</string>\n\t\t<string>--dir</string>\n\t\t<string>/claude/long-running/explainers</string>\n\t\t<string>--addr</string>\n\t\t<string>127.0.0.1:8664</string>") {
		t.Fatalf("plist:\n%s", plist)
	}
}

func TestAnnouncingRecordsThePageForTheDashboardWhoeverPublishesIt(t *testing.T) {
	notice := Notice{Title: "How a lane resumes", Lede: "A lane picks up from its newest handoff.", Link: "https://mac.tail1.ts.net:8665/one-pagers/lane-resume/"}
	for _, test := range []struct {
		lane, recordedAs string
		to               []string
	}{
		{lane: "", recordedAs: Topic, to: []string{"owner-relay"}},
		{lane: "signal-1", recordedAs: "signal-1", to: []string{"owner-relay"}},
		{lane: "owner-relay", recordedAs: "owner-relay"},
	} {
		slack := &shelltest.Fake{}
		slack.Reply(`{"ok": true}`, "cc-slack", "dm-status")
		sent := &posts{}

		if err := (Owner{Runner: slack, Post: sent.Post, Drive: "d1", Lane: test.lane}).Announce(context.Background(), notice); err != nil {
			t.Fatal(err)
		}

		if len(sent.drafts) != 1 || len(slack.Calls()) != 1 {
			t.Fatalf("lane %q: posted %+v, calls %v", test.lane, sent.drafts, slack.Calls())
		}
		got := sent.drafts[0]
		if got.Lane != test.recordedAs || !slices.Equal(got.To, test.to) || got.Attrs["one-pager-url"] != notice.Link || got.Attrs["one-pager-title"] != notice.Title || got.Attrs["one-pager-lede"] != notice.Lede {
			t.Fatalf("lane %q: posted %+v", test.lane, got)
		}
	}
}

func TestAnnouncingAPageTellsOwnerRelayAndCardsTheOwnersDMAndAFailedCardRaisesABlocker(t *testing.T) {
	notice := Notice{Title: "How a lane resumes", Lede: "A lane picks up from its newest handoff.", Link: "https://mac.tail1.ts.net:8665/one-pagers/lane-resume/"}
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
	if len(card) != 4 || card[2] != "--card" || card[3] != `{"body":"A lane picks up from its newest handoff.","links":[{"label":"Open the one-pager","url":"https://mac.tail1.ts.net:8665/one-pagers/lane-resume/"}],"title":"How a lane resumes"}` {
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
	if len(sent.drafts) != 2 || sent.drafts[1].Kind != "blocker" || !slices.Equal(sent.drafts[1].To, []string{"root"}) || !strings.Contains(sent.drafts[1].Text, notice.Link) {
		t.Fatalf("posted %+v", sent.drafts)
	}
}
