package landing

import (
	"context"
	"fmt"
	"slices"
	"strconv"
	"strings"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	queueDrive  = "trunk"
	queueKind   = "queue"
	queueCursor = "trunk-queue"
)

var queueFellOutcomes = []string{"failed", "canceled"}

type QueueFell struct {
	Store *store.Store
	Drive string
	Repo  string
}

func (q QueueFell) Pass(ctx context.Context) error {
	cursor, err := q.Store.Cursor(queueCursor)
	if err != nil {
		return err
	}
	records, err := client.Tail(ctx, client.Query{Drive: queueDrive, Kinds: []string{queueKind}, After: cursor})
	if err != nil || len(records) == 0 {
		return err
	}
	prs, err := q.Store.PRs(q.Repo)
	if err != nil {
		return err
	}
	for _, record := range records {
		for _, pr := range fellOut(record, q.Repo, prs) {
			if err := q.tell(ctx, record, pr); err != nil {
				return err
			}
		}
		if err := q.Store.Advance(queueCursor, record.Seq); err != nil {
			return err
		}
	}
	return nil
}

func (q QueueFell) tell(ctx context.Context, record client.Record, pr store.PR) error {
	id := record.ID
	if id == "" {
		id = strconv.FormatInt(record.Seq, 10)
	}
	to := []string{rootLane}
	if pr.Lane != "" {
		to = append(to, pr.Lane)
	}
	text := []string{fmt.Sprintf("#%d %s: %s", pr.Number, pr.Head[:min(10, len(pr.Head))], record.Text)}
	if record.Refs.URL != "" {
		text = append(text, record.Refs.URL)
	}
	_, err := client.Post(ctx, client.Draft{Drive: q.Drive, Lane: self, Kind: "blocker", To: to, ID: "trunk-queue:" + id, PRs: []string{fmt.Sprintf("%s#%d", q.Repo, pr.Number)}, Text: strings.Join(text, "; ")})
	return err
}

func fellOut(record client.Record, repo string, prs []store.PR) []store.PR {
	if !slices.Contains(queueFellOutcomes, record.Fields.Outcome) {
		return nil
	}
	var fell []store.PR
	for _, named := range record.Refs.PRs {
		for _, pr := range prs {
			if pr.Number == named.Number && strings.EqualFold(named.Repo, repo) && pr.State != machine.StateLanded && pr.State != machine.StateClosed {
				fell = append(fell, pr)
			}
		}
	}
	return fell
}
