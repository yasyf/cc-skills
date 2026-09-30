package main

import (
	"slices"
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

func TestDaemonSpecOpensAsClient(t *testing.T) {
	spec, err := appSpec()
	if err != nil {
		t.Fatal(err)
	}
	if _, err := daemonkit.Open(spec); err != nil {
		t.Fatalf("open: %v", err)
	}
}
