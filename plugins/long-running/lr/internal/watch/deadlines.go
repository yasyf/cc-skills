package watch

import (
	"cmp"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"slices"
	"strings"
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
	Key    string
	Cause  string
}

func (w Wait) subject() string {
	if w.Repo == UnfinishedNamespace {
		return w.Head
	}
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
	var order []waitClass
	classes := map[waitClass][]Wait{}
	var failed error
	for _, wait := range standing {
		if !wait.overdue(now) {
			continue
		}
		raised, err := s.Escalate(wait.subject(), cmp.Or(wait.Key, wait.Cause), wait.To, now)
		if err != nil {
			failed = err
			break
		}
		if !raised {
			continue
		}
		escalated = append(escalated, pass.Escalated{Subject: wait.subject(), Cause: wait.Cause, To: wait.To})
		class := waitClass{wait.Kind, wait.To}
		if classes[class] == nil {
			order = append(order, class)
		}
		classes[class] = append(classes[class], wait)
	}
	for _, class := range order {
		subject, body := overdueBody(class, classes[class])
		if err := post([]string{class.to}, body); err != nil {
			failed = errors.Join(failed, fmt.Errorf("%s: %w", subject, err))
		}
	}
	return escalated, failed
}

type waitClass struct{ kind, to string }

func overdueBody(class waitClass, waits []Wait) (string, bus.Body) {
	if len(waits) == 1 {
		return waits[0].subject(), bus.Overdue(class.to, waits[0].subject(), waits[0].Cause)
	}
	lines := make([]string, len(waits))
	for i, wait := range waits {
		lines[i] = wait.subject() + "\n" + wait.Cause
	}
	detail := strings.Join(lines, "\n\n") + "\n"
	sum := sha256.Sum256([]byte(detail))
	subject := fmt.Sprintf("%s/%d/%s", class.kind, len(waits), hex.EncodeToString(sum[:])[:8])
	return subject, bus.OverdueClass(class.to, subject, fmt.Sprintf("%d overdue at once; the body names each id and what moves it", len(waits)), detail)
}
