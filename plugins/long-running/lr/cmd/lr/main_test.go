package main

import (
	"bytes"
	"context"
	"errors"
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

type deskRun struct {
	argv []string
	lane string
}

func spriteWithStoreDesk(t *testing.T, stdout, stderr string, exit int) *deskRun {
	t.Helper()
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
	ran := &deskRun{}
	ctx, cancel := context.WithCancel(context.Background())
	stopped := make(chan struct{})
	go func() {
		askdesk.Desk{Lane: askdesk.StoreDesk, Drive: "d", Duty: askdesk.StoreDuty, Bodies: t.TempDir(), Now: time.Now, Log: testLog{t}, Read: askdesk.StoreRuns("/mac/lr.json", func(_ context.Context, argv []string, lane string) (string, string, int, error) {
			ran.argv, ran.lane = argv, lane
			return stdout, stderr, exit, nil
		})}.Follow(ctx)
		close(stopped)
	}()
	t.Cleanup(func() {
		cancel()
		<-stopped
	})
	return ran
}

func TestAStoreVerbOnASpriteRunsOnTheMacsStoreDeskAndAnUnlistedVerbRunsHere(t *testing.T) {
	ran := spriteWithStoreDesk(t, "#51766 waits on his tap\n", "a warning from the Mac\n", 1)
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
	if want := []string{"board", "waits", "--config", "/mac/lr.json", "--pr=51766", "--"}; !slices.Equal(ran.argv, want) || ran.lane != "sentry-1-fix" {
		t.Fatalf("the desk ran %q as %q, want %q as sentry-1-fix", ran.argv, ran.lane, want)
	}
	ran.argv = nil
	if _, err := run(t, "probe"); err != nil || !ranHere || ran.argv != nil {
		t.Fatalf("an unlisted verb returned %v, ran here %t, and reached the desk with %q", err, ranHere, ran.argv)
	}
}

func TestARulingsCheckOnASpriteWithNoRulingsPyRunsOnTheMacAndExitsAsItExited(t *testing.T) {
	refusal := "RULINGS-UNCITED 5bc8992 rule/d7b507a\n"
	ran := spriteWithStoreDesk(t, refusal, "", 1)

	var out, errs bytes.Buffer
	cmd := root(app.New(&errs))
	cmd.SetOut(&out)
	cmd.SetErr(&errs)
	err := execute(cmd, []string{"rules", "check-pr", "--pr", "Forge-AI/monorepo#51949", "--head", "5bc8992ab8772a649f81125e82982b56a86dc18b"})

	var exit *wait.ExitError
	if !errors.As(err, &exit) || exit.Code != 1 {
		t.Fatalf("got %v (stderr %q), want the Mac's exit 1", err, errs.String())
	}
	if out.String() != refusal {
		t.Fatalf("printed %q, want the Mac's %q", out.String(), refusal)
	}
	if want := []string{"rules", "check-pr", "--config", "/mac/lr.json", "--head=5bc8992ab8772a649f81125e82982b56a86dc18b", "--pr=Forge-AI/monorepo#51949", "--"}; !slices.Equal(ran.argv, want) {
		t.Fatalf("the desk ran %q, want %q", ran.argv, want)
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

type testLog struct{ t *testing.T }

func (l testLog) Write(said []byte) (int, error) {
	l.t.Log(strings.TrimSpace(string(said)))
	return len(said), nil
}
