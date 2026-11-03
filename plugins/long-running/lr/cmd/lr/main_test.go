package main

import (
	"bytes"
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
