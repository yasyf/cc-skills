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

const lrSource = "plugins/long-running/lr"

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
		for _, query := range []string{"sha=lr--v" + r.LR, "sha=long-running--v" + r.Pack, "sha=long-running--v" + r.Pack + "&path=" + lrSource} {
			subjects, err := r.CCX.GH(ctx, ccx.MaxAgeCached, "api", "--paginate", fmt.Sprintf("repos/%s/commits?%s&since=%s&per_page=100", r.Repo, query, mergedSince.UTC().Format(time.RFC3339)), "--jq", `.[].commit.message | split("\n")[0]`)
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
		inLR, inPack, touchesLR := tagged[0], tagged[1], tagged[2]
		r.held = map[int]bool{}
		for pr := range inPack {
			r.held[pr] = inLR[pr] || !touchesLR[pr]
		}
	}
	return r.held[number], nil
}

func (r *Running) unproven(pr string) string {
	return fmt.Sprintf("%s installed in lr %s and pack %s since %s, proof owed", pr, r.LR, r.Pack, r.Since.In(pacificZone).Format("3:04 PM"))
}
