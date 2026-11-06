package handoff

import (
	"bytes"
	"encoding/json"
	"fmt"
	"regexp"
	"slices"
	"strings"
	"time"
	"unicode"

	"github.com/yasyf/cc-inbox/client"
)

const (
	shortID           = 7
	standingTopic     = "standing"
	registerHeading   = "## Standing owner rules"
	narrativeHeading  = "## Root narrative"
	rowsHeading       = "### Scope rows, in the owner's words"
	quotePrefix       = "  >"
	continuation      = 4
	inForce           = "in-force"
	pendingOwner      = "pending-owner"
	scopeKind         = "scope"
	gateKind          = "gate"
	goKind            = "go"
	correctionKind    = "correction"
	touchedWindow     = 24 * time.Hour
	touchedBudget     = 12_000
	unconfirmedBudget = 6_000
	ruleID            = `(?:[A-Z]{1,2}\d+(?:\.\d+)?|#\d+)`
)

var (
	standingSectionHeading = regexp.MustCompile(`(?im)^##\s+standing owner rules\b.*$`)
	ownRules               = regexp.MustCompile(`(?im)^#{2,3}\s+standing owner rules\b`)
	nextSection            = regexp.MustCompile(`(?m)^#{1,2}\s`)
	bulletID               = regexp.MustCompile("^\\s*[-*]\\s+`?(" + ruleID + ")`?\\b")
	ownerGate              = regexp.MustCompile(`(?i)\bowner(?:'|’)?s (?:word|approval|sign-?off|go|click)\b|\bowner (?:approval|sign-?off|go|click)\b|\breserved for the owner\b`)
	hexToken               = regexp.MustCompile(`\b[0-9a-f]{7,40}\b`)
	ruleLine               = regexp.MustCompile(`(?m)^\d+\.\s`)
	noRegisterDoc          = regexp.MustCompile("no `?standing-rules`? register doc")
	rowBullet              = regexp.MustCompile("^(\\s*)(?:[-*]|\\d+\\.)\\s+[*`\\[]*([a-z]+/[\\w.-]+|[0-9a-f]{7,40})\\b")
	quotedText             = regexp.MustCompile(`[“"][^”"]+[”"]`)
	lineBreaks             = strings.NewReplacer("\r\n", "\n", "\r", "\n")
)

type busRecord struct {
	Seq   int64
	Num   string
	Drive string
	Lane  string
	Kind  string
	At    time.Time
	Text  string
	Topic string
	Re    int64
	CCN   string
}

func (r busRecord) Name() string {
	return client.Record{Seq: r.Seq, Num: r.Num}.Name()
}

type standingRule struct {
	id     string
	text   string
	source string
}

type standingRules struct {
	rules      []standingRule
	superseded map[string]string
}

func readStanding(records []busRecord, drive string) standingRules {
	found := standingRules{superseded: map[string]string{}}
	kept := make([]busRecord, 0, len(records))
	for _, record := range records {
		if record.Drive != drive || record.Topic != standingTopic || (record.Kind != goKind && record.Kind != correctionKind) {
			continue
		}
		kept = append(kept, record)
		if record.Kind == correctionKind && record.Re != 0 {
			found.superseded[fmt.Sprintf("#%d", record.Re)] = fmt.Sprintf("#%d", record.Seq)
		}
	}
	for _, record := range kept {
		id := fmt.Sprintf("#%d", record.Seq)
		if _, gone := found.superseded[id]; gone {
			continue
		}
		source := "cci " + record.Name()
		if record.CCN != "" {
			source = "ccn " + record.CCN
		}
		found.rules = append(found.rules, standingRule{id, id + " " + record.Text, source})
	}
	return found
}

type register struct {
	id   string
	body string
}

type implication struct {
	N         int    `json:"n"`
	Text      string `json:"text"`
	Direction string `json:"direction"`
	State     string `json:"state"`
}

type registerRow struct {
	key          string
	kind         string
	status       string
	words        string
	said         string
	updatedAt    string
	updated      time.Time
	evidence     string
	no           string
	isNo         bool
	answers      []string
	implications []implication
	names        []string
}

type rowSet struct {
	rows    []registerRow
	failure string
	readAt  time.Time
}

func parseRows(raw []byte) ([]registerRow, error) {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	if _, err := decoder.Token(); err != nil {
		return nil, err
	}
	rows := []registerRow{}
	for decoder.More() {
		key, err := decoder.Token()
		if err != nil {
			return nil, err
		}
		var fields map[string]string
		if err := decoder.Decode(&fields); err != nil {
			return nil, fmt.Errorf("row %v: %w", key, err)
		}
		row, err := newRow(fmt.Sprint(key), fields)
		if err != nil {
			return nil, fmt.Errorf("row %v: %w", key, err)
		}
		rows = append(rows, row)
	}
	return rows, nil
}

func newRow(key string, fields map[string]string) (registerRow, error) {
	no, isNo := fields["no"]
	row := registerRow{
		key:       key,
		kind:      fields["kind"],
		status:    fields["status"],
		words:     fields["words"],
		said:      fields["said"],
		updatedAt: fields["updated_at"],
		evidence:  fields["evidence"],
		no:        no,
		isNo:      isNo,
	}
	for name, target := range map[string]any{"answer": &row.answers, "implications": &row.implications, "names": &row.names} {
		if fields[name] == "" {
			continue
		}
		if err := json.Unmarshal([]byte(fields[name]), target); err != nil {
			return row, fmt.Errorf("%s: %w", name, err)
		}
	}
	if row.updatedAt != "" {
		updated, err := time.Parse(time.RFC3339Nano, row.updatedAt)
		if err != nil {
			return row, err
		}
		row.updated = updated
	}
	return row, nil
}

func splitLines(text string) []string {
	if text == "" {
		return nil
	}
	return strings.Split(strings.TrimSuffix(lineBreaks.Replace(text), "\n"), "\n")
}

func firstRunes(text string, limit int) string {
	runes := []rune(text)
	return string(runes[:min(limit, len(runes))])
}

func flat(text string) string {
	return strings.Join(strings.Fields(text), " ")
}

func indentOf(line string) int {
	return len(line) - len(strings.TrimLeftFunc(line, unicode.IsSpace))
}

func section(body string) ([]string, bool) {
	heading := standingSectionHeading.FindStringIndex(body)
	if heading == nil {
		return nil, false
	}
	rest := body[heading[1]:]
	if next := nextSection.FindStringIndex(rest); next != nil {
		rest = rest[:next[0]]
	}
	return splitLines(strings.Trim(rest, "\n")), true
}

type carriedRule struct {
	id   string
	line string
}

func carried(lines []string) []carriedRule {
	found := []carriedRule{}
	for _, line := range lines {
		match := bulletID.FindStringSubmatch(line)
		if match == nil {
			continue
		}
		if at := slices.IndexFunc(found, func(rule carriedRule) bool { return rule.id == match[1] }); at >= 0 {
			found[at].line = line
			continue
		}
		found = append(found, carriedRule{match[1], line})
	}
	return found
}

func holdsRule(rules []carriedRule, id string) bool {
	return slices.ContainsFunc(rules, func(rule carriedRule) bool { return rule.id == id })
}

func cites(line string, live []string) bool {
	for _, token := range hexToken.FindAllString(line, -1) {
		if slices.ContainsFunc(live, func(answer string) bool { return strings.HasPrefix(answer, token) }) {
			return true
		}
	}
	return false
}

func gated(line string, live []string) bool {
	return ownerGate.MatchString(line) && !cites(line, live)
}

func ruleCount(reg *register) int {
	if reg == nil {
		return 0
	}
	return len(ruleLine.FindAllString(reg.body, -1))
}

func pointer(reg *register) string {
	return fmt.Sprintf("Register `ccn doc show %s`: %d owner-approved rules, delivered verbatim after every compaction.", reg.id[:shortID], ruleCount(reg))
}

func ruleFindings(body, previous string, reg *register) []string {
	var problems []string
	lines, found := section(body)
	if !found {
		problems = append(problems, "no `## Standing owner rules` section; regenerate the handoff with `lr handoff generate`")
	}
	if reg != nil && !slices.Contains(lines, pointer(reg)) {
		problems = append(problems, fmt.Sprintf("the standing rules section does not name register `%s`; regenerate the handoff with `lr handoff generate`", reg.id[:shortID]))
	}
	if previous == "" {
		return problems
	}
	now := carried(lines)
	before, _ := section(previous)
	for _, rule := range carried(before) {
		if !holdsRule(now, rule.id) && !strings.Contains(rule.line, "superseded by") {
			problems = append(problems, fmt.Sprintf("dropped `%s` since the previous handoff (%s); carry it, or write `- %s superseded by <id>`", rule.id, strings.TrimSpace(rule.line), rule.id))
		}
	}
	return problems
}

func citedRows(token string, rows []registerRow) []registerRow {
	var cited []registerRow
	for _, row := range rows {
		if row.words == "" {
			continue
		}
		matches := row.key == token
		if !strings.Contains(token, "/") {
			matches = slices.ContainsFunc(row.answers, func(answer string) bool {
				return strings.HasPrefix(answer, token) || strings.HasPrefix(token, answer)
			})
		}
		if matches {
			cited = append(cited, row)
		}
	}
	return cited
}

func bulletBlock(lines []string, at, indent int) string {
	block := []string{lines[at]}
	for _, line := range lines[at+1:] {
		if indentOf(line) < indent+continuation {
			break
		}
		block = append(block, line)
	}
	return strings.Join(block, " ")
}

func rowFindings(body string, rows *rowSet) []string {
	var problems []string
	if found := noRegisterDoc.FindString(body); found != "" {
		problems = append(problems, fmt.Sprintf("the handoff says \"%s\"; the register ledger holds the owner's rows, so regenerate the section with `lr handoff generate`", found))
	}
	if rows == nil || rows.failure != "" {
		return problems
	}
	lines := splitLines(body)
	for at, line := range lines {
		if strings.HasPrefix(line, quotePrefix) {
			continue
		}
		match := rowBullet.FindStringSubmatch(line)
		if match == nil {
			continue
		}
		cited := citedRows(match[2], rows.rows)
		if len(cited) == 0 {
			continue
		}
		block := bulletBlock(lines, at, len(match[1]))
		if slices.ContainsFunc(cited, func(row registerRow) bool { return strings.Contains(flat(block), flat(row.words)) }) {
			continue
		}
		shape := "cites without its words register row"
		if quotedText.MatchString(block) {
			shape = "paraphrases register row"
		}
		problems = append(problems, fmt.Sprintf("`%s` %s `%s`: %s; quote the row's words verbatim: “%s”", match[2], shape, cited[0].key, firstRunes(strings.TrimSpace(line), 200), cited[0].words))
	}
	return problems
}

func implicationLine(item implication) string {
	return fmt.Sprintf("implication %d, %s, %s: %s", item.N, item.Direction, item.State, item.Text)
}

func childLines(row registerRow) []string {
	lines := make([]string, 0, len(row.implications)+2)
	for _, item := range row.implications {
		lines = append(lines, "  - "+implicationLine(item))
	}
	evidence := row.evidence
	if evidence == "" {
		evidence = "none recorded"
	}
	return append(lines, namesLine(row), "  - evidence: "+evidence)
}

func namesLine(row registerRow) string {
	return "  - names: " + strings.Join(row.names, " | ")
}

func continuationLines(row registerRow) []string {
	words := splitLines(row.words)
	if len(words) < 2 {
		return nil
	}
	lines := make([]string, 0, len(words)-1)
	for _, line := range words[1:] {
		lines = append(lines, strings.Repeat(" ", continuation)+line)
	}
	lines[len(lines)-1] += "”"
	return lines
}

func quotedWords(row registerRow, detail string) []string {
	first := ""
	if words := splitLines(row.words); len(words) > 0 {
		first = words[0]
	}
	following := continuationLines(row)
	closing := "”"
	if len(following) > 0 {
		closing = ""
	}
	return append([]string{fmt.Sprintf("- `%s` (%s): “%s%s", row.key, detail, first, closing)}, following...)
}

func (s *rowSet) current() []registerRow {
	var rows []registerRow
	for _, row := range s.rows {
		if (row.status == inForce || row.status == pendingOwner) && row.words != "" {
			rows = append(rows, row)
		}
	}
	return rows
}

func (s *rowSet) unconfirmedLines() []string {
	var lines []string
	for _, row := range s.current() {
		for _, item := range row.implications {
			if item.State == "proposed" {
				lines = append(lines, fmt.Sprintf("- %s (of `%s`)", implicationLine(item), row.key))
			}
		}
	}
	return lines
}

func registerLines(lines []string, rows *rowSet) map[int]bool {
	quoted := map[int]bool{}
	if rows == nil || rows.failure != "" {
		return quoted
	}
	unconfirmed := rows.unconfirmedLines()
	for at, line := range lines {
		if slices.Contains(unconfirmed, line) {
			quoted[at] = true
		}
		match := rowBullet.FindStringSubmatch(line)
		if match == nil {
			continue
		}
		cited := citedRows(match[2], rows.rows)
		if len(cited) == 0 {
			continue
		}
		quoted[at] = true
		children := []string{}
		for _, row := range cited {
			following := continuationLines(row)
			if end := at + 1 + len(following); end <= len(lines) && slices.Equal(lines[at+1:end], following) {
				for below := at + 1; below < end; below++ {
					quoted[below] = true
				}
			}
			children = append(children, childLines(row)...)
		}
		for below := at + 1; below < len(lines); below++ {
			if indentOf(lines[below]) <= len(match[1]) {
				break
			}
			if slices.Contains(children, lines[below]) {
				quoted[below] = true
			}
		}
	}
	return quoted
}

func lintBody(body, previous string, reg *register, live []string, rows *rowSet) []string {
	lines := splitLines(body)
	quoted := registerLines(lines, rows)
	problems := ruleFindings(body, previous, reg)
	for at, line := range lines {
		if !quoted[at] && !strings.HasPrefix(line, quotePrefix) && gated(line, live) {
			problems = append(problems, "owner-gate line cites no live answer id: "+firstRunes(strings.TrimSpace(line), 200))
		}
	}
	return append(problems, rowFindings(body, rows)...)
}

func noneIfEmpty(lines []string) []string {
	if len(lines) == 0 {
		return []string{"- none"}
	}
	return lines
}

func touchedLines(rows *rowSet) []string {
	since := rows.readAt.Add(-touchedWindow)
	touched := slices.DeleteFunc(slices.Clone(rows.rows), func(row registerRow) bool {
		return row.updatedAt == "" || row.updated.Before(since)
	})
	slices.SortStableFunc(touched, func(a, b registerRow) int { return b.updated.Compare(a.updated) })
	var lines []string
	used := 0
	for index, row := range touched {
		entry := touchedEntry(row)
		for _, line := range entry {
			used += len(line) + 1
		}
		if used > touchedBudget {
			return append(lines, fmt.Sprintf("- %d more rows: run `rulings.py rows --json`", len(touched)-index))
		}
		lines = append(lines, entry...)
	}
	return lines
}

func touchedEntry(row registerRow) []string {
	switch {
	case row.isNo:
		return []string{fmt.Sprintf("- `%s` (no ruling, touched %s): %s", row.key, row.updatedAt, row.no)}
	case row.words != "":
		return quotedWords(row, fmt.Sprintf("%s, touched %s", row.kind, row.updatedAt))
	default:
		return []string{fmt.Sprintf("- `%s` (%s, touched %s): no words", row.key, row.kind, row.updatedAt)}
	}
}

func riskLines(rows *rowSet) []string {
	var lines []string
	for _, row := range rows.rows {
		if row.status == inForce || row.isNo {
			continue
		}
		status := row.status
		if status == "" {
			status = "no status"
		}
		if row.words != "" {
			lines = append(lines, quotedWords(row, status)...)
		} else {
			lines = append(lines, fmt.Sprintf("- `%s` (%s): no words", row.key, status))
		}
		children := childLines(row)
		lines = append(lines, children[len(children)-1])
	}
	return lines
}

func rowsSection(rows *rowSet) []string {
	if rows.failure != "" {
		return []string{fmt.Sprintf("- the register rows did not load (%s); read them with `rulings.py rows --json`", rows.failure), ""}
	}
	out := []string{rowsHeading, "", "Each row's implications and their states: `rulings.py rows --json`.", ""}
	var owed []string
	for _, row := range rows.current() {
		if row.kind == scopeKind {
			said := row.said
			if said == "" {
				said = "1"
			}
			out = append(out, quotedWords(row, fmt.Sprintf("%s, said %s", row.status, said))...)
			out = append(out, namesLine(row))
		}
		if row.kind == gateKind && row.status == inForce {
			owed = append(owed, quotedWords(row, "owed")...)
		}
	}
	out = append(out, "", "### Rows touched in the last 24 hours", "")
	out = append(out, noneIfEmpty(touchedLines(rows))...)
	out = append(out, "", "### Owed gates", "")
	out = append(out, noneIfEmpty(owed)...)
	out = append(out, "", "### Rows at risk or broken", "")
	out = append(out, noneIfEmpty(riskLines(rows))...)
	out = append(out, "", "### Unconfirmed implications", "")
	return append(append(out, withinBudget(noneIfEmpty(rows.unconfirmedLines()), unconfirmedBudget, "rulings.py rows --json")...), "")
}

func sectionOf(reg *register, lines []string, narrative string, rows *rowSet) string {
	out := []string{registerHeading, ""}
	switch {
	case reg != nil:
		out = append(out, pointer(reg), "")
	case rows == nil || rows.failure != "":
		own := ""
		if ownRules.MatchString(narrative) {
			own = fmt.Sprintf("; the coordinator's own `%s` follows verbatim under `%s`", registerHeading, narrativeHeading)
		}
		out = append(out, "- no `standing-rules` register doc"+own, "")
	}
	out = append(out, lines...)
	if rows == nil {
		return strings.Join(out, "\n") + "\n"
	}
	if len(lines) > 0 {
		out = append(out, "")
	}
	return strings.Join(append(out, rowsSection(rows)...), "\n")
}
