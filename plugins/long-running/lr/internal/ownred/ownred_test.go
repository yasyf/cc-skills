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
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/scaletest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

const (
	job           = "https://github.com/acme/app/actions/runs/1/job/9"
	ownLine       = "OWN-RED #7 lr-go " + job
	inheritedLine = "INHERITED #7 lr-go"
	unreadBase    = "BASE-UNREAD #7 lr-go"
)

func checkRun(status, conclusion string) map[string]any {
	return map[string]any{"name": "lr-go", "status": status, "conclusion": conclusion, "detailsUrl": job}
}

func namedRun(name, conclusion string) map[string]any {
	run := checkRun("COMPLETED", conclusion)
	run["name"] = name
	return run
}

var (
	failing = checkRun("COMPLETED", "FAILURE")
	pending = checkRun("IN_PROGRESS", "")
	green   = checkRun("COMPLETED", "SUCCESS")
	baseOff = checkRun("COMPLETED", "CANCELLED")
)

func pull(runs ...map[string]any) map[string]any {
	suite := map[string]any{"checkRuns": map[string]any{"nodes": runs}}
	return map[string]any{
		"commits": map[string]any{"nodes": []map[string]any{{"commit": map[string]any{"checkSuites": map[string]any{"nodes": []any{suite}}}}}},
	}
}

func ancestor(runs ...map[string]any) map[string]any {
	suite := map[string]any{"checkRuns": map[string]any{"nodes": runs}}
	return map[string]any{"checkSuites": map[string]any{"nodes": []any{suite}}}
}

func withHistory(p map[string]any, history ...map[string]any) map[string]any {
	head := p["commits"].(map[string]any)["nodes"].([]map[string]any)[0]["commit"].(map[string]any)
	head["parents"] = map[string]any{"nodes": []any{map[string]any{"history": map[string]any{"nodes": history}}}}
	return p
}

func redOnGreenBase() map[string]any {
	return withHistory(pull(failing), ancestor(green))
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

func TestEveryMergePathReadsARedAgainstItsBase(t *testing.T) {
	paths := map[string]ownred.Merge{
		"gh pr merge":    {Selector: "7"},
		"ccx vcs merge":  {Selector: "7", Mode: ownred.Below},
		"gh stack merge": {Selector: "7", Mode: ownred.Stack},
	}
	checks := map[string]struct {
		pull map[string]any
		want []string
	}{
		"red on a green base": {redOnGreenBase(), []string{ownLine}},
		"red on a red base":   {withHistory(pull(failing), ancestor(failing)), []string{inheritedLine}},
		"red on no base run":  {withHistory(pull(failing)), []string{unreadBase}},
		"pending":             {withHistory(pull(pending), ancestor(green)), nil},
		"green":               {withHistory(pull(green), ancestor(failing)), nil},
	}
	for path, merge := range paths {
		for name, check := range checks {
			t.Run(path+"/"+name, func(t *testing.T) {
				fake := github(t, map[int]map[string]any{6: pull(green), 7: check.pull}, true)
				if got := lines(t, fake, merge); !slices.Equal(got, check.want) {
					t.Fatalf("got %q, want %q", got, check.want)
				}
			})
		}
	}
}

func TestAFailingTestThePRNeverTouchedReadsItsBaseAndNothingElse(t *testing.T) {
	const check = "test (ubuntu-latest, 7)"
	for name, c := range map[string]struct {
		history []map[string]any
		want    string
	}{
		"its base is green": {[]map[string]any{ancestor(namedRun(check, "SUCCESS"))}, "OWN-RED #7 " + check + " " + job},
		"its base is red":   {[]map[string]any{ancestor(namedRun(check, "FAILURE"))}, "INHERITED #7 " + check},
		"its base has none": {nil, "BASE-UNREAD #7 " + check},
	} {
		t.Run(name, func(t *testing.T) {
			fake := github(t, map[int]map[string]any{7: withHistory(pull(namedRun(check, "FAILURE")), c.history...)}, false)
			if got, want := lines(t, fake, ownred.Merge{Selector: "7"}), []string{c.want}; !slices.Equal(got, want) {
				t.Fatalf("got %q, want %q", got, want)
			}
		})
	}
}

func TestAVetFindingReadsItsBaseWhateverFilesThePRChanged(t *testing.T) {
	const found = "internal/reply/reply_test.go"
	vet := checkRun("COMPLETED", "FAILURE")
	vet["annotations"] = map[string]any{"nodes": []map[string]any{{"path": found, "location": map[string]any{"start": map[string]any{"line": 7, "column": 2}}, "message": `vet: "fmt" imported and not used`}}}
	for name, changed := range map[string]string{
		"a PR of text files":              "docs/landing.md",
		"a PR that touched the same file": found,
	} {
		for base, want := range map[string]struct {
			run  map[string]any
			line string
		}{
			"red":   {failing, inheritedLine},
			"green": {green, ownLine},
		} {
			t.Run(name+"/base "+base, func(t *testing.T) {
				p := withHistory(pull(vet), ancestor(want.run))
				p["files"] = map[string]any{"nodes": []map[string]any{{"path": changed}}}
				got := lines(t, github(t, map[int]map[string]any{7: p}, false), ownred.Merge{Selector: "7"})
				if !slices.Equal(got, []string{want.line}) {
					t.Fatalf("got %q, want %q", got, want.line)
				}
			})
		}
	}
}

func inWorkflow(name string, runs ...map[string]any) map[string]any {
	return map[string]any{"workflowRun": map[string]any{"workflow": map[string]any{"name": name}}, "checkRuns": map[string]any{"nodes": runs}}
}

func TestACheckReadsOnlyItsOwnWorkflowOnTheBase(t *testing.T) {
	const check = "test"
	red, passing := namedRun(check, "FAILURE"), namedRun(check, "SUCCESS")
	for name, c := range map[string]struct {
		base []any
		want string
	}{
		"its workflow is green and another of that name is red": {[]any{inWorkflow("long-running CI", red), inWorkflow("live-dashboard CI", passing)}, "OWN-RED #7 test " + job},
		"its workflow is red and another of that name is green": {[]any{inWorkflow("long-running CI", passing), inWorkflow("live-dashboard CI", red)}, "INHERITED #7 test"},
		"only another workflow has that name":                   {[]any{inWorkflow("long-running CI", red)}, "BASE-UNREAD #7 test"},
	} {
		t.Run(name, func(t *testing.T) {
			p := pull()
			head := p["commits"].(map[string]any)["nodes"].([]map[string]any)[0]["commit"].(map[string]any)
			head["checkSuites"] = map[string]any{"nodes": []any{inWorkflow("live-dashboard CI", red)}}
			withHistory(p, map[string]any{"checkSuites": map[string]any{"nodes": c.base}})
			got := lines(t, github(t, map[int]map[string]any{7: p}, false), ownred.Merge{Selector: "7"})
			if !slices.Equal(got, []string{c.want}) {
				t.Fatalf("got %q, want %q", got, c.want)
			}
		})
	}
}

const (
	escapeUsage = "      --allow-escape-sequences   Allow printing terminal escape sequences\n"
	inherited   = "tests/test_fail_open.py::test_every_refusal[go_posts_parse - kept]"
	introduced  = "tests/test_fail_open.py::test_every_refusal[go_posts_parse - new]"
	other       = "tests/test_reply.py::test_reply"
)

func jobLink(id int) string {
	return fmt.Sprintf("https://github.com/acme/app/actions/runs/1/job/%d", id)
}

func shard(name, conclusion string, id int) map[string]any {
	return map[string]any{"name": name, "status": "COMPLETED", "conclusion": conclusion, "detailsUrl": jobLink(id)}
}

func pytestLog(tests ...string) string {
	var log strings.Builder
	log.WriteString("2026-11-06T19:36:17.6245033Z =========================== short test summary info ============================\n")
	for _, test := range tests {
		fmt.Fprintf(&log, "2026-11-06T19:36:17.6246128Z FAILED %s - AssertionError: assert {\n2026-11-06T19:36:17.6247380Z   Extra items in the left set:\n", test)
	}
	fmt.Fprintf(&log, "2026-11-06T19:36:17.6248090Z %d failed, 320 passed in 20.36s\n2026-11-06T19:36:17.6702454Z ##[error]Process completed with exit code 1.\n", len(tests))
	return log.String()
}

func withLogs(fake *shelltest.Fake, usage string, read []string, logs map[int]string) {
	fake.Reply(usage, "gh", "api", "--help")
	for id, log := range logs {
		fake.Reply(log, slices.Concat(read, []string{fmt.Sprintf("repos/acme/app/actions/jobs/%d/logs", id)})...)
	}
}

func TestARedIsItsOwnOnlyWhenATestItFailsPassesOnTheBase(t *testing.T) {
	const pr, base1, base12 = 11, 21, 32
	for name, c := range map[string]struct {
		pr   map[string]any
		base []map[string]any
		logs map[int]string
		want string
	}{
		"a test red on the base in another shard": {
			shard("pytest 1/12", "FAILURE", pr),
			[]map[string]any{shard("pytest 1/12", "SUCCESS", base1), shard("pytest 12/12", "FAILURE", base12)},
			map[int]string{pr: pytestLog(inherited), base12: pytestLog(inherited)},
			"INHERITED #7 pytest 1/12",
		},
		"a test red only on the PR": {
			shard("pytest 1/12", "FAILURE", pr),
			[]map[string]any{shard("pytest 1/12", "SUCCESS", base1), shard("pytest 12/12", "FAILURE", base12)},
			map[int]string{pr: pytestLog(introduced), base12: pytestLog(inherited)},
			"OWN-RED #7 pytest 1/12 " + jobLink(pr) + " " + introduced,
		},
		"a test red only on the PR in a shard red on the base for another": {
			shard("pytest 1/12", "FAILURE", pr),
			[]map[string]any{shard("pytest 1/12", "FAILURE", base1)},
			map[int]string{pr: pytestLog(introduced), base1: pytestLog(other)},
			"OWN-RED #7 pytest 1/12 " + jobLink(pr) + " " + introduced,
		},
		"an inherited test beside one only the PR fails": {
			shard("pytest 1/12", "FAILURE", pr),
			[]map[string]any{shard("pytest 1/12", "SUCCESS", base1), shard("pytest 12/12", "FAILURE", base12)},
			map[int]string{pr: pytestLog(inherited, introduced), base12: pytestLog(inherited)},
			"OWN-RED #7 pytest 1/12 " + jobLink(pr) + " " + introduced,
		},
		"a FAILED line printed before the summary": {
			shard("pytest 1/12", "FAILURE", pr),
			[]map[string]any{shard("pytest 12/12", "FAILURE", base12)},
			map[int]string{pr: "2026-11-06T19:36:17.0000000Z FAILED " + introduced + "\n" + pytestLog(inherited), base12: pytestLog(inherited)},
			"INHERITED #7 pytest 1/12",
		},
		"a colored summary": {
			shard("pytest 1/12", "FAILURE", pr),
			[]map[string]any{shard("pytest 1/12", "SUCCESS", base1), shard("pytest 12/12", "FAILURE", base12)},
			map[int]string{pr: strings.NewReplacer("FAILED", "\x1b[31mFAILED\x1b[0m", "1 failed", "\x1b[31m1 failed\x1b[0m").Replace(pytestLog(inherited)), base12: pytestLog(inherited)},
			"INHERITED #7 pytest 1/12",
		},
		"a summary the job printed before something else failed it": {
			shard("controls 1/3", "FAILURE", pr),
			[]map[string]any{shard("controls 1/3", "SUCCESS", base1), shard("controls 2/3", "FAILURE", base12)},
			map[int]string{
				pr:     strings.Replace(pytestLog(inherited), "##[error]", "control reader-validation failed\n2026-11-06T19:36:18.0000000Z ##[error]", 1),
				base12: pytestLog(inherited),
			},
			"OWN-RED #7 controls 1/3 " + jobLink(pr),
		},
		"a PR log that names no test, its shard green on the base": {
			shard("pytest 1/12", "FAILURE", pr),
			[]map[string]any{shard("pytest 1/12", "SUCCESS", base1), shard("pytest 12/12", "FAILURE", base12)},
			map[int]string{pr: "2026-11-06T19:36:17.0000000Z ##[error]The operation was canceled.\n", base12: pytestLog(inherited)},
			"OWN-RED #7 pytest 1/12 " + jobLink(pr),
		},
		"a PR log that names no test, its shard red on the base": {
			shard("pytest 1/12", "FAILURE", pr),
			[]map[string]any{shard("pytest 1/12", "FAILURE", base1)},
			map[int]string{pr: "", base1: pytestLog(inherited)},
			"INHERITED #7 pytest 1/12",
		},
		"a base log that names no test, its shard red on the base": {
			shard("pytest 1/12", "FAILURE", pr),
			[]map[string]any{shard("pytest 1/12", "FAILURE", base1)},
			map[int]string{pr: pytestLog(introduced), base1: ""},
			"INHERITED #7 pytest 1/12",
		},
	} {
		t.Run(name, func(t *testing.T) {
			fake := github(t, map[int]map[string]any{7: withHistory(pull(c.pr), ancestor(c.base...))}, false)
			withLogs(fake, escapeUsage, []string{"gh", "api", "--allow-escape-sequences"}, c.logs)
			if got := lines(t, fake, ownred.Merge{Selector: "7"}); !slices.Equal(got, []string{c.want}) {
				t.Fatalf("got %q, want %q", got, c.want)
			}
		})
	}
}

func TestALogReadsOnAGhWithAndWithoutTheEscapeFlag(t *testing.T) {
	for name, gh := range map[string]struct {
		usage string
		read  []string
	}{
		"gh 2.97 and later": {escapeUsage, []string{"gh", "api", "--allow-escape-sequences"}},
		"gh 2.79":           {"      --paginate   Make additional HTTP requests to fetch all pages of results\n", []string{"gh", "api"}},
	} {
		t.Run(name, func(t *testing.T) {
			fake := github(t, map[int]map[string]any{7: withHistory(pull(shard("pytest 1/12", "FAILURE", 11)), ancestor(shard("pytest 1/12", "SUCCESS", 21), shard("pytest 12/12", "FAILURE", 32)))}, false)
			withLogs(fake, gh.usage, gh.read, map[int]string{11: pytestLog(inherited), 32: pytestLog(inherited)})
			if got, want := lines(t, fake, ownred.Merge{Selector: "7"}), []string{"INHERITED #7 pytest 1/12"}; !slices.Equal(got, want) {
				t.Fatalf("got %q, want %q", got, want)
			}
		})
	}
}

func TestALogOfManyFailingTestsTakesTheSameCallsAsALogOfOne(t *testing.T) {
	calls := map[int]int{}
	for _, n := range []int{1, scaletest.N()} {
		tests := make([]string, 0, n)
		for i := range n {
			tests = append(tests, fmt.Sprintf("tests/test_scale.py::test_%d", i))
		}
		fake := github(t, map[int]map[string]any{7: withHistory(pull(shard("pytest 1/12", "FAILURE", 11)), ancestor(shard("pytest 12/12", "FAILURE", 32)))}, false)
		withLogs(fake, escapeUsage, []string{"gh", "api", "--allow-escape-sequences"}, map[int]string{11: pytestLog(tests...), 32: pytestLog(tests...)})
		if got, want := lines(t, fake, ownred.Merge{Selector: "7"}), []string{"INHERITED #7 pytest 1/12"}; !slices.Equal(got, want) {
			t.Fatalf("n=%d: got %q, want %q", n, got, want)
		}
		calls[n] = len(fake.Calls())
	}
	if calls[1] != calls[scaletest.N()] {
		t.Fatalf("calls grew with the failing tests: %v", calls)
	}
}

func TestAStackedMergeReadsEveryOpenPRBelowItsTargetAndNoneAbove(t *testing.T) {
	pulls := map[int]map[string]any{5: redOnGreenBase(), 6: redOnGreenBase(), 7: pull(green), 8: redOnGreenBase()}
	for _, mode := range []ownred.Mode{ownred.Below, ownred.Stack} {
		got := lines(t, github(t, pulls, true), ownred.Merge{Selector: "7", Mode: mode})
		if want := []string{"OWN-RED #6 lr-go " + job}; !slices.Equal(got, want) {
			t.Fatalf("mode %d: got %q, want %q", mode, got, want)
		}
	}
}

func TestAStackMergeOfAStackNumberOrOfNoSelectorReadsTheWholeStack(t *testing.T) {
	pulls := map[int]map[string]any{6: pull(green), 7: pull(green), 8: redOnGreenBase()}
	want := []string{"OWN-RED #8 lr-go " + job}
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
	got := lines(t, github(t, map[int]map[string]any{7: redOnGreenBase()}, false), ownred.Merge{Selector: "7", Mode: ownred.Below})
	if want := []string{ownLine}; !slices.Equal(got, want) {
		t.Fatalf("got %q, want %q", got, want)
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

func TestAMergeAfterAStoreDeskRoundTripCountsTheRelayButNotTheDesksAnswer(t *testing.T) {
	ask := map[string]any{"seq": 654, "lane": "fix-1", "kind": "ask", "text": `{"verb":"landing gate","args":["--","51981"],"lane":"fix-1"}`, "to": []string{"store-desk"}, "status": "closed"}
	answer := map[string]any{"seq": 655, "lane": "store-desk", "kind": "answer", "text": "lr landing gate: 1 lines, exit 0", "to": []string{"fix-1"}, "resolves": 654}
	part := map[string]any{"seq": 656, "lane": "store-desk", "kind": "note", "text": "part 2 of 2 of the answer to ask #654", "to": []string{"fix-1"}, "re": 654}
	relay := map[string]any{"seq": 657, "lane": "meta-desk-2", "kind": "relay", "text": "hold #51981", "to": []string{"fix-1"}}
	fake := github(t, map[int]map[string]any{7: pull(green)}, false)
	fake.Reply(bus(t, ask, answer, part, relay), "cci", "tail")
	got := lines(t, fake, ownred.Merge{Selector: "7", Drive: "d", Lane: "fix-1"})
	if want := []string{"UNREAD #657 meta-desk-2 relay: hold #51981"}; !slices.Equal(got, want) {
		t.Fatalf("got %q, want %q", got, want)
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
	fake := github(t, map[int]map[string]any{7: redOnGreenBase()}, false)
	fake.Reply("", "cci", "tail")
	if got := lines(t, fake, ownred.Merge{Selector: "7", Drive: "d", Lane: "fix-1"}); !slices.Equal(got, []string{ownLine}) {
		t.Fatalf("got %q, want %q", got, []string{ownLine})
	}
}

func TestAMergeOutsideAnyLaneNeverReadsTheBus(t *testing.T) {
	fake := github(t, map[int]map[string]any{7: pull(green)}, false)
	lines(t, fake, ownred.Merge{Selector: "7"})
	if argvs := reads(fake); len(argvs) != 0 {
		t.Fatalf("an unbound merge read the bus: %q", argvs)
	}
}

func TestTheLaneReadTakesTheSameCallsAtOneRecordAndAtMany(t *testing.T) {
	counts := map[int]int{}
	for _, n := range []int{1, scaletest.N()} {
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
	if counts[1] != counts[scaletest.N()] {
		t.Fatalf("calls grew with the bus: %v", counts)
	}
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

func TestTheBaseReadsAsTheNearestFinishedRunOfTheSameCheck(t *testing.T) {
	otherCheck := namedRun("publish", "FAILURE")
	for name, c := range map[string]struct {
		history []map[string]any
		want    []string
	}{
		"a skip-ci merge base over a green run": {[]map[string]any{ancestor(), ancestor(otherCheck), ancestor(green)}, []string{ownLine}},
		"a skip-ci merge base over a red run":   {[]map[string]any{ancestor(), ancestor(failing)}, []string{inheritedLine}},
		"green nearer than an older red":        {[]map[string]any{ancestor(green), ancestor(failing)}, []string{ownLine}},
		"red nearer than an older green":        {[]map[string]any{ancestor(failing), ancestor(green)}, []string{inheritedLine}},
		"cancelled and pending runs read past":  {[]map[string]any{ancestor(baseOff), ancestor(pending), ancestor(green)}, []string{ownLine}},
		"red and green at one commit":           {[]map[string]any{ancestor(green, failing)}, []string{inheritedLine}},
		"no finished run in the window":         {[]map[string]any{ancestor(), ancestor(baseOff), ancestor(pending)}, []string{unreadBase}},
		"no history at all":                     {nil, []string{unreadBase}},
	} {
		t.Run(name, func(t *testing.T) {
			fake := github(t, map[int]map[string]any{7: withHistory(pull(failing), c.history...)}, false)
			if got := lines(t, fake, ownred.Merge{Selector: "7"}); !slices.Equal(got, c.want) {
				t.Fatalf("got %q, want %q", got, c.want)
			}
		})
	}
}

func TestAPRWithNoRedNeverReadsTheBase(t *testing.T) {
	fake := github(t, map[int]map[string]any{7: pull(green, pending)}, false)
	lines(t, fake, ownred.Merge{Selector: "7"})
	if read := queries(fake); len(read) != 1 {
		t.Fatalf("a PR with no red read the base: %q", read)
	}
}

type baseReadFails struct{ *shelltest.Fake }

func (r baseReadFails) Run(ctx context.Context, c shell.Command) (shell.Result, error) {
	if slices.ContainsFunc(c.Argv, func(arg string) bool { return strings.Contains(arg, "...base") }) {
		return shell.Result{}, &shell.ExitError{Argv: c.Argv, Code: 1, Stderr: "gh: rate limit exceeded (HTTP 403)"}
	}
	return r.Fake.Run(ctx, c)
}

func TestABaseItCannotReadSaysSoRatherThanRefusing(t *testing.T) {
	runner := baseReadFails{github(t, map[int]map[string]any{7: redOnGreenBase()}, false)}
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
			pulls[number] = redOnGreenBase()
		}
		fake := github(t, pulls, true)
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
	fake := github(t, map[int]map[string]any{7: redOnGreenBase()}, false)
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
