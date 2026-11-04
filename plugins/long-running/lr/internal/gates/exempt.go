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
	PortOfAttr        = "port-of"
	PortInteractsAttr = "port-interacts"
	portCleanWord     = "no"
	portTree          = "go/sand/next/"
	headKind          = "head"
)

var DefaultExemptGrades = []string{PortGrade, TestOnlyGrade}

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
	finder, finderRefusal := finderCite(c, r.Records)
	port, claimed := PortAt(c, r.Records)
	exempt := r.Exempt
	if claimed {
		exempt = portsFirst(exempt)
	}
	class, fidelity, fidelityRefusals := fidelityCite(c, r.Records, exempt)
	rules := tapRules(r.TapRules, r.Files)
	var portRefused []string
	if isPortClass(class) {
		portRefused = portRefusals(c, r, port, claimed)
		if len(portRefused) == 0 {
			rules = nil
		}
	}
	refusals := slices.Concat(nonEmpty(finderRefusal), fidelityRefusals, portRefused, openHolds(c, r.Records, r.Lifts), openCards(c, r), rules, standingGos(c, r), nonEmpty(UnsafeOnDev(UnsafeWait(r.Body))))
	if len(refusals) > 0 {
		return "", refusals
	}
	return fmt.Sprintf("go #%d at %s on green [exempt: %s]: %s, %s", c.PR, short(c.Head), class, fidelity, finder), nil
}

func PortAt(c Claim, records []client.Record) (PortClaim, bool) {
	for _, record := range slices.Backward(records) {
		if string(record.Kind) != headKind || !namesHead(record, c) {
			continue
		}
		source, err := strconv.Atoi(record.Fields.Attrs[PortOfAttr])
		if err != nil {
			continue
		}
		return PortClaim{source, record.Fields.Attrs[PortInteractsAttr] != portCleanWord, record}, true
	}
	return PortClaim{}, false
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

func portRefusals(c Claim, r Read, port PortClaim, claimed bool) []string {
	if !claimed {
		return []string{fmt.Sprintf("port: no head record at %s carries %s=<old-tree PR>, and a grade's words do not prove a port", short(c.Head), PortOfAttr)}
	}
	var out []string
	if port.Interacts {
		out = append(out, fmt.Sprintf("port: head record %s does not read %s=%s, so it goes to the owner on one card and lands on his tap", port.Record.Name(), PortInteractsAttr, portCleanWord))
	}
	if !r.PortSourceMerged {
		out = append(out, fmt.Sprintf("port: its source #%d is not a merged pull request", port.Source))
	}
	if file := slices.IndexFunc(r.Files, func(file string) bool { return !strings.HasPrefix(file, portTree) }); file >= 0 {
		out = append(out, fmt.Sprintf("port: %s is outside %s, so it is no pure port and lands on his tap", r.Files[file], portTree))
	}
	return out
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

func finderCite(c Claim, records []client.Record) (string, string) {
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
	if override, found := rootOverride(c, records, finderKind, func(cites string) bool { return cites != "" && named(records, cites) }); found {
		return "finder-override " + override.Name(), ""
	}
	said := "no finder-desk grade"
	if grade != "" {
		said = "finder-desk's newest grade " + newest.Name() + " is major"
	}
	return "", fmt.Sprintf("finder: %s at %s, and no root decide reads finder-override #%d at %s citing a record that exists", said, short(c.Head), c.PR, short(c.Head))
}

func fidelityCite(c Claim, records []client.Record, exempt []string) (string, string, []string) {
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
	}
	if class == "" {
		refusals = append(refusals, fmt.Sprintf("fidelity: no ok grade at %s names an exempt class (%s)", short(c.Head), strings.Join(exempt, "; ")))
	}
	if _, overridden := rootOverride(c, records, fidelityKind, func(string) bool { return true }); len(missing) > 0 && !overridden {
		refusals = append(refusals, fmt.Sprintf("fidelity: %s at %s, and no root decide reads fidelity-override #%d at %s", strings.Join(missing, ", "), short(c.Head), c.PR, short(c.Head)))
	}
	return class, cite, refusals
}

func rootOverride(c Claim, records []client.Record, grader string, cited func(string) bool) (client.Record, bool) {
	for _, record := range slices.Backward(records) {
		if string(record.Kind) != kindDecide || record.Lane != rootLane {
			continue
		}
		if slices.ContainsFunc(bus.Overrides(record), func(o bus.Override) bool {
			return o.Grader == grader && o.PR == c.PR && strings.HasPrefix(c.Head, o.Head) && cited(o.Cites)
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

func openCards(c Claim, r Read) []string {
	names := regexp.MustCompile(`(?:^|\D)` + strconv.Itoa(c.PR) + `(?:\D|$)`)
	var out []string
	for _, card := range r.Cards {
		if names.MatchString(card.ID) || names.MatchString(card.Title) {
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
