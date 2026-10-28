package main

import "testing"

func TestSuperviseRoutesToTheConsumerTree(t *testing.T) {
	if !isConsumerSubcommand("supervise") {
		t.Fatal("supervise does not route to the consumer tree")
	}
	for _, command := range consumerRoot().Commands() {
		if command.Name() == "supervise" {
			if !command.Hidden {
				t.Fatal("supervise is not hidden")
			}
			return
		}
	}
	t.Fatal("consumer tree is missing the supervise command")
}
