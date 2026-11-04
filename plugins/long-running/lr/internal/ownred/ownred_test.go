package ownred_test

import (
	"context"
	"encoding/json"
	"fmt"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ownred"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

const (
	sources     = "lr/internal/lanes/sources.go"
	sourcesTest = "lr/internal/lanes/sources_test.go"
	prealloc    = "Consider preallocating messages with capacity 1 (prealloc)"
	ownLint     = "OWN-RED #7 lr-go " + sources + ":336 " + prealloc
)

type annotation struct {
	path    string
	line    int
	message string
}

func checkRun(status, conclusion string, notes ...annotation) map[string]any {
	nodes := make([]map[string]any, 0, len(notes))
	for _, note := range notes {
		nodes = append(nodes, map[string]any{"path": note.path, "location": map[string]any{"start": map[string]any{"line": note.line}}, "message": note.message})
	}
	return map[string]any{"name": "lr-go", "status": status, "conclusion": conclusion, "databaseId": 9, "annotations": map[string]any{"nodes": nodes}}
}

var (
	ownRed       = checkRun("COMPLETED", "FAILURE", annotation{sources, 336, prealloc + "\nmore detail"})
	inheritedRed = checkRun("COMPLETED", "FAILURE", annotation{"lr/internal/asks/command.go", 12, "QF1008: could remove embedded field (staticcheck)"})
	pending      = checkRun("IN_PROGRESS", "", annotation{sources, 336, prealloc})
	green        = checkRun("COMPLETED", "SUCCESS")
	testFailure  = checkRun("COMPLETED", "FAILURE", annotation{".github", 140, "Process completed with exit code 1."})
)

func pull(runs ...map[string]any) map[string]any {
	suite := map[string]any{"app": map[string]any{"slug": "github-actions"}, "checkRuns": map[string]any{"nodes": runs}}
	return map[string]any{
		"files":   map[string]any{"nodes": []map[string]any{{"path": sources}, {"path": sourcesTest}}},
		"commits": map[string]any{"nodes": []map[string]any{{"commit": map[string]any{"checkSuites": map[string]any{"nodes": []any{suite}}}}}},
	}
}

func encoded(t *testing.T, v any) string {
	t.Helper()
	out, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return string(out)
}

func stack() map[string]any {
	pulls := make([]map[string]any, 0, 4)
	for number, state := range []string{"closed", "open", "open", "open"} {
		pulls = append(pulls, map[string]any{"number": number + 5, "state": state})
	}
	return map[string]any{"url": "https://api.github.com/repos/acme/app/stacks/42", "pull_requests": pulls}
}

func github(t *testing.T, pulls map[int]map[string]any, stacked bool) *shelltest.Fake {
	t.Helper()
	fake := &shelltest.Fake{}
	repository := map[string]any{}
	for number, p := range pulls {
		repository[fmt.Sprintf("p%d", number)] = p
	}
	fake.Reply(encoded(t, map[string]any{"data": map[string]any{"repository": repository}}), "gh", "api", "graphql")
	fake.Reply(`{"number":7,"url":"https://github.com/acme/app/pull/7"}`, "gh", "pr", "view")
	fake.Fail(1, "gh: Not Found (HTTP 404)", "gh", "api", "repos/{owner}/{repo}/stacks/7")
	fake.Reply(encoded(t, stack()), "gh", "api", "repos/{owner}/{repo}/stacks/42")
	listed := []any{}
	if stacked {
		listed = append(listed, stack())
	}
	fake.Reply(encoded(t, listed), "gh", "api", "repos/acme/app/stacks?pull_request=7")
	return fake
}

func lines(t *testing.T, fake *shelltest.Fake, m ownred.Merge) []string {
	t.Helper()
	got, err := ownred.Guard{Runner: fake}.Lines(context.Background(), m)
	if err != nil {
		t.Fatal(err)
	}
	return got
}

func TestEveryMergePathNamesItsOwnRedAndPassesAnInheritedRedAndAPendingCheck(t *testing.T) {
	paths := map[string]ownred.Merge{
		"gh pr merge":    {Selector: "7"},
		"ccx vcs merge":  {Selector: "7", Mode: ownred.Below},
		"gh stack merge": {Selector: "7", Mode: ownred.Stack},
	}
	checks := map[string]struct {
		run  map[string]any
		want []string
	}{
		"own red":       {ownRed, []string{ownLint}},
		"inherited red": {inheritedRed, []string{"INHERITED #7 lr-go"}},
		"pending":       {pending, nil},
		"green":         {green, nil},
	}
	for path, merge := range paths {
		for name, check := range checks {
			t.Run(path+"/"+name, func(t *testing.T) {
				fake := github(t, map[int]map[string]any{6: pull(green), 7: pull(check.run)}, true)
				if got := lines(t, fake, merge); !slices.Equal(got, check.want) {
					t.Fatalf("got %q, want %q", got, check.want)
				}
			})
		}
	}
}

func TestAStackedMergeReadsEveryOpenPRBelowItsTargetAndNoneAbove(t *testing.T) {
	pulls := map[int]map[string]any{5: pull(ownRed), 6: pull(ownRed), 7: pull(green), 8: pull(ownRed)}
	for _, mode := range []ownred.Mode{ownred.Below, ownred.Stack} {
		got := lines(t, github(t, pulls, true), ownred.Merge{Selector: "7", Mode: mode})
		if want := []string{"OWN-RED #6 lr-go " + sources + ":336 " + prealloc}; !slices.Equal(got, want) {
			t.Fatalf("mode %d: got %q, want %q", mode, got, want)
		}
	}
}

func TestAStackMergeOfAStackNumberOrOfNoSelectorReadsTheWholeStack(t *testing.T) {
	pulls := map[int]map[string]any{6: pull(green), 7: pull(green), 8: pull(ownRed)}
	want := []string{"OWN-RED #8 lr-go " + sources + ":336 " + prealloc}
	for _, selector := range []string{"42", ""} {
		if got := lines(t, github(t, pulls, true), ownred.Merge{Selector: selector, Mode: ownred.Stack}); !slices.Equal(got, want) {
			t.Fatalf("selector %q: got %q, want %q", selector, got, want)
		}
	}
	if got := lines(t, github(t, pulls, true), ownred.Merge{Mode: ownred.Below}); len(got) != 0 {
		t.Fatalf("ccx vcs merge of the current branch read above it: %q", got)
	}
}

func TestAPROutsideAnyStackMergesAlone(t *testing.T) {
	got := lines(t, github(t, map[int]map[string]any{7: pull(ownRed)}, false), ownred.Merge{Selector: "7", Mode: ownred.Below})
	if want := []string{ownLint}; !slices.Equal(got, want) {
		t.Fatalf("got %q, want %q", got, want)
	}
}

func TestATestFailureThatAnnotatesNoFileIsReadFromItsJobLog(t *testing.T) {
	ownLog := "2026-11-03T04:10:55.1971800Z ok  \tlr/internal/asks\n2026-11-03T04:10:55.1980294Z \x1b[31m    sources_test.go:42: got 2 messages, want 1\x1b[0m\n"
	inheritedLog := "2026-11-03T04:10:55.1980294Z     command_test.go:9: want ask line\n"
	for log, want := range map[string][]string{
		ownLog:       {"OWN-RED #7 lr-go " + sourcesTest + ":42 sources_test.go:42: got 2 messages, want 1"},
		inheritedLog: {"INHERITED #7 lr-go"},
	} {
		fake := github(t, map[int]map[string]any{7: pull(testFailure)}, false)
		fake.Reply("      --allow-escape-sequences   Allow terminal escape sequences in the output\n", "gh", "api", "--help")
		fake.Reply(log, "gh", "api", "repos/acme/app/actions/jobs/9/logs", "--allow-escape-sequences")
		if got := lines(t, fake, ownred.Merge{Selector: "7"}); !slices.Equal(got, want) {
			t.Fatalf("got %q, want %q", got, want)
		}
	}
}

func TestAGhWithoutTheEscapeFlagReadsTheJobLogWithoutIt(t *testing.T) {
	fake := github(t, map[int]map[string]any{7: pull(testFailure)}, false)
	fake.Reply("  -X, --method string   The HTTP method for the request\n", "gh", "api", "--help")
	fake.Reply("2026-11-03T04:10:55.1980294Z     sources_test.go:42: got 2 messages, want 1\n", "gh", "api", "repos/acme/app/actions/jobs/9/logs")
	got := lines(t, fake, ownred.Merge{Selector: "7"})
	if want := []string{"OWN-RED #7 lr-go " + sourcesTest + ":42 sources_test.go:42: got 2 messages, want 1"}; !slices.Equal(got, want) {
		t.Fatalf("got %q, want %q", got, want)
	}
	for _, call := range fake.Calls() {
		if slices.Contains(call.Argv, "--allow-escape-sequences") {
			t.Fatalf("an old gh got the flag it exits on: %q", call.Argv)
		}
	}
}

func bus(t *testing.T, records ...map[string]any) string {
	t.Helper()
	var out strings.Builder
	for _, r := range records {
		r["num"] = fmt.Sprintf("#%v", r["seq"])
		out.WriteString(encoded(t, r) + "\n")
	}
	return out.String()
}

func reads(fake *shelltest.Fake) [][]string {
	var argvs [][]string
	for _, call := range fake.Calls() {
		if call.Argv[0] == "cci" {
			argvs = append(argvs, call.Argv)
		}
	}
	return argvs
}

func TestAMergeByALaneRefusesOnARelayItHasNotReadAndNotOnOneItsReadClosed(t *testing.T) {
	relay := map[string]any{"seq": 360037, "lane": "meta-desk-2", "kind": "relay", "text": "hold #2188,\ndo not merge", "to": []string{"fix-1"}}
	answered := map[string]any{"seq": 360038, "lane": "root", "kind": "ask", "text": "which head?", "to": []string{"fix-1"}, "status": "closed"}
	broadcast := map[string]any{"seq": 360040, "lane": "root", "kind": "note", "text": "dev is green"}
	fake := github(t, map[int]map[string]any{7: pull(green)}, false)
	fake.Reply(bus(t, relay, answered, broadcast), "cci", "tail")
	got := lines(t, fake, ownred.Merge{Selector: "7", Drive: "d", Lane: "fix-1"})
	want := []string{
		"UNREAD #360037 meta-desk-2 relay: hold #2188, do not merge",
		"MAIL #360038 root ask: which head?",
		"MAIL #360040 root note: dev is green",
	}
	if !slices.Equal(got, want) {
		t.Fatalf("got %q, want %q", got, want)
	}
	argvs := reads(fake)
	if !slices.Equal(argvs[0], []string{"cci", "tail", "--drive", "d", "--cursor", "fix-1", "--reader", "fix-1", "--json", "--budget", "2147483647"}) {
		t.Fatalf("the merge read the bus as %q, not whole through the lane's own cursor", argvs)
	}
	if !slices.Equal(argvs[1], []string{"cci", "tail", "--drive", "d", "--lane", "fix-1", "--since", "#360037", "--json", "--budget", "2147483647"}) {
		t.Fatalf("the merge read the lane's own records as %q, not whole from the oldest record it delivered", argvs)
	}
}

func TestAMergeListsNoStatusRecordUnlessItIsAddressedToTheLane(t *testing.T) {
	evidence := map[string]any{"seq": 1, "lane": "progress-desk-2", "kind": "evidence", "text": "its build is running"}
	peer := map[string]any{"seq": 2, "lane": "registry", "kind": "peer", "text": "dar-x lane"}
	state := map[string]any{"seq": 3, "lane": "root", "kind": "state", "text": "SPAWNED x"}
	addressed := map[string]any{"seq": 4, "lane": "root", "kind": "state", "text": "stop", "to": []string{"fix-1"}}
	note := map[string]any{"seq": 5, "lane": "root", "kind": "note", "text": "dev is green"}
	fake := github(t, map[int]map[string]any{7: pull(green)}, false)
	fake.Reply(bus(t, evidence, peer, state, addressed, note), "cci", "tail")
	got := lines(t, fake, ownred.Merge{Selector: "7", Drive: "d", Lane: "fix-1"})
	want := []string{"UNREAD #4 root state: stop", "MAIL #5 root note: dev is green"}
	if !slices.Equal(got, want) {
		t.Fatalf("got %q, want %q", got, want)
	}
}

func TestAMergeCountsOnlyWhatAnotherLaneSentAndTheLaneHasNotActedOn(t *testing.T) {
	note := func() map[string]any {
		return map[string]any{"seq": 100, "lane": "meta-desk-2", "kind": "note", "text": "take #2188", "to": []string{"fix-1"}}
	}
	unread := "UNREAD #100 meta-desk-2 note: take #2188"
	read := "MAIL #100 meta-desk-2 note: take #2188"
	for name, c := range map[string]struct {
		delivered []map[string]any
		own       []map[string]any
		want      []string
	}{
		"its own record addressed to itself": {
			delivered: []map[string]any{{"seq": 101, "lane": "fix-1", "kind": "note", "text": "for me", "to": []string{"fix-1"}}},
		},
		"its own reply to its own record": {
			delivered: []map[string]any{{"seq": 102, "lane": "fix-1", "kind": "correction", "text": "fixed", "re": 101, "to": []string{"root"}}},
		},
		"its own records around another lane's": {
			delivered: []map[string]any{{"seq": 99, "lane": "fix-1", "kind": "note", "text": "for me", "to": []string{"fix-1"}}, note()},
			want:      []string{unread},
		},
		"a record it has not acted on": {
			delivered: []map[string]any{note()},
			want:      []string{unread},
		},
		"a record it answered": {
			delivered: []map[string]any{note()},
			own:       []map[string]any{{"seq": 110, "lane": "fix-1", "kind": "answer", "text": "taken", "re": 100, "to": []string{"meta-desk-2"}}},
			want:      []string{read},
		},
		"a record it resolved": {
			delivered: []map[string]any{note()},
			own:       []map[string]any{{"seq": 110, "lane": "fix-1", "kind": "done", "text": "taken", "resolves": 100}},
			want:      []string{read},
		},
		"a record it resolved among others": {
			delivered: []map[string]any{note()},
			own:       []map[string]any{{"seq": 110, "lane": "fix-1", "kind": "done", "text": "taken", "resolves": 98, "also_resolves": []int{100}}},
			want:      []string{read},
		},
		"a record another lane resolved": {
			delivered: []map[string]any{note(), {"seq": 111, "lane": "root", "kind": "note", "text": "moot", "resolves": 100}},
			want:      []string{read, "MAIL #111 root note: moot"},
		},
		"a record another lane only answered": {
			delivered: []map[string]any{note(), {"seq": 111, "lane": "root", "kind": "answer", "text": "seen", "re": 100}},
			want:      []string{unread, "MAIL #111 root answer: seen"},
		},
	} {
		t.Run(name, func(t *testing.T) {
			fake := github(t, map[int]map[string]any{7: pull(green)}, false)
			fake.Reply(bus(t, c.own...), "cci", "tail", "--drive", "d", "--lane", "fix-1")
			fake.Reply(bus(t, c.delivered...), "cci", "tail")
			if got := lines(t, fake, ownred.Merge{Selector: "7", Drive: "d", Lane: "fix-1"}); !slices.Equal(got, c.want) {
				t.Fatalf("got %q, want %q", got, c.want)
			}
		})
	}
}

func TestAMergeListsAllItsUnreadAndCutsOnlyItsBroadcasts(t *testing.T) {
	records := make([]map[string]any, 0, 60)
	var want []string
	for seq := 1; seq <= 30; seq++ {
		records = append(records, map[string]any{"seq": seq, "lane": "root", "kind": "go", "text": "take it", "to": []string{"fix-1"}})
		want = append(want, fmt.Sprintf("UNREAD #%d root go: take it", seq))
	}
	for seq := 31; seq <= 60; seq++ {
		records = append(records, map[string]any{"seq": seq, "lane": "root", "kind": "note", "text": "dev is green"})
	}
	want = append(want, "MAIL #31 root note: dev is green", "MAIL 9 more after #31 not shown; read them with cci tail --drive d --reader fix-1 --since #31 --oldest")
	for seq := 41; seq <= 60; seq++ {
		want = append(want, fmt.Sprintf("MAIL #%d root note: dev is green", seq))
	}
	fake := github(t, map[int]map[string]any{7: pull(green)}, false)
	fake.Reply(bus(t, records...), "cci", "tail")
	if got := lines(t, fake, ownred.Merge{Selector: "7", Drive: "d", Lane: "fix-1"}); !slices.Equal(got, want) {
		t.Fatalf("got %q, want %q", got, want)
	}
}

func TestAMergeShowsEveryBroadcastUpToTheCut(t *testing.T) {
	records := make([]map[string]any, 0, 21)
	var want []string
	for seq := 1; seq <= 21; seq++ {
		records = append(records, map[string]any{"seq": seq, "lane": "root", "kind": "note", "text": "dev is green"})
		want = append(want, fmt.Sprintf("MAIL #%d root note: dev is green", seq))
	}
	fake := github(t, map[int]map[string]any{7: pull(green)}, false)
	fake.Reply(bus(t, records...), "cci", "tail")
	if got := lines(t, fake, ownred.Merge{Selector: "7", Drive: "d", Lane: "fix-1"}); !slices.Equal(got, want) {
		t.Fatalf("got %q, want %q", got, want)
	}
}

func TestAMergeAfterTheLaneReadItsRecordsComesDownToItsChecks(t *testing.T) {
	fake := github(t, map[int]map[string]any{7: pull(ownRed)}, false)
	fake.Reply("", "cci", "tail")
	if got := lines(t, fake, ownred.Merge{Selector: "7", Drive: "d", Lane: "fix-1"}); !slices.Equal(got, []string{ownLint}) {
		t.Fatalf("got %q, want %q", got, []string{ownLint})
	}
}

func TestAMergeOutsideAnyLaneNeverReadsTheBus(t *testing.T) {
	fake := github(t, map[int]map[string]any{7: pull(green)}, false)
	lines(t, fake, ownred.Merge{Selector: "7"})
	if argvs := reads(fake); len(argvs) != 0 {
		t.Fatalf("an unbound merge read the bus: %q", argvs)
	}
}

func TestTheLaneReadTakesTheSameCallsAtOneRecordAndAtAThousand(t *testing.T) {
	counts := map[int]int{}
	for _, n := range []int{1, 1000} {
		records := make([]map[string]any, 0, n)
		for seq := range n {
			records = append(records, map[string]any{"seq": seq + 1, "lane": "root", "kind": "note", "text": "dev is green", "to": []string{"fix-1"}})
		}
		fake := github(t, map[int]map[string]any{7: pull(green)}, false)
		fake.Reply(bus(t, records...), "cci", "tail")
		if got := lines(t, fake, ownred.Merge{Selector: "7", Drive: "d", Lane: "fix-1"}); len(got) != n {
			t.Fatalf("n=%d: got %d lines", n, len(got))
		}
		counts[n] = len(fake.Calls())
	}
	if counts[1] != counts[1000] {
		t.Fatalf("calls grew with the bus: %v", counts)
	}
}

const (
	redeclared    = "internal/owner/watch_test.go:18:2: his redeclared in this block"
	redeclaredLog = "2026-11-04T18:35:40.5345492Z ##[error]vet: " + redeclared + "\n"
	ownRedeclared = "OWN-RED #7 lr-go internal/owner/watch_test.go:18 vet: " + redeclared
	unreadBase    = "INHERITED #7 lr-go (base branch state unread)"
)

func ancestor(runs ...map[string]any) map[string]any {
	suite := map[string]any{"checkRuns": map[string]any{"nodes": runs}}
	return map[string]any{"checkSuites": map[string]any{"nodes": []any{suite}}}
}

func withHistory(p map[string]any, history ...map[string]any) map[string]any {
	head := p["commits"].(map[string]any)["nodes"].([]map[string]any)[0]["commit"].(map[string]any)
	head["parents"] = map[string]any{"nodes": []any{map[string]any{"history": map[string]any{"nodes": history}}}}
	return p
}

var (
	baseRed     = checkRun("COMPLETED", "FAILURE")
	baseGreen   = checkRun("COMPLETED", "SUCCESS")
	baseOff     = checkRun("COMPLETED", "CANCELLED")
	basePending = checkRun("IN_PROGRESS", "")
)

func logReads(fake *shelltest.Fake, log string) {
	fake.Reply("", "gh", "api", "--help")
	fake.Reply(log, "gh", "api", "repos/acme/app/actions/jobs/9/logs")
}

func redFromLog(t *testing.T, log string, history ...map[string]any) *shelltest.Fake {
	t.Helper()
	fake := github(t, map[int]map[string]any{7: withHistory(pull(testFailure), history...)}, false)
	logReads(fake, log)
	return fake
}

func queries(fake *shelltest.Fake) []string {
	var read []string
	for _, call := range fake.Calls() {
		if len(call.Argv) > 2 && call.Argv[2] == "graphql" {
			read = append(read, call.Argv[4])
		}
	}
	return read
}

func TestARedeclarationOutsideTheDiffIsOwnRedWhenTheBaseIsGreenAndInheritedWhenItIsRed(t *testing.T) {
	paths := map[string]ownred.Merge{
		"gh pr merge":    {Selector: "7"},
		"ccx vcs merge":  {Selector: "7", Mode: ownred.Below},
		"gh stack merge": {Selector: "7", Mode: ownred.Stack},
	}
	bases := map[string]struct {
		base map[string]any
		want []string
	}{
		"base green": {baseGreen, []string{ownRedeclared}},
		"base red":   {baseRed, []string{"INHERITED #7 lr-go"}},
	}
	for path, merge := range paths {
		for name, base := range bases {
			t.Run(path+"/"+name, func(t *testing.T) {
				fake := github(t, map[int]map[string]any{6: pull(green), 7: withHistory(pull(testFailure), ancestor(base.base))}, true)
				logReads(fake, redeclaredLog)
				if got := lines(t, fake, merge); !slices.Equal(got, base.want) {
					t.Fatalf("got %q, want %q", got, base.want)
				}
			})
		}
	}
}

func TestTheBaseReadsAsTheNearestFinishedRunOfTheSameCheck(t *testing.T) {
	otherCheck := checkRun("COMPLETED", "FAILURE")
	otherCheck["name"] = "publish"
	for name, c := range map[string]struct {
		history []map[string]any
		want    []string
	}{
		"a skip-ci merge base over a green run": {[]map[string]any{ancestor(), ancestor(otherCheck), ancestor(baseGreen)}, []string{ownRedeclared}},
		"a skip-ci merge base over a red run":   {[]map[string]any{ancestor(), ancestor(baseRed)}, []string{"INHERITED #7 lr-go"}},
		"green nearer than an older red":        {[]map[string]any{ancestor(baseGreen), ancestor(baseRed)}, []string{ownRedeclared}},
		"red nearer than an older green":        {[]map[string]any{ancestor(baseRed), ancestor(baseGreen)}, []string{"INHERITED #7 lr-go"}},
		"cancelled and pending runs read past":  {[]map[string]any{ancestor(baseOff), ancestor(basePending), ancestor(baseGreen)}, []string{ownRedeclared}},
		"red and green at one commit":           {[]map[string]any{ancestor(baseGreen, baseRed)}, []string{"INHERITED #7 lr-go"}},
		"no finished run in the window":         {[]map[string]any{ancestor(), ancestor(baseOff), ancestor(basePending)}, []string{unreadBase}},
		"no history at all":                     {nil, []string{unreadBase}},
	} {
		t.Run(name, func(t *testing.T) {
			if got := lines(t, redFromLog(t, redeclaredLog, c.history...), ownred.Merge{Selector: "7"}); !slices.Equal(got, c.want) {
				t.Fatalf("got %q, want %q", got, c.want)
			}
		})
	}
}

func TestEveryCompileClassReadsTheBaseAndEveryOtherFailureKeepsTheOutsideReading(t *testing.T) {
	for _, text := range []string{
		"internal/owner/watch.go:18:2: undefined: his",
		"vet: internal/owner/watch.go:18:2: undefined: his",
		"./watch.go:18:2: invalid operation: a + b (mismatched types int and string)",
		"internal/owner/watch.go:18:2: his redeclared in this block (typecheck)",
		"internal/owner/watch.go:18:2: self-assignment of x to x (govet)",
		"src/watch.ts(18,2): error TS2451: Cannot redeclare block-scoped variable 'his'.",
		"src/watch.ts:18:2 - error TS2451: Cannot redeclare block-scoped variable 'his'.",
		"ios/Watch.swift:18:2: error: invalid redeclaration of 'his'",
		"native/watch.c:18:2: fatal error: 'his.h' file not found",
	} {
		t.Run(text, func(t *testing.T) {
			got := lines(t, redFromLog(t, "2026-11-04T18:35:40.5345492Z "+text+"\n", ancestor(baseGreen)), ownred.Merge{Selector: "7"})
			if len(got) != 1 || !strings.HasPrefix(got[0], "OWN-RED #7 lr-go ") || !strings.HasSuffix(got[0], text) {
				t.Fatalf("got %q, want one OWN-RED line ending %q", got, text)
			}
		})
	}
	for _, text := range []string{
		"    command_test.go:9: want ask line",
		"    command_test.go:9: got \"watch.go:18:2: undefined: his\"",
		"\t/home/runner/work/lr/internal/owner/watch.go:18 +0x1d",
		"internal/asks/command.go:12:2: QF1008: could remove embedded field (staticcheck)",
		"ios/Watch.swift:18:2: warning: var never mutated",
	} {
		t.Run(text, func(t *testing.T) {
			fake := redFromLog(t, "2026-11-04T18:35:40.5345492Z "+text+"\n", ancestor(baseGreen))
			if got, want := lines(t, fake, ownred.Merge{Selector: "7"}), []string{"INHERITED #7 lr-go"}; !slices.Equal(got, want) {
				t.Fatalf("got %q, want %q", got, want)
			}
			if read := queries(fake); len(read) != 1 {
				t.Fatalf("a failure outside the compile class read the base: %q", read)
			}
		})
	}
}

func TestATscErrorInTheDiffIsOwnRedWithoutReadingTheBase(t *testing.T) {
	p := pull(testFailure)
	p["files"] = map[string]any{"nodes": []map[string]any{{"path": "src/watch.ts"}}}
	fake := github(t, map[int]map[string]any{7: p}, false)
	logReads(fake, "2026-11-04T18:35:40.5345492Z src/watch.ts(18,2): error TS2451: Cannot redeclare 'his'.\n")
	want := []string{"OWN-RED #7 lr-go src/watch.ts:18 src/watch.ts(18,2): error TS2451: Cannot redeclare 'his'."}
	if got := lines(t, fake, ownred.Merge{Selector: "7"}); !slices.Equal(got, want) {
		t.Fatalf("got %q, want %q", got, want)
	}
	if read := queries(fake); len(read) != 1 {
		t.Fatalf("a red in the diff read the base: %q", read)
	}
}

func annotatedAt(path string, line, column int, message string) map[string]any {
	run := checkRun("COMPLETED", "FAILURE")
	start := map[string]any{"line": line}
	if column > 0 {
		start["column"] = column
	}
	run["annotations"] = map[string]any{"nodes": []map[string]any{{"path": path, "location": map[string]any{"start": start}, "message": message}}}
	return run
}

func TestAnAnnotationReadsItsClassFromItsColumnAndItsLinter(t *testing.T) {
	const outside = "lr/internal/asks/command.go"
	for name, c := range map[string]struct {
		run  map[string]any
		want []string
	}{
		"a compiler annotation": {annotatedAt(outside, 12, 2, "his redeclared in this block"), []string{"OWN-RED #7 lr-go " + outside + ":12 his redeclared in this block"}},
		"a typecheck lint":      {annotatedAt(outside, 12, 0, "his redeclared in this block (typecheck)"), []string{"OWN-RED #7 lr-go " + outside + ":12 his redeclared in this block (typecheck)"}},
		"a test assertion":      {annotatedAt(outside, 12, 0, "got 2 messages, want 1"), []string{"INHERITED #7 lr-go"}},
		"another lint":          {annotatedAt(outside, 12, 2, "could remove embedded field (staticcheck)"), []string{"INHERITED #7 lr-go"}},
	} {
		t.Run(name, func(t *testing.T) {
			fake := github(t, map[int]map[string]any{7: withHistory(pull(c.run), ancestor(baseGreen))}, false)
			if got := lines(t, fake, ownred.Merge{Selector: "7"}); !slices.Equal(got, c.want) {
				t.Fatalf("got %q, want %q", got, c.want)
			}
		})
	}
}

type baseReadFails struct{ *shelltest.Fake }

func (r baseReadFails) Run(ctx context.Context, c shell.Command) (shell.Result, error) {
	if slices.ContainsFunc(c.Argv, func(arg string) bool { return strings.Contains(arg, "...base") }) {
		return shell.Result{}, &shell.ExitError{Argv: c.Argv, Code: 1, Stderr: "gh: rate limit exceeded (HTTP 403)"}
	}
	return r.Fake.Run(ctx, c)
}

func TestABaseItCannotReadKeepsTheOutsideReadingAndSaysSo(t *testing.T) {
	runner := baseReadFails{redFromLog(t, redeclaredLog, ancestor(baseGreen))}
	got, err := ownred.Guard{Runner: runner}.Lines(context.Background(), ownred.Merge{Selector: "7"})
	if err != nil {
		t.Fatal(err)
	}
	if want := []string{unreadBase}; !slices.Equal(got, want) {
		t.Fatalf("got %q, want %q", got, want)
	}
}

func TestEveryPRInAStackReadsItsBaseInOneCall(t *testing.T) {
	calls := map[int]int{}
	for _, numbers := range [][]int{{6}, {6, 7, 8}} {
		pulls := map[int]map[string]any{6: pull(green), 7: pull(green), 8: pull(green)}
		for _, number := range numbers {
			pulls[number] = withHistory(pull(testFailure), ancestor(baseGreen))
		}
		fake := github(t, pulls, true)
		logReads(fake, redeclaredLog)
		lines(t, fake, ownred.Merge{Selector: "42", Mode: ownred.Stack})
		calls[len(numbers)] = len(queries(fake))
	}
	if calls[1] != 2 || calls[3] != 2 {
		t.Fatalf("graphql calls by reds in the stack: %v, want 2 for each", calls)
	}
}

var connection = regexp.MustCompile(`(?:first|last): (\d+)|[{}]`)

func possibleNodes(fragment string) int {
	total, pending, scale := 0, 1, []int{1}
	for _, match := range connection.FindAllStringSubmatch(fragment, -1) {
		switch match[0] {
		case "{":
			scale = append(scale, scale[len(scale)-1]*pending)
			pending = 1
		case "}":
			scale = scale[:len(scale)-1]
		default:
			pending, _ = strconv.Atoi(match[1])
			total += scale[len(scale)-1] * pending
		}
	}
	return total
}

func TestAStackOfTenStaysInsideGitHubsNodeLimit(t *testing.T) {
	fake := redFromLog(t, redeclaredLog, ancestor(baseGreen))
	lines(t, fake, ownred.Merge{Selector: "7"})
	read := queries(fake)
	if len(read) != 2 {
		t.Fatalf("got %d queries, want the checks and the base: %q", len(read), read)
	}
	for _, query := range read {
		_, fragment, _ := strings.Cut(query, "\nfragment ")
		if nodes := possibleNodes(fragment); nodes == 0 || nodes*10 > 500_000 {
			t.Fatalf("a stack of ten asks for %d nodes, past the 500,000 limit: %q", nodes*10, fragment)
		}
	}
}
