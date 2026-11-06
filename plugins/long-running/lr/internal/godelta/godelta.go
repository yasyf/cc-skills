package godelta

import (
	"context"
	"fmt"
	"go/ast"
	"go/token"
	"go/types"
	"maps"
	"os"
	"path"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"golang.org/x/tools/go/packages"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/wait"
)

const (
	undefined   = "undefined: "
	newerGoList = "This application uses version go1."
	loadMode    = packages.NeedName | packages.NeedFiles | packages.NeedCompiledGoFiles | packages.NeedImports | packages.NeedTypes | packages.NeedSyntax | packages.NeedTypesInfo
)

type Finding struct {
	At      string
	Message string
}

func (f Finding) String() string {
	return f.At + ": " + f.Message
}

type parsed struct {
	pkg       *packages.Package
	selectors map[token.Pos]*ast.SelectorExpr
	imports   []*ast.ImportSpec
	names     map[*token.File][]string
}

type Result struct {
	Findings []Finding
	Skipped  int
	seen     map[Finding]bool
}

func Command(a *app.App) *cobra.Command {
	var base string
	cmd := &cobra.Command{
		Use:   "go-delta [--base REF]",
		Short: "Typecheck the Go packages this checkout changed, with their tests, past the generated sandsql/component and sand/proto code a sparse lane or Sprite lacks",
		Args:  cobra.NoArgs,
		RunE: func(c *cobra.Command, _ []string) error {
			started := time.Now()
			ctx := c.Context()
			top, err := git(ctx, a.Runner, "", "rev-parse", "--show-toplevel")
			if err != nil {
				return err
			}
			fork, err := git(ctx, a.Runner, top, "merge-base", "HEAD", base)
			if err != nil {
				return err
			}
			dirs, err := touched(ctx, a.Runner, top, fork)
			if err != nil {
				return err
			}
			if len(dirs) == 0 {
				fmt.Fprintf(c.ErrOrStderr(), "go-delta: no Go package changed against %.10s\n", fork)
				return nil
			}
			result, err := Check(ctx, top, dirs)
			if err != nil {
				return err
			}
			for _, finding := range result.Findings {
				fmt.Fprintln(c.OutOrStdout(), finding)
			}
			fmt.Fprintf(c.ErrOrStderr(), "go-delta: %d errors in %d packages against %.10s, %d from missing generated code left out, %.1fs\n", len(result.Findings), len(dirs), fork, result.Skipped, time.Since(started).Seconds())
			if len(result.Findings) > 0 {
				return &wait.ExitError{Code: 1}
			}
			return nil
		},
	}
	cmd.Flags().StringVar(&base, "base", "origin/dev", "diff against the merge base of HEAD and this ref")
	return cmd
}

func git(ctx context.Context, runner shell.Runner, dir string, args ...string) (string, error) {
	result, err := runner.Run(ctx, shell.Command{Argv: append([]string{"git"}, args...), Dir: dir})
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(result.Stdout), nil
}

func touched(ctx context.Context, runner shell.Runner, top, fork string) ([]string, error) {
	changed, err := git(ctx, runner, top, "diff", "--name-only", "-z", fork)
	if err != nil {
		return nil, err
	}
	untracked, err := git(ctx, runner, top, "ls-files", "--others", "--exclude-standard", "-z")
	if err != nil {
		return nil, err
	}
	var dirs []string
	for _, path := range strings.Split(changed+"\x00"+untracked, "\x00") {
		dir := filepath.Dir(path)
		if filepath.Ext(path) != ".go" || slices.Contains(dirs, dir) || slices.ContainsFunc(strings.Split(dir, "/"), func(part string) bool { return part == "testdata" || part == "vendor" }) {
			continue
		}
		sources, err := filepath.Glob(filepath.Join(top, dir, "*.go"))
		if err != nil {
			return nil, err
		}
		if len(sources) > 0 {
			dirs = append(dirs, dir)
		}
	}
	return dirs, nil
}

func Check(ctx context.Context, top string, dirs []string) (Result, error) {
	modules := map[string][]string{}
	for _, dir := range dirs {
		absolute := filepath.Join(top, dir)
		module := absolute
		for {
			if _, err := os.Stat(filepath.Join(module, "go.mod")); err == nil {
				break
			}
			if module == top {
				return Result{}, fmt.Errorf("%s has no go.mod in this checkout", dir)
			}
			module = filepath.Dir(module)
		}
		modules[module] = append(modules[module], absolute)
	}
	result := Result{seen: map[Finding]bool{}}
	for _, module := range slices.Sorted(maps.Keys(modules)) {
		loaded, err := packages.Load(&packages.Config{Context: ctx, Dir: module, Mode: loadMode, Tests: true}, modules[module]...)
		if err != nil {
			return result, err
		}
		for _, pkg := range loaded {
			result.add(top, pkg)
		}
	}
	return result, nil
}

func (r *Result) add(top string, pkg *packages.Package) {
	source := parsed{pkg: pkg, selectors: map[token.Pos]*ast.SelectorExpr{}, names: map[*token.File][]string{}}
	for _, file := range pkg.Syntax {
		for _, spec := range file.Imports {
			imported, err := strconv.Unquote(spec.Path.Value)
			if err != nil || !generated(imported) {
				continue
			}
			source.imports = append(source.imports, spec)
			local := path.Base(imported)
			if spec.Name != nil {
				local = spec.Name.Name
			}
			source.names[pkg.Fset.File(file.Pos())] = append(source.names[pkg.Fset.File(file.Pos())], local)
		}
		ast.Inspect(file, func(node ast.Node) bool {
			if selector, ok := node.(*ast.SelectorExpr); ok {
				source.selectors[selector.Sel.Pos()] = selector
			}
			return true
		})
	}
	for _, err := range pkg.TypeErrors {
		position := err.Fset.Position(err.Pos)
		if rel, relErr := filepath.Rel(top, position.Filename); relErr == nil {
			position.Filename = rel
		}
		finding := Finding{At: position.String(), Message: err.Msg}
		if !source.fromGenerated(err) {
			r.report(finding)
			continue
		}
		if !r.seen[finding] {
			r.seen[finding] = true
			r.Skipped++
		}
	}
	for _, err := range pkg.Errors {
		if err.Kind == packages.TypeError || err.Kind == packages.ListError && strings.HasPrefix(err.Msg, "# ") || err.Kind == packages.UnknownError && strings.HasPrefix(err.Msg, newerGoList) {
			continue
		}
		at := strings.TrimPrefix(err.Pos, top+string(filepath.Separator))
		if at == "" {
			at = pkg.PkgPath
		}
		r.report(Finding{At: at, Message: err.Msg})
	}
}

func (r *Result) report(finding Finding) {
	if r.seen[finding] {
		return
	}
	r.seen[finding] = true
	r.Findings = append(r.Findings, finding)
}

func (p parsed) fromGenerated(err types.Error) bool {
	if slices.ContainsFunc(p.imports, func(spec *ast.ImportSpec) bool { return spec.Pos() <= err.Pos && err.Pos < spec.End() }) {
		return true
	}
	name, missing := strings.CutPrefix(err.Msg, undefined)
	selector := p.selectors[err.Pos]
	if selector == nil {
		if !missing {
			return false
		}
		local := strings.Fields(name)[0]
		return generated(p.pkg.PkgPath) && !strings.Contains(local, ".") || slices.Contains(p.names[err.Fset.File(err.Pos)], local)
	}
	if missing {
		qualifier, ok := selector.X.(*ast.Ident)
		imported, isPackage := p.pkg.TypesInfo.Uses[qualifier].(*types.PkgName)
		return ok && isPackage && generated(imported.Imported().Path())
	}
	receiver := p.pkg.TypesInfo.TypeOf(selector.X)
	return strings.Contains(err.Msg, " undefined (") && receiver != nil && embedsGenerated(receiver, map[*types.Struct]bool{})
}

func generated(importPath string) bool {
	return strings.HasSuffix(importPath, "/sandsql/component") || importPath == "sand/proto"
}

func embedsGenerated(typ types.Type, seen map[*types.Struct]bool) bool {
	if pointer, ok := typ.Underlying().(*types.Pointer); ok {
		typ = pointer.Elem()
	}
	fields, ok := typ.Underlying().(*types.Struct)
	if !ok || seen[fields] {
		return false
	}
	seen[fields] = true
	for field := range fields.Fields() {
		if !field.Embedded() {
			continue
		}
		embedded := field.Type()
		if pointer, ok := embedded.(*types.Pointer); ok {
			embedded = pointer.Elem()
		}
		if embedded == types.Typ[types.Invalid] && (generated(field.Pkg().Path()) || slices.ContainsFunc(field.Pkg().Imports(), func(imported *types.Package) bool { return generated(imported.Path()) })) || embedsGenerated(field.Type(), seen) {
			return true
		}
	}
	return false
}
