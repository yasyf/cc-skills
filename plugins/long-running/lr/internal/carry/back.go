package carry

import (
	"context"
	"fmt"
	"path"
	"regexp"
	"slices"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	SpriteHome  = "/home/sprite"
	Box         = SpriteHome + "/drive/carried"
	installLink = ".claude/long-running/current"
)

var mirrored = regexp.MustCompile(regexp.QuoteMeta(Box) + `/[^` + pathStop + `]+`)

func mirrors(said []string) []string {
	var paths []string
	for _, words := range said {
		for _, named := range mirrored.FindAllString(words, -1) {
			if named = path.Clean(strings.TrimRight(named, ".")); !slices.Contains(paths, named) {
				paths = append(paths, named)
			}
		}
	}
	return paths
}

func Back(ctx context.Context, runner shell.Runner, sprite, home string, said []string) []string {
	var left []string
	for _, mirror := range mirrors(said) {
		rel, under := strings.CutPrefix(strings.TrimPrefix(mirror, Box), home+"/")
		if !under || !slices.ContainsFunc(sharedRoots, func(root string) bool { return rel == root || strings.HasPrefix(rel, root+"/") }) {
			left = append(left, mirror+" (no directory carry shares with the Mac holds it)")
			continue
		}
		packed, err := runner.Run(ctx, shell.Command{Argv: []string{"sprite", "exec", "-s", sprite, "--no-stdin", "--", "tar", "-C", Box + home, "-czf", "-", rel}})
		if err == nil {
			_, err = runner.Run(ctx, shell.Command{Argv: []string{"tar", "-C", home, "-xzkf", "-"}, Stdin: packed.Stdout})
		}
		if err != nil {
			left = append(left, fmt.Sprintf("%s (its copy from Sprite %s failed: %v)", mirror, sprite, err))
		}
	}
	return left
}
