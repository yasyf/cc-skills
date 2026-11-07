package board

import (
	"context"
	"encoding/json"
	"regexp"
	"slices"
	"strconv"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/cardtext"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

func TestWords(ctx context.Context, runner shell.Runner, read ccx.Client, checkout, repo string, number int, head, text string) string {
	ids := slices.Compact(slices.Sorted(slices.Values(cardtext.CorpusRow.FindAllString(text, -1))))
	if len(ids) == 0 {
		return text
	}
	var changed struct {
		Files []struct {
			Path string `json:"path"`
		} `json:"files"`
	}
	if view, err := read.GH(ctx, ccx.MaxAgeCached, "pr", "view", strconv.Itoa(number), "--repo", repo, "--json", "files"); err == nil {
		_ = json.Unmarshal([]byte(view), &changed)
	}
	files := make([]string, 0, len(changed.Files))
	for _, file := range changed.Files {
		files = append(files, file.Path)
	}
	pattern := `(` + strings.Join(ids, "|") + `): ([^"` + "`" + `]+)`
	named := regexp.MustCompile(pattern)
	checks := map[string]string{}
	for _, scope := range [][]string{files, nil} {
		if len(scope) == 0 && scope != nil {
			continue
		}
		argv := append([]string{"git", "-C", checkout, "grep", "-h", "-o", "-E", pattern, head, "--"}, scope...)
		done, err := runner.Run(ctx, shell.Command{Argv: argv})
		if err != nil {
			continue
		}
		for _, line := range strings.Split(done.Stdout, "\n") {
			if found := named.FindStringSubmatch(line); found != nil && checks[found[1]] == "" {
				checks[found[1]] = strings.TrimRight(strings.TrimSpace(found[2]), ",.;")
			}
		}
	}
	return cardtext.Described(text, checks)
}
