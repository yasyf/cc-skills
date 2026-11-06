package install

import (
	"context"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

type launchdDesks map[string]string

func (l launchdDesks) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	switch c.Argv[0] {
	case "launchctl":
		for label, pid := range l {
			if strings.HasSuffix(c.Argv[2], "/"+label) && pid != "" {
				return shell.Result{Stdout: "\tstate = running\n\tpid = " + pid + "\n"}, nil
			}
		}
		return shell.Result{}, &shell.ExitError{Argv: c.Argv, Code: 113}
	case "ps":
		listed := map[string]string{
			"11": "Thu Nov  5 21:00:00 2026     /cache/lr obs desk PATH=/usr/bin BINRUN_PLUGIN_ROOT=/plugins/long-running/0.7.1805",
			"12": "Thu Nov  5 20:00:00 2026     /cache/lr supervisor run BINRUN_PLUGIN_ROOT=/plugins/long-running/0.7.1807",
			"13": "Thu Nov  5 21:45:00 2026     /cache/lr relay run BINRUN_PLUGIN_ROOT=/plugins/long-running/0.7.1805",
			"14": "Thu Nov  5 19:00:00 2026     /bin/sh keep.sh PATH=/usr/bin",
		}[c.Argv[len(c.Argv)-1]]
		return shell.Result{Stdout: listed + "\n"}, nil
	}
	return shell.Result{}, nil
}

func TestTheReadBackNamesEachDriveDeskStillRunningAProcessFromBeforeTheInstall(t *testing.T) {
	home := t.TempDir()
	agents := filepath.Join(home, "Library", "LaunchAgents")
	if err := os.MkdirAll(agents, 0o750); err != nil {
		t.Fatal(err)
	}
	runner := launchdDesks{
		"com.long-running.d1.obs":         "11",
		"com.long-running.d1.supervisor":  "12",
		"com.long-running.d1.relay":       "13",
		"com.long-running.d1.pool-keeper": "14",
		"com.long-running.d1.board-link":  "",
		"com.long-running.d2.obs":         "11",
	}
	for label := range runner {
		if err := os.WriteFile(filepath.Join(agents, label+".plist"), nil, 0o600); err != nil {
			t.Fatal(err)
		}
	}
	moved := time.Date(2026, 11, 5, 21, 30, 0, 0, time.Local)

	stale, err := staleDesks(context.Background(), runner, home, "d1", "0.7.1807", moved)
	if err != nil {
		t.Fatal(err)
	}

	want := []desk{
		{Label: "com.long-running.d1.obs", PID: 11, Started: time.Date(2026, 11, 5, 21, 0, 0, 0, time.Local), Version: "0.7.1805"},
		{Label: "com.long-running.d1.pool-keeper", PID: 14, Started: time.Date(2026, 11, 5, 19, 0, 0, 0, time.Local), Version: noPack},
	}
	if !reflect.DeepEqual(stale, want) {
		t.Fatalf("stale desks %+v, want %+v: a desk started after the install, one already on the installed pack, one not running and another drive's desk all stay off the read-back", stale, want)
	}
}
