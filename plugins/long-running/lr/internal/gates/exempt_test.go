package gates

import (
	"bufio"
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/yasyf/cc-inbox/client"
)

const monorepo = "Forge-AI/monorepo"

var (
	driveClasses = []string{"class owner-pick", "class regression", "class old-tree-port", "class twin", "class test-only", "class ci-only", "class dev-red"}
	driveTaps    = map[string][]string{"rule/82317b0": {"go/sand/next/*", "infra/ci/src/pipelines/test/sand.ts"}}
	goLanes      = []string{"go-carry-sweep", "go-carry-sweep-3", "go-sweep-8", "go-sweep-9"}
	countCard    = Card{ID: "card-48879-lookup-count-dedupe"}
)

func recordsBefore(t *testing.T, pr string, before int64) []client.Record {
	t.Helper()
	file, err := os.Open(filepath.Join("testdata", "pr-"+pr+".jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	var records []client.Record
	lines := bufio.NewScanner(file)
	lines.Buffer(nil, 1<<20)
	for lines.Scan() {
		var record client.Record
		if err := json.Unmarshal(lines.Bytes(), &record); err != nil {
			t.Fatal(err)
		}
		if record.Seq < before {
			records = append(records, record)
		}
	}
	if err := lines.Err(); err != nil {
		t.Fatal(err)
	}
	return records
}

func gate(records []client.Record, pr int, head string, cards ...Card) (string, []string) {
	return ExemptGo(Claim{Repo: monorepo, PR: pr, Head: head}, Read{Records: records, GoLanes: goLanes, Exempt: driveClasses, TapRules: driveTaps, Files: []string{"dashboard/src/iris/x.ts"}, Cards: cards})
}

func refusedWith(t *testing.T, text string, refusals []string, want string) {
	t.Helper()
	if text != "" || !slices.ContainsFunc(refusals, func(refusal string) bool { return strings.Contains(refusal, want) }) {
		t.Fatalf("got go %q and refusals %q, want a refusal saying %q", text, refusals, want)
	}
}

func TestTonightsWrongExemptGosAreRefusedFromTheRecordsTheirPostersRead(t *testing.T) {
	cases := map[string]struct {
		pr     string
		number int
		head   string
		before int64
		cards  []Card
		want   string
	}{
		"#48704 go 350192, a head his tap still holds":             {"48704", 48704, "cb9ae506fc0cd466404f9c05efa86ec23f04462a", 350192, nil, "fidelity-pipeline's newest grade #18147@06eb0455 is missing"},
		"#48375 go 355825, citing an override that does not exist": {"48375", 48375, "71bd0ab1c2dc27453798cbc7fb75db730f59bf84", 355825, nil, "finder: no finder-desk grade at 71bd0ab, and no root decide reads finder-override #48375"},
		"#48375 after ask 355738, whose text reads as an override": {"48375", 48375, "71bd0ab1c2dc27453798cbc7fb75db730f59bf84", 355739, nil, "finder: no finder-desk grade at 71bd0ab"},
		"#48879 go 357125, over his open card":                     {"48879", 48879, "c541801b719e3c072b38b1d4ede5afb7af23d3f6", 357125, []Card{countCard}, "owner card card-48879-lookup-count-dedupe is open"},
		"#48879 go 357125, over a missing fidelity grade":          {"48879", 48879, "c541801b719e3c072b38b1d4ede5afb7af23d3f6", 357125, nil, "fidelity-pipeline's newest grade #55194@b5522464 is missing"},
		"#48707 under typed hold 355470":                           {"48707", 48707, "8fcfba3211db21eb43c6dedb4a1ad6e02eda069d", 355471, nil, "hold #355470 by landing-desk-3 is open"},
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			text, refusals := gate(recordsBefore(t, c.pr, c.before), c.number, c.head, c.cards...)
			refusedWith(t, text, refusals, c.want)
		})
	}
}

func holdRecord(t *testing.T, raw string) client.Record {
	t.Helper()
	var record client.Record
	if err := json.Unmarshal([]byte(raw), &record); err != nil {
		t.Fatal(err)
	}
	return record
}

func TestAHoldHoldsThePRsItWasPostedWithAndItsTextIsProse(t *testing.T) {
	hold := holdRecord(t, `{"seq":2948,"lane":"root","kind":"hold","text":"hold #2 until the cutover, behind the test PR #2","refs":{"prs":["Forge-AI/monorepo#1"]}}`)

	for pr, want := range map[int]int{1: 1, 2: 0} {
		if open := openHolds(Claim{Repo: monorepo, PR: pr}, []client.Record{hold}, nil); len(open) != want {
			t.Fatalf("#%d: open holds %q", pr, open)
		}
	}
}

func TestAHoldThatLiftsAnEarlierHoldIsNotOpen(t *testing.T) {
	lift := holdRecord(t, `{"seq":364345,"lane":"root","kind":"hold","text":"hold #48889 lift: the rework is done","resolves":362361,"refs":{"prs":["Forge-AI/monorepo#48889"]}}`)
	claim := Claim{Repo: monorepo, PR: 48889}

	if open := openHolds(claim, []client.Record{lift}, map[int64]bool{lift.Seq: true}); len(open) != 0 {
		t.Fatalf("a lift reads as open: %q", open)
	}
	if open := openHolds(claim, []client.Record{lift}, nil); len(open) != 1 {
		t.Fatalf("a hold that lifts nothing should stay open, got %q", open)
	}
}

func TestAHoldWrittenAsProseInARootRecordStopsNoGo(t *testing.T) {
	text, refusals := gate(recordsBefore(t, "48707", 355281), 48707, "8fcfba3211db21eb43c6dedb4a1ad6e02eda069d")
	if len(refusals) > 0 || !strings.HasPrefix(text, "go #48707 at 8fcfba3 on green [exempt: owner-pick]") {
		t.Fatalf("got go %q and refusals %q; root 342984's hold was prose, so only typed hold 355470 stops this go", text, refusals)
	}
}

func TestARootDecideOverridingTheFinderAtTheHeadSettlesTheFinder(t *testing.T) {
	_, refusals := gate(recordsBefore(t, "48704", 350192), 48704, "cb9ae506fc0cd466404f9c05efa86ec23f04462a")
	if slices.ContainsFunc(refusals, func(refusal string) bool { return strings.HasPrefix(refusal, "finder:") }) {
		t.Fatalf("root 349895 overrides the finder at cb9ae50 citing 18115@06eb0455, yet got %q", refusals)
	}
}

func TestAnOverrideCitingARecordThatDoesNotExistSettlesNothing(t *testing.T) {
	records := recordsBefore(t, "48375", 355825)
	records = append(records, client.Record{Seq: 355824, Lane: "root", Kind: "decide", Text: "12:22 AM finder-override #48375 at 71bd0ab: 346573"})
	text, refusals := gate(records, 48375, "71bd0ab1c2dc27453798cbc7fb75db730f59bf84")
	refusedWith(t, text, refusals, "no root decide reads finder-override #48375 at 71bd0ab citing a record that exists")
}

func TestAGradedHeadWithRootsOverrideAndAnExemptClassGetsItsGo(t *testing.T) {
	text, refusals := gate(recordsBefore(t, "48375", 356113), 48375, "71bd0ab1c2dc27453798cbc7fb75db730f59bf84")
	if want := "go #48375 at 71bd0ab on green [exempt: owner-pick]: fidelity ok #349440, finder ok #54478@c19fa6c9"; text != want || len(refusals) > 0 {
		t.Fatalf("got go %q and refusals %q, want %q", text, refusals, want)
	}
}

func TestTheSameHeadIsRefusedWhenTheGoLaneAlreadyPostedItsGo(t *testing.T) {
	records := append(recordsBefore(t, "48375", 356113), client.Record{Seq: 356114, Lane: "go-sweep-9", Kind: "go", Text: "go #48375 at 71bd0ab on green [exempt: owner-pick]"})
	text, refusals := gate(records, 48375, "71bd0ab1c2dc27453798cbc7fb75db730f59bf84")
	refusedWith(t, text, refusals, "go #356114 by go-sweep-9 already names this head")
}

func TestAPlainGoNamingTheHeadDoesNotStopTheGoThatCarriesItsClass(t *testing.T) {
	records := append(recordsBefore(t, "48375", 356113), client.Record{Seq: 356114, Lane: "root", Kind: "go", Text: "go #48375 at 71bd0ab TAP-CARRIED: his tap on #48000 carries"})
	text, refusals := gate(records, 48375, "71bd0ab1c2dc27453798cbc7fb75db730f59bf84")
	if text == "" || len(refusals) > 0 {
		t.Fatalf("got go %q and refusals %q", text, refusals)
	}
}

func TestAClassOutsideTheDrivesExemptGradesNeedsHisTap(t *testing.T) {
	text, refusals := ExemptGo(Claim{Repo: monorepo, PR: 48375, Head: "71bd0ab1c2dc27453798cbc7fb75db730f59bf84"}, Read{Records: recordsBefore(t, "48375", 356113), Exempt: DefaultExemptGrades})
	refusedWith(t, text, refusals, "fidelity: no ok grade at 71bd0ab names an exempt class")
}

func TestATapRuleMatchingAChangedFileHoldsTheHeadForHisTap(t *testing.T) {
	read := Read{Records: recordsBefore(t, "48375", 356113), Exempt: driveClasses, TapRules: driveTaps, Files: []string{"infra/rows/x.ts", "go/sand/next/internal/vm/run.go"}}
	text, refusals := ExemptGo(Claim{Repo: monorepo, PR: 48375, Head: "71bd0ab1c2dc27453798cbc7fb75db730f59bf84"}, read)
	refusedWith(t, text, refusals, "tap rule rule/82317b0 names go/sand/next/internal/vm/run.go")
}

func TestAnExemptGoIsRefusedForABodyThatWaitsToGoLiveWithNoWayItIsSafeOnDev(t *testing.T) {
	read := Read{Records: recordsBefore(t, "48375", 356113), GoLanes: goLanes, Exempt: driveClasses, TapRules: driveTaps, Files: []string{"dashboard/src/iris/x.ts"}, Body: pr46018 + "\nHold this PR until that release is live.\n"}
	text, refusals := ExemptGo(Claim{Repo: monorepo, PR: 48375, Head: "71bd0ab1c2dc27453798cbc7fb75db730f59bf84"}, read)
	if want := UnsafeOnDev("Hold this PR until that release is live"); text != "" || !slices.Equal(refusals, []string{want}) {
		t.Fatalf("got go %q and refusals %q, want only %q", text, refusals, want)
	}
}

const (
	portPR   = 49999
	portHead = "abc1234def5678901234567890123456789012ab"
	portBase = "fed4321def5678901234567890123456789012ab"
)

func decoded(t *testing.T, line string) client.Record {
	t.Helper()
	var record client.Record
	if err := json.Unmarshal([]byte(line), &record); err != nil {
		t.Fatal(err)
	}
	return record
}

func graded(t *testing.T, grade string) []client.Record {
	t.Helper()
	return []client.Record{
		decoded(t, `{"seq":1,"lane":"finder-desk","kind":"review","text":"finder: ok `+portHead+`","refs":{"prs":[49999]}}`),
		decoded(t, `{"seq":2,"lane":"fidelity-pipeline","kind":"review","text":"fidelity: ok `+portHead+` `+grade+`: ports #49000 line for line","refs":{"prs":[49999]}}`),
	}
}

func headRecord(t *testing.T, head, attrs string) client.Record {
	t.Helper()
	return decoded(t, `{"seq":3,"lane":"port-x","kind":"head","text":"HEAD","refs":{"prs":[49999]},"fields":{"attrs":{"head:49999":"`+head+`"`+attrs+`}}}`)
}

func TestAPortLandsWithNoTapOnlyWhenItsHeadRecordProvesItCleanAndEveryOtherGateHolds(t *testing.T) {
	const clean = `,"port-of":"49000","port-interacts":"no"`
	next := []string{"go/sand/next/internal/vm/run.go"}
	cases := map[string]struct {
		grade  string
		head   string
		attrs  string
		files  []string
		source bool
		want   string
	}{
		"a clean port lands past the tap rule":                       {"class old-tree-port", portHead, clean, next, true, ""},
		"a moved head record names the head it moved to":             {"class old-tree-port", portBase + ".." + portHead + " rebase", clean, next, true, ""},
		"the older default port phrase counts too":                   {PortGrade, portHead, clean, next, true, ""},
		"a grade naming two classes reads as the port it proves":     {"class regression: class old-tree-port", portHead, clean, next, true, ""},
		"the same shape with no head record needs his tap":           {"class old-tree-port", "", "", next, true, "port: no head record at abc1234 carries port-of"},
		"a head record with no port-of proves nothing":               {"class old-tree-port", portHead, `,"port-interacts":"no"`, next, true, "port: no head record"},
		"a head record at an older head proves nothing here":         {"class old-tree-port", portBase, clean, next, true, "port: no head record"},
		"a non-port grade with a port's files stays on the tap rule": {"class owner-pick", portHead, clean, next, true, "tap rule rule/82317b0 names go/sand/next/internal/vm/run.go"},
		"a port marked interacting needs his tap":                    {"class old-tree-port", portHead, `,"port-of":"49000","port-interacts":"yes"`, next, true, "port-interacts=no, so it goes to the owner on one card"},
		"a port silent on interaction counts as interacting":         {"class old-tree-port", portHead, `,"port-of":"49000"`, next, true, "port-interacts=no"},
		"a source that never merged is no port":                      {"class old-tree-port", portHead, clean, next, false, "its source #49000 is not a merged pull request"},
		"a file outside the next tree is no pure port":               {"class old-tree-port", portHead, clean, append([]string{"infra/rows/x.ts"}, next...), true, "infra/rows/x.ts is outside go/sand/next/"},
		"a missing fidelity class stays refused for a port's head":   {"looks fine", portHead, clean, next, true, "fidelity: no ok grade at abc1234 names an exempt class"},
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			records := graded(t, c.grade)
			if c.head != "" {
				records = append(records, headRecord(t, c.head, c.attrs))
			}
			text, refusals := ExemptGo(Claim{Repo: monorepo, PR: portPR, Head: portHead}, Read{Records: records, Exempt: Grades(nil, driveClasses, ""), TapRules: driveTaps, Files: c.files, PortSourceMerged: c.source})
			if c.want != "" {
				refusedWith(t, text, refusals, c.want)
				return
			}
			if len(refusals) > 0 || !strings.Contains(text, "[exempt: ") {
				t.Fatalf("got go %q and refusals %q, want a go", text, refusals)
			}
		})
	}
}

func TestAPortGoNamesAClassTheBusReadsAsExempt(t *testing.T) {
	records := append(graded(t, "class old-tree-port"), headRecord(t, portHead, `,"port-of":"49000","port-interacts":"no"`))
	text, refusals := ExemptGo(Claim{Repo: monorepo, PR: portPR, Head: portHead}, Read{Records: records, Exempt: driveClasses, TapRules: driveTaps, Files: []string{"go/sand/next/a.go"}, PortSourceMerged: true})
	if want := "go #49999 at abc1234 on green [exempt: old-tree-port]: fidelity ok #2, finder ok #1"; text != want || len(refusals) > 0 {
		t.Fatalf("got go %q and refusals %q, want %q", text, refusals, want)
	}
}

func TestANewerHeadRecordSayingItInteractsOutranksAnOlderCleanOne(t *testing.T) {
	records := append(graded(t, "class old-tree-port"), headRecord(t, portHead, `,"port-of":"49000","port-interacts":"no"`), headRecord(t, portHead, `,"port-of":"49000","port-interacts":"yes"`))
	claim, claimed := PortAt(Claim{Repo: monorepo, PR: portPR, Head: portHead}, records)
	if !claimed || !claim.Interacts || claim.Source != 49000 {
		t.Fatalf("got %+v, claimed %v; want the newest record's interacting claim on #49000", claim, claimed)
	}
}
