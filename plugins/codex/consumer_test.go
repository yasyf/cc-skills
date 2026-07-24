package main

import (
	"slices"
	"testing"

	"github.com/yasyf/cc-interact/daemon"
	"github.com/yasyf/daemonkit/service"
)

func TestConsumerUsesExactRuntimeWiring(t *testing.T) {
	got := launcher()
	if got.WireBuild != daemon.WireBuild || got.RuntimeBuild != appVersion {
		t.Fatalf("launcher identities = %q, %q", got.WireBuild, got.RuntimeBuild)
	}
	if got.Agent.Label != codexServiceLabel || got.Agent.RestartPolicy != service.RestartOnFailure ||
		!slices.Equal(got.Agent.Args, []string{"daemon"}) || got.Roles != appRoles() {
		t.Fatalf("launcher runtime = %#v, %#v", got.Agent, got.Roles)
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
