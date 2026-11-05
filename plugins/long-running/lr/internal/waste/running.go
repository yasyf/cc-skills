package waste

import (
	"context"
	"fmt"
	"strings"
	"time"
	_ "time/tzdata"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/install"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/wait"
)

var pacificZone = func() *time.Location {
	zone, err := time.LoadLocation("America/Los_Angeles")
	if err != nil {
		panic(err)
	}
	return zone
}()

type Running struct {
	CCX   ccx.Client
	Repo  string
	LR    string
	Pack  string
	Since time.Time
	held  map[int]bool
}

func Current(client ccx.Client, claude, lr string) (*Running, error) {
	pack, since, err := install.InstalledSince(claude)
	if err != nil {
		return nil, err
	}
	return &Running{CCX: client, Repo: wait.ReleaseRepo, LR: lr, Pack: pack, Since: since, held: map[int]bool{}}, nil
}

func (r *Running) holds(ctx context.Context, number int) (bool, error) {
	if r.held[number] {
		return true, nil
	}
	merged, err := r.CCX.GH(ctx, ccx.MaxAgeCached, "api", fmt.Sprintf("repos/%s/pulls/%d", r.Repo, number), "--jq", "select(.merged) | .merge_commit_sha")
	if err != nil {
		return false, err
	}
	sha := strings.TrimSpace(merged)
	if sha == "" {
		return false, nil
	}
	for _, tag := range []string{"lr--v" + r.LR, "long-running--v" + r.Pack} {
		status, err := r.CCX.GH(ctx, ccx.MaxAgeCached, "api", fmt.Sprintf("repos/%s/compare/%s...%s", r.Repo, sha, tag), "--jq", ".status")
		if err != nil {
			return false, err
		}
		if status = strings.TrimSpace(status); status != "ahead" && status != "identical" {
			return false, nil
		}
	}
	r.held[number] = true
	return true, nil
}

func (r *Running) unproven(pr string) string {
	return fmt.Sprintf("%s installed in lr %s and pack %s since %s, proof owed", pr, r.LR, r.Pack, r.Since.In(pacificZone).Format("3:04 PM"))
}
