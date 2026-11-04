package lanes

import (
	"context"
	"errors"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

func TestADevboxIsNamedForItsLaneAndItsHolderIsRecorded(t *testing.T) {
	bus := &fakeBus{}
	var ran []string
	runner := scripted(func(argv []string) (shell.Result, error) {
		ran = argv
		return shell.Result{Stdout: "created\n"}, nil
	})

	printed, err := MakeDevbox(context.Background(), runner, bus, testDrive, perfLane, []string{"--platform", "macos/arm64", "--size", "m"})
	if err != nil {
		t.Fatal(err)
	}

	equal(t, printed, "created\n")
	equal(t, ran, []string{"devbox", "create", "--name", perfLane, "--purpose", "lane " + perfLane + " of drive " + testDrive, "--platform", "macos/arm64", "--size", "m"})
	equal(t, len(bus.posted), 1)
	equal(t, []string{bus.posted[0].Lane, bus.posted[0].Topic}, []string{perfLane, devboxTopic + perfLane})
}

func TestARefusedDevboxRecordsNoHolder(t *testing.T) {
	bus := &fakeBus{}
	runner := scripted(func([]string) (shell.Result, error) { return shell.Result{}, errors.New("devbox: not logged in") })

	if _, err := MakeDevbox(context.Background(), runner, bus, testDrive, perfLane, nil); err == nil {
		t.Fatal("want the refusal returned")
	}

	equal(t, len(bus.posted), 0)
}
