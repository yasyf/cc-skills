package godelta

import (
	"path/filepath"
	"strings"
	"testing"
)

func check(t *testing.T, dirs ...string) Result {
	t.Helper()
	top, err := filepath.Abs(filepath.Join("testdata", "mono"))
	if err != nil {
		t.Fatal(err)
	}
	result, err := Check(t.Context(), top, dirs)
	if err != nil {
		t.Fatal(err)
	}
	return result
}

func TestCodeUsingTheMissingGeneratedComponentTypesTypechecksClean(t *testing.T) {
	result := check(t, "clean", "internal/sandsql/component")
	if len(result.Findings) != 0 || result.Skipped == 0 {
		t.Fatalf("findings %v with %d left out; want none, with the generated names left out", result.Findings, result.Skipped)
	}
}

func TestARealTypeErrorInATouchedFileIsReported(t *testing.T) {
	result := check(t, "broken")
	if len(result.Findings) != 1 || !strings.HasPrefix(result.Findings[0].At, "broken/broken.go:10:") || !strings.Contains(result.Findings[0].Message, `"three"`) {
		t.Fatalf("findings %v; want the string returned as an int at broken/broken.go:10", result.Findings)
	}
}

func TestUsingComponentWithoutImportingItIsReported(t *testing.T) {
	result := check(t, "unimported")
	want := Finding{At: "unimported/unimported.go:3:17", Message: "undefined: component"}
	if len(result.Findings) != 1 || result.Findings[0] != want {
		t.Fatalf("findings %v; want only %v", result.Findings, want)
	}
}
