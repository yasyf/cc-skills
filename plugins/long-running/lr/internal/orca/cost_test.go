package orca

import (
	"slices"
	"strings"
	"testing"
)

func TestALaunchAboveTheCostDefaultsNeedsTheOwnersPick(t *testing.T) {
	picked := "# brief\nOwner pick: owner:m836\n"
	for _, tt := range []struct {
		name  string
		order Order
		text  string
		want  string
	}{
		{"opus high", Order{Lane: "lane-a", Model: "opus", Effort: "high"}, "# brief\n", ""},
		{"fable", Order{Lane: "lane-a", Model: "fable", Effort: "high", Brief: "/b.md"}, "# brief\n", "fable starts only when the owner named it for this work: add the line `Owner pick: owner:m<row>` naming his words to brief /b.md, or launch `lr launch lane lane-a opus high /b.md`"},
		{"fable model id", Order{Lane: "lane-a", Model: "claude-fable-5-1", Effort: "high"}, "# brief\n", "claude-fable-5-1 starts only"},
		{"astra", Order{Lane: "lane-a", Model: "astra", Effort: "high", Brief: "/b.md"}, "# brief\n", "`lr launch lane lane-a sol high /b.md`"},
		{"astra model id", Order{Lane: "lane-a", Model: "gpt-6-astra", Effort: "high"}, "# brief\n", "gpt-6-astra starts only"},
		{"xhigh", Order{Lane: "lane-a", Model: "opus", Effort: "xhigh"}, "# brief\n", "effort xhigh is above high"},
		{"max", Order{Lane: "lane-a", Model: "sol", Effort: "max"}, "# brief\n", "effort max is above high"},
		{"picked fable xhigh", Order{Lane: "lane-a", Model: "fable", Effort: "xhigh"}, picked, ""},
		{"picked astra", Order{Lane: "lane-a", Model: "astra", Effort: "high"}, picked, ""},
		{"coordinator on fable", Order{Lane: coordinatorLane, Model: "fable", Effort: "high"}, "# brief\n", ""},
		{"incident xhigh", Order{Lane: "api-fix", Model: agentIncident, Effort: "xhigh"}, "# brief\n", "effort xhigh is above high"},
		{"incident opus xhigh", Order{Lane: "api-comms", Model: modelIncidentOpus, Effort: "xhigh"}, "# brief\n", "effort xhigh is above high"},
		{"incident role xhigh", Order{Lane: "api-fix", Model: "gpt-6.1-sol", Effort: "xhigh", Role: agentIncident}, "# brief\n", "effort xhigh is above high"},
		{"incident brief xhigh", Order{Lane: "api-fix", Model: "opus", Effort: "xhigh"}, "ccx: lane=api-fix role=incident effort=xhigh incident=sentry-1\n", "effort xhigh is above high"},
		{"picked incident xhigh", Order{Lane: "api-fix", Model: agentIncident, Effort: "xhigh"}, picked, ""},
		{"a desk at its table effort", Order{Lane: "board-refresher-10", Model: "opus", Effort: "medium"}, "# brief\n", ""},
		{"a desk above its table effort", Order{Lane: "board-refresher-10", Model: "opus", Effort: "high", Brief: "/b.md"}, "# brief\n", "board-refresher-10 is a desk, and the desk table runs it on opus at medium, not high: launch `lr launch lane board-refresher-10 opus medium /b.md`"},
		{"the table beats a pick on a desk", Order{Lane: "fidelity-desk-31", Model: "opus", Effort: "xhigh", Brief: "/b.md"}, picked, "fidelity-desk-31 is a desk"},
		{"a desk on another model than its row", Order{Lane: "drive-watchdog-5", Model: "sonnet", Effort: "low"}, "# brief\n", ""},
		{"a pick with no row", Order{Lane: "lane-a", Model: "fable", Effort: "high"}, "# brief\nOwner pick: he said so\n", "fable starts only"},
	} {
		t.Run(tt.name, func(t *testing.T) {
			faults := tt.order.costFaults(tt.text)
			if tt.want == "" {
				if len(faults) != 0 {
					t.Fatal(faults)
				}
				return
			}
			if len(faults) != 1 || !strings.Contains(faults[0], tt.want) {
				t.Fatal(faults)
			}
		})
	}
}

func TestARawClaudeStartGetsOpusAtHighUnlessItNamesAnother(t *testing.T) {
	for _, tt := range []struct {
		name string
		args []string
		want []string
	}{
		{"bare", nil, []string{"--model", "opus", "--effort", "high"}},
		{"print", []string{"-p", "hi"}, []string{"-p", "hi", "--model", "opus", "--effort", "high"}},
		{"sonnet low", []string{"--model", "sonnet", "--effort=low"}, []string{"--model", "sonnet", "--effort", "low"}},
		{"picked fable", []string{"--owner-pick", "owner:m836", "--model", "fable", "--effort", "xhigh"}, []string{"--model", "fable", "--effort", "xhigh"}},
		{"picked joined", []string{"--model=claude-fable-5-1", "--owner-pick=owner:m12"}, []string{"--model", "claude-fable-5-1", "--effort", "high"}},
	} {
		t.Run(tt.name, func(t *testing.T) {
			start, err := parseClaudeStart(tt.args)
			if err != nil {
				t.Fatal(err)
			}
			argv, err := start.argv()
			if err != nil || !slices.Equal(argv, tt.want) {
				t.Fatal(argv, err)
			}
		})
	}
}

func TestARawClaudeStartAboveTheDefaultsNamesTheCommandThatCarriesHisWords(t *testing.T) {
	for _, tt := range []struct {
		args []string
		want string
	}{
		{[]string{"--model", "fable", "-p", "hi"}, "fable starts only when the owner named it for this work: run `lr launch claude --owner-pick owner:m<row> --model fable -p hi` naming his words, or drop --model for opus"},
		{[]string{"--model=gpt-6-astra"}, "gpt-6-astra starts only"},
		{[]string{"--effort", "max"}, "effort max is above high, which starts only when the owner named it: run `lr launch claude --owner-pick owner:m<row> --effort max` naming his words, or drop --effort for high"},
	} {
		start, err := parseClaudeStart(tt.args)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := start.argv(); err == nil || !strings.Contains(err.Error(), tt.want) {
			t.Fatal(tt.args, err)
		}
	}
	for _, args := range [][]string{{"--owner-pick", "m836"}, {"--owner-pick"}, {"--model"}} {
		if _, err := parseClaudeStart(args); err == nil {
			t.Fatal(args)
		}
	}
}
