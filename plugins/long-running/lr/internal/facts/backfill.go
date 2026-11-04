package facts

import (
	"context"
	"encoding/json"
	"fmt"
	"slices"
	"strings"

	"github.com/yasyf/cc-inbox/client"
)

const bodyBytes = 60_000

func (s Source) Backfill(ctx context.Context) (int, error) {
	lanes := make(map[int]string)
	rows, err := s.Store.ReviewRows(s.Repo)
	if err != nil {
		return 0, err
	}
	for _, row := range rows {
		lanes[row.Number] = row.Lane
	}
	owned, err := s.Store.Owners()
	if err != nil {
		return 0, err
	}
	for lane, numbers := range owned {
		for _, number := range numbers {
			lanes[number] = lane
		}
	}
	numbers := make([]int, 0, len(lanes))
	for number := range lanes {
		numbers = append(numbers, number)
	}
	slices.Sort(numbers)
	read, err := s.readMembers(ctx, numbers)
	if err != nil {
		return 0, err
	}
	posted := 0
	for _, number := range numbers {
		pr, found := read.prs[number]
		if !found || pr.State != "OPEN" {
			continue
		}
		body, err := json.Marshal(map[string]string{"body": strings.ToValidUTF8(pr.Body[:min(len(pr.Body), bodyBytes)], "")})
		if err != nil {
			return posted, err
		}
		if _, err := client.Post(ctx, client.Draft{
			Drive: s.Drive, Lane: lanes[number], Kind: "opened", Text: fmt.Sprintf("backfill #%d", number),
			ID:    fmt.Sprintf("row-backfill:%s#%d", s.Repo, number),
			PRs:   []string{fmt.Sprintf("%s#%d", s.Repo, number)},
			Body:  body,
			Attrs: map[string]string{"repo": s.Repo, "branch": pr.HeadRefName, "base": pr.BaseRefName, "head": pr.HeadRefOid},
		}); err != nil {
			return posted, err
		}
		posted++
	}
	return posted, nil
}
