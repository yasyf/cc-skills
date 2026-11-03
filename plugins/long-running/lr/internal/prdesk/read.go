package prdesk

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	readSource = "github-read"
	openRead   = `query($owner: String!, $name: String!, $endCursor: String) { repository(owner: $owner, name: $name) { pullRequests(states: OPEN, first: 100, after: $endCursor) { pageInfo { hasNextPage endCursor } nodes { number headRefName baseRefName headRefOid updatedAt author { login } labels(first: 20) { nodes { name } } latestReviews(first: 20) { nodes { author { login } state submittedAt commit { oid } } } commits(last: 1) { nodes { commit { statusCheckRollup { contexts(first: 100) { nodes { __typename ... on CheckRun { name conclusion completedAt databaseId } ... on StatusContext { context state createdAt } } } } } } } } } } }`
)

type GitHubRead struct {
	CCX    ccx.Client
	Store  *store.Store
	Repo   string
	Author string
	Label  string
}

type label struct {
	Name string `json:"name"`
}

type openPR struct {
	Number    int       `json:"number"`
	Branch    string    `json:"headRefName"`
	Base      string    `json:"baseRefName"`
	Head      string    `json:"headRefOid"`
	UpdatedAt time.Time `json:"updatedAt"`
	Author    struct {
		Login string `json:"login"`
	} `json:"author"`
	Labels struct {
		Nodes []label `json:"nodes"`
	} `json:"labels"`
	Reviews struct {
		Nodes []struct {
			Author struct {
				Login string `json:"login"`
			} `json:"author"`
			State       string    `json:"state"`
			SubmittedAt time.Time `json:"submittedAt"`
			Commit      struct {
				OID string `json:"oid"`
			} `json:"commit"`
		} `json:"nodes"`
	} `json:"latestReviews"`
	Commits struct {
		Nodes []struct {
			Commit struct {
				Rollup *struct {
					Contexts struct {
						Nodes []struct {
							Kind        string    `json:"__typename"`
							Name        string    `json:"name"`
							Conclusion  string    `json:"conclusion"`
							CompletedAt time.Time `json:"completedAt"`
							ID          int64     `json:"databaseId"`
							Context     string    `json:"context"`
							State       string    `json:"state"`
							CreatedAt   time.Time `json:"createdAt"`
						} `json:"nodes"`
					} `json:"contexts"`
				} `json:"statusCheckRollup"`
			} `json:"commit"`
		} `json:"nodes"`
	} `json:"commits"`
}

func (g GitHubRead) Read(ctx context.Context, at time.Time) ([]Event, error) {
	owner, name, _ := strings.Cut(g.Repo, "/")
	out, err := g.CCX.GH(ctx, ccx.MaxAgeFresh, "api", "graphql", "--paginate", "-f", "owner="+owner, "-f", "name="+name, "-f", "query="+openRead)
	if err != nil {
		return nil, err
	}
	meta := Meta{Source: readSource, Seq: at.Unix(), At: at, Repo: g.Repo}
	var events []Event
	open := map[int]bool{}
	dec := json.NewDecoder(strings.NewReader(out))
	for {
		var page struct {
			Data struct {
				Repository struct {
					PullRequests struct {
						Nodes []openPR `json:"nodes"`
					} `json:"pullRequests"`
				} `json:"repository"`
			} `json:"data"`
		}
		err := dec.Decode(&page)
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return nil, fmt.Errorf("open pull requests of %s: %w", g.Repo, err)
		}
		for _, pr := range page.Data.Repository.PullRequests.Nodes {
			if pr.Author.Login != g.Author && !slices.Contains(pr.Labels.Nodes, label{g.Label}) {
				continue
			}
			open[pr.Number] = true
			events = append(events, g.events(meta, pr)...)
		}
	}
	gone, err := g.gone(ctx, meta, open)
	return append(events, gone...), err
}

func (g GitHubRead) events(meta Meta, pr openPR) []Event {
	keyed := func(key string, at time.Time) Meta {
		m := meta
		m.Key, m.At = fmt.Sprintf("%s:%d:%s", key, pr.Number, pr.Head), at
		return m
	}
	events := []Event{PullRequest{keyed("pr", pr.UpdatedAt), pr.Number, pr.Branch, pr.Base, pr.Head, true, false, ""}}
	for _, r := range pr.Reviews.Nodes {
		events = append(events, Review{keyed("review:"+r.Author.Login, r.SubmittedAt), pr.Number, r.Author.Login, strings.ToLower(r.State), r.Commit.OID})
	}
	for _, c := range pr.Commits.Nodes {
		if c.Commit.Rollup == nil {
			continue
		}
		for _, check := range c.Commit.Rollup.Contexts.Nodes {
			switch {
			case check.Kind == "CheckRun" && check.Conclusion != "":
				events = append(events, Check{keyed("check:"+check.Name, check.CompletedAt), pr.Head, check.Name, strings.ToLower(check.Conclusion), check.ID})
			case check.Kind == "StatusContext" && check.State != "PENDING" && check.State != "EXPECTED":
				events = append(events, Check{keyed("status:"+check.Context, check.CreatedAt), pr.Head, check.Context, strings.ToLower(check.State), 0})
			}
		}
	}
	return events
}

func (g GitHubRead) gone(ctx context.Context, meta Meta, open map[int]bool) ([]Event, error) {
	var left []store.PRFacts
	if err := g.Store.Update(func(tx *store.Tx) error {
		stored, err := tx.OpenPRs(g.Repo)
		left = slices.DeleteFunc(stored, func(f store.PRFacts) bool { return open[f.Number] })
		return err
	}); err != nil || len(left) == 0 {
		return nil, err
	}
	fields := make([]string, 0, len(left))
	for _, f := range left {
		fields = append(fields, fmt.Sprintf("pr%d: pullRequest(number: %d) { merged closed }", f.Number, f.Number))
	}
	owner, name, _ := strings.Cut(g.Repo, "/")
	out, err := g.CCX.GH(ctx, ccx.MaxAgeFresh, "api", "graphql", "-f", fmt.Sprintf("query=query { repository(owner: %q, name: %q) { %s } }", owner, name, strings.Join(fields, " ")))
	if err != nil {
		return nil, err
	}
	var read struct {
		Data struct {
			Repository map[string]struct {
				Merged bool `json:"merged"`
				Closed bool `json:"closed"`
			} `json:"repository"`
		} `json:"data"`
	}
	if err := json.Unmarshal([]byte(out), &read); err != nil {
		return nil, fmt.Errorf("pull requests that left the open list: %w", err)
	}
	events := make([]Event, 0, len(left))
	for _, f := range left {
		state := read.Data.Repository[fmt.Sprintf("pr%d", f.Number)]
		if !state.Closed {
			continue
		}
		m := meta
		m.Key = fmt.Sprintf("pr:%d:%s:shut", f.Number, f.Head)
		events = append(events, PullRequest{m, f.Number, f.Branch, f.Base, f.Head, false, state.Merged, f.Change})
	}
	return events, nil
}
