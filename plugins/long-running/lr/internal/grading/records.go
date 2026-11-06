package grading

import (
	"context"
	"regexp"
	"slices"
	"strconv"
	"strings"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/grading/grader"
)

const (
	rootLane         = "root"
	headAttr         = "head:"
	finderLine       = "finder:"
	fidelityOK       = "fidelity: ok"
	fidelityMissing  = "fidelity: missing"
	fidelityReader   = "fidelity: reader"
	overrideFinder   = "finder"
	overrideFidelity = "fidelity"
	clipMark         = "…"
	kindReview       = "review"
	kindHead         = "head"
	kindDecide       = "decide"
	kindAnswer       = "answer"
	kindNote         = "note"
	kindGo           = "go"
	kindContract     = "contract"
	kindDefect       = "defect"
	kindIncident     = "incident"
	kindCorrection   = "correction"
)

var (
	shortSHA      = regexp.MustCompile(`\b[0-9a-f]{7,40}\b`)
	spelledSHA    = regexp.MustCompile(`\b[0-9a-f]{40}\b`)
	wholeSHA      = regexp.MustCompile(`^[0-9a-f]{40}$`)
	wasHead       = regexp.MustCompile(`\(was ([0-9a-f]{7,40})\b`)
	prNamed       = regexp.MustCompile(`#(\d+)\b|\[(\d+)\]`)
	finderNamed   = regexp.MustCompile(`(?i)\bfinder\b`)
	laneNumber    = regexp.MustCompile(`^(.+?)(?:-(\d+))?$`)
	clearWord     = regexp.MustCompile(`(?i)\bclear(?:s|ed)?\b`)
	clearPhrase   = regexp.MustCompile(`(?i)\brebuttal accepted\b|\bland it\b`)
	overrideNamed = regexp.MustCompile(`(?i)\b(?:finder|fidelity)-override\b[^.;\n]*`)
	clauseStart   = regexp.MustCompile(`(?m)(?:^|[.!?]\s+)(?:\d{1,2}:\d{2} [AP]M\s+)?(?:(?:root|main):\s+)?$`)
	overrideRead  = regexp.MustCompile(`(?i)^(finder|fidelity)-override\s+#?(\d+)\s+(?:at\s+)?([0-9a-f]{7,40})\b`)
	citedRecord   = regexp.MustCompile(`#(\d{6,})\b`)
	acceptedWord  = regexp.MustCompile(`(?i)^\s*accept(?:ed|s)?\b`)
	classWrong    = regexp.MustCompile(`(?i)^(?:re #\d+:\s*)?class wrong\b`)
	oldAppGrade   = regexp.MustCompile(`(?im)^old-app:\s*(?:unchanged|bug fix|changes)\b.*$`)
	graderLines   = []string{"fidelity:", "old-app:", "touches "}
)

type review struct {
	client.Record
	grade string
	head  string
}

type headEntry struct {
	commit string
	was    string
}

func refsName(r client.Record, repo, pr string) bool {
	for _, named := range r.Refs.PRs {
		if strconv.Itoa(named.Number) == pr && (named.Repo == "" || strings.EqualFold(named.Repo, repo)) {
			return true
		}
	}
	return false
}

func aboutPR(r client.Record, repo, pr string) bool {
	if len(r.Refs.PRs) > 0 {
		return refsName(r, repo, pr)
	}
	for _, found := range prNamed.FindAllStringSubmatch(r.Text, -1) {
		if found[1] == pr || found[2] == pr {
			return true
		}
	}
	return false
}

func headEntries(r client.Record, repo string) map[string]headEntry {
	var entries map[string]headEntry
	for key, value := range r.Fields.Attrs {
		ref, isHead := strings.CutPrefix(key, headAttr)
		if !isHead {
			continue
		}
		if entries == nil {
			entries = map[string]headEntry{}
		}
		number, inRepo := attrPR(ref, repo)
		if !inRepo {
			continue
		}
		moved, _, _ := strings.Cut(value, " ")
		was, commit := "", moved
		if at := strings.LastIndex(moved, ".."); at >= 0 {
			was, commit = moved[:at], moved[at+2:]
		}
		entries[number] = headEntry{strings.ToLower(commit), strings.ToLower(was)}
	}
	return entries
}

func attrPR(ref, repo string) (string, bool) {
	if _, err := strconv.Atoi(ref); err == nil {
		return ref, true
	}
	at := strings.LastIndex(ref, "#")
	if at < 0 || !strings.EqualFold(ref[:at], repo) {
		return "", false
	}
	return ref[at+1:], true
}

func headOf(r client.Record, repo, pr string) string {
	entries := headEntries(r, repo)
	if entries == nil {
		return shortSHA.FindString(r.Text)
	}
	return entries[pr].commit
}

func headNamed(r client.Record, heads []client.Record, repo, pr string) string {
	if named := shortSHA.FindString(r.Text); named != "" {
		return named
	}
	for _, posted := range slices.Backward(heads) {
		if posted.Seq < r.Seq {
			return headOf(posted, repo, pr)
		}
	}
	return ""
}

func aboutAndHeads(found []client.Record, repo, pr string) (about, heads []client.Record) {
	for _, r := range found {
		if !aboutPR(r, repo, pr) {
			continue
		}
		about = append(about, r)
		if r.Kind == kindHead {
			heads = append(heads, r)
		}
	}
	return about, heads
}

func delegated(lane, delegate string) bool {
	if lane == delegate {
		return true
	}
	base, number := laneParts(delegate)
	other, later := laneParts(lane)
	return other == base && later > max(number, 1)
}

func laneParts(lane string) (string, int) {
	found := laneNumber.FindStringSubmatch(lane)
	number, err := strconv.Atoi(found[2])
	if err != nil {
		return found[1], 0
	}
	return found[1], number
}

func finderReviews(found []client.Record, repo, pr string) []review {
	about, heads := aboutAndHeads(found, repo, pr)
	reviews := make([]review, 0, len(about))
	for _, r := range about {
		if r.Kind != kindReview || !finderNamed.MatchString(r.Text) || startsWithAny(r.Text, graderLines) {
			continue
		}
		grade := ""
		if delegated(r.Lane, grader.FinderLane) && strings.HasPrefix(r.Text, finderLine) {
			grade = grader.FinderGrade(r.Text)
		}
		reviews = append(reviews, review{r, grade, headNamed(r, heads, repo, pr)})
	}
	return reviews
}

func startsWithAny(text string, prefixes []string) bool {
	return slices.ContainsFunc(prefixes, func(prefix string) bool { return strings.HasPrefix(text, prefix) })
}

func gradedReviews(reviews []review) []review {
	return slices.DeleteFunc(slices.Clone(reviews), func(r review) bool { return r.grade == "" || r.head == "" })
}

func since(graded []review, found []client.Record, head, repo string) (string, *grader.Record) {
	var prior *review
	for index := range graded {
		if !grader.NamesHead(graded[index].head, head) {
			prior = &graded[index]
		}
	}
	if prior == nil {
		return "", nil
	}
	earlier := &grader.Record{Seq: prior.Seq, Num: prior.Num, Lane: prior.Lane, Head: prior.head, Text: prior.Text}
	if wholeSHA.MatchString(prior.head) {
		return prior.head, earlier
	}
	for _, r := range found {
		if r.Kind != kindHead {
			continue
		}
		for _, sha := range spelled(r, repo) {
			if wholeSHA.MatchString(sha) && strings.HasPrefix(sha, prior.head) {
				return sha, earlier
			}
		}
	}
	return "", earlier
}

func spelled(r client.Record, repo string) []string {
	shas := []string{r.Fields.Commit}
	for _, entry := range headEntries(r, repo) {
		shas = append(shas, entry.commit, entry.was)
	}
	return append(shas, spelledSHA.FindAllString(r.Text, -1)...)
}

func announces(r client.Record, repo, pr, head string) bool {
	if entries := headEntries(r, repo); entries != nil {
		commit := entries[pr].commit
		return commit != "" && grader.NamesHead(commit, head)
	}
	if r.Fields.Commit != "" {
		return grader.NamesHead(r.Fields.Commit, head)
	}
	for _, at := range shortSHA.FindAllStringIndex(r.Text, -1) {
		if !strings.HasSuffix(r.Text[:at[0]], "was ") && grader.NamesHead(r.Text[at[0]:at[1]], head) {
			return true
		}
	}
	return false
}

func previousHead(found []client.Record, repo, pr, head string) string {
	for _, r := range slices.Backward(found) {
		if r.Kind != kindHead || !aboutPR(r, repo, pr) || !announces(r, repo, pr, head) {
			continue
		}
		if entries := headEntries(r, repo); entries != nil {
			return entries[pr].was
		}
		if said := wasHead.FindStringSubmatch(r.Text); said != nil {
			return said[1]
		}
		return ""
	}
	return ""
}

func withSuccessors(lanes []string, found []client.Record) []string {
	read := map[string]bool{}
	for _, r := range found {
		if read[r.Lane] {
			continue
		}
		read[r.Lane] = true
		succeeds := func(lane string) bool { return delegated(r.Lane, lane) }
		if !slices.Contains(lanes, r.Lane) && slices.ContainsFunc(lanes, succeeds) {
			lanes = append(lanes, r.Lane)
		}
	}
	return lanes
}

func fidelityGrades(found []client.Record, lanes []string, repo, pr string) []client.Record {
	grades := make([]client.Record, 0, len(found))
	for _, r := range found {
		if r.Kind != kindReview || !slices.Contains(lanes, r.Lane) || !startsWithAny(r.Text, []string{fidelityOK, fidelityMissing}) || !refsName(r, repo, pr) {
			continue
		}
		r.Text, r.Refs.Path = bus.FullText(r), ""
		grades = append(grades, r)
	}
	return grades
}

func readerSends(found []client.Record) []client.Record {
	return slices.DeleteFunc(slices.Clone(found), func(r client.Record) bool {
		return r.Kind != kindReview || r.Lane != grader.FidelityLane || !strings.HasPrefix(r.Text, fidelityReader)
	})
}

func anyNaming(records []client.Record, sha string) bool {
	return slices.ContainsFunc(records, func(r client.Record) bool {
		named := shortSHA.FindString(r.Text)
		return named != "" && grader.NamesHead(named, sha)
	})
}

func oldApps(found []client.Record, lanes []string, repo, pr string) []grader.OldApp {
	about, heads := aboutAndHeads(found, repo, pr)
	apps := make([]grader.OldApp, 0, len(about))
	for _, r := range about {
		if r.Kind != kindReview || !slices.Contains(lanes, r.Lane) {
			continue
		}
		line := oldAppGrade.FindString(bus.FullText(r))
		head := headNamed(r, heads, repo, pr)
		if line == "" || head == "" {
			continue
		}
		apps = append(apps, grader.OldApp{Seq: r.Seq, Lane: r.Lane, Head: head, Line: line})
	}
	return apps
}

func newestContract(contracts []client.Record, repo, pr string) string {
	for _, r := range slices.Backward(contracts) {
		if aboutPR(r, repo, pr) {
			return bus.FullText(r)
		}
	}
	if len(contracts) == 0 {
		return ""
	}
	return bus.FullText(contracts[len(contracts)-1])
}

func resolves(r client.Record, seq int64) bool {
	return r.Resolves == seq || slices.Contains(r.AlsoResolves, seq)
}

func finderCleared(text string) bool {
	if clearPhrase.MatchString(text) {
		return true
	}
	for _, at := range clearWord.FindAllStringIndex(text, -1) {
		before := strings.ToLower(text[:at[0]])
		if !strings.HasSuffix(before, "not ") && !strings.HasSuffix(before, "never ") {
			return true
		}
	}
	return false
}

func overridden(text, kind, pr, sha string) bool {
	for _, at := range overrideNamed.FindAllStringIndex(text, -1) {
		if !clauseStart.MatchString(text[:at[0]]) {
			continue
		}
		found := overrideRead.FindStringSubmatch(text[at[0]:at[1]])
		if found != nil && strings.EqualFold(found[1], kind) && found[2] == pr && grader.NamesHead(strings.ToLower(found[3]), sha) {
			return true
		}
	}
	return false
}

func decideClearsFinder(decide client.Record, text, pr, sha string, verdict client.Record) bool {
	if decide.Seq <= verdict.Seq {
		return false
	}
	ids := []int64{verdict.Seq}
	if verdict.OriginSeq != 0 {
		ids = append(ids, verdict.OriginSeq)
	}
	if slices.ContainsFunc(ids, func(id int64) bool { return resolves(decide, id) }) {
		return true
	}
	clause := regexp.MustCompile(`(?im)(?:^|[.;]\s+)(?:\d{1,2}:\d\d [AP]M\s+)?(?:root:\s+)?clear(?:ed|s)?\b[^.;]*\bfinder\b[^.;]*#` + pr + `\s+(?:at\s+)?` + sha[:min(7, len(sha))])
	if clause.MatchString(text) || overridden(text, overrideFinder, pr, sha) {
		return true
	}
	return slices.Contains(ids, decide.Re) && finderCleared(decide.Text)
}

func decideSettles(decide client.Record, text, repo, pr string, r client.Record) bool {
	if decide.Re == r.Seq || resolves(decide, r.Seq) {
		return true
	}
	if r.Seq < decide.Seq && strings.Contains(strings.ToLower(decide.Text), "settled") && aboutPR(decide, repo, pr) {
		return true
	}
	sha := shortSHA.FindString(r.Text)
	if strings.HasPrefix(r.Text, finderLine) && decideClearsFinder(decide, text, pr, sha, r) {
		return true
	}
	return strings.HasPrefix(r.Text, fidelityMissing) && r.Seq < decide.Seq && overridden(text, overrideFidelity, pr, sha)
}

func rootSettled(ctx context.Context, drive, repo, pr string, named []client.Record) (map[string]int64, error) {
	settled := map[string]int64{}
	if len(named) == 0 {
		return settled, nil
	}
	seqs := make(map[int64]bool, len(named))
	oldest := named[0].Seq
	for _, r := range named {
		seqs[r.Seq] = true
		oldest = min(oldest, r.Seq)
	}
	decides, err := client.Tail(ctx, client.Query{Drive: drive, Kinds: []string{kindDecide}, Lanes: []string{rootLane}, After: oldest})
	if err != nil {
		return nil, err
	}
	for _, decide := range decides {
		text := bus.FullText(decide)
		for _, r := range named {
			key := strconv.FormatInt(r.Seq, 10)
			if _, done := settled[key]; !done && decideSettles(decide, text, repo, pr, r) {
				settled[key] = decide.Seq
			}
		}
	}
	pull := repo + "#" + pr
	if err := settleByCountedOverrides(ctx, drive, pull, pr, oldest, named, settled); err != nil {
		return nil, err
	}
	if err := settleByRebuttals(ctx, drive, repo, pr, pull, oldest, seqs, settled); err != nil {
		return nil, err
	}
	return settled, settleByAcceptance(ctx, drive, pull, seqs, settled)
}

func settleByCountedOverrides(ctx context.Context, drive, pull, pr string, oldest int64, named []client.Record, settled map[string]int64) error {
	decides, err := client.Tail(ctx, client.Query{Drive: drive, Kinds: []string{kindDecide}, Lanes: []string{bus.SweepLane}, PR: pull, After: oldest})
	if err != nil {
		return err
	}
	for _, decide := range decides {
		text := bus.FullText(decide)
		for _, r := range named {
			if !hasSettled(settled, r.Seq) && r.Seq < decide.Seq && overrideNames(text, pr, r.Text) {
				settled[strconv.FormatInt(r.Seq, 10)] = decide.Seq
			}
		}
	}
	return nil
}

func overrideNames(text, pr, grade string) bool {
	sha := shortSHA.FindString(grade)
	return strings.HasPrefix(grade, finderLine) && overridden(text, overrideFinder, pr, sha) || strings.HasPrefix(grade, fidelityMissing) && overridden(text, overrideFidelity, pr, sha)
}

func settleByRebuttals(ctx context.Context, drive, repo, pr, pull string, oldest int64, seqs map[int64]bool, settled map[string]int64) error {
	replies, err := client.Tail(ctx, client.Query{Drive: drive, Kinds: []string{kindNote, kindAnswer}, PR: pull, After: oldest})
	if err != nil {
		return err
	}
	rebuttals := make([]client.Record, 0, len(replies))
	cited := make([]int64, 0, len(replies))
	for _, r := range replies {
		if !seqs[r.Re] || hasSettled(settled, r.Re) {
			continue
		}
		rebuttals = append(rebuttals, r)
		cited = append(cited, citedSeqs(bus.FullText(r))...)
	}
	if len(cited) == 0 {
		return nil
	}
	found, err := client.Among(ctx, drive, cited)
	if err != nil {
		return err
	}
	rulings := make(map[int64]bool, len(found))
	for _, r := range found {
		if r.Kind == kindDecide && r.Lane == rootLane && aboutPR(r, repo, pr) {
			rulings[r.Seq] = true
		}
	}
	applyRebuttals(rebuttals, rulings, settled)
	return nil
}

func applyRebuttals(rebuttals []client.Record, rulings map[int64]bool, settled map[string]int64) {
	for _, r := range rebuttals {
		for _, seq := range citedSeqs(bus.FullText(r)) {
			if rulings[seq] && !hasSettled(settled, r.Re) {
				settled[strconv.FormatInt(r.Re, 10)] = seq
			}
		}
	}
}

func settleByAcceptance(ctx context.Context, drive, pull string, seqs map[int64]bool, settled map[string]int64) error {
	answers, err := client.Tail(ctx, client.Query{Drive: drive, Kinds: []string{kindAnswer}, PR: pull})
	if err != nil {
		return err
	}
	answers = slices.DeleteFunc(answers, func(r client.Record) bool { return !seqs[r.Re] || !acceptedWord.MatchString(r.Text) })
	if len(answers) == 0 {
		return nil
	}
	rulings, err := client.Tail(ctx, client.Query{Drive: drive, Kinds: []string{kindGo, kindDecide}, Lanes: []string{rootLane}, PR: pull})
	if err != nil {
		return err
	}
	for _, answer := range answers {
		if hasSettled(settled, answer.Re) {
			continue
		}
		for _, ruling := range rulings {
			if ruling.Seq > answer.Seq {
				settled[strconv.FormatInt(answer.Re, 10)] = ruling.Seq
				break
			}
		}
	}
	return nil
}

func hasSettled(settled map[string]int64, seq int64) bool {
	_, done := settled[strconv.FormatInt(seq, 10)]
	return done
}

func citedSeqs(text string) []int64 {
	found := citedRecord.FindAllStringSubmatch(text, -1)
	seqs := make([]int64, 0, len(found))
	for _, cited := range found {
		seq, err := strconv.ParseInt(cited[1], 10, 64)
		if err == nil {
			seqs = append(seqs, seq)
		}
	}
	return seqs
}
