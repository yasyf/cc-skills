package grader

import (
	"cmp"
	"context"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"
)

const (
	FinderLane    = "finder-desk"
	CancelledFile = "cancelled"
	findingsState = "findings.json"
	wholeRead     = "over whole "
	censusPrefix  = "census:"
	followUpWord  = "follow-up:"
	bigSlice      = 150
	solModel      = "sol"
	solEffort     = "medium"
	outputCap     = "max_output_tokens"
	finderTimeout = 5400 * time.Second
	finderPass    = "ok"
	finderFail    = "major"
	areaShown     = 3
	secondFix     = "second fix in this area: the owner's rule needs a class fix, name the invariant and the single owner"
	classFixOwed  = "a class fix in this area, naming the invariant and the single owner, since this port repeats an earlier fix"
	scopedStep    = "2. This head follows an earlier reviewed head, so it gets no row pass. Name an owner row\n" +
		"   only when a hunk this pass reads breaks it, as one major finding whose defect starts\n" +
		`   "row <key> missing:".`
)

//go:embed briefs/finder-desk-brief.md
var finderBrief string

//go:embed briefs/finder-desk-rows.md
var finderRows string

var (
	majors       = []string{"blocker", "major"}
	fixGlyphs    = []string{"🐛", "🚑", "🩹"}
	carriedFrom  = regexp.MustCompile(`\bcarried from #(\d+)`)
	citedFile    = regexp.MustCompile(`([\w.@+-]+(?:/[\w.@+-]+)*\.\w+):\d+`)
	counted      = regexp.MustCompile(`\brows checked:\s*(\d+)`)
	lists        = regexp.MustCompile(`\b(rows met|rows checked|n/a|pending|tests):`)
	classNamed   = regexp.MustCompile(`(?im)^.*\bclass\b.*\b(?:remov\w*|impossible|eliminat\w*|cannot recur|can no longer)\b|\bdeleted at cutover\b|\binvariant\b.*\b(?:single|one|sole) owner\b|\b(?:single|one|sole) owner of\b`)
	landedCommit = regexp.MustCompile(`^@([0-9a-f]{40})$`)
	opensAsPort  = regexp.MustCompile(`(?i)^\s*port of #\d+\b`)
	finderSays   = regexp.MustCompile(`(?i)\bfinder(?:\s*\[\d+\])?:?\s+(ok|lgtm|major|issues)\b`)
	finderMajor  = regexp.MustCompile(`(?i)\b[1-9]\d* majors?\b`)
	finderClean  = regexp.MustCompile(`(?i)\bno (?:majors?|findings)\b|\b0 majors?\b|\bok\b`)
)

type Record struct {
	Seq  int64  `json:"seq"`
	Lane string `json:"lane"`
	Head string `json:"head"`
	Text string `json:"text"`
	Path string `json:"path"`
}

type FinderJob struct {
	Job
	Since    string   `json:"since"`
	Prior    *Record  `json:"prior"`
	Previous string   `json:"previous"`
	History  []Record `json:"history"`
	Ruled    []Record `json:"ruled"`
	Full     bool     `json:"full"`
}

type finding struct {
	Severity string `json:"severity"`
	Cite     string `json:"cite"`
	Defect   string `json:"defect"`
}

type finderReply struct {
	Swept    string    `json:"swept"`
	Findings []finding `json:"findings"`
}

type FinderOutcome struct {
	Skipped  string `json:"skipped,omitempty"`
	Ungraded bool   `json:"ungraded,omitempty"`
	Verdict  string `json:"verdict,omitempty"`
	Record   string `json:"record,omitempty"`
	Posted   string `json:"posted,omitempty"`
	Census   string `json:"census,omitempty"`
	Regrade  string `json:"regrade,omitempty"`
}

type superseded struct {
	why string
}

func (s *superseded) Error() string {
	return s.why
}

type finderPassState struct {
	*pass
	job *FinderJob
}

func Finder(ctx context.Context, dir string, deps Deps) error {
	outcome, err := runFinder(ctx, dir, deps)
	var cancelled *superseded
	if errors.As(err, &cancelled) {
		outcome, err = FinderOutcome{Skipped: cancelled.why}, nil
	}
	return settle(dir, outcome, err)
}

func runFinder(ctx context.Context, dir string, deps Deps) (FinderOutcome, error) {
	data, err := os.ReadFile(filepath.Join(dir, JobFile))
	if err != nil {
		return FinderOutcome{}, err
	}
	var spec FinderJob
	if err := json.Unmarshal(data, &spec); err != nil {
		return FinderOutcome{}, err
	}
	state := &finderPassState{&pass{Deps: deps, ctx: ctx, dir: dir, job: &spec.Job}, &spec}
	return state.grade()
}

func (s *finderPassState) earlierHeads() []string {
	var heads []string
	prior := ""
	if s.job.Prior != nil {
		prior = s.job.Prior.Head
	}
	for _, name := range []string{s.job.Since, prior, s.job.Previous} {
		if name != "" {
			heads = append(heads, name)
		}
	}
	return heads
}

func (s *finderPassState) reviewed() (string, error) {
	for _, name := range s.earlierHeads() {
		full, err := s.fetched(name)
		if err != nil {
			return "", err
		}
		if full != "" {
			return full, nil
		}
	}
	return "", nil
}

func (s *finderPassState) grade() (FinderOutcome, error) {
	spec := s.job
	if err := spec.inputsArrived(); err != nil {
		return FinderOutcome{}, err
	}
	pull := spec.pull()
	plan := s.planRead()
	view, err := s.viewPR()
	if err != nil {
		return FinderOutcome{}, err
	}
	if view.State == "CLOSED" {
		return FinderOutcome{Skipped: pull + " is closed"}, nil
	}
	if !strings.HasPrefix(view.HeadRefOid, spec.Head) {
		return FinderOutcome{Skipped: fmt.Sprintf("%s moved to %s", pull, short(view.HeadRefOid, 12))}, nil
	}
	spec.Head = view.HeadRefOid
	head := spec.Head
	if spec.Prior != nil && NamesHead(spec.Prior.Head, head) {
		return s.replayed(view, head, nil)
	}
	base, trunk, err := s.checkedOut(view)
	if err != nil {
		return FinderOutcome{}, err
	}
	var slicing *future[string]
	if len(s.earlierHeads()) == 0 {
		slicing = s.sliceRows(head, view.BaseRefName)
	}
	last, err := s.reviewed()
	if err != nil {
		return FinderOutcome{}, err
	}
	owning := async(func() ([]string, error) { return s.beyond([]string{base, trunk}, head) })
	var earlier []string
	if last != "" {
		if earlier, err = s.earlierOwn(base, trunk, last); err != nil {
			return FinderOutcome{}, err
		}
	}
	own, err := owning.result()
	if err != nil {
		return FinderOutcome{}, err
	}
	if len(own) == 0 {
		return FinderOutcome{Skipped: fmt.Sprintf("%s at %s carries no commit %s lacks", pull, short(head, 12), view.BaseRefName)}, nil
	}
	if len(earlier) == 0 {
		last = ""
	}
	pairs, changed := "", []pair(nil)
	if last != "" {
		if pairs, changed, err = s.rangePairs(base, last, earlier, own); err != nil {
			return FinderOutcome{}, err
		}
		if len(changed) == 0 {
			gone, kept, err := s.citesAtHead()
			if err != nil {
				return FinderOutcome{}, err
			}
			if len(gone) == 0 || len(kept) == 0 {
				return s.replayed(view, last, gone)
			}
			last = ""
		}
	}
	judged := ""
	if last != "" && spec.Prior != nil && NamesHead(spec.Prior.Head, last) && FinderGrade(spec.Prior.Text) == finderPass {
		answer, err := s.regraded(view, finderMeans, *spec.Prior, last, pairs, changed)
		if err != nil {
			return FinderOutcome{}, err
		}
		if answer.carries() {
			return s.judgedCarry(last, pairs, answer)
		}
		judged = answer.String()
	}
	diff, err := s.touched(own[0]+"^", head)
	if err != nil {
		return FinderOutcome{}, err
	}
	rowsPath, keys := "", []string(nil)
	if last == "" {
		rowsPath = filepath.Join(s.dir, rowsFile)
		if slicing == nil {
			slicing = s.sliceRows(head, view.BaseRefName)
		}
		rows, err := slicing.result()
		if err != nil {
			return FinderOutcome{}, err
		}
		if err := os.WriteFile(rowsPath, []byte(rows), 0o600); err != nil {
			return FinderOutcome{}, err
		}
		keys = rowKeys(rows)
	}
	if len(keys) > bigSlice {
		spec.Model = solModel
	}
	planned, err := planOnThisBox(plan)
	if err != nil {
		return FinderOutcome{}, err
	}
	question := s.prompt(view, base, own, last, pairs, changed, rowsPath, planned, diff)
	questionPath := filepath.Join(s.dir, "question-1.md")
	if err := os.WriteFile(questionPath, []byte(question), 0o600); err != nil {
		return FinderOutcome{}, err
	}
	if said, err := os.ReadFile(filepath.Join(s.dir, CancelledFile)); err == nil {
		return FinderOutcome{Skipped: fmt.Sprintf("%s at %s was superseded by %s before codex read it", pull, short(head, 12), short(strings.TrimSpace(string(said)), 12))}, nil
	}
	reply, parsed, err := s.askUntilSound(questionPath, question, keys)
	if err != nil {
		return FinderOutcome{}, err
	}
	now, err := s.viewPR()
	if err != nil {
		return FinderOutcome{}, err
	}
	if now.HeadRefOid != head {
		return FinderOutcome{Skipped: fmt.Sprintf("%s moved to %s while codex read %s", pull, short(now.HeadRefOid, 12), short(head, 12))}, nil
	}
	repeat, err := s.secondFix(view, diff)
	if err != nil {
		return FinderOutcome{}, err
	}
	if repeat != nil {
		parsed.Findings = append(parsed.Findings, *repeat)
	}
	owed, graded := followUpsOwed(view.Body, parsed.Findings)
	findings, census := triage(graded, diff)
	open, settled := s.openFindings(findings)
	text := s.recordText(own, last, open)
	if len(settled) > 0 {
		text += "; " + settledByPicks(settled)
	}
	if len(owed) > 0 {
		text += "; " + followUpsLine(owed)
	}
	body := filepath.Join(s.dir, bodyFile)
	if err := os.WriteFile(body, []byte(text+"\n\n"+strings.TrimSpace(reply)+"\n"), 0o600); err != nil {
		return FinderOutcome{}, err
	}
	posted, err := s.post(text, body)
	if err != nil {
		return FinderOutcome{}, err
	}
	outcome := FinderOutcome{Verdict: strings.Fields(text)[1], Record: text, Posted: posted, Regrade: judged}
	if len(census) > 0 {
		if outcome.Census, err = s.recordCensus(census); err != nil {
			return FinderOutcome{}, err
		}
	}
	return outcome, nil
}

func (s *finderPassState) citesAtHead() (gone, kept []string, err error) {
	from := s.job.Prior
	if from == nil || FinderGrade(from.Text) != finderFail || s.settledBy(from) != 0 {
		return nil, nil, nil
	}
	var cited []string
	for _, found := range citedFile.FindAllStringSubmatch(s.originOf(from).Text, -1) {
		if !slices.Contains(cited, found[1]) {
			cited = append(cited, found[1])
		}
	}
	if len(cited) == 0 {
		return nil, nil, nil
	}
	listed, err := s.git(append([]string{"ls-tree", "--name-only", s.job.Head, "--"}, cited...)...)
	if err != nil {
		return nil, nil, err
	}
	present := strings.Fields(listed)
	for _, file := range cited {
		if slices.Contains(present, file) {
			kept = append(kept, file)
		} else {
			gone = append(gone, file)
		}
	}
	return gone, kept, nil
}

func (s *finderPassState) originOf(from *Record) *Record {
	bySeq := s.historyBySeq()
	for {
		found := carriedFrom.FindStringSubmatch(from.Text)
		if found == nil {
			return from
		}
		seq, _ := strconv.ParseInt(found[1], 10, 64)
		older, known := bySeq[seq]
		if !known {
			return from
		}
		from = older
	}
}

func (s *finderPassState) historyBySeq() map[int64]*Record {
	bySeq := make(map[int64]*Record, len(s.job.History))
	for index := range s.job.History {
		bySeq[s.job.History[index].Seq] = &s.job.History[index]
	}
	return bySeq
}

func (s *finderPassState) replayed(view pullRequest, last string, gone []string) (FinderOutcome, error) {
	spec := s.job
	head := spec.Head
	said := fmt.Sprintf("%s at %s replays every own commit of %s, patch for patch or dropping only hunks %s carries", spec.pull(), short(head, 12), short(last, 12), view.BaseRefName)
	earlier := spec.Prior
	if earlier == nil || !NamesHead(earlier.Head, last) {
		return FinderOutcome{Skipped: said, Ungraded: true}, nil
	}
	grade := FinderGrade(earlier.Text)
	text := fmt.Sprintf("finder: %s %s carried from #%d", grade, short(head, 7), earlier.Seq)
	if root := s.settledBy(earlier); grade != finderPass && root != 0 {
		text = fmt.Sprintf("finder: %s %s carried from #%d, settled by coordinator #%d", finderPass, short(head, 7), earlier.Seq, root)
	}
	if grade == finderFail && s.onlySecondFix(earlier) && opensAsPort.MatchString(view.Body) {
		_, fixes, _ := strings.Cut(s.originOf(earlier).Text, secondFix)
		text = fmt.Sprintf("finder: %s %s carried from #%d, whose one major, the second fix, stands as a follow-up owed after this port: %s%s", finderPass, short(head, 7), earlier.Seq, classFixOwed, fixes)
	}
	if grade == finderFail && s.onlySecondFix(earlier) && view.namesClassFix() {
		text = fmt.Sprintf("finder: %s %s carried from #%d, whose one major, the second fix, the PR body now answers", finderPass, short(head, 7), earlier.Seq)
	}
	if len(gone) > 0 {
		text = fmt.Sprintf("finder: %s %s carried from #%d, whose findings cite only files gone at this head: %s", finderPass, short(head, 7), earlier.Seq, strings.Join(gone, ", "))
	}
	body := filepath.Join(s.dir, bodyFile)
	if err := os.WriteFile(body, fmt.Appendf(nil, "%s\n\n%s.\n\n#%d: %s\n", text, said, earlier.Seq, earlier.Text), 0o600); err != nil {
		return FinderOutcome{}, err
	}
	posted, err := s.post(text, body)
	if err != nil {
		return FinderOutcome{}, err
	}
	return FinderOutcome{Verdict: strings.Fields(text)[1], Record: text, Posted: posted}, nil
}

func (s *finderPassState) judgedCarry(last, pairs string, answer regradeAnswer) (FinderOutcome, error) {
	earlier := s.job.Prior
	text := fmt.Sprintf("finder: %s %s carried from #%d at %s, judged unchanged in meaning by %s over its range-diff: %s", finderPass, s.job.Head, earlier.Seq, short(last, 7), regradeModel, answer.reason())
	body := filepath.Join(s.dir, bodyFile)
	if err := os.WriteFile(body, fmt.Appendf(nil, "%s\n\nA %s read of `git range-diff %s` answered %s.\n\n#%d: %s\n", text, regradeModel, pairs, answer, earlier.Seq, earlier.Text), 0o600); err != nil {
		return FinderOutcome{}, err
	}
	posted, err := s.post(text, body)
	if err != nil {
		return FinderOutcome{}, err
	}
	return FinderOutcome{Verdict: finderPass, Record: text, Posted: posted, Regrade: answer.String()}, nil
}

func (s *finderPassState) askUntilSound(questionPath, question string, keys []string) (string, finderReply, error) {
	reply, parsed, err := s.askFinder(questionPath, 1)
	if err != nil {
		return "", finderReply{}, err
	}
	gaps := finderShortfalls(keys, parsed)
	if len(gaps) == 0 {
		return reply, parsed, nil
	}
	rule := ""
	if len(keys) > 0 {
		rule = fmt.Sprintf(" Give every row in the rows file a verdict and end `swept` with `rows checked: %d`.", len(keys))
	}
	retry := filepath.Join(s.dir, "question-2.md")
	if err := os.WriteFile(retry, fmt.Appendf(nil, "%s\nYour last reply gave %s.%s\n", question, strings.Join(gaps, "; and "), rule), 0o600); err != nil {
		return "", finderReply{}, err
	}
	if reply, parsed, err = s.askFinder(retry, 2); err != nil {
		return "", finderReply{}, err
	}
	if gaps = finderShortfalls(keys, parsed); len(gaps) > 0 {
		return "", finderReply{}, &JobFailed{"two replies gave " + strings.Join(gaps, "; and ")}
	}
	return reply, parsed, nil
}

func (s *finderPassState) askFinder(question string, attempt int) (string, finderReply, error) {
	spec := s.job
	asked := func(model, lane string) (string, error) {
		return s.rateLimited(func(retry int) (string, error) {
			if said, err := os.ReadFile(filepath.Join(s.dir, CancelledFile)); err == nil {
				return "", &superseded{fmt.Sprintf("%s at %s was superseded by %s while codex retried", spec.pull(), short(spec.Head, 12), short(strings.TrimSpace(string(said)), 12))}
			}
			session := lane
			if retry > 0 {
				session = fmt.Sprintf("%s-r%d", lane, retry)
			}
			return s.ask(model, "findings", filepath.Join(s.dir, session), question, finderTimeout)
		})
	}
	out, err := asked(spec.Model, fmt.Sprintf("codex-%d", attempt))
	var exit *ExitError
	if errors.As(err, &exit) && spec.Model != solModel && strings.Contains(exit.output(), outputCap) {
		out, err = asked(solModel, fmt.Sprintf("codex-%d-%s", attempt, solModel))
	}
	if err != nil {
		return "", finderReply{}, err
	}
	reply, err := replyText(out)
	if err != nil {
		return "", finderReply{}, err
	}
	var parsed finderReply
	err = json.Unmarshal([]byte(reply), &parsed)
	return reply, parsed, err
}

func finderShortfalls(keys []string, reply finderReply) []string {
	var gaps []string
	said := counted.FindAllStringSubmatch(reply.Swept, -1)
	if len(keys) > 0 {
		last := "<none>"
		matches := false
		if len(said) > 0 {
			last = said[len(said)-1][1]
			count, err := strconv.Atoi(last)
			matches = err == nil && count == len(keys)
		}
		if !matches {
			gaps = append(gaps, fmt.Sprintf("`rows checked: %s` where the rows file holds %d rows", last, len(keys)))
		}
	}
	if wordsBefore(reply.Swept, lists) < reasonWords {
		gaps = append(gaps, reasonless)
	}
	return gaps
}

func triage(findings []finding, diff touched) ([]finding, []finding) {
	var stands, census []finding
	for _, f := range findings {
		if strings.HasPrefix(f.Defect, censusPrefix) || (slices.Contains(majors, f.Severity) && !diff.holds(f.Cite)) {
			census = append(census, f)
			continue
		}
		stands = append(stands, f)
	}
	return stands, census
}

func followUpsOwed(body string, findings []finding) ([]finding, []finding) {
	if !opensAsPort.MatchString(body) {
		return nil, findings
	}
	var owed, graded []finding
	for _, f := range findings {
		if strings.HasPrefix(f.Defect, followUpWord) {
			owed = append(owed, f)
			continue
		}
		graded = append(graded, f)
	}
	return owed, graded
}

func followUpsLine(owed []finding) string {
	named := make([]string, 0, len(owed))
	for _, f := range owed {
		named = append(named, squeezed(f.Cite+" "+strings.TrimPrefix(f.Defect, followUpWord)))
	}
	return "follow-ups owed after this port: " + strings.Join(named, "; ")
}

func (s *finderPassState) recordText(own []string, last string, findings []finding) string {
	head := s.job.Head
	read := wholeRead + short(own[0], 7) + "^.." + short(head, 7)
	if last != "" {
		read = "over delta " + short(last, 7) + ".." + short(head, 7)
	}
	var opened []string
	for _, f := range findings {
		if slices.Contains(majors, f.Severity) {
			opened = append(opened, squeezed(f.Cite+" "+f.Defect))
		}
	}
	if len(opened) == 0 {
		return fmt.Sprintf("finder: %s %s %s finder %s: no majors", finderPass, head, s.job.Model, read)
	}
	return fmt.Sprintf("finder: %s %s %d open %s: %s", finderFail, head, len(opened), read, strings.Join(opened, "; "))
}

func listed(records []Record) string {
	var text strings.Builder
	for _, r := range records {
		fmt.Fprintf(&text, "- cci #%d from %s: %s", r.Seq, r.Lane, r.Text)
		if r.Path != "" {
			fmt.Fprintf(&text, " (whole record: %s)", r.Path)
		}
		text.WriteString("\n")
	}
	return text.String()
}

func (b BugItems) itemsText() string {
	var text strings.Builder
	text.WriteString(listed(b.Items))
	for _, number := range b.UnknownItems {
		fmt.Fprintf(&text, "- item %s: no report carries this number, so it grounds nothing\n", number)
	}
	return text.String()
}

func (s *finderPassState) priorText() string {
	text := ""
	if len(s.job.History) > 0 {
		text += "The PR's earlier finder records and the lanes' answers to them, oldest first:\n" + listed(s.job.History) +
			"A finding one of them raised is closed when the head fixes it or an answer rebuts it in a way the code bears " +
			"out; never raise a closed finding again. Raise one only when a hunk this pass reads reintroduces it.\n\n"
	}
	if len(s.job.Ruled) > 0 {
		text += "The coordinator's rulings on this PR, oldest first:\n" + listed(s.job.Ruled) +
			"A point a ruling settles is closed: never raise it again, and never grade against the ruling what it decided.\n\n"
	}
	if text != "" {
		text += "A change the head makes because one of these records asked for it is planned; never grade it as unplanned or raise it as a defect of its own.\n\n"
	}
	return text
}

func scope(head string, own []string, last, pairs string, changed []pair) string {
	whole := fmt.Sprintf("`git diff %s^ %s`", own[0], head)
	if last == "" {
		return fmt.Sprintf("This pass reads the PR's whole change: %s.", whole)
	}
	return fmt.Sprintf(
		"An earlier head, %s, was reviewed. `git range-diff %s` pairs its own commits with this head's; "+
			"a `<` line is a commit the base now carries, or one this head dropped. This pass reads only what changed: for each amended commit, "+
			"only the hunks the range-diff shows for its pair (%s); each new commit whole with `git show <sha>` "+
			"(%s); and the removal of each dropped commit the base lacks, read with `git show <sha>` (%s). "+
			"The PR's whole change, for context only, is %s.",
		last, pairs, marked(changed, "!"), marked(changed, ">"), marked(changed, "<"), whole,
	)
}

func marked(changed []pair, mark string) string {
	var shas []string
	for _, c := range changed {
		if c.mark == mark {
			shas = append(shas, c.sha)
		}
	}
	if len(shas) == 0 {
		return "none"
	}
	return strings.Join(shas, " ")
}

func fill(template string, values map[string]string) string {
	pairs := make([]string, 0, 2*len(values))
	for key, value := range values {
		pairs = append(pairs, "{"+key+"}", value)
	}
	return strings.NewReplacer(pairs...).Replace(template)
}

func (s *finderPassState) prompt(view pullRequest, base string, own []string, last, pairs string, changed []pair, rows, plan string, diff touched) string {
	spec := s.job
	step := scopedStep
	if rows != "" {
		step = strings.TrimRight(fill(finderRows, map[string]string{"rows": rows}), " \t\r\n")
	}
	if plan == "" {
		plan = "none; the register names no plan"
	}
	body := strings.TrimSpace(view.Body)
	if body == "" {
		body = "(empty)"
	}
	return fill(finderBrief, map[string]string{
		"repo":      spec.Repo,
		"pr":        spec.PR,
		"head":      spec.Head,
		"base":      view.BaseRefName,
		"base_oid":  base,
		"checkout":  spec.Checkout,
		"fetch":     spec.Head + " " + base,
		"scope":     scope(spec.Head, own, last, pairs, changed),
		"prior":     s.priorText(),
		"items":     orNone(spec.itemsText()),
		"rows_step": step,
		"hunks":     diff.lines(),
		"plan":      plan,
		"body":      body,
		"rulings":   view.authorComments(),
	})
}

func (s *finderPassState) post(text, body string) (string, error) {
	spec := s.job
	id := "finder:" + spec.pull() + ":" + spec.Head
	if spec.Full {
		id += ":whole"
	}
	if spec.Prior != nil && NamesHead(spec.Prior.Head, spec.Head) {
		id += fmt.Sprintf(":after-%d", spec.Prior.Seq)
	}
	argv := []string{"cci", "post", "--drive", spec.Drive, "--lane", FinderLane, "--kind", "review", "--pr", spec.pull(), "--id", id, "--path", body}
	if to := s.audience(); to != "" {
		argv = append(argv, "--to", to)
	}
	return s.posted(append(argv, "--text", clipped(text), "--json"))
}

func (s *finderPassState) recordCensus(census []finding) (string, error) {
	spec := s.job
	path := filepath.Join(filepath.Dir(s.dir), "census-"+dated(s.Now())+".md")
	if spec.Sprite != "" {
		path = filepath.Join(s.dir, "census.md")
	}
	var lines strings.Builder
	for _, f := range census {
		fmt.Fprintf(&lines, "- %s %s %s: %s\n", spec.pull(), short(spec.Head, 12), squeezed(f.Cite), squeezed(strings.TrimPrefix(f.Defect, censusPrefix)))
	}
	file, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o600)
	if err != nil {
		return "", err
	}
	defer file.Close()
	if _, err := file.WriteString(lines.String()); err != nil {
		return "", err
	}
	text := fmt.Sprintf("finder census: %d dev-only findings on #%s appended to %s", len(census), spec.PR, path)
	argv := []string{"cci", "post", "--drive", spec.Drive, "--lane", FinderLane, "--kind", "note", "--to", s.audience(), "--pr", spec.pull(), "--id", "finder-census:" + spec.pull() + ":" + spec.Head, "--text", text, "--json"}
	if spec.Sprite != "" {
		argv = append(argv, "--path", path)
	}
	return s.posted(argv)
}

func (s *finderPassState) settledBy(from *Record) int64 {
	bySeq := s.historyBySeq()
	for current := from; current != nil; {
		if root, settled := s.job.Settled[strconv.FormatInt(current.Seq, 10)]; settled {
			return root
		}
		found := carriedFrom.FindStringSubmatch(current.Text)
		if found == nil {
			return 0
		}
		seq, _ := strconv.ParseInt(found[1], 10, 64)
		current = bySeq[seq]
	}
	return 0
}

func FinderGrade(text string) string {
	if said := finderSays.FindStringSubmatch(text); said != nil {
		switch word := strings.ToLower(said[1]); word {
		case "issues":
			return finderFail
		case "lgtm":
			return finderPass
		default:
			return word
		}
	}
	if finderMajor.MatchString(text) {
		return finderFail
	}
	if finderClean.MatchString(text) {
		return finderPass
	}
	return ""
}

func isFix(title string) bool {
	return slices.ContainsFunc(fixGlyphs, func(glyph string) bool { return strings.Contains(title, glyph) })
}

func subset(inner, outer []string) bool {
	return !slices.ContainsFunc(inner, func(path string) bool { return !slices.Contains(outer, path) })
}

func (s *finderPassState) secondFix(view pullRequest, diff touched) (*finding, error) {
	mine := diff.area()
	if !isFix(view.Title) || view.namesClassFix() || len(mine) == 0 {
		return nil, nil
	}
	landed, err := s.landedFixes()
	if err != nil {
		return nil, err
	}
	var earlier []landedFix
	for _, fix := range landed {
		if len(fix.files) > 0 && (subset(fix.files, mine) || subset(mine, fix.files)) {
			earlier = append(earlier, fix)
		}
	}
	if len(earlier) == 0 {
		return nil, nil
	}
	first := diff[slices.IndexFunc(diff, func(file fileSpans) bool { return !testFile.MatchString(file.path) })]
	shown := earlier[max(len(earlier)-areaShown, 0):]
	named := make([]string, 0, len(shown))
	for _, fix := range shown {
		files := slices.Sorted(slices.Values(fix.files))
		named = append(named, fmt.Sprintf("#%s (%s) fixed %s", fix.number, fix.title, strings.Join(files, ", ")))
	}
	defect := secondFix
	if opensAsPort.MatchString(view.Body) {
		defect = followUpWord + " " + classFixOwed
	}
	return &finding{Severity: finderFail, Cite: fmt.Sprintf("%s:%d", first.path, first.spans[0].start), Defect: defect + ". " + strings.Join(named, "; ")}, nil
}

func (v pullRequest) namesClassFix() bool {
	return classNamed.MatchString(v.Body + "\n" + v.rulingsComments())
}

func (s *finderPassState) onlySecondFix(from *Record) bool {
	text := s.originOf(from).Text
	return strings.Contains(text, " 1 open ") && strings.Contains(text, secondFix)
}

type LandedRow struct {
	Key   string `json:"key"`
	Title string `json:"title"`
	SHA   string `json:"landed_sha"`
}

type landedFix struct {
	number string
	title  string
	files  []string
}

func (s *finderPassState) landedFixes() ([]landedFix, error) {
	entry, err := driveOf(s.Config, s.job.Drive)
	if err != nil {
		return nil, err
	}
	data, err := os.ReadFile(filepath.Join(s.dir, LandedFile))
	if err != nil {
		return nil, err
	}
	var rows []LandedRow
	if err := json.Unmarshal(data, &rows); err != nil {
		return nil, err
	}
	fixes := map[string]landedFix{}
	var shas []string
	for _, row := range rows {
		repo, number, keyed := ledgerPR(row.Key, entry.Repo)
		if !keyed || repo != s.job.Repo || number == s.job.PR || !isFix(row.Title) {
			continue
		}
		if _, known := fixes[row.SHA]; !known {
			shas = append(shas, row.SHA)
		}
		fixes[row.SHA] = landedFix{number: number, title: row.Title}
	}
	if len(shas) == 0 {
		return nil, nil
	}
	log, err := s.gitFed(lines(shas), "log", "--no-walk=unsorted", "--no-renames", "--name-only", "--format=@%H", "--stdin")
	if err != nil {
		return nil, err
	}
	var landed []landedFix
	current := -1
	for line := range strings.SplitSeq(log, "\n") {
		if found := landedCommit.FindStringSubmatch(line); found != nil {
			current = -1
			if fix, known := fixes[found[1]]; known {
				landed = append(landed, fix)
				current = len(landed) - 1
			}
			continue
		}
		if path := strings.TrimSpace(line); path != "" && current >= 0 && !testFile.MatchString(path) {
			landed[current].files = append(landed[current].files, path)
		}
	}
	return landed, nil
}

func ledgerPR(key, repo string) (string, string, bool) {
	if owner, number, found := strings.Cut(key, "#"); found {
		return owner, number, true
	}
	if _, err := strconv.Atoi(key); err == nil {
		return repo, key, true
	}
	return "", "", false
}

type driveEntry struct {
	Drive    string   `json:"drive"`
	CCIDrive string   `json:"cci_drive"`
	Program  string   `json:"program"`
	StateDir string   `json:"state_dir"`
	Repo     string   `json:"repo"`
	Checkout string   `json:"checkout"`
	Ledger   string   `json:"ledger"`
	Sessions []string `json:"sessions"`
}

func (e driveEntry) cciDrive() string {
	if e.CCIDrive != "" {
		return e.CCIDrive
	}
	if e.Program != "" {
		return e.Program
	}
	return filepath.Base(e.StateDir)
}

var errNoDrive = errors.New("no drive registry entry names this drive")

func driveOf(config, drive string) (driveEntry, error) {
	paths, err := filepath.Glob(filepath.Join(config, "long-running", "drives", "*.json"))
	if err != nil {
		return driveEntry{}, err
	}
	for _, path := range paths {
		data, err := os.ReadFile(path)
		if err != nil {
			return driveEntry{}, err
		}
		var entry driveEntry
		if err := json.Unmarshal(data, &entry); err != nil {
			return driveEntry{}, fmt.Errorf("%s: %w", path, err)
		}
		if entry.cciDrive() == drive {
			return entry, nil
		}
	}
	return driveEntry{}, fmt.Errorf("%w: %s", errNoDrive, drive)
}

func DriveProgram(config, drive string) (string, error) {
	entry, err := driveOf(config, drive)
	if errors.Is(err, errNoDrive) {
		return drive, nil
	}
	if err != nil {
		return "", err
	}
	return cmp.Or(entry.Program, filepath.Base(entry.StateDir)), nil
}

func BookedDrive(config, drive string) (string, error) {
	entry, err := driveOf(config, drive)
	if errors.Is(err, errNoDrive) {
		return "", nil
	}
	if err != nil {
		return "", err
	}
	_, err = os.Stat(filepath.Join(entry.StateDir, findingsState))
	if errors.Is(err, fs.ErrNotExist) {
		return "", nil
	}
	return entry.Drive, err
}

func DriveState(config, drive string) (string, error) {
	entry, err := driveOf(config, drive)
	if errors.Is(err, errNoDrive) {
		return "", nil
	}
	return entry.StateDir, err
}

func RootSession(config, drive string) (string, error) {
	entry, err := driveOf(config, drive)
	if errors.Is(err, errNoDrive) {
		return "", nil
	}
	if err != nil || len(entry.Sessions) == 0 {
		return "", err
	}
	return entry.Sessions[len(entry.Sessions)-1], nil
}
