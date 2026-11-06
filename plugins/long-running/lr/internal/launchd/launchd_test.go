package launchd

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestThePlistPathDropsDirectoriesInsideALaneWorktree(t *testing.T) {
	root := t.TempDir()
	checkout, lane, plain := filepath.Join(root, "checkout"), filepath.Join(root, "lane"), filepath.Join(root, "plain", "bin")
	for _, dir := range []string{filepath.Join(checkout, ".git"), filepath.Join(checkout, "tools", "bin"), filepath.Join(lane, "tools", "bin"), plain} {
		if err := os.MkdirAll(dir, 0o750); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.WriteFile(filepath.Join(lane, ".git"), []byte("gitdir: "+filepath.Join(checkout, ".git", "worktrees", "lane")+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	path := strings.Join([]string{filepath.Join(lane, "tools", "bin"), filepath.Join(checkout, "tools", "bin"), plain, lane, "/usr/bin"}, string(os.PathListSeparator))

	body := string(Job{Label: "l", Env: [][2]string{{"PATH", path}, {"OTHER", lane}}}.Plist())

	want := "<key>PATH</key>\n\t\t<string>" + strings.Join([]string{filepath.Join(checkout, "tools", "bin"), plain, "/usr/bin"}, string(os.PathListSeparator)) + "</string>"
	if !strings.Contains(body, want) {
		t.Fatalf("%q not in\n%s", want, body)
	}
	if want := "<key>OTHER</key>\n\t\t<string>" + lane + "</string>"; !strings.Contains(body, want) {
		t.Fatalf("%q not in\n%s", want, body)
	}
}
