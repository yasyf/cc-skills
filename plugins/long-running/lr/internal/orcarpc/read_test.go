package orcarpc

import (
	"context"
	"fmt"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

type cliRunner struct{ calls int }

func (c *cliRunner) Run(context.Context, shell.Command) (shell.Result, error) {
	c.calls++
	return shell.Result{Stdout: "the CLI's answer"}, nil
}

func TestARunnerAnswersAReadInProcess(t *testing.T) {
	client, _ := serve(t, map[string]string{}, func(got frame) []string {
		return []string{ok(got.ID, `{"dispatch":{"id":"ctx_1"}}`)}
	})
	cli := &cliRunner{}

	result, err := Runner{Client: client, Next: cli}.Run(context.Background(), shell.Command{
		Argv: []string{"orca", "orchestration", "worker-show", "--dispatch", "ctx_1", "--full", "--json"},
		Env:  []string{"ORCA_TERMINAL_HANDLE=term_1"},
	})

	if err != nil || cli.calls != 0 || !strings.Contains(result.Stdout, `"ctx_1"`) {
		t.Fatalf("result %+v, err %v, CLI calls %d", result, err, cli.calls)
	}
}

func TestARunnerLeavesEverythingElseToTheCLI(t *testing.T) {
	client, _ := serve(t, map[string]string{}, func(got frame) []string {
		if got.Method == "orchestration.workerShow" {
			return []string{fmt.Sprintf(`{"id":%q,"ok":false,"error":{"code":"not_found","message":"no such dispatch"}}`, got.ID)}
		}
		return []string{ok(got.ID, `{}`)}
	})
	for _, c := range []shell.Command{
		{Argv: []string{"git", "status"}},
		{Argv: []string{"orca", "terminal", "send", "--terminal", "term_1", "--text", "go", "--json"}},
		{Argv: []string{"orca", "terminal", "show", "--terminal", "term_1", "--environment", "box", "--json"}},
		{Argv: []string{"orca", "orchestration", "worker-show", "--dispatch", "ctx_1", "--full", "--json"}},
		{Argv: []string{"orca", "orchestration", "check", "--peek", "--full", "--json"}, Env: []string{"ORCA_TERMINAL_HANDLE=term_1"}},
	} {
		cli := &cliRunner{}
		result, err := Runner{Client: client, Next: cli}.Run(context.Background(), c)
		if err != nil || cli.calls != 1 || result.Stdout != "the CLI's answer" {
			t.Fatalf("%v: result %+v, err %v, CLI calls %d", c.Argv, result, err, cli.calls)
		}
	}
}
