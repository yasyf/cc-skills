package grader

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	shaTrunk     = "9d1e2f3a4b5c6d7e8f9011a2b3c4d5e6f7a8b9c0"
	shaLanded    = "4d2ea9dfbad0c1e2f3a4b5c6d7e8f9011a2b3c4d"
	shaParentTip = "3c5e7a9b1d2f4a6c8e0b1d3f5a7c9e1b2d4f6a8c"
	shaParent    = "0b0e1f2a3c4d5e6f708192a3b4c5d6e7f8091a2b"
	shaFixed     = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
	shaOtherArea = "cccccccccccccccccccccccccccccccccccccccc"
	reasons      = "The resubscribe hunks keep the fetched position across a cut, and every caller passes it through unchanged. "
	swept        = reasons + "rows checked: 2"
	rowsFixture  = "Required rows for " + repoName + "#43299 at " + shaHead + "\n\n## Every global, pending and touched row (2)\n\n" +
		"### rule/949e6be | scope | in-force | said 1 | 2026-10-22\n> it should be a ssql replica that is live streaming, not polled\n\n" +
		"### prompt/1026-105052 | reserved | in-force | said 1 | 2026-10-26\n> no credential on disk\nslice-hash: 0123456789ab\n"
	wholeRecord = "over whole e1f2a3b^..7414944"
	summaryBody = "## Summary\nA cut signal resubscribes from the position it fetched."
	fixTitle    = "sand: 🐛 the replica resubscribes from its fetched position"
)

var (
	ownPaths   = []string{"signal/stream.go", "signal/stream_test.go"}
	landedDiff = map[string]string{
		shaParent[:11]:             "diff --git a/api/parent.go b/api/parent.go\n--- a/api/parent.go\n+++ b/api/parent.go\n@@ -0,0 +1 @@\n+landed()\n",
		shaBase + ":api/parent.go": "landed()\n",
	}
	earlierRange = shaOldOwn + "^.." + shaOld + " " + shaOwn + "^.." + shaHead
)

func patchOf(lines ...string) string {
	return "diff --git a/go/follow.go b/go/follow.go\nindex 1111111..2222222 100644\n--- a/go/follow.go\n+++ b/go/follow.go\n@@ -1,3 +1,3 @@\n" + strings.Join(lines, "\n") + "\n"
}

func finderDiff() string {
	var diff strings.Builder
	files := []struct {
		path string
		line int
	}{
		{"go/sand/internal/replica/follow.go", 40},
		{"rust/crates/sandsql-journal/src/replica.rs", 2946},
		{"go/poll.go", 9},
		{"go/sand/file0.go", 1},
		{"go/sand/file1.go", 1},
		{"go/sand/file2.go", 1},
		{"go/sand/file3.go", 1},
	}
	for _, file := range files {
		fmt.Fprintf(&diff, "diff --git a/%[1]s b/%[1]s\n--- a/%[1]s\n+++ b/%[1]s\n@@ -%[2]d,0 +%[2]d,4 @@\n+changed()\n", file.path, file.line)
	}
	return diff.String()
}

func pairLines(marks ...string) string {
	lines := make([]string, 0, 1+len(marks))
	lines = append(lines, fmt.Sprintf(" 1:  %s <  -:  ----------- api: the parent's commit before its restack", shaParent[:11]))
	for offset, mark := range marks {
		lines = append(lines, fmt.Sprintf(" %d:  %s %s  %d:  %s sand: the head's own commit", offset+2, shaOldOwn[:11], mark, offset+1, shaOwn[:11]))
	}
	return strings.Join(lines, "\n") + "\n"
}

type finderRepo struct {
	own            map[string][]string
	resolved       map[string]string
	races          int
	gone           []string
	ranges         map[string]string
	shows          map[string]string
	trunk          string
	history        []string
	pulls          map[string]int
	subjects       []string
	commitSubjects map[string]string
	baseGone       bool
	missing        []string
	raw            string
	landedLog      string
	deleted        []string
}

func newFinderFake(t *testing.T, replies ...any) (*fakeShell, *finderRepo) {
	t.Helper()
	shows := map[string]string{
		shaOldOwn[:11]: patchOf("-poll()", "+stream()"),
		shaOwn[:11]:    patchOf("-poll()", "+stream()", "+resubscribe()"),
	}
	for key, value := range landedDiff {
		shows[key] = value
	}
	repo := &finderRepo{
		own:      map[string][]string{shaBase + ".." + shaHead: {shaOwn}},
		resolved: map[string]string{},
		ranges:   map[string]string{},
		shows:    shows,
		trunk:    shaBase,
	}
	shell := &fakeShell{
		dir:      t.TempDir(),
		config:   t.TempDir(),
		replies:  replies,
		state:    "OPEN",
		body:     summaryBody,
		heads:    []string{shaHead},
		rows:     rowsFixture,
		timeouts: map[string]time.Duration{},
		comments: [][]map[string]any{{
			{"author": map[string]string{"login": "yasyf"}, "body": "## Rulings\n- the resubscribe test pins finder #150321's fix"},
			{"author": map[string]string{"login": "yasyf"}, "body": "The caller trace: every caller of the removed path reads the batched store."},
			{"author": map[string]string{"login": "forge-pr-reviewer"}, "body": "LGTM from the review bot"},
		}},
	}
	shell.gitFn = repo.git
	return shell, repo
}

func (r *finderRepo) git(full []string) (string, error) {
	argv := full[1:]
	if argv[0] == "--no-lazy-fetch" {
		argv = argv[1:]
	}
	switch argv[0] {
	case "ls-remote":
		return r.lsRemote(argv), nil
	case "fetch":
		return r.fetch(full, argv)
	case "log":
		return r.log(argv), nil
	case "range-diff":
		text, known := r.ranges[strings.Join(argv[3:], " ")]
		if !known {
			return "", fmt.Errorf("unexpected range-diff %v", argv)
		}
		return text, nil
	case "show":
		return r.shows[argv[len(argv)-1]], nil
	case "diff":
		return finderDiff(), nil
	case "rev-parse":
		name := strings.TrimSuffix(argv[len(argv)-1], "^{commit}")
		sha, known := r.resolved[name]
		if !known {
			return "", &ExitError{Argv: full, Code: 1}
		}
		return sha + "\n", nil
	case "merge-base":
		if r.pulls != nil {
			return "", &ExitError{Argv: full, Code: 1}
		}
		return "", nil
	case "rev-list":
		return r.revList(argv), nil
	case "ls-tree":
		var present []string
		for _, file := range argv[slices.Index(argv, "--")+1:] {
			if !slices.Contains(r.deleted, file) {
				present = append(present, file)
			}
		}
		return strings.Join(present, "\n") + "\n", nil
	case "cat-file":
		var out strings.Builder
		for _, oid := range r.missing {
			out.WriteString(oid + " missing\n")
		}
		return out.String(), nil
	case "-c":
		return "", nil
	}
	return "", fmt.Errorf("unexpected git %v", argv)
}

func (r *finderRepo) lsRemote(argv []string) string {
	var out strings.Builder
	if argv[len(argv)-1] == "refs/pull/*/head" {
		for sha, number := range r.pulls {
			fmt.Fprintf(&out, "%s\trefs/pull/%d/head\n", sha, number)
		}
		return out.String()
	}
	fmt.Fprintf(&out, "%s\tHEAD\n", r.trunk)
	if !r.baseGone {
		fmt.Fprintf(&out, "%s\trefs/heads/dev\n", shaBase)
	}
	return out.String()
}

func (r *finderRepo) fetch(full, argv []string) (string, error) {
	if r.races > 0 {
		r.races--
		return "", &ExitError{Argv: full, Code: 128, Stderr: "fatal: bad object 0000000000000000000000000000000000000000"}
	}
	if slices.ContainsFunc(argv, func(word string) bool { return slices.Contains(r.gone, word) }) {
		return "", &ExitError{Argv: full, Code: 128, Stderr: "fatal: remote error: upload-pack: not our ref"}
	}
	return "", nil
}

func (r *finderRepo) log(argv []string) string {
	switch argv[1] {
	case "--raw":
		return r.raw
	case "--no-walk=unsorted":
		if slices.Contains(argv, "--name-only") {
			return r.landedLog
		}
		return subjectsOf(argv[3:], r.commitSubjects)
	case "--no-merges":
		if len(r.own[argv[5]]) > 0 {
			return strings.Join(ownPaths, "\n\n") + "\n\n"
		}
		return ""
	default:
		return strings.Join(r.subjects, "\n") + "\n"
	}
}

func (r *finderRepo) revList(argv []string) string {
	switch argv[1] {
	case "--topo-order":
		return strings.Join(r.history, "\n") + "\n"
	}
	span := argv[slices.Index(argv, "--")-1]
	return strings.Join(r.own[strings.ReplaceAll(span, "...", "..")], "\n") + "\n"
}

func finderJobDir(t *testing.T, changes map[string]any) string {
	t.Helper()
	spec := map[string]any{
		"drive":    "d1",
		"repo":     repoName,
		"pr":       "43299",
		"head":     shaHead,
		"since":    "",
		"prior":    nil,
		"previous": "",
		"history":  []any{},
		"settled":  map[string]any{},
		"ruled":    []any{},
		"lane":     "p8a-step9",
		"to":       []string{"desk-runner"},
		"checkout": "/srv/checkout",
		"model":    "sol",
		"fast":     true,
		"full":     false,
	}
	return writeJob(t, filepath.Join(t.TempDir(), "finder-desk", "43299-"+shaHead[:12]), spec, changes)
}

func runFinderJob(t *testing.T, directory string, shell *fakeShell) error {
	t.Helper()
	return Finder(context.Background(), directory, shell.deps())
}

func mustFinder(t *testing.T, directory string, shell *fakeShell) {
	t.Helper()
	if err := runFinderJob(t, directory, shell); err != nil {
		t.Fatal(err)
	}
}

func findingOf(severity, citation, defect string) map[string]any {
	return map[string]any{"severity": severity, "cite": citation, "defect": defect, "fix": "keep the marker", "unverified": nil}
}

func replyOf(found ...map[string]any) map[string]any {
	return replySwept(swept, found...)
}

func replySwept(said string, found ...map[string]any) map[string]any {
	verdict := "lgtm"
	if len(found) > 0 {
		verdict = "issues"
	}
	return map[string]any{"verdict": verdict, "swept": said, "findings": found}
}

func priorAt(seq int, text string) map[string]any {
	return map[string]any{"seq": seq, "lane": "p8a-step9", "head": "a269784", "text": text}
}

func TestAHeadWithOnlyMinorsPostsFinderOkOverItsWholeChangeToTheHeadsLane(t *testing.T) {
	for _, state := range []string{"OPEN", "MERGED"} {
		t.Run(state, func(t *testing.T) {
			directory := finderJobDir(t, nil)
			shell, _ := newFinderFake(t, replyOf(findingOf("minor", "a.go:1", "minor"), findingOf("nit", "a.go:2", "nit")))
			shell.state = state
			mustFinder(t, directory, shell)
			post := shell.posted[0]
			equal(t, post["--drive"], "d1")
			equal(t, post["--lane"], FinderLane)
			equal(t, post["--kind"], "review")
			equal(t, post["--pr"], repoName+"#43299")
			equal(t, post["--to"], "p8a-step9,desk-runner")
			equal(t, post["--id"], "finder:"+repoName+"#43299:"+shaHead)
			equal(t, post["--path"], filepath.Join(directory, "record.md"))
			want := "finder: ok " + shaHead + " sol finder " + wholeRecord + ": no majors"
			equal(t, post["--text"], want)
			question := readText(t, directory, "question-1.md")
			holdsAll(t, question,
				"`git diff "+shaOwn+"^ "+shaHead+"`",
				"on base dev ("+shaBase+")",
				filepath.Join(directory, "rows.md"),
				"Open `swept` with your reasoning",
				"## Rulings\n- the resubscribe test pins finder #150321's fix",
				"The caller trace: every caller of the removed path reads the batched store.",
			)
			holdsNone(t, question, shaStale, "LGTM from the review bot")
			outcome := resultOf(t, directory)
			if !reflect.DeepEqual(outcome, map[string]any{"verdict": "ok", "record": want, "posted": "#150001"}) {
				t.Errorf("outcome = %v", outcome)
			}
		})
	}
}

func TestThePlanReadGetsFiveMinutesSoAColdSpriteDiskDoesNotFailThePass(t *testing.T) {
	shell, _ := newFinderFake(t, replyOf())
	mustFinder(t, finderJobDir(t, nil), shell)
	equal(t, shell.timeouts["plan"], 300*time.Second)
}

func TestAReviewAddressedToAWorkerNoLaneRegisteredStillPostsAndThePassSucceeds(t *testing.T) {
	directory := finderJobDir(t, map[string]any{"lane": "p8a7-send-latency", "to": []string{"desk-runner", "p8a-step7"}})
	shell, _ := newFinderFake(t, replyOf(findingOf("major", "go/sand/internal/replica/follow.go:42", "the resubscribe drops the held marker")))
	mustFinder(t, directory, shell)
	equal(t, shell.posted[0]["--to"], "p8a7-send-latency,desk-runner,p8a-step7")
	equal(t, FinderGrade(shell.posted[0]["--text"]), finderFail)
	equal(t, resultOf(t, directory)["posted"].(string), "#150001")
}

func TestAFinderPassAsksCodexAtItsJobsTierWithSolAtMedium(t *testing.T) {
	cases := []struct {
		name  string
		model string
		fast  bool
		want  []string
	}{
		{"fast", "sol", true, []string{"codex-ask", "-m", "sol", "--effort", "medium", "--incident", "--schema", "findings"}},
		{"standard", "sol", false, []string{"codex-ask", "-m", "sol", "--effort", "medium", "--schema", "findings"}},
		{"luna keeps its effort", "luna", false, []string{"codex-ask", "-m", "luna", "--schema", "findings"}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			shell, _ := newFinderFake(t, replyOf())
			mustFinder(t, finderJobDir(t, map[string]any{"fast": c.fast, "model": c.model}), shell)
			sameList(t, shell.asks()[0][:len(c.want)], c.want)
		})
	}
}

func TestAHeadWhoseParentWasRestackedReadsOnlyItsOwnCommitsAgainstTheBaseBranchsCurrentTip(t *testing.T) {
	directory := finderJobDir(t, nil)
	shell, repo := newFinderFake(t, replyOf())
	repo.own = map[string][]string{shaBase + ".." + shaHead: {shaOwn, shaNewer}}
	mustFinder(t, directory, shell)
	sameList(t, shell.gitCalls("--no-lazy-fetch", "rev-list", "--cherry-pick")[0], []string{"git", "--no-lazy-fetch", "rev-list", "--cherry-pick", "--right-only", "--no-merges", "--reverse", "--stdin"})
	equal(t, strings.Split(shell.gitStdins("--no-lazy-fetch", "rev-list", "--cherry-pick")[0], "\n")[0], shaBase+"..."+shaHead)
	question := readText(t, directory, "question-1.md")
	holdsAll(t, question, "`git diff "+shaOwn+"^ "+shaHead+"`")
	holdsNone(t, question, shaBase+"..."+shaHead, shaStale+"..."+shaHead)
}

func TestThePatchMatchAgainstTheTrunkReadsOnlyThePathsTheHeadsOwnCommitsTouch(t *testing.T) {
	shell, _ := newFinderFake(t, replyOf())
	mustFinder(t, finderJobDir(t, nil), shell)
	sameList(t, strings.Fields(shell.gitStdins("--no-lazy-fetch", "rev-list", "--cherry-pick")[0])[1:], append([]string{"--"}, ownPaths...))
}

func TestThePatchMatchFetchesBothSidesOfEveryDiffItComparesInOneRequestAndNeverFetchesLazily(t *testing.T) {
	atMergeBase, atHead, present, zero, gitlink := strings.Repeat("a", 40), strings.Repeat("b", 40), strings.Repeat("c", 40), strings.Repeat("0", 40), strings.Repeat("d", 40)
	shell, repo := newFinderFake(t, replyOf())
	repo.raw = ":100644 100644 " + atMergeBase + " " + atHead + " M\tgo/a.go\n" +
		":000000 100644 " + zero + " " + present + " A\tgo/b.go\n" +
		":160000 160000 " + gitlink + " " + gitlink + " M\tvendor/sub\n"
	repo.missing = []string{atMergeBase, atHead}
	mustFinder(t, finderJobDir(t, nil), shell)
	batch := append([]string{"git"}, blobFetch...)
	checkedAt, fetchedAt, comparedAt := -1, -1, -1
	for index, call := range shell.snapshot() {
		switch {
		case slices.Equal(call.Argv, []string{"git", "--no-lazy-fetch", "cat-file", "--batch-check"}) && checkedAt < 0:
			checkedAt = index
			equal(t, call.Stdin, atMergeBase+"\n"+atHead+"\n"+present+"\n")
		case slices.Equal(call.Argv, batch) && fetchedAt < 0:
			fetchedAt = index
			equal(t, call.Stdin, atMergeBase+"\n"+atHead+"\n")
		case len(call.Argv) > 2 && call.Argv[1] == "rev-list" && call.Argv[2] == "--cherry-pick":
			t.Errorf("compared with lazy fetches on: %v", call.Argv)
		case len(call.Argv) > 3 && call.Argv[2] == "rev-list" && call.Argv[3] == "--cherry-pick" && comparedAt < 0:
			comparedAt = index
		}
	}
	if checkedAt < 0 || fetchedAt < checkedAt || comparedAt < fetchedAt {
		t.Errorf("checked at %d, fetched at %d, compared at %d", checkedAt, fetchedAt, comparedAt)
	}
}

func TestAHeadRebasedPastItsStaleBaseBranchOntoTheTrunkReadsAndScopesOnlyItsOwnCommits(t *testing.T) {
	directory := finderJobDir(t, map[string]any{"since": shaOld, "prior": priorAt(161820, "finder: ok a269784")})
	shell, repo := newFinderFake(t, replyOf(findingOf("major", "go/sand/internal/replica/follow.go:41", "drops the marker")))
	repo.own[shaBase+".."+shaHead] = []string{shaLanded, shaOwn}
	repo.own[shaTrunk+".."+shaHead] = []string{shaOwn}
	repo.own[shaBase+".."+shaOld] = []string{shaOldOwn}
	repo.own[shaTrunk+".."+shaOld] = []string{shaOldOwn}
	repo.resolved = map[string]string{shaOld: shaOld}
	repo.ranges = map[string]string{earlierRange: pairLines("!")}
	repo.trunk = shaTrunk
	mustFinder(t, directory, shell)
	question := readText(t, directory, "question-1.md")
	holdsAll(t, question, "`git range-diff "+earlierRange+"`", "`git diff "+shaOwn+"^ "+shaHead+"`", "the range-diff shows for its pair ("+shaOwn[:11]+"); each new commit whole with `git show <sha>` (none)")
	holdsNone(t, question, shaLanded[:11])
	if !strings.HasPrefix(shell.posted[0]["--text"], "finder: major "+shaHead+" 1 open") {
		t.Errorf("posted %q", shell.posted[0]["--text"])
	}
}

func TestAReplayWhoseParentWasRestackedPastItsStaleGraphiteBaseCarriesTheGradedVerdict(t *testing.T) {
	directory := finderJobDir(t, map[string]any{"since": shaOld, "prior": priorAt(165658, "finder: ok a269784 sol finder over delta 6ec2c60..a269784: no majors")})
	shell, repo := newFinderFake(t)
	repo.own[shaBase+".."+shaHead] = []string{shaMoved, shaOwn}
	repo.own[shaParentTip+".."+shaHead] = []string{shaOwn}
	repo.own[shaTrunk+".."+shaHead] = []string{shaMoved, shaOwn}
	repo.own[shaBase+".."+shaOld] = []string{shaOldOwn}
	repo.own[shaParentTip+".."+shaOld] = []string{shaOldOwn}
	repo.own[shaTrunk+".."+shaOld] = []string{shaOldOwn}
	repo.resolved = map[string]string{shaOld: shaOld}
	repo.ranges = map[string]string{earlierRange: fmt.Sprintf(" 1:  %s =  1:  %s sand: the head's own commit\n", shaOldOwn[:11], shaOwn[:11])}
	repo.trunk = shaTrunk
	repo.history = []string{shaHead, shaOwn, shaParentTip, shaMoved}
	repo.pulls = map[string]int{shaHead: 43299, shaParentTip: 43736, shaNewer: 44038}
	mustFinder(t, directory, shell)
	if len(shell.asks()) != 0 || len(shell.gitCalls("diff")) != 0 {
		t.Errorf("asks %v, diffs %v", shell.asks(), shell.gitCalls("diff"))
	}
	sameList(t, shell.texts(), []string{"finder: ok " + shaHead[:7] + " carried from #165658"})
}

func TestARequestedFullPassReadsTheWholeChangeAtTheLiveHeadAndSaysSo(t *testing.T) {
	prior := map[string]any{"seq": 149302, "lane": "finder-desk", "head": "a269784", "text": "finder: ok " + shaHead + " sol finder over delta a269784..7414944: no majors", "path": ""}
	directory := finderJobDir(t, map[string]any{"head": "", "history": []any{prior}, "full": true})
	shell, _ := newFinderFake(t, replyOf())
	mustFinder(t, directory, shell)
	holdsAll(t, readText(t, directory, "question-1.md"), "This pass reads the PR's whole change: `git diff "+shaOwn+"^ "+shaHead+"`.")
	readText(t, directory, "rows.md")
	equal(t, shell.posted[0]["--text"], "finder: ok "+shaHead+" sol finder "+wholeRecord+": no majors")
	equal(t, shell.posted[0]["--id"], "finder:"+repoName+"#43299:"+shaHead+":whole")
}

func TestAPostTheStoreDidNotTakeSettlesThePassWithItsRecordLoggedForTheRunner(t *testing.T) {
	directory := finderJobDir(t, nil)
	shell, _ := newFinderFake(t, replyOf(findingOf("minor", "a.go:1", "minor")))
	shell.postError = &client.BusDown{Cause: errors.New("the store did not answer within 15s"), Path: "/unsent/1.json"}
	mustFinder(t, directory, shell)
	holdsAll(t, readText(t, directory, ResultFile), `"verdict":"ok"`)
	holdsAll(t, readText(t, directory, PostsFile), "finder:"+repoName+"#43299:"+shaHead)
}

func TestAPostTheStoreRefusedFailsThePass(t *testing.T) {
	directory := finderJobDir(t, nil)
	shell, _ := newFinderFake(t, replyOf(findingOf("minor", "a.go:1", "minor")))
	shell.postError = errors.New("this lane may not post here")
	if err := runFinderJob(t, directory, shell); err == nil {
		t.Fatal("a refused post settled the pass")
	}
	holdsAll(t, readText(t, directory, ResultFile), "this lane may not post here")
}

func TestASpriteFinderReturnsItsReviewAndCensusForCoordinatorPosting(t *testing.T) {
	directory := finderJobDir(t, map[string]any{"sprite": "grader-a"})
	shell, _ := newFinderFake(t, replyOf(findingOf("minor", "go/poll.go:9", "census: the poll interval predates this PR")))
	deps := shell.deps()
	deps.Post = nil
	if err := Finder(context.Background(), directory, deps); err != nil {
		t.Fatal(err)
	}
	holdsAll(t, readText(t, directory, ResultFile), `"verdict":"ok"`)
	if len(shell.posted) != 0 {
		t.Fatal("the Sprite posted to a local replica", shell.posted)
	}
	if err := Repost(context.Background(), directory, shell.post); err != nil {
		t.Fatal(err)
	}
	equal(t, len(shell.posted), 2)
	equal(t, shell.posted[0]["--id"], "finder:"+repoName+"#43299:"+shaHead)
	holdsAll(t, shell.posted[0]["--text"], "finder: ok "+shaHead)
	equal(t, shell.posted[1]["--id"], "finder-census:"+repoName+"#43299:"+shaHead)
	equal(t, shell.posted[1]["--path"], filepath.Join(directory, "census.md"))
	holdsAll(t, readText(t, directory, "census.md"), "go/poll.go:9: the poll interval predates this PR")
	for _, post := range shell.posted {
		if filepath.Dir(post["--path"]) != directory {
			t.Fatal("a record body would be left on the Sprite", post)
		}
		readText(t, directory, filepath.Base(post["--path"]))
	}
}

func TestAHeadThatCarriesNoCommitTheBaseLacksRunsNoFinder(t *testing.T) {
	directory := finderJobDir(t, nil)
	shell, repo := newFinderFake(t)
	repo.own = map[string][]string{}
	mustFinder(t, directory, shell)
	if len(shell.asks()) != 0 || len(shell.posted) != 0 {
		t.Errorf("asks %v, posted %v", shell.asks(), shell.posted)
	}
	equal(t, resultOf(t, directory)["skipped"].(string), repoName+"#43299 at "+shaHead[:12]+" carries no commit dev lacks")
}

func TestBlockersAndMajorsPostFinderMajorOneClauseEachAndMinorsStayOffTheRecord(t *testing.T) {
	directory := finderJobDir(t, nil)
	shell, _ := newFinderFake(t, replyOf(
		findingOf("blocker", "rust/crates/sandsql-journal/src/replica.rs:2947", "registered mounts never record a resubscribe position"),
		findingOf("major", "go/poll.go:9", "row rule/949e6be missing: a mount\npolls every 5 s"),
		findingOf("minor", "a.go:1", "a name reads oddly"),
	))
	mustFinder(t, directory, shell)
	equal(t, shell.posted[0]["--text"], "finder: major "+shaHead+" 2 open "+wholeRecord+": rust/crates/sandsql-journal/src/replica.rs:2947 registered mounts never record a resubscribe position; go/poll.go:9 row rule/949e6be missing: a mount polls every 5 s")
	holdsAll(t, readText(t, directory, "record.md"), "a name reads oddly")
}

func ownerPick(id, answers string) map[string]any {
	return map[string]any{"ID": id, "Words": "the owner's words", "CheckKind": "predicate", "CheckRef": "answers:" + answers}
}

func TestAMajorAnOwnerPickAnswersStaysOffTheRecordWhichNamesThePick(t *testing.T) {
	directory := finderJobDir(t, map[string]any{"picks": []any{ownerPick("rule/abc", "polls every 5 s")}})
	shell, _ := newFinderFake(t, replyOf(
		findingOf("blocker", "rust/crates/sandsql-journal/src/replica.rs:2947", "registered mounts never record a resubscribe position"),
		findingOf("major", "go/poll.go:9", "a mount polls every 5 s"),
	))
	mustFinder(t, directory, shell)
	text := shell.posted[0]["--text"]
	holdsAll(t, text, "finder: major "+shaHead+" 1 open", "replica.rs:2947", `settled by owner picks: rule/abc "the owner's words"`)
	holdsNone(t, text, "go/poll.go:9")
}

func TestAHeadWhoseEveryMajorAnOwnerPickAnswersPostsOk(t *testing.T) {
	directory := finderJobDir(t, map[string]any{"picks": []any{ownerPick("rule/abc", "go/poll.go:9")}})
	shell, _ := newFinderFake(t, replyOf(findingOf("major", "go/poll.go:9", "a mount polls every 5 s")))
	mustFinder(t, directory, shell)
	text := shell.posted[0]["--text"]
	equal(t, FinderGrade(text), finderPass)
	holdsAll(t, text, "settled by owner picks: rule/abc")
}

func TestAPickThatChecksNothingOrAnswersNothingSettlesNothing(t *testing.T) {
	directory := finderJobDir(t, map[string]any{"picks": []any{
		map[string]any{"ID": "rule/unchecked", "CheckKind": "manual", "CheckRef": "answers:polls every 5 s"},
		ownerPick("rule/other", "something else"),
	}})
	shell, _ := newFinderFake(t, replyOf(findingOf("major", "go/poll.go:9", "a mount polls every 5 s")))
	mustFinder(t, directory, shell)
	holdsAll(t, shell.posted[0]["--text"], "finder: major "+shaHead+" 1 open", "go/poll.go:9")
	holdsNone(t, shell.posted[0]["--text"], "settled by owner picks")
}

func TestTheFinderAsksWhatRunsPerItemAtAThousand(t *testing.T) {
	directory := finderJobDir(t, nil)
	slow := "slow at N: the chat list reads each chat's settings one at a time, 1,000 reads at 1,000 chats"
	shell, _ := newFinderFake(t, replyOf(findingOf("major", "go/sand/internal/replica/follow.go:42", slow)))
	mustFinder(t, directory, shell)
	holdsAll(t, squeezed(readText(t, directory, "question-1.md")),
		"Ask what the change runs per chat, team, session, or run when there are 1,000 of them.",
		"a loop over chats, teams, sessions, runs, files, or rows that awaits I/O per item",
		"a cache, cap, timer, retry, or idle wait whose PR body names no fundamental fix it stands in for",
	)
	equal(t, shell.posted[0]["--text"], "finder: major "+shaHead+" 1 open "+wholeRecord+": go/sand/internal/replica/follow.go:42 "+slow)
}

func TestAMajorWhoseCitesNameNoChangedLineAndACensusFindingGoToTheCensusFile(t *testing.T) {
	directory := finderJobDir(t, nil)
	shell, _ := newFinderFake(t, replyOf(
		findingOf("blocker", "go/x.go:3, go/sand/internal/replica/follow.go:41-44", "the marker is dropped"),
		findingOf("major", "go/sand/internal/replica/follow.go:12", "the drain loop skips a closed mount"),
		findingOf("minor", "go/poll.go:9", "census: the poll interval predates this PR"),
	))
	mustFinder(t, directory, shell)
	review, note := shell.posted[0], shell.posted[1]
	equal(t, review["--text"], "finder: major "+shaHead+" 1 open "+wholeRecord+": go/x.go:3, go/sand/internal/replica/follow.go:41-44 the marker is dropped")
	census := filepath.Join(filepath.Dir(directory), "census-2026-11-02.md")
	equal(t, note["--kind"], "note")
	equal(t, note["--lane"], FinderLane)
	equal(t, note["--to"], "p8a-step9,desk-runner")
	equal(t, note["--id"], "finder-census:"+repoName+"#43299:"+shaHead)
	equal(t, note["--text"], "finder census: 2 dev-only findings on #43299 appended to "+census)
	equal(t, readText(t, filepath.Dir(census), filepath.Base(census)), "- "+repoName+"#43299 "+shaHead[:12]+" go/sand/internal/replica/follow.go:12: the drain loop skips a closed mount\n- "+repoName+"#43299 "+shaHead[:12]+" go/poll.go:9: the poll interval predates this PR\n")
	holdsAll(t, readText(t, directory, "question-1.md"), "- go/sand/internal/replica/follow.go: 40-43\n")
	equal(t, resultOf(t, directory)["census"].(string), "#150001")
}

func TestAFindingAPortsSourceSharesIsOwedAsAFollowUpAndHoldsNothing(t *testing.T) {
	shared := "follow-up: #49412 go/client.go:88 drops the marker the same way"
	cite := "go/sand/internal/replica/follow.go:41"
	owed := "; follow-ups owed after this port: " + cite + " #49412 go/client.go:88 drops the marker the same way"
	for name, tc := range map[string]struct {
		body     string
		findings []map[string]any
		want     string
	}{
		"a port's shared blocker grades ok and is named": {
			"port of #49412\n\n" + summaryBody,
			[]map[string]any{findingOf("blocker", cite, shared)},
			"finder: ok " + shaHead + " sol finder " + wholeRecord + ": no majors" + owed,
		},
		"a port's own defect still holds it": {
			"port of #49412\n\n" + summaryBody,
			[]map[string]any{findingOf("major", cite, "the next tree's wiring drops the marker"), findingOf("blocker", cite, shared)},
			"finder: major " + shaHead + " 1 open " + wholeRecord + ": " + cite + " the next tree's wiring drops the marker" + owed,
		},
		"a body that does not open as a port keeps the major": {
			summaryBody + "\n\nport of #49412",
			[]map[string]any{findingOf("blocker", cite, shared)},
			"finder: major " + shaHead + " 1 open " + wholeRecord + ": " + cite + " " + shared,
		},
	} {
		t.Run(name, func(t *testing.T) {
			directory := finderJobDir(t, nil)
			shell, _ := newFinderFake(t, replyOf(tc.findings...))
			shell.body = tc.body
			mustFinder(t, directory, shell)
			equal(t, shell.posted[0]["--text"], tc.want)
		})
	}
}

func TestHunksReadNewSideRangesABareCountAsOneLineAndADeletionAsTheLineItFollows(t *testing.T) {
	got := hunks("diff --git a/a.go b/a.go\n--- a/a.go\n+++ b/a.go\n@@ -3 +3 @@\n-x\n+y\n@@ -9,2 +8,0 @@\n-p\n-q\n@@ -20,0 +21,4 @@\n+n\n")
	want := touched{{"a.go", []span{{3, 3}, {8, 8}, {21, 24}}}}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("hunks = %v, want %v", got, want)
	}
}

func TestMajorsPastCcisLimitPostAClippedLineThatStillReadsMajor(t *testing.T) {
	directory := finderJobDir(t, nil)
	found := make([]map[string]any, 0, 4)
	for index := range 4 {
		found = append(found, findingOf("major", fmt.Sprintf("go/sand/file%d.go:%d", index, index+1), strings.Repeat("the follower keeps a stale incarnation after a swap ", 3)))
	}
	shell, _ := newFinderFake(t, replyOf(found...))
	mustFinder(t, directory, shell)
	text := shell.posted[0]["--text"]
	equal(t, len([]rune(text)), cciText)
	if !strings.HasSuffix(text, "…") {
		t.Errorf("text %q", text)
	}
	equal(t, FinderGrade(text), finderFail)
	equal(t, strings.Count(readText(t, directory, "record.md"), "go/sand/file3.go:4"), 2)
}

func TestAPassAfterAnEarlierFinderReadsSinceThatHeadAndChecksItsMajors(t *testing.T) {
	prior := priorAt(149302, "finder: major a269784 1 open: walk.go:9 skips the volume link")
	answer := map[string]any{"seq": 149310, "lane": "p8a-step9", "text": "finder: ok a269784. walk.go:9 is rebutted: the volume link is walked by its caller", "path": "/notes/answer.md"}
	directory := finderJobDir(t, map[string]any{"since": shaOld, "prior": prior, "history": []any{prior, answer}})
	shell, repo := newFinderFake(t, replyOf())
	repo.own[shaBase+".."+shaOld] = []string{shaOldOwn}
	repo.resolved = map[string]string{shaOld: shaOld}
	repo.ranges = map[string]string{earlierRange: pairLines("=", "!")}
	mustFinder(t, directory, shell)
	question := readText(t, directory, "question-1.md")
	holdsAll(t, question,
		"Run nothing that builds", "Change no file, branch, or ref",
		"origin "+shaHead+" "+shaBase+"`",
		"`git range-diff "+earlierRange+"`",
		"the range-diff shows for its pair ("+shaOwn[:11]+"); each new commit whole with `git show <sha>` (none)",
		"- cci #149302 from p8a-step9: finder: major a269784 1 open: walk.go:9 skips the volume link\n",
		"- cci #149310 from p8a-step9: finder: ok a269784. walk.go:9 is rebutted: the volume link is walked by its caller (whole record: /notes/answer.md)\n",
		"never raise a closed finding again", planPath, scopedStep,
	)
	holdsNone(t, question, "rows.md")
	wantNoFile(t, directory, "rows.md")
	if _, sliced := shell.timeouts["slice"]; sliced {
		t.Error("a delta pass read the register slice")
	}
	ask := shell.asks()[0]
	sameList(t, ask[:8], []string{"codex-ask", "-m", "sol", "--effort", "medium", "--incident", "--schema", "findings"})
	equal(t, ask[len(ask)-1], filepath.Join(directory, "question-1.md"))
	equal(t, shell.posted[0]["--text"], "finder: ok "+shaHead+" sol finder over delta "+shaOld[:7]+".."+shaHead[:7]+": no majors")
}

func TestAPassReadsTheRootsRulingsOnItsPRAndNeverGradesWhatTheySettled(t *testing.T) {
	ruling := map[string]any{"seq": 262338, "lane": "root", "text": "Root ruling: the port keeps its source contract exactly.", "path": "/notes/ruling.md"}
	directory := finderJobDir(t, map[string]any{"ruled": []any{ruling}})
	shell, _ := newFinderFake(t, replyOf())
	mustFinder(t, directory, shell)
	holdsAll(t, readText(t, directory, "question-1.md"),
		"- cci #262338 from root: Root ruling: the port keeps its source contract exactly. (whole record: /notes/ruling.md)\n",
		"never raise it again, and never grade against the ruling what it decided",
		"never grade it as unplanned",
	)
}

func TestAReplyThatMiscountsTheRowsIsAskedOnceMoreAndTwoSuchRepliesPostNothing(t *testing.T) {
	miscounted := replySwept(reasons + "rows checked: 1")
	directory := finderJobDir(t, nil)
	shell, _ := newFinderFake(t, miscounted, replyOf())
	mustFinder(t, directory, shell)
	asks := shell.asks()
	equal(t, asks[0][len(asks[0])-1], filepath.Join(directory, "question-1.md"))
	equal(t, asks[1][len(asks[1])-1], filepath.Join(directory, "question-2.md"))
	holdsAll(t, readText(t, directory, "question-2.md"), "`rows checked: 1` where the rows file holds 2 rows", "end `swept` with `rows checked: 2`")
	equal(t, len(shell.posted), 1)

	again := finderJobDir(t, nil)
	shell, _ = newFinderFake(t, miscounted, miscounted)
	var failed *JobFailed
	if err := runFinderJob(t, again, shell); !errors.As(err, &failed) {
		t.Fatalf("err = %v, want JobFailed", err)
	}
	equal(t, len(shell.posted), 0)
	equal(t, resultOf(t, again)["error"].(string), "JobFailed: two replies gave `rows checked: 1` where the rows file holds 2 rows")
}

func TestAWholeSliceOfManyRowsRunsOnTheModelTheJobNames(t *testing.T) {
	var rows strings.Builder
	for index := range 151 {
		fmt.Fprintf(&rows, "### rule/%07x | scope | in-force | said 1 | 2026-10-22\n> row %d\n\n", index, index)
	}
	directory := finderJobDir(t, map[string]any{"model": "luna"})
	shell, _ := newFinderFake(t, replySwept(reasons+"rows checked: 151"))
	shell.rows = rows.String()
	mustFinder(t, directory, shell)
	equal(t, shell.asks()[0][2], "luna")
	equal(t, len(shell.posted), 1)
}

func TestAReplyWithNoReasoningIsAskedOnceMoreAndTwoSuchRepliesPostNothing(t *testing.T) {
	tests := make([]string, 0, 8)
	for index := range 8 {
		tests = append(tests, fmt.Sprintf("t%d <- plan.md:%d", index, index))
	}
	bare := replySwept("ok; rows checked: 2; tests: " + strings.Join(tests, "; "))
	directory := finderJobDir(t, nil)
	shell, _ := newFinderFake(t, bare, replyOf())
	mustFinder(t, directory, shell)
	holdsAll(t, readText(t, directory, "question-2.md"), reasonless)
	equal(t, len(shell.posted), 1)

	again := finderJobDir(t, nil)
	shell, _ = newFinderFake(t, bare, bare)
	if err := runFinderJob(t, again, shell); err == nil {
		t.Fatal("two reasonless replies passed")
	}
	equal(t, len(shell.posted), 0)
	equal(t, resultOf(t, again)["error"].(string), "JobFailed: two replies gave "+reasonless)
}

func TestAHeadThePRNoLongerCarriesRunsNoFinder(t *testing.T) {
	directory := finderJobDir(t, nil)
	shell, _ := newFinderFake(t)
	shell.heads = []string{shaNewer}
	mustFinder(t, directory, shell)
	equal(t, len(shell.asks()), 0)
	equal(t, len(shell.posted), 0)
	equal(t, resultOf(t, directory)["skipped"].(string), repoName+"#43299 moved to "+shaNewer[:12])
}

func TestAHeadSupersededWhileCodexReadItPostsNothing(t *testing.T) {
	directory := finderJobDir(t, nil)
	shell, _ := newFinderFake(t, replyOf(findingOf("major", "go/poll.go:9", "polls")))
	shell.heads = []string{shaHead, shaNewer}
	mustFinder(t, directory, shell)
	equal(t, len(shell.asks()), 1)
	equal(t, len(shell.posted), 0)
	equal(t, resultOf(t, directory)["skipped"].(string), repoName+"#43299 moved to "+shaNewer[:12]+" while codex read "+shaHead[:12])
}

func TestAJobTheRunnerCancelledForANewerHeadAsksCodexNothing(t *testing.T) {
	directory := finderJobDir(t, nil)
	if err := os.WriteFile(filepath.Join(directory, CancelledFile), []byte(shaNewer+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	shell, _ := newFinderFake(t)
	mustFinder(t, directory, shell)
	equal(t, len(shell.asks()), 0)
	equal(t, len(shell.posted), 0)
	equal(t, resultOf(t, directory)["skipped"].(string), repoName+"#43299 at "+shaHead[:12]+" was superseded by "+shaNewer[:12]+" before codex read it")
}

func TestAFailedPRReadIsRetriedOnAShortBackoffAndFailsTheJobOnceEveryPauseIsSpent(t *testing.T) {
	directory := finderJobDir(t, nil)
	shell, _ := newFinderFake(t, replyOf())
	shell.failures = 2
	mustFinder(t, directory, shell)
	if !slices.Equal(shell.slept, []time.Duration{2 * time.Second, 5 * time.Second}) {
		t.Errorf("slept %v", shell.slept)
	}
	equal(t, len(shell.posted), 1)

	again := finderJobDir(t, nil)
	shell, _ = newFinderFake(t)
	shell.failures = len(retryBackoff) + 1
	if err := runFinderJob(t, again, shell); err == nil {
		t.Fatal("a read failing past every pause passed")
	}
	holdsAll(t, resultOf(t, again)["error"].(string), "TLS handshake timeout")
}

func TestAFetchGitKilledOnAnotherLanesZeroWorktreeRefIsRetriedOnAShortBackoff(t *testing.T) {
	directory := finderJobDir(t, nil)
	shell, repo := newFinderFake(t, replyOf())
	repo.races = 2
	mustFinder(t, directory, shell)
	if !slices.Equal(shell.slept, []time.Duration{2 * time.Second, 5 * time.Second}) {
		t.Errorf("slept %v", shell.slept)
	}
	equal(t, len(shell.posted), 1)
	fetches := shell.gitCalls("fetch")
	equal(t, len(fetches), 3)
	sameList(t, fetches[0], []string{"git", "fetch", "--no-write-fetch-head", "-q", "origin", shaHead, shaBase, shaBase})

	again := finderJobDir(t, nil)
	shell, repo = newFinderFake(t)
	repo.races = len(retryBackoff) + 1
	if err := runFinderJob(t, again, shell); err == nil {
		t.Fatal("a fetch failing past every pause passed")
	}
	holdsAll(t, resultOf(t, again)["error"].(string), "bad object 0000000000000000000000000000000000000000")
}

func TestAFetchThatReadsARefARestartFilledWithNulsDropsItBeforeItsRetry(t *testing.T) {
	common := t.TempDir()
	nulled := filepath.Join(common, "refs", "remotes", "origin", "resume-first-turn-1")
	kept := filepath.Join(common, "refs", "tags", "v1")
	locked := filepath.Join(common, "refs", "heads", "dev.lock")
	for path, content := range map[string]string{nulled: strings.Repeat("\x00", 123), kept: shaBase + "\n", locked: strings.Repeat("\x00", 41)} {
		if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	directory := finderJobDir(t, nil)
	shell, repo := newFinderFake(t, replyOf())
	repo.resolved["--git-common-dir"] = common
	repo.races = 1
	mustFinder(t, directory, shell)
	if _, err := os.Stat(nulled); !errors.Is(err, os.ErrNotExist) {
		t.Errorf("the NUL-filled ref survived: %v", err)
	}
	for _, path := range []string{kept, locked} {
		if _, err := os.Stat(path); err != nil {
			t.Error(err)
		}
	}
	equal(t, len(shell.gitCalls("fetch")), 2)
}

func TestAWholePassReadsBothTipsInOneLsRemoteAndRunsThePlanWhileTheHeadFetches(t *testing.T) {
	planning := make(chan struct{})
	overlapped := false
	directory := finderJobDir(t, nil)
	shell, _ := newFinderFake(t, replyOf())
	shell.before = func(command Command) {
		if command.Argv[0] == rulingsPath && command.Argv[1] == "plan" {
			close(planning)
			return
		}
		if command.Argv[0] == "git" && command.Argv[1] == "fetch" {
			overlapped = waited(planning)
		}
	}
	mustFinder(t, directory, shell)
	if !overlapped {
		t.Error("the head's fetch ran alone")
	}
	lists := shell.gitCalls("ls-remote")
	equal(t, len(lists), 1)
	sameList(t, lists[0], []string{"git", "ls-remote", "origin", "HEAD", "refs/heads/dev"})
}

func TestAFinderSliceStartsOnlyOnceTheHeadsFetchHasLandedItsTree(t *testing.T) {
	directory := finderJobDir(t, nil)
	shell, _ := newFinderFake(t, replyOf())
	shell.refuseSliceBeforeFetch(t)
	mustFinder(t, directory, shell)
}

func waited(done chan struct{}) bool {
	select {
	case <-done:
		return true
	case <-time.After(5 * time.Second):
		return false
	}
}

func TestACarriedMajorRootSettledCarriesAsOkCitingTheRootDecide(t *testing.T) {
	cases := []struct {
		name    string
		graded  string
		settled map[string]int
		want    string
	}{
		{"settled", "finder: major a269784 1 open: walk.go:9 skips the volume link", map[string]int{"149302": 149400}, "finder: ok %s carried from #149302, settled by coordinator #149400"},
		{"settled-upchain", "finder: major a269784 carried from #149100", map[string]int{"149100": 149150}, "finder: ok %s carried from #149302, settled by coordinator #149150"},
		{"unsettled", "finder: major a269784 1 open: walk.go:9 skips the volume link", map[string]int{"149100": 149150}, "finder: major %s carried from #149302"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			prior := map[string]any{"seq": 149302, "lane": "finder-desk", "head": "a269784", "text": c.graded}
			earlier := map[string]any{"seq": 149100, "lane": "finder-desk", "text": "finder: major 9f00e11 1 open: walk.go:9 skips the volume link", "path": ""}
			directory := finderJobDir(t, map[string]any{"prior": prior, "history": []any{earlier, prior}, "settled": c.settled})
			shell, repo := newFinderFake(t)
			repo.own[shaBase+".."+shaOld] = []string{shaOldOwn}
			repo.resolved = map[string]string{"a269784": shaOld}
			repo.ranges = map[string]string{earlierRange: pairLines("=", "=")}
			mustFinder(t, directory, shell)
			equal(t, len(shell.asks()), 0)
			equal(t, shell.posted[0]["--text"], fmt.Sprintf(c.want, shaHead[:7]))
		})
	}
}

func TestAReplayOntoAMovedParentWhoseEveryOwnCommitPairsEqualPostsTheGradedHeadsVerdictAsCarried(t *testing.T) {
	for _, graded := range []string{"finder: ok a269784 sol finder over dev...a269784: no majors", "finder: major a269784 1 open: walk.go:9 skips the volume link"} {
		t.Run(graded, func(t *testing.T) {
			directory := finderJobDir(t, map[string]any{"prior": map[string]any{"seq": 149302, "lane": "finder-desk", "head": "a269784", "text": graded}})
			shell, repo := newFinderFake(t)
			repo.own[shaBase+".."+shaOld] = []string{shaOldOwn}
			repo.resolved = map[string]string{"a269784": shaOld}
			repo.ranges = map[string]string{earlierRange: pairLines("=", "=")}
			mustFinder(t, directory, shell)
			verdict := FinderGrade(graded)
			post := shell.posted[0]
			equal(t, len(shell.asks()), 0)
			equal(t, post["--text"], "finder: "+verdict+" "+shaHead[:7]+" carried from #149302")
			equal(t, post["--kind"], "review")
			equal(t, post["--id"], "finder:"+repoName+"#43299:"+shaHead)
			holdsAll(t, readText(t, directory, "record.md"), graded)
			outcome := resultOf(t, directory)
			if !reflect.DeepEqual(outcome, map[string]any{"verdict": verdict, "record": post["--text"], "posted": "#150001"}) {
				t.Errorf("outcome = %v", outcome)
			}
		})
	}
}

func TestAReplayDropsAMajorWhoseEveryCitedFileIsGoneAtTheHead(t *testing.T) {
	original := "finder: major a269784 1 open: gone/walk.go:9; gone/walk.go:40 skips the volume link"
	for name, job := range map[string]map[string]any{
		"the graded record": {"prior": map[string]any{"seq": 149302, "lane": "finder-desk", "head": "a269784", "text": original}},
		"a carried chain": {
			"prior":   map[string]any{"seq": 149302, "lane": "finder-desk", "head": "a269784", "text": "finder: major a269784 carried from #149100"},
			"history": []map[string]any{{"seq": 149100, "lane": "finder-desk", "text": original}},
		},
	} {
		t.Run(name, func(t *testing.T) {
			directory := finderJobDir(t, job)
			shell, repo := newFinderFake(t)
			repo.own[shaBase+".."+shaOld] = []string{shaOldOwn}
			repo.resolved = map[string]string{"a269784": shaOld}
			repo.ranges = map[string]string{earlierRange: pairLines("=", "=")}
			repo.deleted = []string{"gone/walk.go"}
			mustFinder(t, directory, shell)
			equal(t, len(shell.asks()), 0)
			equal(t, shell.posted[0]["--text"], "finder: ok "+shaHead[:7]+" carried from #149302, whose findings cite only files gone at this head: gone/walk.go")
		})
	}
}

func TestAReplayWithOnlySomeCitedFilesGoneReadsTheWholeChangeAgain(t *testing.T) {
	directory := finderJobDir(t, map[string]any{"prior": map[string]any{"seq": 149302, "lane": "finder-desk", "head": "a269784", "text": "finder: major a269784 1 open: gone/walk.go:9; kept/link.go:2 skips the volume link"}})
	shell, repo := newFinderFake(t, replyOf(findingOf("minor", "a.go:1", "minor")))
	repo.own[shaBase+".."+shaOld] = []string{shaOldOwn}
	repo.resolved = map[string]string{"a269784": shaOld}
	repo.ranges = map[string]string{earlierRange: pairLines("=", "=")}
	repo.deleted = []string{"gone/walk.go"}
	mustFinder(t, directory, shell)
	equal(t, len(shell.asks()), 1)
	equal(t, shell.posted[0]["--text"], "finder: ok "+shaHead+" sol finder "+wholeRecord+": no majors")
}

func TestAHeadWhoseOnlyFinderReviewNamesNoHeadReadsSinceTheHeadItsHeadRecordReplaced(t *testing.T) {
	directory := finderJobDir(t, map[string]any{"previous": shaOld})
	shell, repo := newFinderFake(t)
	repo.own[shaBase+".."+shaOld] = []string{shaOldOwn}
	repo.resolved = map[string]string{shaOld: shaOld}
	repo.ranges = map[string]string{earlierRange: pairLines("=", "=")}
	mustFinder(t, directory, shell)
	equal(t, len(shell.asks()), 0)
	equal(t, len(shell.posted), 0)
	outcome := resultOf(t, directory)
	if !reflect.DeepEqual(outcome, map[string]any{"skipped": repoName + "#43299 at " + shaHead[:12] + " replays every own commit of " + shaOld[:12] + ", patch for patch or dropping only hunks dev carries", "ungraded": true}) {
		t.Errorf("outcome = %v", outcome)
	}
}

func TestAFixedHeadNamedShortByTheLastFinderRecordReadsOnlyItsNewCommits(t *testing.T) {
	directory := finderJobDir(t, map[string]any{"prior": priorAt(149302, "finder: ok a269784")})
	shell, repo := newFinderFake(t, replyOf())
	repo.own[shaBase+".."+shaOld] = []string{shaOldOwn}
	repo.resolved = map[string]string{"a269784": shaOld}
	repo.ranges = map[string]string{earlierRange: pairLines("=", ">")}
	mustFinder(t, directory, shell)
	holdsAll(t, readText(t, directory, "question-1.md"), "for its pair (none); each new commit whole with `git show <sha>` ("+shaOwn[:11]+")", scopedStep)
	wantNoFile(t, directory, "rows.md")
	equal(t, shell.posted[0]["--text"], "finder: ok "+shaHead+" sol finder over delta "+shaOld[:7]+".."+shaHead[:7]+": no majors")
}

func TestAMergedPRWhoseBaseBranchTheRemoteDeletedStillGetsItsPassOverTheTrunk(t *testing.T) {
	directory := finderJobDir(t, nil)
	shell, repo := newFinderFake(t, replyOf(findingOf("minor", "a.go:1", "minor")))
	shell.state = "MERGED"
	repo.baseGone = true
	mustFinder(t, directory, shell)
	equal(t, FinderGrade(shell.posted[0]["--text"]), finderPass)
}

func TestAClosedPRRunsNoFinderAndPostsNothing(t *testing.T) {
	directory := finderJobDir(t, nil)
	shell, _ := newFinderFake(t)
	shell.state = "CLOSED"
	mustFinder(t, directory, shell)
	equal(t, len(shell.asks()), 0)
	equal(t, len(shell.posted), 0)
	equal(t, resultOf(t, directory)["skipped"].(string), repoName+"#43299 is closed")
}

func TestAGradedHeadOriginNoLongerServesReadsTheWholeChange(t *testing.T) {
	directory := finderJobDir(t, map[string]any{"since": shaOld, "prior": priorAt(149302, "finder: ok a269784")})
	shell, repo := newFinderFake(t, replyOf())
	repo.gone = []string{shaOld}
	mustFinder(t, directory, shell)
	holdsAll(t, readText(t, directory, "question-1.md"), "This pass reads the PR's whole change: `git diff "+shaOwn+"^ "+shaHead+"`.")
	readText(t, directory, "rows.md")
	equal(t, shell.posted[0]["--text"], "finder: ok "+shaHead+" sol finder "+wholeRecord+": no majors")
}

func TestAGradedHeadWithNoOwnCommitPastTheBaseReadsTheWholeChangeWithNoRangeDiff(t *testing.T) {
	directory := finderJobDir(t, map[string]any{"since": shaOld, "prior": priorAt(149302, "finder: ok a269784")})
	shell, repo := newFinderFake(t, replyOf())
	repo.resolved = map[string]string{shaOld: shaOld}
	mustFinder(t, directory, shell)
	holdsAll(t, readText(t, directory, "question-1.md"), "This pass reads the PR's whole change: `git diff "+shaOwn+"^ "+shaHead+"`.")
	equal(t, len(shell.gitCalls("--no-lazy-fetch", "range-diff")), 0)
	equal(t, shell.posted[0]["--text"], "finder: ok "+shaHead+" sol finder "+wholeRecord+": no majors")
}

func amendedRepo(repo *finderRepo, marks ...string) {
	repo.own[shaBase+".."+shaOld] = []string{shaOldOwn}
	repo.resolved = map[string]string{shaOld: shaOld}
	repo.ranges = map[string]string{earlierRange: pairLines(marks...)}
}

func TestAnAmendedCommitThatOnlyDropsAHunkTheBaseNowCarriesIsACarry(t *testing.T) {
	directory := finderJobDir(t, map[string]any{"since": shaOld, "prior": priorAt(149302, "finder: ok a269784")})
	shell, repo := newFinderFake(t)
	amendedRepo(repo, "=", "!")
	repo.shows = map[string]string{
		shaParent[:11]:             landedDiff[shaParent[:11]],
		shaBase + ":api/parent.go": "landed()\n",
		shaOldOwn[:11]:             patchOf("-poll()", "+stream()", "+azurePrefix()"),
		shaOwn[:11]:                patchOf("-poll()", "+stream()"),
		shaBase + ":go/follow.go":  "func f() {\n\tazurePrefix()\n}\n",
	}
	mustFinder(t, directory, shell)
	equal(t, len(shell.asks()), 0)
	sameList(t, shell.texts(), []string{"finder: ok " + shaHead[:7] + " carried from #149302"})
}

func TestAnAmendedCommitThatDropsAHunkTheBaseLacksIsRead(t *testing.T) {
	directory := finderJobDir(t, map[string]any{"since": shaOld, "prior": priorAt(149302, "finder: ok a269784")})
	shell, repo := newFinderFake(t, replyOf())
	amendedRepo(repo, "=", "!")
	repo.shows = map[string]string{
		shaParent[:11]:             landedDiff[shaParent[:11]],
		shaBase + ":api/parent.go": "landed()\n",
		shaOldOwn[:11]:             patchOf("-poll()", "+stream()", "+azurePrefix()"),
		shaOwn[:11]:                patchOf("-poll()", "+stream()"),
		shaBase + ":go/follow.go":  "func f() {\n\tpoll()\n}\n",
	}
	mustFinder(t, directory, shell)
	holdsAll(t, readText(t, directory, "question-1.md"), "the range-diff shows for its pair ("+shaOwn[:11]+")")
	equal(t, len(shell.posted), 1)
}

func TestAReplayThatDropsAnEarlierCommitTheBaseLacksReadsItsRemovalAndCarriesNothing(t *testing.T) {
	directory := finderJobDir(t, map[string]any{"since": shaOld, "prior": priorAt(149302, "finder: ok a269784")})
	shell, repo := newFinderFake(t, replyOf())
	amendedRepo(repo, "=", "=")
	repo.shows[shaBase+":api/parent.go"] = "pending()\n"
	mustFinder(t, directory, shell)
	holdsAll(t, readText(t, directory, "question-1.md"), "the removal of each dropped commit the base lacks, read with `git show <sha>` ("+shaParent[:11]+")")
	equal(t, shell.posted[0]["--text"], "finder: ok "+shaHead+" sol finder over delta "+shaOld[:7]+".."+shaHead[:7]+": no majors")
}

func TestAReplayWhoseDroppedCommitMovedIntoTheBaseAmendedCarriesTheGradedVerdict(t *testing.T) {
	directory := finderJobDir(t, map[string]any{"since": shaOld, "prior": priorAt(149302, "finder: ok a269784")})
	shell, repo := newFinderFake(t)
	amendedRepo(repo, "=", "=")
	repo.shows[shaBase+":api/parent.go"] = "pending()\n"
	repo.subjects = []string{"api: the parent's commit before its restack"}
	mustFinder(t, directory, shell)
	equal(t, len(shell.asks()), 0)
	sameList(t, shell.texts(), []string{"finder: ok " + shaHead[:7] + " carried from #149302"})
}

func TestAnEarlierHeadsLowerStackCommitTheBaseNowCarriesStaysOutOfTheRangeDiff(t *testing.T) {
	directory := finderJobDir(t, map[string]any{"since": shaOld, "prior": priorAt(149302, "finder: ok a269784")})
	shell, repo := newFinderFake(t)
	amendedRepo(repo, "=", "=")
	repo.shows[shaBase+":api/parent.go"] = "pending()\n"
	repo.own[shaBase+".."+shaOld] = []string{shaParentTip, shaOldOwn}
	repo.commitSubjects = map[string]string{shaParentTip: "sand: delete the old Go tree"}
	repo.subjects = []string{"api: the parent's commit before its restack", "sand: delete the old Go tree"}
	mustFinder(t, directory, shell)
	sameList(t, shell.texts(), []string{"finder: ok " + shaHead[:7] + " carried from #149302"})
}

func TestAFailedCommandReportsTheEndOfItsTracebackWhereTheExceptionIsNamed(t *testing.T) {
	trace := "Traceback (most recent call last):\n" + strings.Repeat("  File \"rulings.py\", line 2832, in <module>\n    raise SystemExit(main())\n", 20) + "subprocess.TimeoutExpired: ccn ledger show timed out\n"
	err := &ExitError{Argv: []string{"rulings.py", "plan", "--repo", "/repo", "--drive"}, Code: 1, Stderr: trace}
	reported := err.Error()
	if !strings.HasSuffix(reported, "subprocess.TimeoutExpired: ccn ledger show timed out") {
		t.Errorf("reported %q", reported)
	}
	holdsAll(t, reported, "rulings.py plan --repo /repo exited 1: ")
}

func TestEveryCommandAPassRunsLogsItsDuration(t *testing.T) {
	var logged bytes.Buffer
	live := &pass{Deps: Deps{Runner: shell.Exec{}, Log: slog.New(slog.NewTextHandler(&logged, nil)), Config: t.TempDir()}, ctx: context.Background()}
	if _, err := live.run([]string{"sh", "-c", "exit 0"}, "", time.Minute); err != nil {
		t.Fatal(err)
	}
	_, err := live.run([]string{"false"}, "", time.Minute)
	if !exited(err) {
		t.Fatalf("err = %v, want an exit", err)
	}
	holdsAll(t, logged.String(), `stage="sh -c exit 0"`, "stage=false", "seconds=")
}

func TestAPathListOfTheFailingSizeStillReadsTheHeadsOwnCommits(t *testing.T) {
	const paths = 4000
	directory, tail := strings.Repeat(strings.Repeat("d", 240)+"/", 3), strings.Repeat("x", 223)
	var stream strings.Builder
	stream.WriteString("blob\nmark :1\ndata 1\nx\ncommit refs/heads/base\nmark :2\ncommitter lr <lr@example.com> 1 +0000\ndata 4\nbase\n")
	stream.WriteString("commit refs/heads/head\ncommitter lr <lr@example.com> 2 +0000\ndata 4\nhead\nfrom :2\n")
	for index := range paths {
		fmt.Fprintf(&stream, "M 100644 :1 %s%06d-%s\n", directory, index, tail)
	}
	live := &pass{Deps: Deps{Runner: shell.Exec{}, Log: slog.New(slog.DiscardHandler), Config: t.TempDir()}, ctx: context.Background(), job: &Job{Checkout: t.TempDir()}}
	if _, err := live.git("init", "-q"); err != nil {
		t.Fatal(err)
	}
	if _, err := live.gitFed(stream.String(), "fast-import", "--quiet"); err != nil {
		t.Fatal(err)
	}
	base, err := live.git("rev-parse", "base")
	if err != nil {
		t.Fatal(err)
	}
	head, err := live.git("rev-parse", "head")
	if err != nil {
		t.Fatal(err)
	}

	own, err := live.beyond([]string{base}, head)
	if err != nil {
		t.Fatal(err)
	}

	sameList(t, own, []string{head})
}

func TestACommandPastItsDeadlineReportsATimeoutNotAnExit(t *testing.T) {
	live := &pass{Deps: Deps{Runner: shell.Exec{}, Log: slog.New(slog.DiscardHandler), Config: t.TempDir()}, ctx: context.Background()}
	_, err := live.run([]string{"sleep", "30"}, "", 50*time.Millisecond)
	var timedOut *TimeoutError
	if !errors.As(err, &timedOut) {
		t.Fatalf("err = %v, want a timeout", err)
	}
}

func TestAReplyCutOffAtTheModelsOutputCapIsAskedOnceMoreOnSol(t *testing.T) {
	capped := &ExitError{Argv: []string{"codex-ask"}, Code: 1, Stdout: `{"type":"error","message":"Incomplete response returned, reason: max_output_tokens"}`}
	directory := finderJobDir(t, map[string]any{"model": "luna"})
	shell, _ := newFinderFake(t, capped, replyOf())
	mustFinder(t, directory, shell)
	asks := shell.asks()
	equal(t, asks[0][2], "luna")
	equal(t, asks[0][slices.Index(asks[0], "-s")+1], filepath.Join(directory, "codex-1"))
	sameList(t, asks[1][:5], []string{"codex-ask", "-m", "sol", "--effort", "medium"})
	equal(t, asks[1][slices.Index(asks[1], "-s")+1], filepath.Join(directory, "codex-1-sol"))
	equal(t, len(shell.posted), 1)

	other := finderJobDir(t, map[string]any{"model": "luna"})
	shell, _ = newFinderFake(t, &ExitError{Argv: []string{"codex-ask"}, Code: 1, Stdout: "codex: rate limited"})
	if err := runFinderJob(t, other, shell); err == nil {
		t.Fatal("another codex failure passed")
	}
	equal(t, len(shell.asks()), 1)
}

func registered(t *testing.T, directory string, shell *fakeShell) {
	t.Helper()
	entry := filepath.Join(shell.config, "long-running", "drives", "d1.json")
	if err := os.MkdirAll(filepath.Dir(entry), 0o750); err != nil {
		t.Fatal(err)
	}
	data := fmt.Sprintf(`{"drive":"d1","cci_drive":"d1","ledger":"abc","repo":%q,"checkout":"/srv/checkout","sessions":[]}`, repoName)
	if err := os.WriteFile(entry, []byte(data), 0o600); err != nil {
		t.Fatal(err)
	}
	landed, err := json.Marshal([]LandedRow{
		{Key: "47001", Title: "sand: 🐛 the replica keeps its held marker", SHA: shaFixed},
		{Key: "47002", Title: "sand: ✨ replica metrics", SHA: strings.Repeat("b", 40)},
		{Key: "47003", Title: "api: 🐛 the parent read retries", SHA: shaOtherArea},
	})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(directory, LandedFile), landed, 0o600); err != nil {
		t.Fatal(err)
	}
}

func landedFiles(path string) string {
	return "@" + shaFixed + "\n\n" + path + "\ngo/sand/internal/replica/follow_test.go\n@" + shaOtherArea + "\n\napi/parent.go\n"
}

func TestASecondBugFixInAnAreaTheDriveAlreadyFixedPostsABlockingPointNamingTheEarlierFix(t *testing.T) {
	directory := finderJobDir(t, nil)
	shell, repo := newFinderFake(t, replyOf())
	shell.title = fixTitle
	registered(t, directory, shell)
	repo.landedLog = landedFiles("go/sand/internal/replica/follow.go")
	mustFinder(t, directory, shell)
	if !strings.HasPrefix(shell.posted[0]["--text"], "finder: major "+shaHead+" 1 open "+wholeRecord+": go/sand/internal/replica/follow.go:40 "+secondFix+".") {
		t.Errorf("posted %q", shell.posted[0]["--text"])
	}
	sameList(t, strings.Fields(landedReads(shell)[0]), []string{shaFixed, shaOtherArea})
}

func TestASecondFixWhoseBodyNamesTheClassItRemovesOrTheCutoverDeletionStandsOk(t *testing.T) {
	for _, body := range []string{
		"## Summary\nThe class: a resubscribe that reads its position twice. This fix removes it, since one owner holds the position.",
		"## Design\n- Invariant: a claim carries a fence only while its transfer waits. Single owner: `Control.Transfer`.",
		"## Design\n- One invariant, one owner: a pod takes no work it lacks room to journal, and `leaveIfDiskLow` is the one place that decides.",
		"## Design\n- Different concerns; the one owner of \"a disk takes no pod until it is clear\" is `journal_disk_quarantines`.",
		"## Summary\nThe replica reader is deleted at cutover, so this is its last fix.",
	} {
		directory := finderJobDir(t, nil)
		shell, repo := newFinderFake(t, replyOf())
		shell.title, shell.body = fixTitle, body
		registered(t, directory, shell)
		repo.landedLog = landedFiles("go/sand/internal/replica/follow.go")
		mustFinder(t, directory, shell)
		if !strings.HasPrefix(shell.posted[0]["--text"], "finder: ok "+shaHead+" ") {
			t.Errorf("posted %q", shell.posted[0]["--text"])
		}
	}
}

func TestACarriedSecondFixMajorIsReadAgainstTheBodyAsItStandsNow(t *testing.T) {
	graded := "finder: major a269784 1 open over whole 4be7a52^..a269784: go/sand/internal/replica/follow.go:40 " + secondFix + ". #43100 (sand: 🐛 the replica follows) fixed go/sand/internal/replica/follow.go"
	classLine := "## Summary\nThe class: a resubscribe that reads its position twice. This fix removes it, since one owner holds the position."
	cleared := "finder: ok %s carried from #149302, whose one major, the second fix, the PR body now answers"
	for name, c := range map[string]struct {
		head, body, want, id string
	}{
		"a carry whose body names the class":     {"a269784", classLine, cleared, ""},
		"a carry whose body names no class":      {"a269784", "## Summary\nThe replica resubscribes once.", "finder: major %s carried from #149302", ""},
		"a body edit at the head the major read": {shaHead[:7], classLine, cleared, ":after-149302"},
	} {
		t.Run(name, func(t *testing.T) {
			directory := finderJobDir(t, map[string]any{"prior": map[string]any{"seq": 149302, "lane": "finder-desk", "head": c.head, "text": graded}})
			shell, repo := newFinderFake(t)
			shell.title, shell.body = fixTitle, c.body
			repo.own[shaBase+".."+shaOld] = []string{shaOldOwn}
			repo.resolved = map[string]string{"a269784": shaOld}
			repo.ranges = map[string]string{earlierRange: pairLines("=", "=")}
			mustFinder(t, directory, shell)
			equal(t, len(shell.asks()), 0)
			equal(t, shell.posted[0]["--text"], fmt.Sprintf(c.want, shaHead[:7]))
			equal(t, shell.posted[0]["--id"], "finder:"+repoName+"#43299:"+shaHead+c.id)
		})
	}
}

func TestAPortOwesItsSecondFixAsAFollowUpAndAnyOtherBodyKeepsTheMajor(t *testing.T) {
	cite := "go/sand/internal/replica/follow.go:40"
	owed := "follow-ups owed after this port: " + cite + " " + classFixOwed + ". #47001 ("
	own := "the next tree's wiring drops the marker"
	for name, tc := range map[string]struct {
		body     string
		findings []map[string]any
		want     string
	}{
		"a port grades ok and names the follow-up": {
			"port of #49412\n\n" + summaryBody,
			nil,
			"finder: ok " + shaHead + " sol finder " + wholeRecord + ": no majors; " + owed,
		},
		"a port's own defect still holds it, beside the follow-up": {
			"port of #49412\n\n" + summaryBody,
			[]map[string]any{findingOf("major", cite, own)},
			"finder: major " + shaHead + " 1 open " + wholeRecord + ": " + cite + " " + own + "; " + owed,
		},
		"a body that only mentions a port keeps the major": {
			summaryBody + "\n\nport of #49412",
			nil,
			"finder: major " + shaHead + " 1 open " + wholeRecord + ": " + cite + " " + secondFix + ".",
		},
		"the port's follow-up PR keeps the major": {
			"follow-up to port of #49412\n\n" + summaryBody,
			nil,
			"finder: major " + shaHead + " 1 open " + wholeRecord + ": " + cite + " " + secondFix + ".",
		},
	} {
		t.Run(name, func(t *testing.T) {
			directory := finderJobDir(t, nil)
			shell, repo := newFinderFake(t, replyOf(tc.findings...))
			shell.title, shell.body = fixTitle, tc.body
			registered(t, directory, shell)
			repo.landedLog = landedFiles("go/sand/internal/replica/follow.go")
			mustFinder(t, directory, shell)
			if !strings.HasPrefix(shell.posted[0]["--text"], tc.want) {
				t.Errorf("posted %q, want the prefix %q", shell.posted[0]["--text"], tc.want)
			}
		})
	}
}

func TestACarriedGradeOnAPortClearsOnlyASecondFixThatWasItsOneMajor(t *testing.T) {
	fixes := ". #43100 (sand: 🐛 the replica follows) fixed go/sand/internal/replica/follow.go"
	cite := "go/sand/internal/replica/follow.go:40"
	whole := "finder: major a269784 1 open over whole 4be7a52^..a269784: " + cite + " "
	port := "port of #49412\n\n" + summaryBody
	classLine := "port of #49412\n\nThe class: a resubscribe that reads its position twice. This fix removes it, since one owner holds the position."
	cleared := "finder: ok " + shaHead[:7] + " carried from #149302, whose one major, the second fix, stands as a follow-up owed after this port: " + classFixOwed + fixes
	held := "finder: major " + shaHead[:7] + " carried from #149302"
	for name, c := range map[string]struct {
		graded, body, want string
	}{
		"a second fix on a body that opens as a port":                     {whole + secondFix + fixes, port, cleared},
		"a second fix on a body that only mentions a port":                {whole + secondFix + fixes, summaryBody + "\n\nport of #49412", held},
		"an own major beside a second fix already owed as a follow-up":    {whole + "the wiring drops the marker; follow-ups owed after this port: " + cite + " " + classFixOwed + fixes, port, held},
		"an own major beside an owed follow-up, on a body naming a class": {whole + "the wiring drops the marker; follow-ups owed after this port: " + cite + " " + classFixOwed + fixes, classLine, held},
	} {
		t.Run(name, func(t *testing.T) {
			directory := finderJobDir(t, map[string]any{"prior": map[string]any{"seq": 149302, "lane": "finder-desk", "head": "a269784", "text": c.graded}})
			shell, repo := newFinderFake(t)
			shell.title, shell.body = fixTitle, c.body
			repo.own[shaBase+".."+shaOld] = []string{shaOldOwn}
			repo.resolved = map[string]string{"a269784": shaOld}
			repo.ranges = map[string]string{earlierRange: pairLines("=", "=")}
			mustFinder(t, directory, shell)
			equal(t, len(shell.asks()), 0)
			equal(t, shell.posted[0]["--text"], c.want)
		})
	}
}

func TestAFixBesideAnEarlierFixInOneFlatDirectoryIsNoSecondFix(t *testing.T) {
	directory := finderJobDir(t, nil)
	shell, repo := newFinderFake(t, replyOf())
	shell.title = fixTitle
	registered(t, directory, shell)
	repo.landedLog = "@" + shaFixed + "\n\ngo/sand/internal/replica/marker.go\n@" + shaOtherArea + "\n\napi/parent.go\n"
	mustFinder(t, directory, shell)
	if !strings.HasPrefix(shell.posted[0]["--text"], "finder: ok "+shaHead+" ") {
		t.Errorf("posted %q", shell.posted[0]["--text"])
	}
}

func TestAChangeThatIsNoBugFixReadsNoLandedArea(t *testing.T) {
	directory := finderJobDir(t, nil)
	shell, _ := newFinderFake(t, replyOf())
	shell.title = "sand: ✨ the replica streams its position"
	registered(t, directory, shell)
	mustFinder(t, directory, shell)
	if !strings.HasPrefix(shell.posted[0]["--text"], "finder: ok "+shaHead+" ") {
		t.Errorf("posted %q", shell.posted[0]["--text"])
	}
	equal(t, len(landedReads(shell)), 0)
}

func TestALandedFixTheCloneLacksStopsThePassInsteadOfDroppingTheFix(t *testing.T) {
	directory := finderJobDir(t, nil)
	shell, _ := newFinderFake(t, replyOf())
	shell.title = fixTitle
	registered(t, directory, shell)
	read := shell.gitFn
	shell.gitFn = func(argv []string) (string, error) {
		if argv[1] == "log" && slices.Contains(argv, "--name-only") && slices.Contains(argv, shaFixed) {
			return "", &ExitError{Argv: argv, Code: 128, Stderr: "fatal: bad object " + shaFixed}
		}
		return read(argv)
	}
	if err := runFinderJob(t, directory, shell); err == nil {
		t.Fatal("the pass dropped a landed fix and settled")
	}
	holdsAll(t, resultOf(t, directory)["error"].(string), "bad object "+shaFixed)
	equal(t, len(shell.posted), 0)
}

func landedReads(shell *fakeShell) []string {
	var read []string
	for _, call := range shell.snapshot() {
		if call.Argv[0] == "git" && slices.Contains(call.Argv, "--name-only") && slices.Contains(call.Argv, "--no-walk=unsorted") {
			read = append(read, call.Stdin)
		}
	}
	return read
}

func TestAModelRateLimitIsAskedAgainAfterTheDelayItNamesNeverFailingThePass(t *testing.T) {
	limited := &ExitError{Argv: []string{"codex-ask"}, Code: 1, Stdout: `{"error":{"message":"rate limit exceeded: Rate limit reached for gpt-6.1-sol in organization org-x on tokens per min (TPM): Limit 40000000, Used 39876988, Requested 180914. Please try again in 86ms. Visit https://platform.openai.com/account/rate-limits"}}`}
	directory := finderJobDir(t, nil)
	shell, _ := newFinderFake(t, limited, limited, replyOf())
	mustFinder(t, directory, shell)
	sessions := make([]string, 0, 3)
	for _, ask := range shell.asks() {
		sessions = append(sessions, ask[slices.Index(ask, "-s")+1])
	}
	sameList(t, sessions, []string{filepath.Join(directory, "codex-1"), filepath.Join(directory, "codex-1-r1"), filepath.Join(directory, "codex-1-r2")})
	if !slices.Equal(shell.slept, []time.Duration{5 * time.Second, 10 * time.Second}) {
		t.Errorf("slept %v", shell.slept)
	}
	equal(t, len(shell.posted), 1)
}

func TestARateLimitThatOutlastsEveryFloorKeepsWaitingInsideThePass(t *testing.T) {
	limited := &ExitError{Argv: []string{"codex-ask"}, Code: 1, Stdout: "Rate limit reached for gpt-6.1-sol. Please try again in 167ms."}
	directory := finderJobDir(t, nil)
	shell, _ := newFinderFake(t, limited, limited, limited, limited, limited, limited, limited, limited, replyOf())
	mustFinder(t, directory, shell)
	equal(t, len(shell.asks()), 9)
	want := []time.Duration{5 * time.Second, 10 * time.Second, 20 * time.Second, 40 * time.Second, 60 * time.Second, 60 * time.Second, 60 * time.Second, 60 * time.Second}
	if !slices.Equal(shell.slept, want) {
		t.Errorf("slept %v, want %v", shell.slept, want)
	}
	equal(t, len(shell.posted), 1)
}

func TestARateLimitNamingMinutesWaitsThatLong(t *testing.T) {
	limited := &ExitError{Argv: []string{"codex-ask"}, Code: 1, Stdout: "Rate limit reached for gpt-6.1-sol. Please try again in 1m30s."}
	directory := finderJobDir(t, nil)
	shell, _ := newFinderFake(t, limited, replyOf())
	mustFinder(t, directory, shell)
	if !slices.Equal(shell.slept, []time.Duration{90 * time.Second}) {
		t.Errorf("slept %v", shell.slept)
	}
}

func TestACancelDuringARateLimitPauseStartsNoFurtherCodexRun(t *testing.T) {
	directory := finderJobDir(t, nil)
	limited := &ExitError{Argv: []string{"codex-ask"}, Code: 1, Stdout: "Rate limit reached for gpt-6.1-sol. Please try again in 86ms."}
	shell, _ := newFinderFake(t, limited, replyOf())
	deps := shell.deps()
	deps.Sleep = func(time.Duration) {
		if err := os.WriteFile(filepath.Join(directory, CancelledFile), []byte(shaNewer+"\n"), 0o600); err != nil {
			t.Error(err)
		}
	}
	if err := Finder(context.Background(), directory, deps); err != nil {
		t.Fatal(err)
	}
	equal(t, len(shell.asks()), 1)
	equal(t, len(shell.posted), 0)
	equal(t, resultOf(t, directory)["skipped"].(string), repoName+"#43299 at "+shaHead[:12]+" was superseded by "+shaNewer[:12]+" while codex retried")
}

func TestAFinderQuestionCarriesTheReportOfTheBugItemItsJobNames(t *testing.T) {
	report := map[string]any{"seq": 450055, "lane": "slack-bugs-2", "text": "Josh said the session is offline"}
	for name, c := range map[string]struct {
		items   []any
		unknown []any
		want    string
	}{
		"named":   {[]any{report}, nil, "- cci #450055 from slack-bugs-2: Josh said the session is offline"},
		"unknown": {[]any{report}, []any{"7"}, "- item 7: no report carries this number, so it grounds nothing"},
		"none":    {nil, nil, "The\n   items this PR's body names:\n(none)"},
	} {
		t.Run(name, func(t *testing.T) {
			shell, _ := newFinderFake(t, replyOf())
			directory := finderJobDir(t, map[string]any{"items": c.items, "unknown_items": c.unknown})
			mustFinder(t, directory, shell)
			question := readText(t, directory, "question-1.md")
			holdsAll(t, question, c.want)
			if strings.Contains(question, "{items}") {
				t.Errorf("question left {items} unfilled:\n%s", question)
			}
		})
	}
}

func amendedPastAGrade(t *testing.T, prior map[string]any, replies ...any) (string, *fakeShell) {
	t.Helper()
	changes := map[string]any{"since": shaOld, "prior": prior}
	if prior == nil {
		changes = nil
	}
	directory := finderJobDir(t, changes)
	shell, repo := newFinderFake(t, replies...)
	amendedRepo(repo, "=", "!")
	repo.shows = map[string]string{
		shaParent[:11]:             landedDiff[shaParent[:11]],
		shaBase + ":api/parent.go": "landed()\n",
		shaOldOwn[:11]:             patchOf("-poll()", "+stream()", "+azurePrefix()"),
		shaOwn[:11]:                patchOf("-poll()", "+stream()"),
		shaBase + ":go/follow.go":  "func f() {\n\tpoll()\n}\n",
	}
	return directory, shell
}

func TestAnAmendedHeadPastAnOkTheSonnetReadCallsUnchangedCarriesTheOkWithNoCodexPass(t *testing.T) {
	why := "The amend only drops azurePrefix(), which no caller in the PR reaches."
	directory, shell := amendedPastAGrade(t, priorAt(149302, "finder: ok a269784"))
	shell.judged = []any{regradeAnswer{carryAnswer, why}}
	mustFinder(t, directory, shell)
	equal(t, len(shell.asks()), 0)
	judges := shell.judges()
	equal(t, len(judges), 1)
	holdsAll(t, strings.Join(judges[0], " "), "--model sonnet", "--effort high", "Bash(git range-diff:*)")
	holdsAll(t, readText(t, directory, regradeQuestion),
		"`git range-diff "+earlierRange+"`", "Amended commits: "+shaOwn[:11]+". New commits: none.",
		"- cci #149302 from p8a-step9: finder: ok a269784\n", finderMeans, summaryBody,
	)
	want := "finder: ok " + shaHead + " carried from #149302 at " + shaOld[:7] + ", judged unchanged in meaning by sonnet over its range-diff: The amend only drops azurePrefix(), which no caller in the PR reaches"
	sameList(t, shell.texts(), []string{want})
	equal(t, FinderGrade(want), finderPass)
	outcome := resultOf(t, directory)
	if !reflect.DeepEqual(outcome, map[string]any{"verdict": "ok", "record": want, "posted": "#150001", "regrade": "carry: " + why}) {
		t.Errorf("outcome = %v", outcome)
	}
}

func TestAnAmendedHeadTheSonnetReadDoesNotCarryGetsTheSolDeltaPassAndKeepsTheReadsAnswer(t *testing.T) {
	cases := []struct {
		name   string
		judged any
		wants  []string
	}{
		{"sol", regradeAnswer{solAnswer, "The amend drops the azurePrefix() call the base lacks, so follow.go behaves differently."}, []string{"sol: The amend drops the azurePrefix() call"}},
		{"failed", &ExitError{Code: 1, Stdout: `{"is_error":true,"result":"Not logged in"}`}, []string{"sol: the read failed: ", "the reader exited 1: Not logged in"}},
		{"prose", "Both heads look about the same to me.", []string{"sol: the read gave no answer: Both heads look about the same to me."}},
		{"unknown", regradeAnswer{"maybe", "unclear"}, []string{"sol: the read gave no answer: ", `"answer":"maybe"`}},
		{"reasonless", regradeAnswer{carryAnswer, " "}, []string{"sol: the read gave no answer: "}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			directory, shell := amendedPastAGrade(t, priorAt(149302, "finder: ok a269784"), replyOf())
			shell.judged = []any{c.judged}
			mustFinder(t, directory, shell)
			equal(t, len(shell.judges()), 1)
			equal(t, len(shell.asks()), 1)
			equal(t, shell.posted[0]["--text"], "finder: ok "+shaHead+" sol finder over delta "+shaOld[:7]+".."+shaHead[:7]+": no majors")
			holdsAll(t, resultOf(t, directory)["regrade"].(string), c.wants...)
		})
	}
}

func TestAHeadWithNoOkAtItsEarlierHeadNeverAsksTheSonnetRead(t *testing.T) {
	cases := []struct {
		name  string
		prior map[string]any
	}{
		{"no earlier grade", nil},
		{"earlier major", priorAt(149302, "finder: major a269784 1 open: walk.go:9 skips the volume link")},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			directory, shell := amendedPastAGrade(t, c.prior, replyOf())
			mustFinder(t, directory, shell)
			equal(t, len(shell.judges()), 0)
			equal(t, len(shell.asks()), 1)
			wantNoFile(t, directory, regradeQuestion)
			if _, judged := resultOf(t, directory)["regrade"]; judged {
				t.Error("a pass the Sonnet read never saw names its answer")
			}
		})
	}
}
