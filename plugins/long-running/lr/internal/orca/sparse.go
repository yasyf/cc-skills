package orca

import (
	"context"
	"errors"
	"fmt"
	"path"
	"path/filepath"
	"slices"
	"strings"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"golang.org/x/mod/modfile"
)

func SparseCommand(a *app.App) *cobra.Command {
	cmd := &cobra.Command{Use: "sparse", Short: "Widen a sparse checkout so what it touches can build"}
	cmd.AddCommand(&cobra.Command{
		Use:   "add DIR...",
		Short: "Add repo-relative directories to this checkout's sparse set, each widened to its Go module with that module's local replace targets, plus the tracked .agents and .claude and each ccx.sparseInclude directory the set lacks, as ccx vcs worktree add checks out",
		Args:  cobra.MinimumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			shown, err := a.Runner.Run(cmd.Context(), shell.Command{Argv: []string{"git", "rev-parse", "--show-toplevel"}})
			if err != nil {
				return err
			}
			top := strings.TrimSpace(shown.Stdout)
			sparse, err := a.Runner.Run(cmd.Context(), shell.Command{Argv: []string{"git", "config", "--type=bool", "--default=false", "core.sparseCheckout"}, Dir: top})
			if err != nil {
				return err
			}
			if strings.TrimSpace(sparse.Stdout) != "true" {
				fmt.Fprintf(cmd.OutOrStdout(), "sparse: a full checkout already holds %s\n", strings.Join(args, " "))
				return nil
			}
			dirs := withGoModules(args, func(goMod string) string {
				read, err := a.Runner.Run(cmd.Context(), shell.Command{Argv: []string{"git", "show", "HEAD:" + goMod}, Dir: top})
				if err != nil {
					return ""
				}
				return read.Stdout
			})
			lane, err := missingLaneDirs(cmd.Context(), a.Runner, top)
			if err != nil {
				return err
			}
			if _, err := a.Runner.Run(cmd.Context(), shell.Command{Argv: []string{"git", "sparse-checkout", "add", "--stdin"}, Dir: top, Stdin: strings.Join(slices.Concat(dirs, lane), "\n") + "\n"}); err != nil {
				return err
			}
			said := "sparse: added " + strings.Join(dirs, " ")
			if len(lane) > 0 {
				said += " and the lane directories " + strings.Join(lane, " ")
			}
			fmt.Fprintln(cmd.OutOrStdout(), said)
			return nil
		},
	})
	return cmd
}

func missingLaneDirs(ctx context.Context, runner shell.Runner, top string) ([]string, error) {
	included, err := runner.Run(ctx, shell.Command{Argv: []string{"git", "config", "--get-all", "ccx.sparseInclude"}, Dir: top})
	var unset *shell.ExitError
	if err != nil && (!errors.As(err, &unset) || unset.Code != 1) {
		return nil, err
	}
	tracked, err := runner.Run(ctx, shell.Command{Argv: slices.Concat([]string{"git", "ls-tree", "-d", "--name-only", "HEAD", "--", ".agents", ".claude"}, strings.Fields(included.Stdout)), Dir: top})
	if err != nil {
		return nil, err
	}
	listed, err := runner.Run(ctx, shell.Command{Argv: []string{"git", "sparse-checkout", "list"}, Dir: top})
	if err != nil {
		return nil, err
	}
	held := strings.Fields(listed.Stdout)
	var missing []string
	for _, dir := range strings.Fields(tracked.Stdout) {
		if !slices.ContainsFunc(held, func(cone string) bool { return dir == cone || strings.HasPrefix(dir, cone+"/") }) {
			missing = append(missing, dir)
		}
	}
	return missing, nil
}

func withGoModules(dirs []string, goMod func(path string) string) []string {
	closure := make([]string, 0, len(dirs))
	for _, dir := range dirs {
		if root := moduleRoot(path.Clean(dir), goMod); !slices.Contains(closure, root) {
			closure = append(closure, root)
		}
	}
	for at := 0; at < len(closure); at++ {
		parsed, err := modfile.Parse("go.mod", []byte(goMod(path.Join(closure[at], "go.mod"))), func(_, version string) (string, error) { return version, nil })
		if err != nil {
			continue
		}
		for _, replace := range parsed.Replace {
			if replace.New.Version != "" || path.IsAbs(replace.New.Path) {
				continue
			}
			target := path.Join(closure[at], replace.New.Path)
			if filepath.IsLocal(target) && !slices.Contains(closure, target) {
				closure = append(closure, target)
			}
		}
	}
	return closure
}

func moduleRoot(dir string, goMod func(path string) string) string {
	for at := dir; at != "." && at != "/"; at = path.Dir(at) {
		if goMod(path.Join(at, "go.mod")) != "" {
			return at
		}
	}
	return dir
}
