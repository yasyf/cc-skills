package main

import (
	"bytes"
	"slices"
	"strings"
	"testing"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
)

func run(t *testing.T, args ...string) (string, error) {
	t.Helper()
	var out bytes.Buffer
	cmd := root(app.New(&out))
	cmd.SetOut(&out)
	cmd.SetErr(&out)
	cmd.SetArgs(args)
	err := cmd.Execute()
	return out.String(), err
}

func TestLRConfigNamesTheDriveOnASpriteDeskWithNoRegistry(t *testing.T) {
	t.Setenv("LR_CONFIG", "/drive/lr.json")
	a := app.New(&bytes.Buffer{})
	root(a)

	path, err := a.ConfigFile()

	if err != nil || path != "/drive/lr.json" {
		t.Fatalf("config %q, %v", path, err)
	}
}

func TestVersionPrintsTheBuildVersion(t *testing.T) {
	out, err := run(t, "--version")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out, appVersion) {
		t.Fatalf("--version printed %q, want it to carry %q", out, appVersion)
	}
}

func TestAnUnknownVerbIsRefused(t *testing.T) {
	_, err := run(t, "no-such-verb")
	if err == nil || !strings.Contains(err.Error(), "no-such-verb") {
		t.Fatalf("got %v, want a refusal naming the verb", err)
	}
}

func TestNoTwoVerbsShareAName(t *testing.T) {
	seen := map[string]bool{}
	for _, verb := range root(app.New(&bytes.Buffer{})).Commands() {
		if seen[verb.Name()] {
			t.Errorf("two verbs are named %s, and only one of them can ever run", verb.Name())
		}
		seen[verb.Name()] = true
	}
}

func TestLaunchKeepsTheStandingSessionsBesideItsVerbs(t *testing.T) {
	cmd := root(app.New(&bytes.Buffer{}))
	start, _, err := cmd.Find([]string{"launch", "start", "lane-a", "opus", "high", "brief.md"})
	if err != nil || start.Name() != "start" {
		t.Fatalf("launch start reached %s, %v", start.Name(), err)
	}
	session, args, err := cmd.Find([]string{"launch", "coordinator", "root/brief.md"})
	if err != nil || session.Name() != "launch" || !slices.Equal(args, []string{"coordinator", "root/brief.md"}) {
		t.Fatalf("launch coordinator reached %s with %v, %v", session.Name(), args, err)
	}
}

func TestARegisteredVerbRuns(t *testing.T) {
	saved := verbs
	t.Cleanup(func() { verbs = saved })
	ran := false
	register(func(*app.App) *cobra.Command {
		return &cobra.Command{Use: "probe", RunE: func(*cobra.Command, []string) error {
			ran = true
			return nil
		}}
	})
	if _, err := run(t, "probe"); err != nil {
		t.Fatal(err)
	}
	if !ran {
		t.Fatal("the registered verb did not run")
	}
}
