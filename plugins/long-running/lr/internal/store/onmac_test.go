package store

import (
	"errors"
	"os"
	"path/filepath"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
)

func TestARegistryMarkedOnTheMacRefusesToOpenSoNoSpritePrintsAnEmptyOne(t *testing.T) {
	path := filepath.Join(t.TempDir(), "lr.sqlite")
	if err := os.WriteFile(path+drive.OnMac, nil, 0o600); err != nil {
		t.Fatal(err)
	}
	if s, err := Open(path); !errors.Is(err, drive.ErrOnMac) {
		t.Fatalf("opened %v, err %v", s, err)
	}
	if _, err := os.Stat(path); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("the refused open left a registry at %s: %v", path, err)
	}
}
