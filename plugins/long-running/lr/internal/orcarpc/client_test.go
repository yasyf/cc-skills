package orcarpc

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"
)

type frame struct {
	ID                    string          `json:"id"`
	AuthToken             string          `json:"authToken"`
	Method                string          `json:"method"`
	Params                json.RawMessage `json:"params"`
	ContractVersion       int             `json:"orchestrationContractVersion"`
	CompatibilityID       string          `json:"compatibilityInvocationId"`
	CompatibilityEvidence map[string]any  `json:"orchestrationCompatibilityEvidence"`
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
			_ = conn.Close()
		}
	}()
	return &Client{UserData: dir, Getenv: func(name string) string { return env[name] }, Timeout: 5 * time.Second}, seen
}

func ok(id, result string) string {
	return fmt.Sprintf(`{"id":%q,"ok":true,"result":%s,"_meta":{"runtimeId":"rt"}}`, id, result)
}

func TestWorkerListSendsContractAndAppendsScope(t *testing.T) {
	client, seen := serve(t, map[string]string{"ORCA_TERMINAL_HANDLE": "term_1", "ORCA_AGENT_SESSION_ID": "s1"}, func(got frame) []string {
		return []string{`{"_keepalive":true}`, ok(got.ID, `{"workers":[]}`)}
	})
	reply, err := client.WorkerList(context.Background(), WorkerListParams{Run: "run_1", IncludeRemote: true, Limit: 100})
	if err != nil {
		t.Fatal(err)
	}
	if got, want := string(reply.Result), `{"workers":[],"scope":{"run":"run_1","source":"flag"}}`; got != want {
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

func TestCallRefusesWriteMethods(t *testing.T) {
	client, seen := serve(t, nil, func(got frame) []string { return []string{ok(got.ID, `{}`)} })
	if _, err := client.Call(context.Background(), "orchestration.workerStart", nil); !errors.Is(err, ErrWriteMethod) {
		t.Fatalf("err = %v", err)
	}
	if sent := seen.all(); len(sent) != 0 {
		t.Fatalf("sent %d frames", len(sent))
	}
}

func TestCheckReadNeverConsumes(t *testing.T) {
	client, seen := serve(t, map[string]string{"ORCA_TERMINAL_HANDLE": "term_env", "ORCA_PANE_KEY": "pane"}, func(got frame) []string {
		return []string{ok(got.ID, `{"messages":[]}`)}
	})
	if _, err := client.Call(context.Background(), checkMethod, CheckParams{Terminal: "term_1"}); !errors.Is(err, ErrWriteMethod) {
		t.Fatalf("consuming check: err = %v", err)
	}
	if _, err := client.Call(context.Background(), checkMethod, map[string]any{"peek": true, "ack": "d"}); !errors.Is(err, ErrWriteMethod) {
		t.Fatalf("untyped check: err = %v", err)
	}
	if sent := seen.all(); len(sent) != 0 {
		t.Fatalf("sent %d frames", len(sent))
	}
	if _, err := client.CheckRead(context.Background(), CheckParams{Peek: true, Run: "run_1"}); err != nil {
		t.Fatal(err)
	}
	if got, want := string(seen.all()[0].Params), `{"terminal":"term_env","terminalPaneKey":"pane","unread":false,"peek":true,"compatibilityCliCommand":"`+client.cliCommand()+`","run":"run_1"}`; got != want {
		t.Fatalf("params = %s, want %s", got, want)
	}
}

func TestDialRefusesSelectedRemote(t *testing.T) {
	client, _ := serve(t, map[string]string{"ORCA_ENVIRONMENT": "box"}, func(frame) []string { return nil })
	if _, err := client.Dial(context.Background()); !errors.Is(err, ErrRemoteSelected) {
		t.Fatalf("err = %v", err)
	}
}

func TestCallRejectsMismatchedFrames(t *testing.T) {
	for name, answer := range map[string]func(frame) []string{
		"id": func(frame) []string { return []string{ok("other", `{}`)} },
		"runtime": func(got frame) []string {
			return []string{fmt.Sprintf(`{"id":%q,"ok":true,"result":{},"_meta":{"runtimeId":"new"}}`, got.ID)}
		},
	} {
		t.Run(name, func(t *testing.T) {
			client, _ := serve(t, nil, answer)
			if _, err := client.TerminalShow(context.Background(), "term_1"); err == nil {
				t.Fatal("no error")
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
	if _, err := client.TerminalShow(context.Background(), "term_1"); err != nil {
		t.Fatal(err)
	}
	sent := seen.all()
	last := sent[len(sent)-1]
	if sent[0].Method != "status.get" || last.ExpectedRuntimeSource == nil || *last.ExpectedRuntimeSource != (expectedSource{SourceID: "install", RuntimeID: "rt"}) {
		t.Fatalf("frames = %+v", sent)
	}

	client, _ = serve(t, stamp, status("someone-else"))
	if _, err := client.TerminalShow(context.Background(), "term_1"); err == nil || !strings.Contains(err.Error(), "runtime_source_mismatch") {
		t.Fatalf("err = %v", err)
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
