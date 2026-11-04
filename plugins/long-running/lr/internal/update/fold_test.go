package update

import (
	"context"
	"errors"
	"fmt"
	"reflect"
	"regexp"
	"strconv"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/asks"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ledger"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/plans"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/wait"
)

const fivePieces = "SandSQL five pieces: ssql-server (two modes), ssql-replicator, ssql-follower, ssql-signal, ssql-journal; migration R0-R8"

var threeModes = PlanRuling{"two modes", "three modes: laptop, sidecar, server"}

func crowded(t *testing.T) Inputs {
	t.Helper()
	const stepCount, laneCount, prCount, fyiCount = 16, 200, 200, 30
	words := strings.Repeat("the writer waits on the reader before it lands ", 4)
	steps := make([]*plans.Step, stepCount)
	for i := range steps {
		steps[i] = &plans.Step{
			Number: strconv.Itoa(i),
			Title:  fmt.Sprintf("%d. B%d: part %d of the rebuild (rev 5.15 row %d; f56f3b%d)", i, i, i, i, i),
			Tasks:  []plans.Task{{Title: "the work", State: plans.InProgress}},
		}
	}
	in := inputs(t)
	in.Plans = []*plans.Plan{
		{Title: fivePieces, Steps: []*plans.Step{{Number: plans.Unmapped, Title: "No step label"}}},
		{Title: "The rebuild", Steps: steps},
	}
	rows := map[string]map[string]string{}
	in.Lanes, in.Records, in.Waits = nil, nil, nil
	in.Streams = append(in.Streams, Workstream{Name: "The rebuild", Delivers: "every part of the rebuild", Stems: []string{"worker"}})
	for i := range laneCount {
		name := fmt.Sprintf("worker-%d", i)
		lane := heard(name, "desired", 5)
		lane.Predecessor = name + "-before"
		if i%2 == 0 {
			lane.Step = strconv.Itoa(i % stepCount)
		}
		in.Lanes = append(in.Lanes, lane)
		in.Records = append(in.Records, record(int64(i+1), name, "report", words, 5))
		if i%3 == 0 {
			in.Waits = append(in.Waits, record(int64(i+1000), name, "ask", words, 10))
		}
	}
	for i := range prCount {
		in.PRRows = append(in.PRRows, store.ReviewRow{Number: 48000 + i, Title: words, State: "open", Head: "abc"})
	}
	book, err := asks.Read(rows)
	if err != nil {
		t.Fatal(err)
	}
	in.Book = book
	in.All = nil
	for i := range fyiCount {
		in.All = append(in.All, fyiRecord(int64(i+2000), bridge, "Bug fixes: "+words, 5))
	}
	return in
}

func closing(text string) string {
	lines := strings.Split(strings.TrimSpace(text), "\n")
	return lines[len(lines)-1]
}

func TestAnUpdateOver88KBFoldsUnderTheCapNamesWhatItFoldedAndLeavesNoGapOrMissingRow(t *testing.T) {
	report := Render(crowded(t))
	if len(report.Text) >= updateBytes {
		t.Fatalf("size = %d", len(report.Text))
	}
	from := regexp.MustCompile(`^Folded from (\d+) KB to stay under 40 KB: done and unchanged rows to one line per section; `).FindStringSubmatch(closing(report.Text))
	if from == nil {
		t.Fatalf("closing = %q", closing(report.Text))
	}
	if whole, err := strconv.Atoi(from[1]); err != nil || whole < 88 {
		t.Fatalf("the fixture only reached %s KB", from[1])
	}
	if strings.Contains(report.Text, "Dropped to fit") || strings.Contains(closing(report.Text), "over the") {
		t.Fatalf("closing = %q", closing(report.Text))
	}
	if len(report.Gaps) != 0 {
		t.Fatalf("gaps = %v", report.Gaps)
	}
	if left := Missing(report.Text, report.Needs); len(left) != 0 {
		t.Fatalf("missing = %v", left)
	}
}

func TestAnUpdateHisWordsAloneOverflowKeepsThemDropsTheTailAndSaysSo(t *testing.T) {
	in := inputs(t)
	for i := range 500 {
		in.All = append(in.All, record(int64(i+1), bridge, "ask", strings.Repeat("words ", 30), 5))
	}
	report := Render(in)
	if len(report.Text) >= updateBytes {
		t.Fatalf("size = %d", len(report.Text))
	}
	if !regexp.MustCompile(`Dropped to fit: the \d+ lines after "`).MatchString(closing(report.Text)) {
		t.Fatalf("closing = %q", closing(report.Text))
	}
	if left := Missing(report.Text, report.Needs); len(left) != 0 {
		t.Fatalf("a dropped item still counts as missing: %v", left)
	}
	for _, gap := range report.Gaps {
		if strings.Contains(gap, "KB") {
			t.Fatalf("size is a gap: %q", gap)
		}
	}
}

func TestLongMessagesFromHimClipBeforeAnyLineDropsAndKeepWhatCarriesThem(t *testing.T) {
	in := inputs(t)
	for i := range 100 {
		in.All = append(in.All, record(int64(i+1), bridge, "ask", strings.Repeat("words ", 100), 5))
	}
	report := Render(in)
	if len(report.Text) >= updateBytes || strings.Contains(report.Text, "Dropped to fit") {
		t.Fatalf("size = %d, closing = %q", len(report.Text), closing(report.Text))
	}
	if !strings.Contains(closing(report.Text), "the messages quoted from you to their first 240 characters") {
		t.Fatalf("closing = %q", closing(report.Text))
	}
	if !strings.Contains(report.Text, "… | "+noCarrier) {
		t.Fatalf("a clipped message lost what carries it:\n%s", report.Text)
	}
}

func TestAtTheLastFoldsALaneIsStillNamedAndUnownedLanesBecomeACount(t *testing.T) {
	blocked := Row{"red-sweep-3", "reds", "desired", "red-sweep-3", "red-sweep-3 waits on the coordinator's answer to ask #9", "x", noneRecorded, false}
	plain := Row{"flake-1", "flakes", "desired", "flake-1", nothingBlocks, "x", noneRecorded, false}
	quiet := Row{"old-lane-1", "x", "desired", nobody, nothingBlocks, "x", "x", false}
	lanes := section{title: noStepTitle, rows: []Row{blocked, plain, quiet}}
	want := []string{"Waiting on the coordinator: red-sweep-3", "On it, nothing blocking: flake-1", "", quietNow + "old-lane-1"}
	if got := lanes.lines(folding{level: foldLast}); !reflect.DeepEqual(got, want) {
		t.Fatalf("lines = %v", got)
	}
	step := section{title: "The writer", rows: []Row{{Name: "Step 3", Who: nobody, Blocks: nothingBlocks}}, step: true}
	want = []string{"3 open rows have nobody on them; each also sits in its own section below.", "- The writer (1): Step 3", "- 2 lanes, named in their own sections"}
	withQuiet := section{title: noStepTitle, rows: []Row{quiet, quiet}}
	if got := unownedLines([]section{step, withQuiet}, folding{level: foldUnowned}); !reflect.DeepEqual(got, want) {
		t.Fatalf("unowned = %v", got)
	}
}

func TestAHeadingNeverCarriesAPlanIDARowIDOrACommitSHA(t *testing.T) {
	for raw, want := range map[string]string{
		"0. The base and the copy (F)":                                    "The base and the copy",
		"4. Telemetry and the watch, before code (E6; AGENTS.md Metrics)": "Telemetry and the watch, before code",
		"5. B1: one process tree, one primary, one build check (rev 5.15 row 1 and s4; 5.16 step 1; D-local 4.1; f56f3b8; a201ee6)": "one process tree, one primary, one build check",
		"8. B4: one pass admits; idle pods ask nothing (rev 5.15 row 4, s2; C-polling design A; adeaf2a, E1)":                       "one pass admits; idle pods ask nothing",
		"10. B6a: the cloud writer under step 8's permission, with the scope cuts (rev 5.15 row 6 first half; 44bd6c6, 4830123)":    "the cloud writer under step 8's permission, with the scope cuts",
		"11. Releases: R1a cloud, R1b Mac, then one step per release (F3; 7ab71e2, c0db6b7, 81403ed)":                               "Releases: cloud, Mac, then one step per release",
		fivePieces: "SandSQL five pieces: ssql-server (two modes), ssql-replicator, ssql-follower, ssql-signal, ssql-journal",
		"Durable agents rebuild: the plan (Oct 23)": "Durable agents rebuild: the plan (Oct 23)",
		"The reader lands at f56f3b8":               "The reader lands at",
		"The defaced page moves to S3":              "The defaced page moves to S3",
	} {
		if got := plainTitle(raw); got != want {
			t.Errorf("plainTitle(%q) = %q, want %q", raw, got, want)
		}
	}
	report := Render(crowded(t))
	for _, line := range strings.Split(report.Text, "\n") {
		if strings.HasPrefix(line, "#") && regexp.MustCompile(`rev 5|f56f3b|\bB\d+:|R0-R8`).MatchString(line) {
			t.Errorf("heading carries an id: %q", line)
		}
	}
}

func TestALaneNamingARawStepSitsOnNoStepAndAWorkstreamHeadingReadsInWords(t *testing.T) {
	in := inputs(t)
	coded := heard("coded-1", "desired", 5)
	coded.Step = "S5"
	worded := heard("worded-1", "desired", 5)
	worded.Step = "next-build-bugs"
	in.Lanes = append(in.Lanes, coded, worded)
	report := Render(in)
	if strings.Contains(report.Text, "Workstream S5") || !strings.Contains(report.Text, "### Workstream next build bugs") {
		t.Fatalf("text = %s", report.Text)
	}
	if !strings.Contains(sectionOf(t, report.Text, noStepTitle), "coded-1") {
		t.Fatalf("no-step section = %s", sectionOf(t, report.Text, noStepTitle))
	}
}

func TestAPlanHeadingAndPieceRowReadTheRulingThatSupersedesThePlanText(t *testing.T) {
	in := inputs(t)
	in.Plans = []*plans.Plan{{Title: fivePieces, Steps: []*plans.Step{{Number: plans.Unmapped, Title: "No step label"}}}}
	before := Render(in)
	if !strings.Contains(before.Text, "(two modes)") {
		t.Fatalf("with no ruling the plan's words stand:\n%s", before.Text)
	}
	in.Rulings = []PlanRuling{threeModes}
	report := Render(in)
	if strings.Contains(report.Text, "two modes") {
		t.Fatalf("the superseded words render:\n%s", report.Text)
	}
	if !strings.Contains(report.Text, "## SandSQL five pieces: ssql-server (three modes: laptop, sidecar, server), ssql-replicator, ssql-follower, ssql-signal, ssql-journal\n") {
		t.Fatalf("heading:\n%s", report.Text)
	}
	if !strings.Contains(report.Text, "| ssql-server | the ssql-server piece of SandSQL five pieces: three modes: laptop, sidecar, server |") {
		t.Fatalf("piece row:\n%s", report.Text)
	}
	for _, piece := range []string{"ssql-server", "ssql-replicator", "ssql-follower", "ssql-signal", "ssql-journal"} {
		if !strings.Contains(report.Text, "### "+piece+"\n") {
			t.Errorf("no section for %s:\n%s", piece, report.Text)
		}
	}
}

func TestOnlyAnInForceRegisterRowOfTheNewestRegisterRewritesPlanText(t *testing.T) {
	var asked []string
	listing := `[{"id":"old","updated_at":"2026-10-01T00:00:00Z"},{"id":"new","updated_at":"2026-11-01T00:00:00Z"}]`
	ccn := func(_ context.Context, args ...string) (string, error) {
		asked = args
		return listing, nil
	}
	source := retroLedger{rows: []ledger.Row{
		{Key: "rule/a", Fields: map[string]string{"status": "in-force", "plan_was": "two modes", "plan_now": "three modes"}},
		{Key: "rule/b", Fields: map[string]string{"status": "superseded", "plan_was": "one mode", "plan_now": "two modes"}},
		{Key: "rule/c", Fields: map[string]string{"status": "in-force", "words": "no plan text here"}},
	}}
	got, err := ReadRulings(context.Background(), ccn, source, "brook")
	if err != nil {
		t.Fatal(err)
	}
	if want := []PlanRuling{{"two modes", "three modes"}}; !reflect.DeepEqual(got, want) {
		t.Fatalf("rulings = %+v", got)
	}
	if want := []string{"ledger", "list", "--label", "register", "--label", "program:brook", "--json"}; !reflect.DeepEqual(asked, want) {
		t.Fatalf("asked = %v", asked)
	}
	listing = "[]\n"
	if got, err := ReadRulings(context.Background(), ccn, source, "brook"); err != nil || got != nil {
		t.Fatalf("a drive with no register = %v, %v", got, err)
	}
	failed := errors.New("ccn down")
	broken := func(context.Context, ...string) (string, error) { return "", failed }
	if _, err := ReadRulings(context.Background(), broken, source, "brook"); !errors.Is(err, failed) {
		t.Fatalf("err = %v", err)
	}
}

func TestAPlanStepIsOneLineUnlessARowUnderItChangedSinceTheLastSentUpdate(t *testing.T) {
	in := inputs(t)
	first := Render(in)
	if !strings.Contains(first.Text, "### The readers\n") || !strings.Contains(first.Text, "### The writers\n") {
		t.Fatalf("with nothing sent every step is in full:\n%s", first.Text)
	}
	in.Before = first.Snapshot
	unmoved := Render(in)
	if strings.Contains(unmoved.Text, "### The readers") || strings.Contains(unmoved.Text, "### The writers") {
		t.Fatalf("an unchanged step kept its table:\n%s", unmoved.Text)
	}
	for _, line := range []string{"- The readers: 0 of 1 tasks done, 1 in progress, 0 blocked; ship-2", "- The writers: 0 of 1 tasks done, 0 in progress, 0 blocked; " + noLiveLane + ", no lane record names it; " + nobody} {
		if !strings.Contains(unmoved.Text, line) {
			t.Errorf("no %q in\n%s", line, unmoved.Text)
		}
	}
	if got := closing(unmoved.Text); got != "Folded: plan steps with no change to one line each (2)." {
		t.Fatalf("closing = %q", got)
	}
	if left := Missing(unmoved.Text, unmoved.Needs); len(left) != 0 {
		t.Fatalf("missing = %v", left)
	}
	in.Lanes[0].Heard = ago(300)
	moved := Render(in)
	if !strings.Contains(moved.Text, "### The readers\n") || strings.Contains(moved.Text, "### The writers") {
		t.Fatalf("only the step whose row changed opens:\n%s", moved.Text)
	}
}

func TestDoneAndUnchangedRowsFoldFirstAndAPinnedRowOrAskNeverDoes(t *testing.T) {
	duty := Row{"refresh the board", "refresh", "desired", "board-7", nothingBlocks, "x", noneRecorded, false}
	found := section{title: dutiesTitle, rows: []Row{duty}}
	f := folding{level: foldUnchanged, before: takeSnapshot([]section{found}, now).Rows, since: "1:00 AM"}
	if got, want := found.lines(f), []string{"No change since 1:00 AM: refresh the board"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("lines = %v", got)
	}
	for _, title := range []string{pinnedTitle, asksTitle} {
		kept := section{title: title, rows: []Row{duty}}
		if got := kept.lines(folding{level: foldUnchanged, before: takeSnapshot([]section{kept}, now).Rows}); len(got) != 3 {
			t.Errorf("%s folded: %v", title, got)
		}
	}
}

func TestAMissingSectionIsNamedInTheExitLine(t *testing.T) {
	report := Render(inputs(t))
	err := missingError(Missing(strings.ReplaceAll(report.Text, "The writers", ""), report.Needs))
	if err == nil || err.Error() != "missing: 1: The writers" {
		t.Fatalf("err = %v", err)
	}
}

func TestARenderWithOnlyGapsHasItsOwnExitCodeAndARealMissIsStillOne(t *testing.T) {
	var stderr strings.Builder
	if err := exitOf(Report{Text: "ship-2"}, &stderr); err != nil || stderr.Len() != 0 {
		t.Fatalf("a complete render = %v, %q", err, stderr.String())
	}
	gaps := Report{Text: "ship-2", Needs: []Need{{"ship-2", ""}}, Gaps: []string{"owner words no record carries: #13"}}
	err := exitOf(gaps, &stderr)
	var exit *wait.ExitError
	if !errors.As(err, &exit) || exit.Code != gapsExit {
		t.Fatalf("err = %v", err)
	}
	if got := stderr.String(); got != "lr: gaps: 1: owner words no record carries: #13\n" {
		t.Fatalf("stderr = %q", got)
	}
	gaps.Needs = append(gaps.Needs, Need{"release-5", ""})
	err = exitOf(gaps, &stderr)
	if errors.As(err, &exit) || err == nil || err.Error() != "missing: 2: release-5; owner words no record carries: #13" {
		t.Fatalf("a real miss = %v", err)
	}
}

func TestAnAskRowReadsInWordsWithNoLedgerKey(t *testing.T) {
	book, err := asks.Read(map[string]map[string]string{"ask/000025": {"lane": "ship-2", "text": "Owner 4:38 AM Oct 30: cut the Test job to under 3 minutes", "asked_at": ago(300).Format("2006-01-02T15:04:05Z07:00")}})
	if err != nil {
		t.Fatal(err)
	}
	in := inputs(t)
	in.Book = book
	report := Render(in)
	if strings.Contains(report.Text, "ask/000025") || !strings.Contains(report.Text, "| Owner 4:38 AM Oct 30 | cut the Test job to under 3 minutes |") {
		t.Fatalf("text:\n%s", report.Text)
	}
	if left := Missing(report.Text, report.Needs); len(left) != 0 {
		t.Fatalf("missing = %v", left)
	}
}
