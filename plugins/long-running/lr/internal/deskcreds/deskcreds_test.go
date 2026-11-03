package deskcreds

import (
	"context"
	"encoding/json"
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
	"testing"
	"time"

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
	calls   []shell.Command
	replies map[string]shell.Result
	fail    map[string]bool
}

func (f *fakeHost) Run(_ context.Context, c shell.Command) (shell.Result, error) {
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
