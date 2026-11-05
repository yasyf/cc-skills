package terminalcap

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

type host struct {
	ptys      string
	terminals int
	listCalls int
}

func (h *host) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	if c.Argv[0] == "sysctl" {
		if h.ptys == "" {
			return shell.Result{Code: 1}, &shell.ExitError{Argv: c.Argv, Code: 1}
		}
		return shell.Result{Stdout: h.ptys + "\n"}, nil
	}
	h.listCalls++
	rows := make([]Terminal, h.terminals)
	raw, err := json.Marshal(map[string]any{"result": map[string]any{"terminals": rows}})
	return shell.Result{Stdout: string(raw)}, err
}

func TestTheCapIsHalfTheMacsPtysUntilOrcaSavesOne(t *testing.T) {
	home := t.TempDir()
	h := &host{ptys: "511", terminals: 3}
	reading, err := Read(context.Background(), h, home)
	if err != nil || reading.Cap != 256 || len(reading.Terminals) != 3 {
		t.Fatalf("reading = %+v, err = %v", reading, err)
	}
	saved := filepath.Join(home, ".orca", "host-settings", "abc", "terminals.json")
	if err := os.MkdirAll(filepath.Dir(saved), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(saved, []byte(`{"schemaVersion":1,"maxTerminals":400}`), 0o600); err != nil {
		t.Fatal(err)
	}
	reading, err = Read(context.Background(), h, home)
	if err != nil || reading.Cap != 400 {
		t.Fatalf("reading = %+v, err = %v", reading, err)
	}
}

func TestAHostWithNoPtyLimitIsUnreadAndListsNothing(t *testing.T) {
	h := &host{}
	var exit *shell.ExitError
	if _, err := Read(context.Background(), h, t.TempDir()); !errors.As(err, &exit) {
		t.Fatal(err)
	}
	if h.listCalls != 0 {
		t.Fatal(h.listCalls)
	}
}

func TestFullAndNearCountAgainstTheCap(t *testing.T) {
	for _, tt := range []struct {
		terminals, limit int
		near, full       bool
	}{
		{229, 256, false, false},
		{231, 256, true, false},
		{255, 256, true, false},
		{256, 256, true, true},
		{360, 400, true, false},
		{359, 400, false, false},
	} {
		reading := Reading{Terminals: make([]Terminal, tt.terminals), Cap: tt.limit}
		if reading.Near() != tt.near || reading.Full() != tt.full {
			t.Errorf("%d of %d: near %v, full %v", tt.terminals, tt.limit, reading.Near(), reading.Full())
		}
	}
}
