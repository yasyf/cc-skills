package bk

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	spriteIP = "100.64.0.1"
	laptopIP = "100.64.0.2"
	admitted = "tag:orca"
	checkout = "/drive/checkout"
)

type fakeRunner struct {
	tags  map[string][]string
	calls []shell.Command
	reply shell.Result
}

func (f *fakeRunner) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	if c.Argv[0] == "tailscale" {
		raw, err := json.Marshal(map[string]any{"Node": map[string]any{"Tags": f.tags[c.Argv[len(c.Argv)-1]]}})
		return shell.Result{Stdout: string(raw)}, err
	}
	f.calls = append(f.calls, c)
	if f.reply.Code != 0 {
		return f.reply, &shell.ExitError{Argv: c.Argv, Code: f.reply.Code, Stderr: f.reply.Stderr}
	}
	return f.reply, nil
}

func newRunner() *fakeRunner {
	return &fakeRunner{tags: map[string][]string{spriteIP: {"tag:other", admitted}, laptopIP: {"tag:laptop"}}}
}

func proxyFor(runner *fakeRunner) Proxy {
	return Proxy{Runner: runner, CLI: []string{"tools/ci", "bk"}, Checkout: checkout, AdmitTag: admitted}
}

func proxied(runner *fakeRunner, caller string, args []string) *httptest.ResponseRecorder {
	body, _ := json.Marshal(args)
	request := httptest.NewRequest(http.MethodPost, "/", bytes.NewReader(body))
	if caller != "" {
		request.Header.Set("X-Forwarded-For", caller)
	}
	recorder := httptest.NewRecorder()
	proxyFor(runner).ServeHTTP(recorder, request)
	return recorder
}

func TestTheProxyRefusesAWriteAMutationAndEveryCallerButAnAdmittedSprite(t *testing.T) {
	read := []string{"api", "/pipelines/test/builds/1"}
	cases := []struct {
		name, caller string
		args         []string
	}{
		{"a local caller tailscale serve did not forward", "", read},
		{"a tailnet machine without the admitted tag", laptopIP, read},
		{"a POST", spriteIP, []string{"api", "-X", "POST", "/pipelines/test/builds"}},
		{"a body", spriteIP, []string{"api", "-d", "{}", "/pipelines/test/builds"}},
		{"another organization", spriteIP, []string{"api", "/organizations/other/pipelines"}},
		{"a cancel", spriteIP, []string{"cancel", "test", "1"}},
		{"a rerun", spriteIP, []string{"rerun", "test", "dev", "--unit", "go"}},
		{"a mutation", spriteIP, []string{"graphql", "mutation { buildCancel(input: {id: \"x\"}) { build { id } } }"}},
		{"a mutation after a query", spriteIP, []string{"graphql", "-v", "{}", "query { viewer { id } } mutation { a }"}},
		{"a subscription", spriteIP, []string{"graphql", "subscription { builds { id } }"}},
	}
	for _, c := range cases {
		runner := newRunner()
		if got := proxied(runner, c.caller, c.args); got.Code != http.StatusForbidden || len(runner.calls) != 0 {
			t.Errorf("%s: status %d, %d bk runs: %s", c.name, got.Code, len(runner.calls), got.Body.String())
		}
	}
}

func TestAnAdmittedReadRunsTheCheckoutsBuildkiteCLI(t *testing.T) {
	reads := [][]string{
		{"api", "--no-wait", "/pipelines/test/builds/7"},
		{"log", "test", "7", "0190-job"},
		{"graphql", "-v", `{"slug":"test"}`, "# no mutation here\nquery($slug: ID!) { pipeline(slug: $slug) { description(match: \"mutation\") } }"},
	}
	for _, args := range reads {
		runner := newRunner()
		runner.reply = shell.Result{Stdout: "{}"}
		got := proxied(runner, spriteIP, args)
		var reply Reply
		if got.Code != http.StatusOK || json.Unmarshal(got.Body.Bytes(), &reply) != nil || reply != (Reply{Stdout: "{}"}) {
			t.Fatalf("%v: status %d: %s", args, got.Code, got.Body.String())
		}
		want := shell.Command{Argv: append([]string{"tools/ci", "bk"}, args...), Dir: checkout, Timeout: readTimeout}
		if len(runner.calls) != 1 || !reflect.DeepEqual(runner.calls[0], want) {
			t.Fatalf("%v: ran %+v", args, runner.calls)
		}
	}
}

func TestForwardPrintsTheReadAndReturnsItsExitCode(t *testing.T) {
	runner := newRunner()
	runner.reply = shell.Result{Code: 75, Stdout: "partial", Stderr: "ci bk: 429"}
	served := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		r.Header.Set("X-Forwarded-For", spriteIP)
		proxyFor(runner).ServeHTTP(w, r)
	}))
	defer served.Close()
	home := t.TempDir()
	var stdout, stderr bytes.Buffer
	if _, err := Forward(context.Background(), home, []string{"api", "/user"}, &stdout, &stderr); err == nil || !strings.Contains(err.Error(), "WORKER_BK_URL") {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(home, ".config", "lr"), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(home, urlFile), []byte(served.URL+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	code, err := Forward(context.Background(), home, []string{"api", "--no-wait", "/pipelines/test/builds"}, &stdout, &stderr)
	if err != nil || code != 75 || stdout.String() != "partial" || stderr.String() != "ci bk: 429" {
		t.Fatal(code, err, stdout.String(), stderr.String())
	}
	if _, err := Forward(context.Background(), home, []string{"cancel", "test", "1"}, &stdout, &stderr); err == nil || !strings.Contains(err.Error(), "403") {
		t.Fatal(err)
	}
}
