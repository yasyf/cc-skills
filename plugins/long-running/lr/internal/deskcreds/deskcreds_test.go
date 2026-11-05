package deskcreds

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

var t0 = time.Date(2026, 11, 2, 12, 0, 0, 0, time.UTC)

func clock() time.Time { return t0 }

func credentials(hours int) map[string]any {
	return map[string]any{"Version": 1, "AccessKeyId": "AKIA", "SecretAccessKey": "secret", "SessionToken": "token", "Expiration": t0.Add(time.Duration(hours) * time.Hour).Format(time.RFC3339)}
}

func serving(t *testing.T) int {
	t.Helper()
	server := httptest.NewServer(Handler(clock, io.Discard))
	t.Cleanup(server.Close)
	_, port, err := net.SplitHostPort(server.Listener.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	number, err := strconv.Atoi(port)
	if err != nil {
		t.Fatal(err)
	}
	return number
}

func put(t *testing.T, port int, key string, body any) int {
	t.Helper()
	encoded, err := json.Marshal(body)
	if err != nil {
		t.Fatal(err)
	}
	request, err := http.NewRequestWithContext(context.Background(), http.MethodPut, fmt.Sprintf("http://127.0.0.1:%d/%s", port, key), strings.NewReader(string(encoded)))
	if err != nil {
		t.Fatal(err)
	}
	answer, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = answer.Body.Close() }()
	return answer.StatusCode
}

func TestAPushedProfileReadsBackForItsCredentialProcess(t *testing.T) {
	port := serving(t)
	if status := put(t, port, "release-operator", credentials(1)); status != http.StatusNoContent {
		t.Fatalf("put %d", status)
	}
	var whole, field strings.Builder
	if err := Get(context.Background(), "release-operator", "", port, &whole); err != nil {
		t.Fatal(err)
	}
	var got map[string]any
	if err := json.Unmarshal([]byte(whole.String()), &got); err != nil {
		t.Fatal(err)
	}
	if got["AccessKeyId"] != "AKIA" || got["Version"] != float64(1) || len(got) != 5 {
		t.Fatalf("got %v", got)
	}
	if err := Get(context.Background(), "release-operator", "AccessKeyId", port, &field); err != nil || field.String() != "AKIA\n" {
		t.Fatalf("got %q %v", field.String(), err)
	}
}

func TestExpiredOrUnpushedCredentialsAreNeverServed(t *testing.T) {
	port := serving(t)
	if status := put(t, port, "core-auto", credentials(-1)); status != http.StatusBadRequest {
		t.Fatalf("put %d", status)
	}
	var out strings.Builder
	if err := Get(context.Background(), "core-auto", "", port, &out); err == nil {
		t.Fatalf("served %q", out.String())
	}
}

func TestAPushedSecretReadsBackUntilItExpires(t *testing.T) {
	port := serving(t)
	ahead := t0.Add(45 * time.Minute).Format(time.RFC3339)
	if status := put(t, port, "secret/GH_TOKEN", map[string]string{"Value": "gho_user", "Expiration": ahead}); status != http.StatusNoContent {
		t.Fatalf("put %d", status)
	}
	held, err := Fetch(context.Background(), "secret/GH_TOKEN", port)
	if err != nil || string(held["Value"]) != `"gho_user"` {
		t.Fatalf("held %v %v", held, err)
	}
	if status := put(t, port, "secret/GH_TOKEN", map[string]string{"Value": "gho_old", "Expiration": t0.Add(-time.Minute).Format(time.RFC3339)}); status != http.StatusBadRequest {
		t.Fatalf("expired put %d", status)
	}
	if status := put(t, port, "secret/BUILDKITE_TOKEN", map[string]string{"Expiration": ahead}); status != http.StatusBadRequest {
		t.Fatalf("valueless put %d", status)
	}
	if _, err := Fetch(context.Background(), "secret/BUILDKITE_TOKEN", port); err == nil {
		t.Fatal("served a secret nobody pushed")
	}
}

type fakeHost struct {
	mu      sync.Mutex
	calls   []shell.Command
	replies map[string]shell.Result
	fail    map[string]bool
}

func (f *fakeHost) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.calls = append(f.calls, c)
	joined := strings.Join(c.Argv, " ")
	for needle := range f.fail {
		if strings.Contains(joined, needle) {
			return shell.Result{Code: 1, Stderr: "refused\n"}, &shell.ExitError{Argv: c.Argv, Code: 1, Stderr: "refused\n"}
		}
	}
	for needle, reply := range f.replies {
		if strings.Contains(joined, needle) {
			return reply, nil
		}
	}
	return shell.Result{}, nil
}

func (f *fakeHost) index(needle string) int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return slices.IndexFunc(f.calls, func(c shell.Command) bool { return strings.Contains(strings.Join(c.Argv, " "), needle) })
}

func writeConfig(t *testing.T, dir string, config map[string]any) string {
	t.Helper()
	data, err := json.Marshal(config)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(dir, "runner.json")
	if err := os.WriteFile(path, data, 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func envFor(t *testing.T, runner shell.Runner) (Env, *strings.Builder) {
	t.Helper()
	var out strings.Builder
	return Env{Runner: runner, Out: &out, Err: &out, Now: clock}, &out
}

func TestPushHandsEachSecretOverStandardInputWithAnExpiryThreePushesOut(t *testing.T) {
	exported, err := json.Marshal(credentials(1))
	if err != nil {
		t.Fatal(err)
	}
	host := &fakeHost{replies: map[string]shell.Result{"aws configure": {Stdout: string(exported)}, "gh auth token": {Stdout: "gho_user\n"}}}
	e, _ := envFor(t, host)
	config := writeConfig(t, t.TempDir(), map[string]any{"host": map[string]any{"sprite": "dar-desk-1", "aws_profiles": []string{"core-auto"}, "secrets": map[string][]string{"GH_TOKEN": {"gh", "auth", "token"}}}})

	if err := e.Push(context.Background(), config, 9181); err != nil {
		t.Fatal(err)
	}

	sent := map[string]map[string]any{}
	for _, call := range host.calls {
		if call.Argv[0] != "sprite" {
			continue
		}
		var body map[string]any
		if err := json.Unmarshal([]byte(call.Stdin), &body); err != nil {
			t.Fatal(err)
		}
		_, key, _ := strings.Cut(call.Argv[len(call.Argv)-1], "9181/")
		sent[key] = body
		if strings.Contains(strings.Join(call.Argv, " "), "gho_user") {
			t.Fatalf("a secret reached an argument: %v", call.Argv)
		}
	}
	if sent["core-auto"]["AccessKeyId"] != "AKIA" || sent["secret/GH_TOKEN"]["Value"] != "gho_user" {
		t.Fatalf("sent %v", sent)
	}
	if sent["secret/GH_TOKEN"]["Expiration"] != t0.Add(45*time.Minute).Format(time.RFC3339) {
		t.Fatalf("expiry %v", sent["secret/GH_TOKEN"]["Expiration"])
	}
}

func TestAFailedPushNamesWhatDidNotArriveAndStillSendsTheRest(t *testing.T) {
	host := &fakeHost{fail: map[string]bool{"--profile core-auto": true}, replies: map[string]shell.Result{"gh auth token": {Stdout: "gho_user\n"}}}
	e, out := envFor(t, host)
	config := writeConfig(t, t.TempDir(), map[string]any{"host": map[string]any{"sprite": "s", "aws_profiles": []string{"core-auto"}, "secrets": map[string][]string{"GH_TOKEN": {"gh", "auth", "token"}}}})

	err := e.Push(context.Background(), config, 9181)

	if err == nil || !strings.Contains(out.String(), "core-auto did not export: refused") {
		t.Fatalf("got %v, said %q", err, out.String())
	}
	if host.index("secret/GH_TOKEN") < 0 {
		t.Fatalf("the secret was not sent: %+v", host.calls)
	}
}

const drive = "d1"

type said struct {
	mu   sync.Mutex
	text strings.Builder
}

func (s *said) Write(p []byte) (int, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.text.Write(p)
}

func (s *said) String() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.text.String()
}

func waitFor(t *testing.T, what string, done func() bool) {
	t.Helper()
	deadline := time.Now().Add(30 * time.Second)
	for !done() {
		if time.Now().After(deadline) {
			t.Fatalf("never saw %s", what)
		}
		time.Sleep(20 * time.Millisecond)
	}
}

func records(t *testing.T, kind string) []client.Record {
	t.Helper()
	found, err := client.Tail(context.Background(), client.Query{Drive: drive, Kinds: []string{kind}})
	if err != nil {
		t.Fatal(err)
	}
	return found
}

func TestAWaitingGetAnswersThePushThatArrivesWhileItWaits(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	port := serving(t)
	var out, log said
	waited := make(chan error, 1)
	go func() {
		waited <- Wait(context.Background(), "secret/TRUNK_MERGE_API_KEY", "Value", port, drive, time.Minute, &log, &out)
	}()
	ahead := t0.Add(45 * time.Minute).Format(time.RFC3339)
	waitFor(t, "the wait begin", func() bool { return strings.Contains(log.String(), "waiting on secret/TRUNK_MERGE_API_KEY") })

	if status := put(t, port, "secret/TRUNK_MERGE_API_KEY", map[string]string{"Value": "trunk", "Expiration": ahead}); status != http.StatusNoContent {
		t.Fatalf("put %d", status)
	}

	if err := <-waited; err != nil || out.String() != "trunk\n" {
		t.Fatalf("got %q %v", out.String(), err)
	}
	if blockers := records(t, "blocker"); len(blockers) != 0 {
		t.Fatalf("a wait the push ended escalated: %+v", blockers)
	}
}

func TestAGetStillWaitingPastItsPatienceTellsRootOnceWhatItWaitsOn(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	port := serving(t)
	var out, log said
	waited := make(chan error, 1)
	go func() {
		waited <- Wait(context.Background(), "secret/TRUNK_MERGE_API_KEY", "Value", port, drive, 50*time.Millisecond, &log, &out)
	}()
	waitFor(t, "the escalation", func() bool { return len(records(t, "blocker")) > 0 })

	ahead := t0.Add(45 * time.Minute).Format(time.RFC3339)
	if status := put(t, port, "secret/TRUNK_MERGE_API_KEY", map[string]string{"Value": "trunk", "Expiration": ahead}); status != http.StatusNoContent {
		t.Fatalf("put %d", status)
	}

	if err := <-waited; err != nil || out.String() != "trunk\n" {
		t.Fatalf("got %q %v", out.String(), err)
	}
	blockers := records(t, "blocker")
	if len(blockers) != 1 || !slices.Equal(blockers[0].To, []string{"root"}) || !strings.Contains(blockers[0].Text, "waiting on secret/TRUNK_MERGE_API_KEY") || strings.Contains(blockers[0].Text, "trunk") {
		t.Fatalf("blockers %+v", blockers)
	}
}

func TestAPlainGetOfAnUnpushedKeyStillFailsAtOnce(t *testing.T) {
	port := serving(t)
	var out strings.Builder
	if err := Get(context.Background(), "secret/TRUNK_MERGE_API_KEY", "Value", port, &out); err == nil || !strings.Contains(err.Error(), "HTTP 404") {
		t.Fatalf("got %v", err)
	}
}

func TestKeepPushesWhenARestartedServeAsksAndAnswersTheAsk(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	if _, err := client.Post(context.Background(), client.Draft{Drive: drive, Lane: "root", Kind: "note", Text: "a drive has records before the host restarts"}); err != nil {
		t.Fatal(err)
	}
	host := &fakeHost{replies: map[string]shell.Result{"gh auth token": {Stdout: "gho_user\n"}}}
	e, _ := envFor(t, host)
	config := writeConfig(t, t.TempDir(), map[string]any{"drive": drive, "host": map[string]any{"sprite": "dar-desk-1", "secrets": map[string][]string{"GH_TOKEN": {"gh", "auth", "token"}}}})
	ctx, stop := context.WithCancel(context.Background())
	kept := make(chan error, 1)
	go func() { kept <- e.Keep(ctx, config, 9181) }()
	waitFor(t, "the first push", func() bool { return host.index("secret/GH_TOKEN") >= 0 })

	started := t0
	waitFor(t, "an answered ask", func() bool {
		started = started.Add(time.Second)
		if err := AskForPush(context.Background(), drive, started); err != nil {
			t.Fatal(err)
		}
		return len(records(t, "answer")) > 0
	})
	stop()

	if err := <-kept; err != nil && !errors.Is(err, context.Canceled) {
		t.Fatalf("Keep = %v", err)
	}
	answer := records(t, "answer")[0]
	if answer.Lane != PushLane || !slices.Equal(answer.To, []string{serveLane}) || !strings.Contains(answer.Text, "dar-desk-1") {
		t.Fatalf("answer %+v", answer)
	}
	host.mu.Lock()
	defer host.mu.Unlock()
	pushes := 0
	for _, call := range host.calls {
		if strings.HasSuffix(call.Argv[len(call.Argv)-1], "/secret/GH_TOKEN") {
			pushes++
		}
	}
	if pushes < 2 {
		t.Fatalf("%d pushes: the ask did not push", pushes)
	}
}

func TestARestartedServeAsksThePushLaneOncePerStart(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	for _, started := range []time.Time{t0, t0.Add(11 * time.Minute)} {
		if err := AskForPush(context.Background(), drive, started); err != nil {
			t.Fatal(err)
		}
	}
	asks := records(t, askKind)
	if len(asks) != 2 || !slices.Equal(asks[1].To, []string{PushLane}) || asks[0].Seq == asks[1].Seq {
		t.Fatalf("asks %+v", asks)
	}
}
