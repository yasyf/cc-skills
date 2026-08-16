package main

import (
	"slices"
	"testing"

	"github.com/yasyf/cc-interact/daemon"
	"github.com/yasyf/daemonkit"
)

func TestConsumerUsesExactRuntimeWiring(t *testing.T) {
	got := launcher()
	if got.RuntimeBuild != appVersion || got.Paths != appPaths() {
		t.Fatalf("launcher identities = %q, %#v", got.RuntimeBuild, got.Paths)
	}
	if got.Daemon.Label != codexServiceLabel || got.Daemon.Restart != daemonkit.RestartOnFailure ||
		!slices.Equal(got.Daemon.Args, []string{"daemon"}) ||
		!slices.Equal(got.Daemon.Schemas, []daemonkit.Schema{daemon.WireBuild}) {
		t.Fatalf("launcher runtime = %#v", got.Daemon)
	}
}

func TestConsumerRoutesStop(t *testing.T) {
	if !isConsumerSubcommand("stop") {
		t.Fatal("stop command does not route to the consumer tree")
	}
	for _, command := range consumerRoot().Commands() {
		if command.Name() == "stop" {
			if command.Hidden {
				t.Fatal("stop command is hidden")
			}
			return
		}
	}
	t.Fatal("consumer tree is missing the stop command")
}
