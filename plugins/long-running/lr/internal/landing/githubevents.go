package landing

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
)

const (
	githubDrive  = "github"
	githubKind   = "github"
	githubTopic  = "pull_request"
	githubAudit  = time.Hour
	githubSkew   = time.Minute
	openPRsQuery = `query($owner: String!, $name: String!, $endCursor: String) { repository(owner: $owner, name: $name) { pullRequests(states: OPEN, first: 100, after: $endCursor) { pageInfo { hasNextPage endCursor } nodes { number headRefOid baseRefName isDraft } } } }`
)

var githubShut = []string{"closed", "merged"}

type githubPR struct {
	Head  string `json:"headRefOid"`
	Base  string `json:"baseRefName"`
	Draft bool   `json:"isDraft"`
}

type GithubEvents struct {
	CCX   ccx.Client
	Drive string
	Repo  string
	Now   func() time.Time

	open    map[int]githubPR
	after   int64
	audited time.Time
}

func (g *GithubEvents) Pass(ctx context.Context) error {
	now := g.Now()
	if g.open == nil {
		read, err := g.read(ctx)
		if err != nil {
			return err
		}
		g.open, g.audited = read, now
		return g.fold(ctx, client.Query{Drive: githubDrive, Kinds: []string{githubKind}, Since: now.Add(-githubSkew)})
	}
	if err := g.fold(ctx, client.Query{Drive: githubDrive, Kinds: []string{githubKind}, After: g.after}); err != nil {
		return err
	}
	if now.Sub(g.audited) < githubAudit {
		return nil
	}
	read, err := g.read(ctx)
	if err != nil {
		return err
	}
	disagree := disagreements(g.open, read)
	g.open, g.audited = read, now
	if len(disagree) == 0 {
		return nil
	}
	_, err = client.Post(ctx, client.Draft{
		Drive: g.Drive, Lane: self, Kind: "defect", ID: fmt.Sprintf("github-audit:%s:%d", g.Repo, now.Unix()),
		Text: fmt.Sprintf("GITHUB-EVENTS-DRIFT the hourly read of %s disagrees with its github records on %d pull requests: %s", g.Repo, len(disagree), strings.Join(disagree, "; ")),
	})
	return err
}

func (g *GithubEvents) fold(ctx context.Context, q client.Query) error {
	records, err := client.Tail(ctx, q)
	if err != nil {
		return err
	}
	g.apply(records)
	return nil
}

func (g *GithubEvents) apply(records []client.Record) {
	for _, record := range records {
		g.after = max(g.after, record.Seq)
		if record.Topic != githubTopic {
			continue
		}
		for _, pr := range record.Refs.PRs {
			if !strings.EqualFold(pr.Repo, g.Repo) {
				continue
			}
			if slices.Contains(githubShut, record.Fields.Outcome) {
				delete(g.open, pr.Number)
				continue
			}
			attrs := record.Fields.Attrs
			g.open[pr.Number] = githubPR{Head: record.Fields.Commit, Base: attrs["base"], Draft: attrs["draft"] == "true"}
		}
	}
}

func (g *GithubEvents) read(ctx context.Context) (map[int]githubPR, error) {
	owner, name, found := strings.Cut(g.Repo, "/")
	if !found {
		return nil, fmt.Errorf("repo %q is not owner/name", g.Repo)
	}
	out, err := g.CCX.GH(ctx, ccx.MaxAgeFresh, "api", "graphql", "--paginate", "-f", "owner="+owner, "-f", "name="+name, "-f", "query="+openPRsQuery)
	if err != nil {
		return nil, err
	}
	open := map[int]githubPR{}
	dec := json.NewDecoder(strings.NewReader(out))
	for {
		var page struct {
			Data struct {
				Repository struct {
					PullRequests struct {
						Nodes []struct {
							Number int `json:"number"`
							githubPR
						} `json:"nodes"`
					} `json:"pullRequests"`
				} `json:"repository"`
			} `json:"data"`
		}
		err := dec.Decode(&page)
		if errors.Is(err, io.EOF) {
			return open, nil
		}
		if err != nil {
			return nil, fmt.Errorf("open pull requests: %w", err)
		}
		for _, node := range page.Data.Repository.PullRequests.Nodes {
			open[node.Number] = node.githubPR
		}
	}
}

func disagreements(events, read map[int]githubPR) []string {
	var found []string
	for number, got := range read {
		heard, known := events[number]
		switch {
		case !known:
			found = append(found, fmt.Sprintf("#%d is open at %s with no record", number, short(got.Head)))
		case heard != got:
			found = append(found, fmt.Sprintf("#%d records say %s, GitHub says %s", number, describe(heard), describe(got)))
		}
	}
	for number, heard := range events {
		if _, open := read[number]; !open {
			found = append(found, fmt.Sprintf("#%d records say open at %s, GitHub lists it shut", number, short(heard.Head)))
		}
	}
	slices.Sort(found)
	return found
}

func describe(pr githubPR) string {
	text := fmt.Sprintf("%s onto %s", short(pr.Head), pr.Base)
	if pr.Draft {
		text += " as a draft"
	}
	return text
}

func short(sha string) string {
	return sha[:min(10, len(sha))]
}
