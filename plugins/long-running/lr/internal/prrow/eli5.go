package prrow

import (
	"cmp"
	"context"
	"encoding/json"
	"fmt"
	"regexp"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const companionMarker = "<!-- pr-reviewer bot companion -->"

var eli5Section = regexp.MustCompile(`(?s)### ELI5[ \t]*\n(.*?)(?:\n### |\n!\[|\n<sub>|\z)`)

// ELI5Words is the PR card's plain-words line: the reviewer bot's ELI5, or
// the body's Context section until the bot posts one.
func ELI5Words(row store.ReviewRow) string {
	return cmp.Or(row.ELI5, section(row.Description, "Context"))
}

// ELI5Of is the ELI5 section of a reviewer bot companion comment, or "" for
// any other comment.
func ELI5Of(comment string) string {
	if !strings.Contains(comment, companionMarker) {
		return ""
	}
	found := eli5Section.FindStringSubmatch(comment)
	if found == nil {
		return ""
	}
	return strings.TrimSpace(found[1])
}

// ReadELI5 reads the reviewer bot's ELI5 for each of numbers in repo with one
// GitHub call, keyed by PR number; a PR the bot has not explained is absent.
func ReadELI5(ctx context.Context, runner shell.Runner, dir, repo string, numbers []int) (map[int]string, error) {
	owner, name, _ := strings.Cut(repo, "/")
	var query strings.Builder
	fmt.Fprintf(&query, "query{repository(owner:%q,name:%q){", owner, name)
	for _, n := range numbers {
		fmt.Fprintf(&query, "pr%d:pullRequest(number:%d){comments(last:50){nodes{body}}}", n, n)
	}
	query.WriteString("}}")
	res, err := runner.Run(ctx, shell.Command{Argv: []string{"gh", "api", "graphql", "-f", "query=" + query.String()}, Dir: dir})
	if err != nil {
		return nil, err
	}
	if res.Code != 0 {
		return nil, fmt.Errorf("gh api graphql: %s", strings.TrimSpace(res.Stderr))
	}
	var read struct {
		Data struct {
			Repository map[string]*struct {
				Comments struct {
					Nodes []struct {
						Body string `json:"body"`
					} `json:"nodes"`
				} `json:"comments"`
			} `json:"repository"`
		} `json:"data"`
	}
	if err := json.Unmarshal([]byte(res.Stdout), &read); err != nil {
		return nil, fmt.Errorf("decode gh api graphql: %w", err)
	}
	found := map[int]string{}
	for _, n := range numbers {
		pr := read.Data.Repository[fmt.Sprintf("pr%d", n)]
		if pr == nil {
			continue
		}
		for at := len(pr.Comments.Nodes) - 1; at >= 0; at-- {
			if said := ELI5Of(pr.Comments.Nodes[at].Body); said != "" {
				found[n] = said
				break
			}
		}
	}
	return found, nil
}
