package drive

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
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

func TestADriveIdOutranksAnotherEntrysBusName(t *testing.T) {
	home := t.TempDir()
	t.Setenv("CLAUDE_CONFIG_DIR", home)
	dir := filepath.Join(home, "long-running", "drives")
	if err := os.MkdirAll(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	write(t, dir, "aaa.json", `{"drive":"aaa","cci_drive":"brook","state_dir":"/other","sessions":["s1"]}`)
	write(t, dir, "brook.json", `{"drive":"brook","state_dir":"/scratch/brook","sessions":["s2"]}`)
	entry, err := Named("brook")
	if err != nil || entry.StateDir != "/scratch/brook" {
		t.Fatalf("entry = %+v, %v", entry, err)
	}
	if entry, err := Named("aaa"); err != nil || entry.StateDir != "/other" {
		t.Fatalf("by id: %+v, %v", entry, err)
	}
}

func TestASavedSubDriveReadsBackAsItsParentsChildUntilRemoved(t *testing.T) {
	t.Setenv("CLAUDE_CONFIG_DIR", t.TempDir())
	child := Entry{Drive: "brook--billing", CCIDrive: "brook--billing", StateDir: "/s", Sessions: []string{}, Parent: "brook", Reason: "unrelated", Facts: map[string]string{"no-shared-prs": "none"}}
	for _, entry := range []Entry{child, {Drive: "reef--ops", Sessions: []string{}, Parent: "reef"}, {Drive: "brook", Sessions: []string{"s1"}}} {
		if err := Save(entry); err != nil {
			t.Fatal(err)
		}
	}

	found, err := Children("brook")
	if err != nil {
		t.Fatal(err)
	}
	if len(found) != 1 || found[0].Drive != child.Drive || found[0].Facts["no-shared-prs"] != "none" {
		t.Fatalf("children = %+v", found)
	}

	if err := Remove(child.Drive); err != nil {
		t.Fatal(err)
	}
	if found, err := Children("brook"); err != nil || len(found) != 0 {
		t.Fatalf("after remove: %+v, %v", found, err)
	}
}

func TestAResumedWindowFindsItsDriveAndLeavesItsSessionsAlone(t *testing.T) {
	home := t.TempDir()
	t.Setenv("CLAUDE_CONFIG_DIR", home)
	t.Setenv("CLAUDE_CODE_SESSION_ID", "boot-id-of-the-mcp-server")
	const (
		pid          = 45444
		communicator = "communicator-session"
		coordinator  = "coordinator-session"
	)
	drives := filepath.Join(home, "long-running", "drives")
	if err := os.MkdirAll(drives, 0o700); err != nil {
		t.Fatal(err)
	}
	registry := filepath.Join(drives, "drive.json")
	body := `{"drive":"drive","sessions":["` + communicator + `","` + coordinator + `"]}`
	if err := os.WriteFile(registry, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	sessions := filepath.Join(home, "sessions")
	if err := os.MkdirAll(sessions, 0o700); err != nil {
		t.Fatal(err)
	}
	window := `{"pid":45444,"sessionId":"` + communicator + `"}`
	if err := os.WriteFile(filepath.Join(sessions, "45444.json"), []byte(window), 0o600); err != nil {
		t.Fatal(err)
	}

	entry, err := HoldingWindow(pid)
	if err != nil {
		t.Fatal(err)
	}
	if entry.Drive != "drive" {
		t.Fatalf("drive = %q, want the drive holding the window's session", entry.Drive)
	}
	if entry.Root() != coordinator {
		t.Fatalf("root = %q, want the coordinator still last", entry.Root())
	}
	after, err := os.ReadFile(registry)
	if err != nil {
		t.Fatal(err)
	}
	if string(after) != body {
		t.Fatalf("registry = %s, want it untouched", after)
	}
}

func TestAWindowWithNoSessionFileIsAnError(t *testing.T) {
	t.Setenv("CLAUDE_CONFIG_DIR", t.TempDir())
	if _, err := HoldingWindow(45444); err == nil {
		t.Fatal("want an error naming the window")
	}
}

func TestASessionFindsOnlyTheDriveItCarriesAndNoDriveIsOnePlainRefusal(t *testing.T) {
	home := t.TempDir()
	t.Setenv("CLAUDE_CONFIG_DIR", home)
	dir := filepath.Join(home, "long-running", "drives")
	if err := os.MkdirAll(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	write(t, dir, "brook.json", `{"drive":"brook","state_dir":"/scratch/brook","sessions":["s1"]}`)
	write(t, dir, "reef.json", `{"drive":"reef","state_dir":"/scratch/reef","sessions":["s2"]}`)
	t.Setenv(Env, "reef")
	entry, err := Named("")
	if err != nil || entry.Config() != "/scratch/reef/lr.json" {
		t.Fatalf("session drive = %+v, %v", entry, err)
	}
	if entry, err := Named("brook"); err != nil || entry.Drive != "brook" {
		t.Fatalf("named drive = %+v, %v", entry, err)
	}
	t.Setenv(Env, "")
	if _, err := Session(); !errors.Is(err, ErrNoDrive) || strings.Contains(err.Error(), "\n") {
		t.Fatalf("a session in no drive = %v", err)
	}
}

func TestTheDriveCCIBindsIsTheDriveWordOfItsMeLine(t *testing.T) {
	for me, want := range map[string]string{
		"monorepo-8a lane drive=durable-agents-rebuild lane=root branch=dev busy now session=e0ae64c1\n": "durable-agents-rebuild",
		"monorepo-8a lane lane=root branch=dev busy now\n":                                               "",
		"": "",
	} {
		if got := BoundInCCI(me); got != want {
			t.Errorf("BoundInCCI(%q) = %q, want %q", me, got, want)
		}
	}
}
