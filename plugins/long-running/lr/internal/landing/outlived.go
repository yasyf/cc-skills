package landing

import (
	"context"
	"fmt"
	"regexp"
	"strconv"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

var headBlocker = regexp.MustCompile(`^(?:build-failed|rulings|rebase-direct|land):(\d+):([0-9a-f]{40})$`)

type Outlived struct {
	Store *store.Store
	Drive string
	Repo  string
}

func (o Outlived) Pass(ctx context.Context) error {
	open, err := client.Open(ctx, o.Drive, "blocker")
	if err != nil {
		return err
	}
	prs, err := o.Store.PRs(o.Repo)
	if err != nil {
		return err
	}
	byNumber := make(map[string]store.PR, len(prs))
	for _, pr := range prs {
		byNumber[strconv.Itoa(pr.Number)] = pr
	}
	for _, record := range open {
		named := headBlocker.FindStringSubmatch(record.ID)
		if record.Lane != self || named == nil {
			continue
		}
		pr, known := byNumber[named[1]]
		if !known || (!pr.State.Terminal() && pr.Head == named[2]) {
			continue
		}
		if _, err := client.Post(ctx, client.Draft{
			Drive:    o.Drive,
			Lane:     self,
			Kind:     "note",
			To:       record.To,
			ID:       fmt.Sprintf("outlived:%d", record.Seq),
			Resolves: record.Seq,
			PRs:      []string{o.Repo + "#" + named[1]},
			Text:     fmt.Sprintf("RESOLVED %s: #%s has since landed, closed or moved past %s", record.Name(), named[1], shortHead(named[2])),
		}); err != nil {
			return err
		}
	}
	return nil
}
