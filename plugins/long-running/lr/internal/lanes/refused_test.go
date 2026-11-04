package lanes

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"regexp"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

func refusedEdge() []config.Refusal {
	return []config.Refusal{{Match: regexp.MustCompile(`(?i)edge-vendor`), Why: "never chased"}}
}

func TestAnAlertOnARefusedTopicOpensNothingAndItsNeighbourOpensItsIncident(t *testing.T) {
	cases := map[string]struct {
		text     string
		refused  []config.Refusal
		launched bool
	}{
		"refused":   {"Triggered: edge-vendor answers 403 — plat\n" + monitorLink, refusedEdge(), false},
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

func TestTheDropClassesComeFromTheRulingsOwnLabels(t *testing.T) {
	listing := `[{"id":"ff7fca14cc8d","title":"Do we chase Akamai edge 403s?","body":"No. Never a lane, a read, a report, a line to him. Owner, Nov 3.","tags":["akamai","drop-class","match:gsk edge","match:platform.poetic.gsk.com"]}]`
	env := Env{Config: config.Config{Checkout: "/repo"}, Runner: scripted(func(argv []string) (shell.Result, error) {
		equal(t, strings.Join(argv, " "), "ccn -R /repo answer list --label drop-class --limit 0 --json")
		return shell.Result{Stdout: listing}, nil
	})}
	got := env.dropClasses(context.Background(), slog.Default())
	refusal, line, refused := config.RefusedLine(got, "ok\nfailing with timeouts and 403s at the GSK edge\nok")
	if !refused || line != "failing with timeouts and 403s at the GSK edge" {
		t.Fatalf("the card that reached him was not classified: %v %q %v", refusal, line, refused)
	}
	equal(t, refusal.Why, "owner ruling ff7fca1: No. Never a lane, a read, a report, a line to him.")
	if _, _, refused := config.RefusedLine(got, "the api answers 500"); refused {
		t.Fatal("a neighbour was refused")
	}
	if _, _, refused := config.RefusedLine(got, "platform.poeticXgsk.com"); refused {
		t.Fatal("a phrase matched as a pattern")
	}
}

func TestDropClassesThatDoNotReadDropNothing(t *testing.T) {
	env := Env{Config: config.Config{Checkout: "/repo"}, Runner: scripted(func([]string) (shell.Result, error) {
		return shell.Result{}, errors.New("ccn is down")
	})}
	equal(t, len(env.dropClasses(context.Background(), slog.Default())), 0)
}
