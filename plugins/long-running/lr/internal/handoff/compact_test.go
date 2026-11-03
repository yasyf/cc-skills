package handoff

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const screenWidth = 80

var (
	rule       = strings.Repeat("─", screenWidth)
	modeLine   = "  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents"
	idleScreen = []string{"⏺ Done.", rule, "❯ ", rule, ljust(modeLine, screenWidth), rjust("447569 tokens", screenWidth-2)}
)

func ljust(text string, width int) string {
	return text + strings.Repeat(" ", max(0, width-len([]rune(text))))
}

func rjust(text string, width int) string {
	return strings.Repeat(" ", max(0, width-len([]rune(text)))) + text
}

func writeTranscript(t *testing.T, data []byte) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "t.jsonl")
	if err := os.WriteFile(path, data, 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func appendTranscript(t *testing.T, path string, data []byte) {
	t.Helper()
	file, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = file.Close() }()
	if _, err := file.Write(data); err != nil {
		t.Fatal(err)
	}
}

func assertCompacted(t *testing.T, cursor *compactionCursor, want bool) {
	t.Helper()
	got, err := cursor.compacted()
	if err != nil {
		t.Fatal(err)
	}
	if got != want {
		t.Fatalf("compacted %v, want %v", got, want)
	}
}

func cursorAtEnd(t *testing.T, path string) *compactionCursor {
	t.Helper()
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	return newCompactionCursor(path, info.Size())
}

func TestCompactJobStopsOnceTheSessionCompacts(t *testing.T) {
	path := writeTranscript(t, []byte(`{"type":"assistant"}`+"\n"))
	cursor := cursorAtEnd(t, path)
	assertCompacted(t, cursor, false)

	appendTranscript(t, path, []byte(`{"type":"system","subtype":"compact_boundary","content":"Conversation compacted"}`+"\n"))

	assertCompacted(t, cursor, true)
	assertCompacted(t, cursor, true)
}

func TestCompactJobReadsAppendedTranscriptBytesOnce(t *testing.T) {
	path := writeTranscript(t, []byte(`{"type":"assistant"}`+"\n"))
	cursor := cursorAtEnd(t, path)
	appendTranscript(t, path, bytes.Repeat([]byte("x"), 256))
	assertCompacted(t, cursor, false)

	file, err := os.OpenFile(path, os.O_WRONLY, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = file.Close() }()
	if _, err := file.WriteAt(compactBoundary, 64); err != nil {
		t.Fatal(err)
	}

	assertCompacted(t, cursor, false)
}

func TestCompactJobFindsBoundarySplitBetweenPolls(t *testing.T) {
	for _, split := range []int{1, 13, len(compactBoundary) - 1} {
		t.Run(strconv.Itoa(split), func(t *testing.T) {
			path := writeTranscript(t, compactBoundary)
			cursor := cursorAtEnd(t, path)
			appendTranscript(t, path, compactBoundary[:split])
			assertCompacted(t, cursor, false)
			appendTranscript(t, path, compactBoundary[split:])
			assertCompacted(t, cursor, true)
		})
	}
}

func TestCompactJobFindsBoundarySplitBetweenReadChunks(t *testing.T) {
	path := writeTranscript(t, append(bytes.Repeat([]byte("x"), readChunk-13), compactBoundary...))

	assertCompacted(t, newCompactionCursor(path, 0), true)
}

func TestCompactJobRestartsItsOriginalOffsetAfterRotationOrTruncation(t *testing.T) {
	for _, replace := range []bool{false, true} {
		t.Run(strconv.FormatBool(replace), func(t *testing.T) {
			path := writeTranscript(t, []byte("before\n"))
			cursor := cursorAtEnd(t, path)
			appendTranscript(t, path, bytes.Repeat([]byte("x"), 1024))
			assertCompacted(t, cursor, false)
			data := []byte("before\n")
			if replace {
				data = append(data, bytes.Repeat([]byte("y"), 2048)...)
			}
			data = append(data, compactBoundary...)
			if replace {
				staged := path + ".new"
				if err := os.WriteFile(staged, data, 0o600); err != nil {
					t.Fatal(err)
				}
				if err := os.Rename(staged, path); err != nil {
					t.Fatal(err)
				}
			} else if err := os.WriteFile(path, data, 0o600); err != nil {
				t.Fatal(err)
			}
			assertCompacted(t, cursor, true)
		})
	}
}

func screenOf(tail ...string) terminalScreen {
	return terminalScreen{Source: "screen", Tail: tail}
}

func TestOnlyThePlainEmptyPromptIsIdle(t *testing.T) {
	agentList := append(append([]string{}, idleScreen...), "  ⏺ main", ljust("  ◯ fix-core                     Fix hacks: core", screenWidth-12)+"3h 54m 14s", "  ↓ 19 more")
	question := []string{rule, " ☐ Test page", "│ Should I kick off the end-to-end test page now?", "❯ 1. Kick it off now (Recommended)", "  2. After release starts work again", "  3. Type something.", rule, "  4. Chat about this", "Enter to select · ↑/↓ to navigate · Esc to cancel"}
	permission := []string{"⏺ Computer Use[request_access]", rule, "  Computer Use wants to control these apps", "     ◉ Slack", "   ❯ Deny, and tell Claude what to do differently (esc)", "     Allow for this session (1 app)", "  Enter to confirm · Esc to cancel"}
	trust := []string{rule, " Accessing workspace:", " Quick safety check: Is this a project you created or one you trust?", " ❯ No, exit", "   Yes, I trust this folder", " Enter to confirm · Esc to cancel"}
	drafted := screenOf(idleScreen...)
	drafted.Draft = json.RawMessage(`"/compact  <optional custom summarization instructions>"`)
	unavailable := screenOf(idleScreen...)
	unavailable.Source = "screen-unavailable"
	for name, tc := range map[string]struct {
		terminal terminalScreen
		idle     bool
	}{
		"idle":               {screenOf(idleScreen...), true},
		"labelled rule":      {screenOf("⏺ Done.", string([]rune(rule)[:screenWidth-12])+" ultracode ─", "❯", rule, "  ⏸ manual mode on"), true},
		"no break space":     {screenOf("⏺ done", rule, "❯ ", rule), true},
		"notice below":       {screenOf("❯ update the plan again", "  ⎿ ok", rule, "❯", rule, modeLine, "", rjust("new task? /clear to save 448.8k tokens", screenWidth-2)), true},
		"update notice":      {screenOf(rule, "❯", rule, "  ⏸ plan mode on (shift+tab to cycle)", rjust("current: 2.1.287 · latest: 2.1.288 ✔ Update installed", screenWidth-2)), true},
		"agent list":         {screenOf(agentList...), false},
		"question":           {screenOf(question...), false},
		"permission":         {screenOf(permission...), false},
		"trust":              {screenOf(trust...), false},
		"command picker":     {screenOf(rule, "❯", rule, modeLine, "  /compact          Clear conversation history but keep a summary"), false},
		"typed prompt":       {screenOf(rule, "❯ half-typed ask", rule, modeLine), false},
		"two typed lines":    {screenOf(rule, "❯ first line", "  second line", rule, modeLine), false},
		"numbered picker":    {screenOf("❯ 1. My other session owns it", "  2. Go ahead"), false},
		"draft":              {drafted, false},
		"screen unavailable": {unavailable, false},
	} {
		t.Run(name, func(t *testing.T) {
			if got := idlePrompt(tc.terminal); got != tc.idle {
				t.Fatalf("idle %v, want %v", got, tc.idle)
			}
		})
	}
}

type orcaFake struct {
	wait     func() bool
	screen   string
	onRead   func()
	sent     []string
	polls    int
	readSeen int
}

func (o *orcaFake) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	switch c.Argv[2] {
	case "wait":
		o.polls++
		if !o.wait() {
			return shell.Result{Code: 1}, &shell.ExitError{Argv: c.Argv, Code: 1}
		}
	case "read":
		o.readSeen++
		if o.onRead != nil {
			o.onRead()
		}
		return shell.Result{Stdout: o.screen}, nil
	case "send":
		o.sent = append(o.sent, c.Argv[6])
	}
	return shell.Result{}, nil
}

func encodedScreen(t *testing.T, tail ...string) string {
	t.Helper()
	data, err := json.Marshal(map[string]any{"result": map[string]any{"terminal": screenOf(tail...)}})
	if err != nil {
		t.Fatal(err)
	}
	return string(data)
}

func testJob(fake *orcaFake) compactJob {
	moment := time.Date(2026, 10, 10, 22, 0, 0, 0, time.UTC)
	return compactJob{
		runner: fake,
		now:    func() time.Time { return moment },
		sleep:  func(wait time.Duration) { moment = moment.Add(wait) },
	}
}

func TestTheJobSendsCompactAtTheFirstIdlePrompt(t *testing.T) {
	fake := &orcaFake{wait: func() bool { return true }, screen: encodedScreen(t, idleScreen...)}

	if err := testJob(fake).run(context.Background(), "term", "/compact keep it", writeTranscript(t, []byte("x\n"))); err != nil {
		t.Fatal(err)
	}

	equal(t, fake.sent, []string{"/compact keep it"})
}

func TestTheJobNeverSendsToABusyTerminalAndStopsAtItsDeadline(t *testing.T) {
	fake := &orcaFake{wait: func() bool { return false }}

	if err := testJob(fake).run(context.Background(), "term", "/compact", writeTranscript(t, []byte("x\n"))); err != nil {
		t.Fatal(err)
	}

	equal(t, len(fake.sent), 0)
	equal(t, fake.polls, int(compactDeadline/pollInterval))
}

func TestTheJobWaitsPastAScreenThatIsNotThePlainPrompt(t *testing.T) {
	fake := &orcaFake{wait: func() bool { return true }, screen: encodedScreen(t, rule, "❯ half-typed ask", rule)}

	if err := testJob(fake).run(context.Background(), "term", "/compact", writeTranscript(t, []byte("x\n"))); err != nil {
		t.Fatal(err)
	}

	equal(t, len(fake.sent), 0)
	equal(t, fake.readSeen, int(compactDeadline/pollInterval))
}

func TestTheJobSendsNothingOnceTheSessionCompactsWhileItLooks(t *testing.T) {
	path := writeTranscript(t, []byte("x\n"))
	fake := &orcaFake{wait: func() bool { return true }, screen: encodedScreen(t, idleScreen...)}
	fake.onRead = func() { appendTranscript(t, path, compactBoundary) }

	if err := testJob(fake).run(context.Background(), "term", "/compact", path); err != nil {
		t.Fatal(err)
	}

	equal(t, len(fake.sent), 0)
	equal(t, fake.readSeen, 1)
}
