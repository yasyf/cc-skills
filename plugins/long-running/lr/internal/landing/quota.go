package landing

import (
	"context"
	"encoding/json"
	"fmt"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	quotaUrgent = 1500
	topCallers  = 5
)

var quotaResources = []string{"core", "graphql"}

type Quota struct {
	Runner shell.Runner
	Drive  string
	Repo   string
}

type quotaPool struct {
	Identity  string    `json:"identity"`
	Resource  string    `json:"resource"`
	Limit     int       `json:"limit"`
	Remaining int       `json:"remaining"`
	ResetAt   time.Time `json:"resetAt"`
	Callers   []struct {
		Caller   string `json:"caller"`
		Requests int    `json:"requests"`
	} `json:"callers"`
}

func (q Quota) Pass(ctx context.Context) error {
	read, err := q.Runner.Run(ctx, shell.Command{Argv: []string{"ccx", "vcs", "auth", "status", q.Repo, "--json"}, Timeout: readTimeout})
	if err != nil {
		return err
	}
	var auths []struct {
		Reads struct {
			Pools []quotaPool `json:"pools"`
		} `json:"reads"`
		Writes struct {
			Pools []quotaPool `json:"pools"`
		} `json:"writes"`
	}
	if err := json.Unmarshal([]byte(read.Stdout), &auths); err != nil {
		return err
	}
	for _, auth := range auths {
		for _, pool := range slices.Concat(auth.Reads.Pools, auth.Writes.Pools) {
			if !slices.Contains(quotaResources, pool.Resource) || pool.Remaining >= quotaUrgent {
				continue
			}
			if err := q.warn(ctx, pool); err != nil {
				return err
			}
		}
	}
	return nil
}

func (q Quota) warn(ctx context.Context, pool quotaPool) error {
	callers := make([]string, 0, topCallers)
	for _, caller := range pool.Callers[:min(topCallers, len(pool.Callers))] {
		callers = append(callers, fmt.Sprintf("%s %d", caller.Caller, caller.Requests))
	}
	named := strings.Join(callers, ", ")
	if named == "" {
		named = "none recorded"
	}
	_, err := client.Post(ctx, client.Draft{
		Drive: q.Drive,
		Lane:  self,
		Kind:  "urgent",
		To:    []string{rootLane},
		ID:    fmt.Sprintf("github-quota:%s-%s:%d", pool.Identity, pool.Resource, pool.ResetAt.Unix()),
		Text:  fmt.Sprintf("GITHUB-QUOTA-LOW the %s %s quota has %d of %d calls left until %s; top callers: %s", pool.Identity, pool.Resource, pool.Remaining, pool.Limit, pool.ResetAt.In(pacificZone()).Format("3:04 PM"), named),
	})
	return err
}
