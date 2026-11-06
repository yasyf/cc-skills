package supervisor

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

type storeRunner struct{ ran shell.Command }

func (r *storeRunner) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	r.ran = c
	return shell.Result{Stdout: "#51741 waits on CI\n"}, nil
}

func TestTheStoreDeskRunsAVerbWithItsOwnLrExecutableAsTheAskingLane(t *testing.T) {
	runner := &storeRunner{}
	verb := []string{"board", "waits", "--config", "/mac/lr.json", "--pr=51741", "--"}

	stdout, _, exit, err := storeVerbRun(runner, "durable-agents-rebuild")(context.Background(), verb, "sentry-1-fix")

	self, selfErr := os.Executable()
	if err != nil || selfErr != nil || exit != 0 || stdout != "#51741 waits on CI\n" {
		t.Fatalf("answered %q exit %d, %v, %v", stdout, exit, err, selfErr)
	}
	if want := append([]string{self}, verb...); !slices.Equal(runner.ran.Argv, want) {
		t.Fatalf("ran %q, want %q: the desk's own lr executable, never its version string", runner.ran.Argv, want)
	}
	if !slices.Contains(runner.ran.Env, lanes.LaneEnv+"=sentry-1-fix") {
		t.Fatalf("ran with %q, want the asking lane bound", runner.ran.Env)
	}
}

func TestOnlyTheHostHoldingTheDrivesStoreRunsItsStoreDesk(t *testing.T) {
	const name = "durable-agents-rebuild"
	for label, c := range map[string]struct {
		entry  *drive.Entry
		serves bool
	}{
		"no entry":                 {nil, false},
		"an entry with no session": {&drive.Entry{Drive: name}, false},
		"a Sprite's entry":         {&drive.Entry{Drive: name, Sessions: []string{"root"}, Host: drive.HostSprite}, false},
		"the Mac's entry":          {&drive.Entry{Drive: name, Sessions: []string{"root"}}, true},
	} {
		t.Run(label, func(t *testing.T) {
			config := t.TempDir()
			t.Setenv("CLAUDE_CONFIG_DIR", config)
			if c.entry != nil {
				data, err := json.Marshal(c.entry)
				if err != nil {
					t.Fatal(err)
				}
				dir := filepath.Join(config, "long-running", "drives")
				if err := os.MkdirAll(dir, 0o750); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(filepath.Join(dir, name+".json"), data, 0o600); err != nil {
					t.Fatal(err)
				}
			}
			if err := holdsStore(name); (err == nil) != c.serves {
				t.Fatalf("holdsStore returned %v, want serving %t", err, c.serves)
			}
		})
	}
}
