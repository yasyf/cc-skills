package bk

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	drive     = "brook"
	lane      = "chat-list-1"
	checkout  = "/drive/checkout"
	readToken = "bk-token-never-in-a-record"
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
	posted, err := client.Post(context.Background(), client.Draft{Drive: drive, Lane: from, Kind: askKind, Text: text, To: []string{Lane}, Force: true})
	if err != nil {
		t.Fatal(err)
	}
	return posted
}

func firstPass(t *testing.T, desk *Desk) client.Query {
	t.Helper()
	next, err := desk.pass(context.Background(), client.Query{Drive: drive, Since: time.Now().Add(-catchUp), Kinds: []string{askKind}})
	if err != nil {
		t.Fatal(err)
	}
	return next
}

func answers(t *testing.T) []client.Record {
	t.Helper()
	found, err := client.Tail(context.Background(), client.Query{Drive: drive, Kinds: []string{answerKind}, Lanes: []string{Lane}})
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
	still, err := client.Open(context.Background(), drive, askKind)
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

	if _, err := desk.pass(context.Background(), next); err != nil {
		t.Fatal(err)
	}
	if _, err := desk.pass(context.Background(), next); err != nil {
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
	still, err := client.Open(context.Background(), drive, askKind)
	if err != nil || len(still) != 1 || still[0].Seq != stuck.Seq {
		t.Fatalf("open asks = %+v, %v, want only #%d", still, err, stuck.Seq)
	}
}

func TestAnAskToAnotherLaneIsLeftAlone(t *testing.T) {
	runner := &fakeRunner{}
	desk, _ := newDesk(t, runner)
	if _, err := client.Post(context.Background(), client.Draft{Drive: drive, Lane: lane, Kind: askKind, Text: "bk api /x", To: []string{"root"}, Force: true}); err != nil {
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
		"bk log test 12",
		"bk log test 12 job extra",
		"bk build test",
		"bk build test twelve",
		"bk jobs test twelve",
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

	read := desk.read(context.Background(), "bk log test 12 j-fail")

	if len(read.body) > replyLimit+100 {
		t.Fatalf("body is %d bytes, over the %d limit", len(read.body), replyLimit)
	}
	if !strings.HasSuffix(read.body, "panic: the real failure\n") || !strings.HasPrefix(read.body, "… cut: the last") {
		t.Fatalf("body ends %q", read.body[len(read.body)-60:])
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
	ask(t, "bk grep test 12 job-1 --- PASS: Test")

	firstPass(t, desk)

	found := answers(t)
	if len(found) != 1 {
		t.Fatalf("answers = %d, want 1", len(found))
	}
	if want := []string{"tools/ci", "bk", "log", "test", "12", "job-1"}; !slices.Equal(runner.calls[0].Argv, want) {
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
