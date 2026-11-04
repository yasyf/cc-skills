package screen_test

import (
	"encoding/json"
	"fmt"
	"os"
	"slices"
	"strings"
	"testing"
	"unicode/utf8"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/watch/screen"
)

const (
	width     = 80
	mode      = "  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents"
	hookAsked = "Bash command · from the startup-speed agent / Time the current single-stream fetch with its SHA-256 check on the measurement Sprite as a same-host control / This shell -c script runs rm and could not be checked / Do you want to proceed? / ❯ 1. Yes / 2. No"
)

var (
	rule = strings.Repeat("─", width)
	idle = []string{"⏺ Done.", rule, "❯ ", rule, ljust(mode, width), rjust("447569 tokens", width-2)}

	agentList  = joined(idle, []string{"  ⏺ main", ljust("  ◯ fix-core                     Fix hacks: core", width-12) + "3h 54m 14s", "  ↓ 19 more"})
	askUser    = []string{rule, " ☐ Test page", "│ Should I kick off the end-to-end test page now?", "❯ 1. Kick it off now (Recommended)", "  2. After release starts work again", "  3. Type something.", rule, "  4. Chat about this", "Enter to select · ↑/↓ to navigate · Esc to cancel"}
	permission = []string{"⏺ Computer Use[request_access]", rule, "  Computer Use wants to control these apps", "     ◉ Slack", "   ❯ Deny, and tell Claude what to do differently (esc)", "     Allow for this session (1 app)", "  Enter to confirm · Esc to cancel"}
	trust      = []string{rule, " Accessing workspace:", " Quick safety check: Is this a project you created or one you trust?", " ❯ No, exit", "   Yes, I trust this folder", " Enter to confirm · Esc to cancel"}

	hookApproval  = []string{rule, " Bash command · from the startup-speed agent", " │ Time the current single-stream fetch with its SHA-256 check on the measurement Sprite as a same-host control", " This shell -c script runs rm and could not be checked", " Do you want to proceed?", " ❯ 1. Yes", "   2. No", " Esc to cancel · Tab to amend"}
	agentPanel    = []string{"", "  ⏺ main", ljust("  ◯ startup-speed             Speed up remote lane startup", width), "  ↓ 3 more"}
	codexApproval = []string{"  Would you like to run the following command?", "", "  $ rm -rf build/out", "", "› 1. Yes, proceed (y)", "  2. No, and tell Codex what to do differently (esc)", "", "  Press enter to confirm or esc to cancel"}
	codexWorking  = []string{"• Working (12s • esc to interrupt)", "› Ask Codex to do anything", "  GPT-6.1-Sol xhigh · ~/app", "  ? for shortcuts"}
	printed       = []string{" Do you want to proceed?", " ❯ 1. Yes", "   2. No", " Esc to cancel · Tab to amend"}
	codexIdle     = []string{"• Completed and reported success through Orca.", "  Worked for 4m 35s • 10:10 AM", "› Ask Codex to do anything", "  GPT-6.1-Sol xhigh · ~/app", "  ? for shortcuts"}
	claudeWorking = joined([]string{"· Timing the fetch… (14s · still thinking with xhigh effort)", ""}, idle)
)

func ljust(line string, to int) string {
	return line + strings.Repeat(" ", max(0, to-utf8.RuneCountInString(line)))
}

func rjust(line string, to int) string {
	return strings.Repeat(" ", max(0, to-utf8.RuneCountInString(line))) + line
}

func joined(parts ...[]string) []string {
	return slices.Concat(parts...)
}

func on(tail ...string) screen.Terminal {
	return screen.Terminal{Source: "screen", Tail: tail}
}

func drafted(draft string, tail ...string) screen.Terminal {
	raw, err := json.Marshal(draft)
	if err != nil {
		panic(err)
	}
	return screen.Terminal{Source: "screen", Tail: tail, Draft: raw}
}

func unavailable(tail ...string) screen.Terminal {
	return screen.Terminal{Source: "screen-unavailable", Tail: tail}
}

func TestOnlyThePlainEmptyPromptIsIdle(t *testing.T) {
	cases := []struct {
		terminal screen.Terminal
		idle     bool
	}{
		{on(idle...), true},
		{on("⏺ Done.", string([]rune(rule)[:width-12])+" ultracode ─", "❯", rule, "  ⏸ manual mode on"), true},
		{on("⏺ done", rule, "❯ ", rule), true},
		{on("❯ update the plan again", "  ⎿ ok", rule, "❯", rule, mode, "", rjust("new task? /clear to save 448.8k tokens", width-2)), true},
		{on(rule, "❯", rule, "  ⏸ plan mode on (shift+tab to cycle)", rjust("current: 2.1.287 · latest: 2.1.288 ✔ Update installed", width-2)), true},
		{on(agentList...), false},
		{on(askUser...), false},
		{on(permission...), false},
		{on(trust...), false},
		{on(rule, "❯", rule, mode, "  /compact          Clear conversation history but keep a summary"), false},
		{on(rule, "❯ half-typed ask", rule, mode), false},
		{on(rule, "❯ first line", "  second line", rule, mode), false},
		{on("❯ 1. My other session owns it", "  2. Go ahead"), false},
		{drafted("/compact  <optional custom summarization instructions>", idle...), false},
		{unavailable(idle...), false},
	}
	for i, c := range cases {
		t.Run(fmt.Sprint(i), func(t *testing.T) {
			if got := screen.IdlePrompt(c.terminal); got != c.idle {
				t.Fatalf("IdlePrompt = %v, want %v for %q", got, c.idle, c.terminal.Tail)
			}
		})
	}
}

func TestAWakeIsTypedOnlyAtARestingPrompt(t *testing.T) {
	cases := []struct {
		name     string
		terminal screen.Terminal
		wakeable bool
	}{
		{"claude at its prompt", on(idle...), true},
		{"claude working above an empty prompt, as a relay always typed", on(claudeWorking...), true},
		{"codex at its composer", on(codexIdle...), true},
		{"codex working", on(codexWorking...), false},
		{"codex asking for approval", on(codexApproval...), false},
		{"codex with a draft", drafted("half-typed", codexIdle...), false},
		{"an unreadable screen", unavailable(codexIdle...), false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if got := screen.Wakeable(c.terminal); got != c.wakeable {
				t.Fatalf("Wakeable = %v, want %v for %q", got, c.wakeable, c.terminal.Tail)
			}
		})
	}
}

func TestADialogIsACancelFooterUnderASelectionCursor(t *testing.T) {
	cases := []struct {
		terminal screen.Terminal
		kind     screen.Kind
		asked    string
	}{
		{on(hookApproval...), screen.Approval, hookAsked},
		{on(joined([]string{"⏺ Two lanes report in."}, hookApproval, agentPanel)...), screen.Approval, hookAsked},
		{on(askUser...), screen.Question, "Test page / Should I kick off the end-to-end test page now? / ❯ 1. Kick it off now (Recommended) / 2. After release starts work again / 3. Type something. / 4. Chat about this"},
		{on(rule, " ☐ Rebase", "  1. Rebase now", "❯ 2. Wait", rule, "Enter to select · ↑/↓ to navigate · Esc to cancel"), screen.Question, "Rebase / 1. Rebase now / ❯ 2. Wait"},
		{on(permission...), screen.Approval, "Computer Use wants to control these apps / ◉ Slack / ❯ Deny, and tell Claude what to do differently (esc) / Allow for this session (1 app)"},
		{on(trust...), screen.Approval, "Accessing workspace: / Quick safety check: Is this a project you created or one you trust? / ❯ No, exit / Yes, I trust this folder"},
		{on(codexApproval...), screen.Approval, "Would you like to run the following command? / $ rm -rf build/out / › 1. Yes, proceed (y) / 2. No, and tell Codex what to do differently (esc)"},
	}
	for i, c := range cases {
		t.Run(fmt.Sprint(i), func(t *testing.T) {
			found, open := screen.FindDialog(c.terminal)
			if !open || found.Kind != c.kind || found.Asked != c.asked {
				t.Fatalf("FindDialog = %+v, %v; want %s %q", found, open, c.kind, c.asked)
			}
		})
	}
}

func TestAQuestionWhosePreviewPanePutsItsCursorFarAboveTheFooterIsStillAQuestion(t *testing.T) {
	data, err := os.ReadFile("testdata/screen.coordinator.preview-question.json")
	if err != nil {
		t.Fatal(err)
	}
	var read struct {
		Result struct {
			Terminal screen.Terminal `json:"terminal"`
		} `json:"result"`
	}
	if err := json.Unmarshal(data, &read); err != nil {
		t.Fatal(err)
	}
	found, open := screen.FindDialog(read.Result.Terminal)
	if !open || found.Kind != screen.Question {
		t.Fatalf("FindDialog = %+v, %v", found, open)
	}
	if !strings.HasPrefix(found.Asked, "Reply draft / Post this reply") || !strings.Contains(found.Asked, "/ ❯ 1. Post as drafted ") || !strings.HasSuffix(found.Asked, "/ Chat about this") {
		t.Fatalf("asked = %q", found.Asked)
	}
	if got := screen.InputState(read.Result.Terminal); got != screen.Waiting {
		t.Fatalf("InputState = %q", got)
	}
}

func replaced(tail []string, old, with string) []string {
	out := make([]string, len(tail))
	for i, line := range tail {
		out[i] = strings.ReplaceAll(line, old, with)
	}
	return out
}

func TestTwoCommandsBehindOneWarningAskTwoDifferentThings(t *testing.T) {
	other, _ := screen.FindDialog(on(replaced(hookApproval, "single-stream fetch", "eight-stream fetch")...))
	if want := strings.ReplaceAll(hookAsked, "single-stream fetch", "eight-stream fetch"); other.Asked != want {
		t.Fatalf("asked = %q", other.Asked)
	}
	first, _ := screen.FindDialog(on(codexApproval...))
	second, _ := screen.FindDialog(on(replaced(codexApproval, "build/out", "build/cache")...))
	if first == second {
		t.Fatalf("two commands read alike: %+v", first)
	}
}

func TestAnAutoDenyCountdownIsReadApartAndLeavesWhatTheDialogAsksUnchanged(t *testing.T) {
	at := slices.Index(hookApproval, " Do you want to proceed?")
	for _, left := range []string{"1:58", "0:55"} {
		warned := fmt.Sprintf(" ⚠ Claude Code will automatically deny this request in %s, to avoid blocking progress on an unattended session", left)
		found, open := screen.FindDialog(on(joined(hookApproval[:at], []string{warned}, hookApproval[at:])...))
		if !open || found.Countdown != left || found.Asked != hookAsked || found.Title() != "Bash command · from the startup-speed agent" {
			t.Fatalf("FindDialog = %+v, %v; title %q", found, open, found.Title())
		}
	}
}

func TestATimerTickingAboveAnUnruledDialogLeavesWhatItAsksUnchanged(t *testing.T) {
	bare, _ := screen.FindDialog(on(codexApproval...))
	for _, elapsed := range []string{"12s", "1m 42s"} {
		found, _ := screen.FindDialog(on(joined([]string{fmt.Sprintf("• Working (%s • esc to interrupt)", elapsed)}, codexApproval)...))
		if found != bare {
			t.Fatalf("timer %s: %+v, want %+v", elapsed, found, bare)
		}
	}
}

func TestAFooterAboveTheInputBoxOrWithNoCursorIsNoDialog(t *testing.T) {
	cases := []screen.Terminal{
		on(idle...),
		on(agentList...),
		on(joined(printed, idle)...),
		on(joined(printed, codexWorking)...),
		on(`  25 "Enter to select · ↑/↓ to navigate · Esc to cancel",`, "❯ 1. Yes"),
		on("❯ 1. Yes", "  2. No", "Press esc to cancel the run: it is still going"),
		on(" Do you want to proceed?", "   1. Yes", "   2. No", " Esc to cancel · Tab to amend"),
		unavailable(hookApproval...),
		on(),
	}
	for i, terminal := range cases {
		t.Run(fmt.Sprint(i), func(t *testing.T) {
			if found, open := screen.FindDialog(terminal); open {
				t.Fatalf("FindDialog = %+v", found)
			}
		})
	}
}

func TestTheInputBoxIsClaudesRuledPromptOrCodexsComposerLine(t *testing.T) {
	cases := []struct {
		tail []string
		box  int
	}{
		{idle, 1},
		{agentList, 1},
		{[]string{rule, "❯ half-typed ask", rule, mode}, 0},
		{codexWorking, 1},
		{hookApproval, -1},
		{askUser, -1},
		{[]string{rule, "❯ 1. Yes", "  2. No", rule}, -1},
		{codexApproval, -1},
		{[]string{" Resume Session", "", "   Loading conversations…"}, -1},
	}
	for i, c := range cases {
		t.Run(fmt.Sprint(i), func(t *testing.T) {
			box, found := screen.Composer(c.tail)
			if !found {
				box = -1
			}
			if box != c.box {
				t.Fatalf("Composer = %d, want %d", box, c.box)
			}
		})
	}
}

func TestIdleBusyAndWaitingStayApartForClaudeAndCodex(t *testing.T) {
	cases := []struct {
		terminal screen.Terminal
		state    screen.Input
	}{
		{on(idle...), screen.Idle},
		{on(codexIdle...), screen.Idle},
		{on(claudeWorking...), screen.Busy},
		{on(codexWorking...), screen.Busy},
		{drafted("half a thought", codexIdle...), screen.Busy},
		{on(agentList...), screen.Busy},
		{on(rule, "❯ half-typed ask", rule, mode), screen.Busy},
		{on(codexApproval...), screen.Waiting},
		{on(hookApproval...), screen.Waiting},
		{on(askUser...), screen.Waiting},
		{on(" Resume Session", "", "   Loading conversations…"), screen.NoInput},
		{unavailable(codexIdle...), screen.NoInput},
	}
	for i, c := range cases {
		t.Run(fmt.Sprint(i), func(t *testing.T) {
			if got := screen.InputState(c.terminal); got != c.state {
				t.Fatalf("InputState = %q, want %q", got, c.state)
			}
		})
	}
}
