package main

import (
	"slices"
	"testing"

	"github.com/yasyf/cc-interact/daemon"
)

func TestConsumerUsesExactRuntimeWiring(t *testing.T) {
	got := launcher()
	if got.WireBuild != daemon.WireBuild || got.RuntimeBuild != appVersion {
		t.Fatalf("launcher identities = %q, %q", got.WireBuild, got.RuntimeBuild)
	}
	if !slices.Equal(got.Args, []string{"daemon"}) ||
		!slices.Equal(got.StopArgs, []string{daemon.StopControlCommand}) {
		t.Fatalf("launcher roles = %#v, %#v", got.Args, got.StopArgs)
	}
}

func TestConsumerRoutesHiddenStopControl(t *testing.T) {
	if !isConsumerSubcommand(daemon.StopControlCommand) {
		t.Fatal("stop control command does not route to the consumer tree")
	}
	for _, command := range consumerRoot().Commands() {
		if command.Name() == daemon.StopControlCommand {
			if !command.Hidden {
				t.Fatal("stop control command is public")
			}
			return
		}
	}
	t.Fatal("consumer tree is missing the stop control command")
}
