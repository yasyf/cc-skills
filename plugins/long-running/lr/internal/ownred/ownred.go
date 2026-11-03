package ownred

import (
	"cmp"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	OwnLine       = "OWN-RED"
	InheritedLine = "INHERITED"
	readTimeout   = 60 * time.Second
	workflowPath  = ".github"
	actionsApp    = "github-actions"
	notFound      = "HTTP 404"
	allowEscapes  = "--allow-escape-sequences"
	checksFields  = `fragment checks on PullRequest {
  files(first: 100) { nodes { path } }
  commits(last: 1) { nodes { commit { checkSuites(first: 50) { nodes {
    app { slug }
    checkRuns(first: 50) { nodes { name status conclusion databaseId annotations(first: 50) { nodes { path location { start { line } } message } } } }
  } } } } }
}`
)

type Mode int

const (
	One Mode = iota
	Below
	Stack
)

var (
	pullURL  = regexp.MustCompile(`^https://github\.com/([\w.-]+/[\w.-]+)/pull/\d+$`)
	stackURL = regexp.MustCompile(`/repos/([\w.-]+/[\w.-]+)/stacks/\d+$`)
	location = regexp.MustCompile(`([\w.@+-]+(?:/[\w.@+-]+)*\.\w+):(\d+)`)
	logStamp = regexp.MustCompile(`^\S+Z (?:##\[\w+\])?`)
	ansi     = regexp.MustCompile(`\x1b\[[0-9;]*[A-Za-z]`)
)

type Merge struct {
	Repo     string
	Selector string
	Mode     Mode
}

type Guard struct {
	Runner shell.Runner
}

type stack struct {
	URL   string `json:"url"`
	Pulls []struct {
		Number int    `json:"number"`
		State  string `json:"state"`
	} `json:"pull_requests"`
}

type checkRun struct {
	Name        string `json:"name"`
	Status      string `json:"status"`
	Conclusion  string `json:"conclusion"`
	DatabaseID  int64  `json:"databaseId"`
	Annotations struct {
		Nodes []struct {
			Path     string `json:"path"`
			Location struct {
				Start struct {
					Line int `json:"line"`
				} `json:"start"`
			} `json:"location"`
			Message string `json:"message"`
		} `json:"nodes"`
	} `json:"annotations"`
}

type pull struct {
	Files struct {
		Nodes []struct {
			Path string `json:"path"`
		} `json:"nodes"`
	} `json:"files"`
	Commits struct {
		Nodes []struct {
			Commit struct {
				CheckSuites struct {
					Nodes []struct {
						App struct {
							Slug string `json:"slug"`
						} `json:"app"`
						CheckRuns struct {
							Nodes []checkRun `json:"nodes"`
						} `json:"checkRuns"`
					} `json:"nodes"`
				} `json:"checkSuites"`
			} `json:"commit"`
		} `json:"nodes"`
	} `json:"commits"`
}

type finding struct {
	path string
	line int
	text string
}

func (g Guard) Lines(ctx context.Context, m Merge) ([]string, error) {
	repo, numbers, err := g.merged(ctx, m)
	if err != nil {
		return nil, err
	}
	pulls, err := g.checks(ctx, repo, numbers)
	if err != nil {
		return nil, err
	}
	var lines []string
	seen := map[string]bool{}
	add := func(line string) {
		if !seen[line] {
			seen[line] = true
			lines = append(lines, line)
		}
	}
	for _, number := range numbers {
		p := pulls["p"+strconv.Itoa(number)]
		files := make([]string, 0, len(p.Files.Nodes))
		for _, node := range p.Files.Nodes {
			files = append(files, node.Path)
		}
		slices.Sort(files)
		for _, suite := range p.Commits.Nodes[0].Commit.CheckSuites.Nodes {
			for _, run := range suite.CheckRuns.Nodes {
				if run.Status != "COMPLETED" || run.Conclusion != "FAILURE" {
					continue
				}
				found := annotated(run)
				if len(found) == 0 && suite.App.Slug == actionsApp {
					if found, err = g.logged(ctx, repo, run.DatabaseID); err != nil {
						return nil, err
					}
				}
				own := false
				for _, f := range found {
					if file := changed(f.path, files); file != "" {
						own = true
						add(fmt.Sprintf("%s #%d %s %s:%d %s", OwnLine, number, run.Name, file, f.line, f.text))
					}
				}
				if !own {
					add(fmt.Sprintf("%s #%d %s", InheritedLine, number, run.Name))
				}
			}
		}
	}
	return lines, nil
}

func (g Guard) merged(ctx context.Context, m Merge) (string, []int, error) {
	if number, err := strconv.Atoi(m.Selector); m.Mode == Stack && err == nil {
		var s stack
		if err = g.read(ctx, &s, "gh", "api", fmt.Sprintf("repos/%s/stacks/%d", cmp.Or(m.Repo, "{owner}/{repo}"), number)); err == nil {
			return stackURL.FindStringSubmatch(s.URL)[1], s.open(0), nil
		}
		var exit *shell.ExitError
		if !errors.As(err, &exit) || !strings.Contains(exit.Stderr, notFound) {
			return "", nil, err
		}
	}
	argv := []string{"gh", "pr", "view"}
	if m.Selector != "" {
		argv = append(argv, m.Selector)
	}
	if m.Repo != "" {
		argv = append(argv, "--repo", m.Repo)
	}
	var pr struct {
		Number int    `json:"number"`
		URL    string `json:"url"`
	}
	if err := g.read(ctx, &pr, append(argv, "--json", "number,url")...); err != nil {
		return "", nil, err
	}
	repo := pullURL.FindStringSubmatch(pr.URL)[1]
	if m.Mode == One {
		return repo, []int{pr.Number}, nil
	}
	var stacks []stack
	if err := g.read(ctx, &stacks, "gh", "api", fmt.Sprintf("repos/%s/stacks?pull_request=%d", repo, pr.Number)); err != nil {
		return "", nil, err
	}
	if len(stacks) == 0 {
		return repo, []int{pr.Number}, nil
	}
	if m.Mode == Stack && m.Selector == "" {
		return repo, stacks[0].open(0), nil
	}
	return repo, stacks[0].open(pr.Number), nil
}

func (s stack) open(through int) []int {
	numbers := make([]int, 0, len(s.Pulls))
	for _, p := range s.Pulls {
		if p.State == "open" {
			numbers = append(numbers, p.Number)
		}
		if p.Number == through {
			break
		}
	}
	return numbers
}

func (g Guard) checks(ctx context.Context, repo string, numbers []int) (map[string]pull, error) {
	owner, name, _ := strings.Cut(repo, "/")
	var fields strings.Builder
	for _, number := range numbers {
		fmt.Fprintf(&fields, "p%d: pullRequest(number: %d) { ...checks } ", number, number)
	}
	query := fmt.Sprintf("query($owner: String!, $name: String!) { repository(owner: $owner, name: $name) { %s} }\n%s", fields.String(), checksFields)
	var reply struct {
		Data struct {
			Repository map[string]pull `json:"repository"`
		} `json:"data"`
	}
	if err := g.read(ctx, &reply, "gh", "api", "graphql", "-f", "query="+query, "-f", "owner="+owner, "-f", "name="+name); err != nil {
		return nil, err
	}
	return reply.Data.Repository, nil
}

func annotated(run checkRun) []finding {
	found := make([]finding, 0, len(run.Annotations.Nodes))
	for _, note := range run.Annotations.Nodes {
		if note.Path != workflowPath {
			text, _, _ := strings.Cut(note.Message, "\n")
			found = append(found, finding{note.Path, note.Location.Start.Line, text})
		}
	}
	return found
}

func (g Guard) logged(ctx context.Context, repo string, job int64) ([]finding, error) {
	usage, err := g.Runner.Run(ctx, shell.Command{Argv: []string{"gh", "api", "--help"}, Timeout: readTimeout})
	if err != nil {
		return nil, err
	}
	argv := []string{"gh", "api", fmt.Sprintf("repos/%s/actions/jobs/%d/logs", repo, job)}
	if strings.Contains(usage.Stdout, allowEscapes) {
		argv = append(argv, allowEscapes)
	}
	done, err := g.Runner.Run(ctx, shell.Command{Argv: argv, Timeout: readTimeout})
	if err != nil {
		return nil, err
	}
	var found []finding
	for line := range strings.SplitSeq(ansi.ReplaceAllString(done.Stdout, ""), "\n") {
		text := strings.TrimSpace(logStamp.ReplaceAllString(line, ""))
		for _, match := range location.FindAllStringSubmatch(line, -1) {
			number, _ := strconv.Atoi(match[2])
			found = append(found, finding{match[1], number, text})
		}
	}
	return found, nil
}

func changed(path string, files []string) string {
	for _, file := range files {
		if strings.HasSuffix("/"+path, "/"+file) || strings.HasSuffix("/"+file, "/"+path) {
			return file
		}
	}
	return ""
}

func (g Guard) read(ctx context.Context, into any, argv ...string) error {
	done, err := g.Runner.Run(ctx, shell.Command{Argv: argv, Timeout: readTimeout})
	if err != nil {
		return err
	}
	return json.Unmarshal([]byte(done.Stdout), into)
}
