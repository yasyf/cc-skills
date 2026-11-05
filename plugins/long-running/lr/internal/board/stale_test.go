package board

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const shownHead = "aaaaaaa"

type staleShell struct {
	outcomes string
	calls    [][]string
	pushed   []string
	retired  []string
	pulled   []string
}

func (s *staleShell) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	if c.Argv[1] == "outcomes" {
		return shell.Result{Stdout: s.outcomes}, nil
	}
	s.calls = append(s.calls, c.Argv)
	s.pushed = append(s.pushed, c.Stdin)
	return shell.Result{}, nil
}

func graded(number int, head string, change func(*machine.Facts)) machine.Facts {
	f := machine.Facts{Number: number, Head: head, Lane: "lane-7", Finder: machine.FinderClear, Fidelity: machine.FidelityPass}
	if change != nil {
		change(&f)
	}
	return f
}

func onBoard(rows ...int) string {
	prs := make([]string, len(rows))
	for i, number := range rows {
		prs[i] = fmt.Sprintf(`{"number":%d,"head":%q,"title":"t"}`, number, shownHead)
	}
	return fmt.Sprintf(`{"rev":4,"doc":{"blocks":[{"id":"card-merge-triage","type":"card","children":[{"id":"merge-prs","type":"pr-groups","groups":[{"id":"g","title":"g","prs":[%s]}]}]}]},"interactions":{"choices":{},"triage":{}}}`, strings.Join(prs, ","))
}

func runStale(t *testing.T, outcomes string, snapshot ...machine.Facts) (*staleShell, []DroppedTap) {
	t.Helper()
	on := &staleShell{outcomes: outcomes}
	var said []DroppedTap
	sweep := StaleTaps{Runner: on, Session: "s1", Say: func(_ context.Context, tap DroppedTap) error {
		said = append(said, tap)
		return nil
	}, Retire: func(_ context.Context, card, why string) error {
		if namesID.MatchString(why) {
			t.Fatalf("the owner would read an id: %s", why)
		}
		on.retired = append(on.retired, card)
		return nil
	}, Pull: func(_ context.Context, target, why string) error {
		if namesID.MatchString(why) {
			t.Fatalf("the owner would read an id: %s", why)
		}
		on.pulled = append(on.pulled, target)
		return nil
	}}
	if err := sweep.Pass(context.Background(), snapshot); err != nil {
		t.Fatal(err)
	}
	return on, said
}

func TestATapStaysAtItsHeadAndLeavesWhenTheHeadMovesOrAGradeGoesBad(t *testing.T) {
	cases := map[string]struct {
		facts machine.Facts
		stays bool
	}{
		"its own head and clean grades":     {graded(1, shownHead+"000", nil), true},
		"carried to a patch-identical head": {graded(1, "bbbbbbb", func(f *machine.Facts) { f.CarriedBy = "same-diff" }), true},
		"not graded yet":                    {graded(1, shownHead, func(f *machine.Facts) { f.Finder, f.Fidelity = "", "" }), true},
		"a bad grade with an override":      {graded(1, shownHead, func(f *machine.Facts) { f.Finder, f.FinderOverride = machine.FinderAsk, true }), true},
		"the head moved":                    {graded(1, "bbbbbbb", nil), false},
		"the finder asks":                   {graded(1, shownHead, func(f *machine.Facts) { f.Finder = machine.FinderAsk }), false},
		"the fidelity grade failed":         {graded(1, shownHead, func(f *machine.Facts) { f.Fidelity = machine.FidelityFail }), false},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			on, said := runStale(t, onBoard(1), tc.facts)
			if tc.stays {
				if len(on.calls) != 0 || len(said) != 0 {
					t.Fatalf("wrote %v, said %v", on.calls, said)
				}
				return
			}
			if len(on.calls) != 1 || on.calls[0][1] != "update-block" || len(said) != 1 || said[0].PR != 1 || said[0].Lane != "lane-7" {
				t.Fatalf("wrote %v, said %v", on.calls, said)
			}
			if pulled := len(on.pulled) == 1 && on.pulled[0] == "#1"; pulled == said[0].Moved {
				t.Fatalf("moved %v pulled %v: only a bad grade at the shown head stamps that head withdrawn", said[0].Moved, on.pulled)
			}
		})
	}
}

func TestDroppingOneRowKeepsTheRestAndADroppedLastRowEmptiesItsGroup(t *testing.T) {
	on, _ := runStale(t, onBoard(1, 2), graded(1, "bbbbbbb", nil), graded(2, shownHead, nil))
	var block struct {
		ID     string `json:"id"`
		Groups []struct {
			PRs []struct {
				Number int `json:"number"`
			} `json:"prs"`
		} `json:"groups"`
	}
	if err := json.Unmarshal([]byte(on.pushed[0]), &block); err != nil {
		t.Fatal(err)
	}
	if block.ID != "merge-prs" || len(block.Groups) != 1 || len(block.Groups[0].PRs) != 1 || block.Groups[0].PRs[0].Number != 2 {
		t.Fatalf("block = %s", on.pushed[0])
	}
	on, _ = runStale(t, onBoard(1), graded(1, "bbbbbbb", nil))
	if err := json.Unmarshal([]byte(on.pushed[0]), &block); err != nil || len(block.Groups) != 0 {
		t.Fatalf("block = %s, %v", on.pushed[0], err)
	}
}

func TestARowForAPRNoPassReadsIsLeftAlone(t *testing.T) {
	if on, said := runStale(t, onBoard(1)); len(on.calls) != 0 || len(said) != 0 {
		t.Fatalf("wrote %v, said %v", on.calls, said)
	}
}

func mergeCardBoard(answered bool) string {
	choices := `{}`
	if answered {
		choices = `{"merge:1:aaaaaaa-answer":{"optionIds":["merge"]}}`
	}
	return fmt.Sprintf(`{"rev":4,"doc":{"blocks":[{"id":"merge:1:aaaaaaa","type":"card","children":[{"id":"merge:1:aaaaaaa-answer","type":"choice"}]}]},"interactions":{"choices":%s,"triage":{}}}`, choices)
}

func TestAMergeCardRetiresWhenItsHeadMovesAndAGradeGoingBadWithdrawsItsHead(t *testing.T) {
	on, said := runStale(t, mergeCardBoard(false), graded(1, "bbbbbbb", nil))
	if len(on.retired) != 1 || on.retired[0] != "merge:1:aaaaaaa" || len(on.pulled) != 0 || len(said) != 1 {
		t.Fatalf("retired %v, pulled %v, said %v", on.retired, on.pulled, said)
	}
	on, said = runStale(t, mergeCardBoard(false), graded(1, shownHead, func(f *machine.Facts) { f.Finder = machine.FinderAsk }))
	if len(on.pulled) != 1 || on.pulled[0] != "merge:1:aaaaaaa" || len(on.retired) != 0 || len(said) != 1 {
		t.Fatalf("retired %v, pulled %v, said %v", on.retired, on.pulled, said)
	}
	if on, _ := runStale(t, mergeCardBoard(false), graded(1, shownHead, nil)); len(on.retired)+len(on.pulled) != 0 {
		t.Fatalf("a current card was removed: %v %v", on.retired, on.pulled)
	}
	if on, _ := runStale(t, mergeCardBoard(true), graded(1, "bbbbbbb", nil)); len(on.retired)+len(on.pulled) != 0 {
		t.Fatalf("an answered card was removed: %v %v", on.retired, on.pulled)
	}
}

func TestALaterWriteUsesTheRevTheEarlierOnePrinted(t *testing.T) {
	both := `{"rev":4,"doc":{"blocks":[{"id":"merge-prs","type":"pr-groups","groups":[{"id":"g","prs":[{"number":1,"head":"aaaaaaa"}]}]},{"id":"more-prs","type":"pr-groups","groups":[{"id":"h","prs":[{"number":2,"head":"aaaaaaa"}]}]}]},"interactions":{"choices":{},"triage":{}}}`
	on := &staleRevShell{outcomes: both}
	sweep := StaleTaps{Runner: on, Session: "s1", Say: func(context.Context, DroppedTap) error { return nil }}
	if err := sweep.Pass(context.Background(), []machine.Facts{graded(1, "bbbbbbb", nil), graded(2, "bbbbbbb", nil)}); err != nil {
		t.Fatal(err)
	}
	if len(on.revs) != 2 || on.revs[0] != "4" || on.revs[1] != "5" {
		t.Fatalf("revs = %v", on.revs)
	}
}

type staleRevShell struct {
	outcomes string
	revs     []string
}

func (s *staleRevShell) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	if c.Argv[1] == "outcomes" {
		return shell.Result{Stdout: s.outcomes}, nil
	}
	s.revs = append(s.revs, c.Argv[len(c.Argv)-1])
	return shell.Result{Stdout: "rev: 5\n"}, nil
}

func TestATapLeavingTheBoardSaysWhatChanged(t *testing.T) {
	why, moved := staleWhy(shownHead, machine.Facts{Head: "bbbbbbb", TapReturned: "a behavior change at bbbbbbb: drops chats"})
	if want := "a new version replaced it, so the tap no longer covers it: a behavior change at bbbbbbb: drops chats"; why != want || !moved {
		t.Fatalf("why %q moved %v, want %q", why, moved, want)
	}
}
