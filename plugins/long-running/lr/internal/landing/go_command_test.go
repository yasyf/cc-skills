package landing

import (
	"os"
	"path/filepath"
	"slices"
	"testing"
)

func TestTheGateCountsGoClassesBesideTheExemptGradesTheRunnerAdmitsAlone(t *testing.T) {
	path := filepath.Join(t.TempDir(), runnerFile)
	runner := `{"drive":"d","orca":{"briefs":{"repo":"o/r"},"desk_inbox":"i","run":"r"},"landing":{"go_classes":["class owner-pick"]}}`
	if err := os.WriteFile(path, []byte(runner), 0o600); err != nil {
		t.Fatal(err)
	}
	section, err := landingSection(path)
	if err != nil {
		t.Fatal(err)
	}
	if want := []string{"pure port, no interaction", "test-only or CI-only, no non-test code", "class owner-pick"}; !slices.Equal(section.ExemptGrades, want) {
		t.Fatalf("got %q, want %q", section.ExemptGrades, want)
	}
}
