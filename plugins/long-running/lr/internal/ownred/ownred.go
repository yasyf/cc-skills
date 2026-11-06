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

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/askdesk"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	OwnLine       = "OWN-RED"
	InheritedLine = "INHERITED"
	UnreadBase    = "BASE-UNREAD"
	UnreadLine    = "UNREAD"
	MailLine      = "MAIL"
	closed        = "closed"
	failed        = "FAILURE"
	passed        = "SUCCESS"
	wholeBudget   = "2147483647"
	mailShown     = 20
	mailCut       = "%s %d more after %s not shown; read them with cci tail --drive %s --reader %s --since %s --oldest"
	readTimeout   = 60 * time.Second
	notFound      = "HTTP 404"
	checksFields  = `fragment checks on PullRequest {
  commits(last: 1) { nodes { commit { checkSuites(first: 30) { nodes {
    workflowRun { workflow { name } }
    checkRuns(first: 50) { nodes { name status conclusion detailsUrl } }
  } } } } }
}`
	baseFields = `fragment base on PullRequest {
  commits(first: 1) { nodes { commit { parents(first: 1) { nodes { history(first: 30) { nodes { checkSuites(first: 30) { nodes {
    workflowRun { workflow { name } }
    checkRuns(first: 50) { nodes { name conclusion } }
  } } } } } } } } }
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
)

type Merge struct {
	Repo     string
	Selector string
	Mode     Mode
	Drive    string
	Lane     string
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
	Name       string `json:"name"`
	Status     string `json:"status"`
	Conclusion string `json:"conclusion"`
	DetailsURL string `json:"detailsUrl"`
}

type commit struct {
	CheckSuites struct {
		Nodes []struct {
			WorkflowRun struct {
				Workflow struct {
					Name string `json:"name"`
				} `json:"workflow"`
			} `json:"workflowRun"`
			CheckRuns struct {
				Nodes []checkRun `json:"nodes"`
			} `json:"checkRuns"`
		} `json:"nodes"`
	} `json:"checkSuites"`
	Parents struct {
		Nodes []struct {
			History struct {
				Nodes []commit `json:"nodes"`
			} `json:"history"`
		} `json:"nodes"`
	} `json:"parents"`
}

type pull struct {
	Commits struct {
		Nodes []struct {
			Commit commit `json:"commit"`
		} `json:"nodes"`
	} `json:"commits"`
}

func (p pull) baseConclusion(workflow, check string) string {
	for _, first := range p.Commits.Nodes {
		for _, parent := range first.Commit.Parents.Nodes {
			for _, ancestor := range parent.History.Nodes {
				var conclusions []string
				for _, suite := range ancestor.CheckSuites.Nodes {
					if suite.WorkflowRun.Workflow.Name != workflow {
						continue
					}
					for _, run := range suite.CheckRuns.Nodes {
						if run.Name == check {
							conclusions = append(conclusions, run.Conclusion)
						}
					}
				}
				for _, settled := range []string{failed, passed} {
					if slices.Contains(conclusions, settled) {
						return settled
					}
				}
			}
		}
	}
	return ""
}

type record struct {
	Seq          int64    `json:"seq"`
	Num          string   `json:"num"`
	Lane         string   `json:"lane"`
	Kind         string   `json:"kind"`
	Text         string   `json:"text"`
	To           []string `json:"to"`
	Status       string   `json:"status"`
	Re           int64    `json:"re"`
	Resolves     int64    `json:"resolves"`
	AlsoResolves []int64  `json:"also_resolves"`
}

func (r record) Name() string {
	return client.Record{Seq: r.Seq, Num: r.Num}.Name()
}

func (r record) line(kind string) string {
	return fmt.Sprintf("%s %s %s %s: %s", kind, r.Name(), r.Lane, r.Kind, strings.ReplaceAll(r.Text, "\n", " "))
}

type red struct {
	number   int
	workflow string
	check    string
	job      string
}

func (r red) line(base pull) string {
	switch base.baseConclusion(r.workflow, r.check) {
	case failed:
		return fmt.Sprintf("%s #%d %s", InheritedLine, r.number, r.check)
	case passed:
		return fmt.Sprintf("%s #%d %s %s", OwnLine, r.number, r.check, r.job)
	}
	return fmt.Sprintf("%s #%d %s", UnreadBase, r.number, r.check)
}

func (g Guard) Lines(ctx context.Context, m Merge) ([]string, error) {
	lines, err := g.mail(ctx, m)
	if err != nil {
		return nil, err
	}
	repo, numbers, err := g.merged(ctx, m)
	if err != nil {
		return nil, err
	}
	pulls, err := g.pulls(ctx, repo, numbers, "checks", checksFields)
	if err != nil {
		return nil, err
	}
	var reds []red
	for _, number := range numbers {
		for _, suite := range pulls[alias(number)].Commits.Nodes[0].Commit.CheckSuites.Nodes {
			for _, run := range suite.CheckRuns.Nodes {
				if run.Status == "COMPLETED" && run.Conclusion == failed {
					reds = append(reds, red{number, suite.WorkflowRun.Workflow.Name, run.Name, run.DetailsURL})
				}
			}
		}
	}
	bases := g.bases(ctx, repo, reds)
	seen := map[string]bool{}
	for _, r := range reds {
		line := r.line(bases[alias(r.number)])
		if !seen[line] {
			seen[line] = true
			lines = append(lines, line)
		}
	}
	return lines, nil
}

func alias(number int) string {
	return "p" + strconv.Itoa(number)
}

var statusKinds = []string{"evidence", "peer", "state"}

func (g Guard) mail(ctx context.Context, m Merge) ([]string, error) {
	if m.Lane == "" {
		return nil, nil
	}
	delivered, err := g.records(ctx, "--drive", m.Drive, "--cursor", m.Lane, "--reader", m.Lane)
	if err != nil {
		return nil, err
	}
	others := slices.DeleteFunc(slices.Clone(delivered), func(r record) bool { return r.Lane == m.Lane || askdesk.ReadByAsker(r.Lane, r.Kind) })
	if len(others) == 0 {
		return nil, nil
	}
	own, err := g.records(ctx, "--drive", m.Drive, "--lane", m.Lane, "--since", others[0].Num)
	if err != nil {
		return nil, err
	}
	settled := map[int64]bool{}
	for _, r := range own {
		settled[r.Re] = true
	}
	for _, r := range slices.Concat(delivered, own) {
		settled[r.Resolves] = true
		for _, seq := range r.AlsoResolves {
			settled[seq] = true
		}
	}
	var lines []string
	var mail []record
	for _, r := range others {
		if slices.Contains(r.To, m.Lane) && r.Status != closed && !settled[r.Seq] {
			lines = append(lines, r.line(UnreadLine))
			continue
		}
		if !slices.Contains(statusKinds, r.Kind) {
			mail = append(mail, r)
		}
	}
	if len(mail) > mailShown+1 {
		oldest := mail[0]
		lines = append(lines, oldest.line(MailLine), fmt.Sprintf(mailCut, MailLine, len(mail)-mailShown-1, oldest.Num, m.Drive, m.Lane, oldest.Num))
		mail = mail[len(mail)-mailShown:]
	}
	for _, r := range mail {
		lines = append(lines, r.line(MailLine))
	}
	return lines, nil
}

func (g Guard) records(ctx context.Context, read ...string) ([]record, error) {
	done, err := g.Runner.Run(ctx, shell.Command{Argv: slices.Concat([]string{"cci", "tail"}, read, []string{"--json", "--budget", wholeBudget}), Timeout: readTimeout})
	if err != nil {
		return nil, err
	}
	var records []record
	decoder := json.NewDecoder(strings.NewReader(done.Stdout))
	for decoder.More() {
		var r record
		if err := decoder.Decode(&r); err != nil {
			return nil, err
		}
		records = append(records, r)
	}
	return records, nil
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

func (g Guard) bases(ctx context.Context, repo string, reds []red) map[string]pull {
	var numbers []int
	for _, r := range reds {
		if !slices.Contains(numbers, r.number) {
			numbers = append(numbers, r.number)
		}
	}
	if len(numbers) == 0 {
		return nil
	}
	bases, err := g.pulls(ctx, repo, numbers, "base", baseFields)
	if err != nil {
		return nil
	}
	return bases
}

func (g Guard) pulls(ctx context.Context, repo string, numbers []int, fragment, fragmentFields string) (map[string]pull, error) {
	owner, name, _ := strings.Cut(repo, "/")
	var fields strings.Builder
	for _, number := range numbers {
		fmt.Fprintf(&fields, "%s: pullRequest(number: %d) { ...%s } ", alias(number), number, fragment)
	}
	query := fmt.Sprintf("query($owner: String!, $name: String!) { repository(owner: $owner, name: $name) { %s} }\n%s", fields.String(), fragmentFields)
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

func (g Guard) read(ctx context.Context, into any, argv ...string) error {
	done, err := g.Runner.Run(ctx, shell.Command{Argv: argv, Timeout: readTimeout, Idempotent: true})
	if err != nil {
		return err
	}
	return json.Unmarshal([]byte(done.Stdout), into)
}
