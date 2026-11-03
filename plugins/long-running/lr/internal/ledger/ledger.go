package ledger

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"maps"
	"os"
	"slices"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

type Row struct {
	Key     string            `json:"key"`
	Fields  map[string]string `json:"fields"`
	Updated time.Time         `json:"updated_at,omitzero"`
}

type Store interface {
	Rows(ctx context.Context, ledger string) ([]Row, error)
	SetRow(ctx context.Context, ledger, key string, fields map[string]string) error
	RemoveRow(ctx context.Context, ledger, key string) error
	Sync(ctx context.Context, ledger string, rows []Row) error
}

func FromState(ctx context.Context, source Store, path string) ([]Row, error) {
	data, err := os.ReadFile(path)
	if errors.Is(err, fs.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var state struct {
		Ledger string `json:"ledger"`
	}
	if err := json.Unmarshal(data, &state); err != nil {
		return nil, fmt.Errorf("%s: %w", path, err)
	}
	if state.Ledger == "" {
		return nil, nil
	}
	return source.Rows(ctx, state.Ledger)
}

type CLI struct {
	Runner shell.Runner
	Repo   string
}

func (c CLI) Rows(ctx context.Context, ledger string) ([]Row, error) {
	result, err := c.Runner.Run(ctx, shell.Command{Argv: c.argv("show", ledger, "--json")})
	if err != nil {
		return nil, err
	}
	var payload struct {
		Rows []Row `json:"rows"`
	}
	if err := json.Unmarshal([]byte(result.Stdout), &payload); err != nil {
		return nil, err
	}
	return payload.Rows, nil
}

func (c CLI) SetRow(ctx context.Context, ledger, key string, fields map[string]string) error {
	argv := c.argv("row", "set", ledger, "--key", key)
	for _, name := range slices.Sorted(maps.Keys(fields)) {
		argv = append(argv, "--field", name+"="+fields[name])
	}
	_, err := c.Runner.Run(ctx, shell.Command{Argv: argv})
	return err
}

func (c CLI) RemoveRow(ctx context.Context, ledger, key string) error {
	_, err := c.Runner.Run(ctx, shell.Command{Argv: c.argv("row", "rm", ledger, "--key", key)})
	return err
}

func (c CLI) Sync(ctx context.Context, ledger string, rows []Row) error {
	encoded, err := json.Marshal(rows)
	if err != nil {
		return err
	}
	_, err = c.Runner.Run(ctx, shell.Command{Argv: c.argv("sync", ledger, "--file", "-"), Stdin: string(encoded)})
	return err
}

func (c CLI) argv(verb ...string) []string {
	argv := []string{"ccn"}
	if c.Repo != "" {
		argv = append(argv, "-R", c.Repo)
	}
	return append(append(argv, "ledger"), verb...)
}
