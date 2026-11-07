package board

import (
	"context"
	"slices"
	"strings"
	"testing"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

func answerAdds(calls []shell.Command) [][]string {
	var adds [][]string
	for _, c := range calls {
		if slices.Equal(c.Argv[:min(5, len(c.Argv))], []string{"ccn", "-R", "/co", "answer", "add"}) {
			adds = append(adds, c.Argv)
		}
	}
	return adds
}

func labelsOf(argv []string) []string {
	var labels []string
	for i, arg := range argv {
		if arg == "--label" && i+1 < len(argv) {
			labels = append(labels, argv[i+1])
		}
	}
	return labels
}

func TestACardApproveAndHoldEachBecomeACCNAnswerNamingThePRHeadAndVerdict(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	ctx := context.Background()
	fake := &shelltest.Fake{}
	fake.Reply("[]", "ccn", "-R", "/co", "answer", "list")
	fake.Reply(`{"id":"a1"}`, "ccn", "-R", "/co", "answer", "add")
	var warned strings.Builder
	hooks := postHooks(ctx, cardAnswers{fake, "/co", &warned}, "d", "pr-cards", repo)

	if err := hooks.Tap(TapRecord{number, head, byOwner, machine.TapTapped}); err != nil {
		t.Fatal(err)
	}
	if err := hooks.Hold(number, head); err != nil {
		t.Fatal(err)
	}

	adds := answerAdds(fake.Calls())
	if len(adds) != 2 || warned.Len() != 0 {
		t.Fatalf("answer adds = %q, warned %q; want one Approve and one Hold, no warning", adds, warned.String())
	}
	for i, verdict := range []string{verdictApproved, verdictHeld} {
		labels := labelsOf(adds[i])
		for _, want := range []string{"pr:#45583", "head:" + head, "verdict:" + verdict, "card-tap:45583/" + head + "/" + verdict} {
			if !slices.Contains(labels, want) {
				t.Errorf("%s answer labels = %v, want %s", verdict, labels, want)
			}
		}
		if title := adds[i][5]; !strings.HasPrefix(title, "#45583 "+verdict) {
			t.Errorf("%s answer title = %q, want it to open on #45583 %s", verdict, title, verdict)
		}
	}
}

func TestACardTapAlreadyInCCNAnswersWritesNoSecondAnswer(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	fake := &shelltest.Fake{}
	fake.Reply(`[{"id":"a1"}]`, "ccn", "-R", "/co", "answer", "list")
	var warned strings.Builder

	if err := postHooks(context.Background(), cardAnswers{fake, "/co", &warned}, "d", "pr-cards", repo).Tap(TapRecord{number, head, byOwner, machine.TapTapped}); err != nil {
		t.Fatal(err)
	}
	if adds := answerAdds(fake.Calls()); len(adds) != 0 || warned.Len() != 0 {
		t.Fatalf("answer adds = %q, warned %q; want none", adds, warned.String())
	}
}

func TestAFailedCCNWriteWarnsAndTheTapStillReachesTheBus(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	ctx := context.Background()
	fake := &shelltest.Fake{}
	fake.Fail(1, "Error: not a cc-notes repo", "ccn", "-R", "/co", "answer", "list")
	var warned strings.Builder

	if err := postHooks(ctx, cardAnswers{fake, "/co", &warned}, "d", "pr-cards", repo).Tap(TapRecord{number, head, byOwner, machine.TapTapped}); err != nil {
		t.Fatalf("tap = %v, want a failed ccn write to leave the tap standing", err)
	}
	if !strings.Contains(warned.String(), "#45583") {
		t.Fatalf("warned %q, want a warning naming #45583", warned.String())
	}
	written, err := client.Tail(ctx, client.Query{Drive: "d", Kinds: []string{"owner"}})
	if err != nil {
		t.Fatal(err)
	}
	if len(written) != 1 {
		t.Fatalf("owner records = %+v, want the tap", written)
	}
}
