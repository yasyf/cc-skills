package terminalcap

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	listLimit   = "1000"
	mostOrcaCap = 4096
	readTimeout = 30 * time.Second
)

type Terminal struct {
	Handle   string `json:"handle"`
	Worktree string `json:"worktreePath"`
}

type Reading struct {
	Terminals []Terminal
	Cap       int
}

func (r Reading) Full() bool {
	return len(r.Terminals) >= r.Cap
}

func (r Reading) Near() bool {
	return len(r.Terminals)*10 >= r.Cap*9
}

func Read(ctx context.Context, runner shell.Runner, home string) (Reading, error) {
	limit, err := orcaCap(ctx, runner, home)
	if err != nil {
		return Reading{}, err
	}
	terminals, err := List(ctx, runner)
	if err != nil {
		return Reading{}, err
	}
	return Reading{Terminals: terminals, Cap: limit}, nil
}

func List(ctx context.Context, runner shell.Runner) ([]Terminal, error) {
	result, err := runner.Run(ctx, shell.Command{Argv: []string{"orca", "terminal", "list", "--json", "--limit", listLimit}, Timeout: readTimeout})
	if err != nil {
		return nil, fmt.Errorf("orca terminal list: %w", err)
	}
	var listed struct {
		Result struct {
			Terminals []Terminal `json:"terminals"`
		} `json:"result"`
	}
	if err := json.Unmarshal([]byte(result.Stdout), &listed); err != nil {
		return nil, fmt.Errorf("orca terminal list: %w", err)
	}
	return listed.Result.Terminals, nil
}

func orcaCap(ctx context.Context, runner shell.Runner, home string) (int, error) {
	saved, err := filepath.Glob(filepath.Join(home, ".orca", "host-settings", "*", "terminals.json"))
	if err != nil {
		return 0, err
	}
	if len(saved) > 0 {
		return savedCap(saved[0])
	}
	result, err := runner.Run(ctx, shell.Command{Argv: []string{"sysctl", "-n", "kern.tty.ptmx_max"}, Timeout: readTimeout})
	if err != nil {
		return 0, fmt.Errorf("sysctl kern.tty.ptmx_max: %w", err)
	}
	ptys, err := strconv.Atoi(strings.TrimSpace(result.Stdout))
	if err != nil {
		return 0, fmt.Errorf("sysctl kern.tty.ptmx_max: %w", err)
	}
	return min((ptys+1)/2, mostOrcaCap), nil
}

func savedCap(path string) (int, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return 0, err
	}
	var saved struct {
		MaxTerminals int `json:"maxTerminals"`
	}
	if err := json.Unmarshal(raw, &saved); err != nil {
		return 0, fmt.Errorf("%s: %w", path, err)
	}
	return saved.MaxTerminals, nil
}
