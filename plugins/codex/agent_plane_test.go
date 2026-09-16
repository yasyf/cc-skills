package main

import (
	"bytes"
	"context"
	"encoding/json"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/spf13/cobra"

	"github.com/yasyf/cc-interact/cmd"
	"github.com/yasyf/cc-interact/daemon"
	"github.com/yasyf/cc-interact/event"
	"github.com/yasyf/cc-interact/store"
	"github.com/yasyf/daemonkit/paths"
)

const hookGateProbe = `
import importlib.util, json, sys, types
sys.modules["captain_hook"] = types.SimpleNamespace(BaseHookEvent=object)
spec = importlib.util.spec_from_file_location("common", "capt-hook/hooks/common.py")
common = importlib.util.module_from_spec(spec)
spec.loader.exec_module(common)
evt = types.SimpleNamespace(_raw=json.load(sys.stdin))
json.dump({
    "state_db": str(common.state_db()),
    "daemon_socket": str(common.daemon_socket()),
    "directive_pending": common.directive_pending(evt),
    "subject_in_scope": common.subject_in_scope(evt),
}, sys.stdout)
`

type hookGate struct {
	StateDB          string `json:"state_db"`
	DaemonSocket     string `json:"daemon_socket"`
	DirectivePending bool   `json:"directive_pending"`
	SubjectInScope   bool   `json:"subject_in_scope"`
}

type planeProbe struct {
	t   *testing.T
	srv *daemon.Server
}

func (p planeProbe) gate(raw map[string]string) hookGate {
	p.t.Helper()
	payload, _ := json.Marshal(raw)
	c := exec.Command("python3", "-c", hookGateProbe)
	c.Stdin = bytes.NewReader(payload)
	var stderr bytes.Buffer
	c.Stderr = &stderr
	out, err := c.Output()
	if err != nil {
		p.t.Fatalf("hook gate probe: %v\n%s", err, stderr.String())
	}
	var g hookGate
	if err := json.Unmarshal(out, &g); err != nil {
		p.t.Fatalf("hook gate probe output %q: %v", out, err)
	}
	return g
}

func (p planeProbe) run(sub func(cmd.Deps) *cobra.Command, claudePID int, raw map[string]string) string {
	p.t.Helper()
	d := deps()
	d.ClaudePID = func() int { return claudePID }
	c := sub(d)
	root := &cobra.Command{Use: binaryName, SilenceUsage: true, SilenceErrors: true}
	root.AddCommand(c)
	payload, _ := json.Marshal(raw)
	var out bytes.Buffer
	root.SetIn(bytes.NewReader(payload))
	root.SetOut(&out)
	root.SetArgs([]string{c.Name()})
	if err := root.Execute(); err != nil {
		p.t.Fatalf("%s: %v", c.Name(), err)
	}
	return out.String()
}

func (p planeProbe) events() int {
	p.t.Helper()
	var n int
	if err := p.srv.DB().QueryRowContext(context.Background(), "SELECT COUNT(*) FROM events").Scan(&n); err != nil {
		p.t.Fatal(err)
	}
	return n
}

func (p planeProbe) inject(session string, claudePID int, agentID, scope string) (string, hookGate) {
	p.t.Helper()
	raw := map[string]string{"session_id": session, "cwd": scope, "agent_id": agentID}
	g := p.gate(raw)
	out := p.run(cmd.AgentInjectCmd, claudePID, raw)
	if out != "" && !g.DirectivePending {
		p.t.Fatalf("agent-inject for %q in %q injected %q, but the hook gate would have skipped it", agentID, scope, out)
	}
	return out, g
}

func (p planeProbe) stop(session string, claudePID int, agentID, scope string) (string, hookGate) {
	p.t.Helper()
	raw := map[string]string{"session_id": session, "cwd": scope, "agent_id": agentID}
	g := p.gate(raw)
	out := p.run(cmd.AgentStopCmd, claudePID, raw)
	if out != "" && !g.SubjectInScope {
		p.t.Fatalf("agent-stop for %q in %q decided %q, but the hook gate would have skipped it", agentID, scope, out)
	}
	return out, g
}

func (p planeProbe) report(session, toolUseID, scope string) (bool, hookGate) {
	p.t.Helper()
	raw := map[string]string{
		"session_id": session, "cwd": scope, "tool_name": "Agent", "tool_use_id": toolUseID, "tool_response": `"done"`,
	}
	g := p.gate(raw)
	before := p.events()
	p.run(cmd.AgentReportCmd, 0, raw)
	recorded := p.events() > before
	if recorded && !g.SubjectInScope {
		p.t.Fatalf("agent-report in %q recorded an event, but the hook gate would have skipped it", scope)
	}
	return recorded, g
}

func TestHookGatesAdmitEveryPlaneReply(t *testing.T) {
	shortHome(t)
	t.Setenv("HOME", mustTempDir(t))
	srv := serveInProcess(t)
	scope, elsewhere := canonicalScope(t), canonicalScope(t)
	unnormalized := scope + string(filepath.Separator)
	p := planeProbe{t: t, srv: srv}

	var seq int
	var name, file string
	if err := srv.DB().QueryRowContext(context.Background(), "PRAGMA database_list").Scan(&seq, &name, &file); err != nil {
		t.Fatal(err)
	}
	const session = "sess-plane"
	g := p.gate(map[string]string{"session_id": session, "cwd": scope})
	if g.StateDB != file {
		t.Fatalf("hook reads store %q, daemon serves %q", g.StateDB, file)
	}
	if want := paths.Agent(codexServiceLabel).SocketPath(); g.DaemonSocket != want {
		t.Fatalf("hook dials socket %q, daemonkit resolves %q", g.DaemonSocket, want)
	}

	if out, g := p.inject(session, 0, "", scope); out != "" || g.DirectivePending || g.SubjectInScope {
		t.Fatalf("empty store: inject %q, gate %+v", out, g)
	}
	if out, _ := p.stop(session, 0, "agent-1", scope); out != "" {
		t.Fatalf("empty store: stop %q", out)
	}
	if recorded, g := p.report(session, "toolu-empty", scope); recorded || g.SubjectInScope {
		t.Fatalf("empty store: report recorded %v, gate %+v", recorded, g)
	}

	sub, err := store.NewSubjectStore(srv.DB()).
		Create(context.Background(), "abcdef0123456789abcdef0123456789", "codex-plane", session, scope, 4242, statusOpen)
	if err != nil {
		t.Fatalf("seed subject: %v", err)
	}
	if out, g := p.inject(session, 0, "", scope); out != "" || g.DirectivePending || !g.SubjectInScope {
		t.Fatalf("empty mailbox: inject %q, gate %+v", out, g)
	}
	if recorded, g := p.report(session, "toolu-elsewhere", elsewhere); recorded || g.SubjectInScope {
		t.Fatalf("report outside the subject's scope: recorded %v, gate %+v", recorded, g)
	}
	if recorded, g := p.report(session, "toolu-in-scope", scope); !recorded || !g.SubjectInScope {
		t.Fatalf("report in scope: recorded %v, gate %+v", recorded, g)
	}

	registerOwner(t, session, scope, "agent-1")
	if _, g := p.inject(session, 0, "agent-2", scope); g.DirectivePending {
		t.Fatal("pending match leaked across agents")
	}
	if out, g := p.inject(session, 0, "agent-1", unnormalized); out != "" || g.DirectivePending || g.SubjectInScope {
		t.Fatalf("unnormalized cwd %q: inject %q, gate %+v", unnormalized, out, g)
	}
	if out, g := p.inject("sess-rotated", 4242, "agent-1", scope); !strings.Contains(out, "You are agent agent-1") || !g.DirectivePending {
		t.Fatalf("claude-pid fallback: inject %q, gate %+v", out, g)
	}
	if out, g := p.inject(session, 0, "agent-1", scope); out != "" || g.DirectivePending {
		t.Fatalf("drained mailbox: inject %q, gate %+v", out, g)
	}

	if _, err := srv.Direct(context.Background(), sub.ID, "", event.OriginHuman, "steer the top level"); err != nil {
		t.Fatal(err)
	}
	if out, g := p.inject(session, 0, "", elsewhere); out != "" || g.DirectivePending {
		t.Fatalf("top-level directive outside its scope: inject %q, gate %+v", out, g)
	}
	if out, _ := p.inject(session, 0, "", scope); !strings.Contains(out, "steer the top level") {
		t.Fatalf("top-level inject = %q, want the directive", out)
	}

	if _, err := srv.Direct(context.Background(), sub.ID, "agent-1", event.OriginHuman, "one more thing"); err != nil {
		t.Fatal(err)
	}
	if out, g := p.stop(session, 0, "agent-1", elsewhere); out != "" || g.SubjectInScope {
		t.Fatalf("stop outside the subject's scope: %q, gate %+v", out, g)
	}
	if out, _ := p.stop(session, 0, "agent-1", scope); !strings.Contains(out, "one more thing") {
		t.Fatalf("stop = %q, want a block carrying the directive", out)
	}
}
