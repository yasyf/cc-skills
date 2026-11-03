package update

import (
	"reflect"
	"testing"
)

func TestReconcileNamesEachRetiredOwnerAndEachLiveLaneWithNoTask(t *testing.T) {
	covered := heard("ship-2", "desired", 5)
	bare := heard("bare-3", "desired", 5)
	bare.Deliverable = "the sealed journal"
	moved := heard("old-1", "retired", 300)
	moved.RetiredBy = "new-2"
	alone := heard("gone-4", "retired", 300)
	lanes := []Lane{covered, bare, moved, heard("new-2", "desired", 5), alone}
	tasks := []ClaudeTask{
		{"1", "Ship it", "in_progress", "ship-2"},
		{"2", "Port the reader", "in_progress", "old-1"},
		{"3", "Port the writer", "pending", "gone-4"},
		{"4", "Finished", "completed", "gone-4"},
		{"5", "Nobody's yet", "pending", ""},
		{"6", "Successor's", "in_progress", "new-2"},
	}
	want := []string{
		"task #2 old-1: Port the reader: old-1 retired; move it to new-2",
		"task #3 gone-4: Port the writer: gone-4 retired with no successor; complete it or start one",
		"no task: bare-3 is live with none; TaskCreate for the sealed journal",
	}
	if got := Reconcile(lanes, tasks, newView(lanes, nil, nil, now)); !reflect.DeepEqual(got, want) {
		t.Fatalf("drift = %q", got)
	}
}

func TestAMatchingListHasNoDrift(t *testing.T) {
	lanes := []Lane{heard("ship-2", "desired", 5)}
	tasks := []ClaudeTask{{"1", "Ship it", "in_progress", "ship-2"}}
	if got := Reconcile(lanes, tasks, newView(lanes, nil, nil, now)); len(got) != 0 {
		t.Fatalf("drift = %q", got)
	}
}
