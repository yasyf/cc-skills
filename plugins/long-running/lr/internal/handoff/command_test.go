package handoff

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
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

func posted(t *testing.T, draft client.Draft) client.Record {
	t.Helper()
	stored, err := client.Post(context.Background(), draft)
	if err != nil {
		t.Fatalf("Post(%+v): %v", draft, err)
	}
	return stored
}

func TestTheStandingVerbReadsLiveRulesFromARealStore(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	first := posted(t, client.Draft{Drive: "brook", Lane: rootLane, Kind: goKind, Topic: standingTopic, To: []string{"landing-desk"}, Text: "deploy every landing"})
	second := posted(t, client.Draft{Drive: "brook", Lane: rootLane, Kind: goKind, Topic: standingTopic, To: []string{"landing-desk"}, Text: "dev is always releasable"})
	posted(t, client.Draft{Drive: "brook", Lane: rootLane, Kind: goKind, To: []string{"landing-desk"}, Text: "an ordinary go"})
	third := posted(t, client.Draft{Drive: "brook", Lane: rootLane, Kind: correctionKind, Topic: standingTopic, Re: first.Seq, To: []string{"landing-desk"}, Text: "deploy every landing within five minutes"})

	printed, err := execute(t, "", "standing", "--drive", "brook")
	if err != nil {
		t.Fatal(err)
	}

	equal(t, printed, fmt.Sprintf("live standing: #%[1]d, #%[2]d\n#%[1]d dev is always releasable [cci #%[1]d]\n#%[2]d deploy every landing within five minutes [cci #%[2]d]\n", second.Seq, third.Seq))
}

func TestTailRecordsTakesEveryKindTheDocReadsAndCarriesTheFieldsItUses(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	ctx := context.Background()
	stored := posted(t, client.Draft{Drive: "brook", Lane: "inc-load-fix", Kind: "report", Topic: "load", To: []string{rootLane}, Text: "CANDIDATE the fork reads the parent"})

	found, err := tailRecords(ctx, recordQuery{drive: "brook", kinds: reportedKinds})
	if err != nil {
		t.Fatal(err)
	}
	equal(t, len(found), 1)
	record := found[0]
	equal(t, record.Seq, stored.Seq)
	equal(t, record.Drive, "brook")
	equal(t, record.Lane, "inc-load-fix")
	equal(t, record.Kind, "report")
	equal(t, record.Topic, "load")
	equal(t, record.Text, "CANDIDATE the fork reads the parent")
	if !record.At.Equal(stored.At) {
		t.Fatalf("at %v, want %v", record.At, stored.At)
	}

	for _, kinds := range [][]string{{goKind, correctionKind}, textKinds, settlingKinds} {
		if _, err := tailRecords(ctx, recordQuery{drive: "brook", kinds: kinds}); err != nil {
			t.Fatalf("kinds %v: %v", kinds, err)
		}
	}

	later, err := tailRecords(ctx, recordQuery{drive: "brook", since: stored.At.Add(time.Hour)})
	if err != nil {
		t.Fatal(err)
	}
	equal(t, len(later), 0)
}

func TestTheBlockerReachesRootOnceAnHour(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	ctx := context.Background()

	postBlocker(ctx, "brook", "register-rows:brook:20261010T22", "the rows did not load")
	postBlocker(ctx, "brook", "register-rows:brook:20261010T22", "the rows did not load")

	found, err := client.Tail(ctx, client.Query{Drive: "brook", Kinds: []string{blockerKind}})
	if err != nil {
		t.Fatal(err)
	}
	equal(t, len(found), 1)
	equal(t, found[0].Lane, rootLane)
	equal(t, found[0].To, []string{rootLane})
	equal(t, found[0].Text, "the rows did not load")
}
