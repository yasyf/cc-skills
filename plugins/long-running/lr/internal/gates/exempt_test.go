package gates

import (
	"bufio"
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"testing"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
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
	text, _, refusals := ExemptGo(Claim{Repo: monorepo, PR: pr, Head: head}, Read{Records: records, GoLanes: goLanes, Exempt: driveClasses, TapRules: driveTaps, Files: []string{"dashboard/src/iris/x.ts"}, Cards: cards})
	return text, refusals
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
		"#48375 go 355825, citing an override that does not exist": {"48375", 48375, "71bd0ab1c2dc27453798cbc7fb75db730f59bf84", 355825, nil, "finder: no finder-desk grade at 71bd0ab, and no counted decide reads finder-override #48375"},
		"#48375 after ask 355738, whose text reads as an override": {"48375", 48375, "71bd0ab1c2dc27453798cbc7fb75db730f59bf84", 355739, nil, "finder: no finder-desk grade at 71bd0ab"},
		"#48879 go 357125, over his open card":                     {"48879", 48879, "c541801b719e3c072b38b1d4ede5afb7af23d3f6", 357125, []Card{countCard}, "owner card card-48879-lookup-count-dedupe is open"},
		"#48879 go 357125, over a missing fidelity grade":          {"48879", 48879, "c541801b719e3c072b38b1d4ede5afb7af23d3f6", 357125, nil, "fidelity-pipeline's newest grade #55194@b5522464 is missing"},
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
	hold := holdRecord(t, `{"seq":2948,"lane":"root","kind":"hold","text":"hold #2 until the cutover, behind the test PR #2","refs":{"prs":["Forge-AI/monorepo#1"]},"fields":{"attrs":{"breaks":"the cutover deletes what it reads"}}}`)

	for pr, want := range map[int]int{1: 1, 2: 0} {
		if open := openHolds(Claim{Repo: monorepo, PR: pr}, []client.Record{hold}, nil, nil); len(open) != want {
			t.Fatalf("#%d: open holds %q", pr, open)
		}
	}
}

func TestOnlyHisHoldOrOneNamingAProductionFailureStopsAGo(t *testing.T) {
	claim := Claim{Repo: monorepo, PR: 50011}
	for name, c := range map[string]struct {
		hold string
		open int
	}{
		"run-search-shape's lane hold 450329":      {`{"seq":450329,"lane":"run-search-shape","kind":"hold","text":"until #49305 lands","refs":{"prs":["Forge-AI/monorepo#50011"]}}`, 0},
		"root's plain hold":                        {`{"seq":457669,"lane":"root","kind":"hold","text":"hold #50011: copy pass first","refs":{"prs":["Forge-AI/monorepo#50011"]}}`, 0},
		"his own hold from the board":              {`{"seq":565538,"lane":"owner","kind":"hold","text":"hold #50011 at 61c1c7ab3c07: the owner rejected this head on the board","refs":{"prs":["Forge-AI/monorepo#50011"]}}`, 1},
		"a root hold naming a production failure":  {`{"seq":567568,"lane":"supervisor","kind":"hold","text":"readers-first: #49305 not live on every client.","refs":{"prs":["Forge-AI/monorepo#50011"]},"fields":{"attrs":{"breaks":"#50011 writes a format that a client still on a build without #49305 cannot read"}}}`, 1},
		"a lane hold citing his words":             {`{"seq":574001,"lane":"old-tree-port-desk-1","kind":"hold","text":"#50011 deletes old-tree code before Step 11","refs":{"prs":["Forge-AI/monorepo#50011"]},"fields":{"attrs":{"ruling":"18082c8"}}}`, 1},
		"a lane hold citing a note not his answer": {`{"seq":574002,"lane":"root","kind":"hold","text":"copy pass first","refs":{"prs":["Forge-AI/monorepo#50011"]},"fields":{"attrs":{"ruling":"c3230a2"}}}`, 0},
	} {
		t.Run(name, func(t *testing.T) {
			if open := openHolds(claim, []client.Record{holdRecord(t, c.hold)}, nil, map[string]bool{"18082c8": true, "c3230a2": false}); len(open) != c.open {
				t.Fatalf("open holds %q, want %d", open, c.open)
			}
		})
	}
}

func TestAHoldThatLiftsAnEarlierHoldIsNotOpen(t *testing.T) {
	lift := holdRecord(t, `{"seq":364345,"lane":"root","kind":"hold","text":"hold #48889 lift: the rework is done","resolves":362361,"refs":{"prs":["Forge-AI/monorepo#48889"]},"fields":{"attrs":{"breaks":"the rework is not in yet"}}}`)
	claim := Claim{Repo: monorepo, PR: 48889}

	if open := openHolds(claim, []client.Record{lift}, map[int64]bool{lift.Seq: true}, nil); len(open) != 0 {
		t.Fatalf("a lift reads as open: %q", open)
	}
	if open := openHolds(claim, []client.Record{lift}, nil, nil); len(open) != 1 {
		t.Fatalf("a hold that lifts nothing should stay open, got %q", open)
	}
}

func TestAHoldCciReadsClosedIsNotOpenThoughItsLiftNamesNoPR(t *testing.T) {
	claim := Claim{Repo: monorepo, PR: 51563}
	for status, want := range map[string]int{"closed": 0, "open": 1} {
		hold := holdRecord(t, `{"seq":521104,"lane":"owner","kind":"hold","text":"hold #51563 at 08906a2: the owner rejected this head on the board","re":520879,"refs":{"prs":["Forge-AI/monorepo#51563"]},"status":"`+status+`","num":"#521104"}`)
		if open := openHolds(claim, []client.Record{hold}, nil, nil); len(open) != want {
			t.Fatalf("status %s: open holds %q, want %d", status, open, want)
		}
	}
}

func TestNeitherRootsProseHoldNorALanesTypedHoldNamingNoFailureStopsAGo(t *testing.T) {
	for _, before := range []int64{355281, 355471} {
		_, refusals := gate(recordsBefore(t, "48707", before), 48707, "8fcfba3211db21eb43c6dedb4a1ad6e02eda069d")
		if slices.ContainsFunc(refusals, func(refusal string) bool { return strings.HasPrefix(refusal, "hold ") }) {
			t.Fatalf("before %d, refusals %q; root 342984 wrote prose and landing-desk-3's 355470 names no breaks=, so neither holds", before, refusals)
		}
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
	refusedWith(t, text, refusals, "no counted decide reads finder-override #48375 at 71bd0ab citing a record that exists")
}

func TestAnOwnerPickTheDriveListsIsNoClassHisRulingsLandUntappedSoItWaitsOnHisTap(t *testing.T) {
	text, refusals := gate(recordsBefore(t, "48375", 356113), 48375, "71bd0ab1c2dc27453798cbc7fb75db730f59bf84")
	refusedWith(t, text, refusals, "fidelity: no ok grade at 71bd0ab names an exempt class")
	if slices.ContainsFunc(refusals, func(refusal string) bool { return strings.HasPrefix(refusal, "finder:") }) {
		t.Fatalf("root's override settles the finder, yet got %q", refusals)
	}
}

func TestOnlyAClassHisRulingsLandUntappedGetsAGo(t *testing.T) {
	for grade, lands := range map[string]bool{
		"class test-only":  true,
		"class ci-only":    true,
		TestOnlyGrade:      true,
		"class dev-red":    false,
		"class owner-pick": false,
		"class regression": false,
		"class twin":       false,
	} {
		t.Run(grade, func(t *testing.T) {
			text, _, refusals := ExemptGo(Claim{Repo: monorepo, PR: portPR, Head: portHead}, Read{Records: graded(t, grade), Exempt: Grades(nil, driveClasses, ""), Files: []string{"go/x/x_test.go"}})
			if !lands {
				refusedWith(t, text, refusals, "fidelity: no ok grade at abc1234 names an exempt class")
				return
			}
			if len(refusals) > 0 || !strings.HasPrefix(text, "go #49999 at abc1234 on green [exempt: ") {
				t.Fatalf("got go %q and refusals %q, want a go", text, refusals)
			}
		})
	}
}

func TestTheSameHeadIsRefusedWhenTheGoLaneAlreadyPostedItsGo(t *testing.T) {
	posted := client.Record{Seq: 356114, Lane: "go-sweep-9", Kind: "go", Text: "go #48375 at 71bd0ab on green [exempt: test-only]"}
	posted.Fields.Attrs = map[string]string{bus.ExemptClassAttr: "test-only"}
	records := append(recordsBefore(t, "48375", 356113), posted)
	text, refusals := gate(records, 48375, "71bd0ab1c2dc27453798cbc7fb75db730f59bf84")
	refusedWith(t, text, refusals, "go #356114 by go-sweep-9 already names this head")
}

func TestAPlainGoNamingTheHeadDoesNotStopTheGoThatCarriesItsClass(t *testing.T) {
	records := append(recordsBefore(t, "48375", 356113), client.Record{Seq: 356114, Lane: "root", Kind: "go", Text: "go #48375 at 71bd0ab TAP-CARRIED: his tap on #48000 carries"})
	_, refusals := gate(records, 48375, "71bd0ab1c2dc27453798cbc7fb75db730f59bf84")
	if slices.ContainsFunc(refusals, func(refusal string) bool { return strings.Contains(refusal, "already names this head") }) {
		t.Fatalf("root's plain go reads as the exempt go: %q", refusals)
	}
}

func TestAClassOutsideTheDrivesExemptGradesNeedsHisTap(t *testing.T) {
	text, _, refusals := ExemptGo(Claim{Repo: monorepo, PR: 48375, Head: "71bd0ab1c2dc27453798cbc7fb75db730f59bf84"}, Read{Records: recordsBefore(t, "48375", 356113), Exempt: DefaultExemptGrades})
	refusedWith(t, text, refusals, "fidelity: no ok grade at 71bd0ab names an exempt class")
}

func TestATapRuleMatchingAChangedFileHoldsTheHeadForHisTap(t *testing.T) {
	read := Read{Records: recordsBefore(t, "48375", 356113), Exempt: driveClasses, TapRules: driveTaps, Files: []string{"infra/rows/x.ts", "go/sand/next/internal/vm/run.go"}}
	text, _, refusals := ExemptGo(Claim{Repo: monorepo, PR: 48375, Head: "71bd0ab1c2dc27453798cbc7fb75db730f59bf84"}, read)
	refusedWith(t, text, refusals, "tap rule rule/82317b0 names go/sand/next/internal/vm/run.go")
}

func TestAnExemptGoIsRefusedForABodyThatWaitsToGoLiveWithNoWayItIsSafeOnDev(t *testing.T) {
	read := Read{Records: recordsBefore(t, "48375", 356113), GoLanes: goLanes, Exempt: driveClasses, TapRules: driveTaps, Files: []string{"dashboard/src/iris/x.ts"}, Body: pr46018 + "\nHold this PR until that release is live.\n"}
	text, _, refusals := ExemptGo(Claim{Repo: monorepo, PR: 48375, Head: "71bd0ab1c2dc27453798cbc7fb75db730f59bf84"}, read)
	refusedWith(t, text, refusals, UnsafeOnDev("Hold this PR until that release is live"))
}

func TestAnExemptGoWaitsOnAnOpenCardItsBodyNamesThoughTheCardNamesNoPR(t *testing.T) {
	body := "Lands only on the owner's answer on card rmw3cuhus. Built ahead of that answer; not for merge before it.\n\n## Context\n"
	pacing := Card{ID: "card-rmw3cuhus", Title: "Which pacing should a 429 with no team get?"}
	other := Card{ID: "card-rmw38erwu", Title: "Open the chat window pack?"}
	claim := Claim{Repo: monorepo, PR: 48375, Head: "71bd0ab1c2dc27453798cbc7fb75db730f59bf84"}
	read := func(cards ...Card) Read {
		return Read{Records: recordsBefore(t, "48375", 356113), GoLanes: goLanes, Exempt: driveClasses, TapRules: driveTaps, Files: []string{"dashboard/src/iris/x.ts"}, Body: body, Cards: cards}
	}

	text, _, refusals := ExemptGo(claim, read(pacing, other))
	refusedWith(t, text, refusals, "owner card card-rmw3cuhus is open")
	if _, _, refusals := ExemptGo(claim, read(other)); slices.ContainsFunc(refusals, func(refusal string) bool { return strings.HasPrefix(refusal, "owner card") }) {
		t.Fatalf("with no open card its body names, got refusals %q", refusals)
	}
}

func TestABodyNamesACardByItsWholeID(t *testing.T) {
	cards := []Card{{ID: "card-memory-schema"}, {ID: "card-memory"}, {ID: "card-rmw3cuhus"}}
	for body, want := range map[string][]string{
		"Waits on card-memory-schema.":            {"owner card card-memory-schema is open"},
		"Waits on Card rmw3cuhus, then lands.":    {"owner card card-rmw3cuhus is open"},
		"Reads the memory schema the cards hold.": nil,
		"Follows card-memory-schema-v2.":          nil,
	} {
		if got := OpenCards(50000, body, cards); !slices.Equal(got, want) {
			t.Errorf("%q: got %q, want %q", body, got, want)
		}
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

func pipelineGrade(t *testing.T, seq int, verdict, class string) client.Record {
	t.Helper()
	record := decoded(t, `{"seq":`+strconv.Itoa(seq)+`,"lane":"fidelity-pipeline","kind":"review","text":"fidelity: `+verdict+` `+portHead+` keeps it out of class test-only, as test-only or CI-only, no non-test code","refs":{"prs":[49999]}}`)
	record.Fields.Attrs = map[string]string{FidelityClassAttr: class}
	return record
}

func TestTheGateReadsTheClassFromThePipelinesFieldAndNeverFromTypedText(t *testing.T) {
	finder := decoded(t, `{"seq":1,"lane":"finder-desk","kind":"review","text":"finder: ok `+portHead+`","refs":{"prs":[49999]}}`)
	desk := decoded(t, `{"seq":3,"lane":"fidelity-desk-31","kind":"review","text":"fidelity: ok `+portHead+` class owner-pick: desk grade for root, in root's owner-pick class.","refs":{"prs":[49999]}}`)
	override := client.Record{Seq: 4, Lane: "root", Kind: "decide", Text: "fidelity-override #49999 at abc1234: 2"}
	ciOnly := []string{"go/ci/prune.go", "go/ci/prune_test.go", "infra/ci/src/cache/units.ts"}
	passBox := []string{"docs/pass-boxes.md", "go/ci/passbox.go", "go/ci/passbox_test.go", "tools/loadtest/tasks/README.md"}
	for name, c := range map[string]struct {
		records []client.Record
		files   []string
		lands   string
		refused string
	}{
		"a CI-only diff the pipeline names test-only or CI-only":     {[]client.Record{finder, pipelineGrade(t, 2, "ok", ClassOf(TestOnlyGrade))}, ciOnly, ClassOf(TestOnlyGrade), ""},
		"remote tooling the pipeline names ci-tooling needs his tap": {[]client.Record{finder, pipelineGrade(t, 2, "ok", "ci-tooling")}, []string{"tools/cc-remote/config.yaml"}, "", "names class ci-tooling"},
		"a missing grade root overrode keeps the class it names":     {[]client.Record{finder, pipelineGrade(t, 2, "missing", ClassOf(TestOnlyGrade)), override}, ciOnly, ClassOf(TestOnlyGrade), ""},
		"#51878's pass-box verb the pipeline names none":             {[]client.Record{finder, pipelineGrade(t, 2, "ok", NoClass)}, passBox, "", "fidelity-pipeline's grade #2 names class none"},
		"#51878 overridden still names none":                         {[]client.Record{finder, pipelineGrade(t, 2, "missing", NoClass), override}, passBox, "", "names class none"},
		"a desk typing a class beside the pipeline's none":           {[]client.Record{finder, pipelineGrade(t, 2, "ok", NoClass), desk}, passBox, "", "fidelity-pipeline's grade #2 names class none; fidelity-desk-31's grade #3 types class owner-pick, but only fidelity-pipeline's fidelity-class attr names a head's class, so his tap lands it"},
		"a desk typing a class over an overridden missing":           {[]client.Record{finder, pipelineGrade(t, 2, "missing", NoClass), desk, override}, passBox, "", "names an exempt class"},
		"a class his rulings do not land untapped needs his tap":     {[]client.Record{finder, pipelineGrade(t, 2, "ok", "owner-pick")}, ciOnly, "", "names class owner-pick"},
		"an ok the pipeline posted before the field reads its text":  {[]client.Record{finder, decoded(t, `{"seq":2,"lane":"fidelity-pipeline","kind":"review","text":"fidelity: ok `+portHead+` `+TestOnlyGrade+`: one test","refs":{"prs":[49999]}}`)}, ciOnly, ClassOf(TestOnlyGrade), ""},
	} {
		t.Run(name, func(t *testing.T) {
			text, class, refusals := ExemptGo(Claim{Repo: monorepo, PR: portPR, Head: portHead}, Read{Records: c.records, Exempt: Grades(nil, []string{"class ci-tooling", "class owner-pick"}, ""), Files: c.files})
			if c.refused != "" {
				refusedWith(t, text, refusals, c.refused)
				return
			}
			if len(refusals) > 0 || class != c.lands || !strings.Contains(text, "[exempt: "+c.lands+"]") {
				t.Fatalf("got go %q of class %q and refusals %q, want a go of class %q", text, class, refusals, c.lands)
			}
		})
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
		"a clean port lands past the tap rule":                             {"class old-tree-port", portHead, clean, next, true, ""},
		"a clean port lands whichever class its grade names":               {"class twin", portHead, clean, next, true, ""},
		"a clean claim the go disproves names why, not only the tap rule":  {"class owner-pick", portHead, clean, next, false, "its source #49000 is not a merged pull request"},
		"a clean port lands on an ok that names no class":                  {"looks fine", portHead, clean, next, true, ""},
		"a moved head record names the head it moved to":                   {"class old-tree-port", portBase + ".." + portHead + " rebase", clean, next, true, ""},
		"the older default port phrase counts too":                         {PortGrade, portHead, clean, next, true, ""},
		"a grade naming two classes reads as the port it proves":           {"class regression: class old-tree-port", portHead, clean, next, true, ""},
		"the same shape with no head record needs his tap":                 {"class old-tree-port", "", "", next, true, "port: no head record at abc1234 carries port-of"},
		"a head record with no port-of proves nothing":                     {"class old-tree-port", portHead, `,"port-interacts":"no"`, next, true, "port: no head record"},
		"a head record at an older head proves nothing here":               {"class old-tree-port", portBase, clean, next, true, "port: no head record"},
		"an unclean port graded as another class stays on the tap rule":    {"class owner-pick", portHead, `,"port-of":"49000","port-interacts":"yes"`, next, true, "tap rule rule/82317b0 names go/sand/next/internal/vm/run.go"},
		"a PR with no port claim stays on the tap rule":                    {"class owner-pick", portHead, "", next, true, "tap rule rule/82317b0 names go/sand/next/internal/vm/run.go"},
		"a port marked interacting needs his tap":                          {"class old-tree-port", portHead, `,"port-of":"49000","port-interacts":"yes"`, next, true, "port-interacts=no, and no fidelity grade of pure port, no interaction or class old-tree-port stands in"},
		"a port silent on interaction its grade names old-tree-port lands": {"class old-tree-port", portHead, `,"port-of":"49000"`, next, true, ""},
		"a port silent on interaction graded another class stays tapped":   {"class twin", portHead, `,"port-of":"49000"`, next, true, "tap rule rule/82317b0"},
		"a source that never merged is no port":                            {"class old-tree-port", portHead, clean, next, false, "its source #49000 is not a merged pull request"},
		"a file outside the next tree is no pure port":                     {"class old-tree-port", portHead, clean, append([]string{"infra/rows/x.ts"}, next...), true, "infra/rows/x.ts is outside go/sand/next/"},
		"a missing fidelity class stays refused for an unclean port":       {"looks fine", portHead, `,"port-of":"49000"`, next, true, "fidelity: no ok grade at abc1234 names an exempt class"},
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			records := graded(t, c.grade)
			if c.head != "" {
				records = append(records, headRecord(t, c.head, c.attrs))
			}
			text, _, refusals := ExemptGo(Claim{Repo: monorepo, PR: portPR, Head: portHead}, Read{Records: records, Exempt: Grades(nil, driveClasses, ""), TapRules: driveTaps, Files: c.files, PortSourceMerged: c.source})
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

func TestAPortItsBodyOrASilentHeadRecordNamesLandsWithNoTapOnlyWhenFidelityGradesItPure(t *testing.T) {
	const opening = "Port, old tree to new: dev #49000 (port of #49000)\n\n## Context"
	next := []string{"go/sand/next/internal/platformapi/oauth.go"}
	for name, c := range map[string]struct {
		grade string
		desk  string
		attrs string
		body  string
		want  string
	}{
		"#52183: its body names the source and fidelity grades it pure":            {PortGrade, "", "", opening, ""},
		"the older port of line counts as its body's port words":                   {PortGrade, "", "", "port of #49000\n\nthe rest", ""},
		"a head record silent on interaction, graded pure":                         {PortGrade, "", `,"port-of":"49000"`, "", ""},
		"its body names the source and fidelity grades it old-tree-port":           {"class old-tree-port", "", "", opening, ""},
		"its body names the source and fidelity grades it some other class":        {"class twin", "", "", opening, "tap rule rule/82317b0"},
		"a lane that says it interacts outranks a pure grade":                      {PortGrade, "", `,"port-of":"49000","port-interacts":"yes"`, opening, "port-interacts=no"},
		"port words past the opening name no source":                               {PortGrade, "", "", "sand-next: a fix (port of #49000)", "port: no head record at abc1234 carries port-of=<old-tree PR> and its body does not open"},
		"#51495: the pipeline grades it test-only and fidelity-desk old-tree-port": {"looks fine", "class old-tree-port", "", opening, ""},
		"the pipeline grades it test-only and fidelity-desk some other class":      {"looks fine", "class twin", "", opening, "tap rule rule/82317b0"},
	} {
		t.Run(name, func(t *testing.T) {
			records := graded(t, c.grade)
			if c.desk != "" {
				records = append(records, pipelineGrade(t, 2, "ok", ClassOf(TestOnlyGrade)), decoded(t, `{"seq":3,"lane":"fidelity-desk-31","kind":"review","text":"fidelity: ok `+portHead+` `+c.desk+`: line for line","refs":{"prs":[49999]}}`))
			}
			if c.attrs != "" {
				records = append(records, headRecord(t, portHead, c.attrs))
			}
			read := Read{Records: records, Exempt: Grades(nil, driveClasses, ""), TapRules: driveTaps, Files: next, PortSourceMerged: true, Body: c.body}
			text, class, refusals := ExemptGo(Claim{Repo: monorepo, PR: portPR, Head: portHead}, read)
			if c.want != "" {
				refusedWith(t, text, refusals, c.want)
				return
			}
			if len(refusals) > 0 || class != OldTreePortClass {
				t.Fatalf("got go %q of class %q and refusals %q, want an old-tree-port go past the tap rule", text, class, refusals)
			}
		})
	}
}

func TestACleanPortWhoseOnlyFidelityIsACountedOverrideLands(t *testing.T) {
	missing := []client.Record{
		decoded(t, `{"seq":1,"lane":"finder-desk","kind":"review","text":"finder: ok `+portHead+`","refs":{"prs":[49999]}}`),
		decoded(t, `{"seq":2,"lane":"fidelity-pipeline","kind":"review","text":"fidelity: missing `+portHead+` a doc line","refs":{"prs":[49999]}}`),
	}
	override := client.Record{Seq: 4, Lane: "root", Kind: "decide", Text: "fidelity-override #49999 at abc1234: 2. Port of #49000."}
	clean := `,"port-of":"49000","port-interacts":"no"`
	for name, c := range map[string]struct {
		records []client.Record
		want    string
	}{
		"overridden, it lands":                               {append(slices.Clone(missing), headRecord(t, portHead, clean), override), ""},
		"not overridden, the grade stands":                   {append(slices.Clone(missing), headRecord(t, portHead, clean)), "fidelity: no ok grade at abc1234"},
		"overridden with no port claim, the tap rule stands": {append(slices.Clone(missing), override), "tap rule rule/82317b0"},
	} {
		t.Run(name, func(t *testing.T) {
			text, _, refusals := ExemptGo(Claim{Repo: monorepo, PR: portPR, Head: portHead}, Read{Records: c.records, Exempt: Grades(nil, driveClasses, ""), TapRules: driveTaps, Files: []string{"go/sand/next/a.go"}, PortSourceMerged: true})
			if c.want != "" {
				refusedWith(t, text, refusals, c.want)
				return
			}
			if want := "[exempt: old-tree-port]: fidelity-override #4,"; len(refusals) > 0 || !strings.Contains(text, want) {
				t.Fatalf("got go %q and refusals %q, want %q", text, refusals, want)
			}
		})
	}
}

func TestAFinderOverrideWrittenOnAnotherStoreCountsWithItsBareSeqCite(t *testing.T) {
	major := decoded(t, `{"seq":1,"lane":"finder-desk","kind":"review","text":"finder: major `+portHead+` two rows","refs":{"prs":[49999]}}`)
	fidelity := decoded(t, `{"seq":2,"lane":"fidelity-desk","kind":"review","text":"fidelity: ok `+portHead+` class old-tree-port: line for line","refs":{"prs":[49999]}}`)
	head := headRecord(t, portHead, `,"port-of":"49000","port-interacts":"no"`)
	override := func(num, cites string) client.Record {
		return client.Record{Seq: 272617, Num: num, Lane: "root", Kind: "decide", Text: "finder-override #49999 at abc1234: " + cites + ". Port of #49000."}
	}
	for name, c := range map[string]struct {
		override client.Record
		want     string
	}{
		"#50254, another store's bare seq, unknown here":       {override("#436664", "430803"), ""},
		"this store's bare seq naming no record stays refused": {override("", "430803"), "citing a record that exists"},
		"another store's cite that is no seq stays refused":    {override("#436664", "nothing"), "citing a record that exists"},
	} {
		t.Run(name, func(t *testing.T) {
			text, _, refusals := ExemptGo(Claim{Repo: monorepo, PR: portPR, Head: portHead}, Read{Records: []client.Record{major, fidelity, head, c.override}, Exempt: Grades(nil, driveClasses, ""), TapRules: driveTaps, Files: []string{"go/sand/next/a.go"}, PortSourceMerged: true})
			if c.want != "" {
				refusedWith(t, text, refusals, c.want)
				return
			}
			if len(refusals) > 0 {
				t.Fatalf("got go %q and refusals %q, want a go", text, refusals)
			}
		})
	}
}

func TestAPortGoNamesAClassTheBusReadsAsExempt(t *testing.T) {
	records := append(graded(t, "class old-tree-port"), headRecord(t, portHead, `,"port-of":"49000","port-interacts":"no"`))
	text, _, refusals := ExemptGo(Claim{Repo: monorepo, PR: portPR, Head: portHead}, Read{Records: records, Exempt: driveClasses, TapRules: driveTaps, Files: []string{"go/sand/next/a.go"}, PortSourceMerged: true})
	if want := "go #49999 at abc1234 on green [exempt: old-tree-port]: fidelity ok #2, finder ok #1"; text != want || len(refusals) > 0 {
		t.Fatalf("got go %q and refusals %q, want %q", text, refusals, want)
	}
}

func TestANewerHeadRecordSayingItInteractsOutranksAnOlderCleanOne(t *testing.T) {
	records := append(graded(t, "class old-tree-port"), headRecord(t, portHead, `,"port-of":"49000","port-interacts":"no"`), headRecord(t, portHead, `,"port-of":"49000","port-interacts":"yes"`))
	claim, claimed := PortAt(Claim{Repo: monorepo, PR: portPR, Head: portHead}, Read{Records: records})
	if !claimed || !claim.Interacts || claim.Source != 49000 {
		t.Fatalf("got %+v, claimed %v; want the newest record's interacting claim on #49000", claim, claimed)
	}
}

func TestAFlakeFixLandsWithNoTapOnlyWhenItsWholeChangeIsTestCodeWithNoTimeoutBump(t *testing.T) {
	const named = `,"flake-fix":"TestChatStream"`
	patch := func(text string) *string { return &text }
	next := "go/sand/next/internal/vm/run_test.go"
	helper := "go/sand/next/internal/callcount/callcount.go"
	cases := map[string]struct {
		grade   string
		attrs   string
		files   []string
		patches map[string]*string
		want    string
	}{
		"test code alone lands past the tap rule":       {"class test-only", named, []string{next, "dashboard/src/iris/testing/chatHarness.tsx"}, map[string]*string{next: patch("+retry()"), "dashboard/src/iris/testing/chatHarness.tsx": patch("+props")}, ""},
		"a grade naming no class still counts":          {"reads right", named, []string{next}, map[string]*string{next: patch("+retry()")}, ""},
		"a test-only PR with no flake record needs him": {"class test-only", "", []string{next}, map[string]*string{next: patch("+retry()")}, "tap rule rule/82317b0 names " + next},
		"product code is not covered":                   {"reads right", named, []string{next, "go/sand/next/internal/vm/run.go"}, map[string]*string{next: patch("+retry()"), "go/sand/next/internal/vm/run.go": patch("+x")}, "flake fix: go/sand/next/internal/vm/run.go is not test or fixture code, so it lands on his tap"},
		"#50715, a helper a reader graded test-only":    {"class test-only", named, []string{next, helper}, map[string]*string{next: patch("+retry()"), helper: patch("+uncounted")}, ""},
		"a helper no grade vouches for":                 {"class twin", named, []string{next, helper}, map[string]*string{next: patch("+retry()"), helper: patch("+uncounted")}, "flake fix: " + helper + " is not test or fixture code"},
		"a graded helper's timeout bump is not covered": {"class test-only", named, []string{helper}, map[string]*string{helper: patch("+\tdeadline := time.Minute")}, "adds a timeout line"},
		"a graded helper with no record needs him":      {"class test-only", "", []string{helper}, map[string]*string{helper: patch("+uncounted")}, "tap rule rule/82317b0 names " + helper},
		"CI config is not test code":                    {"class test-only", named, []string{"infra/ci/x.ts"}, map[string]*string{"infra/ci/x.ts": patch("+x")}, "infra/ci/x.ts is not test or fixture code"},
		"a release file a reader called a helper":       {"class test-only", named, []string{next, "go/ci/internal/release/pipeline/cancel.go"}, map[string]*string{next: patch("+retry()"), "go/ci/internal/release/pipeline/cancel.go": patch("+x")}, "go/ci/internal/release/pipeline/cancel.go is not test or fixture code"},
		"a timeout bump is not covered":                 {"class test-only", named, []string{next}, map[string]*string{next: patch(" keep\n+\tctx, cancel := context.WithTimeout(ctx, time.Minute)")}, "adds a timeout line"},
		"a removed timeout is fine":                     {"class test-only", named, []string{next}, map[string]*string{next: patch("-\tWithTimeout(ctx, time.Minute)\n+\tretry()")}, ""},
		"a patch GitHub withheld cannot be cleared":     {"class test-only", named, []string{next}, map[string]*string{next: nil}, "GitHub returned no patch"},
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			records := graded(t, c.grade)
			if c.attrs != "" {
				records = append(records, headRecord(t, portHead, c.attrs))
			}
			text, _, refusals := ExemptGo(Claim{Repo: monorepo, PR: portPR, Head: portHead}, Read{Records: records, Exempt: driveClasses, TapRules: driveTaps, Files: c.files, Patches: c.patches})
			if c.want != "" {
				refusedWith(t, text, refusals, c.want)
				return
			}
			if len(refusals) > 0 || !strings.Contains(text, "[exempt: flake-fix]") {
				t.Fatalf("got go %q and refusals %q, want a flake-fix go", text, refusals)
			}
		})
	}
}

func TestOnlyALanesNewestGradeVouchesForAFlakeFixHelper(t *testing.T) {
	const helper = "go/sand/next/internal/callcount/callcount.go"
	patch := "+uncounted"
	regraded := decoded(t, `{"seq":4,"lane":"fidelity-pipeline","kind":"review","text":"fidelity: ok `+portHead+` reads right","refs":{"prs":[49999]}}`)
	records := append(graded(t, "class test-only"), headRecord(t, portHead, `,"flake-fix":"TestChatStream"`), regraded)
	text, _, refusals := ExemptGo(Claim{Repo: monorepo, PR: portPR, Head: portHead}, Read{Records: records, Exempt: driveClasses, TapRules: driveTaps, Files: []string{helper}, Patches: map[string]*string{helper: &patch}})
	refusedWith(t, text, refusals, "flake fix: "+helper+" is not test or fixture code")
}

func TestAnOverriddenPipelineGradeThatNamesTestOnlyVouchesForAFlakeFixHelper(t *testing.T) {
	const helper = "go/sand/next/internal/callcount/callcount.go"
	patch := "+uncounted"
	finder := decoded(t, `{"seq":1,"lane":"finder-desk","kind":"review","text":"finder: ok `+portHead+`","refs":{"prs":[49999]}}`)
	claim := headRecord(t, portHead, `,"flake-fix":"TestChatStream"`)
	override := client.Record{Seq: 4, Lane: "root", Kind: "decide", Text: "fidelity-override #49999 at abc1234: 2"}
	read := func(records ...client.Record) (string, []string) {
		text, _, refusals := ExemptGo(Claim{Repo: monorepo, PR: portPR, Head: portHead}, Read{Records: records, Exempt: driveClasses, TapRules: driveTaps, Files: []string{helper}, Patches: map[string]*string{helper: &patch}})
		return text, refusals
	}

	if text, refusals := read(finder, pipelineGrade(t, 2, "missing", "test-only"), claim, override); len(refusals) > 0 || !strings.Contains(text, "[exempt: flake-fix]") {
		t.Fatalf("got go %q and refusals %q, want a flake-fix go", text, refusals)
	}
	text, refusals := read(finder, pipelineGrade(t, 2, "missing", NoClass), claim, override)
	refusedWith(t, text, refusals, "flake fix: "+helper+" is not test or fixture code")
}

func TestAFlakeRecordDoesNotExcuseAMissingFidelityGrade(t *testing.T) {
	records := []client.Record{graded(t, "class test-only")[0], headRecord(t, portHead, `,"flake-fix":"TestChatStream"`)}
	text, _, refusals := ExemptGo(Claim{Repo: monorepo, PR: portPR, Head: portHead}, Read{Records: records, Exempt: driveClasses})
	refusedWith(t, text, refusals, "fidelity: no ok grade at abc1234 names an exempt class")
}

func TestOnlyTheCountedRecordSettlesTheFinderNeverTheAppealItself(t *testing.T) {
	records := recordsBefore(t, "48375", 355825)
	cite := strconv.FormatInt(records[0].Seq, 10)
	const at = "71bd0ab1c2dc27453798cbc7fb75db730f59bf84"
	claim := Claim{Repo: monorepo, PR: 48375, Head: at}
	finder := func(refusal string) bool { return strings.HasPrefix(refusal, "finder:") }
	appealed := append(records, client.Record{Seq: 355824, Lane: "grader-appeals-1", Kind: "decide", Text: "finder-override #48375 at 71bd0ab: " + cite + ". Row rule/4236611 grounds it"})

	_, _, alone := ExemptGo(claim, Read{Records: appealed, GoLanes: goLanes, Exempt: driveClasses})
	if !slices.ContainsFunc(alone, finder) {
		t.Fatalf("an appeal nothing has counted settled the finder: %q", alone)
	}
	counted := append(appealed, client.Record{Seq: 355830, Lane: bus.SweepLane, Kind: "decide", Text: "finder-override #48375 at 71bd0ab: " + cite + ". Counted from grader-appeals-1's cci #355824, whose owner rows exist."})
	_, _, read := ExemptGo(claim, Read{Records: counted, GoLanes: goLanes, Exempt: driveClasses})
	if slices.ContainsFunc(read, finder) {
		t.Fatalf("a counted override left the finder refused: %q", read)
	}
}

func TestAnOldTreeFileIsUnderGoSandAndOutsideTheNextTree(t *testing.T) {
	for _, c := range []struct {
		files []string
		want  string
	}{
		{[]string{"go/sand/internal/steptree/tree.go"}, "go/sand/internal/steptree/tree.go"},
		{[]string{"go/sand/next/internal/steptree/tree.go", "go/sand/next/internal/steptree/tree_test.go"}, ""},
		{[]string{"api/src/x.ts", "go/sand/next/a.go", "go/sand/cmd/iris.go"}, "go/sand/cmd/iris.go"},
		{[]string{"go/sandsql/x.go", "dashboard/src/a.tsx"}, ""},
	} {
		if got := OldTreeFile(c.files); got != c.want {
			t.Errorf("OldTreeFile(%q) = %q, want %q", c.files, got, c.want)
		}
	}
	if got := NextTreeFile([]string{"go/sand/cmd/iris.go", "go/sand/next/a.go"}); got != "go/sand/next/a.go" {
		t.Errorf("NextTreeFile = %q, want the next-tree file", got)
	}
}

func TestAnOverrideItsLaneWithdrewStopsCountingInEveryCopy(t *testing.T) {
	graded := []client.Record{
		decoded(t, `{"seq":1,"lane":"finder-desk","kind":"review","text":"finder: major `+portHead+` a plan row","refs":{"prs":[49999]}}`),
		decoded(t, `{"seq":2,"lane":"fidelity-pipeline","kind":"review","text":"fidelity: missing `+portHead+` plan:116","refs":{"prs":[49999]}}`),
	}
	const overrides = "fidelity-override #49999 at abc1234: 2. finder-override #49999 at abc1234: 1. Row rule/29f3c70 grounds it"
	appeal := client.Record{Seq: 10, Lane: "grader-appeals-1", Kind: "decide", Text: overrides}
	repost := client.Record{Seq: 11, Lane: "root", Kind: "decide", Text: overrides + " (grader-appeals-1 #10)"}
	counted := client.Record{Seq: 12, Lane: bus.SweepLane, Kind: "decide", Text: overrides + ". Counted from grader-appeals-1's cci #10, whose owner rows exist."}
	withdraw := func(lane string) client.Record {
		return client.Record{Seq: 13, Lane: lane, Kind: "withdraw", Re: 10, Text: "takes back 10 on #49999"}
	}
	reposted := client.Record{Seq: 14, Lane: "root", Kind: "decide", Text: overrides}
	for name, c := range map[string]struct {
		records []client.Record
		counts  bool
	}{
		"root's repost and the sweep's count stand before any withdraw": {[]client.Record{appeal, repost, counted}, true},
		"the appeal lane's withdraw drops both copies":                  {[]client.Record{appeal, repost, counted, withdraw("grader-appeals-1")}, false},
		"a repost after the withdraw does not bring it back":            {[]client.Record{appeal, repost, withdraw("grader-appeals-1"), reposted}, false},
		"a withdraw from another lane drops nothing":                    {[]client.Record{appeal, repost, withdraw("go-sweep-8")}, true},
	} {
		t.Run(name, func(t *testing.T) {
			_, refusals := gate(slices.Concat(graded, c.records), portPR, portHead)
			for _, grader := range []string{"finder", "fidelity"} {
				uncounted := slices.ContainsFunc(refusals, func(refusal string) bool {
					return strings.Contains(refusal, "no counted decide reads "+grader+"-override #49999 at abc1234")
				})
				if uncounted == c.counts {
					t.Fatalf("%s override counted %v, want %v: %q", grader, !uncounted, c.counts, refusals)
				}
			}
		})
	}
}
