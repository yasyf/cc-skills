package lanes

import (
	"errors"
	"os"
	"path/filepath"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func TestOnASpriteWithNoStoreEveryStoreAndRunnerReadRefusesNamingTheMac(t *testing.T) {
	state := t.TempDir()
	for _, held := range []string{StoreFile, "runner.json"} {
		if err := os.WriteFile(filepath.Join(state, held+drive.OnMac), nil, 0o600); err != nil {
			t.Fatal(err)
		}
	}
	reads := map[string]func() error{
		"store": func() error {
			_, err := store.Open(filepath.Join(state, StoreFile))
			return err
		},
		"runner": func() error {
			_, err := config.LoadRunner(filepath.Join(state, "runner.json"))
			return err
		},
		"landing": func() error {
			_, err := ReadRunnerLanding(state)
			return err
		},
		"cc-remote": func() error {
			_, err := CcRemoteOf(state)
			return err
		},
	}
	for name, read := range reads {
		if err := read(); !errors.Is(err, drive.ErrOnMac) {
			t.Errorf("%s read on a Sprite returned %v, want a refusal naming the Mac", name, err)
		}
	}
}
