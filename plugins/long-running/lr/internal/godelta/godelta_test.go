package godelta

import (
	"errors"
	"go/ast"
	"go/parser"
	"go/token"
	"go/types"
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

func TestAnUnchangedFileUsingAModuleTheCheckoutLacksIsLeftOut(t *testing.T) {
	result := check(t, "ci", ".")
	if len(result.Findings) != 0 || result.Skipped == 0 {
		t.Fatalf("findings %v with %d left out; want none, with the absent llmclient errors left out", result.Findings, result.Skipped)
	}
}

func TestAnAbsentModuleGoListNamedAfterAnImporterIsLeftOut(t *testing.T) {
	fset := token.NewFileSet()
	file, err := parser.ParseFile(fset, "/repo/go/ci/evalstriage.go", "package main\n\nimport \"llmclient\"\n\nvar url, key = llmclient.EnvURL, llmclient.EnvKey\n", 0)
	if err != nil {
		t.Fatal(err)
	}
	misnamed := types.NewPackage("llmclient", "ai")
	misnamed.MarkComplete()
	var typeErrors []types.Error
	info := &types.Info{Uses: map[*ast.Ident]types.Object{}, Types: map[ast.Expr]types.TypeAndValue{}}
	checker := types.Config{Importer: importerFunc(func(string) (*types.Package, error) { return misnamed, nil }), Error: func(err error) {
		var typeErr types.Error
		if errors.As(err, &typeErr) {
			typeErrors = append(typeErrors, typeErr)
		}
	}}
	checked, _ := checker.Check("ci", fset, []*ast.File{file}, info)
	if len(typeErrors) != 3 {
		t.Fatalf("type errors %v; want undefined llmclient twice and llmclient imported as ai", typeErrors)
	}
	result := Result{seen: map[Finding]bool{}}
	result.add("/repo", &packages.Package{PkgPath: "ci", Fset: fset, Syntax: []*ast.File{file}, Types: checked, TypesInfo: info, TypeErrors: typeErrors}, missing{"llmclient": true})
	if len(result.Findings) != 0 || result.Skipped != 3 {
		t.Fatalf("findings %v with %d left out; want none, with all three left out", result.Findings, result.Skipped)
	}
}

type importerFunc func(string) (*types.Package, error)

func (f importerFunc) Import(path string) (*types.Package, error) {
	return f(path)
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
	}}}, missing{})
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
