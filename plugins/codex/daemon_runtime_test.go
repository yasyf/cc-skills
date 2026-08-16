package main

import (
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/yasyf/cc-interact/daemon"
	"github.com/yasyf/daemonkit"
	"github.com/yasyf/daemonkit/launchd"
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

func TestDaemonSpecOpensAsClient(t *testing.T) {
	spec, err := appSpec()
	if err != nil {
		t.Fatal(err)
	}
	if _, err := daemonkit.Open(spec); err != nil {
		t.Fatalf("open: %v", err)
	}
}

// Genuine here: spec.Program is daemonkit.Stable(), and launchd's renderEnv
// injects the ownership marker itself. Reconstructed: the staged path, built
// from daemonkit's documented rule because v0.21.4 exports no dry-run render
// (Program has no exported methods) — so ProgramArguments proves Plist echoes
// a staged path, not that Ensure stages that one.
func TestDaemonSpecPinsStableProgramAndPlistInjectsOwnerMarker(t *testing.T) {
	home := shortHome(t)
	spec, err := appSpec()
	if err != nil {
		t.Fatal(err)
	}
	stable, err := daemonkit.Stable()
	if err != nil {
		t.Fatal(err)
	}
	if spec.Program != stable {
		t.Fatalf("Program = %#v, want daemonkit.Stable() = %#v", spec.Program, stable)
	}
	if record := spec.RecordPath(); !strings.HasPrefix(record, home+string(filepath.Separator)) {
		t.Fatalf("RecordPath() = %q, want it under the isolated home %q", record, home)
	}

	if codexServiceLabel != "com.yasyf.codex-ask" {
		t.Fatalf("label = %q, want com.yasyf.codex-ask — the label the installed agent already carries", codexServiceLabel)
	}
	staged := filepath.Join(home, ".daemonkit", "bin", codexServiceLabel)
	plist, err := launchd.Agent{
		Label:         codexServiceLabel,
		Program:       staged,
		Args:          spec.Args,
		LogPath:       spec.Log,
		RestartPolicy: launchd.RestartOnFailure,
	}.Plist()
	if err != nil {
		t.Fatal(err)
	}
	wantArgs := "    <key>ProgramArguments</key>\n    <array>\n" +
		"        <string>" + staged + "</string>\n" +
		"        <string>daemon</string>\n    </array>\n"
	if !strings.Contains(string(plist), wantArgs) {
		t.Fatalf("plist ProgramArguments do not lead with %q:\n%s", staged, plist)
	}
	wantOwner := "<key>" + launchd.OwnerEnvKey + "</key>\n        <string>daemonkit</string>"
	if !strings.Contains(string(plist), wantOwner) {
		t.Fatalf("plist carries no %s=daemonkit marker:\n%s", launchd.OwnerEnvKey, plist)
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
