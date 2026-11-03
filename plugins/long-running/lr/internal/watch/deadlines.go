package watch

import (
	"fmt"
	"slices"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/pass"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const root = string(machine.MoverRoot)

type Wait struct {
	Kind   string
	Repo   string
	Number int
	Head   string
	Mover  machine.Mover
	Since  time.Time
	Within time.Duration
	To     string
	Cause  string
}

func (w Wait) subject() string {
	return fmt.Sprintf("%s#%d", w.Repo, w.Number)
}

func (w Wait) overdue(now time.Time) bool {
	return !now.Before(w.Since.Add(w.Within))
}

func standsFor(standing []Wait, item store.Item) bool {
	return slices.ContainsFunc(standing, func(w Wait) bool {
		return w.Kind == item.Kind && w.Repo == item.Repo && w.Number == item.Number && w.Head == item.Head
	})
}

func Apply(s *store.Store, namespaces []string, standing []Wait, now time.Time, post pass.Post) ([]pass.Escalated, error) {
	for _, wait := range standing {
		if _, err := s.OpenItem(store.NewItem{Kind: wait.Kind, Repo: wait.Repo, Number: wait.Number, Head: wait.Head, Mover: wait.Mover, Deadline: wait.Within}, wait.Since); err != nil {
			return nil, err
		}
	}
	for _, namespace := range namespaces {
		open, err := s.OpenItems(namespace, 0)
		if err != nil {
			return nil, err
		}
		for _, item := range open {
			if standsFor(standing, item) {
				continue
			}
			if err := s.CloseItem(item.ID, now); err != nil {
				return nil, err
			}
		}
	}
	var escalated []pass.Escalated
	for _, wait := range standing {
		if !wait.overdue(now) {
			continue
		}
		raised, err := s.Escalate(wait.subject(), wait.Cause, wait.To, now)
		if err != nil {
			return escalated, err
		}
		if !raised {
			continue
		}
		escalated = append(escalated, pass.Escalated{Subject: wait.subject(), Cause: wait.Cause, To: wait.To})
		if err := post([]string{wait.To}, bus.Overdue(wait.To, wait.subject(), wait.Cause)); err != nil {
			return escalated, err
		}
	}
	return escalated, nil
}
