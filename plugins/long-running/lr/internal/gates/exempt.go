package gates

import (
	"cmp"
	"fmt"
	"maps"
	"regexp"
	"slices"
	"strconv"
	"strings"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
)

const (
	rootLane     = "root"
	kindDecide   = "decide"
	kindHold     = "hold"
	finderKind   = "finder"
	fidelityKind = "fidelity"
	shortHead    = 7
)

const (
	PortGrade     = "pure port, no interaction"
	TestOnlyGrade = "test-only or CI-only, no non-test code"

	OldTreePortClass  = "old-tree-port"
	PortOfAttr        = bus.PortOfAttr
	PortInteractsAttr = bus.PortInteractsAttr
	PortCleanWord     = "no"
	PortSourceFact    = "port-source"
	PortOutsideFact   = "port-outside"
	portSourceMerged  = "merged"
	portSourceOpen    = "not merged"
	FlakeFixClass     = "flake-fix"
	FlakeFixAttr      = bus.FlakeFixAttr
	StandingRefused   = bus.GoRefusedFact
	portTree          = "go/sand/next/"
	oldTree           = "go/sand/"
	headKind          = "head"
)

var (
	DefaultExemptGrades = []string{PortGrade, TestOnlyGrade}
	timeoutWord         = regexp.MustCompile(`(?i)time_?out|deadline`)
	cardNamed           = regexp.MustCompile(`(?i)(?:^|[^\w-])card[ -]([\w-]+)`)
)

type Claim struct {
	Repo string
	PR   int
	Head string
}

type Card struct {
	ID    string
	Title string
}

type Read struct {
	Records  []client.Record
	GoLanes  []string
	Exempt   []string
	TapRules map[string][]string
	Files    []string
	Cards    []Card
	Lifts    map[int64]bool
	Body     string
	Patches  map[string]*string

	PortSourceMerged bool
}

type standingGrade struct {
	record client.Record
	ok     bool
}

type PortClaim struct {
	Source    int
	Interacts bool
	Record    client.Record
}

func ExemptGo(c Claim, r Read) (string, []string) {
	finder, finderRefusal := finderCite(c, r)
	port, claimed := PortAt(c, r.Records)
	flaked := FlakeAt(c, r.Records)
	portFacts := claimFacts(port, r)
	word := ""
	switch {
	case flaked:
		word = FlakeFixClass
	case claimed && len(PortRefusals(portFacts)) == 0:
		word = OldTreePortClass
	}
	exempt := r.Exempt
	if claimed {
		exempt = portsFirst(exempt)
	}
	class, fidelity, fidelityRefusals := fidelityCite(c, r, exempt, word)
	rules := tapRules(r.TapRules, r.Files)
	var classRefused []string
	switch {
	case class == FlakeFixClass:
		classRefused = flakeRefusals(r, gradedTestOnly(c, r))
	case isPortClass(class) && !claimed:
		classRefused = []string{fmt.Sprintf("port: no head record at %s carries %s=<old-tree PR>, and a grade's words do not prove a port", short(c.Head), PortOfAttr)}
	case isPortClass(class) || claimed && !port.Interacts:
		classRefused = PortRefusals(portFacts)
	}
	if (class == FlakeFixClass || isPortClass(class)) && len(classRefused) == 0 {
		rules = nil
	}
	refusals := slices.Concat(nonEmpty(finderRefusal), fidelityRefusals, classRefused, openHolds(c, r.Records, r.Lifts), OpenCards(c.PR, r.Body, r.Cards), rules, standingGos(c, r), nonEmpty(UnsafeOnDev(UnsafeWait(r.Body))))
	if len(refusals) > 0 {
		return "", refusals
	}
	return fmt.Sprintf("go #%d at %s on green [exempt: %s]: %s, %s", c.PR, short(c.Head), class, fidelity, finder), nil
}

func PortAt(c Claim, records []client.Record) (PortClaim, bool) {
	for _, record := range slices.Backward(records) {
		if !claimRecord(record) || !namesHead(record, c) {
			continue
		}
		source, err := strconv.Atoi(record.Fields.Attrs[PortOfAttr])
		if err != nil {
			continue
		}
		return PortClaim{source, record.Fields.Attrs[PortInteractsAttr] != PortCleanWord, record}, true
	}
	return PortClaim{}, false
}

func FlakeAt(c Claim, records []client.Record) bool {
	return slices.ContainsFunc(records, func(record client.Record) bool {
		return claimRecord(record) && record.Fields.Attrs[FlakeFixAttr] != "" && namesHead(record, c)
	})
}

func claimRecord(record client.Record) bool {
	return string(record.Kind) == headKind || bus.CarriedClaim(record)
}

func CarriedClaim(from, head string, atFrom, atHead map[string]string) (map[string]string, string) {
	if atHead[PortOfAttr] != "" || atHead[FlakeFixAttr] != "" || !SameDiff(atFrom, atHead) {
		return nil, ""
	}
	attrs := map[string]string{headKind: head}
	for _, attr := range []string{PortOfAttr, PortInteractsAttr, FlakeFixAttr} {
		if value := atFrom[attr]; value != "" {
			attrs[attr] = value
		}
	}
	if len(attrs) == 1 {
		return nil, ""
	}
	return attrs, fmt.Sprintf("Claim at %s carried from %s, whose own diff is the same.", short(head), short(from))
}

func flakeRefusals(r Read, graded bool) []string {
	var out []string
	for _, file := range r.Files {
		patch := r.Patches[file]
		readerJudged := graded && !testOrCIFile(file)
		switch {
		case !testCode(file) && !readerJudged:
			out = append(out, fmt.Sprintf("flake fix: %s is not test or fixture code, so it lands on his tap", file))
		case patch == nil:
			out = append(out, fmt.Sprintf("flake fix: GitHub returned no patch for %s, so no timeout bump can be ruled out", file))
		case addsTimeout(*patch):
			out = append(out, fmt.Sprintf("flake fix: %s adds a timeout line, and a timeout bump lands on his tap", file))
		}
	}
	return out
}

func gradedTestOnly(c Claim, r Read) bool {
	testOnly := slices.DeleteFunc(slices.Clone(r.Exempt), func(phrase string) bool { return !TestOnlyClass(phrase) })
	lanes, standing := fidelityStanding(c, r.Records)
	return slices.ContainsFunc(lanes, func(lane string) bool {
		return standing[lane].ok && exemptPhrase(standing[lane].record.Text, testOnly) != ""
	})
}

func addsTimeout(patch string) bool {
	return slices.ContainsFunc(strings.Split(patch, "\n"), func(line string) bool {
		return strings.HasPrefix(line, "+") && timeoutWord.MatchString(line)
	})
}

func namesHead(record client.Record, c Claim) bool {
	named := cmp.Or(bus.HeadNamed(record, c.Repo, c.PR), record.Fields.Attrs[headKind])
	if named == "" {
		return false
	}
	sha := strings.Fields(named)[0]
	if _, moved, found := strings.Cut(sha, ".."); found {
		sha = moved
	}
	return strings.HasPrefix(c.Head, sha)
}

func LandsOnWord(facts map[string]string) (class, refused string) {
	_, unclaimed := strconv.Atoi(facts[PortOfAttr])
	switch {
	case facts[FlakeFixAttr] != "":
		class = FlakeFixClass
	case unclaimed == nil && facts[PortInteractsAttr] == PortCleanWord:
		class = OldTreePortClass
	default:
		return "", ""
	}
	if said := facts[StandingRefused]; said != "" {
		return "", said
	}
	if class == OldTreePortClass {
		if why := PortRefusals(facts); len(why) > 0 {
			return "", strings.Join(why, "; ")
		}
	}
	return class, ""
}

func PortRefusals(facts map[string]string) []string {
	var out []string
	if facts[PortInteractsAttr] != PortCleanWord {
		out = append(out, fmt.Sprintf("port: its head record does not read %s=%s, so it goes to the owner on one card and lands on his tap", PortInteractsAttr, PortCleanWord))
	}
	if source := facts[PortSourceFact]; source != "" && source != portSourceMerged {
		out = append(out, fmt.Sprintf("port: its source #%s is not a merged pull request, and no commit on trunk names it", facts[PortOfAttr]))
	}
	if outside := facts[PortOutsideFact]; outside != "" {
		out = append(out, fmt.Sprintf("port: %s is outside %s, so it is no pure port and lands on his tap", outside, portTree))
	}
	return out
}

func PortSourceLanded(facts map[string]string) bool {
	return facts[PortSourceFact] == portSourceMerged
}

func PortEvidence(sourceMerged bool, files []string) map[string]string {
	evidence := map[string]string{PortSourceFact: portSourceOpen}
	if sourceMerged {
		evidence[PortSourceFact] = portSourceMerged
	}
	if file := slices.IndexFunc(files, func(file string) bool { return !strings.HasPrefix(file, portTree) }); file >= 0 {
		evidence[PortOutsideFact] = files[file]
	}
	return evidence
}

func NextTreeFile(files []string) string {
	if file := slices.IndexFunc(files, func(file string) bool { return strings.HasPrefix(file, portTree) }); file >= 0 {
		return files[file]
	}
	return ""
}

func OldTreeFile(files []string) string {
	if file := slices.IndexFunc(files, func(file string) bool {
		return strings.HasPrefix(file, oldTree) && !strings.HasPrefix(file, portTree)
	}); file >= 0 {
		return files[file]
	}
	return ""
}

func claimFacts(port PortClaim, r Read) map[string]string {
	facts := PortEvidence(r.PortSourceMerged, r.Files)
	facts[PortOfAttr] = strconv.Itoa(port.Source)
	if !port.Interacts {
		facts[PortInteractsAttr] = PortCleanWord
	}
	return facts
}

func isPortClass(class string) bool {
	return class == OldTreePortClass || class == ClassOf(PortGrade)
}

func portsFirst(exempt []string) []string {
	ordered := slices.Clone(exempt)
	slices.SortStableFunc(ordered, func(a, b string) int {
		return cmp.Compare(portRank(a), portRank(b))
	})
	return ordered
}

func portRank(phrase string) int {
	if isPortClass(ClassOf(phrase)) {
		return 0
	}
	return 1
}

func finderCite(c Claim, r Read) (string, string) {
	records := r.Records
	var newest client.Record
	grade := ""
	for _, record := range records {
		for _, fact := range bus.LegacyFacts(record, c.Repo, nil) {
			if fact.Kind == finderKind && fact.PR == c.PR && strings.HasPrefix(c.Head, fact.Head) {
				newest, grade = record, fact.Value
			}
		}
	}
	if grade == string(machine.FinderClear) {
		return "finder ok " + newest.Name(), ""
	}
	if override, found := countedOverride(c, r, finderKind, func(override client.Record, cites string) bool {
		return cites != "" && (named(records, cites) || citesAnotherStore(override, cites))
	}); found {
		return "finder-override " + override.Name(), ""
	}
	said := "no finder-desk grade"
	if grade != "" {
		said = "finder-desk's newest grade " + newest.Name() + " is major"
	}
	return "", fmt.Sprintf("finder: %s at %s, and no counted decide reads finder-override #%d at %s citing a record that exists", said, short(c.Head), c.PR, short(c.Head))
}

func fidelityStanding(c Claim, records []client.Record) ([]string, map[string]standingGrade) {
	var lanes []string
	standing := map[string]standingGrade{}
	for _, record := range records {
		for _, fact := range bus.LegacyFacts(record, c.Repo, nil) {
			if fact.Kind != fidelityKind || fact.PR != c.PR || !strings.HasPrefix(c.Head, fact.Head) {
				continue
			}
			if _, seen := standing[record.Lane]; !seen {
				lanes = append(lanes, record.Lane)
			}
			standing[record.Lane] = standingGrade{record, fact.Value == string(machine.FidelityPass)}
		}
	}
	return lanes, standing
}

func fidelityCite(c Claim, r Read, exempt []string, word string) (string, string, []string) {
	lanes, standing := fidelityStanding(c, r.Records)
	class, cite := "", ""
	var missing, refusals []string
	for _, lane := range lanes {
		grade := standing[lane]
		if !grade.ok {
			missing = append(missing, fmt.Sprintf("%s's newest grade %s is missing", lane, grade.record.Name()))
			continue
		}
		if phrase := exemptPhrase(grade.record.Text, exempt); phrase != "" && class == "" {
			class, cite = ClassOf(phrase), "fidelity ok "+grade.record.Name()
		}
		if word != "" {
			class, cite = word, "fidelity ok "+grade.record.Name()
		}
	}
	override, overridden := countedOverride(c, r, fidelityKind, func(client.Record, string) bool { return true })
	if class == "" && word != "" && overridden {
		class, cite = word, "fidelity-override "+override.Name()
	}
	if class == "" {
		refusals = append(refusals, fmt.Sprintf("fidelity: no ok grade at %s names an exempt class (%s)", short(c.Head), strings.Join(exempt, "; ")))
	}
	if len(missing) > 0 && !overridden {
		refusals = append(refusals, fmt.Sprintf("fidelity: %s at %s, and no counted decide reads fidelity-override #%d at %s", strings.Join(missing, ", "), short(c.Head), c.PR, short(c.Head)))
	}
	return class, cite, refusals
}

func countedOverride(c Claim, r Read, grader string, cited func(client.Record, string) bool) (client.Record, bool) {
	for _, record := range slices.Backward(r.Records) {
		if string(record.Kind) != kindDecide || record.Lane != rootLane && record.Lane != bus.SweepLane {
			continue
		}
		if slices.ContainsFunc(bus.Overrides(record), func(o bus.Override) bool {
			return o.Grader == grader && o.PR == c.PR && strings.HasPrefix(c.Head, o.Head) && cited(record, o.Cites)
		}) {
			return record, true
		}
	}
	return client.Record{}, false
}

func named(records []client.Record, cites string) bool {
	return slices.ContainsFunc(records, func(record client.Record) bool {
		return record.Name() == "#"+cites || strconv.FormatInt(record.Seq, 10) == cites
	})
}

func citesAnotherStore(override client.Record, cites string) bool {
	_, notSeq := strconv.Atoi(cites)
	return override.Num != "" && notSeq == nil
}

func exemptPhrase(text string, exempt []string) string {
	lowered := strings.ToLower(text)
	for _, phrase := range exempt {
		if strings.Contains(lowered, strings.ToLower(phrase)) {
			return phrase
		}
	}
	return ""
}

func Grades(exempt, goClasses []string, port string) []string {
	if exempt == nil {
		exempt = slices.Clone(DefaultExemptGrades)
		if port != "" {
			exempt[0] = port
		}
	}
	return slices.Concat(exempt, goClasses)
}

func ClassOf(phrase string) string {
	lead, _, _ := strings.Cut(strings.ToLower(phrase), ",")
	return strings.Join(strings.Fields(strings.TrimPrefix(strings.TrimSpace(lead), "class ")), "-")
}

func openHolds(c Claim, records []client.Record, lifts map[int64]bool) []string {
	closed := map[int64]bool{}
	for _, record := range records {
		closed[record.Resolves] = true
		for _, seq := range record.AlsoResolves {
			closed[seq] = true
		}
	}
	var out []string
	for _, record := range records {
		if string(record.Kind) == kindHold && !closed[record.Seq] && !lifts[record.Seq] && slices.Contains(bus.HeldBy(record, c.Repo), c.PR) {
			out = append(out, fmt.Sprintf("hold %s by %s is open", record.Name(), record.Lane))
		}
	}
	return out
}

func OpenCards(pr int, body string, cards []Card) []string {
	names := regexp.MustCompile(`(?:^|\D)` + strconv.Itoa(pr) + `(?:\D|$)`)
	inBody := map[string]bool{}
	for _, found := range cardNamed.FindAllStringSubmatch(body, -1) {
		inBody[strings.TrimPrefix(strings.ToLower(found[1]), "card-")] = true
	}
	var out []string
	for _, card := range cards {
		if names.MatchString(card.ID) || names.MatchString(card.Title) || inBody[strings.TrimPrefix(card.ID, "card-")] {
			out = append(out, fmt.Sprintf("owner card %s is open", card.ID))
		}
	}
	return out
}

func tapRules(rules map[string][]string, files []string) []string {
	var out []string
	for _, rule := range slices.Sorted(maps.Keys(rules)) {
		if file, found := matching(rules[rule], files); found {
			out = append(out, fmt.Sprintf("tap rule %s names %s, so it lands on his tap", rule, file))
		}
	}
	return out
}

func matching(globs, files []string) (string, bool) {
	for _, file := range files {
		for _, glob := range globs {
			if fnmatch(glob).MatchString(file) {
				return file, true
			}
		}
	}
	return "", false
}

func fnmatch(glob string) *regexp.Regexp {
	var pattern strings.Builder
	for _, char := range glob {
		switch char {
		case '*':
			pattern.WriteString(".*")
		case '?':
			pattern.WriteString(".")
		default:
			pattern.WriteString(regexp.QuoteMeta(string(char)))
		}
	}
	return regexp.MustCompile("^" + pattern.String() + "$")
}

func standingGos(c Claim, r Read) []string {
	var out []string
	for _, record := range bus.LiveGos(r.Records, r.GoLanes) {
		admits := slices.ContainsFunc(bus.LegacyFacts(record, c.Repo, r.GoLanes), func(fact bus.Fact) bool { return fact.Kind == "exempt" && fact.PR == c.PR })
		if admits && bus.GoNamesHead(record, c.PR, c.Head) {
			out = append(out, fmt.Sprintf("go %s by %s already names this head", record.Name(), record.Lane))
		}
	}
	return out
}

func nonEmpty(text string) []string {
	if text == "" {
		return nil
	}
	return []string{text}
}

func short(head string) string {
	return head[:min(shortHead, len(head))]
}
