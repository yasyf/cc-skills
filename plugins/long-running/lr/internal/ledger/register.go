package ledger

import (
	"context"
	"encoding/json"
	"fmt"
	"slices"
	"strings"
)

type registerLedger struct {
	ID        string `json:"id"`
	UpdatedAt string `json:"updated_at"`
}

func Register(ctx context.Context, ccn func(ctx context.Context, args ...string) (string, error), source Store, program string) ([]Row, error) {
	listed, err := ccn(ctx, "ledger", "list", "--label", "register", "--label", "program:"+program, "--json")
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
	return source.Rows(ctx, newest.ID)
}
