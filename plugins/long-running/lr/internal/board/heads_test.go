package board

import (
	"context"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
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

func TestTwoMovedPRsOnOneCardWriteFromTheRevEachLanded(t *testing.T) {
	on := &revShell{passShell: passShell{outcomes: `{"rev":40,"doc":{"blocks":[{"id":"c","type":"card","children":[` +
		`{"id":"a","type":"pr","repo":"o/r","number":1,"head":"` + head[:7] + `"},{"id":"b","type":"pr","repo":"o/r","number":2,"head":"` + head[:7] + `"}]}]},"interactions":{"choices":{}}}`}}
	pass := Pass{Store: withWords(t), Repo: repo, Runner: on, CCX: &liveHeads{head: moved}, Session: "s1", Hooks: (&recorded{}).hooks(), Now: func() time.Time { return t0 }}
	if _, err := pass.Run(context.Background(), facts(nil)); err != nil {
		t.Fatal(err)
	}
	if want := []string{"40", "41"}; len(on.revs) < 2 || on.revs[0] != want[0] || on.revs[1] != want[1] {
		t.Fatalf("--rev of each write = %v, want the read's 40, then the 41 the first write printed", on.revs)
	}
}

func TestARefusedHeadMoveLeavesThePassRunningForTheNextRead(t *testing.T) {
	on := &revShell{refuse: "refusing to write \"card-0\": lane card-mirror-2 wrote it at rev 41, after rev 40 that you read"}
	on.outcomes = questionCards(1, head[:7], false)
	pass := Pass{Store: withWords(t), Repo: repo, Runner: on, CCX: &liveHeads{head: moved}, Session: "s1", Hooks: (&recorded{}).hooks(), Now: func() time.Time { return t0 }}
	if _, err := pass.Run(context.Background(), facts(nil)); err != nil {
		t.Fatalf("a refused head move stopped the pass: %v", err)
	}
}

type revShell struct {
	passShell
	refuse string
	revs   []string
	landed int64
}

func (r *revShell) Run(ctx context.Context, c shell.Command) (shell.Result, error) {
	if c.Argv[1] != "update-block" {
		return r.passShell.Run(ctx, c)
	}
	if r.refuse != "" {
		return shell.Result{Code: 1}, &shell.ExitError{Argv: c.Argv, Code: 1, Stderr: r.refuse}
	}
	r.revs = append(r.revs, c.Argv[len(c.Argv)-1])
	r.landed = max(r.landed, 40) + 1
	return shell.Result{Stdout: fmt.Sprintf("rev: %d\n", r.landed)}, nil
}

