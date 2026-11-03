package drive

import (
	"os"
	"path/filepath"
	"testing"
)

func write(t *testing.T, dir, name, body string) {
	t.Helper()
	if err := os.WriteFile(filepath.Join(dir, name), []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
}

func TestADriveIsFoundByItsBusNameAndItsNewestSessionIsItsRoot(t *testing.T) {
	home := t.TempDir()
	t.Setenv("CLAUDE_CONFIG_DIR", home)
	dir := filepath.Join(home, "long-running", "drives")
	if err := os.MkdirAll(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	write(t, dir, "a.json", `{"drive":"a","cci_drive":"bus","sessions":[]}`)
	write(t, dir, "b.json", `{"drive":"b","cci_drive":"bus","ledger":"l1","state_dir":"/s","sessions":["old","new"]}`)
	write(t, dir, "c.json", `{"drive":"c","program":"prog","sessions":["x"]}`)
	entry, err := Named("bus")
	if err != nil {
		t.Fatal(err)
	}
	if entry.Drive != "b" || entry.Root() != "new" || entry.Store() != "/s/lr.sqlite" {
		t.Fatalf("entry = %+v", entry)
	}
	if entry, err := Named("prog"); err != nil || entry.Drive != "c" {
		t.Fatalf("by program: %+v, %v", entry, err)
	}
	if _, err := Named("nope"); err == nil {
		t.Fatal("an unknown drive read")
	}
}
