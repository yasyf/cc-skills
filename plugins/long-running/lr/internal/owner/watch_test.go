package owner

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"slices"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

const (
	hisEvent = `{"type":"slack.message","channel_id":"D0B6Z","thread_ts":"1.0","ts":"1.1","user":"U1","from_claude":false,"text":"why no reply"}`
	bot      = `{"type":"slack.message","channel_id":"D0B6Z","thread_ts":"1.0","ts":"1.2","user":"","from_claude":true,"text":"a lane's card"}`
	asClaude = `{"type":"slack.message","channel_id":"D0B6Z","thread_ts":"1.0","ts":"1.5","user":"U1","from_claude":false,"text":"_(Yasyf's Claude)_ sent through his login"}`
	someone  = `{"type":"slack.message","channel_id":"C1","thread_ts":"1.0","ts":"1.3","user":"U2","from_claude":false,"text":"a teammate"}`
	tapped   = `{"type":"slack.action","channel_id":"D0B6Z","ts":"1.4","label":"Land"}`
	started  = "owner Slack watch running for U1: each of his messages gets :eyes: and wakes this window\n"
)

type streamed struct {
	*shelltest.Fake
	lines []string
	ended error
}

func (s streamed) Run(ctx context.Context, c shell.Command) (shell.Result, error) {
	if !slices.Equal(c.Argv, []string{"cc-slack", "watch"}) {
		return s.Fake.Run(ctx, c)
	}
	if _, err := io.WriteString(c.Stdout, strings.Join(s.lines, "\n")+"\n"); err != nil {
		return shell.Result{}, err
	}
	return shell.Result{}, s.ended
}

func watched(lines ...string) streamed {
	fake := &shelltest.Fake{}
	fake.Reply(`{"user_id":"U1"}`, "cc-slack", "whoami")
	fake.Reply(`{"ok":true}`, "cc-slack", "react")
	return streamed{Fake: fake, lines: lines}
}

func reactions(fake *shelltest.Fake) [][]string {
	var found [][]string
	for _, call := range fake.Calls() {
		if call.Argv[1] == "react" {
			found = append(found, call.Argv[2:])
		}
	}
	return found
}

func TestHisMessageGetsEyesBeforeItWakesTheWindow(t *testing.T) {
	runner := watched(hisEvent)
	var out bytes.Buffer

	if err := watch(context.Background(), runner, &out); err != nil {
		t.Fatal(err)
	}

	want := [][]string{{"--channel", "D0B6Z", "--ts", "1.1", "--name", "eyes"}}
	if got := reactions(runner.Fake); !slices.EqualFunc(got, want, slices.Equal[[]string]) {
		t.Fatalf("reactions = %q, want eyes on his message", got)
	}
	if out.String() != started+hisEvent+"\n" {
		t.Fatalf("printed %q, want the start line then his event", out.String())
	}
}

func TestOnlyHisOwnWordsGetEyesAndABotsPostWakesNothing(t *testing.T) {
	runner := watched(bot, asClaude, someone, tapped)
	var out bytes.Buffer

	if err := watch(context.Background(), runner, &out); err != nil {
		t.Fatal(err)
	}

	if got := reactions(runner.Fake); len(got) != 0 {
		t.Fatalf("reactions = %q, want none", got)
	}
	if want := started + someone + "\n" + tapped + "\n"; out.String() != want {
		t.Fatalf("printed %q, want a person's message and a tap, with neither bot post", out.String())
	}
}

func TestAFailedReactionStillDeliversHisMessage(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply(`{"user_id":"U1"}`, "cc-slack", "whoami")
	fake.Fail(1, "channel_not_found", "cc-slack", "react")
	var out bytes.Buffer

	if err := watch(context.Background(), streamed{Fake: fake, lines: []string{hisEvent}}, &out); err != nil {
		t.Fatal(err)
	}

	if !strings.Contains(out.String(), "eyes did not land on his message 1.1") || !strings.Contains(out.String(), hisEvent) {
		t.Fatalf("printed %q, want the failure and his message", out.String())
	}
}

func TestAWatchThatDiesEndsTheMonitorWithItsCause(t *testing.T) {
	runner := watched(hisEvent)
	runner.ended = errors.New("daemon gone")

	err := watch(context.Background(), runner, io.Discard)

	if err == nil || !strings.Contains(fmt.Sprint(err), "daemon gone") {
		t.Fatalf("err = %v, want the watch's cause", err)
	}
}
