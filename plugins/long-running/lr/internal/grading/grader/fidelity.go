package grader

import (
	"bytes"
	"cmp"
	"context"
	_ "embed"
	"encoding/json"
	"fmt"
	"maps"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
)

const (
	FidelityLane    = "fidelity-pipeline"
	fidelityOK      = "fidelity: ok"
	fidelityMissing = "fidelity: missing"
	fidelityReader  = "fidelity: reader"
	oldAppPending   = "OLD-APP-PENDING"
	behaviorLine    = "behavior:"
	findingChars    = 160
	fidelityTimeout = 3600 * time.Second
	deletionKey     = "owner/deletion"
	answerKey       = "answer/"
	rubricFile      = "fidelity-desk-brief.md"
	schemaFile      = "fidelity.schema.json"
)

//go:embed briefs/fidelity-job-brief.md
var fidelityBrief string

//go:embed briefs/fidelity-desk-brief.md
var fidelityRubric []byte

//go:embed briefs/fidelity.schema.json
var fidelitySchema []byte

var (
	carriedSeq  = regexp.MustCompile(`\bcarried from [0-9a-f]{7,40} \(cci #(\d+)\)`)
	oldAppLine  = regexp.MustCompile(`(?i)^old-app:\s*(unchanged|bug fix|changes)\b:?\s*(?:[0-9a-f]{7,40}\b)?\s*(.*)$`)
	planLine    = regexp.MustCompile(`^plan:(?:[\w./-]+\.md:)?\d+(?:-\d+)?$`)
	agentsLine  = regexp.MustCompile(`^(?:[\w.-]+/)*AGENTS\.md:\d+(?:-\d+)?$`)
	spanDash    = regexp.MustCompile(`[–—]`)
	deletes     = regexp.MustCompile(`(?i)\b(?:delete|deletes|deleting|remove|removes|removing|drop|drops|dropping)\s+(?:the\s+|a\s+|an\s+|its\s+)?([^,;:()\n]+)`)
	numstatLine = regexp.MustCompile(`^(\d+)\t(\d+)\t`)
	ownerWords  = regexp.MustCompile(`(?i)\bowner\b[^"“\n]{0,80}["“]([^"”]+)["”]`)
	nounWord    = regexp.MustCompile(`[a-z][a-z0-9]+`)
	shortSHA    = regexp.MustCompile(`\b[0-9a-f]{7,40}\b`)
	afterWords  = regexp.MustCompile(`(?s)^\s*(\S+)\s+(\S+)\s+(.*)$`)
	generic     = map[string]bool{"page": true, "pages": true, "route": true, "routes": true, "file": true, "files": true, "feature": true, "features": true, "endpoint": true, "endpoints": true, "code": true, "which": true, "that": true, "and": true, "the": true, "its": true, "from": true, "for": true, "with": true, "unused": true, "old": true, "dead": true}
)

type OldApp struct {
	Seq  int64  `json:"seq"`
	Lane string `json:"lane"`
	Head string `json:"head"`
	Line string `json:"line"`
}

type Ruling struct {
	Seq  int64  `json:"seq"`
	Text string `json:"text"`
}

type FidelityJob struct {
	Job
	Readers        []string          `json:"readers"`
	Lanes          []string          `json:"lanes"`
	Grades         []Record          `json:"grades"`
	OldApps        []OldApp          `json:"old_apps"`
	Corrected      []int64           `json:"corrected"`
	Rulings        []Ruling          `json:"rulings"`
	Contract       string            `json:"contract"`
	Sources        string            `json:"sources"`
	Regrade        int               `json:"regrade,omitempty"`
	ReportOnly     bool              `json:"report_only,omitempty"`
	Exempt         map[string]string `json:"exempt"`
	ExemptGrades   []string          `json:"exempt_grades"`
	Baseline       string            `json:"baseline"`
	Since          string            `json:"since,omitempty"`
	Claims         []string          `json:"claims,omitempty"`
	ServiceTouches []ServiceTouch    `json:"service_touches,omitempty"`
	Findings       []FindingRow      `json:"findings,omitempty"`
	OwedDeletions  []string          `json:"owed_deletions,omitempty"`
}

type missingEntry struct {
	Key     string `json:"key"`
	Line    string `json:"line"`
	Settled int64  `json:"settled"`
}

type fidelityOldApp struct {
	Grade string `json:"grade"`
	Why   string `json:"why"`
}

type fidelityReply struct {
	Verdict     string         `json:"verdict"`
	Summary     string         `json:"summary"`
	Swept       string         `json:"swept"`
	Checked     []string       `json:"checked"`
	Names       []string       `json:"names"`
	Missing     []missingEntry `json:"missing"`
	Touches     []string       `json:"touches"`
	Exempt      string         `json:"exempt"`
	Reader      string         `json:"reader"`
	OldApp      fidelityOldApp `json:"old_app"`
	Behavior    string         `json:"behavior"`
	WhatChanged string         `json:"what_changed"`
	picks       string
}

type FidelityOutcome struct {
	Skipped string   `json:"skipped,omitempty"`
	Verdict string   `json:"verdict,omitempty"`
	From    string   `json:"from,omitempty"`
	Posted  []string `json:"posted,omitempty"`
}

type gradeLine struct {
	text string
	also []string
}

type fidelityPassState struct {
	*pass
	job     *FidelityJob
	touches []string
	class   string
}

func Fidelity(ctx context.Context, dir string, deps Deps) error {
	outcome, err := runFidelity(ctx, dir, deps)
	return settle(dir, outcome, err)
}

func runFidelity(ctx context.Context, dir string, deps Deps) (FidelityOutcome, error) {
	data, err := os.ReadFile(filepath.Join(dir, JobFile))
	if err != nil {
		return FidelityOutcome{}, err
	}
	var spec FidelityJob
	if err := json.Unmarshal(data, &spec); err != nil {
		return FidelityOutcome{}, err
	}
	state := &fidelityPassState{pass: &pass{Deps: deps, ctx: ctx, dir: dir, job: &spec.Job}, job: &spec}
	return state.grade()
}

func (s *fidelityPassState) grade() (FidelityOutcome, error) {
	spec := s.job
	if err := spec.inputsArrived(); err != nil {
		return FidelityOutcome{}, err
	}
	pull := spec.pull()
	plan := s.planRead()
	view, err := s.viewPR()
	if err != nil {
		return FidelityOutcome{}, err
	}
	if view.State == "CLOSED" {
		return FidelityOutcome{Skipped: pull + " is closed"}, nil
	}
	if !strings.HasPrefix(view.HeadRefOid, spec.Head) {
		return FidelityOutcome{Skipped: fmt.Sprintf("%s moved to %s", pull, short(view.HeadRefOid, 12))}, nil
	}
	spec.Head = view.HeadRefOid
	head := spec.Head
	if len(spec.Findings) > 0 {
		return s.postOnly(findingsLine(head, spec.Findings))
	}
	var heads []candidate
	if spec.Since == "" {
		heads = s.candidates()
	}
	var slicing *future[string]
	if len(heads) == 0 {
		slicing = s.sliceRows(head, view.BaseRefName, "--words", "--cards", filepath.Join(s.dir, CardsFile))
	}
	base, trunk, err := s.checkedOut(view)
	if err != nil {
		return FidelityOutcome{}, err
	}
	own, err := s.beyond([]string{base, trunk}, head)
	if err != nil {
		return FidelityOutcome{}, err
	}
	if len(own) == 0 && spec.ReportOnly {
		own = []string{head}
	}
	if len(own) == 0 {
		return FidelityOutcome{Skipped: fmt.Sprintf("%s at %s carries no commit %s lacks", pull, short(head, 12), view.BaseRefName)}, nil
	}
	if said := unnamedDeletion(view.Title, view.Body); said != "" {
		stat, err := s.git("diff", "--find-renames", "--numstat", "--summary", own[0]+"^", head)
		if err != nil {
			return FidelityOutcome{}, err
		}
		if removesCode(stat) {
			return s.postOnly(fmt.Sprintf("%s %s %s %s", fidelityMissing, head, deletionKey, said))
		}
	}
	if s.touches, err = s.serviceTouches(own[0] + "^"); err != nil {
		return FidelityOutcome{}, err
	}
	paths, err := s.git("diff", "--name-only", "--no-renames", own[0]+"^", head)
	if err != nil {
		return FidelityOutcome{}, err
	}
	s.class = gates.PathClass(strings.Fields(paths), spec.ExemptGrades)
	if len(heads) > 0 {
		match, err := s.carry(own, heads)
		if err != nil {
			return FidelityOutcome{}, err
		}
		if match != nil {
			return s.carried(match, "git patch-id", "patch-id "+match.patch+" at both heads.")
		}
	}
	last, pairs, changed, err := s.sinceEarlier(heads, base, trunk, own)
	if err != nil {
		return FidelityOutcome{}, err
	}
	if last != "" && len(changed) == 0 {
		proof := fmt.Sprintf("`git range-diff %s` pairs every own commit equal, or amended only by lines the base now carries.", pairs)
		return s.carried(&carryMatch{head: last, grade: newestGrade(heads[0].stand)}, "git range-diff", proof)
	}
	rowsPath := filepath.Join(s.dir, rowsFile)
	if slicing == nil {
		slicing = s.sliceRows(head, view.BaseRefName, "--words", "--cards", filepath.Join(s.dir, CardsFile))
	}
	rows, err := slicing.result()
	if err != nil {
		return FidelityOutcome{}, err
	}
	if err := os.WriteFile(rowsPath, []byte(rows), 0o600); err != nil {
		return FidelityOutcome{}, err
	}
	keys := rowKeys(rows)
	diff, err := s.touched(own[0]+"^", head)
	if err != nil {
		return FidelityOutcome{}, err
	}
	planned, err := planOnThisBox(plan)
	if err != nil {
		return FidelityOutcome{}, err
	}
	frame := promptFrame{base: base, trunk: trunk, rows: rowsPath, plan: planned, diff: diff, scope: scope(head, own, last, pairs, changed)}
	var reply fidelityReply
	for _, attempt := range []int{1, 3} {
		if reply, err = s.askUntilSound(view, frame, keys, attempt); err != nil {
			return FidelityOutcome{}, err
		}
		now, err := s.viewPR()
		if err != nil {
			return FidelityOutcome{}, err
		}
		if now.HeadRefOid != head {
			return FidelityOutcome{Skipped: fmt.Sprintf("%s moved to %s while codex read %s", pull, short(now.HeadRefOid, 12), short(head, 12))}, nil
		}
		if now.rulingsComments() == view.rulingsComments() {
			break
		}
		view = now
	}
	if reply, err = s.unsettled(reply); err != nil {
		return FidelityOutcome{}, err
	}
	reply = s.settleByPicks(reply)
	lines := s.gradedLines(reply)
	body := filepath.Join(s.dir, bodyFile)
	if err := s.writeGradeBody(body, lines, reply); err != nil {
		return FidelityOutcome{}, err
	}
	posted, err := s.post(lines, body)
	if err != nil {
		return FidelityOutcome{}, err
	}
	return FidelityOutcome{Verdict: reply.Verdict, Posted: posted}, nil
}

func (s *fidelityPassState) postOnly(line string) (FidelityOutcome, error) {
	body := filepath.Join(s.dir, bodyFile)
	if err := os.WriteFile(body, []byte(line+"\n"), 0o600); err != nil {
		return FidelityOutcome{}, err
	}
	posted, err := s.post([]gradeLine{{text: line}}, body)
	if err != nil {
		return FidelityOutcome{}, err
	}
	return FidelityOutcome{Verdict: "missing", Posted: posted}, nil
}

func unnamedDeletion(title, body string) string {
	deleted := deletes.FindStringSubmatch(title)
	if deleted == nil {
		return ""
	}
	thing := strings.TrimSpace(deleted[1])
	nounSet := map[string]bool{}
	for _, word := range nounWord.FindAllString(strings.ToLower(thing), -1) {
		if !generic[word] {
			nounSet[word] = true
		}
	}
	nouns := make([]string, 0, len(nounSet))
	for noun := range nounSet {
		nouns = append(nouns, noun)
	}
	slices.Sort(nouns)
	quoted := ownerWords.FindAllStringSubmatch(body, -1)
	if len(nouns) == 0 || len(quoted) == 0 {
		return ""
	}
	cited := make([]string, 0, len(quoted))
	for _, found := range quoted {
		words := found[1]
		for _, noun := range nouns {
			if strings.Contains(strings.ToLower(words), noun) {
				return ""
			}
		}
		runes := []rune(squeezed(words))
		cited = append(cited, "“"+string(runes[:min(len(runes), 160)])+"”")
	}
	return fmt.Sprintf("deletes %s, and the owner words it cites never name %s: %s. A drive PR deletes only what a plan step, the owner's words or a drive-touched fix names.", thing, strings.Join(nouns, ", "), strings.Join(cited, "; "))
}

func removesCode(stat string) bool {
	added, removed := 0, 0
	for line := range strings.SplitSeq(stat, "\n") {
		if strings.HasPrefix(line, " delete mode ") {
			return true
		}
		if counts := numstatLine.FindStringSubmatch(line); counts != nil {
			adds, _ := strconv.Atoi(counts[1])
			removes, _ := strconv.Atoi(counts[2])
			added, removed = added+adds, removed+removes
		}
	}
	return removed > added
}

type FindingRow struct {
	Seq   string `json:"seq"`
	Sweep string `json:"sweep"`
	Owner string `json:"owner"`
	Text  string `json:"text"`
}

func findingsLine(head string, found []FindingRow) string {
	keys := make([]string, 0, len(found))
	said := make([]string, 0, len(found))
	for _, row := range found {
		keys = append(keys, "finding/"+row.Seq)
		runes := []rune(squeezed(row.Text))
		said = append(said, fmt.Sprintf("open finding %s from %s, owner %s: %s", row.Seq, row.Sweep, row.Owner, string(runes[:min(len(runes), findingChars)])))
	}
	return fmt.Sprintf("%s %s %s %s", fidelityMissing, head, strings.Join(keys, ","), strings.Join(said, "; "))
}

type candidate struct {
	head  string
	stand []Record
}

func gradedHead(text string) string {
	return shortSHA.FindString(text)
}

func newestNaming(grades []Record, sha string) *Record {
	var newest *Record
	for index := range grades {
		grade := &grades[index]
		if head := gradedHead(grade.Text); head != "" && NamesHead(head, sha) && (newest == nil || grade.Seq > newest.Seq) {
			newest = grade
		}
	}
	return newest
}

func (s *fidelityPassState) standing(sha string) []Record {
	var stand []Record
	for _, lane := range s.job.Lanes {
		var own []Record
		for _, grade := range s.job.Grades {
			if grade.Lane == lane {
				own = append(own, grade)
			}
		}
		if newest := newestNaming(own, sha); newest != nil {
			stand = append(stand, *newest)
		}
	}
	return stand
}

func (s *fidelityPassState) passes(grade Record) bool {
	if strings.HasPrefix(grade.Text, fidelityOK) {
		return true
	}
	_, settled := s.job.Settled[strconv.FormatInt(grade.Seq, 10)]
	return strings.HasPrefix(grade.Text, fidelityMissing) && settled
}

func (s *fidelityPassState) candidates() []candidate {
	oks := slices.Clone(s.job.Grades)
	slices.SortStableFunc(oks, func(a, b Record) int { return cmp.Compare(b.Seq, a.Seq) })
	var named []string
	for _, grade := range oks {
		if !s.passes(grade) {
			continue
		}
		parts := afterWords.FindStringSubmatch(grade.Text)
		if parts == nil {
			continue
		}
		if head := gradedHead(parts[3]); head != "" && !NamesHead(head, s.job.Head) && !slices.Contains(named, head) {
			named = append(named, head)
		}
	}
	var heads []candidate
	for _, head := range named {
		stand := s.standing(head)
		if len(stand) > 0 && !slices.ContainsFunc(stand, func(grade Record) bool { return !s.passes(grade) }) {
			heads = append(heads, candidate{head, stand})
		}
	}
	return heads
}

func newestGrade(stand []Record) Record {
	return slices.MaxFunc(stand, func(a, b Record) int { return cmp.Compare(a.Seq, b.Seq) })
}

type carryMatch struct {
	head  string
	grade Record
	patch string
}

func (s *fidelityPassState) patchID(start, end string) (string, error) {
	out, err := s.run([]string{"sh", "-c", `git diff "$1" "$2" | git patch-id --stable`, "patch-id", start, end}, s.job.Checkout, gitTimeout)
	if err != nil {
		return "", err
	}
	id, _, _ := strings.Cut(strings.TrimSpace(out), " ")
	return id, nil
}

func (s *fidelityPassState) carry(own []string, heads []candidate) (*carryMatch, error) {
	type patched struct {
		full  string
		patch string
	}
	reading := async(func() (string, error) { return s.patchID(own[0]+"^", s.job.Head) })
	patches := make([]*future[patched], 0, len(heads))
	for _, head := range heads {
		patches = append(patches, async(func() (patched, error) {
			full, err := s.fetched(head.head)
			if err != nil || full == "" {
				return patched{full: full}, err
			}
			patch, err := s.patchID(fmt.Sprintf("%s~%d", full, len(own)), full)
			return patched{full, patch}, err
		}))
	}
	ours, err := reading.result()
	if err != nil {
		return nil, err
	}
	var match *carryMatch
	for index, reading := range patches {
		theirs, err := reading.result()
		if err != nil {
			return nil, err
		}
		if match == nil && ours != "" && theirs.patch == ours {
			match = &carryMatch{head: theirs.full, grade: newestGrade(heads[index].stand), patch: ours}
		}
	}
	return match, nil
}

func (s *fidelityPassState) sinceEarlier(heads []candidate, base, trunk string, own []string) (string, string, []pair, error) {
	if len(heads) == 0 {
		return "", "", nil, nil
	}
	last, err := s.fetched(heads[0].head)
	if err != nil || last == "" {
		return "", "", nil, err
	}
	earlier, err := s.earlierOwn(base, trunk, last)
	if err != nil || len(earlier) == 0 {
		return "", "", nil, err
	}
	pairs, changed, err := s.rangePairs(base, last, earlier, own)
	return last, pairs, changed, err
}

func (s *fidelityPassState) chain(grade Record) []int64 {
	bySeq := make(map[int64]Record, len(s.job.Grades))
	for _, other := range s.job.Grades {
		bySeq[other.Seq] = other
	}
	var seqs []int64
	for current, known := grade, true; known; {
		seqs = append(seqs, current.Seq)
		found := carriedSeq.FindStringSubmatch(current.Text)
		if found == nil {
			break
		}
		seq, _ := strconv.ParseInt(found[1], 10, 64)
		current, known = bySeq[seq]
	}
	return seqs
}

func (s *fidelityPassState) exemptPhrases(text string) []string {
	var said []string
	for _, phrase := range s.job.ExemptGrades {
		if strings.Contains(strings.ToLower(text), strings.ToLower(phrase)) {
			said = append(said, phrase)
		}
	}
	return said
}

func (s *fidelityPassState) carriedClass(grade Record) string {
	if s.class != "" {
		return s.class + ": "
	}
	if slices.ContainsFunc(s.chain(grade), func(seq int64) bool { return slices.Contains(s.job.Corrected, seq) }) {
		return ""
	}
	desk := grade
	found := false
	for _, other := range s.job.Grades {
		if other.Lane != FidelityLane && strings.HasPrefix(other.Text, fidelityOK) && (!found || other.Seq > desk.Seq) {
			desk, found = other, true
		}
	}
	class := ""
	for _, phrase := range s.exemptPhrases(desk.Text) {
		class += phrase + ": "
	}
	return class
}

func (s *fidelityPassState) carried(match *carryMatch, by, proof string) (FidelityOutcome, error) {
	spec := s.job
	settled := ""
	for _, seq := range s.chain(match.grade) {
		if root, known := spec.Settled[strconv.FormatInt(seq, 10)]; known {
			settled = fmt.Sprintf(", settled by coordinator #%d", root)
			break
		}
	}
	lines := []gradeLine{{text: fmt.Sprintf("%s %s %scarried from %s (cci #%d), patch-identical by %s over its own commits%s.", fidelityOK, spec.Head, s.carriedClass(match.grade), short(match.head, 7), match.grade.Seq, by, settled)}}
	if line, touched := s.touchedOldApp(); touched {
		lines = append(lines, gradeLine{text: line})
	} else if earlier := newestOldApp(spec.OldApps, match.head); earlier != nil {
		if parts := oldAppLine.FindStringSubmatch(earlier.Line); parts != nil {
			lines = append(lines, gradeLine{text: strings.TrimRight(fmt.Sprintf("old-app: %s %s %s", parts[1], short(spec.Head, 7), parts[2]), " \t\r\n")})
		}
	}
	body := filepath.Join(s.dir, bodyFile)
	text := fmt.Sprintf("%s\n\n%s\n\n#%d: %s\n", joinedText(lines), proof, match.grade.Seq, match.grade.Text)
	if err := os.WriteFile(body, []byte(text), 0o600); err != nil {
		return FidelityOutcome{}, err
	}
	posted, err := s.post(lines, body)
	if err != nil {
		return FidelityOutcome{}, err
	}
	return FidelityOutcome{Verdict: "carried", From: match.head, Posted: posted}, nil
}

func newestOldApp(records []OldApp, sha string) *OldApp {
	var newest *OldApp
	for index := range records {
		if NamesHead(records[index].Head, sha) && (newest == nil || records[index].Seq > newest.Seq) {
			newest = &records[index]
		}
	}
	return newest
}

func joinedText(lines []gradeLine) string {
	texts := make([]string, 0, len(lines))
	for _, line := range lines {
		texts = append(texts, line.text)
	}
	return strings.Join(texts, "\n")
}

type promptFrame struct {
	base  string
	trunk string
	rows  string
	plan  string
	diff  touched
	scope string
}

func (s *fidelityPassState) prompt(view pullRequest, frame promptFrame) (string, error) {
	spec := s.job
	rubric := filepath.Join(s.dir, rubricFile)
	if err := os.WriteFile(rubric, fidelityRubric, 0o600); err != nil {
		return "", err
	}
	plan, packaged := frame.plan, "none; the register names no plan"
	if plan == "" {
		plan = packaged
	} else {
		packaged = filepath.Dir(filepath.Dir(plan))
	}
	body := strings.TrimSpace(view.Body)
	if body == "" {
		body = "(empty)"
	}
	contract := strings.TrimSpace(spec.Contract)
	if contract == "" {
		contract = "(the lane posted none)"
	}
	sources := spec.Sources
	if sources == "" {
		sources = "none"
	}
	var settled strings.Builder
	for _, point := range s.settledPoints() {
		fmt.Fprintf(&settled, "- %s (coordinator #%d)\n", point.text, point.root)
	}
	var rulings strings.Builder
	for _, r := range spec.Rulings {
		fmt.Fprintf(&rulings, "- coordinator #%d: %s\n", r.Seq, squeezed(r.Text))
	}
	return fill(fidelityBrief, map[string]string{
		"repo":         spec.Repo,
		"pr":           spec.PR,
		"head":         spec.Head,
		"base":         view.BaseRefName,
		"base_oid":     frame.base,
		"trunk_oid":    frame.trunk,
		"checkout":     spec.Checkout,
		"rubric":       rubric,
		"fetch":        spec.Head + " " + frame.base,
		"scope":        frame.scope,
		"hunks":        frame.diff.lines(),
		"rows":         frame.rows,
		"port":         spec.Exempt["port"],
		"classes":      s.classList(),
		"baseline":     s.baseline(),
		"plan":         plan,
		"package":      packaged,
		"contract":     contract,
		"sources":      sources,
		"body":         body,
		"rulings":      view.authorComments(),
		"settled":      orNone(settled.String()),
		"root_rulings": orNone(rulings.String()),
		"since":        cmp.Or(spec.Since, "none"),
		"items":        orNone(spec.itemsText()),
		"owed":         orNone(strings.Join(spec.OwedDeletions, "\n")),
	}), nil
}

func orNone(text string) string {
	if text == "" {
		return nothing
	}
	return text
}

func (s *fidelityPassState) baseline() string {
	if s.job.Baseline != "" {
		return s.job.Baseline
	}
	return "the old app as last released"
}

type settledGrade struct {
	head   string
	points []string
	root   int64
}

func (s *fidelityPassState) settledGrades() []settledGrade {
	var grades []settledGrade
	for _, grade := range s.job.Grades {
		root, settled := s.job.Settled[strconv.FormatInt(grade.Seq, 10)]
		if !settled || !strings.HasPrefix(grade.Text, fidelityMissing) {
			continue
		}
		if parts := afterWords.FindStringSubmatch(strings.TrimPrefix(grade.Text, fidelityMissing)); parts != nil {
			grades = append(grades, settledGrade{parts[1], strings.Split(parts[3], "; "), root})
		}
	}
	return grades
}

type settledPoint struct {
	text string
	root int64
}

func (s *fidelityPassState) settledPoints() []settledPoint {
	var points []settledPoint
	index := map[string]int{}
	for _, grade := range s.settledGrades() {
		for _, point := range grade.points {
			if at, known := index[point]; known {
				points[at].root = grade.root
				continue
			}
			index[point] = len(points)
			points = append(points, settledPoint{point, grade.root})
		}
	}
	return points
}

type place struct {
	path  string
	first int
	last  int
}

func places(line string) []place {
	var found []place
	for _, match := range cite.FindAllStringSubmatch(spanDash.ReplaceAllString(line, "-"), -1) {
		path, first := match[1], match[2]
		if !strings.Contains(path[strings.LastIndex(path, "/")+1:], ".") || agentsLine.MatchString(path+":"+first) {
			continue
		}
		start, _ := strconv.Atoi(first)
		end := start
		if match[3] != "" {
			end, _ = strconv.Atoi(match[3])
		}
		found = append(found, place{path, start, end})
	}
	return found
}

type changeReader struct {
	state *fidelityPassState
	seen  map[string]*touched
}

func (c *changeReader) since(head string) (*touched, error) {
	if known, read := c.seen[head]; read {
		return known, nil
	}
	full, err := c.state.fetched(head)
	if err != nil {
		return nil, err
	}
	var diff *touched
	if full != "" {
		read, err := c.state.touched(full, c.state.job.Head)
		if err != nil {
			return nil, err
		}
		diff = &read
	}
	c.seen[head] = diff
	return diff, nil
}

func (s *fidelityPassState) settledPlace(line string, reader *changeReader) (int64, error) {
	cited := places(line)
	if len(cited) == 0 {
		return 0, nil
	}
	smallest := int64(0)
	for _, at := range cited {
		root, err := s.rootSettling(at, reader)
		if err != nil || root == 0 {
			return 0, err
		}
		if smallest == 0 || root < smallest {
			smallest = root
		}
	}
	return smallest, nil
}

func (s *fidelityPassState) rootSettling(at place, reader *changeReader) (int64, error) {
	for _, grade := range s.settledGrades() {
		for _, named := range places(strings.Join(grade.points, "; ")) {
			if !samePath(at.path, named.path) || named.first > at.last || at.first > named.last {
				continue
			}
			diff, err := reader.since(grade.head)
			if err != nil {
				return 0, err
			}
			if diff != nil && !diff.holds(fmt.Sprintf("%s:%d-%d", at.path, at.first, at.last)) {
				return grade.root, nil
			}
		}
	}
	return 0, nil
}

func (s *fidelityPassState) unsettled(reply fidelityReply) (fidelityReply, error) {
	if reply.Verdict != "missing" {
		return reply, nil
	}
	points := map[string]int64{}
	for _, point := range s.settledPoints() {
		points[point.text] = point.root
	}
	roots := map[int64]bool{}
	for _, root := range points {
		roots[root] = true
	}
	for _, r := range s.job.Rulings {
		roots[r.Seq] = true
	}
	reader := &changeReader{state: s, seen: map[string]*touched{}}
	var kept []missingEntry
	settling := map[int64]bool{}
	for _, entry := range reply.Missing {
		root := points[squeezed(entry.Line)]
		if root == 0 {
			placed, err := s.settledPlace(entry.Line, reader)
			if err != nil {
				return reply, err
			}
			root = placed
		}
		if root == 0 && roots[entry.Settled] {
			root = entry.Settled
		}
		if root == 0 {
			kept = append(kept, entry)
			continue
		}
		settling[root] = true
	}
	if len(kept) > 0 {
		reply.Missing = kept
		return reply, nil
	}
	seqs := make([]int64, 0, len(settling))
	for root := range settling {
		seqs = append(seqs, root)
	}
	slices.Sort(seqs)
	cited := make([]string, 0, len(seqs))
	for _, root := range seqs {
		cited = append(cited, fmt.Sprintf("#%d", root))
	}
	reply.Verdict, reply.Missing = "ok", []missingEntry{}
	reply.Summary = fmt.Sprintf("%s Every missing point is one coordinator settled at %s.", reply.Summary, strings.Join(cited, ", "))
	return reply, nil
}

func grounded(keys []string, key string) bool {
	return planLine.MatchString(key) || agentsLine.MatchString(key) || (slices.Contains(keys, key) && !strings.HasPrefix(key, "design/"))
}

func fidelityShortfalls(keys []string, since string, reply fidelityReply) []string {
	var gaps []string
	var unchecked, ungrounded []string
	for _, key := range keys {
		if !slices.Contains(reply.Checked, key) {
			unchecked = append(unchecked, key)
		}
	}
	if len(unchecked) > 0 {
		gaps = append(gaps, "no grade for "+strings.Join(unchecked, ", "))
	}
	for _, entry := range reply.Missing {
		if !grounded(keys, entry.Key) {
			ungrounded = append(ungrounded, entry.Key)
		}
	}
	if len(ungrounded) > 0 {
		gaps = append(gaps, "`missing` keys that are neither a plan line `plan:<n>`, a package doc line `plan:<path>:<n>`, an AGENTS.md line `<path>AGENTS.md:<n>`, nor a non-design row of the rows file: "+strings.Join(ungrounded, ", "))
	}
	if reply.Verdict == "missing" && len(reply.Missing) == 0 {
		gaps = append(gaps, "verdict missing with no `missing` entries")
	}
	if reply.Verdict == "reader" && strings.TrimSpace(reply.Reader) == "" {
		gaps = append(gaps, "verdict reader with no reason in `reader`")
	}
	if since != "" && reply.Behavior != "same" && reply.Behavior != "changed" {
		gaps = append(gaps, "no `behavior` judged against the tapped head "+since)
	}
	if reply.Behavior == "changed" && strings.TrimSpace(reply.WhatChanged) == "" {
		gaps = append(gaps, "behavior changed with no `what_changed`")
	}
	if len(strings.Fields(reply.Swept)) < reasonWords {
		gaps = append(gaps, reasonless)
	}
	return gaps
}

func (s *fidelityPassState) askFidelity(question string, attempt int) (fidelityReply, error) {
	schema := filepath.Join(s.dir, schemaFile)
	if err := os.WriteFile(schema, fidelitySchema, 0o600); err != nil {
		return fidelityReply{}, err
	}
	out, err := s.rateLimited(func(retry int) (string, error) {
		if said, err := os.ReadFile(filepath.Join(s.dir, CancelledFile)); err == nil {
			return "", fmt.Errorf("%s at %s was cancelled by %s before codex read it", s.job.pull(), short(s.job.Head, 12), strings.TrimSpace(string(said)))
		}
		session := fmt.Sprintf("codex-%d", attempt)
		if retry > 0 {
			session = fmt.Sprintf("%s-r%d", session, retry)
		}
		return s.ask(s.job.Model, schema, filepath.Join(s.dir, session), question, fidelityTimeout)
	})
	if err != nil {
		return fidelityReply{}, err
	}
	text, err := replyText(out)
	if err != nil {
		return fidelityReply{}, err
	}
	var reply fidelityReply
	if err := json.Unmarshal([]byte(text), &reply); err != nil {
		return fidelityReply{}, err
	}
	if len(reply.Missing) > 0 {
		reply.Verdict = "missing"
	}
	return reply, nil
}

func (s *fidelityPassState) askUntilSound(view pullRequest, frame promptFrame, keys []string, attempt int) (fidelityReply, error) {
	question, err := s.prompt(view, frame)
	if err != nil {
		return fidelityReply{}, err
	}
	path := filepath.Join(s.dir, fmt.Sprintf("question-%d.md", attempt))
	if err := os.WriteFile(path, []byte(question), 0o600); err != nil {
		return fidelityReply{}, err
	}
	reply, err := s.askFidelity(path, attempt)
	if err != nil {
		return fidelityReply{}, err
	}
	wrong, unnamed := s.classGaps(reply)
	gaps := append(fidelityShortfalls(keys, s.job.Since, reply), wrong...)
	if len(gaps)+len(unnamed) == 0 {
		return reply, nil
	}
	retry := filepath.Join(s.dir, fmt.Sprintf("question-%d.md", attempt+1))
	if err := os.WriteFile(retry, fmt.Appendf(nil, "%s\nYour last reply gave %s. Every key in the rows file appears in `checked`.\n", question, strings.Join(append(gaps, unnamed...), "; and ")), 0o600); err != nil {
		return fidelityReply{}, err
	}
	if reply, err = s.askFidelity(retry, attempt+1); err != nil {
		return fidelityReply{}, err
	}
	wrong, _ = s.classGaps(reply)
	if gaps = append(fidelityShortfalls(keys, s.job.Since, reply), wrong...); len(gaps) > 0 {
		return fidelityReply{}, &JobFailed{"two replies gave " + strings.Join(gaps, "; and ")}
	}
	return reply, nil
}

func (s *fidelityPassState) classGaps(reply fidelityReply) (wrong, unnamed []string) {
	if _, known := s.job.Exempt[reply.Exempt]; reply.Exempt != "none" && !known {
		wrong = append(wrong, fmt.Sprintf("`exempt` %q, which names no class; the classes are %s", reply.Exempt, s.classList()))
	}
	if len(s.job.Claims) > 0 && reply.Verdict == "ok" && reply.Exempt == "none" && s.class == "" {
		unnamed = append(unnamed, fmt.Sprintf("an ok that names no class, though a lane claims %s for this head; the classes are %s, and `none` stays right when the head is none of them", strings.Join(s.job.Claims, ", "), s.classList()))
	}
	return wrong, unnamed
}

func (s *fidelityPassState) classList() string {
	classes := make([]string, 0, len(s.job.Exempt))
	for _, class := range slices.Sorted(maps.Keys(s.job.Exempt)) {
		if gates.PathDecided(s.job.Exempt[class]) {
			continue
		}
		classes = append(classes, fmt.Sprintf("`%s` (\"%s\")", class, s.job.Exempt[class]))
	}
	return strings.Join(classes, ", ")
}

func (s *fidelityPassState) gradedLines(reply fidelityReply) []gradeLine {
	spec := s.job
	head := spec.Head
	lines := make([]gradeLine, 0, len(reply.Touches)+3)
	reader := fmt.Sprintf("%s %s %s", fidelityReader, head, squeezed(reply.Reader))
	switch reply.Verdict {
	case "ok":
		said := ""
		switch phrase := spec.Exempt[reply.Exempt]; {
		case s.class != "":
			said = s.class + ": "
		case reply.Exempt != "none" && !gates.PathDecided(phrase):
			said = phrase + ": "
		}
		lines = append(lines, gradeLine{text: fmt.Sprintf("%s %s %s%s", fidelityOK, head, said, squeezed(reply.Summary))})
	case "missing":
		var keys []string
		broken := make([]string, 0, len(reply.Missing))
		for _, entry := range reply.Missing {
			if !slices.Contains(keys, entry.Key) {
				keys = append(keys, entry.Key)
			}
			line := squeezed(entry.Line)
			if ruling, ok := strings.CutPrefix(entry.Key, answerKey); ok {
				line = fmt.Sprintf("departs from ruling %s: %s", ruling, line)
			}
			broken = append(broken, line)
		}
		if reply.picks != "" {
			broken = append(broken, reply.picks)
		}
		lines = append(lines, gradeLine{text: fmt.Sprintf("%s %s %s %s", fidelityMissing, head, strings.Join(keys, ","), strings.Join(broken, "; "))})
		if strings.TrimSpace(reply.Reader) != "" {
			lines = append(lines, gradeLine{reader, spec.Readers})
		}
	default:
		line := gradeLine{reader, spec.Readers}
		if _, touched := s.touchedOldApp(); touched {
			line = gradeLine{text: fmt.Sprintf("%s %s %s: %s", fidelityReader, head, oldAppPending, squeezed(reply.Reader))}
		}
		lines = append(lines, line)
	}
	for _, key := range reply.Touches {
		lines = append(lines, gradeLine{text: "touches " + key})
	}
	switch {
	case spec.Since == "":
	case reply.Behavior == "same":
		lines = append(lines, gradeLine{text: fmt.Sprintf("%s same %s since %s", behaviorLine, head, spec.Since)})
	default:
		lines = append(lines, gradeLine{text: fmt.Sprintf("%s changed %s since %s: %s", behaviorLine, head, spec.Since, squeezed(reply.WhatChanged))})
	}
	if line, touched := s.touchedOldApp(); touched {
		return append(lines, gradeLine{text: line})
	}
	grade := reply.OldApp.Grade
	if grade == "changes" {
		grade = "CHANGES"
	}
	return append(lines, gradeLine{text: fmt.Sprintf("old-app: %s %s baseline %s; %s", grade, short(head, 7), s.baseline(), squeezed(reply.OldApp.Why))})
}

func (s *fidelityPassState) writeGradeBody(body string, lines []gradeLine, reply fidelityReply) error {
	var shown bytes.Buffer
	encoder := json.NewEncoder(&shown)
	encoder.SetEscapeHTML(false)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(reply); err != nil {
		return err
	}
	text := fmt.Sprintf("%s\nchecked %s names %s\n\n%s", joinedText(lines), strings.Join(reply.Checked, ","), strings.Join(reply.Names, "|"), shown.String())
	return os.WriteFile(body, []byte(text), 0o600)
}

func (s *fidelityPassState) gradeID(index int) string {
	id := fmt.Sprintf("fidelity:%s:%s:%d", s.job.pull(), s.job.Head, index)
	if s.job.Regrade > 0 {
		return fmt.Sprintf("%s:regrade-%d", id, s.job.Regrade)
	}
	return id
}

func (s *fidelityPassState) post(lines []gradeLine, body string) ([]string, error) {
	spec := s.job
	posted := make([]string, 0, len(lines))
	for index, line := range lines {
		argv := []string{"cci", "post", "--drive", spec.Drive, "--lane", FidelityLane, "--kind", "review", "--pr", spec.pull(), "--id", s.gradeID(index), "--path", body}
		if to := s.audience(line.also...); to != "" {
			argv = append(argv, "--to", to)
		}
		receipt, err := s.posted(append(argv, "--text", clipped(line.text), "--json"))
		if err != nil {
			return nil, err
		}
		posted = append(posted, receipt)
	}
	return posted, nil
}
