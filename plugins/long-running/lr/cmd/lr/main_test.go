package main

import (
	"bytes"
	"context"
	"errors"
	"io"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/askdesk"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/wait"
)

func run(t *testing.T, args ...string) (string, error) {
	t.Helper()
	var out bytes.Buffer
	cmd := root(app.New(&out))
	cmd.SetOut(&out)
	cmd.SetErr(&out)
	err := execute(cmd, args)
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

func TestAGroupRefusesAVerbItDoesNotHave(t *testing.T) {
	saved := verbs
	t.Cleanup(func() { verbs = saved })
	register(func(*app.App) *cobra.Command {
		group := &cobra.Command{Use: "probe-group"}
		nested := &cobra.Command{Use: "inner"}
		nested.AddCommand(&cobra.Command{Use: "leaf", RunE: func(*cobra.Command, []string) error { return nil }})
		group.AddCommand(nested)
		return group
	})
	for _, args := range [][]string{{"probe-group", "gone"}, {"probe-group", "inner", "gone"}} {
		if _, err := run(t, args...); err == nil || !strings.Contains(err.Error(), "has no verb gone") {
			t.Errorf("lr %v = %v, want the unknown verb refused so a probe does not read it as installed", args, err)
		}
	}
	for _, args := range [][]string{{"probe-group"}, {"probe-group", "inner"}, {"probe-group", "inner", "leaf"}} {
		if _, err := run(t, args...); err != nil {
			t.Errorf("lr %v = %v, want help or the verb", args, err)
		}
	}
}

func TestAStoreVerbOnASpriteRunsOnTheMacsStoreDeskAndAnUnlistedVerbRunsHere(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	t.Setenv("CLAUDE_CONFIG_DIR", t.TempDir())
	t.Setenv(lanes.LaneEnv, "sentry-1-fix")
	registry := filepath.Join(os.Getenv("CLAUDE_CONFIG_DIR"), "long-running", "drives")
	if err := os.MkdirAll(registry, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(registry, "d.json"), []byte(`{"drive":"d","cci_drive":"d","state_dir":"`+t.TempDir()+`","sessions":["s"],"host":"`+drive.HostSprite+`"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	var ranArgv []string
	var ranLane string
	ctx, cancel := context.WithCancel(context.Background())
	stopped := make(chan struct{})
	go func() {
		askdesk.Desk{Lane: askdesk.StoreDesk, Drive: "d", Bodies: t.TempDir(), Now: time.Now, Log: io.Discard, Read: askdesk.StoreRuns("/mac/lr.json", func(_ context.Context, argv []string, lane string) (string, string, int, error) {
			ranArgv, ranLane = argv, lane
			return "#51766 waits on his tap\n", "a warning from the Mac\n", 1, nil
		})}.Follow(ctx)
		close(stopped)
	}()
	t.Cleanup(func() {
		cancel()
		<-stopped
	})
	saved := verbs
	t.Cleanup(func() { verbs = saved })
	ranHere := false
	register(func(*app.App) *cobra.Command {
		return &cobra.Command{Use: "probe", RunE: func(*cobra.Command, []string) error {
			ranHere = true
			return nil
		}}
	})

	var out, errs bytes.Buffer
	cmd := root(app.New(&errs))
	cmd.SetOut(&out)
	cmd.SetErr(&errs)
	err := execute(cmd, []string{"board", "waits", "--pr", "51766"})

	var exit *wait.ExitError
	if !errors.As(err, &exit) || exit.Code != 1 {
		t.Fatalf("got %v, want the Mac's exit 1", err)
	}
	if out.String() != "#51766 waits on his tap\n" || !strings.Contains(errs.String(), "a warning from the Mac") {
		t.Fatalf("printed %q and %q, want the Mac's stdout and stderr", out.String(), errs.String())
	}
	if want := []string{"board", "waits", "--config", "/mac/lr.json", "--pr=51766", "--"}; !slices.Equal(ranArgv, want) || ranLane != "sentry-1-fix" {
		t.Fatalf("the desk ran %q as %q, want %q as sentry-1-fix", ranArgv, ranLane, want)
	}
	ranArgv = nil
	if _, err := run(t, "probe"); err != nil || !ranHere || ranArgv != nil {
		t.Fatalf("an unlisted verb returned %v, ran here %t, and reached the desk with %q", err, ranHere, ranArgv)
	}
}

func TestEveryStoreVerbNamesARunnableVerb(t *testing.T) {
	cmd := root(app.New(&bytes.Buffer{}))
	for _, verb := range askdesk.StoreVerbs {
		found, _, err := cmd.Find(strings.Fields(verb))
		if err != nil || found.CommandPath() != "lr "+verb || found.RunE == nil {
			t.Errorf("store verb %q reached %s, %v: the list names no runnable verb", verb, found.CommandPath(), err)
		}
	}
}
