package actionstest

import (
	"fmt"
	"io/fs"
	"maps"
	"slices"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/actions"
)

type Fake struct {
	Incidents map[string]actions.Incident
}

func (f Fake) IDs() ([]string, error) {
	return slices.Sorted(maps.Keys(f.Incidents)), nil
}

func (f Fake) Load(id string) (actions.Incident, error) {
	incident, found := f.Incidents[id]
	if !found {
		return actions.Incident{}, fmt.Errorf("%s: %w", id, fs.ErrNotExist)
	}
	return incident, nil
}

var _ actions.Store = Fake{}
