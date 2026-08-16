package main

import (
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/yasyf/cc-interact/daemon"
	"github.com/yasyf/daemonkit"
)

func TestDaemonSpecPinsExactRuntimeIdentity(t *testing.T) {
	spec, err := appSpec()
	if err != nil {
		t.Fatal(err)
	}
	if spec.Label != codexServiceLabel || spec.Restart != daemonkit.RestartOnFailure {
		t.Fatalf("daemon identity = %q, %v", spec.Label, spec.Restart)
	}
	if !slices.Equal(spec.Args, []string{"daemon"}) || spec.Log != appPaths().LogPath() {
		t.Fatalf("daemon job = %#v, %q", spec.Args, spec.Log)
	}
	if !slices.Equal(spec.Schemas, []daemonkit.Schema{daemon.WireBuild}) {
		t.Fatalf("schemas = %#v", spec.Schemas)
	}
}

func TestDaemonSpecPinsSignedControlAuthority(t *testing.T) {
	spec, err := appSpec()
	if err != nil {
		t.Fatal(err)
	}
	control := spec.Trust.Control
	if control == nil {
		t.Fatal("control lane states no requirement")
	}
	if control.TeamID != codexSigningTeamID || control.SigningIdentifier != codexSigningIdentifier {
		t.Fatalf("control requirement = %#v", *control)
	}
}

// TestDaemonSpecOpensAsClient pins the posture v0.21 refuses by default: Open
// runs ValidateForClient, so an unstated Trust.Serving fails here rather than on
// the first call.
func TestDaemonSpecOpensAsClient(t *testing.T) {
	spec, err := appSpec()
	if err != nil {
		t.Fatal(err)
	}
	if _, err := daemonkit.Open(spec); err != nil {
		t.Fatalf("open: %v", err)
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
