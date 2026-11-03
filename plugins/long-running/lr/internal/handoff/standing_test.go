package handoff

import (
	"encoding/json"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"
)

const (
	liveRelease = "4ffc9a5000000000000000000000000000000000"
	liveTickets = "104926a000000000000000000000000000000000"
	syncing     = "the app shows no syncing state, and the downloading bar is the one transfer state,\non a share or a chat older than 30 days"
	continued   = "no cloud worker continues a chat that started on a Mac, and no card or plan line mentions one"
	releases    = "no release goes out until every scenario passes on every platform"
	cards       = "> a lane waits on an open card: \n\nnothing starts until every card is answered"
	unquoted    = "the standing rules section does not name register `0cf17c9`; regenerate the handoff with `lr handoff generate`"
	noDoc       = "the handoff says \"no `standing-rules` register doc\"; the register ledger holds the owner's rows, so regenerate the section with `lr handoff generate`"
)

var (
	liveAnswers = []string{liveRelease, liveTickets}
	testReg     = &register{id: "0cf17c9000000000000000000000000000000000", body: "# Register\n\n1. Release as it merges, never on the owner's word (4ffc9a5).\n"}
	carriedDoc  = sectionOf(testReg, []string{"- R312 deploy every landing"}, "", nil)
)

func equal(t *testing.T, got, want any) {
	t.Helper()
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got  %#v\nwant %#v", got, want)
	}
}

func contains(t *testing.T, text string, parts ...string) {
	t.Helper()
	for _, part := range parts {
		if !strings.Contains(text, part) {
			t.Fatalf("%q is missing from:\n%s", part, text)
		}
	}
}

func lacks(t *testing.T, text string, parts ...string) {
	t.Helper()
	for _, part := range parts {
		if strings.Contains(text, part) {
			t.Fatalf("%q should be absent from:\n%s", part, text)
		}
	}
}

func after(text, marker string) string {
	_, rest, _ := strings.Cut(text, marker)
	return rest
}

func before(text, marker string) string {
	head, _, _ := strings.Cut(text, marker)
	return head
}

func TestAHandoffQuotingTheRegisterPasses(t *testing.T) {
	body := carriedDoc + "\n## Owner asks\nSoFi releases as it merges (4ffc9a5), never on the owner's word\n"

	if problems := lintBody(body, carriedDoc, testReg, liveAnswers, nil); len(problems) != 0 {
		t.Fatalf("problems %v", problems)
	}
}

func TestAHandoffWithoutTheSectionFails(t *testing.T) {
	problems := lintBody("## Owner asks\nnone\n", "", testReg, liveAnswers, nil)

	if len(problems) != 2 || !strings.HasPrefix(problems[0], "no `## Standing owner rules` section") || problems[1] != unquoted {
		t.Fatalf("problems %v", problems)
	}
}

func TestASectionWithoutTheRegisterFails(t *testing.T) {
	equal(t, lintBody("## Standing owner rules\n- R312 deploy every landing\n", "", testReg, liveAnswers, nil), []string{unquoted})
	if problems := lintBody("## Standing owner rules\n- R312 deploy every landing\n", "", nil, liveAnswers, nil); len(problems) != 0 {
		t.Fatalf("problems %v", problems)
	}
	equal(t, lintBody("## Standing owner rules\n\n  > # Register\n", "", testReg, liveAnswers, nil), []string{unquoted})
}

func TestDroppingACarriedRuleNeedsASupersededByLine(t *testing.T) {
	dropped := sectionOf(testReg, nil, "", nil)
	superseded := sectionOf(testReg, []string{"- R312 superseded by R575"}, "", nil)

	equal(t, lintBody(dropped, carriedDoc, testReg, liveAnswers, nil), []string{
		"dropped `R312` since the previous handoff (- R312 deploy every landing); carry it, or write `- R312 superseded by <id>`",
	})
	if problems := lintBody(superseded, carriedDoc, testReg, liveAnswers, nil); len(problems) != 0 {
		t.Fatalf("problems %v", problems)
	}
	if problems := lintBody(dropped, superseded, testReg, liveAnswers, nil); len(problems) != 0 {
		t.Fatalf("problems %v", problems)
	}
}

func TestAnOwnerGateLineMustCiteALiveAnswer(t *testing.T) {
	for line, flagged := range map[string]bool{
		"real sanddb on SoFi (owner's word)":                                    true,
		"needs owner approval before enqueue":                                   true,
		"the SoFi release still requires owner sign-off":                        true,
		"Card 1: reserved for the owner":                                        true,
		"released as it merges, no owner's word gate (4ffc9a5)":                 false,
		"CODEOWNER approvals on #28756":                                         false,
		"owner approval missing (deadbee)":                                      true,
		"Where an audit row and an owner answer differ, the owner's words win.": false,
		"When the owner's words list categories, the row names the surface":     false,
		"reserved for the owners of each lane":                                  false,
		"the owner's go-ahead on SoFi":                                          true,
		"the owner’s word with a curly apostrophe":                              true,
	} {
		t.Run(line, func(t *testing.T) {
			problems := lintBody(carriedDoc+"\n## Asks\n"+line+"\n", "", testReg, liveAnswers, nil)
			if flagged {
				equal(t, problems, []string{"owner-gate line cites no live answer id: " + line})
				return
			}
			if len(problems) != 0 {
				t.Fatalf("problems %v", problems)
			}
		})
	}
}

func TestAStandingRuleStaysLiveUntilACorrectionReplacesIt(t *testing.T) {
	found := readStanding([]busRecord{
		{Seq: 4, Drive: "brook", Topic: "standing", Kind: "go", Text: "deploy every landing", CCN: "4ffc9a5"},
		{Seq: 7, Drive: "brook", Topic: "standing", Kind: "go", Text: "dev is always releasable"},
		{Seq: 9, Drive: "brook", Topic: "standing", Kind: "correction", Re: 4, Text: "deploy every landing within five minutes", CCN: "87833ea"},
	}, "brook")

	equal(t, found.rules, []standingRule{
		{"#7", "#7 dev is always releasable", "cci #7"},
		{"#9", "#9 deploy every landing within five minutes", "ccn 87833ea"},
	})
	equal(t, found.superseded, map[string]string{"#4": "#9"})
}

func TestPeerMailCciTailCarriesIsNotAStandingRule(t *testing.T) {
	found := readStanding([]busRecord{
		{Seq: 4, Drive: "brook", Topic: "standing", Kind: "go", Text: "deploy every landing"},
		{Seq: 5, Drive: "release-v3", Kind: "state", Text: "verification only"},
		{Seq: 6, Drive: "release-v3", Topic: "standing", Kind: "go", Text: "verification only"},
		{Seq: 7, Drive: "brook", Kind: "go", Text: "verification only"},
		{Seq: 8, Drive: "brook", Topic: "standing", Kind: "decide", Text: "verification only"},
		{Seq: 9, Drive: "release-v3", Topic: "standing", Kind: "correction", Re: 4, Text: "verification only"},
	}, "brook")

	equal(t, found.rules, []standingRule{{"#4", "#4 deploy every landing", "cci #4"}})
	equal(t, found.superseded, map[string]string{})
}

func TestACarriedCciRuleIDIsABulletID(t *testing.T) {
	line := "- #9 deploy every landing within five minutes [ccn 87833ea]"

	equal(t, carried([]string{line}), []carriedRule{{"#9", line}})
}

func proposedFirst(t *testing.T, lines ...[2]string) []implication {
	t.Helper()
	items := make([]implication, 0, len(lines))
	for at, line := range lines {
		state := "ticked"
		if at == 0 {
			state = "proposed"
		}
		items = append(items, implication{N: at + 1, Direction: line[0], Text: line[1], State: state})
	}
	return items
}

func stamp(t *testing.T, at string) time.Time {
	t.Helper()
	parsed, err := time.Parse(time.RFC3339, at)
	if err != nil {
		t.Fatal(err)
	}
	return parsed
}

func testRows(t *testing.T) *rowSet {
	t.Helper()
	old := "2026-10-21T08:00:00Z"
	fresh := "2026-10-23T11:58:25Z"
	return &rowSet{
		readAt: stamp(t, "2026-10-23T12:00:00Z"),
		rows: []registerRow{
			{
				key: "rule/f958522", kind: "scope", status: inForce, words: syncing, answers: []string{"f958522"}, said: "2",
				implications: proposedFirst(t, [2]string{"removes", "No Syncing state appears anywhere in the UI."}, [2]string{"permits", "The downloading bar shows on a share without the owner's word."}),
				names:        []string{"syncing state", "Syncing"}, updatedAt: fresh, updated: stamp(t, fresh), evidence: "seed",
			},
			{
				key: "rule/4830123", kind: "scope", status: inForce, words: continued, answers: []string{"4830123"}, said: "1",
				implications: proposedFirst(t, [2]string{"removes", "No cloud worker continues a chat that started on a Mac."}),
				names:        []string{"cloud-continued Mac chat"}, updatedAt: old, updated: stamp(t, old), evidence: "seed",
			},
			{key: "rule/7ab71e2", kind: "gate", status: inForce, words: releases, answers: []string{"7ab71e2"}, updatedAt: old, updated: stamp(t, old), evidence: "seed"},
			{key: "rule/bc8938b", kind: "gate", status: inForce, words: cards, answers: []string{"bc8938b"}, updatedAt: old, updated: stamp(t, old), evidence: "seed"},
			{key: "rule/140cfe6", kind: "gate", status: "met", words: "plan mode starts once the incident work is done", answers: []string{"140cfe6"}, updatedAt: old, updated: stamp(t, old), evidence: "R1 watch passed"},
			{key: "rule/6f75431", kind: "pick", status: "superseded", words: "high-risk heads only", answers: []string{"6f75431"}, updatedAt: old, updated: stamp(t, old), evidence: "superseded by 3f34533"},
			{key: "corpus/b-tests", kind: "corpus", status: inForce, updatedAt: fresh, updated: stamp(t, fresh), evidence: "pinned"},
			{key: "corpus/fault-run", kind: "corpus", status: "broken", updatedAt: old, updated: stamp(t, old), evidence: "the 15-minute fault run failed"},
		},
	}
}

func verbatim(words string) string {
	return "; quote the row's words verbatim: “" + words + "”"
}

func aff1d7d4(t *testing.T) string {
	t.Helper()
	body, err := os.ReadFile("testdata/handoff-aff1d7d4-shape.md")
	if err != nil {
		t.Fatal(err)
	}
	return string(body)
}

func TestTheAff1d7d4HandoffFailsOnTheSentenceBareIDsAndAParaphrase(t *testing.T) {
	equal(t, lintBody(aff1d7d4(t), "", nil, nil, testRows(t)), []string{
		noDoc,
		"`f958522` cites without its words register row `rule/f958522`: - f958522: the app shows no syncing state." + verbatim(syncing),
		"`7ab71e2` paraphrases register row `rule/7ab71e2`: - 7ab71e2 (OP1 a): plus \"no release before every scenario passes\"." + verbatim(releases),
		"`4830123` cites without its words register row `rule/4830123`: - 4830123: no cloud worker continues a Mac chat." + verbatim(continued),
	})
}

func TestBulletsCarryingEachRowsWordsPassAndAFailedReadChecksOnlyTheSentence(t *testing.T) {
	first, second, _ := strings.Cut(syncing, "\n")
	body := "## Standing owner rules\n\n- R312 deploy every landing\n\n## Root narrative\n\n" +
		"- f958522 (Oct 21): “" + first + "\n      " + second + "”\n" +
		"- `rule/7ab71e2`: “" + releases + "”, so no release cuts before the bar\n" +
		"- 4830123: “" + continued + "”\n" +
		"- corpus/b-tests: the pinned corpus\n" +
		"- Merge mode EVERY (3f34533) as the owner set it\n"

	if problems := lintBody(body, "", nil, nil, testRows(t)); len(problems) != 0 {
		t.Fatalf("problems %v", problems)
	}
	equal(t, lintBody(aff1d7d4(t), "", nil, nil, &rowSet{failure: "exit 2"}), []string{noDoc})
	equal(t, lintBody(aff1d7d4(t), "", nil, nil, nil), []string{noDoc})
}

func TestTheSectionPrintsEveryScopeRowInTheOwnersWordsAndLintsClean(t *testing.T) {
	rows := testRows(t)
	indented := strings.ReplaceAll(syncing, "\n", "\n    ")

	rendered := sectionOf(nil, []string{"- #2 [cci #2]"}, "", rows)

	lacks(t, rendered, "no `standing-rules` register doc")
	if !strings.HasPrefix(rendered, "## Standing owner rules\n\n- #2 [cci #2]\n\n### Scope rows, in the owner's words\n\n") {
		t.Fatalf("rendered:\n%s", rendered)
	}
	contains(t, rendered,
		"- `rule/f958522` (in-force, said 2): “"+indented+"”\n",
		"  - implication 1, removes, proposed: No Syncing state appears anywhere in the UI.\n",
		"  - implication 2, permits, ticked: The downloading bar shows on a share without the owner's word.\n",
		"  - names: syncing state | Syncing\n",
		"- `rule/4830123` (in-force, said 1): “"+continued+"”\n",
	)
	equal(t, before(after(rendered, "### Rows touched in the last 24 hours\n\n"), "\n\n"),
		"- `rule/f958522` (scope, touched 2026-10-23T11:58:25Z): “"+indented+"”\n- `corpus/b-tests` (corpus, touched 2026-10-23T11:58:25Z): no words")
	equal(t, before(after(rendered, "### Owed gates\n\n"), "\n\n### "),
		"- `rule/7ab71e2` (owed): “"+releases+"”\n- `rule/bc8938b` (owed): “> a lane waits on an open card: \n    \n    nothing starts until every card is answered”")
	equal(t, before(after(rendered, "### Rows at risk or broken\n\n"), "\n\n"),
		"- `rule/140cfe6` (met): “plan mode starts once the incident work is done”\n  - evidence: R1 watch passed\n"+
			"- `rule/6f75431` (superseded): “high-risk heads only”\n  - evidence: superseded by 3f34533\n"+
			"- `corpus/fault-run` (broken): no words\n  - evidence: the 15-minute fault run failed")
	equal(t, after(rendered, "### Unconfirmed implications\n\n"),
		"- implication 1, removes, proposed: No Syncing state appears anywhere in the UI. (of `rule/f958522`)\n"+
			"- implication 1, removes, proposed: No cloud worker continues a chat that started on a Mac. (of `rule/4830123`)\n")
	if problems := lintBody(rendered, "", nil, nil, rows); len(problems) != 0 {
		t.Fatalf("problems %v", problems)
	}
}

func TestALinkedIDIsCheckedAndACopiedHeadingExemptsNoOwnerGateLine(t *testing.T) {
	body := "## Standing owner rules\n\n### Scope rows, in the owner's words\n\n" +
		"- [f958522](https://example.com/f958522): the app shows no syncing state.\n" +
		"- release waits on owner approval\n" +
		"- release waits on owner approval (of `rule/f958522`)\n"

	equal(t, lintBody(body, "", nil, nil, testRows(t)), []string{
		"owner-gate line cites no live answer id: - release waits on owner approval",
		"owner-gate line cites no live answer id: - release waits on owner approval (of `rule/f958522`)",
		"`f958522` cites without its words register row `rule/f958522`: - [f958522](https://example.com/f958522): the app shows no syncing state." + verbatim(syncing),
	})
}

func TestALineAfterAClosedQuotationIsCheckedEvenWhenTheWordsContainIt(t *testing.T) {
	rows := &rowSet{rows: []registerRow{{key: "rule/0a1b2c3", kind: "gate", status: inForce, words: "Never require owner approval", answers: []string{"0a1b2c3"}}}}
	body := "## Standing owner rules\n\n- `rule/0a1b2c3` (owed): “Never require owner approval”\n    require owner approval\n"

	equal(t, lintBody(body, "", nil, nil, rows), []string{"owner-gate line cites no live answer id: require owner approval"})
}

func TestAVerbatimRowExemptsOnlyItsOwnLinesFromTheOwnerGateCheck(t *testing.T) {
	body := "## Standing owner rules\n\n- `rule/7ab71e2` (owed): “" + releases + "”\n" +
		"  - release waits on owner approval\n" +
		"    - and owner approval again\n" +
		"- `rule/f958522` (in-force, said 2): “" + strings.ReplaceAll(syncing, "\n", "\n    ") + "”\n" +
		"  - implication 2, permits, ticked: The downloading bar shows on a share without the owner's word.\n"

	equal(t, lintBody(body, "", nil, nil, testRows(t)), []string{
		"owner-gate line cites no live answer id: - release waits on owner approval",
		"owner-gate line cites no live answer id: - and owner approval again",
	})
}

func TestTheDocSentenceStaysOnlyUntilTheRegisterLedgerIsRead(t *testing.T) {
	for _, tc := range []struct {
		name     string
		rows     *rowSet
		sentence bool
	}{
		{"no ledger", nil, true},
		{"failed read", &rowSet{failure: "exit 2"}, true},
		{"read", &rowSet{}, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			rendered := sectionOf(nil, nil, "", tc.rows)

			equal(t, strings.Contains(rendered, "- no `standing-rules` register doc\n"), tc.sentence)
			equal(t, strings.Contains(rendered, "- the register rows did not load (exit 2)"), tc.rows != nil && tc.rows.failure != "")
			lacks(t, sectionOf(testReg, nil, "", tc.rows), "no `standing-rules` register doc")
		})
	}
}

func TestATouchedNoRulingRowRendersItsReasonInTheRowsSection(t *testing.T) {
	rows := testRows(t)
	touched := "2026-10-23T11:58:20Z"
	rows.rows = append(rows.rows, registerRow{
		key: "no/e7f38c625a2e68f9", no: `a status question ("overall status since i went to sleep?"); it carries no ruling`, isNo: true,
		updatedAt: touched, updated: stamp(t, touched),
	})

	rendered := sectionOf(testReg, nil, "", rows)

	contains(t, rendered, "- `no/e7f38c625a2e68f9` (no ruling, touched 2026-10-23T11:58:20Z): a status question (\"overall status since i went to sleep?\"); it carries no ruling")
	lacks(t, rendered, "`no/e7f38c625a2e68f9` (no status)")
}

func TestParsedRowsKeepTheOrderOfTheJSONObjectAndTypeTheirFields(t *testing.T) {
	names, err := json.Marshal([]string{"syncing state"})
	if err != nil {
		t.Fatal(err)
	}
	raw, err := json.Marshal(map[string]any{
		"words":        syncing,
		"answer":       `["f958522"]`,
		"names":        string(names),
		"implications": `[{"n":1,"text":"t","direction":"removes","state":"proposed","proof":"seed"}]`,
		"updated_at":   "2026-10-23T11:58:25Z",
	})
	if err != nil {
		t.Fatal(err)
	}

	rows, err := parseRows([]byte(`{"rule/z":` + string(raw) + `,"rule/a":{"no":"a question"},"rule/m":{"kind":"gate"}}`))
	if err != nil {
		t.Fatal(err)
	}

	keys := make([]string, 0, len(rows))
	for _, row := range rows {
		keys = append(keys, row.key)
	}
	equal(t, keys, []string{"rule/z", "rule/a", "rule/m"})
	equal(t, rows[0].answers, []string{"f958522"})
	equal(t, rows[0].names, []string{"syncing state"})
	equal(t, rows[0].implications, []implication{{N: 1, Text: "t", Direction: "removes", State: "proposed"}})
	equal(t, rows[0].updated, stamp(t, "2026-10-23T11:58:25Z"))
	equal(t, rows[1].isNo, true)
	equal(t, rows[2].isNo, false)
}

func TestARowWithAMalformedFieldIsAnError(t *testing.T) {
	if _, err := parseRows([]byte(`{"rule/z":{"answer":"not json"}}`)); err == nil || !strings.Contains(err.Error(), "rule/z") {
		t.Fatalf("got %v", err)
	}
}
