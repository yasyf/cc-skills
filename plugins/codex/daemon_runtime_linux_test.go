package main

import "testing"

func TestDaemonSpecTrustsTheSameUserOnly(t *testing.T) {
	spec, err := appSpec()
	if err != nil {
		t.Fatal(err)
	}
	if spec.Trust.Control != nil || spec.Trust.Business != nil {
		t.Fatalf("trust = %#v, want only the same-user serving policy: linux has no signing verifier", spec.Trust)
	}
}

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
