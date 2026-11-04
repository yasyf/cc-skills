package gates

import (
	"context"
	"log/slog"
	"strconv"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const RefusedFact = "gate-refused"

type Gate struct {
	Name  string
	Kind  string
	Clear string
	Check func(ctx context.Context, f machine.Facts) (Verdict, error)
}

type Verdict struct {
	Cause   string
	Refusal string
	Reread  bool
	Held    bool
}

type Keeper struct {
	Store *store.Store
	Now   func() time.Time
	Log   *slog.Logger
	Tell  func(ctx context.Context, f machine.Facts, kind, id, text string)
}

func (k Keeper) Refuses(ctx context.Context, f machine.Facts, gates ...Gate) bool {
	refused := false
	for _, gate := range gates {
		refused = k.refuses(ctx, f, gate) || refused
	}
	return refused
}

func (k Keeper) refuses(ctx context.Context, f machine.Facts, gate Gate) bool {
	if gate.Clear != "" {
		facts, err := k.Store.Facts(f.Repo, f.Number, f.Head)
		if err != nil {
			k.Log.Warn("a gate did not read its clear, so it passes this head this pass", "gate", gate.Name, "pr", f.Number, "err", err)
			return false
		}
		if facts[gate.Clear] != "" {
			return false
		}
	}
	verdict, err := gate.Check(ctx, f)
	if err != nil {
		k.Log.Warn("a gate did not read, so it passes this head this pass", "gate", gate.Name, "pr", f.Number, "err", err)
		return false
	}
	if verdict.Held {
		return true
	}
	if verdict.Refusal != "" {
		if _, err := k.Store.PutFact(f.Repo, f.Number, f.Head, RefusedFact, verdict.Cause, k.Now()); err != nil {
			k.Log.Warn("a gate did not keep its refusal", "gate", gate.Name, "pr", f.Number, "err", err)
		}
		k.Tell(ctx, f, gate.Kind, verdict.Cause+":"+strconv.Itoa(f.Number)+":"+f.Head, verdict.Refusal)
		return true
	}
	if gate.Clear != "" && !verdict.Reread {
		if _, err := k.Store.PutFact(f.Repo, f.Number, f.Head, gate.Clear, "1", k.Now()); err != nil {
			k.Log.Warn("a gate did not keep its clear", "gate", gate.Name, "pr", f.Number, "err", err)
		}
	}
	return false
}
