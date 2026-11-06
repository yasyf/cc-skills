package waste

import (
	"context"
	"fmt"
	"regexp"
	"strconv"
	"time"
	_ "time/tzdata"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/install"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/wait"
)

var squashed = regexp.MustCompile(`(?m)\(#(\d+)\)$`)

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
	return &Running{CCX: client, Repo: wait.ReleaseRepo, LR: lr, Pack: pack, Since: since}, nil
}

func (r *Running) holds(ctx context.Context, number int, mergedSince time.Time) (bool, error) {
	if r.held == nil {
		var tagged []map[int]bool
		for _, tag := range []string{"lr--v" + r.LR, "long-running--v" + r.Pack} {
			subjects, err := r.CCX.GH(ctx, ccx.MaxAgeCached, "api", "--paginate", fmt.Sprintf("repos/%s/commits?sha=%s&since=%s&per_page=100", r.Repo, tag, mergedSince.UTC().Format(time.RFC3339)), "--jq", `.[].commit.message | split("\n")[0]`)
			if err != nil {
				return false, err
			}
			numbers := map[int]bool{}
			for _, found := range squashed.FindAllStringSubmatch(subjects, -1) {
				pr, err := strconv.Atoi(found[1])
				if err != nil {
					return false, err
				}
				numbers[pr] = true
			}
			tagged = append(tagged, numbers)
		}
		r.held = map[int]bool{}
		for pr := range tagged[0] {
			r.held[pr] = tagged[1][pr]
		}
	}
	return r.held[number], nil
}

func (r *Running) unproven(pr string) string {
	return fmt.Sprintf("%s installed in lr %s and pack %s since %s, proof owed", pr, r.LR, r.Pack, r.Since.In(pacificZone).Format("3:04 PM"))
}
