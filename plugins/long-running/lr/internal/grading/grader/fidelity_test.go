package grader

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"
	"time"
)

const (
	baselineName  = "c57cad26 (release 10435, fleet sand 0.162.1876)"
	portPhrase    = "pure port, no interaction"
	testOnly      = "test-only or CI-only, no non-test code"
	fidelityDiff  = "diff --git a/go/sand/next/replica/follow.go b/go/sand/next/replica/follow.go\n--- a/go/sand/next/replica/follow.go\n+++ b/go/sand/next/replica/follow.go\n@@ -40,0 +41,3 @@\n+resubscribe()\n"
	fidelitySwept = "The resubscribe hunk keeps the fetched position across a cut and every caller passes it through, so the streaming row holds."
	settledPt     = "an empty listing returns TxNotAvailable at plan.rs:108 before subscribing"
	openPoint     = "replica.go:452 rejects a stopped share below its stop"
	permissions   = "dashboard/src/iris/lib/IrisPermissionsContext.tsx"
	graphiqlTitle = "api: 🔥 delete the /graphiql page, which loaded GraphiQL and React from esm.sh at view time"
	graphiqlBody  = "## Motivation\n\nOwner, Nov 1 10:41 AM: \"remove entirely the code that installs litestream at runtime ... any other similar things?\" The coordinator's ruling (cci #289128) was to delete this page as the third of three rows under one line: nothing we ship loads code from a third-party CDN at run time.\n"
)

var (
	fidelityKeys = []string{"rule/949e6be", "prompt/1026-105052"}
	samePatch    = map[string]string{shaOwn + "^ " + shaHead: "5f1e", shaOld + "~1 " + shaOld: "5f1e"}
	otherPatch   = map[string]string{shaOwn + "^ " + shaHead: "5f1e", shaOld + "~1 " + shaOld: "77aa"}
	lane         = map[string]string{"login": "yasyf"}
	bot          = map[string]string{"login": "forge-pr-reviewer"}
	baseComments = []map[string]any{
		{"author": lane, "body": "### Rulings\n- ports old-tree 304c1e7ea5e (#41725)"},
		{"author": lane, "body": "Body addendum\nThe caller trace for GetIrisTeamCreds runs through serve only."},
		{"author": bot, "body": "ci is green"},
	}
	deskOK          = gradeOf(170001, "fidelity-desk-5", "fidelity: ok "+shaOld+" "+portPhrase+": ports old-tree 304c1e7ea5e (#41725) line for line.")
	pipelineMissing = gradeOf(170006, FidelityLane, "fidelity: missing "+shaOld+" plan:811,rule/8330d10 "+settledPt+"; "+openPoint)
)

func gradeOf(seq int, name, text string) map[string]any {
	return map[string]any{"seq": seq, "lane": name, "text": text}
}

type fidelityRepo struct {
	patches   map[string]string
	known     []string
	rangeDiff string
	shows     map[string]string
	diff      string
	names     string
	stat      string
}

func (r *fidelityRepo) git(full []string) (string, error) {
	if full[0] == "sh" {
		key := full[len(full)-2] + " " + full[len(full)-1]
		if patch, read := r.patches[key]; read {
			return patch + " " + full[len(full)-1] + "\n", nil
		}
		return "", nil
	}
	argv := full[1:]
	if argv[0] == "--no-lazy-fetch" {
		argv = argv[1:]
	}
	switch argv[0] {
	case "ls-remote":
		return shaBase + "\tHEAD\n" + shaBase + "\trefs/heads/dev\n", nil
	case "fetch", "merge-base":
		return "", nil
	case "log":
		if argv[1] == "--no-walk=unsorted" {
			return subjectsOf(argv[3:], nil), nil
		}
		return "signal/stream.go\n", nil
	case "rev-list":
		span := argv[slices.Index(argv, "--")-1]
		if strings.HasSuffix(span, shaHead) {
			return shaOwn + "\n", nil
		}
		return shaOldOwn + "\n", nil
	case "range-diff":
		return r.rangeDiff, nil
	case "show":
		return r.shows[argv[len(argv)-1]], nil
	case "rev-parse":
		name := strings.TrimSuffix(argv[len(argv)-1], "^{commit}")
		for _, sha := range r.known {
			if name == sha || name == sha[:7] {
				return sha + "\n", nil
			}
		}
		return "", &ExitError{Argv: full, Code: 1}
	case "diff":
		if slices.Contains(argv, "--name-only") {
			return r.names, nil
		}
		if slices.Contains(argv, "--numstat") {
			return r.stat, nil
		}
		return r.diff, nil
	}
	return "", fmt.Errorf("unexpected git %v", argv)
}

func newFidelityFake(t *testing.T, patches map[string]string, replies ...any) (*fakeShell, *fidelityRepo) {
	t.Helper()
	repo := &fidelityRepo{
		patches:   patches,
		known:     []string{shaOld},
		rangeDiff: fmt.Sprintf("1:  %s ! 1:  %s resubscribe\n", shaOldOwn[:7], shaOwn[:7]),
		diff:      fidelityDiff,
		shows: map[string]string{
			shaOldOwn[:7]: "diff --git a/f.go b/f.go\n+old\n",
			shaOwn[:7]:    "diff --git a/f.go b/f.go\n+new\n",
		},
	}
	var rows strings.Builder
	for _, key := range fidelityKeys {
		fmt.Fprintf(&rows, "### %s | scope | in-force | said 1 | 2026-10-22\n> the owner's words\n\n", key)
	}
	rows.WriteString("slice-hash: 0123456789ab\n")
	shell := &fakeShell{
		dir:      t.TempDir(),
		config:   t.TempDir(),
		replies:  replies,
		state:    "OPEN",
		title:    "sand: 🐛 a cut signal resubscribes",
		body:     summaryBody,
		heads:    []string{shaHead},
		comments: [][]map[string]any{baseComments},
		rows:     rows.String(),
		counting: true,
		timeouts: map[string]time.Duration{},
		gitFn:    repo.git,
	}
	return shell, repo
}

func fidelityDir(t *testing.T, changes map[string]any) string {
	t.Helper()
	oldApp := map[string]any{
		"seq":  170002,
		"lane": "fidelity-desk-5",
		"head": shaOld[:7],
		"line": "old-app: unchanged " + shaOld[:7] + " baseline " + baselineName + "; go/sand/next only",
	}
	spec := map[string]any{
		"drive":         "d1",
		"repo":          repoName,
		"pr":            "43299",
		"head":          shaHead,
		"lane":          "p8a-step9",
		"to":            []string{"desk-runner", "board-refresher-2"},
		"readers":       []string{"fidelity-desk-4", "fidelity-desk-5"},
		"lanes":         []string{"fidelity-desk-4", "fidelity-desk-5", FidelityLane},
		"grades":        []any{deskOK},
		"old_apps":      []any{oldApp},
		"corrected":     []any{},
		"settled":       map[string]any{},
		"rulings":       []any{},
		"contract":      "contract: builds the resubscribe from the fetched position; deletes nothing",
		"sources":       "",
		"exempt":        map[string]string{"port": portPhrase, "test-only": testOnly},
		"exempt_grades": []string{portPhrase, testOnly},
		"baseline":      baselineName,
		"checkout":      "/srv/checkout",
		"model":         "sol",
		"fast":          true,
	}
	return writeJob(t, filepath.Join(t.TempDir(), FidelityLane, "43299-"+shaHead[:12]+"-149000"), spec, changes)
}

func mustFidelity(t *testing.T, directory string, shell *fakeShell) {
	t.Helper()
	if err := Fidelity(context.Background(), directory, shell.deps()); err != nil {
		t.Fatal(err)
	}
}

func TestASpriteFidelityReturnsItsGradeForCoordinatorPosting(t *testing.T) {
	directory := fidelityDir(t, map[string]any{"sprite": "grader-a", "grades": []any{}, "baseline": ""})
	shell, _ := newFidelityFake(t, nil, fidelityReplyOf(nil))
	deps := shell.deps()
	deps.Post = nil
	if err := Fidelity(context.Background(), directory, deps); err != nil {
		t.Fatal(err)
	}
	holdsAll(t, readText(t, directory, ResultFile), `"verdict":"ok"`)
	if len(shell.posted) != 0 {
		t.Fatal("the Sprite posted to a local replica", shell.posted)
	}
	if err := Repost(context.Background(), directory, shell.post); err != nil {
		t.Fatal(err)
	}
	if len(shell.posted) == 0 {
		t.Fatal("the coordinator received no grade")
	}
	holdsAll(t, shell.posted[0]["--text"], "fidelity: ok "+shaHead)
	for index, post := range shell.posted {
		equal(t, post["--id"], fmt.Sprintf("fidelity:%s#43299:%s:%d", repoName, shaHead, index))
		if filepath.Dir(post["--path"]) != directory {
			t.Fatal("a grade body would be left on the Sprite", post)
		}
		readText(t, directory, filepath.Base(post["--path"]))
	}
}

func TestAPassPrintsItsRowsFromTheAnswersItsJobCarries(t *testing.T) {
	directory := fidelityDir(t, nil)
	shell, _ := newFidelityFake(t, nil, fidelityReplyOf(nil))
	mustFidelity(t, directory, shell)
	carried := []string{"--answers", filepath.Join(directory, AnswersFile)}
	for _, call := range shell.snapshot() {
		if call.Argv[0] == rulingsPath && call.Argv[1] == "slice" {
			if slices.Index(call.Argv, carried[0]) < 0 || call.Argv[slices.Index(call.Argv, carried[0])+1] != carried[1] {
				t.Errorf("slice = %v, want %v", call.Argv, carried)
			}
			return
		}
	}
	t.Fatal("no pass printed its rows")
}

func fidelityReplyOf(changes map[string]any) map[string]any {
	reply := map[string]any{
		"verdict": "ok",
		"summary": "Resubscribes from the fetched position after a cut.",
		"swept":   fidelitySwept,
		"checked": fidelityKeys,
		"names":   []string{"poll", "resubscribe"},
		"missing": []any{},
		"touches": []string{},
		"exempt":  "none",
		"reader":  "",
		"old_app": map[string]string{"grade": "unchanged", "why": "go/sand/next only"},
	}
	for key, value := range changes {
		reply[key] = value
	}
	return reply
}

func missingOf(key, line string) map[string]any {
	return map[string]any{"key": key, "line": line, "settled": 0}
}

func TestARestackPatchIdenticalToAGradedOkHeadCarriesItsPortPhraseAndOldAppLineWithNoModelCall(t *testing.T) {
	directory := fidelityDir(t, nil)
	shell, _ := newFidelityFake(t, samePatch)
	mustFidelity(t, directory, shell)
	equal(t, len(shell.asks()), 0)
	texts := shell.texts()
	equal(t, texts[0], "fidelity: ok "+shaHead+" "+portPhrase+": carried from "+shaOld[:7]+" (cci #170001), patch-identical by git patch-id over its own commits.")
	equal(t, texts[1], "old-app: unchanged "+shaHead[:7]+" baseline "+baselineName+"; go/sand/next only")
	for _, post := range shell.posted {
		equal(t, post["--lane"], FidelityLane)
		equal(t, post["--to"], "p8a-step9,desk-runner,board-refresher-2")
	}
	outcome := resultOf(t, directory)
	if !reflect.DeepEqual(outcome, map[string]any{"verdict": "carried", "from": shaOld, "posted": []any{"#150002", "#150003"}}) {
		t.Errorf("outcome = %v", outcome)
	}
}

func TestEveryEarlierGradedHeadIsFetchedAndPatchReadAtOnceAndTheNewestMatchCarries(t *testing.T) {
	newer := gradeOf(170005, "fidelity-desk-5", "fidelity: ok "+shaMoved+" an earlier restack that reads the same.")
	read := map[string]chan struct{}{shaOld: make(chan struct{}), shaMoved: make(chan struct{})}
	guarded := make(chan bool, 2)
	directory := fidelityDir(t, map[string]any{"grades": []any{deskOK, newer}})
	shell, repo := newFidelityFake(t, samePatch)
	repo.known = []string{shaOld, shaMoved}
	shell.before = func(command Command) {
		if command.Argv[0] != "sh" {
			return
		}
		done, tracked := read[command.Argv[len(command.Argv)-1]]
		if !tracked {
			return
		}
		close(done)
		guarded <- waited(read[shaOld]) && waited(read[shaMoved])
	}
	mustFidelity(t, directory, shell)
	if first, second := <-guarded, <-guarded; !first || !second {
		t.Errorf("the patch reads ran apart: %v %v", first, second)
	}
	equal(t, len(shell.asks()), 0)
	equal(t, shell.texts()[0], "fidelity: ok "+shaHead+" carried from "+shaOld[:7]+" (cci #170001), patch-identical by git patch-id over its own commits.")
}

func TestACarryTakesItsClassFromARootCorrectionThenTheNewestDeskGradeThenItsOwnPriorPhrase(t *testing.T) {
	first := gradeOf(170000, FidelityLane, "fidelity: ok "+shaMoved+" "+testOnly+": the added commit fixes a test.")
	pipeline := gradeOf(170003, FidelityLane, "fidelity: ok "+shaOld+" "+testOnly+": carried from "+shaMoved[:7]+" (cci #170000), patch-identical by git patch-id over its own commits.")
	desk := gradeOf(170004, "fidelity-desk-4", "fidelity: ok "+shaMoved+" tap row: it ends a moved share's watch, an on-purpose old-app fix.")
	deskSaid := gradeOf(170004, "fidelity-desk-4", "fidelity: ok "+shaMoved+" "+testOnly+": every commit is a test.")
	cases := []struct {
		name      string
		grades    []any
		corrected []int
		said      string
	}{
		{"own-phrase", []any{first, pipeline}, nil, testOnly + ": "},
		{"desk-tap-row", []any{first, pipeline, desk}, nil, ""},
		{"root-corrected", []any{first, pipeline}, []int{170003}, ""},
		{"root-corrected-upchain", []any{first, pipeline}, []int{170000}, ""},
		{"root-over-desk", []any{first, pipeline, deskSaid}, []int{170003}, ""},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			directory := fidelityDir(t, map[string]any{"grades": c.grades, "corrected": c.corrected})
			shell, _ := newFidelityFake(t, samePatch)
			mustFidelity(t, directory, shell)
			equal(t, shell.texts()[0], "fidelity: ok "+shaHead+" "+c.said+"carried from "+shaOld[:7]+" (cci #170003), patch-identical by git patch-id over its own commits.")
		})
	}
}

func TestAPatchIdenticalHeadCarriesAMissingRootSettledAsOkNamingTheDecide(t *testing.T) {
	carriedGrade := gradeOf(170008, FidelityLane, "fidelity: ok "+shaOld+" carried from "+shaMoved[:7]+" (cci #170006), patch-identical by git patch-id over its own commits, settled by coordinator #170007.")
	moved := gradeOf(170006, FidelityLane, strings.ReplaceAll(pipelineMissing["text"].(string), shaOld, shaMoved))
	settled := map[string]int{"170006": 170007}
	cases := []struct {
		name    string
		grades  []any
		settled map[string]int
		carried int
	}{
		{"root-settled", []any{pipelineMissing}, settled, 170006},
		{"settled-up-the-chain", []any{moved, carriedGrade}, settled, 170008},
		{"unsettled", []any{pipelineMissing}, map[string]int{}, 0},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			directory := fidelityDir(t, map[string]any{"grades": c.grades, "settled": c.settled})
			shell, _ := newFidelityFake(t, samePatch, fidelityReplyOf(nil))
			mustFidelity(t, directory, shell)
			if len(c.settled) == 0 {
				equal(t, len(shell.asks()), 1)
				return
			}
			equal(t, len(shell.asks()), 0)
			equal(t, shell.texts()[0], fmt.Sprintf("fidelity: ok %s carried from %s (cci #%d), patch-identical by git patch-id over its own commits, settled by coordinator #170007.", shaHead, shaOld[:7], c.carried))
		})
	}
}

func TestASettledMissingCarriesOnlyWhileRangeDiffFindsNoOwnCommitChanged(t *testing.T) {
	realigned := map[string]string{
		shaOldOwn[:7]: "diff --git a/types.go b/types.go\n+\tapiBase     *url.URL\n",
		shaOwn[:7]:    "diff --git a/types.go b/types.go\n+\tapiBase       *url.URL\n",
	}
	waits := map[string]string{
		shaOldOwn[:7]: "diff --git a/copyup.rs b/copyup.rs\n+free(pass);\n",
		shaOwn[:7]:    "diff --git a/copyup.rs b/copyup.rs\n+activity.idle().await;\n+free(pass);\n",
	}
	settled := map[string]any{"grades": []any{pipelineMissing}, "settled": map[string]int{"170006": 170007}}

	directory := fidelityDir(t, settled)
	shell, repo := newFidelityFake(t, otherPatch)
	repo.shows = realigned
	mustFidelity(t, directory, shell)
	equal(t, len(shell.asks()), 0)
	equal(t, shell.texts()[0], "fidelity: ok "+shaHead+" carried from "+shaOld[:7]+" (cci #170006), patch-identical by git range-diff over its own commits, settled by coordinator #170007.")
	outcome := resultOf(t, directory)
	if !reflect.DeepEqual(outcome, map[string]any{"verdict": "carried", "from": shaOld, "posted": []any{"#150002", "#150003"}}) {
		t.Errorf("outcome = %v", outcome)
	}

	read := fidelityDir(t, settled)
	shell, repo = newFidelityFake(t, otherPatch, fidelityReplyOf(nil))
	repo.shows = waits
	mustFidelity(t, read, shell)
	equal(t, len(shell.asks()), 1)
	holdsAll(t, readText(t, read, "question-1.md"), "An earlier head, "+shaOld+", was reviewed.", "only the hunks the range-diff shows for its pair ("+shaOwn[:7]+")", "- "+settledPt+" (coordinator #170007)")
}

func TestARestackWhoseCommitsRangeDiffEqualCarriesASettledMissingBesideADeskOk(t *testing.T) {
	desk := gradeOf(170005, "fidelity-desk-4", "fidelity: ok "+shaOld+" new-tree fix, stays a tap row.")
	directory := fidelityDir(t, map[string]any{"grades": []any{desk, pipelineMissing}, "settled": map[string]int{"170006": 170007}})
	shell, repo := newFidelityFake(t, otherPatch)
	repo.rangeDiff = fmt.Sprintf("1:  %s = 1:  %s resubscribe\n", shaOldOwn[:7], shaOwn[:7])
	mustFidelity(t, directory, shell)
	equal(t, len(shell.asks()), 0)
	equal(t, shell.texts()[0], "fidelity: ok "+shaHead+" carried from "+shaOld[:7]+" (cci #170006), patch-identical by git range-diff over its own commits, settled by coordinator #170007.")
}

func TestAFidelityJobTheRunnerCancelledAsksCodexNothingAndFails(t *testing.T) {
	directory := fidelityDir(t, nil)
	if err := os.WriteFile(filepath.Join(directory, CancelledFile), []byte("its deadline\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	shell, _ := newFidelityFake(t, otherPatch)
	if err := Fidelity(context.Background(), directory, shell.deps()); err == nil {
		t.Fatal("a cancelled pass settled as if it had graded")
	}
	equal(t, len(shell.asks()), 0)
	if said := resultOf(t, directory)["error"].(string); !strings.Contains(said, "was cancelled by its deadline before codex read it") {
		t.Errorf("error = %q", said)
	}
}

func TestANewHeadsMissingPointThatRepeatsASettledOneWordForWordIsDropped(t *testing.T) {
	settledGrade := gradeOf(170006, FidelityLane, "fidelity: missing "+shaOld+" plan:811 "+settledPt)
	cases := []struct {
		name    string
		missing []string
		settled map[string]int
		posted  string
	}{
		{"unsettled-point-kept", []string{settledPt, openPoint}, map[string]int{}, "fidelity: missing " + shaHead + " plan:811 " + settledPt + "; " + openPoint},
		{"all-settled", []string{settledPt}, map[string]int{"170006": 170007}, "fidelity: ok " + shaHead + " Resubscribes from the fetched position after a cut. Every missing point is one coordinator settled at #170007."},
		{"settled-dropped", []string{"  " + settledPt + " ", "a new point"}, map[string]int{"170006": 170007}, "fidelity: missing " + shaHead + " plan:811 a new point"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			entries := make([]any, 0, len(c.missing))
			for _, line := range c.missing {
				entries = append(entries, missingOf("plan:811", line))
			}
			directory := fidelityDir(t, map[string]any{"grades": []any{settledGrade}, "settled": c.settled})
			shell, _ := newFidelityFake(t, otherPatch, fidelityReplyOf(map[string]any{"verdict": "missing", "missing": entries}))
			mustFidelity(t, directory, shell)
			equal(t, shell.texts()[0], c.posted)
			if len(c.settled) > 0 {
				holdsAll(t, readText(t, directory, "question-1.md"), "- "+settledPt+" (coordinator #170007)")
			}
		})
	}
}

func TestARewordedPointOnCodeLinesASettledPointCitesIsDroppedUnlessTheChangeSinceTouchedThem(t *testing.T) {
	said := permissions + ":126 newly copies Relay's isPlatformPreviewer into the provider's useState; go/sand/next/replica/follow.go:41 resubscribes from zero"
	cases := []struct {
		name   string
		raised string
		posted string
	}{
		{"same-lines-reworded", permissions + ":123–153 adds copying Relay's isPlatformPreviewer result into provider state through an effect", "fidelity: ok " + shaHead + " Resubscribes from the fetched position after a cut. Every missing point is one coordinator settled at #170007."},
		{"lines-the-change-touched", "go/sand/next/replica/follow.go:41 still resubscribes from zero", "fidelity: missing " + shaHead + " dashboard/AGENTS.md:37 go/sand/next/replica/follow.go:41 still resubscribes from zero"},
		{"another-file", "dashboard/src/iris/lib/IrisShell.tsx:126 copies the flag into state", "fidelity: missing " + shaHead + " dashboard/AGENTS.md:37 dashboard/src/iris/lib/IrisShell.tsx:126 copies the flag into state"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			directory := fidelityDir(t, map[string]any{"grades": []any{gradeOf(170006, FidelityLane, "fidelity: missing "+shaOld+" dashboard/AGENTS.md:42 "+said)}, "settled": map[string]int{"170006": 170007}})
			shell, _ := newFidelityFake(t, otherPatch, fidelityReplyOf(map[string]any{"verdict": "missing", "missing": []any{missingOf("dashboard/AGENTS.md:37", c.raised)}}))
			mustFidelity(t, directory, shell)
			equal(t, shell.texts()[0], c.posted)
		})
	}
}

func TestARewordedPointARootRulingSettledIsDroppedByTheRulingTheReplyNames(t *testing.T) {
	cases := []struct {
		name    string
		settled int
		posted  string
	}{
		{"a-carried-ruling", 265587, "fidelity: ok " + shaHead + " Resubscribes from the fetched position after a cut. Every missing point is one coordinator settled at #265587."},
		{"a-number-the-job-never-carried", 999, "fidelity: missing " + shaHead + " plan:811 the census route refuses an unproven head"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			ruling := map[string]any{"seq": 265587, "text": "2:07 PM #43299: the census route refusing an unproven head is the plan's behavior; settled."}
			entry := map[string]any{"key": "plan:811", "line": "the census route refuses an unproven head", "settled": c.settled}
			directory := fidelityDir(t, map[string]any{"rulings": []any{ruling}})
			shell, _ := newFidelityFake(t, otherPatch, fidelityReplyOf(map[string]any{"verdict": "missing", "missing": []any{entry}}))
			mustFidelity(t, directory, shell)
			equal(t, shell.texts()[0], c.posted)
			holdsAll(t, readText(t, directory, "question-1.md"), "- coordinator #265587: 2:07 PM #43299: the census route refusing an unproven head is the plan's behavior; settled.")
		})
	}
}

func TestAHeadThatMatchesNoHeadEveryLaneGradedOkIsReadByCodex(t *testing.T) {
	oneMissing := gradeOf(170005, "fidelity-desk-4", "fidelity: missing "+shaOld+" rule/949e6be polls the signal")
	cases := []struct {
		name    string
		grades  []any
		patches map[string]string
	}{
		{"another-patch", []any{deskOK}, otherPatch},
		{"one-lane-missing", []any{deskOK, oneMissing}, samePatch},
		{"only-missing", []any{oneMissing}, samePatch},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			directory := fidelityDir(t, map[string]any{"grades": c.grades})
			shell, _ := newFidelityFake(t, c.patches, fidelityReplyOf(nil))
			mustFidelity(t, directory, shell)
			ask := shell.asks()[0]
			sameList(t, ask[:6], []string{"codex-ask", "-m", "sol", "--incident", "--schema", filepath.Join(directory, schemaFile)})
			if !strings.HasPrefix(shell.texts()[0], "fidelity: ok "+shaHead+" Resubscribes") {
				t.Errorf("posted %q", shell.texts()[0])
			}
		})
	}
}

func TestAHeadPastOneEveryLaneGradedOkIsGradedOnItsRangeDiffDeltaAlone(t *testing.T) {
	directory := fidelityDir(t, nil)
	shell, _ := newFidelityFake(t, map[string]string{}, fidelityReplyOf(nil))
	mustFidelity(t, directory, shell)
	holdsAll(t, readText(t, directory, "question-1.md"),
		"An earlier head, "+shaOld+", was reviewed. `git range-diff "+shaOldOwn+"^.."+shaOld+" "+shaOwn+"^.."+shaHead+"`",
		"only the hunks the range-diff shows for its pair ("+shaOwn[:7]+")",
	)
	if !strings.HasPrefix(shell.texts()[0], "fidelity: ok "+shaHead+" Resubscribes") {
		t.Errorf("posted %q", shell.texts()[0])
	}
}

func TestTheGraderReadsAgentsMdRulesAtTheTrunkTipNeverTheHeadsCopy(t *testing.T) {
	directory := fidelityDir(t, map[string]any{"grades": []any{}})
	shell, _ := newFidelityFake(t, samePatch, fidelityReplyOf(nil))
	mustFidelity(t, directory, shell)
	holdsAll(t, readText(t, directory, "question-1.md"), "at the trunk tip "+shaBase+",\nwith `git show "+shaBase+":<path>`", "never the\nhead's copy")
}

func TestTheGradeReadsEveryCommentThePRAuthorPostedAndNoBots(t *testing.T) {
	directory := fidelityDir(t, map[string]any{"grades": []any{}})
	shell, _ := newFidelityFake(t, samePatch, fidelityReplyOf(nil))
	shell.comments = [][]map[string]any{append(slices.Clone(baseComments), map[string]any{"author": lane, "body": "Caller trace for the removed path: every caller reads the batched store."})}
	mustFidelity(t, directory, shell)
	question := readText(t, directory, "question-1.md")
	holdsAll(t, question, "Caller trace for the removed path: every caller reads the batched store.", "The caller trace for GetIrisTeamCreds runs through serve only.")
	holdsNone(t, question, "ci is green")
}

func TestACommentPostedWhileCodexReadTheHeadIsReadOnceMoreBeforeTheGradePosts(t *testing.T) {
	cases := []struct {
		name   string
		body   string
		wanted string
	}{
		{"rulings", "**Rulings and review answers at 7414944**\n\nThe caller trace: every caller of the removed path reads the batched store.", "every caller of the removed path reads the batched store"},
		{"regression-tests", "## Regression tests\n\n- test_resubscribes_after_a_cut failed at 0f1e2d3 on the old tree.", "test_resubscribes_after_a_cut failed at 0f1e2d3"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			directory := fidelityDir(t, map[string]any{"grades": []any{}})
			missing := fidelityReplyOf(map[string]any{"verdict": "missing", "missing": []any{missingOf("plan:116", "no caller trace for the removed path")}})
			shell, _ := newFidelityFake(t, samePatch, missing, fidelityReplyOf(nil))
			shell.comments = [][]map[string]any{baseComments, append(slices.Clone(baseComments), map[string]any{"author": lane, "body": c.body})}
			mustFidelity(t, directory, shell)
			equal(t, len(shell.asks()), 2)
			holdsAll(t, readText(t, directory, "question-3.md"), c.wanted)
			if !strings.HasPrefix(shell.texts()[0], "fidelity: ok "+shaHead+" Resubscribes") {
				t.Errorf("posted %q", shell.texts()[0])
			}
		})
	}
}

func TestACodexOkPostsTheGradeWithItsExemptPhraseThenTouchesAndOldApp(t *testing.T) {
	directory := fidelityDir(t, map[string]any{"grades": []any{}})
	shell, repo := newFidelityFake(t, samePatch, fidelityReplyOf(map[string]any{
		"exempt":  "none",
		"touches": []string{"prompt/1026-105052"},
		"old_app": map[string]string{"grade": "changes", "why": "go/sand/next only"},
	}))
	repo.names = "go/sand/next/replica/follow_test.go\n.buildkite/pipeline.yml\n"
	mustFidelity(t, directory, shell)
	sameList(t, shell.texts(), []string{
		"fidelity: ok " + shaHead + " " + testOnly + ": Resubscribes from the fetched position after a cut.",
		"touches prompt/1026-105052",
		"old-app: CHANGES " + shaHead[:7] + " baseline " + baselineName + "; go/sand/next only",
	})
	question := readText(t, directory, "question-1.md")
	holdsAll(t, question,
		filepath.Join(directory, rubricFile), filepath.Join(directory, "rows.md"), "`git diff "+shaOwn+"^ "+shaHead+"`",
		"contract: builds the resubscribe", "- go/sand/next/replica/follow.go: 41-43",
		"### Rulings\n- ports old-tree 304c1e7ea5e (#41725)", "Body addendum\nThe caller trace",
		"package, /plans/durable.", "`subplans/`",
	)
	holdsNone(t, question, "ci is green")
	holdsAll(t, readText(t, directory, "record.md"), "checked "+strings.Join(fidelityKeys, ",")+" names poll|resubscribe")
	outcome := resultOf(t, directory)
	if !reflect.DeepEqual(outcome, map[string]any{"verdict": "ok", "posted": []any{"#150002", "#150003", "#150004"}}) {
		t.Errorf("outcome = %v", outcome)
	}
	if rubric := readText(t, directory, rubricFile); !strings.Contains(rubric, "How a desk grades") {
		t.Errorf("rubric written to the job holds %q", rubric[:min(len(rubric), 80)])
	}
}

func TestAMissingVerdictPostsOneOwnerGroundedLineNamingEveryBrokenRow(t *testing.T) {
	directory := fidelityDir(t, map[string]any{"grades": []any{}, "baseline": ""})
	shell, _ := newFidelityFake(t, samePatch, fidelityReplyOf(map[string]any{"verdict": "missing", "missing": []any{
		missingOf("rule/949e6be", "follow.go:41 polls the signal, 'not polled'"),
		missingOf("plan:212", "plan names no retry counter"),
		missingOf("plan:subplans/signal-service.md:45", "signal.rs lives in sandsql-server, not its own server"),
	}}))
	mustFidelity(t, directory, shell)
	sameList(t, shell.texts(), []string{
		"fidelity: missing " + shaHead + " rule/949e6be,plan:212,plan:subplans/signal-service.md:45 follow.go:41 polls the signal, 'not polled'; plan names no retry counter; signal.rs lives in sandsql-server, not its own server",
		"old-app: unchanged " + shaHead[:7] + " baseline the old app as last released; go/sand/next only",
	})
}

func TestAHeadThatBreaksOneOfHisAnswersGradesMissingNamingTheRulingItDepartsFrom(t *testing.T) {
	directory := fidelityDir(t, map[string]any{"grades": []any{}, "baseline": ""})
	shell, _ := newFidelityFake(t, samePatch, fidelityReplyOf(map[string]any{
		"verdict": "missing",
		"checked": append(slices.Clone(fidelityKeys), "answer/9d440bb"),
		"missing": []any{missingOf("answer/9d440bb", "convert.go opens an old chat that fails to convert with no Fork")},
	}))
	shell.rows = strings.Replace(shell.rows, "slice-hash:", "### answer/9d440bb | answer | 2026-11-03\nAsked: Old-format chats in the new build: convert how?\n> Fall back to read only with fork.\n\nslice-hash:", 1)
	mustFidelity(t, directory, shell)
	sameList(t, shell.texts(), []string{
		"fidelity: missing " + shaHead + " answer/9d440bb departs from ruling 9d440bb: convert.go opens an old chat that fails to convert with no Fork",
		"old-app: unchanged " + shaHead[:7] + " baseline the old app as last released; go/sand/next only",
	})
}

func TestAMissingPointAnOwnerPickAnswersLeavesTheLineWhichNamesThePick(t *testing.T) {
	directory := fidelityDir(t, map[string]any{"grades": []any{}, "baseline": "", "picks": []any{ownerPick("rule/abc", "rule/949e6be")}})
	shell, _ := newFidelityFake(t, samePatch, fidelityReplyOf(map[string]any{"verdict": "missing", "missing": []any{
		missingOf("rule/949e6be", "follow.go:41 polls the signal, 'not polled'"),
		missingOf("plan:212", "plan names no retry counter"),
	}}))
	mustFidelity(t, directory, shell)
	line := shell.texts()[0]
	holdsAll(t, line, "fidelity: missing "+shaHead+" plan:212 plan names no retry counter", `settled by owner picks: rule/abc "the owner's words"`)
	holdsNone(t, line, "follow.go:41")
}

func TestAHeadWhoseEveryMissingPointAnOwnerPickAnswersGradesOk(t *testing.T) {
	directory := fidelityDir(t, map[string]any{"grades": []any{}, "baseline": "", "picks": []any{ownerPick("rule/abc", "polls the signal")}})
	shell, _ := newFidelityFake(t, samePatch, fidelityReplyOf(map[string]any{"verdict": "missing", "missing": []any{
		missingOf("rule/949e6be", "follow.go:41 polls the signal, 'not polled'"),
	}}))
	mustFidelity(t, directory, shell)
	holdsAll(t, shell.texts()[0], "fidelity: ok "+shaHead, "Every missing point is settled by owner picks: rule/abc")
}

func TestAHeadCodexCannotSettleGoesToTheHumanFidelityDesks(t *testing.T) {
	directory := fidelityDir(t, map[string]any{"grades": []any{}, "baseline": ""})
	shell, _ := newFidelityFake(t, samePatch, fidelityReplyOf(map[string]any{"verdict": "reader", "reader": "deletes the eviction path, a reserved row no answer authorizes", "touches": []string{"rule/949e6be"}}))
	mustFidelity(t, directory, shell)
	reader, touches := shell.posted[0], shell.posted[1]
	equal(t, reader["--text"], fidelityReader+" "+shaHead+" deletes the eviction path, a reserved row no answer authorizes")
	equal(t, reader["--to"], "p8a-step9,desk-runner,board-refresher-2,fidelity-desk-4,fidelity-desk-5")
	equal(t, touches["--to"], "p8a-step9,desk-runner,board-refresher-2")
}

func TestAReplyThatNamesMissingRowsUnderAnotherVerdictGradesMissingAndStillReachesTheReaders(t *testing.T) {
	directory := fidelityDir(t, map[string]any{"grades": []any{}, "baseline": ""})
	shell, _ := newFidelityFake(t, samePatch, fidelityReplyOf(map[string]any{"verdict": "reader", "reader": "the eviction path needs a human read", "missing": []any{
		missingOf("rule/949e6be", "follow.go:41 polls the signal"),
		missingOf("plan:212", "plan names no retry counter"),
	}}))
	mustFidelity(t, directory, shell)
	equal(t, len(shell.asks()), 1)
	equal(t, shell.posted[0]["--text"], "fidelity: missing "+shaHead+" rule/949e6be,plan:212 follow.go:41 polls the signal; plan names no retry counter")
	equal(t, shell.posted[1]["--text"], fidelityReader+" "+shaHead+" the eviction path needs a human read")
	equal(t, shell.posted[1]["--to"], "p8a-step9,desk-runner,board-refresher-2,fidelity-desk-4,fidelity-desk-5")
}

func TestAReplyThatBreaksTheGradeContractIsAskedOnceMoreAndASecondFailsTheJob(t *testing.T) {
	cases := []struct {
		name string
		bad  map[string]any
	}{
		{"unchecked-row", fidelityReplyOf(map[string]any{"checked": fidelityKeys[:1]})},
		{"root-record-key", fidelityReplyOf(map[string]any{"verdict": "missing", "missing": []any{missingOf("cci#172451", "root said so")}})},
		{"row-outside-the-slice", fidelityReplyOf(map[string]any{"verdict": "missing", "missing": []any{missingOf("rule/1234567", "a row the rows file does not carry")}})},
		{"missing-without-entries", fidelityReplyOf(map[string]any{"verdict": "missing"})},
		{"reader-without-reason", fidelityReplyOf(map[string]any{"verdict": "reader"})},
		{"no-reasoning", fidelityReplyOf(map[string]any{"swept": "looks fine"})},
		{"no-such-class", fidelityReplyOf(map[string]any{"exempt": "hotfix"})},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			directory := fidelityDir(t, map[string]any{"grades": []any{}})
			shell, _ := newFidelityFake(t, samePatch, c.bad, fidelityReplyOf(nil))
			mustFidelity(t, directory, shell)
			equal(t, len(shell.asks()), 2)
			holdsAll(t, readText(t, directory, "question-2.md"), "Your last reply gave")
			if !strings.HasPrefix(shell.texts()[0], "fidelity: ok "+shaHead) {
				t.Errorf("posted %q", shell.texts()[0])
			}

			failing := fidelityDir(t, map[string]any{"grades": []any{}})
			shell, _ = newFidelityFake(t, samePatch, c.bad, c.bad)
			var failed *JobFailed
			if err := Fidelity(context.Background(), failing, shell.deps()); !errors.As(err, &failed) {
				t.Fatalf("err = %v, want JobFailed", err)
			}
			if said := resultOf(t, failing)["error"].(string); !strings.HasPrefix(said, "JobFailed: two replies gave") {
				t.Errorf("error %q", said)
			}
		})
	}
}

func TestAHeadPastHisTapIsReadByCodexEvenWhenPatchIdenticalAndPostsWhetherItMeansWhatTheTappedHeadMeant(t *testing.T) {
	tapped := shaOld[:12]
	for name, c := range map[string]struct {
		reply map[string]any
		line  string
	}{
		"same":    {map[string]any{"behavior": "same"}, "behavior: same " + shaHead + " since " + tapped},
		"changed": {map[string]any{"behavior": "changed", "what_changed": "it now resubscribes from the start"}, "behavior: changed " + shaHead + " since " + tapped + ": it now resubscribes from the start"},
	} {
		t.Run(name, func(t *testing.T) {
			directory := fidelityDir(t, map[string]any{"since": tapped})
			shell, _ := newFidelityFake(t, samePatch, fidelityReplyOf(c.reply))
			mustFidelity(t, directory, shell)
			equal(t, len(shell.asks()), 1)
			if !slices.Contains(shell.texts(), c.line) {
				t.Errorf("posted %q, want %q among them", shell.texts(), c.line)
			}
			holdsAll(t, readText(t, directory, "question-1.md"), "the owner tapped this PR at "+tapped, "Judge\n  against "+tapped+" itself")
		})
	}
}

func TestAHeadWithNoTapBeforeItPostsNoBehaviorLine(t *testing.T) {
	directory := fidelityDir(t, map[string]any{"grades": []any{}})
	shell, _ := newFidelityFake(t, samePatch, fidelityReplyOf(map[string]any{"behavior": "none"}))
	mustFidelity(t, directory, shell)
	for _, text := range shell.texts() {
		if strings.HasPrefix(text, "behavior:") {
			t.Errorf("posted %q", text)
		}
	}
	holdsAll(t, readText(t, directory, "question-1.md"), "the owner tapped this PR at none")
}

func TestAReplyThatJudgesNoBehaviorAgainstHisTapOrNamesNoChangeIsAskedOnceMore(t *testing.T) {
	for name, bad := range map[string]map[string]any{
		"no behavior":     fidelityReplyOf(map[string]any{"behavior": "none"}),
		"no what_changed": fidelityReplyOf(map[string]any{"behavior": "changed"}),
	} {
		t.Run(name, func(t *testing.T) {
			directory := fidelityDir(t, map[string]any{"since": shaOld[:12]})
			shell, _ := newFidelityFake(t, samePatch, bad, fidelityReplyOf(map[string]any{"behavior": "same"}))
			mustFidelity(t, directory, shell)
			equal(t, len(shell.asks()), 2)
			holdsAll(t, readText(t, directory, "question-2.md"), "Your last reply gave")
		})
	}
}

const regressionPhrase = "class regression, fixes shipped production code"

var classes = map[string]string{"port": portPhrase, "test-only": testOnly, "regression": regressionPhrase}

func TestAnOkNamesTheClassItsChangedPathsDecideWhateverTheModelSaid(t *testing.T) {
	const tooling = "class ci-tooling"
	pathClasses := map[string]string{"port": portPhrase, "test-only": testOnly, "regression": regressionPhrase, "ci-tooling": tooling}
	grades := []string{portPhrase, testOnly, regressionPhrase, tooling}
	for name, tc := range map[string]struct {
		names  string
		exempt string
		said   string
	}{
		"#49240's tooling pin with no class from the model": {"mise.toml\ntools/cc-remote/config.yaml\ntools/versions.toml\n", "none", tooling + ": "},
		"tooling over a class the model judged":             {"go/remote-builder/cli/rb/exec.go\ntools/remote-builder/README.md\n", "regression", tooling + ": "},
		"a product path keeps the model's judgment":         {"go/sand/next/replica/follow.go\n", "regression", regressionPhrase + ": "},
		"a product path names no path class the model gave": {"go/sand/next/replica/follow.go\ngo/sand/next/replica/follow_test.go\n", "test-only", ""},
	} {
		t.Run(name, func(t *testing.T) {
			directory := fidelityDir(t, map[string]any{"grades": []any{}, "exempt": pathClasses, "exempt_grades": grades})
			shell, repo := newFidelityFake(t, samePatch, fidelityReplyOf(map[string]any{"exempt": tc.exempt}))
			repo.names = tc.names
			mustFidelity(t, directory, shell)
			equal(t, shell.texts()[0], "fidelity: ok "+shaHead+" "+tc.said+"Resubscribes from the fetched position after a cut.")
			question := readText(t, directory, "question-1.md")
			holdsAll(t, question, "`regression` (\""+regressionPhrase+"\")")
			holdsNone(t, question, "`ci-tooling` (", "`test-only` (")
		})
	}
}

func TestARestackCarriedOntoATestOnlyDiffNamesItsPathClassThoughTheGradeItCarriesNamedNone(t *testing.T) {
	plain := gradeOf(170001, "fidelity-desk-5", "fidelity: ok "+shaOld+" resubscribes after a cut.")
	directory := fidelityDir(t, map[string]any{"grades": []any{plain}})
	shell, repo := newFidelityFake(t, samePatch)
	repo.names = "go/sand/next/replica/follow_test.go\n"
	mustFidelity(t, directory, shell)
	equal(t, len(shell.asks()), 0)
	equal(t, shell.texts()[0], "fidelity: ok "+shaHead+" "+testOnly+": carried from "+shaOld[:7]+" (cci #170001), patch-identical by git patch-id over its own commits.")
}

func TestAnOkNamesAnyClassTheGateAcceptsAndTheBriefListsThemAll(t *testing.T) {
	directory := fidelityDir(t, map[string]any{"grades": []any{}, "exempt": classes})
	shell, _ := newFidelityFake(t, samePatch, fidelityReplyOf(map[string]any{"exempt": "regression"}))
	mustFidelity(t, directory, shell)
	equal(t, shell.texts()[0], "fidelity: ok "+shaHead+" "+regressionPhrase+": Resubscribes from the fetched position after a cut.")
	holdsAll(t, readText(t, directory, "question-1.md"), "`regression` (\""+regressionPhrase+"\")", "`port` (\""+portPhrase+"\")")
}

func TestAnOkNamingNoClassALaneClaimedIsAskedOnceMoreListingTheClassesAndItsSecondAnswerStands(t *testing.T) {
	for name, second := range map[string]struct {
		exempt string
		posted string
	}{
		"it names the class":  {"regression", "fidelity: ok " + shaHead + " " + regressionPhrase + ": Resubscribes"},
		"it still names none": {"none", "fidelity: ok " + shaHead + " Resubscribes"},
	} {
		t.Run(name, func(t *testing.T) {
			directory := fidelityDir(t, map[string]any{"grades": []any{}, "exempt": classes, "claims": []string{"regression"}})
			shell, _ := newFidelityFake(t, samePatch, fidelityReplyOf(nil), fidelityReplyOf(map[string]any{"exempt": second.exempt}))
			mustFidelity(t, directory, shell)
			equal(t, len(shell.asks()), 2)
			holdsAll(t, readText(t, directory, "question-2.md"), "though a lane claims regression for this head", "`regression` (\""+regressionPhrase+"\")")
			if !strings.HasPrefix(shell.texts()[0], second.posted) {
				t.Errorf("posted %q, want it to start %q", shell.texts()[0], second.posted)
			}
		})
	}
}

func TestAnOkNamingNoClassWithNoClaimIsAPlainOk(t *testing.T) {
	directory := fidelityDir(t, map[string]any{"grades": []any{}, "exempt": classes})
	shell, _ := newFidelityFake(t, samePatch, fidelityReplyOf(nil))
	mustFidelity(t, directory, shell)
	equal(t, len(shell.asks()), 1)
	equal(t, shell.texts()[0], "fidelity: ok "+shaHead+" Resubscribes from the fetched position after a cut.")
}

func TestAClosedPROrAHeadThatMovedPostsNothing(t *testing.T) {
	cases := []struct {
		name  string
		state string
		heads []string
	}{
		{"closed", "CLOSED", []string{shaHead}},
		{"moved", "OPEN", []string{shaMoved}},
		{"moved-while-read", "OPEN", []string{shaHead, shaMoved}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			directory := fidelityDir(t, map[string]any{"grades": []any{}})
			shell, _ := newFidelityFake(t, samePatch, fidelityReplyOf(nil))
			shell.state, shell.heads = c.state, c.heads
			mustFidelity(t, directory, shell)
			equal(t, len(shell.posted), 0)
			if _, skipped := resultOf(t, directory)["skipped"]; !skipped {
				t.Errorf("result = %v", resultOf(t, directory))
			}
		})
	}
}

func TestAHeadWhosePRHasAnOpenSweepFindingGradesMissingNamingItWithNoModelCall(t *testing.T) {
	directory := fidelityDir(t, map[string]any{"grades": []any{}})
	shell, _ := newFidelityFake(t, samePatch, fidelityReplyOf(nil))
	state := t.TempDir()
	if err := os.WriteFile(filepath.Join(state, findingsState), []byte("{}"), 0o600); err != nil {
		t.Fatal(err)
	}
	entry := filepath.Join(shell.config, "long-running", "drives", "d1.json")
	if err := os.MkdirAll(filepath.Dir(entry), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(entry, fmt.Appendf(nil, `{"drive":"d1","cci_drive":"d1","state_dir":%q}`, state), 0o600); err != nil {
		t.Fatal(err)
	}
	shell.findings = `{"seq": "205428", "sweep": "local-state-sweep-1", "owner": "naming-diet-2", "text": "dev host /_ssql route\nlives in a JSON file under ~/.iris", "pr": "43299", "state": "open"}` + "\n"
	mustFidelity(t, directory, shell)
	sameList(t, shell.texts(), []string{"fidelity: missing " + shaHead + " finding/205428 open finding 205428 from local-state-sweep-1, owner naming-diet-2: dev host /_ssql route lives in a JSON file under ~/.iris"})
	equal(t, len(shell.asks()), 0)
	equal(t, resultOf(t, directory)["verdict"].(string), "missing")
}

func TestADeletionOnOwnerWordsThatNeverNameTheDeletedThingGradesMissing(t *testing.T) {
	cases := []struct {
		name    string
		title   string
		body    string
		missing bool
	}{
		{"unnamed", graphiqlTitle, graphiqlBody, true},
		{"named", graphiqlTitle, strings.ReplaceAll(graphiqlBody, "installs litestream at runtime", "serves the graphiql page"), false},
		{"the-words-name-it", "infra: 🔥 remove the litestream installer that ran at runtime", graphiqlBody, false},
		{"no-owner-words", graphiqlTitle, "## Motivation\n\nPlan step 4 deletes every page that loads a third-party CDN at run time.\n", false},
		{"no-deletion", "api: ✨ serve the schema at /graphql/schema", graphiqlBody, false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			said := unnamedDeletion(c.title, c.body)
			equal(t, said != "", c.missing)
			if c.missing && !strings.HasPrefix(said, "deletes /graphiql page, and the owner words it cites never name graphiql: “remove entirely the code that installs litestream at runtime") {
				t.Errorf("said %q", said)
			}
		})
	}
}

func TestAnUnnamedDeletionPostsMissingWithNoModelCall(t *testing.T) {
	directory := fidelityDir(t, map[string]any{"grades": []any{}})
	shell, repo := newFidelityFake(t, samePatch)
	repo.stat = "0\t212\tapi/src/graphiql.ts\n delete mode 100644 api/src/graphiql.ts\n"
	shell.title, shell.body = graphiqlTitle, graphiqlBody
	mustFidelity(t, directory, shell)
	equal(t, len(shell.asks()), 0)
	if !strings.HasPrefix(shell.texts()[0], "fidelity: missing "+shaHead+" "+deletionKey+" deletes /graphiql page") {
		t.Errorf("posted %q", shell.texts()[0])
	}
}

func TestADeletionTitleOverADiffThatRemovesNoCodeDrawsNoFinding(t *testing.T) {
	for name, stat := range map[string]string{
		"no removals": "40\t0\tgo/memory/delete_asked.go\n",
		"a rewrite":   "231\t8\tgo/memory/delete_asked.go\n",
		"a rename":    "0\t0\tgo/{old.go => new.go}\n rename go/{old.go => new.go} (100%)\n",
	} {
		t.Run(name, func(t *testing.T) {
			directory := fidelityDir(t, map[string]any{"grades": []any{}})
			shell, repo := newFidelityFake(t, otherPatch, fidelityReplyOf(nil))
			repo.stat = stat
			shell.title, shell.body = graphiqlTitle, graphiqlBody
			mustFidelity(t, directory, shell)
			equal(t, len(shell.asks()), 1)
			for _, text := range shell.texts() {
				if strings.Contains(text, deletionKey) {
					t.Errorf("posted %q", text)
				}
			}
		})
	}
}

func TestRemovesCode(t *testing.T) {
	for name, tc := range map[string]struct {
		stat string
		want bool
	}{
		"nothing removed":                {"12\t0\ta.go\n", false},
		"more added than removed":        {"55\t5\ta.go\n", false},
		"as much added as removed":       {"5\t5\ta.go\n", false},
		"more removed than added":        {"3\t90\ta.go\n", true},
		"removed across files":           {"10\t0\ta.go\n0\t11\tb.go\n", true},
		"a deleted file among additions": {"900\t0\ta.go\n0\t12\tb.go\n delete mode 100644 b.go\n", true},
		"a deleted binary":               {"-\t-\tlogo.png\n delete mode 100644 logo.png\n", true},
		"a binary changed":               {"-\t-\tlogo.png\n", false},
	} {
		t.Run(name, func(t *testing.T) {
			equal(t, removesCode(tc.stat), tc.want)
		})
	}
}

func TestTheGraderReadsThePlanGroundsRootAcceptedOutsideThePackageFromTheJobsSourcesFile(t *testing.T) {
	sources := filepath.Join(t.TempDir(), "sources.md")
	directory := fidelityDir(t, map[string]any{"sources": sources})
	shell, _ := newFidelityFake(t, otherPatch, fidelityReplyOf(nil))
	mustFidelity(t, directory, shell)
	holdsAll(t, readText(t, directory, "question-1.md"), "Plan grounds the coordinator accepted outside the package, such as a class-fix inventory or a lane's\naccepted design contract: "+sources+".")
}

const (
	signalAdded = `\b(?:Iris)?SignalGrant\b|\bSignalStream\b|\b(?:Lane|Team)Signals?\b|/v1/signal/`
	pr44080Test = "diff --git a/dashboard/src/iris/durable/replica.test.ts b/dashboard/src/iris/durable/replica.test.ts\n--- a/dashboard/src/iris/durable/replica.test.ts\n+++ b/dashboard/src/iris/durable/replica.test.ts\n@@ -1182,0 +1218,1 @@\n+async function signalledLane(signal: LaneSignal) {\n"
	pr44080Diff = pr44080Test +
		"diff --git a/dashboard/src/iris/durable/replica.ts b/dashboard/src/iris/durable/replica.ts\n--- a/dashboard/src/iris/durable/replica.ts\n+++ b/dashboard/src/iris/durable/replica.ts\n@@ -1094 +1172,2 @@\n-  async open(\n+  async mount(\n+    signalled: Signalled,\n@@ -1110,0 +1198,2 @@\n+    const signal = new LaneSignal(chat.db, prepared.bucket, prepared.store, signalled)\n+    const lane = await signal.greet(async (marker) => {\n" +
		"diff --git a/dashboard/src/iris/journal/grant.ts b/dashboard/src/iris/journal/grant.ts\n--- a/dashboard/src/iris/journal/grant.ts\n+++ b/dashboard/src/iris/journal/grant.ts\n@@ -15 +15,3 @@\n-export type SessionGrant = AgentContract.IrisSessionStorageCreds\n+export type SessionGrant = AgentContract.IrisSessionStorageCreds & {\n+  signal?: AgentContract.IrisSignalGrant\n+}\n"
	nextSignalDiff = "diff --git a/go/sand/next/cmd/iris_serve_user_creds.go b/go/sand/next/cmd/iris_serve_user_creds.go\n--- a/go/sand/next/cmd/iris_serve_user_creds.go\n+++ b/go/sand/next/cmd/iris_serve_user_creds.go\n@@ -156,0 +157,1 @@\n+func (u *userCreds) signalGrant(ctx context.Context, team string) (platformapi.IrisSignalGrant, error) {\n"
)

var signalTouches = []map[string]string{
	{"service": "signal", "path": "go/sand/internal", "added": signalAdded},
	{"service": "signal", "path": "dashboard/src", "added": signalAdded},
}

func TestAHeadThatAddsANamedServiceTouchOnAnOldTreePathGradesOldAppChangesWhateverTheReadSaid(t *testing.T) {
	directory := fidelityDir(t, map[string]any{"grades": []any{}, "service_touches": signalTouches})
	shell, repo := newFidelityFake(t, samePatch, fidelityReplyOf(map[string]any{
		"old_app": map[string]string{"grade": "unchanged", "why": "the open reads the same rows"},
	}))
	repo.diff = pr44080Diff
	mustFidelity(t, directory, shell)
	texts := shell.texts()
	equal(t, texts[len(texts)-1], "old-app: CHANGES "+shaHead[:7]+" baseline "+baselineName+"; dashboard/src/iris/durable/replica.ts:1198 touches signal; dashboard/src/iris/journal/grant.ts:16 touches signal")
}

func TestARestackThatCarriesAnUnchangedOldAppGradesChangesWhenItAddsANamedServiceTouch(t *testing.T) {
	directory := fidelityDir(t, map[string]any{"service_touches": signalTouches})
	shell, repo := newFidelityFake(t, samePatch)
	repo.diff = pr44080Diff
	mustFidelity(t, directory, shell)
	equal(t, len(shell.asks()), 0)
	equal(t, shell.texts()[1], "old-app: CHANGES "+shaHead[:7]+" baseline "+baselineName+"; dashboard/src/iris/durable/replica.ts:1198 touches signal; dashboard/src/iris/journal/grant.ts:16 touches signal")
}

func TestAServiceTouchOnlyOnTheNewTreeOrInATestLeavesTheReadsOldAppGrade(t *testing.T) {
	directory := fidelityDir(t, map[string]any{"grades": []any{}, "service_touches": signalTouches})
	shell, repo := newFidelityFake(t, samePatch, fidelityReplyOf(nil))
	repo.diff = nextSignalDiff + pr44080Test
	mustFidelity(t, directory, shell)
	texts := shell.texts()
	equal(t, texts[len(texts)-1], "old-app: unchanged "+shaHead[:7]+" baseline "+baselineName+"; go/sand/next only")
}

func TestAServiceTouchEntryThatDoesNotCompileGradesAsBefore(t *testing.T) {
	directory := fidelityDir(t, map[string]any{"grades": []any{}, "service_touches": []map[string]string{
		{"service": "signal", "path": "dashboard/src", "added": "LaneSignal("},
		{"service": "signal", "path": "dashboard/[src", "added": signalAdded},
	}})
	shell, repo := newFidelityFake(t, samePatch, fidelityReplyOf(nil))
	repo.diff = pr44080Diff
	mustFidelity(t, directory, shell)
	texts := shell.texts()
	equal(t, texts[len(texts)-1], "old-app: unchanged "+shaHead[:7]+" baseline "+baselineName+"; go/sand/next only")
}

func TestAReaderHeadWhoseOldAppLineNamesWorkloadsHoldsAtOldAppPendingWithoutReachingEveryReader(t *testing.T) {
	directory := fidelityDir(t, map[string]any{"grades": []any{}, "service_touches": signalTouches})
	shell, repo := newFidelityFake(t, samePatch, fidelityReplyOf(map[string]any{"verdict": "reader", "reader": "the whole diff is too large for one pass"}))
	repo.diff = pr44080Diff
	mustFidelity(t, directory, shell)
	reader := shell.posted[0]
	equal(t, reader["--text"], fidelityReader+" "+shaHead+" OLD-APP-PENDING: the whole diff is too large for one pass")
	equal(t, reader["--to"], "p8a-step9,desk-runner,board-refresher-2")
}

func TestAServiceTouchOnTheDurableReaderPathMarksAnyAddedLineThereAsAnOldAppChange(t *testing.T) {
	directory := fidelityDir(t, map[string]any{"grades": []any{}, "service_touches": []map[string]string{
		{"service": "old-app pages", "path": "dashboard/src/iris/durable", "added": `\S`},
	}})
	shell, repo := newFidelityFake(t, samePatch, fidelityReplyOf(nil))
	repo.diff = pr44080Diff
	mustFidelity(t, directory, shell)
	texts := shell.texts()
	holdsAll(t, texts[len(texts)-1], "old-app: CHANGES "+shaHead[:7], "dashboard/src/iris/durable/replica.ts:1172 touches old-app pages")
	holdsNone(t, texts[len(texts)-1], "journal/grant.ts", "replica.test.ts")
}
