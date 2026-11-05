package board

import (
	"context"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/scaletest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

type answerShell struct {
	calls   int
	answers map[string]string
}

func (a *answerShell) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	a.calls++
	id := c.Argv[len(c.Argv)-2]
	body, found := a.answers[id]
	if !found {
		return shell.Result{}, &shell.ExitError{Stderr: "no such answer"}
	}
	return shell.Result{Stdout: body}, nil
}

func TestABareAnswerIDReadsAsItsWords(t *testing.T) {
	runner := &answerShell{answers: map[string]string{
		"02cd8a5": `{"title":"Compaction by fork?","body":"Owner, Nov 4, verbatim: fork it"}`,
		"366f958": `{"title":"Ship it as a PR?","body":"picked “Ship it now”\nBoard question: Ship it as a PR?"}`,
	}}
	got := NewAnswerWords(runner, "/co").Said(context.Background(), "Plan row: none in the rebuild plan; owner answers 02cd8a5 and 366f958.")
	want := "Plan row: none in the rebuild plan; your answer on “Compaction by fork?”; your answer on “Ship it as a PR?”: Ship it now."
	if got != want {
		t.Fatalf("got %q, want %q", got, want)
	}
}

func TestACiteOfNoAnswerStaysAsWritten(t *testing.T) {
	text := "It follows ruling 0000000 and nothing else."
	if got := NewAnswerWords(&answerShell{}, "/co").Said(context.Background(), text); got != text {
		t.Fatalf("got %q", got)
	}
}

func TestTextWithNoCiteReadsNothing(t *testing.T) {
	runner := &answerShell{}
	NewAnswerWords(runner, "/co").Said(context.Background(), "Checked against the plan by the fidelity review at this version.")
	if runner.calls != 0 {
		t.Fatalf("a line with no cite ran %d reads", runner.calls)
	}
}

func TestOneReadPerAnswerAtAnyNumberOfRows(t *testing.T) {
	for _, rows := range []int{1, scaletest.N()} {
		runner := &answerShell{answers: map[string]string{"02cd8a5": `{"title":"Fork?","body":""}`}}
		words := NewAnswerWords(runner, "/co")
		for range rows {
			if got := words.Said(context.Background(), "owner answer 02cd8a5"); !strings.Contains(got, "Fork?") {
				t.Fatalf("got %q", got)
			}
		}
		if runner.calls != 1 {
			t.Fatalf("%d rows ran %d reads", rows, runner.calls)
		}
	}
}
