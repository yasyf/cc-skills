package lanes

import (
	"context"
	"fmt"
	"io"
	"log/slog"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bkread"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	pageBuild   = "https://buildkite.com/forge/release/builds/700"
	pageChannel = "C0PAGES"
)

type pageFixture struct {
	watch    *PageWatch
	bus      *fakeBus
	history  string
	finished string
	calls    [][]string
}

func newPageWatch(t *testing.T) *pageFixture {
	t.Helper()
	f := &pageFixture{bus: &fakeBus{}, history: `{"messages":[]}`, finished: `{}`}
	f.watch = &PageWatch{
		Env: Env{Bus: f.bus, Config: config.Config{Drive: testDrive, Checkout: "/co"}, Claude: "/claude", Runner: scripted(func(argv []string) (shell.Result, error) {
			f.calls = append(f.calls, argv)
			switch argv[0] {
			case "cc-slack":
				return shell.Result{Stdout: f.history}, nil
			case "/co/tools/ci":
				return shell.Result{Stdout: f.finished}, nil
			case tokenTool:
				return shell.Result{Stdout: "read-token\n"}, nil
			}
			return shell.Result{}, nil
		})},
		Store: memory(t), Channels: []string{pageChannel}, BuildCLI: "tools/ci bk api /pipelines/{pipeline}/builds/{number}",
		Token: bkread.Token{Command: []string{tokenTool}},
		Log:   slog.New(slog.NewTextHandler(io.Discard, nil)),
	}
	return f
}

func (f *pageFixture) released(at time.Time) {
	f.bus.tail = []client.Record{{Seq: 9, Lane: "release-6", Kind: "release", At: at, Text: "started " + pageBuild + " as releases/2026-10-31/4"}}
}

func pageHistory(at time.Time, user, text string) string {
	return fmt.Sprintf(`{"messages":[{"ts":"%.6f","text":%q,"permalink":"https://slack/p1","user":%q,"user_name":"Wadsworth"}]}`, float64(at.UnixMicro())/1e6, text, user)
}

func (f *pageFixture) check(t *testing.T, now time.Time) []client.Draft {
	t.Helper()
	if err := f.watch.Check(context.Background(), now); err != nil {
		t.Fatal(err)
	}
	return f.bus.posted
}

func (f *pageFixture) readHistory() bool {
	return slices.ContainsFunc(f.calls, func(argv []string) bool { return argv[0] == "cc-slack" })
}

func TestNoReleaseOpensNoPageWatch(t *testing.T) {
	f := newPageWatch(t)
	f.history = pageHistory(t0, "U1", "the api is down")

	if posted := f.check(t, t0.Add(mins(1))); len(posted) != 0 || f.readHistory() {
		t.Fatalf("posted = %+v, read history = %v", posted, f.readHistory())
	}
}

func TestAPageDuringALanesReleaseIsOneIncidentToRoot(t *testing.T) {
	f := newPageWatch(t)
	f.released(t0)
	f.history = pageHistory(t0.Add(mins(2)), "U1", "the api is down\nmore")

	posted := f.check(t, t0.Add(mins(3)))
	if len(posted) != 1 || string(posted[0].Kind) != "incident" || !slices.Equal(posted[0].To, []string{"root"}) || posted[0].URL != "https://slack/p1" {
		t.Fatalf("posted = %+v", posted)
	}
	if !strings.HasPrefix(posted[0].Text, "PAGE https://slack/p1 | a drive release is live") || !strings.HasSuffix(posted[0].Text, "Wadsworth: the api is down") {
		t.Fatalf("text = %q", posted[0].Text)
	}

	if again := f.check(t, t0.Add(mins(4))); len(again) != 1 {
		t.Fatalf("the same page posted again: %+v", again)
	}
}

func TestOnlyAPersonOrALiveOncallCardIsAPage(t *testing.T) {
	cases := map[string]struct {
		user, text string
		page       bool
	}{
		"a person":      {"U1", "is prod ok", true},
		"a live card":   {"", ":rotating_light: *Run queue backlog not draining*", true},
		"a retro card":  {"", ":memo: *Retro posted*", false},
		"before it ran": {"U1", "old", false},
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			f := newPageWatch(t)
			f.released(t0)
			at := t0.Add(mins(1))
			if name == "before it ran" {
				at = t0.Add(-mins(1))
			}
			f.history = pageHistory(at, c.user, c.text)
			if posted := f.check(t, t0.Add(mins(2))); (len(posted) == 1) != c.page {
				t.Fatalf("posted = %+v", posted)
			}
		})
	}
}

func TestThePageWatchRunsThirtyMinutesPastTheBuildsEnd(t *testing.T) {
	f := newPageWatch(t)
	f.released(t0)
	end := t0.Add(mins(10))
	f.finished = fmt.Sprintf(`{"finished_at":%q}`, end.Format(time.RFC3339))
	f.history = pageHistory(end.Add(mins(29)), "U1", "after it finished")

	if posted := f.check(t, end.Add(mins(29)+time.Second)); len(posted) != 1 {
		t.Fatalf("posted = %+v", posted)
	}

	f.calls = nil
	f.check(t, end.Add(mins(31)))
	if f.readHistory() {
		t.Fatal("read the channel past the tail")
	}
}

func TestABuildIsClaimedForItsLaneOnce(t *testing.T) {
	f := newPageWatch(t)
	f.released(t0)

	f.check(t, t0.Add(mins(1)))
	f.watch.builds = nil
	f.check(t, t0.Add(mins(2)))

	var claims int
	for _, argv := range f.calls {
		if argv[0] == "cci" {
			claims++
			if !slices.Equal(argv, []string{"cci", "claim", "--for", "release-6@" + testDrive, "--build", pageBuild, "--rel", "started"}) {
				t.Fatalf("claim = %v", argv)
			}
		}
	}
	if claims != 1 {
		t.Fatalf("claims = %d", claims)
	}
	if !slices.ContainsFunc(f.calls, func(argv []string) bool {
		return argv[0] == "/claude/long-running/current/bin/drive.py" && slices.Contains(argv, "2026-10-31/4")
	}) {
		t.Fatalf("the registry was not told: %v", f.calls)
	}
}

func TestAStoodDownLaneIsNotClaimedFor(t *testing.T) {
	f := newPageWatch(t)
	f.released(t0)
	f.bus.peers = []client.Peer{{Lane: "release-6", StoodDown: true}}

	f.check(t, t0.Add(mins(1)))

	if slices.ContainsFunc(f.calls, func(argv []string) bool { return argv[0] == "cci" }) {
		t.Fatalf("claimed for a stood-down lane: %v", f.calls)
	}
}
