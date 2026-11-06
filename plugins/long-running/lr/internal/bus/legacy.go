package bus

import (
	"cmp"
	"context"
	"fmt"
	"log/slog"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
)

const (
	PortOfAttr        = "port-of"
	PortInteractsAttr = "port-interacts"
	FlakeFixAttr      = "flake-fix"
	ExemptClassAttr   = "exempt-class"

	LegacyCursor = "legacy"
	legacyWindow = 72 * time.Hour
	rootLane     = "root"
	SweepLane    = "supervisor"
	AppealsLane  = "grader-appeals"
	RunnerLane   = "desk-runner"

	GradeStoppedWhyAttr  = "grade-stopped"
	GradeStoppedHeadAttr = "grade-stopped-head"
)

var (
	legacyKinds    = []string{"review", "go", "decide", "decision", "withdraw", "correction", "report", TapRefusedKind, GoRefusedKind, StuckKind}
	goKinds        = []string{"go", "decide", "decision"}
	finderDesk     = []string{"finder-desk"}
	fidelityLanes  = []string{"fidelity-pipeline", "fidelity-desk", "fidelity-sweep", "fidelity-shard", "fidelity-shard-1"}
	laneNumber     = regexp.MustCompile(`^(.+?)(?:-(\d+))?$`)
	shortSHA       = regexp.MustCompile(`\b[0-9a-f]{7,40}\b`)
	finderSays     = regexp.MustCompile(`(?i)\bfinder(?:\s*\[\d+\])?:?\s+(ok|lgtm|major|issues)\b`)
	finderMajor    = regexp.MustCompile(`(?i)\b[1-9]\d* majors?\b`)
	finderClean    = regexp.MustCompile(`(?i)\bno (?:majors?|findings)\b|\b0 majors?\b|\bok\b`)
	exemptClass    = regexp.MustCompile(`(?i)\[exempt:\s*([^\],;\s]+)`)
	quotedBlock    = regexp.MustCompile("(?s)```.*?(?:```|\\z)|(?m:^[ \\t]*>[^\\n]*)")
	quotedSpan     = regexp.MustCompile("`[^`\\n]*`|\"[^\"\\n]*\"|“[^”]*”")
	overrideNamed  = regexp.MustCompile(`(?i)\b(?:finder|fidelity)-override\b[^.;\n]*`)
	overrideForm   = regexp.MustCompile(`(?i)^(finder|fidelity)-override\s+#?(\d+)\s+(?:at\s+)?([0-9a-f]{7,40})\b(?:\s*(?::|\s(?:finder|fidelity)\s)\s*#?(\d+(?:@[0-9a-f]+)?)\b)?`)
	citedRecord    = regexp.MustCompile(`(?i)(?:^|[\s(,]|cci\s+#)(\d+(?:@[0-9a-f]+)?)(?:$|[\s,)])`)
	voidNamed      = regexp.MustCompile(`(?i)\b(?:finder|fidelity)-void\b[^.;\n]*`)
	voidForm       = regexp.MustCompile(`(?i)^(finder|fidelity)-void\s+#?(\d+)\s+(?:at\s+)?([0-9a-f]{40})\b\s*:\s*#?\d+\b`)
	clauseStart    = regexp.MustCompile(`(?m)(?:^|[.!?]\s+)(?:\d{1,2}:\d{2} [AP]M\s+)?(?:(?:root|main):\s+)?$`)
	goPair         = `#\d+\s+at\s+[0-9a-f]{7,40}\b`
	goClause       = regexp.MustCompile(`(?i)(?:^\s*(?:\d{1,2}:\d{2}\s*[AP]M\s+)?|[.:;!?]\s+)(?:go(?:\s+(?:port|test-only|ci-only))?|merge)\s+(` + goPair + `(?:(?:\s*,\s*|\s*,?\s+and\s+)` + goPair + `)*)`)
	goHead         = regexp.MustCompile(`(?i)#(\d+)\s+at\s+([0-9a-f]{7,40})\b`)
	withdrawnGo    = regexp.MustCompile(`(?i)^\s*withdrawn\b`)
	withdrawsNamed = regexp.MustCompile(`(?i)\bwithdraws?\b[^.;:\n#]*#(\d+)`)
	finderVerdicts = map[string]string{"ok": "clear", "major": "ask"}
	behaviorSays   = regexp.MustCompile(`^behavior: (same|changed) ([0-9a-f]{7,40}) since ([0-9a-f]{7,40})(?:: (.+))?`)
)

var GoReadKinds = []string{"go", "decide", "decision", "withdraw", "correction"}

type Fact struct {
	PR    int
	Head  string
	Kind  string
	Value string
}

func (b Bus) ReadLegacy(ctx context.Context, since int64, now time.Time) ([]client.Record, error) {
	query := client.Query{Drive: b.Drive, After: since, Kinds: legacyKinds}
	if since == 0 {
		query.Since = now.Add(-legacyWindow)
	}
	return client.Tail(ctx, query)
}

func FidelityLanes() []string {
	return slices.Clone(fidelityLanes)
}

func CarriedClaim(record client.Record) bool {
	attrs := record.Fields.Attrs
	return string(record.Kind) == "decide" && delegated(record.Lane, []string{SweepLane}) && (attrs[PortOfAttr] != "" || attrs[FlakeFixAttr] != "")
}

func carriedClaimFacts(record client.Record, repo string) []Fact {
	var out []Fact
	for _, attr := range []string{PortOfAttr, PortInteractsAttr, FlakeFixAttr} {
		if value := record.Fields.Attrs[attr]; value != "" {
			out = append(out, factsPerPR(record, repo, record.Fields.Attrs[refusedHead], attr, value)...)
		}
	}
	return out
}

func LegacyFacts(record client.Record, repo string, goLanes []string) []Fact {
	if CarriedClaim(record) {
		return carriedClaimFacts(record, repo)
	}
	kind := string(record.Kind)
	switch {
	case kind == "withdraw" || kind == "correction" || kind == ownerKind:
		return nil
	case kind == "report":
		return stoppedFacts(record, repo)
	case kind == TapRefusedKind:
		return factsPerPR(record, repo, record.Fields.Attrs[refusedHead], TapRefusedFact, record.Fields.Attrs[refusalAttr])
	case kind == GoRefusedKind:
		return factsPerPR(record, repo, record.Fields.Attrs[refusedHead], GoRefusedFact, record.Fields.Attrs[refusalAttr])
	case kind == StuckKind:
		return factsPerPR(record, repo, record.Fields.Attrs[refusedHead], StuckFact, record.Fields.Attrs[refusalAttr])
	case kind == "review" && delegated(record.Lane, finderDesk) && strings.HasPrefix(record.Text, "finder:"):
		grade, head := finderVerdicts[finderGrade(record.Text)], firstSHA(record.Text)
		if grade == "" || head == "" {
			return nil
		}
		return factsPerPR(record, repo, head, "finder", grade)
	case kind == "review" && delegated(record.Lane, fidelityLanes) && (strings.HasPrefix(record.Text, "fidelity: ok") || strings.HasPrefix(record.Text, "fidelity: missing")):
		grade := "fail"
		if strings.HasPrefix(record.Text, "fidelity: ok") {
			grade = "pass"
		}
		_, after, _ := strings.Cut(record.Text, ":")
		head := firstSHA(after)
		if head == "" {
			return nil
		}
		return factsPerPR(record, repo, head, "fidelity", grade)
	case kind == "review" && delegated(record.Lane, fidelityLanes) && strings.HasPrefix(record.Text, "behavior:"):
		return behaviorFacts(record, repo)
	case !delegated(record.Lane, goAuthors(goLanes)):
		return nil
	}
	out := append(overrideFacts(CountedOverrides(record)), voidFacts(record)...)
	class := record.Fields.Attrs[ExemptClassAttr]
	if class == "" || !isGo(record) {
		return out
	}
	for _, pair := range goHeads(record.Text) {
		out = append(out, Fact{pair.PR, pair.Head, "exempt", class})
	}
	return out
}

func ExemptClaims(record client.Record) []Fact {
	claimed := exemptClass.FindStringSubmatch(FullText(record))
	if claimed == nil || !isGo(record) {
		return nil
	}
	var out []Fact
	for _, pair := range goHeads(record.Text) {
		out = append(out, Fact{pair.PR, pair.Head, "exempt", strings.ToLower(claimed[1])})
	}
	return out
}

func HandExempt(record client.Record) bool {
	return record.Fields.Attrs[ExemptClassAttr] == "" && len(ExemptClaims(record)) > 0
}

func stoppedFacts(record client.Record, repo string) []Fact {
	why, head := record.Fields.Attrs[GradeStoppedWhyAttr], record.Fields.Attrs[GradeStoppedHeadAttr]
	if client.Stem(record.Lane) != RunnerLane || why == "" || head == "" {
		return nil
	}
	return factsPerPR(record, repo, head, machine.GradeStoppedFact, why)
}

func HeadNamed(record client.Record, repo string, number int) string {
	attrs := record.Fields.Attrs
	return cmp.Or(attrs["head:"+strconv.Itoa(number)], attrs["head:"+repo+"#"+strconv.Itoa(number)])
}

func behaviorFacts(record client.Record, repo string) []Fact {
	said := behaviorSays.FindStringSubmatch(record.Text)
	if said == nil {
		return nil
	}
	out := append(factsPerPR(record, repo, said[2], "behavior", said[1]), factsPerPR(record, repo, said[2], "behavior-since", said[3])...)
	if said[4] != "" {
		out = append(out, factsPerPR(record, repo, said[2], "what-changed", said[4])...)
	}
	return out
}

func factsPerPR(record client.Record, repo, head, kind, value string) []Fact {
	var out []Fact
	for _, pr := range record.Refs.PRs {
		if pr.Repo == "" || strings.EqualFold(pr.Repo, repo) {
			out = append(out, Fact{pr.Number, head, kind, value})
		}
	}
	return out
}

func GoCounts(lane string, goLanes []string) bool {
	return delegated(lane, goAuthors(goLanes))
}

func goAuthors(goLanes []string) []string {
	return append([]string{rootLane, SweepLane}, goLanes...)
}

func delegated(lane string, delegates []string) bool {
	return slices.ContainsFunc(delegates, func(delegate string) bool {
		return lane == delegate || succeeds(lane, delegate)
	})
}

func succeeds(name, lane string) bool {
	base, number := laneNumber.FindStringSubmatch(lane), 1
	other := laneNumber.FindStringSubmatch(name)
	if base == nil || other == nil || other[1] != base[1] || other[2] == "" {
		return false
	}
	if base[2] != "" {
		parsed, err := strconv.Atoi(base[2])
		if err != nil {
			return false
		}
		number = parsed
	}
	later, err := strconv.Atoi(other[2])
	return err == nil && later > number
}

func firstSHA(text string) string {
	return shortSHA.FindString(text)
}

func finderGrade(text string) string {
	if said := finderSays.FindStringSubmatch(text); said != nil {
		switch verdict := strings.ToLower(said[1]); verdict {
		case "issues":
			return "major"
		case "lgtm":
			return "ok"
		default:
			return verdict
		}
	}
	if finderMajor.MatchString(text) {
		return "major"
	}
	if finderClean.MatchString(text) {
		return "ok"
	}
	return ""
}

func FullText(record client.Record) string {
	text, err := client.WholeText(record)
	if err != nil {
		slog.Warn("a record's body could not be read, so its text counts as absent", "seq", record.Seq, "lane", record.Lane, "path", record.Refs.Path, "err", err)
		return ""
	}
	return text
}

type Override struct {
	Grader string
	PR     int
	Head   string
	Cites  string
}

func (o Override) Line(why string) string {
	cited := ""
	if o.Cites != "" {
		cited = ": " + o.Cites
	}
	return fmt.Sprintf("%s-override #%d at %s%s. %s", o.Grader, o.PR, o.Head, cited, why)
}

func (o Override) Value() string {
	return cmp.Or(o.Cites, "accept")
}

func Overrides(record client.Record) []Override {
	return overridesIn(FullText(record))
}

func CountedOverrides(record client.Record) []Override {
	if string(record.Kind) != "decide" || record.Lane != rootLane && record.Lane != SweepLane {
		return nil
	}
	return Overrides(record)
}

func overridesIn(text string) []Override {
	var out []Override
	for _, span := range overrideNamed.FindAllStringIndex(text, -1) {
		if !clauseStart.MatchString(text[:span[0]]) {
			continue
		}
		form := overrideForm.FindStringSubmatch(text[span[0]:span[1]])
		if form == nil {
			continue
		}
		pr, err := strconv.Atoi(form[2])
		if err != nil {
			continue
		}
		out = append(out, Override{strings.ToLower(form[1]), pr, strings.ToLower(form[3]), form[4]})
	}
	return out
}

func voidFacts(record client.Record) []Fact {
	if string(record.Kind) != "decide" || record.Lane != rootLane && record.Lane != SweepLane {
		return nil
	}
	text := FullText(record)
	var out []Fact
	for _, span := range voidNamed.FindAllStringIndex(text, -1) {
		form := voidForm.FindStringSubmatch(text[span[0]:span[1]])
		if form == nil || !clauseStart.MatchString(text[:span[0]]) {
			continue
		}
		pr, err := strconv.Atoi(form[2])
		if err != nil {
			continue
		}
		out = append(out, Fact{pr, strings.ToLower(form[3]), machine.VoidFact(strings.ToLower(form[1])), strconv.FormatInt(record.Seq, 10)})
	}
	return out
}

func Appeal(record client.Record) bool {
	return string(record.Kind) == "decide" && client.Stem(record.Lane) == AppealsLane && len(Overrides(record)) > 0
}

type UnreadOverride struct {
	Text string
	Fix  string
}

func UnreadOverrides(record client.Record) []UnreadOverride {
	text := quotedSpan.ReplaceAllString(quotedBlock.ReplaceAllString(FullText(record), ""), "")
	var unread []UnreadOverride
	for _, span := range overrideNamed.FindAllStringIndex(text, -1) {
		if !clauseStart.MatchString(text[:span[0]]) {
			continue
		}
		clause := text[span[0]:span[1]]
		form := overrideForm.FindStringSubmatch(clause)
		if form != nil && (!strings.EqualFold(form[1], "finder") || form[4] != "") {
			continue
		}
		override := UnreadOverride{Text: strings.TrimSpace(clause)}
		if form != nil {
			if cite := citedRecord.FindStringSubmatch(clause[len(form[0]):]); cite != nil {
				override.Fix = fmt.Sprintf("%s-override #%s at %s: %s", strings.ToLower(form[1]), form[2], strings.ToLower(form[3]), cite[1])
			}
		}
		unread = append(unread, override)
	}
	return unread
}

func overrideFacts(counted []Override) []Fact {
	var out []Fact
	for _, override := range counted {
		fact := Fact{override.PR, override.Head, machine.OverrideFact(override.Grader), override.Value()}
		if !slices.Contains(out, fact) {
			out = append(out, fact)
		}
	}
	return out
}

func isGo(record client.Record) bool {
	kind := string(record.Kind)
	return kind == "go" || slices.Contains(goKinds, kind) && len(goHeads(record.Text)) > 0
}

type headPair struct {
	PR   int
	Head string
}

func goHeads(text string) []headPair {
	if withdrawnGo.MatchString(text) {
		return nil
	}
	clause := goClause.FindStringSubmatch(text)
	if clause == nil {
		return nil
	}
	var pairs []headPair
	for _, named := range goHead.FindAllStringSubmatch(clause[1], -1) {
		pr, err := strconv.Atoi(named[1])
		if err != nil {
			continue
		}
		pairs = append(pairs, headPair{pr, strings.ToLower(named[2])})
	}
	return pairs
}

func RootGos(records []client.Record) []client.Record {
	return LiveGos(records, nil)
}

func LiveGos(records []client.Record, goLanes []string) []client.Record {
	lanes := goAuthors(goLanes)
	closed := make(map[int64]bool)
	for _, record := range records {
		if delegated(record.Lane, lanes) {
			for _, seq := range closes(record) {
				closed[seq] = true
			}
		}
	}
	return slices.DeleteFunc(slices.Clone(records), func(record client.Record) bool {
		return !delegated(record.Lane, lanes) || !isGo(record) || closed[record.Seq]
	})
}

func ClosedGos(record client.Record, goLanes []string) []int64 {
	kind := string(record.Kind)
	if (kind != "withdraw" && kind != "correction") || !delegated(record.Lane, goAuthors(goLanes)) {
		return nil
	}
	return slices.DeleteFunc(closes(record), func(seq int64) bool { return seq == 0 })
}

func WithdrawnOverrides(withdraw client.Record, targets []client.Record) []Override {
	if string(withdraw.Kind) != "withdraw" {
		return nil
	}
	withdrawn := slices.Concat([]int64{withdraw.Re, withdraw.Resolves}, withdraw.AlsoResolves)
	var out []Override
	for _, target := range targets {
		if target.Seq != 0 && slices.Contains(withdrawn, target.Seq) && client.Stem(target.Lane) == client.Stem(withdraw.Lane) {
			out = append(out, Overrides(target)...)
		}
	}
	return out
}

func Withdraws(record client.Record) []int64 {
	if string(record.Kind) != "withdraw" {
		return nil
	}
	return slices.DeleteFunc(slices.Concat([]int64{record.Re, record.Resolves}, record.AlsoResolves), func(seq int64) bool { return seq == 0 })
}

func closes(record client.Record) []int64 {
	seqs := append([]int64{record.Resolves}, record.AlsoResolves...)
	kind := string(record.Kind)
	if kind != "withdraw" && kind != "correction" {
		return seqs
	}
	seqs = append(seqs, record.Re)
	for _, named := range withdrawsNamed.FindAllStringSubmatch(record.Text, -1) {
		if seq, err := strconv.ParseInt(named[1], 10, 64); err == nil {
			seqs = append(seqs, seq)
		}
	}
	return seqs
}

func GoNames(record client.Record, pr int) bool {
	return slices.ContainsFunc(goHeads(record.Text), func(pair headPair) bool { return pair.PR == pr })
}

// GoLifts reports whether one of gos, posted after hold, names pr. Seq only
// orders a store's arrivals, and a relay backfill numbers an old go last.
func GoLifts(gos []client.Record, hold client.Record, pr int) bool {
	return slices.ContainsFunc(gos, func(g client.Record) bool { return postedAfter(g, hold) && GoNames(g, pr) })
}

func postedAfter(a, b client.Record) bool {
	if a.Origin == b.Origin {
		return originSeq(a) > originSeq(b)
	}
	return a.At.After(b.At)
}

func originSeq(record client.Record) int64 {
	if record.OriginSeq == 0 {
		return record.Seq
	}
	return record.OriginSeq
}

func GoNamesHead(record client.Record, pr int, head string) bool {
	return slices.ContainsFunc(goHeads(record.Text), func(pair headPair) bool { return pair.PR == pr && strings.HasPrefix(head, pair.Head) })
}

func PRRecords(ctx context.Context, drive, repo string, pr int) ([]client.Record, error) {
	records, err := client.Tail(ctx, client.Query{Drive: drive, PR: fmt.Sprintf("%s#%d", repo, pr)})
	if err != nil || len(records) == 0 {
		return records, err
	}
	root, err := client.Tail(ctx, client.Query{Drive: drive, Kinds: []string{"decide", "withdraw"}, Lanes: []string{rootLane}, After: records[0].Seq})
	if err != nil {
		return nil, err
	}
	held := map[int64]bool{}
	overriding := map[int64]bool{}
	names := func(record client.Record) bool {
		return slices.ContainsFunc(CountedOverrides(record), func(o Override) bool { return o.PR == pr })
	}
	for _, record := range records {
		held[record.Seq] = true
		overriding[record.Seq] = names(record)
	}
	merged := slices.Clone(records)
	for _, record := range root {
		if !held[record.Seq] && names(record) {
			overriding[record.Seq] = true
			merged = append(merged, record)
		}
	}
	for _, record := range root {
		if !held[record.Seq] && slices.ContainsFunc(Withdraws(record), func(seq int64) bool { return overriding[seq] }) {
			merged = append(merged, record)
		}
	}
	slices.SortStableFunc(merged, func(a, b client.Record) int { return cmp.Compare(a.Seq, b.Seq) })
	return merged, nil
}

func HoldLifts(records []client.Record, read func([]int64) ([]client.Record, error)) (map[int64]bool, error) {
	resolving := map[int64][]int64{}
	var seqs []int64
	for _, record := range records {
		if string(record.Kind) != "hold" {
			continue
		}
		resolved := slices.DeleteFunc(append([]int64{record.Resolves}, record.AlsoResolves...), func(seq int64) bool { return seq == 0 })
		if len(resolved) > 0 {
			resolving[record.Seq] = resolved
			seqs = append(seqs, resolved...)
		}
	}
	lifts := make(map[int64]bool, len(resolving))
	if len(seqs) == 0 {
		return lifts, nil
	}
	targets, err := read(seqs)
	if err != nil {
		for seq := range resolving {
			lifts[seq] = true
		}
		return lifts, err
	}
	holds := map[int64]bool{}
	for _, target := range targets {
		holds[target.Seq] = string(target.Kind) == "hold"
	}
	for seq, resolved := range resolving {
		lifts[seq] = slices.ContainsFunc(resolved, func(target int64) bool { return holds[target] })
	}
	return lifts, nil
}

func HoldsUntilTap(record client.Record) bool {
	return len(record.Refs.PRs) == 1 && (record.Fields.Attrs[holdUntilAttr] == holdUntilTap || untilHisWord.MatchString(record.Text))
}

func HeldBy(record client.Record, repo string) []int {
	var prs []int
	for _, pr := range record.Refs.PRs {
		if pr.Repo == "" || strings.EqualFold(pr.Repo, repo) {
			prs = append(prs, pr.Number)
		}
	}
	return prs
}
