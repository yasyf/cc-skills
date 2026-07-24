package main

import (
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/yasyf/cc-interact/daemon"
	"github.com/yasyf/daemonkit/service"
	"github.com/yasyf/daemonkit/trust"
)

func TestDaemonRuntimeUsesExactServiceAndRoles(t *testing.T) {
	agent := appAgent()
	if agent.Label != codexServiceLabel || agent.RestartPolicy != service.RestartOnFailure {
		t.Fatalf("service identity = %q, %d", agent.Label, agent.RestartPolicy)
	}
	if !filepath.IsAbs(agent.Program) || !slices.Equal(agent.Args, []string{"daemon"}) {
		t.Fatalf("service program = %q %#v", agent.Program, agent.Args)
	}
	roles := appRoles()
	if roles != (daemon.Roles{
		Business: trust.UnprotectedRole, Lifecycle: codexLifecycleRole, StopControl: codexStopControlRole,
	}) {
		t.Fatalf("roles = %#v", roles)
	}
}

func TestDaemonRuntimePinsSignedControlAuthority(t *testing.T) {
	policy, err := appTrustPolicy()
	if err != nil {
		t.Fatal(err)
	}
	roles := appRoles()
	for _, role := range []trust.PeerRole{roles.Lifecycle, roles.StopControl} {
		requirement, ok := policy.Requirement(role)
		if !ok {
			t.Fatalf("missing requirement for %q", role)
		}
		if requirement.TeamID != codexSigningTeamID || requirement.SigningIdentifier != codexSigningIdentifier {
			t.Fatalf("requirement for %q = %#v", role, requirement)
		}
	}
	if !policy.AllowsUnprotected() || !policy.AllowsReceipt(roles.Lifecycle) ||
		!policy.AllowsReadiness(roles.Lifecycle) || !policy.AllowsStop(roles.StopControl) {
		t.Fatal("trust policy does not grant the exact runtime authorities")
	}
}

func TestReleasePinsSigningIdentifier(t *testing.T) {
	workflow := filepath.Join("..", "..", ".github", "workflows", "codex-release.yml")
	payload, err := os.ReadFile(workflow) //nolint:gosec // repository-owned release contract
	if err != nil {
		t.Fatal(err)
	}
	want := "MACOS_CODESIGN_IDENTIFIER=" + codexSigningIdentifier
	if !strings.Contains(string(payload), want) {
		t.Fatalf("release workflow does not pin %q", want)
	}
}
