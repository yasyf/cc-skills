package update

import (
	"context"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ledger"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/plans"
)

const (
	statusField  = "status"
	inForce      = "in-force"
	planWasField = "plan_was"
	planNowField = "plan_now"
)

type PlanRuling struct {
	Was string
	Now string
}

func ReadRulings(ctx context.Context, ccn plans.Ccn, source ledger.Store, program string) ([]PlanRuling, error) {
	rows, err := ledger.Register(ctx, ccn, source, program)
	if err != nil {
		return nil, err
	}
	var out []PlanRuling
	for _, row := range rows {
		if row.Fields[statusField] == inForce && row.Fields[planWasField] != "" {
			out = append(out, PlanRuling{row.Fields[planWasField], row.Fields[planNowField]})
		}
	}
	return out, nil
}

func rulingsReplacer(rulings []PlanRuling) *strings.Replacer {
	pairs := make([]string, 0, 2*len(rulings))
	for _, ruling := range rulings {
		pairs = append(pairs, ruling.Was, ruling.Now)
	}
	return strings.NewReplacer(pairs...)
}
