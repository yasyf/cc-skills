package orcarpc

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"slices"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	parityAttempts        = 3
	parityEnvironments    = 3
	parityMissingWorktree = "path:/orcarpc-parity-missing"
	parityMissingTerminal = "term_orcarpc-parity-missing"
)

var errParityDiffers = errors.New("orcarpc parity: output differs")

func Parity(ctx context.Context, client *Client, cli shell.Runner) error {
	probe := *client
	probe.Gate = nil
	forms, err := parityForms(ctx, &probe)
	if err != nil {
		return err
	}
	for _, form := range forms {
		if err := probe.parityOf(ctx, cli, form); err != nil {
			return err
		}
	}
	return nil
}

func parityForms(ctx context.Context, client *Client) ([][]string, error) {
	forms := [][]string{
		{"status", "--json"},
		{"terminal", "list", "--limit", "1", "--json"},
		{"environment", "list", "--json"},
		{"orchestration", "inbox", "--limit", "1", "--full", "--json"},
		{"worktree", "show", "--worktree", parityMissingWorktree, "--json"},
		{"terminal", "show", "--terminal", parityMissingTerminal, "--json"},
	}
	listed, ok := client.Serve(ctx, forms[1])
	if !ok {
		return nil, fmt.Errorf("orcarpc parity: terminal list was not served")
	}
	var terminals struct {
		Result struct {
			Terminals []struct {
				Handle string `json:"handle"`
			} `json:"terminals"`
		} `json:"result"`
	}
	if err := json.Unmarshal(listed.Stdout, &terminals); err != nil {
		return nil, err
	}
	if len(terminals.Result.Terminals) > 0 {
		handle := terminals.Result.Terminals[0].Handle
		forms = append(forms,
			[]string{"terminal", "show", "--terminal", handle, "--json"},
			[]string{"terminal", "read", "--terminal", handle, "--limit", "1", "--json"},
			[]string{"orchestration", "check", "--terminal", handle, "--peek", "--full", "--json"},
		)
	}
	forms = append(forms, remoteParityForms(ctx, client)...)
	conn, err := client.Dial(ctx)
	if err != nil {
		return nil, err
	}
	defer func() { _ = conn.Close() }()
	reply, err := conn.Call(ctx, "orchestration.runList", map[string]any{})
	if err != nil {
		return nil, err
	}
	if !reply.OK {
		return nil, reply.Error
	}
	var runs struct {
		Runs []struct {
			ID string `json:"id"`
		} `json:"runs"`
	}
	if err := json.Unmarshal(reply.Result, &runs); err != nil {
		return nil, err
	}
	for _, run := range runs.Runs {
		form := []string{"orchestration", "worker-list", "--run", run.ID, "--limit", "1", "--full", "--json"}
		page, ok := client.Serve(ctx, form)
		if !ok {
			continue
		}
		var workers struct {
			Result struct {
				Workers []struct {
					Dispatch string `json:"dispatchId"`
				} `json:"workers"`
			} `json:"result"`
		}
		if json.Unmarshal(page.Stdout, &workers) == nil && len(workers.Result.Workers) > 0 {
			return append(forms, form, []string{"orchestration", "worker-show", "--dispatch", workers.Result.Workers[0].Dispatch, "--full", "--json"}), nil
		}
	}
	return forms, nil
}

func remoteParityForms(ctx context.Context, client *Client) [][]string {
	for _, name := range client.recentEnvironments(parityEnvironments) {
		form := []string{"terminal", "list", "--limit", "1", "--environment", name, "--json"}
		listed, ok := client.Serve(ctx, form)
		if !ok || listed.Code != 0 {
			continue
		}
		var terminals struct {
			Result struct {
				Terminals []struct {
					Handle string `json:"handle"`
				} `json:"terminals"`
			} `json:"result"`
		}
		if json.Unmarshal(listed.Stdout, &terminals) != nil || len(terminals.Result.Terminals) == 0 {
			continue
		}
		handle := terminals.Result.Terminals[0].Handle
		return [][]string{
			form,
			{"terminal", "read", "--terminal", handle, "--screen", "--environment", name, "--json"},
			{"orchestration", "check", "--terminal", handle, "--peek", "--environment", name, "--full", "--json"},
		}
	}
	return nil
}

func (c *Client) parityOf(ctx context.Context, cli shell.Runner, form []string) error {
	var difference error
	for range parityAttempts {
		served, ok := c.Serve(ctx, form)
		if !ok {
			return nil
		}
		said, err := cli.Run(ctx, shell.Command{Argv: append([]string{"orca"}, form...), Timeout: statusRequestTimeout * 60})
		var exit *shell.ExitError
		if err != nil && (!errors.As(err, &exit) || exit.Code != served.Code) {
			return fmt.Errorf("orcarpc parity: orca %s: %w", strings.Join(form, " "), err)
		}
		difference = sameShape(form, served.Stdout, []byte(said.Stdout))
		if difference == nil {
			return nil
		}
	}
	return difference
}

func sameShape(form []string, ours, theirs []byte) error {
	left, err := shapeOf(ours)
	if err != nil {
		return err
	}
	right, err := shapeOf(theirs)
	if err != nil {
		return err
	}
	if slices.Equal(left, right) {
		return nil
	}
	return fmt.Errorf("%w: orca %s: ours %v, theirs %v", errParityDiffers, strings.Join(form, " "), left, right)
}

func shapeOf(raw []byte) ([]string, error) {
	var value any
	if err := json.Unmarshal(raw, &value); err != nil {
		return nil, err
	}
	seen := map[string]bool{}
	walk(value, "", seen)
	paths := make([]string, 0, len(seen))
	for path := range seen {
		paths = append(paths, path)
	}
	slices.Sort(paths)
	return paths, nil
}

func walk(value any, path string, seen map[string]bool) {
	switch typed := value.(type) {
	case map[string]any:
		for key, child := range typed {
			walk(child, path+"/"+key, seen)
		}
	case []any:
		for _, child := range typed {
			walk(child, path+"[]", seen)
		}
	default:
		seen[path] = true
	}
}
