package deskhost

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

func keysFile(t *testing.T) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "keys.json")
	body := `{"region":"us-west-2","keys":{"GH_TOKEN":"/d/gh","BUILDKITE_TOKEN":"/t/bk","BUILDKITE_API_TOKEN":"/t/bk"}}`
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestKeysReadInOneBatchAndLandInTheEnvironmentByName(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply(`{"Parameters":[{"Name":"/d/gh","Value":"gh-value"},{"Name":"/t/bk","Value":"bk-value"}],"InvalidParameters":[]}`, "aws", "ssm", "get-parameters")

	env, err := KeyEnv(context.Background(), fake, keysFile(t))
	if err != nil {
		t.Fatal(err)
	}

	want := []string{"BUILDKITE_API_TOKEN=bk-value", "BUILDKITE_TOKEN=bk-value", "GH_TOKEN=gh-value"}
	if !slices.Equal(env, want) {
		t.Fatalf("env = %q, want %q", env, want)
	}
	if calls := fake.Calls(); len(calls) != 1 {
		t.Fatalf("%d aws calls, want one batched read", len(calls))
	}
	argv := fake.Calls()[0].Argv
	if slices.Contains(argv, "--profile") {
		t.Fatalf("the read names a profile, so it is not by the host's role: %q", argv)
	}
	if count := len(slices.DeleteFunc(slices.Clone(argv), func(word string) bool { return word != "/t/bk" })); count != 1 {
		t.Fatalf("a parameter two keys share was asked for %d times", count)
	}
}

func TestAKeyStillOnItsPlaceholderRefusesAndNamesTheParameterNotAValue(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply(`{"Parameters":[{"Name":"/d/gh","Value":"unset"},{"Name":"/t/bk","Value":"bk-secret"}],"InvalidParameters":[]}`, "aws", "ssm", "get-parameters")

	_, err := KeyEnv(context.Background(), fake, keysFile(t))

	if err == nil || !strings.Contains(err.Error(), "/d/gh") || strings.Contains(err.Error(), "bk-secret") {
		t.Fatalf("err = %v, want one naming /d/gh and no value", err)
	}
}

func TestAMissingParameterRefuses(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply(`{"Parameters":[{"Name":"/t/bk","Value":"bk"}],"InvalidParameters":["/d/gh"]}`, "aws", "ssm", "get-parameters")

	_, err := KeyEnv(context.Background(), fake, keysFile(t))

	if err == nil || !strings.Contains(err.Error(), "/d/gh") {
		t.Fatalf("err = %v, want one naming /d/gh", err)
	}
}

func TestARoleThatMayNotReadRefusesWithAwsWords(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Fail(254, "AccessDeniedException", "aws", "ssm", "get-parameters")

	_, err := KeyEnv(context.Background(), fake, keysFile(t))

	if err == nil || !strings.Contains(err.Error(), "AccessDeniedException") {
		t.Fatalf("err = %v", err)
	}
}

type clock struct{ at time.Time }

func (c *clock) now() time.Time { return c.at }

func (c *clock) sleep(d time.Duration) { c.at = c.at.Add(d) }

func deployWith(t *testing.T, fake *shelltest.Fake) (Deploy, *bytes.Buffer) {
	t.Helper()
	root := t.TempDir()
	plugin := filepath.Join(root, "plugin", "bin")
	if err := os.MkdirAll(plugin, 0o750); err != nil {
		t.Fatal(err)
	}
	pin := map[string]any{"platforms": map[string]any{"linux-x86_64": map[string]any{
		"hash": "sha256", "digest": strings.Repeat("a", 64), "format": "tar.gz", "path": "lr",
		"providers": []map[string]string{{"type": "github-release", "repo": "yasyf/cc-skills", "tag": "lr-v1", "name": "lr.tgz"}},
	}}}
	encoded, err := json.Marshal(pin)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(plugin, "lr.binrun"), append([]byte("#!/bin/sh\n"), encoded...), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("BINRUN_PLUGIN_ROOT", filepath.Join(root, "plugin"))
	config := filepath.Join(root, "runner.json")
	runner := `{"orca":{"sprite":{"env":{"WORKER_CCI_URL":"https://x/cci.tgz","WORKER_CCI_SHA256":"` + strings.Repeat("b", 64) + `"},"tools":[]}}}`
	if err := os.WriteFile(config, []byte(runner), 0o600); err != nil {
		t.Fatal(err)
	}
	fake.Reply(`[{"mesh":"default","url":"https://relay/","routes":["durable-agents-rebuild"]}]`, "cci", "relay", "status")
	fake.Reply(`{"public_key":"owner-key"}`, "cci", "node")
	fake.Reply("", "aws", "--profile", "core-auto", "--region", "us-west-2", "ssm", "put-parameter")
	fake.Reply("command-1\n", "aws", "--profile", "core-auto", "--region", "us-west-2", "ssm", "send-command")
	var out bytes.Buffer
	c := &clock{at: time.Date(2026, 11, 3, 15, 0, 0, 0, time.UTC)}
	return Deploy{Runner: fake, Out: &out, Sleep: c.sleep, Now: c.now, Config: config, Host: "frg-desk-host", Profile: "core-auto", Region: "us-west-2"}, &out
}

func callNamed(fake *shelltest.Fake, verb string) shell.Command {
	for _, call := range fake.Calls() {
		if slices.Contains(call.Argv, verb) {
			return call
		}
	}
	return shell.Command{}
}

func TestADeployRecordsThePackPinsThenWaitsForTheHostToSync(t *testing.T) {
	fake := &shelltest.Fake{}
	d, out := deployWith(t, fake)
	fake.Reply(`["Success"]`, "aws", "--profile", "core-auto", "--region", "us-west-2", "ssm", "list-command-invocations")

	if err := d.Run(context.Background()); err != nil {
		t.Fatal(err)
	}

	put := callNamed(fake, "put-parameter").Argv
	var stored map[string]any
	if err := json.Unmarshal([]byte(put[slices.Index(put, "--value")+1]), &stored); err != nil {
		t.Fatal(err)
	}
	if stored["drive"] != "durable-agents-rebuild" || stored["relay_owner"] != "owner-key" {
		t.Fatalf("stored %v", stored)
	}
	if lr := stored["lr"].(map[string]any); lr["url"] != "https://github.com/yasyf/cc-skills/releases/download/lr-v1/lr.tgz" || lr["sha256"] != strings.Repeat("a", 64) {
		t.Fatalf("lr pin %v", lr)
	}
	if !strings.Contains(out.String(), "frg-desk-host") {
		t.Fatalf("out = %q", out.String())
	}
}

func TestAFailedSyncIsTheDeploysFailure(t *testing.T) {
	fake := &shelltest.Fake{}
	d, _ := deployWith(t, fake)
	fake.Reply(`["Failed"]`, "aws", "--profile", "core-auto", "--region", "us-west-2", "ssm", "list-command-invocations")

	if err := d.Run(context.Background()); err == nil || !strings.Contains(err.Error(), "Failed") {
		t.Fatalf("err = %v", err)
	}
}

func TestAHostThatNeverAnswersEndsTheWait(t *testing.T) {
	fake := &shelltest.Fake{}
	d, _ := deployWith(t, fake)
	fake.Reply(`[]`, "aws", "--profile", "core-auto", "--region", "us-west-2", "ssm", "list-command-invocations")

	if err := d.Run(context.Background()); err == nil || !strings.Contains(err.Error(), "did not answer") {
		t.Fatalf("err = %v", err)
	}
}
