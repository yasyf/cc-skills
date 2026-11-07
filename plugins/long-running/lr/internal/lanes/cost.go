package lanes

import (
	"regexp"
	"slices"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	OwnerPickLine = "Owner pick:"
	laneEffort    = "high"
)

type Cost struct{ Model, Effort string }

var (
	AboveHigh      = []string{"xhigh", "max"}
	BriefOwnerPick = regexp.MustCompile(`(?m)^` + OwnerPickLine + ` *owner:m[0-9]+\b`)
	deskMedium     = Cost{"opus", "medium"}
	DeskCosts      = map[string]Cost{
		"fidelity-desk":      deskMedium,
		"fidelity-shard":     deskMedium,
		"red-sweep":          deskMedium,
		"board-refresher":    deskMedium,
		"drive-watchdog":     deskMedium,
		"grader-appeals":     deskMedium,
		"install-desk":       deskMedium,
		"meta-desk":          deskMedium,
		"old-tree-port-desk": deskMedium,
		"alerts-desk":        {"sonnet", "low"},
	}
)

var macDesks = map[string]string{"install-desk": "mac-build", OwnerRelay: "cc-present"}

func MacDeskNeed(name string) string {
	base, _ := family(name)
	return macDesks[base]
}

func DeskCostOf(name string) (Cost, bool) {
	base, _ := family(name)
	cost, desk := DeskCosts[base]
	return cost, desk
}

func successorCost(old store.Lane) Cost {
	base, _ := family(old.Name)
	if cost, desk := DeskCostOf(old.Name); desk && cost.Model == old.Model {
		return cost
	}
	keeps := base == OwnerRelay || must(BriefOf(old.Brief)).OwnerPicked
	if slices.Contains(AboveHigh, old.Effort) && !keeps {
		return Cost{old.Model, laneEffort}
	}
	return Cost{old.Model, old.Effort}
}
