package update

import (
	"context"
	"encoding/json"
	"fmt"
	"slices"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ledger"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/plans"
)

const (
	registerLabel = "register"
	statusField   = "status"
	inForce       = "in-force"
	planWasField  = "plan_was"
	planNowField  = "plan_now"
)

type PlanRuling struct {
	Was string
	Now string
}

type registerLedger struct {
	ID        string `json:"id"`
	UpdatedAt string `json:"updated_at"`
}

func ReadRulings(ctx context.Context, ccn plans.Ccn, source ledger.Store, program string) ([]PlanRuling, error) {
	listed, err := ccn(ctx, "ledger", "list", "--label", registerLabel, "--label", "program:"+program, "--json")
	if err != nil {
		return nil, err
	}
	var registers []registerLedger
	if err := json.Unmarshal([]byte(listed), &registers); err != nil {
		return nil, fmt.Errorf("register ledgers: %w", err)
	}
	if len(registers) == 0 {
		return nil, nil
	}
	newest := slices.MaxFunc(registers, func(one, other registerLedger) int { return strings.Compare(one.UpdatedAt, other.UpdatedAt) })
	rows, err := source.Rows(ctx, newest.ID)
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
