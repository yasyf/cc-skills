package board

import (
	"context"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
)

type liveHeads struct {
	ccx.Client
	head  string
	reads int
}

func (l *liveHeads) PRStatus(_ context.Context, _ string, numbers []int) ([]ccx.PRStatus, error) {
	l.reads++
	rows := make([]ccx.PRStatus, len(numbers))
	for i, n := range numbers {
		rows[i] = ccx.PRStatus{Number: n, Head: l.head}
	}
	return rows, nil
}

func questionCards(n int, asked string, picked bool) string {
	cards := make([]string, n)
	choices := make([]string, 0, n)
	for i := range cards {
		cards[i] = fmt.Sprintf(`{"id":"card-%d","type":"card","children":[{"id":"pr-%d","type":"pr","repo":%q,"number":%d,"head":%q},{"id":"pick-%d","type":"choice","options":[{"id":"go","label":"Go"}]}]}`, i, i, repo, number+i, asked, i)
		if picked {
			choices = append(choices, fmt.Sprintf(`"pick-%d":{"optionIds":["go"]}`, i))
		}
	}
	return fmt.Sprintf(`{"doc":{"blocks":[%s]},"interactions":{"choices":{%s}}}`, strings.Join(cards, ","), strings.Join(choices, ","))
}

func redraws(pushed []string) []string {
	var cards []string
	for _, block := range pushed {
		if strings.Contains(block, `"type":"pr"`) {
			cards = append(cards, block)
		}
	}
	return cards
}

func passOver(t *testing.T, on *passShell, live *liveHeads) {
	t.Helper()
	pass := Pass{Store: withWords(t), Repo: repo, Runner: on, CCX: live, Session: "s1", Hooks: (&recorded{}).hooks(), Now: func() time.Time { return t0 }}
	if _, err := pass.Run(context.Background(), facts(nil)); err != nil {
		t.Fatal(err)
	}
}

func TestACardWhosePRMovedIsRedrawnAtTheLiveHeadOnce(t *testing.T) {
	on, live := &passShell{outcomes: questionCards(1, head[:7], false)}, &liveHeads{head: moved}
	passOver(t, on, live)
	got := redraws(on.pushed)
	if len(got) != 1 || !strings.Contains(got[0], `"id":"pr-0"`) || !strings.Contains(got[0], `"head":"`+moved[:12]+`"`) {
		t.Fatalf("redraws = %q, want pr-0 once at %s", got, moved[:12])
	}
	on.outcomes = questionCards(1, moved[:12], false)
	passOver(t, on, live)
	if got := redraws(on.pushed); len(got) != 1 {
		t.Fatalf("redraws after the card shows the live head = %q, want still one", got)
	}
}

func TestACardAtTheLiveHeadIsLeftAlone(t *testing.T) {
	on := &passShell{outcomes: questionCards(1, head[:7], false)}
	passOver(t, on, &liveHeads{head: head})
	if got := redraws(on.pushed); len(got) != 0 {
		t.Fatalf("redraws = %q, want none", got)
	}
}

func TestAPickedCardKeepsTheHeadThePickWasMadeOn(t *testing.T) {
	on := &passShell{outcomes: questionCards(1, head[:7], true)}
	passOver(t, on, &liveHeads{head: moved})
	if got := redraws(on.pushed); len(got) != 0 {
		t.Fatalf("redraws = %q, want none: his pick stands at %s and never moves to the new head", got, head[:7])
	}
}

func TestLiveHeadReadsStayFlatFromOneCardToAHundred(t *testing.T) {
	for _, n := range []int{1, 100} {
		on, live := &passShell{outcomes: questionCards(n, head[:7], false)}, &liveHeads{head: moved}
		passOver(t, on, live)
		if live.reads != 1 || len(redraws(on.pushed)) != n {
			t.Fatalf("%d cards: %d reads, %d redraws; want 1 read and %d redraws", n, live.reads, len(redraws(on.pushed)), n)
		}
	}
}
