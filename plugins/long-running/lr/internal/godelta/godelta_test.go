package godelta

import (
	"path/filepath"
	"strings"
	"testing"

	"golang.org/x/tools/go/packages"
)

func check(t *testing.T, fixture string, dirs ...string) Result {
	t.Helper()
	top, err := filepath.Abs(filepath.Join("testdata", fixture))
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
	result := check(t, "mono", "clean", "internal/sandsql/component")
	if len(result.Findings) != 0 || result.Skipped == 0 {
		t.Fatalf("findings %v with %d left out; want none, with the generated names left out", result.Findings, result.Skipped)
	}
}

func TestCodeImportingTheMissingGeneratedSandProtoTypechecksClean(t *testing.T) {
	result := check(t, "sand", "cmd", "internal/uds")
	if len(result.Findings) != 0 || result.Skipped == 0 {
		t.Fatalf("findings %v with %d left out; want none, with the sand/proto errors left out", result.Findings, result.Skipped)
	}
}

func TestARealTypeErrorInATouchedFileIsReported(t *testing.T) {
	result := check(t, "mono", "broken")
	if len(result.Findings) != 1 || !strings.HasPrefix(result.Findings[0].At, "broken/broken.go:10:") || !strings.Contains(result.Findings[0].Message, `"three"`) {
		t.Fatalf("findings %v; want the string returned as an int at broken/broken.go:10", result.Findings)
	}
}

func TestANewerGoListAdvisoryOnAPackageWithOnlyGeneratedErrorsIsNotAFinding(t *testing.T) {
	result := Result{seen: map[Finding]bool{}}
	result.add("/repo", &packages.Package{PkgPath: "sand/next/internal/vm/builtins/tablepkg", Errors: []packages.Error{{
		Pos:  "-",
		Msg:  "This application uses version go1.26 of the source-processing packages but runs version go1.27 of 'go list'. It may fail to process source files that rely on newer language features. If so, rebuild the application using a newer version of Go.",
		Kind: packages.UnknownError,
	}}})
	if len(result.Findings) != 0 {
		t.Fatalf("findings %v; want none", result.Findings)
	}
}

func TestUsingComponentWithoutImportingItIsReported(t *testing.T) {
	result := check(t, "mono", "unimported")
	want := Finding{At: "unimported/unimported.go:3:17", Message: "undefined: component"}
	if len(result.Findings) != 1 || result.Findings[0] != want {
		t.Fatalf("findings %v; want only %v", result.Findings, want)
	}
}
