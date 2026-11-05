package bus_test

import (
	"bytes"
	"context"
	"testing"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
)

func lrPost(t *testing.T, args ...string) (string, error) {
	t.Helper()
	var out bytes.Buffer
	cmd := bus.Command(app.New(&out))
	cmd.SetOut(&out)
	cmd.SetErr(&out)
	cmd.SetArgs(args)
	err := cmd.Execute()
	return out.String(), err
}

func TestLrPostWritesOneTypedRecord(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	t.Setenv("LR_DRIVE", "d")

	out, err := lrPost(t, "grade", "--as", "finder-desk", "--pr", "45821", "--head", head, "--grader", "finder", "--verdict=clear", "--to", "supervisor")
	if err != nil {
		t.Fatalf("%v: %s", err, out)
	}

	rows, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{"lr.grade"}})
	if err != nil || len(rows) != 1 || rows[0].Lane != "finder-desk" {
		t.Fatalf("Tail = %+v, %v", rows, err)
	}
	if want := "#1 grade\n"; out != want {
		t.Errorf("printed %q, want %q", out, want)
	}
}

func TestLrPostRefusesAVerdictThatCannotNameItsGrader(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())

	_, err := lrPost(t, "verdict", "--drive", "d", "--as", "root", "--pr", "45821", "--head", head, "--verdict", "accept", "--to", "supervisor")

	wantRefused(t, err, "a verdict cannot name the grader it answers, so it clears nothing; root posts a decide record reading finder-override #<pr> at <sha>: <cite>, or fidelity-override")
	rows, err := client.Tail(context.Background(), client.Query{Drive: "d"})
	if err != nil || len(rows) != 0 {
		t.Fatalf("Tail = %+v, %v", rows, err)
	}
}

func TestLrPostPrintsTheCorrectedCommandAndStoresNothing(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())

	_, err := lrPost(t, "note", "--drive", "d", "--as", "finder-desk", "--pr", "45821", "--head", head, "--grader", "finder", "--verdict", "clear", "--to", "supervisor")

	wantRefused(t, err, `these fields are a grade record, not note; run: lr post grade --pr 45821 --head `+head+` --grader finder --verdict clear --to supervisor`)
	rows, err := client.Tail(context.Background(), client.Query{Drive: "d"})
	if err != nil || len(rows) != 0 {
		t.Fatalf("Tail = %+v, %v", rows, err)
	}
}

func TestLrPostGivenARepeatedPRAsksForOneRecordEach(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())

	_, err := lrPost(t, "grade", "--drive", "d", "--as", "finder-desk", "--pr", "1", "--pr", "2", "--head", head, "--grader", "finder", "--verdict", "clear")

	wantRefused(t, err, `a grade names one PR; post one record per PR: lr post grade --pr 1 .* && lr post grade --pr 2 `)
}

func TestLrPostNamesAWordWithNoField(t *testing.T) {
	if _, err := lrPost(t, "note", "stray"); err == nil || err.Error() != "lr post: expected --<field> before 'stray'" {
		t.Fatalf("err = %v", err)
	}
	if _, err := lrPost(t, "note", "--text"); err == nil || err.Error() != "lr post: --text needs a value" {
		t.Fatalf("err = %v", err)
	}
}
