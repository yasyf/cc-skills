package ledgertest

import (
	"context"
	"maps"
	"slices"
	"sync"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ledger"
)

type Fake struct {
	mu      sync.Mutex
	ledgers map[string]map[string]map[string]string
}

func (f *Fake) Rows(_ context.Context, id string) ([]ledger.Row, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	stored := f.ledgers[id]
	rows := make([]ledger.Row, 0, len(stored))
	for _, key := range slices.Sorted(maps.Keys(stored)) {
		rows = append(rows, ledger.Row{Key: key, Fields: maps.Clone(stored[key])})
	}
	return rows, nil
}

func (f *Fake) SetRow(_ context.Context, id, key string, fields map[string]string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.merge(id, key, fields)
	return nil
}

func (f *Fake) RemoveRow(_ context.Context, id, key string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	delete(f.ledgers[id], key)
	return nil
}

func (f *Fake) Sync(_ context.Context, id string, rows []ledger.Row) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	for _, row := range rows {
		f.merge(id, row.Key, row.Fields)
	}
	return nil
}

func (f *Fake) merge(id, key string, fields map[string]string) {
	if f.ledgers == nil {
		f.ledgers = map[string]map[string]map[string]string{}
	}
	if f.ledgers[id] == nil {
		f.ledgers[id] = map[string]map[string]string{}
	}
	if f.ledgers[id][key] == nil {
		f.ledgers[id][key] = map[string]string{}
	}
	maps.Copy(f.ledgers[id][key], fields)
}

var _ ledger.Store = (*Fake)(nil)
