package wait

import (
	"os"
	"path/filepath"
	"testing"
)

func TestArmedForReadsItsRecordsPastALaneCursorInTheSameDirectory(t *testing.T) {
	t.Setenv("XDG_STATE_HOME", t.TempDir())
	if err := recordArmed(Armed{PID: os.Getpid(), Terminal: "term_a", Output: "/tmp/wait.out"}); err != nil {
		t.Fatal(err)
	}
	dir, err := armedDir()
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "passbox-zone.cursor"), []byte("fb8a2156-2c47-4bf8-8b33-402647d1a03d\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	armed, err := ArmedFor("term_a")
	if err != nil {
		t.Fatal(err)
	}
	if len(armed) != 1 || armed[0].PID != os.Getpid() {
		t.Fatalf("armed = %+v, want the one record for pid %d", armed, os.Getpid())
	}
}
