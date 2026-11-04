package orca

import (
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
		Short: "Add repo-relative directories to this checkout's sparse set, each widened to its Go module with that module's local replace targets",
		Args:  cobra.MinimumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			shown, err := a.Runner.Run(cmd.Context(), shell.Command{Argv: []string{"git", "rev-parse", "--show-toplevel"}})
			if err != nil {
				return err
			}
			top := strings.TrimSpace(shown.Stdout)
			dirs := withGoModules(args, func(goMod string) string {
				read, err := a.Runner.Run(cmd.Context(), shell.Command{Argv: []string{"git", "show", "HEAD:" + goMod}, Dir: top})
				if err != nil {
					return ""
				}
				return read.Stdout
			})
			if _, err := a.Runner.Run(cmd.Context(), shell.Command{Argv: []string{"git", "sparse-checkout", "add", "--stdin"}, Dir: top, Stdin: strings.Join(dirs, "\n") + "\n"}); err != nil {
				return err
			}
			fmt.Fprintf(cmd.OutOrStdout(), "sparse: added %s\n", strings.Join(dirs, " "))
			return nil
		},
	})
	return cmd
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
