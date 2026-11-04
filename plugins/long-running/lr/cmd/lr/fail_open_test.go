package main

import (
	"bytes"
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/wait"
)

const readFailed = "read failed"

var keptGates = map[string]string{"landing go": "answer 8c73884: a go is the authorization itself, so an unread go has nothing to pass"}

type unreadable struct{ reads atomic.Int64 }

func (u *unreadable) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	u.reads.Add(1)
	return shell.Result{Code: 1, Stderr: readFailed}, &shell.ExitError{Argv: c.Argv, Code: 1, Stderr: readFailed}
}

func unreadDrive(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	config := filepath.Join(dir, "lr.json")
	if err := os.WriteFile(config, []byte(`{"repo":"acme/app","drive":"d","checkout":"/co","author":"a","label":"l","queue_script":"tools/queue","movers":["lanes","landing"]}`), 0o600); err != nil {
		t.Fatal(err)
	}
	s, err := store.Open(filepath.Join(dir, lanes.StoreFile))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = s.Close() }()
	if err := s.Own("lane-a", []int{5}); err != nil {
		t.Fatal(err)
	}
	return config
}

func runUnread(args ...string) (stdout, stderr string, reads int64, err error) {
	var out, errs bytes.Buffer
	runner := &unreadable{}
	a := app.New(&errs)
	a.Runner = runner
	a.CCX = ccx.CLI{Runner: runner}
	cmd := root(a)
	cmd.SetOut(&out)
	cmd.SetErr(&errs)
	cmd.SetArgs(args)
	err = cmd.Execute()
	return out.String(), errs.String(), runner.reads.Load(), err
}

func TestAGateWhoseReadFailedRefusesNothing(t *testing.T) {
	for _, gate := range []struct {
		args     []string
		exitZero bool
	}{
		{args: []string{"landing", "gate", "5"}, exitZero: true},
		{args: []string{"done-check", "lane-a"}},
		{args: []string{"own-red", "5", "--repo", "acme/app"}},
	} {
		t.Run(strings.Join(gate.args, " "), func(t *testing.T) {
			t.Setenv("CCI_HOME", t.TempDir())
			stdout, stderr, reads, err := runUnread(append(gate.args, "--config", unreadDrive(t))...)
			if reads == 0 {
				t.Fatalf("attempted no read, so this proves nothing: %v", err)
			}
			if stdout != "" {
				t.Errorf("printed a refusal it never read: %q", stdout)
			}
			var exit *wait.ExitError
			if errors.As(err, &exit) {
				t.Errorf("exited %d, the code that means it refused", exit.Code)
			}
			if gate.exitZero && (err != nil || !strings.Contains(stderr, "Warning, nothing was refused") || !strings.Contains(stderr, readFailed)) {
				t.Errorf("got %v and stderr %q, want exit 0 and a warning naming the failed read", err, stderr)
			}
		})
	}
}

func TestEachKeptGateIsARealVerb(t *testing.T) {
	cmd := root(app.New(&bytes.Buffer{}))
	for verb, reason := range keptGates {
		if found, _, err := cmd.Find(strings.Fields(verb)); err != nil || found.CommandPath() != "lr "+verb {
			t.Errorf("%s, kept because %s, reached %s: %v", verb, reason, found.CommandPath(), err)
		}
	}
}
