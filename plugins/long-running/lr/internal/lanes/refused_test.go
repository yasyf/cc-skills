package lanes

import (
	"context"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

func refusedEdge() []config.Refusal {
	return []config.Refusal{{Match: regexp.MustCompile(`(?i)edge-vendor`), Except: regexp.MustCompile(`(?i)never`), Why: "never chased"}}
}

func TestAnAlertOnARefusedTopicOpensNothingAndItsNeighbourOpensItsIncident(t *testing.T) {
	cases := map[string]struct {
		text     string
		refused  []config.Refusal
		launched bool
	}{
		"refused":   {"Triggered: edge-vendor answers 403 — plat\n" + monitorLink, refusedEdge(), false},
		"spared":    {"Triggered: edge-vendor is never ours — plat\n" + monitorLink, refusedEdge(), true},
		"neighbour": {"Triggered: runtime-api-next logging errors — plat\n" + monitorLink, refusedEdge(), true},
		"no-list":   {"Triggered: edge-vendor answers 403 — plat\n" + monitorLink, nil, true},
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			bus := &fakeBus{}
			env := Env{Bus: bus, Config: config.Config{Drive: testDrive}, MonitorLane: noMonitorLane, Runner: scripted(func([]string) (shell.Result, error) {
				return shell.Result{Stdout: slackHistory(t0.Add(-mins(4)), c.text)}, nil
			})}
			launches := 0
			check := Alerts{Env: env, Store: memory(t), Channels: []string{"C1"}, Log: slog.Default(), Refused: c.refused, Launch: func(context.Context, Fired) (string, error) {
				launches++
				return "/state/lanes/brief.md", nil
			}}
			if err := check.Check(context.Background(), t0); err != nil {
				t.Fatal(err)
			}
			equal(t, launches == 1, c.launched)
			equal(t, len(bus.posted) == 1, c.launched)
		})
	}
}

func TestARepliedAlertUnderARefusedCardOpensNothing(t *testing.T) {
	fired := float64(t0.Add(-mins(4)).UnixNano()) / 1e9
	card := fmt.Sprintf(`{"ts":"%.6f","text":"edge-vendor answers 403","permalink":"https://slack/p1","reply_count":1,"latest_reply":"%.6f"}`, fired, fired+1)
	reply := fmt.Sprintf(`{"ts":"%.6f","text":%q,"permalink":"https://slack/p2"}`, fired+1, "Triggered: it fired again\n"+monitorLink)
	bus := &fakeBus{}
	env := Env{Bus: bus, Config: config.Config{Drive: testDrive}, MonitorLane: noMonitorLane, Runner: scripted(func(argv []string) (shell.Result, error) {
		return shell.Result{Stdout: `{"messages":[` + map[string]string{"history": card, "thread": card + "," + reply}[argv[1]] + `]}`}, nil
	})}
	launches := 0
	check := Alerts{Env: env, Store: memory(t), Channels: []string{"C1"}, Log: slog.Default(), Refused: refusedEdge(), Launch: func(context.Context, Fired) (string, error) {
		launches++
		return "", nil
	}}
	if err := check.Check(context.Background(), t0); err != nil {
		t.Fatal(err)
	}
	equal(t, launches, 0)
	equal(t, len(bus.posted), 0)
}

func TestAPageOnARefusedTopicPostsNothingAndItsNeighbourPosts(t *testing.T) {
	f := newPageWatch(t)
	f.watch.Refused = refusedEdge()
	f.released(t0)
	f.history = pageHistory(t0.Add(mins(2)), "U1", "edge-vendor answers 403")
	if posted := f.check(t, t0.Add(mins(3))); len(posted) != 0 {
		t.Fatalf("a refused page posted: %+v", posted)
	}
	f.history = pageHistory(t0.Add(mins(3)), "U1", "the api is down")
	if posted := f.check(t, t0.Add(mins(4))); len(posted) != 1 || !strings.HasSuffix(posted[0].Text, "the api is down") {
		t.Fatalf("posted = %+v", posted)
	}
}

func TestAPageWithNoListPostsAsBefore(t *testing.T) {
	f := newPageWatch(t)
	f.released(t0)
	f.history = pageHistory(t0.Add(mins(2)), "U1", "edge-vendor answers 403")
	if posted := f.check(t, t0.Add(mins(3))); len(posted) != 1 {
		t.Fatalf("posted = %+v", posted)
	}
}

func TestTheRefusedListComesFromTheDrivesRunnerConfig(t *testing.T) {
	state := t.TempDir()
	if got, err := ReadRefusedBriefs(state); err != nil || got != nil {
		t.Fatalf("no runner.json: %v, %v", got, err)
	}
	path := filepath.Join(state, "runner.json")
	write := func(raw string) {
		t.Helper()
		if err := os.WriteFile(path, []byte(raw), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	write(`{"drive":"brook"}`)
	if got, err := ReadRefusedBriefs(state); err != nil || len(got) != 0 {
		t.Fatalf("no list: %v, %v", got, err)
	}
	write(`{"orca":{"refused_briefs":[{"match":"edge-vendor","why":"never chased"}]}}`)
	got, err := ReadRefusedBriefs(state)
	if err != nil {
		t.Fatal(err)
	}
	if refusal, line, refused := config.RefusedLine(got, "ok\nan edge-vendor 403\nok"); !refused || line != "an edge-vendor 403" || refusal.Why != "never chased" {
		t.Fatalf("a row with no except: %v %q %v", refusal, line, refused)
	}
	if _, _, refused := config.RefusedLine(got, "the api is down"); refused {
		t.Fatal("a neighbour was refused")
	}
	write(`{"orca":{"refused_briefs":[{"match":"("}]}}`)
	if _, err := ReadRefusedBriefs(state); err == nil {
		t.Fatal("a bad pattern loaded")
	}
}
