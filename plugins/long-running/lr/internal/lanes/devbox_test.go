package lanes

import (
	"context"
	"errors"
	"strings"
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

	printed, err := MakeDevbox(context.Background(), runner, bus, testDrive, perfLane, false, []string{"--platform", "macos/arm64", "--size", "m"})
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

	if _, err := MakeDevbox(context.Background(), runner, bus, testDrive, perfLane, false, nil); err == nil || strings.Contains(err.Error(), "why=devbox") {
		t.Fatalf("want the refusal returned as devbox printed it on the Mac, got %v", err)
	}

	equal(t, len(bus.posted), 0)
}

func TestADevboxRefusedOnASpriteSaysToRelaunchTheLaneOnTheMac(t *testing.T) {
	bus := &fakeBus{}
	login := errors.New("devbox create exited 1: not logged in, run `devbox login` to authenticate")
	runner := scripted(func([]string) (shell.Result, error) { return shell.Result{}, login })

	_, err := MakeDevbox(context.Background(), runner, bus, testDrive, perfLane, true, nil)

	if !errors.Is(err, login) || !strings.Contains(err.Error(), "relaunch lane "+perfLane+" on the Mac with `place=local why=devbox`") {
		t.Fatalf("want the Mac relaunch named beside devbox's own refusal, got %v", err)
	}
	equal(t, len(bus.posted), 0)
}
