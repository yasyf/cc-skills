package facts

import (
	"context"
	"errors"
	"fmt"
	"slices"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	rulingRead   = "ruling-read"
	rulingHis    = "his"
	rulingNotHis = "not his answer"
	noSuchAnswer = 3
)

func HisRulings(ctx context.Context, s *store.Store, repo string, runner shell.Runner, checkout string, ids []string, now time.Time) (map[string]bool, error) {
	ids = slices.DeleteFunc(slices.Compact(slices.Sorted(slices.Values(ids))), func(id string) bool { return id == "" })
	if len(ids) == 0 {
		return nil, nil
	}
	read, err := s.TrunkFacts(repo, rulingRead)
	if err != nil {
		return nil, err
	}
	his := make(map[string]bool, len(ids))
	for _, id := range ids {
		said, known := read[id]
		if !known {
			if said, err = whoseWords(ctx, runner, checkout, id); err != nil {
				return nil, err
			}
			if _, err := s.PutFact(repo, 0, id, rulingRead, said, now); err != nil {
				return nil, err
			}
		}
		his[id] = said == rulingHis
	}
	return his, nil
}

func whoseWords(ctx context.Context, runner shell.Runner, checkout, id string) (string, error) {
	_, err := runner.Run(ctx, shell.Command{Argv: []string{"ccn", "-R", checkout, "answer", "show", id, "--json"}})
	var exit *shell.ExitError
	if errors.As(err, &exit) && exit.Code == noSuchAnswer {
		return rulingNotHis, nil
	}
	if err != nil {
		return "", fmt.Errorf("read ruling %s: %w", id, err)
	}
	return rulingHis, nil
}
