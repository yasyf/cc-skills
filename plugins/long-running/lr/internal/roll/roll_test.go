package roll

import (
	"context"
	"os"
	"path/filepath"
	"testing"
)

func TestTakingEndsWhenTheInstallLinkMovesToAnotherVersionAndNotWhenItIsRepointedAtTheSameOne(t *testing.T) {
	claude, versions := t.TempDir(), t.TempDir()
	if err := os.MkdirAll(filepath.Dir(link(claude)), 0o750); err != nil {
		t.Fatal(err)
	}
	point := func(version string) {
		if err := os.MkdirAll(filepath.Join(versions, version), 0o750); err != nil {
			t.Fatal(err)
		}
		staged := link(claude) + ".staged"
		if err := os.Symlink(filepath.Join(versions, version), staged); err != nil {
			t.Fatal(err)
		}
		if err := os.Rename(staged, link(claude)); err != nil {
			t.Fatal(err)
		}
	}
	point("0.7.1")
	taking := follow(context.Background(), claude, "0.7.1")

	point("0.7.1")
	point("0.7.2")

	<-taking.Done()
	if cause := context.Cause(taking); cause != (Moved{From: "0.7.1", To: "0.7.2"}) {
		t.Fatalf("taking ended with %v; only the move to 0.7.2 ends it", cause)
	}
}
