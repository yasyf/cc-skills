package update

import (
	"fmt"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"
	_ "time/tzdata"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/asks"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/plans"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	root          = "root"
	nobody        = "**nobody**"
	nothingBlocks = "nothing recorded"
	noneRecorded  = "None recorded"
	unregistered  = "unregistered"
	retired       = "retired"
	textCells     = 120
	cellChars     = 100
	titleChars    = 60
	heardWithin   = 2 * time.Hour
	quietAfter    = 24 * time.Hour
	queueLabelled = "labelled for the queue"
)

var (
	pacificZone   = must(time.LoadLocation("America/Los_Angeles"))
	selves        = []string{root, "main", "supervisor"}
	coordinators  = []string{root, "main", "all", "desk-runner", "stall-watch", "supervisor"}
	posting       = []string{string(store.LaneLaunching), string(store.LaneDesired), string(store.LaneHeld), unregistered}
	laneName      = regexp.MustCompile(`^[a-z][\w.-]*$`)
	incidentRole  = regexp.MustCompile(`-(?:fix|evidence|ship|monitor|backup|diag)(?:-\d+)?$`)
	incidentSlug  = regexp.MustCompile(`\bINCIDENT (?:OPEN |REOPENED )?([a-z][a-z0-9-]+[a-z0-9])\b`)
	incidentClose = regexp.MustCompile(`\bINCIDENT (?:\S+ CLOSED|CLOSED \S+)\b|\bis a closed incident\b`)
	parenthetical = regexp.MustCompile(`\([^)]*\)`)
	stepNumber    = regexp.MustCompile(`^\d+[a-z]?\.\s+`)
	unitLabel     = regexp.MustCompile(`^[A-Z]\d+[a-z]?:\s+`)
	unitCode      = regexp.MustCompile(`\b[A-Z]\d+(?:[.-][A-Z]?\d+)*[a-z]?\b`)
	suffixedCode  = regexp.MustCompile(`\b[A-Z]\d+(?:[a-z]|(?:[.-][A-Z]?\d+)+[a-z]?)\b`)
	planCite      = regexp.MustCompile(`\b(?:rev|row) \d|\b[a-z]\d\b`)
	loneLetter    = regexp.MustCompile(`^[A-Z]$`)
	hexWord       = regexp.MustCompile(`\b[0-9a-f]{7,40}\b`)
	spaceBefore   = regexp.MustCompile(`\s+([,;:])`)
	rawStep       = regexp.MustCompile(`^(?:\d|[A-Z]\d)`)
	askWrong      = map[asks.State]string{asks.Lost: "no PR and no record carry it", asks.Stale: "no progress past its due time", asks.LandedNotLive: "landed but not live for users"}
	prWrong       = map[string]string{"red": "its head is red", "refused": "the landing gate refused its head", "held": "a hold stops it"}
	prWorkKinds   = []string{"head", "opened"}
	stepWorkKinds = []string{"head", "opened", "report", "done", "handoff"}
)

func must[T any](value T, err error) T {
	if err != nil {
		panic(err)
	}
	return value
}

type Lane struct {
	Name        string
	State       string
	Heard       time.Time
	PRs         []int
	Step        string
	Standing    string
	Brief       string
	RetiredBy   string
	Predecessor string
	Deliverable string
}

type Record struct {
	Seq      int64
	Drive    string
	Lane     string
	Kind     string
	Text     string
	Status   string
	At       time.Time
	To       []string
	PRs      []string
	Topic    string
	Board    string
	Attrs    map[string]string
	Re       int64
	Resolves []int64
}

func stepIsDone(step *plans.Step) bool {
	return len(step.Tasks) > 0 && step.Count(plans.Done) == len(step.Tasks)
}

type Row struct {
	Name     string
	Delivers string
	State    string
	Who      string
	Blocks   string
	Unblock  string
	Wrong    string
	Done     bool
}

func (r Row) unowned() bool {
	return r.Who == nobody && !r.Done
}

func pacific(at time.Time) string {
	return at.In(pacificZone).Format("3:04 PM")
}

func pacificText(at time.Time) string {
	if at.IsZero() {
		return "an unrecorded time"
	}
	return pacific(at)
}

func clip(text string) string {
	return clipTo(text, textCells)
}

func clipTo(text string, limit int) string {
	text = strings.Join(strings.Fields(text), " ")
	runes := []rune(text)
	if len(runes) <= limit {
		return text
	}
	return strings.TrimRight(string(runes[:limit-1]), " \t\n") + "…"
}

func cell(text string) string {
	return strings.ReplaceAll(clipTo(text, cellChars), "|", "/")
}

func opening(text string) string {
	runes := []rune(clip(text))
	return string(runes[:min(titleChars, len(runes))])
}

func bySeq(records []Record) []Record {
	ordered := slices.Clone(records)
	slices.SortStableFunc(ordered, func(one, other Record) int { return int(one.Seq - other.Seq) })
	return ordered
}

func unique(names []string) []string {
	seen := map[string]bool{}
	return slices.DeleteFunc(slices.Clone(names), func(name string) bool {
		duplicate := seen[name]
		seen[name] = true
		return duplicate
	})
}

func working(lanes []Lane, now time.Time) []Lane {
	var out []Lane
	for _, lane := range lanes {
		if lane.Standing != "" && lane.State != retired {
			out = append(out, lane)
			continue
		}
		if slices.Contains(posting, lane.State) && !slices.Contains(selves, lane.Name) && laneName.MatchString(lane.Name) && !lane.Heard.IsZero() && now.Sub(lane.Heard) < quietAfter {
			out = append(out, lane)
		}
	}
	return out
}

func stepOf(lane Lane) string {
	if lane.Step == "all" {
		return ""
	}
	return lane.Step
}

func onStep(lane Lane, step *plans.Step) bool {
	named := stepOf(lane)
	return named != "" && (named == step.Number || strings.HasPrefix(named, step.Number+"."))
}

func pieces(plan *plans.Plan) []string {
	if slices.ContainsFunc(plan.Steps, func(step *plans.Step) bool { return step.Number != plans.Unmapped }) {
		return nil
	}
	_, listed, _ := strings.Cut(plan.Title, ": ")
	listed, _, _ = strings.Cut(listed, ";")
	var out []string
	for _, piece := range strings.Split(listed, ",") {
		if piece = strings.TrimSpace(parenthetical.ReplaceAllString(piece, "")); piece != "" {
			out = append(out, piece)
		}
	}
	return out
}

func isSHA(word string) bool {
	return strings.ContainsAny(word, "0123456789")
}

func hasSHA(text string) bool {
	return slices.ContainsFunc(hexWord.FindAllString(text, -1), isSHA)
}

func plainTitle(text string) string {
	text = unitLabel.ReplaceAllString(stepNumber.ReplaceAllString(text, ""), "")
	text = parenthetical.ReplaceAllStringFunc(text, func(found string) string {
		inner := found[1 : len(found)-1]
		if loneLetter.MatchString(inner) || unitCode.MatchString(inner) || planCite.MatchString(inner) || hasSHA(inner) {
			return ""
		}
		return found
	})
	clauses := strings.Split(text, "; ")
	kept := slices.DeleteFunc(clauses[1:], func(clause string) bool { return suffixedCode.MatchString(clause) || hasSHA(clause) })
	text = suffixedCode.ReplaceAllString(strings.Join(slices.Insert(kept, 0, clauses[0]), "; "), "")
	text = hexWord.ReplaceAllStringFunc(text, func(word string) string {
		if isSHA(word) {
			return ""
		}
		return word
	})
	return strings.Trim(spaceBefore.ReplaceAllString(strings.Join(strings.Fields(text), " "), "$1"), " ,;:")
}

func pieceDelivers(title, piece string) string {
	name, _, _ := strings.Cut(title, ": ")
	delivers := fmt.Sprintf("the %s piece of %s", piece, name)
	if note := regexp.MustCompile(regexp.QuoteMeta(piece) + ` \(([^)]*)\)`).FindStringSubmatch(title); note != nil {
		return delivers + ": " + note[1]
	}
	return delivers
}

func workstreamOf(lane Lane) string {
	step := stepOf(lane)
	if rawStep.MatchString(step) {
		return ""
	}
	return strings.ReplaceAll(step, "-", " ")
}

func onPiece(lane Lane, piece string) bool {
	word := strings.ToLower(piece[strings.LastIndex(piece, "-")+1:])
	pattern := regexp.MustCompile(`(?:^|[^a-z])` + regexp.QuoteMeta(word))
	return slices.ContainsFunc([]string{lane.Name, lane.Step, lane.Deliverable}, func(text string) bool {
		return pattern.MatchString(strings.ToLower(text))
	})
}

func incidentName(record Record) string {
	if slug := incidentSlug.FindStringSubmatch(record.Text); slug != nil {
		return slug[1]
	}
	if !slices.Contains(coordinators, record.Lane) {
		return record.Lane
	}
	for _, to := range record.To {
		address, _, _ := strings.Cut(to, "@")
		if !slices.Contains(coordinators, address) {
			return address
		}
	}
	return record.Lane
}

func closesIncident(record Record) bool {
	return record.Lane == root && incidentClose.MatchString(record.Text)
}

func openIncidents(records []Record) map[int64]bool {
	closed := map[string]int64{}
	for _, record := range records {
		if closesIncident(record) {
			for _, lane := range record.To {
				closed[client.Stem(lane)] = max(closed[client.Stem(lane)], record.Seq)
			}
		}
	}
	open := map[int64]bool{}
	for _, record := range records {
		if record.Kind != "incident" || record.Status != "open" || closesIncident(record) {
			continue
		}
		lanes := slices.DeleteFunc(append([]string{record.Lane}, record.To...), func(lane string) bool { return lane == root })
		if len(lanes) == 0 || slices.ContainsFunc(lanes, func(lane string) bool { return closed[client.Stem(lane)] <= record.Seq }) {
			open[record.Seq] = true
		}
	}
	return open
}

type view struct {
	lanes   []Lane
	byLane  map[string]Lane
	latest  map[string]Record
	waits   map[string]Record
	records []Record
	history []Record
	retro   []Retro
	rulings *strings.Replacer
	now     time.Time
}

func newView(lanes []Lane, records, waits []Record, now time.Time) view {
	v := view{lanes: lanes, byLane: map[string]Lane{}, latest: map[string]Record{}, waits: map[string]Record{}, records: bySeq(records), rulings: strings.NewReplacer(), now: now}
	v.history = v.records
	for _, lane := range lanes {
		v.byLane[lane.Name] = lane
	}
	for _, record := range v.records {
		v.latest[record.Lane] = record
	}
	for _, wait := range waits {
		v.waits[wait.Lane] = wait
	}
	return v
}

func (v view) live(name string) bool {
	lane, found := v.byLane[name]
	return found && !slices.Contains(selves, name) && slices.Contains(posting, lane.State) && !lane.Heard.IsZero() && v.now.Sub(lane.Heard) < heardWithin
}

func (v view) liveHead(name string) bool {
	lane, found := v.byLane[name]
	return found && v.live(v.headOf(lane).Name)
}

func (v view) newestNaming(kinds []string, pattern *regexp.Regexp, pr string) string {
	for _, record := range slices.Backward(v.records) {
		if !slices.Contains(kinds, record.Kind) || slices.Contains(coordinators, record.Lane) {
			continue
		}
		if pattern.MatchString(record.Text) || (pr != "" && slices.ContainsFunc(record.PRs, func(named string) bool { return named == pr || strings.HasSuffix(named, "#"+pr) })) {
			return record.Lane
		}
	}
	return ""
}

func (v view) ownerOfPR(number int) string {
	pr := strconv.Itoa(number)
	return v.newestNaming(prWorkKinds, regexp.MustCompile(`#`+pr+`\b`), pr)
}

func (v view) ownerOfStep(number string) string {
	last, _ := v.lastOnStep(number)
	return last.Lane
}

func (v view) lastOnStep(number string) (Record, bool) {
	pattern := regexp.MustCompile(`(?i)\bstep[ =]` + regexp.QuoteMeta(number) + `(?:\.\d+)*\b`)
	for _, record := range slices.Backward(v.history) {
		if slices.Contains(stepWorkKinds, record.Kind) && !slices.Contains(coordinators, record.Lane) && v.byLane[record.Lane].Standing == "" && pattern.MatchString(record.Text) {
			return record, true
		}
	}
	return Record{}, false
}

type work struct {
	who     string
	blocks  string
	unblock string
	wrong   string
}

func (v view) workOf(lanes []string) work {
	lanes = unique(lanes)
	on := slices.DeleteFunc(slices.Clone(lanes), func(lane string) bool { return !v.live(lane) })
	var blocks, unblock, wrong []string
	for _, lane := range on {
		if wait, found := v.waits[lane]; found {
			blocks = append(blocks, fmt.Sprintf("%s waits on the coordinator's answer to %s #%d since %s: %s", lane, wait.Kind, wait.Seq, pacific(wait.At), clip(wait.Text)))
		}
		if last, found := v.latest[lane]; found {
			unblock = append(unblock, fmt.Sprintf("%s: %s %s: %s; next record due by %s", lane, last.Kind, pacific(last.At), clip(last.Text), pacific(v.byLane[lane].Heard.Add(store.StaleAfter))))
		}
	}
	if len(unblock) == 0 {
		if len(on) == 0 {
			unblock = []string{"nobody is on it, so the coordinator starts a lane on it this turn"}
		}
		for _, lane := range on {
			unblock = append(unblock, lane+": no record in a day")
		}
	}
	for _, row := range v.retro {
		if slices.Contains(lanes, row.Owner) && row.State != retroLive {
			wrong = append(wrong, row.line())
		}
	}
	for _, lane := range lanes {
		if row, found := v.byLane[lane]; found && !slices.Contains(selves, lane) && !v.live(lane) {
			wrong = append(wrong, fmt.Sprintf("%s quiet since %s", lane, pacificText(row.Heard)))
		}
	}
	for _, lane := range on {
		if predecessor := v.byLane[lane].Predecessor; predecessor != "" {
			wrong = append(wrong, fmt.Sprintf("%s took over from %s", lane, predecessor))
		}
	}
	return work{orWord(strings.Join(on, ", "), nobody), orWord(strings.Join(blocks, "; "), nothingBlocks), strings.Join(unblock, "; "), orWord(strings.Join(wrong, "; "), noneRecorded)}
}

func orWord(text, word string) string {
	if text != "" {
		return text
	}
	return word
}

func (v view) headOf(lane Lane) Lane {
	seen := map[string]bool{lane.Name: true}
	for lane.State == retired {
		after, found := v.byLane[lane.RetiredBy]
		if !found || seen[after.Name] {
			break
		}
		lane = after
		seen[lane.Name] = true
	}
	return lane
}

func (v view) dutyRows() []Row {
	heads := map[string]string{}
	var names []string
	for _, lane := range v.lanes {
		if lane.Standing == "" {
			continue
		}
		head := v.headOf(lane).Name
		if _, seen := heads[head]; !seen {
			names = append(names, head)
		}
		heads[head] = lane.Standing
	}
	slices.SortStableFunc(names, func(one, other string) int { return strings.Compare(heads[one], heads[other]) })
	out := make([]Row, 0, len(names))
	for _, name := range names {
		head, duty := v.byLane[name], heads[name]
		w := v.workOf([]string{name})
		if head.State == retired {
			w.blocks = fmt.Sprintf("%s retired by %s with no successor", name, orWord(head.RetiredBy, "nobody"))
			w.unblock = fmt.Sprintf("the coordinator launches its successor from %s this turn", orWord(head.Brief, "its brief"))
		}
		out = append(out, Row{duty, duty, fmt.Sprintf("%s %s, heard %s", name, head.State, pacificText(head.Heard)), w.who, w.blocks, w.unblock, w.wrong, false})
	}
	return out
}

func (v view) stepRow(name, delivers string, step *plans.Step, lanes []string) Row {
	w := v.workOf(lanes)
	state := fmt.Sprintf("no tasks; %d lanes name it", len(lanes))
	if len(step.Tasks) > 0 {
		state = fmt.Sprintf("%d of %d tasks done, %d in progress, %d blocked", step.Count(plans.Done), len(step.Tasks), step.Count(plans.InProgress), step.Count(plans.Blocked))
	}
	switch {
	case w.who == nobody && step.Count(plans.InProgress) > 0:
		state = fmt.Sprintf("%d of %d tasks done, %d stale with no lane on them, %d blocked; %s", step.Count(plans.Done), len(step.Tasks), step.Count(plans.InProgress), step.Count(plans.Blocked), v.lastWorked(step.Number))
	case w.who == nobody && step.Number != "" && !stepIsDone(step):
		state += fmt.Sprintf("; %s, %s", noLiveLane, v.lastWorked(step.Number))
	}
	var blocks []string
	for _, task := range step.Tasks {
		if task.State == plans.Blocked && task.Waiting != "" {
			blocks = append(blocks, fmt.Sprintf("task %s waits on %s", clip(v.rulings.Replace(task.Title)), task.Waiting))
		}
	}
	if w.blocks != nothingBlocks || len(blocks) == 0 {
		blocks = append(blocks, w.blocks)
	}
	return Row{name, delivers, state, w.who, strings.Join(blocks, "; "), w.unblock, w.wrong, stepIsDone(step)}
}

func (v view) lastWorked(number string) string {
	last, found := v.lastOnStep(number)
	if !found {
		return "no lane record names it"
	}
	return fmt.Sprintf("last worked by %s, %s %s", last.Lane, last.Kind, stamp(last.At, v.now))
}

func (v view) laneRow(lane Lane) Row {
	w := v.workOf([]string{lane.Name})
	state := fmt.Sprintf("%s, heard %s", lane.State, pacificText(lane.Heard))
	if last, found := v.latest[lane.Name]; found {
		state = fmt.Sprintf("%s, last %s %s", lane.State, last.Kind, pacific(last.At))
	}
	delivers := orWord(lane.Deliverable, "no Deliverable line in its brief")
	if len(lane.PRs) > 0 {
		numbers := make([]string, len(lane.PRs))
		for i, pr := range lane.PRs {
			numbers[i] = "#" + strconv.Itoa(pr)
		}
		delivers += " (" + strings.Join(numbers, " ") + ")"
	}
	return Row{lane.Name, delivers, state, w.who, w.blocks, w.unblock, w.wrong, false}
}

func (v view) incidentRows(records []Record, drive string) []Row {
	found := v.incidents(records, drive)
	out := make([]Row, len(found))
	for i, incident := range found {
		word := "closed"
		if incident.Open {
			word = "open"
		}
		w := incident.work
		out[i] = Row{incident.Name, clip(incident.Fired.Text), fmt.Sprintf("%s since %s; newest %s: %s", word, pacific(incident.Fired.At), pacific(incident.Newest.At), clip(incident.Newest.Text)), w.who, w.blocks, w.unblock, w.wrong, !incident.Open}
	}
	return out
}

func (v view) releaseRows(records []Record, drive string) []Row {
	groups := map[string][]Record{}
	var names []string
	for _, record := range records {
		if record.Kind != "release" || record.Drive != drive {
			continue
		}
		if _, seen := groups[record.Lane]; !seen {
			names = append(names, record.Lane)
		}
		groups[record.Lane] = append(groups[record.Lane], record)
	}
	out := make([]Row, 0, len(names))
	for _, name := range names {
		group := groups[name]
		w := v.workOf([]string{name})
		newest := group[len(group)-1]
		out = append(out, Row{name, clip(group[0].Text), fmt.Sprintf("%d release records since midnight; newest %s: %s", len(group), pacific(newest.At), clip(newest.Text)), w.who, w.blocks, w.unblock, w.wrong, !v.live(name)})
	}
	return out
}

func (v view) askRow(ask asks.Graded) Row {
	w := v.workOf([]string{ask.Lane})
	if words, found := askWrong[ask.State]; found {
		w.wrong = fmt.Sprintf("%s: %s", ask.State, words)
	}
	name, rest, found := strings.Cut(ask.Text, ": ")
	if !found {
		name, rest = opening(ask.Text), ask.Text
	}
	return Row{name, clip(rest), string(ask.State), w.who, w.blocks, w.unblock, w.wrong, ask.State == asks.Live}
}

func (v view) prRow(row store.ReviewRow, held map[string]int64) Row {
	state := prWord(row)
	w := v.workOf(slices.DeleteFunc([]string{v.ownerOfPR(row.Number), row.Lane}, func(lane string) bool { return lane == "" }))
	number := strconv.Itoa(row.Number)
	switch seq, isHeld := held[number]; {
	case isHeld:
		w.blocks = fmt.Sprintf("held by cci #%d", seq)
	case state != queueLabelled:
		w.blocks = "waits: " + row.WaitsOn
	}
	if words, found := prWrong[state]; found {
		w.wrong = words
	}
	return Row{"#" + number, clip(row.Title), state, w.who, w.blocks, w.unblock, w.wrong, state == queueLabelled}
}
