package facts

import (
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func TestMustReadEveryRowALandingDecisionReads(t *testing.T) {
	read := store.ReviewRow{Number: 1, Head: "a", ReadHead: "a", State: prrow.StateOpen, Mergeable: string(machine.MergeableClean)}
	for _, tc := range []struct {
		name  string
		row   func(store.ReviewRow) store.ReviewRow
		facts map[string]string
		want  bool
	}{
		{"an untouched read row waits for a record", func(r store.ReviewRow) store.ReviewRow { return r }, nil, false},
		{"a tapped row the queue evicted", func(r store.ReviewRow) store.ReviewRow { r.Tapped, r.State = true, prrow.StateEvicted; return r }, nil, true},
		{"an exempt row", func(r store.ReviewRow) store.ReviewRow { r.State = prrow.StateExempt; return r }, nil, true},
		{"a queued row", func(r store.ReviewRow) store.ReviewRow { r.Queue = string(machine.QueueQueued); return r }, nil, true},
		{"a head GitHub answered unknown for, showing its last known value", func(r store.ReviewRow) store.ReviewRow { return r }, map[string]string{prrow.FactUnknownSince: "2026-11-06T00:00:00Z"}, true},
		{"a row never read", func(r store.ReviewRow) store.ReviewRow { r.ReadHead = ""; return r }, nil, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if got := mustRead(tc.row(read), tc.facts); got != tc.want {
				t.Fatalf("mustRead = %v, want %v", got, tc.want)
			}
		})
	}
}
