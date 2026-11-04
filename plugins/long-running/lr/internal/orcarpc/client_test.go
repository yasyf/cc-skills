package orcarpc

import (
	"bufio"
	"bytes"
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/coder/websocket"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"golang.org/x/crypto/nacl/box"
)

type frame struct {
	ID                    string          `json:"id"`
	AuthToken             string          `json:"authToken"`
	Method                string          `json:"method"`
	Params                json.RawMessage `json:"params"`
	ContractVersion       int             `json:"orchestrationContractVersion"`
	CompatibilityID       string          `json:"compatibilityInvocationId"`
	CompatibilityEvidence map[string]any  `json:"orchestrationCompatibilityEvidence"`
	RequestID             string          `json:"orchestrationRequestId"`
	ExpectedRuntimeSource *expectedSource `json:"expectedRuntimeSource"`
}

type recorder struct {
	mu     sync.Mutex
	frames []frame
}

func (r *recorder) add(got frame) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.frames = append(r.frames, got)
}

func (r *recorder) all() []frame {
	r.mu.Lock()
	defer r.mu.Unlock()
	return slices.Clone(r.frames)
}

func serve(t *testing.T, env map[string]string, answer func(frame) []string) (*Client, *recorder) {
	t.Helper()
	dir, err := os.MkdirTemp("", "orcarpc")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(dir) })
	socket := filepath.Join(dir, "o.sock")
	listener, err := net.Listen("unix", socket)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = listener.Close() })
	metadata := fmt.Appendf(nil, `{"runtimeId":"rt","pid":7,"transports":[{"kind":"websocket","endpoint":"ws://x"},{"kind":"unix","endpoint":%q}],"authToken":"tok"}`, socket)
	if err := os.WriteFile(filepath.Join(dir, metadataFile), metadata, 0o600); err != nil {
		t.Fatal(err)
	}
	seen := &recorder{}
	go func() {
		for {
			conn, err := listener.Accept()
			if err != nil {
				return
			}
			go func() {
				defer func() { _ = conn.Close() }()
				scanner := bufio.NewScanner(conn)
				for scanner.Scan() {
					var got frame
					if json.Unmarshal(scanner.Bytes(), &got) != nil {
						return
					}
					seen.add(got)
					for _, line := range answer(got) {
						_, _ = fmt.Fprintln(conn, line)
					}
				}
			}()
		}
	}()
	return &Client{UserData: dir, Getenv: func(name string) string { return env[name] }, Timeout: 5 * time.Second}, seen
}

func ok(id, result string) string {
	return fmt.Sprintf(`{"id":%q,"ok":true,"result":%s,"_meta":{"runtimeId":"rt"}}`, id, result)
}

func answering(result string) func(frame) []string {
	return func(got frame) []string { return []string{ok(got.ID, result)} }
}

func served(t *testing.T, client *Client, args ...string) Served {
	t.Helper()
	reply, handled := client.Serve(context.Background(), args)
	if !handled {
		t.Fatalf("%v was not served", args)
	}
	return reply
}

func TestWorkerListSendsContractAndAppendsScope(t *testing.T) {
	client, seen := serve(t, map[string]string{"ORCA_TERMINAL_HANDLE": "term_1", "ORCA_AGENT_SESSION_ID": "s1"}, func(got frame) []string {
		return []string{`{"_keepalive":true}`, ok(got.ID, `{"workers":[],"page":{"hasMore":false}}`)}
	})
	reply := served(t, client, "orchestration", "worker-list", "--run", "run_1", "--include-remote", "--limit", "100", "--full", "--json")
	var printed struct {
		Result json.RawMessage `json:"result"`
	}
	if err := json.Unmarshal(reply.Stdout, &printed); err != nil {
		t.Fatal(err)
	}
	var compact bytes.Buffer
	if err := json.Compact(&compact, printed.Result); err != nil {
		t.Fatal(err)
	}
	if got, want := compact.String(), `{"workers":[],"page":{"hasMore":false},"scope":{"run":"run_1","source":"flag"}}`; got != want {
		t.Fatalf("result = %s, want %s", got, want)
	}
	sent := seen.all()[0]
	if sent.AuthToken != "tok" || sent.Method != "orchestration.workerList" || sent.ContractVersion != 1 || sent.CompatibilityID == "" {
		t.Fatalf("envelope = %+v", sent)
	}
	if sent.CompatibilityEvidence["terminalHandle"] != "term_1" || sent.CompatibilityEvidence["agentSessionId"] != "s1" {
		t.Fatalf("evidence = %v", sent.CompatibilityEvidence)
	}
	if got, want := string(sent.Params), `{"paginate":true,"run":"run_1","includeRemote":true,"limit":100}`; got != want {
		t.Fatalf("params = %s, want %s", got, want)
	}
}

func TestServeLeavesEverythingElseToTheCLI(t *testing.T) {
	client, seen := serve(t, nil, answering(`{}`))
	for _, args := range [][]string{
		{"orchestration", "worker-start", "--run", "r", "--json"},
		{"orchestration", "worker-show", "--dispatch", "d", "--json"},
		{"orchestration", "worker-show", "--dispatch", "d", "--full"},
		{"orchestration", "worker-show", "--dispatch", "d", "--run", "r", "--full", "--json"},
		{"orchestration", "worker-list", "--full", "--json"},
		{"orchestration", "check", "--terminal", "t", "--full", "--json"},
		{"orchestration", "check", "--terminal", "t", "--peek", "--all", "--full", "--json"},
		{"orchestration", "check", "--terminal", "t", "--peek", "--ack", "d", "--full", "--json"},
		{"orchestration", "check", "--run", "r", "--terminal", "", "--full", "--json"},
		{"orchestration", "inbox", "--terminal", "", "--full", "--json"},
		{"orchestration", "check", "--terminal", "t", "--peek", "--wait", "--full", "--json"},
		{"terminal", "read", "--terminal", "t", "--environment", "box", "--json"},
		{"terminal", "read", "--json"},
		{"terminal", "read", "--terminal", "t", "--screen", "--cursor", "1", "--json"},
		{"terminal", "wait", "--terminal", "t", "--for", "tui-idle", "--json"},
		{"terminal", "send", "--terminal", "t", "--text", "x", "--json"},
		{"terminal", "list", "--worktree", "active", "--json"},
		{"terminal", "list", "--limit", "0", "--json"},
		{"terminal", "list", "--limit=1", "--json"},
		{"worktree", "show", "--worktree", "current", "--json"},
	} {
		if _, handled := client.Serve(context.Background(), args); handled {
			t.Errorf("%v was served", args)
		}
	}
	if sent := seen.all(); len(sent) != 0 {
		t.Fatalf("sent %d frames", len(sent))
	}
}

func TestServeRefusals(t *testing.T) {
	refusing := func(code string) func(frame) []string {
		return func(got frame) []string {
			return []string{fmt.Sprintf(`{"id":%q,"ok":false,"error":{"code":%q,"message":"no"},"_meta":{"runtimeId":"rt"}}`, got.ID, code)}
		}
	}
	args := []string{"orchestration", "worker-show", "--dispatch", "d", "--full", "--json"}

	client, _ := serve(t, nil, refusing("invalid_argument"))
	if _, handled := client.Serve(context.Background(), args); handled {
		t.Fatal("a refusal the CLI adds to was served")
	}

	client, _ = serve(t, nil, refusing("remote_runtime_unavailable"))
	reply := served(t, client, args...)
	var printed struct {
		OK    bool     `json:"ok"`
		Error RPCError `json:"error"`
	}
	if err := json.Unmarshal(reply.Stdout, &printed); err != nil {
		t.Fatal(err)
	}
	if reply.Code != 1 || printed.OK || printed.Error.Code != "remote_runtime_unavailable" {
		t.Fatalf("code = %d, printed = %+v", reply.Code, printed)
	}
}

func TestCallRefusesWriteMethods(t *testing.T) {
	client, seen := serve(t, nil, answering(`{}`))
	conn, err := client.Dial(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = conn.Close() }()
	for method, params := range map[string]any{
		"orchestration.workerStart": nil,
		checkMethod:                 CheckParams{Terminal: "term_1"},
	} {
		if _, err := conn.Call(context.Background(), method, params); !errors.Is(err, ErrWriteMethod) {
			t.Fatalf("%s: err = %v", method, err)
		}
	}
	if _, err := conn.Call(context.Background(), checkMethod, map[string]any{"peek": true, "ack": "d"}); !errors.Is(err, ErrWriteMethod) {
		t.Fatalf("untyped check: err = %v", err)
	}
	if sent := seen.all(); len(sent) != 0 {
		t.Fatalf("sent %d frames", len(sent))
	}
}

func TestCheckPeekUsesCallerEnvironment(t *testing.T) {
	client, seen := serve(t, map[string]string{"ORCA_TERMINAL_HANDLE": "term_env", "ORCA_PANE_KEY": "pane"}, answering(`{"messages":[]}`))
	served(t, client, "orchestration", "check", "--run", "run_1", "--peek", "--full", "--json")
	want := `{"terminal":"term_env","terminalPaneKey":"pane","unread":false,"peek":true,"compatibilityCliCommand":"` + client.cliCommand() + `","run":"run_1"}`
	if got := string(seen.all()[0].Params); got != want {
		t.Fatalf("params = %s, want %s", got, want)
	}
}

func TestConsumingRunCheckIsDurable(t *testing.T) {
	coordinator := map[string]string{"ORCA_TERMINAL_HANDLE": "term_1", "ORCA_PANE_KEY": ""}
	for name, tc := range map[string]struct {
		args []string
		want string
	}{
		"take": {[]string{"--run", "run_1"}, `{"terminal":"term_1","compatibilityCliCommand":"%s","run":"run_1"}`},
		"ack":  {[]string{"--run", "run_1", "--ack", "d1", "--peek"}, `{"terminal":"term_1","unread":false,"peek":true,"compatibilityCliCommand":"%s","run":"run_1","ack":"d1"}`},
	} {
		t.Run(name, func(t *testing.T) {
			client, seen := serve(t, coordinator, answering(`{"runId":"run_1","messages":[],"count":0}`))
			served(t, client, slices.Concat([]string{"orchestration", "check"}, tc.args, []string{"--full", "--json"})...)
			got := seen.all()[0]
			if want := fmt.Sprintf(tc.want, client.cliCommand()); string(got.Params) != want {
				t.Errorf("params = %s, want %s", got.Params, want)
			}
			if got.RequestID == "" || got.CompatibilityID != got.RequestID {
				t.Errorf("request id = %q, compatibility id = %q", got.RequestID, got.CompatibilityID)
			}
		})
	}
}

func TestReadsCarryNoRequestID(t *testing.T) {
	client, seen := serve(t, map[string]string{"ORCA_TERMINAL_HANDLE": "term_1"}, answering(`{"messages":[],"count":0}`))
	served(t, client, "orchestration", "check", "--run", "run_1", "--peek", "--full", "--json")
	served(t, client, "orchestration", "inbox", "--terminal", "run:run_1", "--limit", "100", "--full", "--json")
	frames := seen.all()
	if frames[0].RequestID != "" || frames[1].RequestID != "" {
		t.Fatalf("request ids = %q, %q", frames[0].RequestID, frames[1].RequestID)
	}
	if want := `{"limit":100,"terminal":"run:run_1"}`; string(frames[1].Params) != want {
		t.Fatalf("inbox params = %s, want %s", frames[1].Params, want)
	}
}

func TestConsumingCheckNeverRunsAFreshCLIAfterSending(t *testing.T) {
	refused := func(got frame) []string {
		return []string{fmt.Sprintf(`{"id":%q,"ok":false,"error":{"code":"consumer_fenced","message":"no"},"_meta":{"runtimeId":"rt"}}`, got.ID)}
	}
	legacy := func(got frame) []string {
		return []string{ok(got.ID, `{"messages":[],"count":0,"legacyCompatibility":{"ackMessageIds":["m1"]}}`)}
	}
	lost := func(frame) []string { return []string{"not json"} }
	for name, answer := range map[string]func(frame) []string{"refused": refused, "only the CLI can finish": legacy, "reply lost": lost} {
		t.Run(name, func(t *testing.T) {
			client, seen := serve(t, map[string]string{"ORCA_TERMINAL_HANDLE": "term_1"}, answer)
			reply, handled := client.Serve(context.Background(), []string{"orchestration", "check", "--run", "run_1", "--full", "--json"})
			if handled {
				t.Fatalf("served %s", reply.Stdout)
			}
			if sent := seen.all()[0].RequestID; sent == "" || reply.Retry != sent {
				t.Fatalf("retry = %q, sent %q", reply.Retry, sent)
			}
		})
	}
}

func TestRunnerRetriesAConsumingCheckByRequestID(t *testing.T) {
	client, seen := serve(t, nil, func(frame) []string { return []string{"not json"} })
	next := &fakeNext{}
	runner := Runner{Next: next}
	t.Setenv("ORCA_USER_DATA_PATH", client.UserData)
	argv := []string{"orca", "orchestration", "check", "--run", "run_1", "--full", "--json"}
	if _, err := runner.Run(context.Background(), shell.Command{Argv: argv, Env: []string{"ORCA_TERMINAL_HANDLE=term_1"}}); err != nil {
		t.Fatal(err)
	}
	want := append(slices.Clone(argv), "--retry-request", seen.all()[0].RequestID)
	if got := next.calls[0].Argv; !slices.Equal(got, want) {
		t.Fatalf("argv = %v, want %v", got, want)
	}
}

func TestCheckFromAnotherSessionGoesToTheCLI(t *testing.T) {
	client, seen := serve(t, map[string]string{"ORCA_AGENT_SESSION_ID": "s1"}, answering(`{"messages":[]}`))
	if _, handled := client.Serve(context.Background(), []string{"orchestration", "check", "--terminal", "term_other", "--peek", "--full", "--json"}); handled {
		t.Fatal("served another caller's mailbox")
	}
	if sent := seen.all(); len(sent) != 0 {
		t.Fatalf("sent %d frames", len(sent))
	}
}

func TestCheckResultsOnlyTheCLICanFinish(t *testing.T) {
	for name, result := range map[string]string{
		"read row":   `{"messages":[{"read":1}]}`,
		"legacy ack": `{"messages":[],"legacyCompatibility":{"ackMessageIds":["m"]}}`,
	} {
		t.Run(name, func(t *testing.T) {
			client, _ := serve(t, nil, answering(result))
			if _, handled := client.Serve(context.Background(), []string{"orchestration", "check", "--terminal", "t", "--peek", "--full", "--json"}); handled {
				t.Fatal("served")
			}
		})
	}
}

func TestTerminalWaitUnsatisfiedExitsOne(t *testing.T) {
	client, _ := serve(t, nil, answering(`{"wait":{"satisfied":false}}`))
	if reply := served(t, client, "terminal", "wait", "--terminal", "t", "--for", "tui-idle", "--timeout-ms", "1000", "--json"); reply.Code != 1 {
		t.Fatalf("code = %d", reply.Code)
	}
}

func TestTerminalScreenWithoutSourceGoesToTheCLI(t *testing.T) {
	client, _ := serve(t, nil, answering(`{"terminal":{"tail":[]}}`))
	if _, handled := client.Serve(context.Background(), []string{"terminal", "read", "--terminal", "t", "--screen", "--json"}); handled {
		t.Fatal("served a stream as a screen")
	}
}

func TestHostScopeNamesPairedEnvironments(t *testing.T) {
	client, _ := serve(t, nil, answering(`{"terminals":[],"hostScope":{"hostIds":["local"],"omittedHostIds":["runtime:a","runtime:b","ssh:c"]},"totalCount":0}`))
	store := `{"version":1,"environments":[{"id":"a","name":"box"}]}`
	if err := os.WriteFile(filepath.Join(client.UserData, "orca-environments.json"), []byte(store), 0o600); err != nil {
		t.Fatal(err)
	}
	reply := served(t, client, "terminal", "list", "--json")
	var compact bytes.Buffer
	if err := json.Compact(&compact, reply.Stdout); err != nil {
		t.Fatal(err)
	}
	want := `"hostScope":{"hostIds":["local"],"omittedHostIds":["runtime:a","runtime:b","ssh:c"],"omittedHostSelectors":[{"hostId":"runtime:a","selector":"--environment box"},{"hostId":"runtime:b","selector":null},{"hostId":"ssh:c","selector":null}]},"totalCount":0`
	if !strings.Contains(compact.String(), want) {
		t.Fatalf("got %s", compact.String())
	}
}

func TestDialRefusesSelectedRemote(t *testing.T) {
	client, _ := serve(t, map[string]string{"ORCA_ENVIRONMENT": "box"}, func(frame) []string { return nil })
	if _, err := client.Dial(context.Background()); !errors.Is(err, ErrRemoteSelected) {
		t.Fatalf("err = %v", err)
	}
}

func TestServeRejectsMismatchedFrames(t *testing.T) {
	for name, answer := range map[string]func(frame) []string{
		"id": func(frame) []string { return []string{ok("other", `{}`)} },
		"runtime": func(got frame) []string {
			return []string{fmt.Sprintf(`{"id":%q,"ok":true,"result":{},"_meta":{"runtimeId":"new"}}`, got.ID)}
		},
	} {
		t.Run(name, func(t *testing.T) {
			client, _ := serve(t, nil, answer)
			if _, handled := client.Serve(context.Background(), []string{"terminal", "show", "--terminal", "term_1", "--json"}); handled {
				t.Fatal("served")
			}
		})
	}
}

func TestSourceFence(t *testing.T) {
	status := func(installation string) func(frame) []string {
		return func(got frame) []string {
			if got.Method == "status.get" {
				return []string{ok(got.ID, fmt.Sprintf(`{"hostDescriptor":{"installationId":%q}}`, installation))}
			}
			return []string{ok(got.ID, `{}`)}
		}
	}
	stamp := map[string]string{"ORCA_RUNTIME_SOURCE_ID": "install", "ORCA_RUNTIME_SOURCE_INCARNATION": "older"}

	client, seen := serve(t, stamp, status("install"))
	served(t, client, "terminal", "show", "--terminal", "term_1", "--json")
	sent := seen.all()
	last := sent[len(sent)-1]
	if sent[0].Method != "status.get" || last.ExpectedRuntimeSource == nil || *last.ExpectedRuntimeSource != (expectedSource{SourceID: "install", RuntimeID: "rt"}) {
		t.Fatalf("frames = %+v", sent)
	}

	client, _ = serve(t, stamp, status("someone-else"))
	if _, handled := client.Serve(context.Background(), []string{"terminal", "show", "--terminal", "term_1", "--json"}); handled {
		t.Fatal("served through a foreign runtime")
	}
}

func TestGateAdmitsOnlyAVerifiedCompatibleRuntime(t *testing.T) {
	state := t.TempDir()
	t.Setenv("XDG_STATE_HOME", state)
	dir := filepath.Join(state, "orca-rpc")
	if err := os.MkdirAll(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	good := `{"appVersion":"1.0.0","capabilities":["orchestration.contract.v1"],"runtimeProtocolVersion":3,"minCompatibleRuntimeClientVersion":2}`
	for name, tc := range map[string]struct {
		status   string
		verified string
		failed   string
		admitted bool
		spawned  bool
	}{
		"verified":        {status: good, verified: "1.0.0", admitted: true},
		"new version":     {status: good, verified: "0.9.0", spawned: true},
		"parity failed":   {status: good, failed: "1.0.0"},
		"no capability":   {status: `{"appVersion":"1.0.0","capabilities":[],"runtimeProtocolVersion":3,"minCompatibleRuntimeClientVersion":2}`, verified: "1.0.0"},
		"client too old":  {status: `{"appVersion":"1.0.0","capabilities":["orchestration.contract.v1"],"runtimeProtocolVersion":4,"minCompatibleRuntimeClientVersion":4}`, verified: "1.0.0"},
		"runtime too old": {status: `{"appVersion":"1.0.0","capabilities":["orchestration.contract.v1"],"runtimeProtocolVersion":2,"minCompatibleRuntimeClientVersion":2}`, verified: "1.0.0"},
	} {
		t.Run(name, func(t *testing.T) {
			_ = os.Remove(filepath.Join(dir, verifiedFile))
			_ = os.Remove(filepath.Join(dir, "1.0.0"+failedSuffix))
			if tc.verified != "" {
				if err := os.WriteFile(filepath.Join(dir, verifiedFile), []byte(`{"version":"`+tc.verified+`"}`), 0o600); err != nil {
					t.Fatal(err)
				}
			}
			if tc.failed != "" {
				if err := os.WriteFile(filepath.Join(dir, tc.failed+failedSuffix), nil, 0o600); err != nil {
					t.Fatal(err)
				}
			}
			client, _ := serve(t, nil, func(got frame) []string {
				if got.Method == "status.get" {
					return []string{ok(got.ID, tc.status)}
				}
				return []string{ok(got.ID, `{}`)}
			})
			spawned := false
			client.Gate = &Gate{Spawn: func() { spawned = true }}
			_, handled := client.Serve(context.Background(), []string{"terminal", "show", "--terminal", "t", "--json"})
			if handled != tc.admitted || spawned != tc.spawned {
				t.Fatalf("handled = %v, spawned = %v", handled, spawned)
			}
		})
	}
}

type fakeNext struct {
	calls []shell.Command
}

func (f *fakeNext) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	f.calls = append(f.calls, c)
	return shell.Result{Stdout: "real"}, nil
}

func TestRunnerServesReadsAndPassesTheRest(t *testing.T) {
	client, _ := serve(t, nil, answering(`{"terminal":{}}`))
	t.Setenv("ORCA_USER_DATA_PATH", client.UserData)
	t.Setenv("ORCA_ENVIRONMENT", "")
	next := &fakeNext{}
	runner := Runner{Next: next}
	result, err := runner.Run(context.Background(), shell.Command{Argv: []string{"orca", "terminal", "show", "--terminal", "t", "--json"}})
	if err != nil || !strings.Contains(result.Stdout, `"terminal"`) || len(next.calls) != 0 {
		t.Fatalf("result = %+v, err = %v, calls = %d", result, err, len(next.calls))
	}
	for _, argv := range [][]string{
		{"orca", "terminal", "send", "--terminal", "t", "--text", "x", "--json"},
		{"git", "status"},
	} {
		if result, err := runner.Run(context.Background(), shell.Command{Argv: argv}); err != nil || result.Stdout != "real" {
			t.Fatalf("%v: result = %+v, err = %v", argv, result, err)
		}
	}
	if len(next.calls) != 2 {
		t.Fatalf("calls = %d", len(next.calls))
	}
}

func TestRunnerReadsCommandEnvironment(t *testing.T) {
	getenv := overlaid([]string{"A=1", "A=2"})
	if got := getenv("A"); got != "2" {
		t.Fatalf("A = %q", got)
	}
}

func TestParityComparesShape(t *testing.T) {
	same := sameShape([]string{"x"}, []byte(`{"a":{"b":1},"c":[{"d":1}]}`), []byte(`{"c":[{"d":2},{"d":3}],"a":{"b":9}}`))
	if same != nil {
		t.Fatal(same)
	}
	differs := sameShape([]string{"x"}, []byte(`{"a":1}`), []byte(`{"a":1,"b":2}`))
	if !errors.Is(differs, errParityDiffers) {
		t.Fatalf("err = %v", differs)
	}
}

func TestRenderMatchesCLIShape(t *testing.T) {
	out, err := Render(Response{ID: "a", OK: true, Result: json.RawMessage(`{"s":"<&>","e":[],"o":{}}`), Meta: json.RawMessage(`{"runtimeId":"rt"}`)})
	if err != nil {
		t.Fatal(err)
	}
	want := "{\n  \"id\": \"a\",\n  \"ok\": true,\n  \"result\": {\n    \"s\": \"<&>\",\n    \"e\": [],\n    \"o\": {}\n  },\n  \"_meta\": {\n    \"runtimeId\": \"rt\"\n  }\n}\n"
	if string(out) != want {
		t.Fatalf("got %s", out)
	}
}

type fakeHost struct {
	key       [boxKeyBytes]byte
	runtimeID string
	token     string
	answer    func(remoteRequest) string
}

func (h *fakeHost) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	socket, err := websocket.Accept(w, r, nil)
	if err != nil {
		return
	}
	defer func() { _ = socket.CloseNow() }()
	ctx := r.Context()
	_, raw, err := socket.Read(ctx)
	if err != nil {
		return
	}
	var hello struct {
		Public string `json:"publicKeyB64"`
	}
	if json.Unmarshal(raw, &hello) != nil {
		return
	}
	peer, err := base64.StdEncoding.DecodeString(hello.Public)
	if err != nil {
		return
	}
	channel := remoteConn{socket: socket}
	box.Precompute(&channel.key, (*[boxKeyBytes]byte)(peer), &h.key)
	if socket.Write(ctx, websocket.MessageText, []byte(`{"type":"e2ee_ready"}`)) != nil {
		return
	}
	opened, err := channel.readSealed(ctx)
	if err != nil {
		return
	}
	var auth struct {
		Token string `json:"deviceToken"`
	}
	if json.Unmarshal(opened, &auth) != nil || auth.Token != h.token {
		_ = channel.writeSealed(ctx, []byte(`{"type":"e2ee_error","errorCode":"unauthorized"}`))
		return
	}
	if channel.writeSealed(ctx, []byte(`{"type":"e2ee_authenticated"}`)) != nil {
		return
	}
	for {
		opened, err := channel.readSealed(ctx)
		if err != nil {
			return
		}
		var request remoteRequest
		if json.Unmarshal(opened, &request) != nil {
			return
		}
		result := h.answer(request)
		if request.Method == "status.get" {
			result = fmt.Sprintf(`{"runtimeId":%q,"runtimeProtocolVersion":3,"minCompatibleRuntimeClientVersion":2}`, h.runtimeID)
		}
		if channel.writeSealed(ctx, []byte(`{"_keepalive":true}`)) != nil {
			return
		}
		frame := fmt.Sprintf(`{"id":%q,"ok":true,"result":%s,"_meta":{"runtimeId":%q}}`, request.ID, result, h.runtimeID)
		if strings.HasPrefix(result, `{"id"`) {
			frame = strings.Replace(result, "REQUEST", request.ID, 1)
		}
		if channel.writeSealed(ctx, []byte(frame)) != nil {
			return
		}
	}
}

func pairedClient(t *testing.T, storedRuntime, deviceToken string, answer func(remoteRequest) string) *Client {
	t.Helper()
	public, secret, err := box.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	server := httptest.NewServer(&fakeHost{key: *secret, runtimeID: "rt-remote", token: "device", answer: answer})
	t.Cleanup(server.Close)
	dir := t.TempDir()
	store, err := json.Marshal(map[string]any{"environments": []storedEnvironment{{
		ID: "env-id", Name: "box", RuntimeID: storedRuntime, Preferred: "ws", Endpoints: []storedEndpoint{{
			ID: "ws", Kind: "websocket", Endpoint: "ws" + strings.TrimPrefix(server.URL, "http"), DeviceToken: deviceToken, PublicKey: base64.StdEncoding.EncodeToString(public[:]),
		}},
	}}})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, environmentStore), store, 0o600); err != nil {
		t.Fatal(err)
	}
	return &Client{UserData: dir, Getenv: func(string) string { return "" }, Timeout: 5 * time.Second}
}

func TestRemoteReadGoesOverTheSealedChannel(t *testing.T) {
	var mu sync.Mutex
	var sent []remoteRequest
	client := pairedClient(t, "rt-remote", "device", func(request remoteRequest) string {
		mu.Lock()
		defer mu.Unlock()
		sent = append(sent, request)
		return `{"terminal":{"source":"screen"}}`
	})
	reply := served(t, client, "terminal", "read", "--terminal", "t", "--screen", "--environment", "box", "--json")
	if reply.Code != 0 || !strings.Contains(string(reply.Stdout), `"source": "screen"`) {
		t.Fatalf("reply = %+v", reply)
	}
	mu.Lock()
	defer mu.Unlock()
	last := sent[len(sent)-1]
	if sent[0].Method != "status.get" || last.Method != "terminal.read" || last.DeviceToken != "device" {
		t.Fatalf("sent = %+v", sent)
	}
}

func TestRemoteLeavesToTheCLI(t *testing.T) {
	answer := func(remoteRequest) string { return `{"terminal":{"source":"screen"}}` }
	args := []string{"terminal", "read", "--terminal", "t", "--screen", "--environment", "box", "--json"}
	for name, client := range map[string]*Client{
		"rejected token":  pairedClient(t, "rt-remote", "wrong", answer),
		"changed runtime": pairedClient(t, "rt-older", "device", answer),
	} {
		t.Run(name, func(t *testing.T) {
			if _, handled := client.Serve(context.Background(), args); handled {
				t.Fatal("served")
			}
		})
	}
	client := pairedClient(t, "rt-remote", "device", answer)
	for _, other := range [][]string{
		{"terminal", "read", "--terminal", "t", "--environment", "unknown", "--json"},
		{"orchestration", "worker-show", "--dispatch", "d", "--environment", "box", "--full", "--json"},
		{"status", "--environment", "box", "--json"},
	} {
		if _, handled := client.Serve(context.Background(), other); handled {
			t.Errorf("%v was served", other)
		}
	}
}

func TestRemoteRefusals(t *testing.T) {
	refusing := func(code string) func(remoteRequest) string {
		return func(remoteRequest) string {
			return fmt.Sprintf(`{"id":"REQUEST","ok":false,"error":{"code":%q,"message":"no"},"_meta":{"runtimeId":"rt-remote"}}`, code)
		}
	}
	args := []string{"terminal", "show", "--terminal", "t", "--environment", "box", "--json"}
	reply := served(t, pairedClient(t, "rt-remote", "device", refusing("terminal_handle_stale")), args...)
	if reply.Code != 1 || !strings.Contains(string(reply.Stdout), `"code": "terminal_handle_stale"`) || strings.Contains(string(reply.Stdout), "nextSteps") {
		t.Fatalf("reply = %s", reply.Stdout)
	}
	if _, handled := pairedClient(t, "rt-remote", "device", refusing("invalid_argument")).Serve(context.Background(), args); handled {
		t.Fatal("a refusal the CLI adds to was served")
	}
}

func TestLocalRefusalsCarryTheCLIRecovery(t *testing.T) {
	refusing := func(code string) func(frame) []string {
		return func(got frame) []string {
			return []string{fmt.Sprintf(`{"id":%q,"ok":false,"error":{"code":%q,"message":%q},"_meta":{"runtimeId":"rt"}}`, got.ID, code, code)}
		}
	}
	for name, tc := range map[string]struct {
		code string
		args []string
		want string
	}{
		"stale terminal": {"terminal_handle_stale", []string{"terminal", "show", "--terminal", "t", "--json"}, `"data":{"nextSteps":["` + staleHandleHint + `"]}`},
		"missing worktree": {
			"selector_not_found",
			[]string{"worktree", "show", "--worktree", "path:/x", "--json"},
			`"data":{"selector":"path:/x","validSelectorForms":["id:<repo-id>::<absolute-path>","path:<absolute-path>","name:<display-name>","branch:<branch>","identity:<identity-key>","issue:<number>","current","active"],"suggestions":[],"nextSteps":["No Orca workspace matched the worktree selector \"path:/x\".","Valid selector forms: id:<repo-id>::<absolute-path>, path:<absolute-path>, name:<display-name>, branch:<branch>, identity:<identity-key>, issue:<number>, current, active.","List the exact values with ` + "`orca worktree list --json`" + `; a bare repository id is not a worktree id."]}`,
		},
		"wait timeout": {"timeout", []string{"terminal", "wait", "--terminal", "t", "--for", "tui-idle", "--timeout-ms", "1", "--json"}, `"error":{"code":"timeout","message":"timeout"},"_meta"`},
	} {
		t.Run(name, func(t *testing.T) {
			client, _ := serve(t, nil, refusing(tc.code))
			reply := served(t, client, tc.args...)
			var compact bytes.Buffer
			if err := json.Compact(&compact, reply.Stdout); err != nil {
				t.Fatal(err)
			}
			if reply.Code != 1 || !strings.Contains(compact.String(), tc.want) {
				t.Fatalf("code = %d, got %s, want %s", reply.Code, compact.String(), tc.want)
			}
		})
	}
}

func TestSelectorSuggestions(t *testing.T) {
	for selector, want := range map[string][]string{
		"id:repo":     {"id:repo::<absolute-path>", "path:<absolute-path>"},
		"id:repo::/x": {},
		"branch:main": {},
		"/x":          {"path:/x"},
		`C:\\x`:       {`path:C:\\x`},
		"thing":       {"id:thing::<absolute-path>", "name:thing", "branch:thing"},
	} {
		if got := suggestSelectorForms(selector); !slices.Equal(got, want) {
			t.Errorf("%s: got %v, want %v", selector, got, want)
		}
	}
}

func TestEnvironmentListRedactsSecrets(t *testing.T) {
	client, _ := serve(t, nil, answering(`{}`))
	store := `{"version":1,"environments":[{"id":"a","name":"box","endpoints":[{"id":"ws","deviceToken":"secret","kind":"websocket","publicKeyB64":"key"}],"lastUsedAt":null}]}`
	if err := os.WriteFile(filepath.Join(client.UserData, environmentStore), []byte(store), 0o600); err != nil {
		t.Fatal(err)
	}
	reply := served(t, client, "environment", "list", "--json")
	var compact bytes.Buffer
	if err := json.Compact(&compact, reply.Stdout); err != nil {
		t.Fatal(err)
	}
	want := `"result":{"environments":[{"id":"a","name":"box","endpoints":[{"id":"ws","kind":"websocket"}],"lastUsedAt":null}]},"_meta":{"runtimeId":"local"}`
	if !strings.Contains(compact.String(), want) || !strings.HasPrefix(compact.String(), `{"id":"local"`) {
		t.Fatalf("got %s", compact.String())
	}
	if err := os.WriteFile(filepath.Join(client.UserData, environmentSidecar), []byte(`{"version":1,"entries":{"a":{}}}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, handled := client.Serve(context.Background(), []string{"environment", "list", "--json"}); handled {
		t.Fatal("served without the sidecar's state")
	}
}

func TestDownHostAnswersLikeTheCLI(t *testing.T) {
	client := pairedClient(t, "rt-remote", "device", nil)
	environments, err := client.environments()
	if err != nil {
		t.Fatal(err)
	}
	environments[0].Endpoints[0].Endpoint = "ws://127.0.0.1:1"
	store, err := json.Marshal(map[string]any{"environments": environments})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(client.UserData, environmentStore), store, 0o600); err != nil {
		t.Fatal(err)
	}
	reply := served(t, client, "terminal", "show", "--terminal", "t", "--environment", "box", "--json")
	var printed struct {
		ID    string   `json:"id"`
		Error RPCError `json:"error"`
	}
	if err := json.Unmarshal(reply.Stdout, &printed); err != nil {
		t.Fatal(err)
	}
	if reply.Code != 1 || printed.ID != "local" || printed.Error.Code != "remote_runtime_unavailable" || printed.Error.Message != connectFailure+"." {
		t.Fatalf("reply = %s", reply.Stdout)
	}
}
