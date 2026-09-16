package main

import (
	"bytes"
	"context"
	"encoding/json"
	"path/filepath"
	"strings"
	"testing"

	"github.com/spf13/cobra"

	"github.com/yasyf/cc-interact/cmd"
	"github.com/yasyf/cc-interact/daemon"
	"github.com/yasyf/cc-interact/event"
	"github.com/yasyf/cc-interact/store"
)

const (
	pendingDirectiveSQL = "SELECT EXISTS(SELECT 1 FROM directives JOIN subjects ON subjects.id = directives.subject_id WHERE directives.agent_id = ? AND directives.delivered_at IS NULL AND subjects.scope = ?)"
	subjectInScopeSQL   = "SELECT EXISTS(SELECT 1 FROM subjects WHERE scope = ?)"
)

type planeProbe struct {
	t   *testing.T
	srv *daemon.Server
}

func (p planeProbe) holds(query string, args ...any) bool {
	p.t.Helper()
	var matched bool
	if err := p.srv.DB().QueryRowContext(context.Background(), query, args...).Scan(&matched); err != nil {
		p.t.Fatalf("%s: %v", query, err)
	}
	return matched
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

func (p planeProbe) inject(session string, claudePID int, agentID, scope string) string {
	p.t.Helper()
	pending := p.holds(pendingDirectiveSQL, agentID, scope)
	out := p.run(cmd.AgentInjectCmd, claudePID, map[string]string{"session_id": session, "cwd": scope, "agent_id": agentID})
	if out != "" && !pending {
		p.t.Fatalf("agent-inject for %q in %q injected %q, but the hook precondition would have skipped it", agentID, scope, out)
	}
	return out
}

func (p planeProbe) stop(session string, claudePID int, agentID, scope string) string {
	p.t.Helper()
	inScope := p.holds(subjectInScopeSQL, scope)
	out := p.run(cmd.AgentStopCmd, claudePID, map[string]string{"session_id": session, "cwd": scope, "agent_id": agentID})
	if out != "" && !inScope {
		p.t.Fatalf("agent-stop for %q in %q decided %q, but the hook precondition would have skipped it", agentID, scope, out)
	}
	return out
}

func TestHookPreconditionsAdmitEveryPlaneReply(t *testing.T) {
	home := shortHome(t)
	srv := serveInProcess(t)
	scope, elsewhere := canonicalScope(t), canonicalScope(t)
	p := planeProbe{t: t, srv: srv}

	var seq int
	var name, file string
	if err := srv.DB().QueryRowContext(context.Background(), "PRAGMA database_list").Scan(&seq, &name, &file); err != nil {
		t.Fatal(err)
	}
	if want := filepath.Join(home, appDir, "cc-interact-v1", "state.db"); file != want {
		t.Fatalf("daemon store = %q, want the path common.state_db() reads, %q", file, want)
	}

	const session = "sess-plane"
	if p.holds(subjectInScopeSQL, scope) || p.inject(session, 0, "", scope) != "" || p.stop(session, 0, "agent-1", scope) != "" {
		t.Fatal("an empty store matched or replied")
	}

	sub, err := store.NewSubjectStore(srv.DB()).
		Create(context.Background(), "abcdef0123456789abcdef0123456789", "codex-plane", session, scope, 4242, statusOpen)
	if err != nil {
		t.Fatalf("seed subject: %v", err)
	}
	if !p.holds(subjectInScopeSQL, scope) || p.holds(subjectInScopeSQL, elsewhere) {
		t.Fatal("subject scope match is wrong")
	}
	if out := p.inject(session, 0, "", scope); out != "" {
		t.Fatalf("empty mailbox injected %q", out)
	}

	registerOwner(t, session, scope, "agent-1")
	if !p.holds(pendingDirectiveSQL, "agent-1", scope) {
		t.Fatal("the greeting directive is not pending")
	}
	if p.holds(pendingDirectiveSQL, "agent-1", elsewhere) || p.holds(pendingDirectiveSQL, "agent-2", scope) || p.holds(pendingDirectiveSQL, "", scope) {
		t.Fatal("pending match leaked across agent or scope")
	}
	if out := p.inject("sess-rotated", 4242, "agent-1", scope); !strings.Contains(out, "You are agent agent-1") {
		t.Fatalf("agent-inject through the claude-pid fallback = %q, want the greeting", out)
	}
	if p.holds(pendingDirectiveSQL, "agent-1", scope) || p.inject(session, 0, "agent-1", scope) != "" {
		t.Fatal("a drained mailbox still matches or injects")
	}

	if _, err := srv.Direct(context.Background(), sub.ID, "", event.OriginHuman, "steer the top level"); err != nil {
		t.Fatal(err)
	}
	if p.inject(session, 0, "", elsewhere) != "" {
		t.Fatal("a top-level directive injected outside its scope")
	}
	if out := p.inject(session, 0, "", scope); !strings.Contains(out, "steer the top level") {
		t.Fatalf("top-level agent-inject = %q, want the directive", out)
	}

	if _, err := srv.Direct(context.Background(), sub.ID, "agent-1", event.OriginHuman, "one more thing"); err != nil {
		t.Fatal(err)
	}
	if p.stop(session, 0, "agent-1", elsewhere) != "" {
		t.Fatal("agent-stop blocked outside the subject's scope")
	}
	if out := p.stop(session, 0, "agent-1", scope); !strings.Contains(out, "one more thing") {
		t.Fatalf("agent-stop = %q, want a block carrying the directive", out)
	}
}
