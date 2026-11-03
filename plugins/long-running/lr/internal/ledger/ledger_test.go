package ledger

import (
	"context"
	"reflect"
	"slices"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

func TestRowsReadsTheShowPayload(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply(`{"id":"abc","title":"t","rows":[{"key":"123","fields":{"state":"open"}}]}`, "ccn", "-R", "/repo", "ledger", "show")
	rows, err := CLI{fake, "/repo"}.Rows(context.Background(), "abc")
	if err != nil {
		t.Fatal(err)
	}
	if want := []Row{{"123", map[string]string{"state": "open"}}}; !reflect.DeepEqual(rows, want) {
		t.Fatalf("rows %+v", rows)
	}
}

func TestSetRowNamesEveryFieldInOneStableOrder(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply("", "ccn", "ledger", "row", "set")
	if err := (CLI{Runner: fake}).SetRow(context.Background(), "abc", "ask/9", map[string]string{"b": "2", "a": "1"}); err != nil {
		t.Fatal(err)
	}
	want := []string{"ccn", "ledger", "row", "set", "abc", "--key", "ask/9", "--field", "a=1", "--field", "b=2"}
	if got := fake.Calls()[0].Argv; !slices.Equal(got, want) {
		t.Fatalf("argv %v", got)
	}
}

func TestSyncSendsTheRowsOnStdin(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply("", "ccn", "ledger", "sync")
	if err := (CLI{Runner: fake}).Sync(context.Background(), "abc", []Row{{"k", map[string]string{"a": "1"}}}); err != nil {
		t.Fatal(err)
	}
	if got := fake.Calls()[0].Stdin; got != `[{"key":"k","fields":{"a":"1"}}]` {
		t.Fatalf("stdin %q", got)
	}
}

func TestRemoveRowNamesTheKey(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply("", "ccn", "ledger", "row", "rm")
	if err := (CLI{Runner: fake}).RemoveRow(context.Background(), "abc", "k"); err != nil {
		t.Fatal(err)
	}
	if got, want := fake.Calls()[0].Argv, []string{"ccn", "ledger", "row", "rm", "abc", "--key", "k"}; !slices.Equal(got, want) {
		t.Fatalf("argv %v", got)
	}
}
