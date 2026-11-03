package handoff

import (
	"bytes"
	"encoding/json"
	"io"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
)

func execute(t *testing.T, stdin string, args ...string) (string, error) {
	t.Helper()
	var out bytes.Buffer
	cmd := Command(app.New(io.Discard))
	cmd.SetIn(strings.NewReader(stdin))
	cmd.SetOut(&out)
	cmd.SetErr(io.Discard)
	cmd.SetArgs(args)
	err := cmd.Execute()
	return out.String(), err
}

func TestTheGenerateVerbReadsTheSessionFromStdinAndWritesAFolderRecord(t *testing.T) {
	home := newHome(t)

	printed, err := execute(t, readFile(t, home.sess), "generate", "--program", "brook", "--plan", home.plan, "--session", "-", "--folder")
	if err != nil {
		t.Fatal(err)
	}

	var out generateOutput
	if err := json.Unmarshal([]byte(printed), &out); err != nil {
		t.Fatalf("%v: %s", err, printed)
	}
	equal(t, out.ID == nil, true)
	contains(t, readFile(t, out.File), "# brook: progress ", "- teammate: orca-desk-6", "- orca-desk.md: head R3, cursor R2")
	contains(t, out.Digest, "Open: 0 owner asks, 1 tasks, 1 lanes, 1 monitors, ")
}

func TestLintTakesExactlyOneOfADocOrAFile(t *testing.T) {
	for _, args := range [][]string{
		{"lint", "--program", "brook"},
		{"lint", "--program", "brook", "--doc", "a", "--file", "b"},
		{"lint", "--doc", "a"},
	} {
		if _, err := execute(t, "", args...); err == nil {
			t.Fatalf("%v was accepted", args)
		}
	}
}

func TestTheCompactVerbTakesItsThreeArguments(t *testing.T) {
	if _, err := execute(t, "", "compact", "term", "/compact"); err == nil {
		t.Fatal("two arguments were accepted")
	}
}
