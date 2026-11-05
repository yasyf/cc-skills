package facts

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	outsideWindow = 72 * time.Hour
	outsideQuery  = `query($owner: String!, $name: String!, $q: String!) { repository(owner: $owner, name: $name) { id } search(query: $q, type: ISSUE, first: 100) { nodes { ... on PullRequest { number state title headRefOid updatedAt files(first: 100) { nodes { path } } } } } }`
)

var outsideStates = map[string]string{"OPEN": prrow.StateOpen, "MERGED": prrow.StateLanded, "CLOSED": prrow.StateClosed}

func (s Source) admitOutside(ctx context.Context, rows []store.ReviewRow) error {
	since, err := s.Store.Cursor(store.OutsideSearchCursor)
	if err != nil {
		return err
	}
	from := time.Unix(since, 0)
	if since == 0 {
		from = s.Now().Add(-outsideWindow)
	}
	out, err := s.graphQL(ctx, outsideQuery, "-f", fmt.Sprintf("q=repo:%s is:pr sort:updated-asc updated:>=%s", s.Repo, from.UTC().Format(time.RFC3339)))
	if err != nil {
		return err
	}
	var reply struct {
		Data struct {
			Search struct {
				Nodes []struct {
					pullRequest
					UpdatedAt time.Time `json:"updatedAt"`
				} `json:"nodes"`
			} `json:"search"`
		} `json:"data"`
	}
	if err := json.Unmarshal([]byte(out), &reply); err != nil {
		return err
	}
	driven := make(map[int]bool, len(rows))
	for _, row := range rows {
		driven[row.Number] = true
	}
	return s.Store.Update(func(t *store.Tx) error {
		admitted, err := t.OutsidePRs(s.Repo)
		if err != nil {
			return err
		}
		found := map[int]store.OutsidePR{}
		for _, pr := range reply.Data.Search.Nodes {
			if err := t.Advance(store.OutsideSearchCursor, pr.UpdatedAt.Unix()); err != nil {
				return err
			}
			oldTree := pr.oldTree()
			if oldTree == "" || pr.nextTree() != "" || driven[pr.Number] {
				if err := t.DeleteOutsidePR(s.Repo, pr.Number); err != nil {
					return err
				}
				continue
			}
			known := admitted[pr.Number]
			if known.Head != pr.HeadRefOid {
				known.NoPort = ""
			}
			found[pr.Number] = store.OutsidePR{Repo: s.Repo, Number: pr.Number, Title: pr.Title, Head: pr.HeadRefOid, State: outsideStates[pr.State], OldTree: oldTree, NoPort: known.NoPort, Port: known.Port}
		}
		return t.PutOutsidePRs(found)
	})
}
