package supervisor

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
)

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
