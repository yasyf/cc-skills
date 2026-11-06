package bk

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/url"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/askdesk"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	drive     = "brook"
	lane      = "chat-list-1"
	checkout  = "/drive/checkout"
	readToken = "bk-token-never-in-a-record"
	failedJob = "01a1acec-8921-462e-be74-64ca11399168"
)

var started = time.Date(2026, 11, 3, 12, 0, 0, 0, time.UTC)

type fakeRunner struct {
	calls   []shell.Command
	reply   shell.Result
	fail    error
	replies map[string]string
}

func (f *fakeRunner) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	if c.Argv[0] == "print-token" {
		return shell.Result{Stdout: readToken + "\n"}, nil
	}
	f.calls = append(f.calls, c)
	if said, found := f.replies[c.Argv[len(c.Argv)-1]]; found {
		return shell.Result{Stdout: said}, nil
	}
	return f.reply, f.fail
}

func newDesk(t *testing.T, runner *fakeRunner) (*Desk, *time.Time) {
	t.Helper()
	t.Setenv("CCI_HOME", t.TempDir())
	now := started
	desk, err := NewDesk(context.Background(), runner, Settings{CLI: "tools/ci bk", Checkout: checkout, TokenCommand: []string{"print-token"}}, drive, t.TempDir(), func() time.Time { return now }, io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	return desk, &now
}

func ask(t *testing.T, text string) client.Record {
	t.Helper()
	return askAs(t, lane, text)
}

func askAs(t *testing.T, from, text string) client.Record {
	t.Helper()
	posted, err := client.Post(context.Background(), client.Draft{Drive: drive, Lane: from, Kind: askdesk.AskKind, Text: text, To: []string{Lane}, Force: true})
	if err != nil {
		t.Fatal(err)
	}
	return posted
}

func firstPass(t *testing.T, desk *Desk) client.Query {
	t.Helper()
	next, err := desk.loop().Pass(context.Background(), client.Query{Drive: drive, Since: time.Now().Add(-askdesk.CatchUp), Kinds: []string{askdesk.AskKind}})
	if err != nil {
		t.Fatal(err)
	}
	return next
}

func answers(t *testing.T) []client.Record {
	t.Helper()
	found, err := client.Tail(context.Background(), client.Query{Drive: drive, Kinds: []string{askdesk.AnswerKind}, Lanes: []string{Lane}})
	if err != nil {
		t.Fatal(err)
	}
	return found
}

func bodyOf(t *testing.T, answer client.Record) string {
	t.Helper()
	body, err := os.ReadFile(answer.Refs.Path)
	if err != nil {
		t.Fatal(err)
	}
	return string(body)
}

func TestAnAskIsAnsweredToItsLaneAndClosed(t *testing.T) {
	runner := &fakeRunner{reply: shell.Result{Stdout: `{"state":"failed"}`}}
	desk, _ := newDesk(t, runner)
	opened := ask(t, "bk api /pipelines/test/builds/1")

	firstPass(t, desk)

	found := answers(t)
	if len(found) != 1 {
		t.Fatalf("answers = %d, want 1", len(found))
	}
	if !slices.Equal(found[0].To, []string{client.Stem(lane)}) || found[0].Resolves != opened.Seq {
		t.Fatalf("to %v resolves %d, want %s, the stem the asking lane holds, and %d", found[0].To, found[0].Resolves, client.Stem(lane), opened.Seq)
	}
	if body := bodyOf(t, found[0]); body != `{"state":"failed"}` {
		t.Fatalf("body = %q", body)
	}
	still, err := client.Open(context.Background(), drive, askdesk.AskKind)
	if err != nil || len(still) != 0 {
		t.Fatalf("open asks = %v, %v", still, err)
	}
	if call := runner.calls[0]; call.Dir != checkout || !slices.Equal(call.Argv, []string{"tools/ci", "bk", "api", "-no-wait", "/pipelines/test/builds/1"}) {
		t.Fatalf("ran %v in %s", call.Argv, call.Dir)
	}
}

func TestARestartAnswersOnlyTheAskNothingClosed(t *testing.T) {
	runner := &fakeRunner{reply: shell.Result{Stdout: "{}"}}
	desk, _ := newDesk(t, runner)
	ask(t, "bk api /one")
	firstPass(t, desk)
	ask(t, "bk api /two")

	firstPass(t, desk)

	if len(runner.calls) != 2 || !strings.HasSuffix(strings.Join(runner.calls[1].Argv, " "), "/two") {
		t.Fatalf("reads = %v, want /one then /two", runner.calls)
	}
	if found := answers(t); len(found) != 2 {
		t.Fatalf("answers = %d, want 2", len(found))
	}
}

func TestALaterPassReadsOnlyNewAsks(t *testing.T) {
	runner := &fakeRunner{reply: shell.Result{Stdout: "{}"}}
	desk, _ := newDesk(t, runner)
	ask(t, "bk api /one")
	next := firstPass(t, desk)
	ask(t, "bk api /two")

	if _, err := desk.loop().Pass(context.Background(), next); err != nil {
		t.Fatal(err)
	}
	if _, err := desk.loop().Pass(context.Background(), next); err != nil {
		t.Fatal(err)
	}

	if len(runner.calls) != 2 {
		t.Fatalf("reads = %d, want 2", len(runner.calls))
	}
}

func TestAnAskWhoseAnswerFailsStaysOpenAndTheNextIsStillAnswered(t *testing.T) {
	runner := &fakeRunner{replies: map[string]string{"/stuck": "stuck body", "/next": "next body"}}
	desk, _ := newDesk(t, runner)
	sum := sha256.Sum256([]byte("stuck body"))
	if err := os.MkdirAll(filepath.Join(desk.Bodies, hex.EncodeToString(sum[:])[:16]+".txt"), 0o750); err != nil {
		t.Fatal(err)
	}
	stuck := ask(t, "bk api /stuck")
	ask(t, "bk api /next")

	firstPass(t, desk)

	found := answers(t)
	if len(found) != 1 || bodyOf(t, found[0]) != "next body" {
		t.Fatalf("answers = %+v, want only the next ask's", found)
	}
	still, err := client.Open(context.Background(), drive, askdesk.AskKind)
	if err != nil || len(still) != 1 || still[0].Seq != stuck.Seq {
		t.Fatalf("open asks = %+v, %v, want only #%d", still, err, stuck.Seq)
	}
}

func TestAnAskToAnotherLaneIsLeftAlone(t *testing.T) {
	runner := &fakeRunner{}
	desk, _ := newDesk(t, runner)
	if _, err := client.Post(context.Background(), client.Draft{Drive: drive, Lane: lane, Kind: askdesk.AskKind, Text: "bk api /x", To: []string{"root"}, Force: true}); err != nil {
		t.Fatal(err)
	}

	firstPass(t, desk)

	if len(runner.calls) != 0 || len(answers(t)) != 0 {
		t.Fatalf("reads = %v, answers = %v", runner.calls, answers(t))
	}
}

func TestManyLanesReadingOneBuildCostOneBuildkiteCall(t *testing.T) {
	for _, asks := range []int{1, 100} {
		t.Run(fmt.Sprint(asks), func(t *testing.T) {
			runner := &fakeRunner{reply: shell.Result{Stdout: "{}"}}
			desk, _ := newDesk(t, runner)
			for i := range asks {
				askAs(t, fmt.Sprintf("lane-%d", i), "bk api /pipelines/test/builds/1")
			}

			firstPass(t, desk)

			if len(runner.calls) != 1 {
				t.Fatalf("Buildkite calls = %d for %d asks, want 1", len(runner.calls), asks)
			}
			if found := answers(t); len(found) != asks {
				t.Fatalf("answers = %d, want %d", len(found), asks)
			}
		})
	}
}

func TestARepeatedReadReachesBuildkiteAgainOnceItsWindowPasses(t *testing.T) {
	runner := &fakeRunner{reply: shell.Result{Stdout: "{}"}}
	desk, now := newDesk(t, runner)

	desk.read(context.Background(), "bk api /x")
	*now = started.Add(fresh - time.Second)
	desk.read(context.Background(), "bk api /x")
	*now = started.Add(fresh)
	desk.read(context.Background(), "bk api /x")

	if len(runner.calls) != 2 {
		t.Fatalf("Buildkite calls = %d, want 2", len(runner.calls))
	}
	if len(desk.cache) != 1 {
		t.Fatalf("cache kept %d entries, want 1", len(desk.cache))
	}
}

func TestEveryWriteAndUnknownVerbIsRefusedWithoutRunningTheClient(t *testing.T) {
	for _, text := range []string{
		"please read build 12",
		"bk cancel test 12",
		"bk rerun test --unit x",
		"bk graphql mutation { buildCancel { id } }",
		"bk api -X POST /pipelines/test/builds/12/cancel",
		"bk api -d {} /pipelines/test/builds",
		"bk api /pipelines/test/builds/12 extra",
		"bk api /organizations/other/pipelines",
		"bk api organizations/other/pipelines",
		"bk api /pipelines/test/builds/12/annotations --jq .[].context",
		"bk api /pipelines/test/builds --fields",
		"bk api /pipelines/test/builds --fields number extra",
		"bk annotations test",
		"bk annotations test twelve",
		"bk annotations test 12 one two",
		"bk log test 12",
		"bk log test 12 job extra",
		"bk log test 12 job --context 51",
		"bk log test 12 job --context -1",
		"bk log test 12 job --grep (unclosed",
		"bk log test 12 job --grep 'panic|fatal error",
		"bk log test 12 --grep panic",
		"bk log test 12 " + failedJob + " 22/36",
		"bk log test 12 job 0/36",
		"bk log test 12 job 22/36 extra",
		"bk build test",
		"bk build test twelve",
		"bk jobs test twelve",
		"bk builds",
		"bk builds --branch dev",
		"bk builds test extra",
		"bk builds test --fields number",
		"bk builds test --since yesterday",
		"bk grep test 12 job",
		"bk grep test 12 job (unclosed",
	} {
		t.Run(text, func(t *testing.T) {
			runner := &fakeRunner{}
			desk, _ := newDesk(t, runner)
			read := desk.read(context.Background(), text)
			if len(runner.calls) != 0 {
				t.Fatalf("ran %v", runner.calls[0].Argv)
			}
			if read.head != "bk read refused" || read.body == "" {
				t.Fatalf("reply = %+v", read)
			}
		})
	}
}

func TestALeadingProseOrLabelDoesNotHideTheRead(t *testing.T) {
	runner := &fakeRunner{reply: shell.Result{Stdout: "{}"}}
	desk, _ := newDesk(t, runner)

	desk.read(context.Background(), "please: bk api /pipelines/test/builds/9")

	if !strings.HasSuffix(strings.Join(runner.calls[0].Argv, " "), "/pipelines/test/builds/9") {
		t.Fatalf("ran %v", runner.calls[0].Argv)
	}
}

func TestABuildReadNamesItsStateAndEveryJobNotPassed(t *testing.T) {
	one := 1
	raw, err := json.Marshal(build{
		Number: 12, State: "failed", Branch: "feature", Commit: "abc123", WebURL: "https://buildkite.com/org/test/builds/12",
		Jobs: []job{
			{ID: "j-pass", Type: "script", Name: "Lint", State: "passed"},
			{ID: "j-skip", Type: "script", Name: "Skipped", State: "skipped"},
			{ID: "j-fail", Type: "script", Name: "Run sand tests", State: "failed", ExitStatus: &one},
			{ID: "j-soft", Type: "script", Name: "Flaky", State: "failed", ExitStatus: &one, SoftFailed: true},
			{ID: "j-run", Type: "script", Name: "Still going", State: "running"},
			{ID: "j-wait", Type: "waiter", State: "waiting_failed"},
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	runner := &fakeRunner{reply: shell.Result{Stdout: string(raw)}}
	desk, _ := newDesk(t, runner)

	read := desk.read(context.Background(), "bk build test 12")

	if want := []string{"tools/ci", "bk", "api", "-no-wait", "/pipelines/test/builds/12"}; !slices.Equal(runner.calls[0].Argv, want) {
		t.Fatalf("ran %v", runner.calls[0].Argv)
	}
	if read.head != "bk build test 12: failed, 3 of 5 jobs not passed" {
		t.Fatalf("head = %q", read.head)
	}
	for _, named := range []string{"build 12 failed on feature at abc123", "failed exit=1 job=j-fail Run sand tests", "failed soft exit=1 job=j-soft Flaky", "running exit=- job=j-run Still going"} {
		if !strings.Contains(read.body, named) {
			t.Fatalf("body misses %q:\n%s", named, read.body)
		}
	}
	if strings.Contains(read.body, "j-pass") || strings.Contains(read.body, "j-wait") {
		t.Fatalf("body names a job it should not:\n%s", read.body)
	}
	ask(t, "bk build test 12")
	firstPass(t, desk)
	if found := answers(t); len(found) != 1 || found[0].Refs.URL != "https://buildkite.com/org/test/builds/12" {
		t.Fatalf("answers = %+v, want one carrying the build's URL", found)
	}
}

func TestALogReplyKeepsItsTailWithoutTerminalEscapes(t *testing.T) {
	log := strings.Repeat("early line\n", 4000) + "\x1b_bk;t=1793716985000\x07\x1b[31mpanic: the real failure\x1b[0m\n"
	runner := &fakeRunner{reply: shell.Result{Stdout: log}}
	desk, _ := newDesk(t, runner)

	read := desk.read(context.Background(), "bk log test 12 "+failedJob)

	if len(read.body) > replyLimit+100 {
		t.Fatalf("body is %d bytes, over the %d limit", len(read.body), replyLimit)
	}
	if !strings.HasSuffix(read.body, "panic: the real failure\n") || !strings.HasPrefix(read.body, "… cut: the last") {
		t.Fatalf("body ends %q", read.body[len(read.body)-60:])
	}
}

func TestALogOfAFailedJobAnswersItsFailuresFromTheMiddleOfTheLog(t *testing.T) {
	failure := "--- FAIL: TestIrisServeWireChangeRaisesAPIVersion (0.31s)\n"
	log := strings.Repeat("=== RUN TestSomething\n", 4000) + "    wire_test.go:88: api version 41, want 42\n" + failure + strings.Repeat("ok  \tpkg\t0.1s\n", 4000)
	build := `{"number":12,"state":"failed","jobs":[{"id":"` + failedJob + `","type":"script","name":"go 3/8","state":"failed"}]}`
	runner := &fakeRunner{replies: map[string]string{"/pipelines/test/builds/12": build, failedJob: log}}
	desk, _ := newDesk(t, runner)

	read := desk.read(context.Background(), "bk log test 12 "+failedJob)

	if !strings.Contains(read.body, "wire_test.go:88: api version 41, want 42\n"+failure+"ok  \tpkg") {
		t.Fatalf("body misses the failure and its context:\n%s", read.body)
	}
	if !strings.HasSuffix(read.head, "1 lines matching "+failureMark+" in a "+strconv.Itoa(len(log))+" byte log, 3 lines of context each") {
		t.Fatalf("head = %q", read.head)
	}
}

func TestALogOfAPassedJobKeepsItsTail(t *testing.T) {
	log := "--- FAIL: TestRetriedThenPassed\n" + strings.Repeat("ok\n", 100)
	build := `{"number":12,"state":"passed","jobs":[{"id":"` + failedJob + `","type":"script","name":"go 3/8","state":"passed"}]}`
	runner := &fakeRunner{replies: map[string]string{"/pipelines/test/builds/12": build, failedJob: log}}
	desk, _ := newDesk(t, runner)

	read := desk.read(context.Background(), "bk log test 12 "+failedJob)

	if read.body != log {
		t.Fatalf("body = %q, want the whole short log", read.body)
	}
}

func TestAQuotedGrepRegexWithSpacesAndAlternationReachesTheDeskWhole(t *testing.T) {
	log := "ok one\npanic: boom\nok two\nok three\nok four\nok five\nok six\nok seven\nfatal error: concurrent map writes\nok eight\n"
	runner := &fakeRunner{replies: map[string]string{failedJob: log}}
	desk, _ := newDesk(t, runner)

	read := desk.read(context.Background(), "bk log test 12 "+failedJob+" --grep 'panic|fatal error' --context 0")

	if want := []string{"tools/ci", "bk", "log", "test", "12", failedJob}; len(runner.calls) != 1 || !slices.Equal(runner.calls[0].Argv, want) {
		t.Fatalf("ran %v, want only %v", runner.calls, want)
	}
	if read.body != "panic: boom\n--\nfatal error: concurrent map writes\n" {
		t.Fatalf("body = %q", read.body)
	}
	if !strings.Contains(read.head, "2 lines matching panic|fatal error in a") {
		t.Fatalf("head = %q", read.head)
	}
}

func TestALogGrepAnswersEachMatchWithItsContextAndCountsWhatDidNotFit(t *testing.T) {
	var log strings.Builder
	for i := range 20000 {
		fmt.Fprintf(&log, "line %d\n", i)
		if i%10 == 5 {
			fmt.Fprintf(&log, "panic: number %d\n", i)
		}
	}
	runner := &fakeRunner{replies: map[string]string{failedJob: log.String()}}
	desk, _ := newDesk(t, runner)

	read := desk.read(context.Background(), "bk log test 12 "+failedJob+" --grep panic: --context 1")

	if !strings.HasPrefix(read.body, "line 5\npanic: number 5\nline 6\n--\nline 15\npanic: number 15\nline 16\n--\n") {
		t.Fatalf("body starts %q", read.body[:80])
	}
	if len(read.body) > replyLimit {
		t.Fatalf("body = %d bytes, over the limit", len(read.body))
	}
	shown := strings.Count(read.body, "panic: number")
	if want := fmt.Sprintf(": %d of 2000 lines matching panic: in a %d byte log, %d more dropped to fit", shown, log.Len(), 2000-shown); !strings.HasSuffix(read.head, want) {
		t.Fatalf("head = %q, want suffix %q", read.head, want)
	}
	if len(runner.calls) != 1 {
		t.Fatalf("calls = %d, want only the log read when a regex is given", len(runner.calls))
	}
}

func TestAnAPIReplyKeepsItsHeadAndSaysItWasCut(t *testing.T) {
	runner := &fakeRunner{reply: shell.Result{Stdout: "{" + strings.Repeat("x", 3*replyLimit)}}
	desk, _ := newDesk(t, runner)

	read := desk.read(context.Background(), "bk api /x")

	if !strings.HasPrefix(read.body, "{xxx") || !strings.Contains(read.body, "… cut: the first") || len(read.body) > replyLimit+100 {
		t.Fatalf("body is %d bytes starting %q", len(read.body), read.body[:8])
	}
}

func TestAFailedReadKeepsErrorTextOutOfTheRecordLine(t *testing.T) {
	said := "ci bk: GET failed: 401 at 6:30 AM"
	runner := &fakeRunner{
		reply: shell.Result{Stderr: said},
		fail:  &shell.ExitError{Argv: []string{"tools/ci"}, Code: 75, Stderr: said},
	}
	desk, _ := newDesk(t, runner)
	ask(t, "bk api /x")

	firstPass(t, desk)

	found := answers(t)
	if len(found) != 1 {
		t.Fatalf("answers = %d, want 1: a failed read still answers", len(found))
	}
	if !strings.Contains(found[0].Text, "the read exited 75") || strings.Contains(found[0].Text, "6:30") {
		t.Fatalf("line = %q", found[0].Text)
	}
	if body := bodyOf(t, found[0]); body != said {
		t.Fatalf("body = %q", body)
	}
}

func TestAReadThatStayedOverloadedAnswersWithItsTriesAndLastError(t *testing.T) {
	said := "ci bk: GET /pipelines/release/builds/12379 failed: 503 envoy overloaded"
	runner := &fakeRunner{
		reply: shell.Result{Stderr: said},
		fail:  &shell.ExitError{Argv: []string{"tools/ci"}, Code: 1, Stderr: said, Tries: 5},
	}
	desk, _ := newDesk(t, runner)
	ask(t, "bk build release 12379")

	firstPass(t, desk)

	found := answers(t)
	if len(found) != 1 || !strings.Contains(found[0].Text, "the read exited 1 after 5 tries") {
		t.Fatalf("answers = %+v, want one naming the tries", found)
	}
	if body := bodyOf(t, found[0]); body != said {
		t.Fatalf("body = %q, want the last error", body)
	}
	if !runner.calls[0].Idempotent {
		t.Fatal("the read is not marked idempotent, so a transient refusal is not retried")
	}
}

func TestTheTokenReachesOnlyTheClientsEnvironment(t *testing.T) {
	runner := &fakeRunner{reply: shell.Result{Stdout: "{}"}}
	desk, _ := newDesk(t, runner)
	ask(t, "bk api /x")

	firstPass(t, desk)

	call := runner.calls[0]
	if !slices.Contains(call.Env, "BUILDKITE_TOKEN="+readToken) || strings.Contains(strings.Join(call.Argv, " "), readToken) {
		t.Fatalf("env %v argv %v", call.Env, call.Argv)
	}
	found := answers(t)
	raw, err := json.Marshal(found)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(raw), readToken) || strings.Contains(bodyOf(t, found[0]), readToken) {
		t.Fatal("the token reached a record")
	}
}

func TestADeskStartRefusesATokenCommandThatPrintsNothing(t *testing.T) {
	_, err := NewDesk(context.Background(), emptyToken{}, Settings{CLI: "tools/ci bk", Checkout: checkout, TokenCommand: []string{"print-token"}}, drive, t.TempDir(), time.Now, io.Discard)
	if err == nil {
		t.Fatal("a desk started with no token")
	}
}

type emptyToken struct{}

func (emptyToken) Run(context.Context, shell.Command) (shell.Result, error) {
	return shell.Result{}, nil
}

func TestSettingsNameTheClientCheckoutAndTokenCommand(t *testing.T) {
	path := filepath.Join(t.TempDir(), "bk.json")
	for name, body := range map[string]string{
		"no cli":      `{"checkout": "/c", "token_command": ["t"]}`,
		"no checkout": `{"cli": "tools/ci bk", "token_command": ["t"]}`,
		"no command":  `{"cli": "tools/ci bk", "checkout": "/c"}`,
		"not json":    `{`,
	} {
		t.Run(name, func(t *testing.T) {
			if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
				t.Fatal(err)
			}
			if _, err := LoadSettings(path); err == nil {
				t.Fatalf("%s loaded", body)
			}
		})
	}
	if err := os.WriteFile(path, []byte(`{"cli": "tools/ci bk", "checkout": "/c", "token_command": ["t"]}`), 0o600); err != nil {
		t.Fatal(err)
	}
	settings, err := LoadSettings(path)
	if err != nil || settings.CLI != "tools/ci bk" || settings.Checkout != "/c" {
		t.Fatalf("settings = %+v, %v", settings, err)
	}
}

func TestAGrepReadsTheWholeLogNotItsTail(t *testing.T) {
	needle := "--- PASS: TestTheNewTest (0.01s)\n"
	runner := &fakeRunner{reply: shell.Result{Stdout: needle + strings.Repeat("noise\n", replyLimit) + "\x1b[32m--- PASS: TestTheOtherTest (0.02s)\n"}}
	desk, _ := newDesk(t, runner)
	ask(t, "bk grep test 12 "+failedJob+" --- PASS: Test")

	firstPass(t, desk)

	found := answers(t)
	if len(found) != 1 {
		t.Fatalf("answers = %d, want 1", len(found))
	}
	if want := []string{"tools/ci", "bk", "log", "test", "12", failedJob}; !slices.Equal(runner.calls[0].Argv, want) {
		t.Fatalf("ran %v, want %v", runner.calls[0].Argv, want)
	}
	if body := bodyOf(t, found[0]); body != needle+"--- PASS: TestTheOtherTest (0.02s)\n" {
		t.Fatalf("body = %q", body)
	}
	if !strings.Contains(found[0].Text, "2 matching lines") {
		t.Fatalf("line = %q", found[0].Text)
	}
}

func TestJobsListsPassedJobsTooSoAPassCanBeProven(t *testing.T) {
	build := `{"number":12,"state":"passed","jobs":[{"id":"job-1","type":"script","name":"go tests","state":"passed"},{"id":"wait","type":"waiter","state":"passed"},{"id":"job-2","type":"script","name":"lint","state":"failed"}]}`
	runner := &fakeRunner{reply: shell.Result{Stdout: build}}
	desk, _ := newDesk(t, runner)
	ask(t, "bk jobs test 12")

	firstPass(t, desk)

	found := answers(t)
	if len(found) != 1 {
		t.Fatalf("answers = %d, want 1", len(found))
	}
	if body := bodyOf(t, found[0]); body != "passed job=job-1 go tests\nfailed job=job-2 lint\n" {
		t.Fatalf("body = %q", body)
	}
}

func TestAnOrganizationPathIsRefusedWithTheReadToSendInstead(t *testing.T) {
	desk, _ := newDesk(t, &fakeRunner{})

	read := desk.read(context.Background(), "bk api organizations/forge/pipelines/release/builds/13303/annotations")

	if read.head != "bk read refused" || !strings.HasSuffix(read.body, ": bk api /pipelines/release/builds/13303/annotations") {
		t.Fatalf("reply = %+v", read)
	}
}

func releaseAnnotations(t *testing.T) string {
	t.Helper()
	raw, err := json.Marshal([]annotation{
		{Context: "release-slack-root", Style: "info", BodyHTML: `<p><a href="https://slack.example/p1">Posted in #releases</a></p>`},
		{Context: "release-summary", Style: "warning", BodyHTML: "<p>" + strings.Repeat("🔴 rsw-cp/plat-usw2-prod delete Deployment<br>\n", replyLimit/40) + "</p>"},
		{Context: "retire-control-plane-restate", Style: "warning", BodyHTML: "<p>Left 1 control-plane generation(s) in place, not retired:</p>\n<ul>\n<li>\n<code>rsw-cp-bf72b7d</code> &mdash; unstarted invocations &amp; more [restate_generation_busy]</li>\n</ul>"},
	})
	if err != nil {
		t.Fatal(err)
	}
	return string(raw)
}

func TestAReleaseBuildsAnnotationsAnswerAsTextWithTheLongOnesNamed(t *testing.T) {
	runner := &fakeRunner{reply: shell.Result{Stdout: releaseAnnotations(t)}}
	desk, _ := newDesk(t, runner)
	ask(t, "bk annotations release 13303")

	firstPass(t, desk)

	if want := []string{"tools/ci", "bk", "api", "-no-wait", "/pipelines/release/builds/13303/annotations?page=1&per_page=100"}; len(runner.calls) != 1 || !slices.Equal(runner.calls[0].Argv, want) {
		t.Fatalf("ran %v, want only %v", runner.calls, want)
	}
	found := answers(t)
	if len(found) != 1 || found[0].Text != "bk annotations release 13303: 3 annotations, 1 too long to fit and named by context" {
		t.Fatalf("answers = %+v", found)
	}
	body := bodyOf(t, found[0])
	for _, shown := range []string{
		"── info release-slack-root\nPosted in #releases\n",
		"── warning release-summary: ",
		"bytes, so bk annotations release 13303 release-summary reads it\n",
		"── warning retire-control-plane-restate\nLeft 1 control-plane generation(s) in place, not retired:\n\n\nrsw-cp-bf72b7d — unstarted invocations & more [restate_generation_busy]\n",
	} {
		if !strings.Contains(body, shown) {
			t.Fatalf("body misses %q:\n%s", shown, body)
		}
	}
	if strings.Contains(body, "<") || strings.Contains(body, "delete Deployment") {
		t.Fatalf("body carries markup or the long annotation's text:\n%s", body)
	}
}

func TestOneAnnotationIsReadByItsContext(t *testing.T) {
	runner := &fakeRunner{reply: shell.Result{Stdout: releaseAnnotations(t)}}
	desk, _ := newDesk(t, runner)

	read := desk.read(context.Background(), "bk annotations release 13303 release-summary")
	missing := desk.read(context.Background(), "bk annotations release 13303 retire-go-restate-store")

	if !strings.HasPrefix(read.head, "bk annotations release 13303 release-summary: warning, ") || !strings.HasPrefix(read.body, "🔴 rsw-cp/plat-usw2-prod delete Deployment\n") || !strings.Contains(read.body, "… cut: the first") {
		t.Fatalf("reply = %q, body starts %q", read.head, read.body[:60])
	}
	if missing.head != "bk annotations release 13303 retire-go-restate-store: none of its 3 annotations has that context" || !strings.Contains(missing.body, "warning retire-control-plane-restate\n") {
		t.Fatalf("reply = %+v", missing)
	}
}

func TestALogNamedByStepKeyReadsThatStepsCurrentJob(t *testing.T) {
	build := `{"number":13303,"state":"failed","jobs":[` +
		`{"id":"01a1acea-0000-4000-8000-000000000001","type":"script","name":"Delete what rsw-cp left","state":"failed","step_key":"retire-rsw-cp--plat-usw2-prod","retried":true},` +
		`{"id":"` + failedJob + `","type":"script","name":"Delete what rsw-cp left","state":"passed","step_key":"retire-rsw-cp--plat-usw2-prod"},` +
		`{"id":"01a1ace4-307c-4975-99ba-4d7e6c9100f7","type":"script","name":"Plan restate","state":"passed","step_key":"plan-restate--plat-usw2-prod"}]}`
	runner := &fakeRunner{replies: map[string]string{"/pipelines/release/builds/13303": build, failedJob: "retired 2 generations\n"}}
	desk, _ := newDesk(t, runner)
	ask(t, "bk log release 13303 retire-rsw-cp--plat-usw2-prod")

	firstPass(t, desk)

	want := [][]string{
		{"tools/ci", "bk", "api", "-no-wait", "/pipelines/release/builds/13303"},
		{"tools/ci", "bk", "log", "release", "13303", failedJob},
	}
	if len(runner.calls) != len(want) || !slices.Equal(runner.calls[0].Argv, want[0]) || !slices.Equal(runner.calls[1].Argv, want[1]) {
		t.Fatalf("ran %v, want %v", runner.calls, want)
	}
	found := answers(t)
	if len(found) != 1 || bodyOf(t, found[0]) != "retired 2 generations\n" {
		t.Fatalf("answers = %+v", found)
	}
}

func TestAStepKeyNoJobCarriesIsAnsweredWithoutALogRead(t *testing.T) {
	runner := &fakeRunner{reply: shell.Result{Stdout: `{"number":13303,"jobs":[{"id":"` + failedJob + `","type":"script","state":"passed","step_key":"plan-restate--plat-usw2-prod"}]}`}}
	desk, _ := newDesk(t, runner)

	read := desk.read(context.Background(), "bk grep release 13303 apply-restate--plat-usw2-prod error")

	if len(runner.calls) != 1 {
		t.Fatalf("ran %v, want only the build read", runner.calls)
	}
	if read.head != "bk grep release 13303 apply-restate--plat-usw2-prod error: 0 jobs of build 13303 carry step key apply-restate--plat-usw2-prod, so name one job id, or the step key and its N/M; bk jobs release 13303 lists them" {
		t.Fatalf("head = %q", read.head)
	}
}

func TestAParallelStepsJobsAreListedWithTheirShardAndOneIsReadByItsStepKeyAndShard(t *testing.T) {
	const shardJob = "01a1acec-0000-4000-8000-000000000022"
	build := `{"number":12,"state":"failed","jobs":[` +
		`{"id":"01a1acec-0000-4000-8000-000000000021","type":"script","name":"Run sand tests","state":"passed","step_key":"sand-next-tests","parallel_group_index":20,"parallel_group_total":36},` +
		`{"id":"` + shardJob + `","type":"script","name":"Run sand tests","state":"failed","step_key":"sand-next-tests","parallel_group_index":21,"parallel_group_total":36},` +
		`{"id":"` + failedJob + `","type":"script","name":"lint","state":"passed","step_key":"lint"}]}`
	runner := &fakeRunner{replies: map[string]string{"/pipelines/test/builds/12": build, shardJob: "ok TestFast\n--- FAIL: TestSlow (9.00s)\n"}}
	desk, _ := newDesk(t, runner)

	jobs := desk.read(context.Background(), "bk jobs test 12")
	shard := desk.read(context.Background(), "bk log test 12 sand-next-tests 22/36")
	last := runner.calls[len(runner.calls)-1].Argv
	unnamed := desk.read(context.Background(), "bk log test 12 sand-next-tests --grep FAIL")

	if jobs.body != "passed job=01a1acec-0000-4000-8000-000000000021 Run sand tests 21/36\nfailed job="+shardJob+" Run sand tests 22/36\npassed job="+failedJob+" lint\n" {
		t.Fatalf("jobs body = %q", jobs.body)
	}
	if !slices.Equal(last, []string{"tools/ci", "bk", "log", "test", "12", shardJob}) || !strings.Contains(shard.body, "--- FAIL: TestSlow") {
		t.Fatalf("shard read ran %v and answered %+v", last, shard)
	}
	if !strings.Contains(unnamed.head, "2 jobs of build 12 carry step key sand-next-tests, so name one job id, or the step key and its N/M") || !strings.Contains(unnamed.body, " 22/36\n") {
		t.Fatalf("unnamed = %+v", unnamed)
	}
}

const listedCommit = "0123456789abcdef0123456789abcdef01234567"

func listedBuilds(from, count, messageBytes int) string {
	builds := make([]map[string]any, count)
	for i := range builds {
		builds[i] = map[string]any{
			"number": from - i, "state": "passed", "branch": "releases/rollback/1", "commit": listedCommit, "created_at": "2026-11-04T05:41:31Z",
			"message": "Undo it\n" + strings.Repeat("m", messageBytes), "env": map[string]string{"HUGE": strings.Repeat("e", 1000)},
		}
	}
	raw, err := json.Marshal(builds)
	if err != nil {
		panic(err)
	}
	return string(raw)
}

func TestABuildListingIsOneReadThatKeepsWhatACallerNeeds(t *testing.T) {
	runner := &fakeRunner{reply: shell.Result{Stdout: listedBuilds(27, 27, 100)}}
	desk, _ := newDesk(t, runner)

	read := desk.read(context.Background(), "bk builds release --branch releases/rollback/* --since 2026-10-22")

	if len(runner.calls) != 1 {
		t.Fatalf("calls = %d, want the one page 27 builds fit in", len(runner.calls))
	}
	path := runner.calls[0].Argv[len(runner.calls[0].Argv)-1]
	parsed, err := url.Parse(path)
	if err != nil {
		t.Fatal(err)
	}
	query := parsed.Query()
	if parsed.Path != "/pipelines/release/builds" || query.Get("branch") != "releases/rollback/*" || query.Get("created_from") != "2026-10-22T00:00:00Z" || query.Get("exclude_jobs") != "true" {
		t.Fatalf("read %s", path)
	}
	if read.head != "bk builds release branch=releases/rollback/* created_from=2026-10-22T00:00:00Z: 27 builds" {
		t.Fatalf("head = %q", read.head)
	}
	if !strings.HasPrefix(read.body, "27 passed releases/rollback/1 "+listedCommit+" Undo it\n26 passed") {
		t.Fatalf("body misses the first build's one line:\n%s", read.body)
	}
	if strings.Contains(read.body, "HUGE") || strings.Contains(read.body, "mmm") {
		t.Fatal("body carries more than each build's subject line")
	}
}

func TestABuildListingPagesAtTheDeskUntilAShortPage(t *testing.T) {
	runner := &fakeRunner{replies: map[string]string{}}
	desk, _ := newDesk(t, runner)
	first := "/pipelines/test/builds?exclude_jobs=true&exclude_pipeline=true&page=1&per_page=100"
	second := "/pipelines/test/builds?exclude_jobs=true&exclude_pipeline=true&page=2&per_page=100"
	runner.replies[first] = listedBuilds(103, perPage, 0)
	runner.replies[second] = listedBuilds(3, 3, 0)

	read := desk.read(context.Background(), "bk builds test")

	if len(runner.calls) != 2 {
		t.Fatalf("calls = %d, want 2", len(runner.calls))
	}
	if read.head != "bk builds test: 103 builds" {
		t.Fatalf("head = %q", read.head)
	}
}

func TestABuildListingFitsAWholePageHoweverLongItsMessages(t *testing.T) {
	runner := &fakeRunner{replies: map[string]string{}}
	desk, _ := newDesk(t, runner)
	runner.replies["/pipelines/test/builds?exclude_jobs=true&exclude_pipeline=true&page=1&per_page=100"] = listedBuilds(100, perPage, 5000)
	runner.replies["/pipelines/test/builds?exclude_jobs=true&exclude_pipeline=true&page=2&per_page=100"] = "[]"

	read := desk.read(context.Background(), "bk builds test")

	if read.head != "bk builds test: 100 builds" {
		t.Fatalf("head = %q, want every build of a page whose messages run 5 KB each", read.head)
	}
	if lines := strings.Count(read.body, "\n"); lines != 100 {
		t.Fatalf("body has %d lines, want one per build", lines)
	}
}

func TestABuildListingStopsReadingOnceTheAnswerIsFull(t *testing.T) {
	runner := &fakeRunner{reply: shell.Result{Stdout: listedBuilds(100, perPage, 0)}}
	desk, _ := newDesk(t, runner)

	read := desk.read(context.Background(), "bk builds test")

	if len(runner.calls) != 3 {
		t.Fatalf("calls = %d, want the third page, which fills the answer, to be the last", len(runner.calls))
	}
	if len(read.body) > replyLimit {
		t.Fatalf("body = %d bytes, over the limit", len(read.body))
	}
	shown := strings.Count(read.body, "\n")
	if want := fmt.Sprintf("bk builds test: the newest %d builds; older ones did not fit, so narrow with --branch or --since", shown); read.head != want {
		t.Fatalf("head = %q, want %q", read.head, want)
	}
	if !strings.HasSuffix(read.body, " Undo it\n") {
		t.Fatal("the last listed build was cut mid-line")
	}
}

func TestAnAPIReadWithFieldsAnswersOneLinePerElement(t *testing.T) {
	path := "/pipelines/test/builds?state=failed"
	runner := &fakeRunner{replies: map[string]string{path: `[{"number":12,"state":"failed","commit":"abc","creator":{"name":"Ann"},"message":"fix it\n\nlong body"},{"number":11,"state":"failed","commit":"def","creator":null,"message":"other"}]`}}
	desk, _ := newDesk(t, runner)

	read := desk.read(context.Background(), "bk api "+path+" --fields number,state,commit,creator.name,message")

	if want := []string{"tools/ci", "bk", "api", "-no-wait", path}; len(runner.calls) != 1 || !slices.Equal(runner.calls[0].Argv, want) {
		t.Fatalf("ran %v", runner.calls)
	}
	if read.body != "12 failed abc Ann fix it\n11 failed def - other\n" {
		t.Fatalf("body = %q", read.body)
	}
	if !strings.HasSuffix(read.head, ": 2 rows") {
		t.Fatalf("head = %q", read.head)
	}
}

func TestABuildListingWithNoMatchSaysSo(t *testing.T) {
	runner := &fakeRunner{reply: shell.Result{Stdout: "[]"}}
	desk, _ := newDesk(t, runner)
	opened := ask(t, "bk builds test --branch nothing/*")

	firstPass(t, desk)

	found := answers(t)
	if len(found) != 1 || found[0].Resolves != opened.Seq || !strings.HasSuffix(found[0].Text, ": 0 builds") {
		t.Fatalf("answers = %+v", found)
	}
}
