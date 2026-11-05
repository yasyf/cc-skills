package owner

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	wordsFile     = "word-rows.jsonl"
	quoteChars    = 12
	askWords      = 4
	mostAsks      = 6
	redacted      = "[credential redacted]"
	tokenChars    = 32
	tokenEdges    = `([{<.,;:!?)]}>"'` + "`"
	dmPrefix      = "D"
	eventChars    = 500
	cutField      = "cut"
	rowsField     = "rows"
	answersField  = "answers"
	linkedField   = "linked"
	permalinkName = "permalink"
	textField     = "text"
	labelField    = "label"
	errorField    = "error"
	watchesField  = "watches"
	captureField  = "capture"
	optionsField  = "options"
	questionField = "question"
)

var cutFields = map[string]string{slackMessage: textField, slackAction: labelField, slackWatchError: errorField, slackOwnerStopped: watchesField}

var (
	paragraphBreak = regexp.MustCompile(`\n\s*\n`)
	afterQuestion  = regexp.MustCompile(`\?\s+`)
	beforeAlso     = regexp.MustCompile(`(?i)(?:[,;.]\s*|\s+and\s+|\n\s*)(also\W+(?:` + orderVerbs + `)\b)`)
	countedIssues  = regexp.MustCompile(`(?i)\b(two|three|four|five|six|[2-6])\s+(?:\w+\s+)?(?:issues|things|questions|asks|problems|bugs)\b`)
	clauseBreak    = regexp.MustCompile(`\s*[,;]\s*`)
	issueCounts    = map[string]int{"two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
	credentials    = regexp.MustCompile(`\b(?:sk|pk|rk)-[\w-]{16,}|\b(?:gh[pousr]|github_pat)_[\w.-]{20,}|\bxox[abprs]-[\w-]{10,}|\b(?:AKIA|ASIA)[0-9A-Z]{16}\b|\b(?:api|sdk|mob)-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b|\beyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}`)
	longRun        = regexp.MustCompile(`\S{32,}`)
	tokenBody      = regexp.MustCompile(`^[\w+/=]+$`)
)

type wordRows struct {
	TS   string   `json:"ts"`
	Rows []string `json:"rows"`
}

func (w threadWord) owed() bool {
	return w.Text != "" && (strings.HasPrefix(w.Channel, dmPrefix) || w.Thread != w.TS)
}

func (w threadWord) rows(ctx context.Context, runner shell.Runner, entry drive.Entry) ([]string, error) {
	if !w.owed() {
		return nil, nil
	}
	if w.Owner != nil {
		onDrive, err := sessionOnDrive(ctx, runner, entry, w.Owner.Session)
		if err != nil || !onDrive {
			return nil, err
		}
	}
	seconds, err := strconv.ParseFloat(w.TS, 64)
	if err != nil {
		return nil, fmt.Errorf("slack ts %q: %w", w.TS, err)
	}
	path := Ledger(entry.StateDir)
	var rows []string
	var added map[string]string
	err = locked(path, func() error {
		written, err := writtenRows(path, w.TS)
		if err != nil || written != nil {
			rows = written
			return err
		}
		rows, added, err = w.write(path, time.Unix(int64(seconds), 0))
		return err
	})
	if err != nil {
		return nil, err
	}
	for _, row := range rows {
		if said, posted := added[row]; posted {
			if err := postRow(ctx, runner, entry, row, said); err != nil {
				return rows, err
			}
		}
	}
	return rows, nil
}

func (w threadWord) write(path string, at time.Time) ([]string, map[string]string, error) {
	ledger, err := os.ReadFile(path)
	if err != nil && !errors.Is(err, fs.ErrNotExist) {
		return nil, nil, err
	}
	rows := Parse(ledger)
	var carriers, fresh []string
	for _, ask := range asks(redact(w.Text)) {
		if carrier := carrierOf(rows, ask, w.TS, at); carrier != "" {
			carriers = append(carriers, carrier)
			continue
		}
		fresh = append(fresh, ask)
	}
	ids, err := appendRows(path, ledger, at, fresh, opened, 0)
	if err != nil {
		return nil, nil, err
	}
	added := make(map[string]string, len(ids))
	for i, id := range ids {
		added[id] = fresh[i]
	}
	all := append(carriers, ids...)
	return all, added, rememberRows(path, wordRows{TS: w.TS, Rows: all})
}

func carrierOf(rows []Row, ask, ts string, at time.Time) string {
	link := "/p" + strings.ReplaceAll(ts, ".", "")
	words := fold(ask)
	day := at.In(pacific).Format("Jan 2 ")
	for _, row := range rows {
		if strings.Contains(row.Text, link) {
			return row.ID
		}
		if !strings.HasPrefix(row.At, day) || strings.HasPrefix(row.Text, boardTap) {
			continue
		}
		if overlapping(fold(row.Text), words) {
			return row.ID
		}
	}
	return ""
}

func fold(text string) string {
	return strings.ToLower(strings.Join(strings.Fields(text), " "))
}

func overlapping(left, right string) bool {
	if len(left) > len(right) {
		left, right = right, left
	}
	return len(left) >= quoteChars && strings.Contains(right, left)
}

func writtenRows(path, ts string) ([]string, error) {
	file, err := os.Open(filepath.Join(filepath.Dir(path), wordsFile))
	if errors.Is(err, fs.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	defer file.Close()
	lines := bufio.NewScanner(file)
	for lines.Scan() {
		var written wordRows
		if err := json.Unmarshal(lines.Bytes(), &written); err != nil {
			return nil, fmt.Errorf("%s: %w", wordsFile, err)
		}
		if written.TS == ts {
			return written.Rows, nil
		}
	}
	return nil, lines.Err()
}

func rememberRows(path string, written wordRows) error {
	file, err := os.OpenFile(filepath.Join(filepath.Dir(path), wordsFile), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	defer file.Close()
	line, err := json.Marshal(written)
	if err != nil {
		return err
	}
	_, err = file.Write(append(line, '\n'))
	return err
}

func sessionOnDrive(ctx context.Context, runner shell.Runner, entry drive.Entry, session string) (bool, error) {
	who, err := runner.Run(ctx, shell.Command{Argv: []string{"cci", "who", "--drive", entry.Bus(), "--session", session, "--all", "--json"}, Timeout: rowWait})
	if err != nil {
		return false, err
	}
	return strings.TrimSpace(who.Stdout) != "", nil
}

func postRow(ctx context.Context, runner shell.Runner, entry drive.Entry, row, said string) error {
	_, err := runner.Run(ctx, shell.Command{
		Argv:    []string{"cci", "post", "--drive", entry.Bus(), "--lane", communicator, "--kind", "owner", "--id", "owner:" + row, "--attr", "row=" + row, "--quoted", said},
		Timeout: rowWait,
	})
	return err
}

func asks(text string) []string {
	var pieces []string
	for _, paragraph := range paragraphBreak.Split(strings.TrimSpace(text), -1) {
		for _, question := range splitAfterQuestions(paragraph) {
			for _, piece := range splitBeforeAlso(question) {
				pieces = append(pieces, issues(piece)...)
			}
		}
	}
	var merged []string
	for _, piece := range pieces {
		piece = strings.TrimSpace(piece)
		switch {
		case piece == "":
		case len(merged) > 0 && continues(merged[len(merged)-1], piece):
			merged[len(merged)-1] += " " + piece
		default:
			merged = append(merged, piece)
		}
	}
	if len(merged) > 1 && len(merged) <= mostAsks {
		return merged
	}
	return []string{text}
}

func splitAfterQuestions(text string) []string {
	var pieces []string
	start := 0
	for _, found := range afterQuestion.FindAllStringIndex(text, -1) {
		pieces = append(pieces, text[start:found[0]+1])
		start = found[1]
	}
	return append(pieces, text[start:])
}

func splitBeforeAlso(text string) []string {
	var pieces []string
	start := 0
	for _, found := range beforeAlso.FindAllStringSubmatchIndex(text, -1) {
		pieces = append(pieces, text[start:found[0]])
		start = found[2]
	}
	return append(pieces, text[start:])
}

func issues(piece string) []string {
	clauses := clauseBreak.Split(piece, -1)
	for at, clause := range clauses {
		found := countedIssues.FindStringSubmatch(clause)
		if found == nil {
			continue
		}
		count, named := issueCounts[strings.ToLower(found[1])]
		if !named {
			count = int(found[1][0] - '0')
		}
		if len(clauses)-at-1 != count {
			return []string{piece}
		}
		return append([]string{strings.Join(clauses[:at+2], ", ")}, clauses[at+2:]...)
	}
	return []string{piece}
}

func continues(before, piece string) bool {
	return min(len(strings.Fields(before)), len(strings.Fields(piece))) < askWords ||
		strings.HasPrefix(before, ">") ||
		strings.Count(before, "(") > strings.Count(before, ")")
}

func redact(text string) string {
	return longRun.ReplaceAllStringFunc(credentials.ReplaceAllString(text, redacted), func(run string) string {
		token := strings.Trim(run, tokenEdges)
		if len(token) < tokenChars || !tokenBody.MatchString(token) || !mixedToken(token) {
			return run
		}
		return strings.Replace(run, token, redacted, 1)
	})
}

func mixedToken(token string) bool {
	return strings.ContainsFunc(token, unicode.IsLower) && strings.ContainsFunc(token, unicode.IsUpper) && strings.ContainsFunc(token, unicode.IsDigit)
}

func fitted(raw []byte, rows []string) ([]byte, error) {
	if len(rows) == 0 && len(raw) <= eventChars {
		return raw, nil
	}
	line := map[string]any{}
	if err := json.Unmarshal(raw, &line); err != nil {
		return nil, err
	}
	if len(rows) > 0 {
		line[rowsField] = rows
	}
	encoded, err := json.Marshal(line)
	if err != nil {
		return nil, err
	}
	for _, shrink := range []func(map[string]any){withoutCardPicks, withoutCardWords, withoutLinkedWords, withoutPermalink} {
		if len(encoded) <= eventChars {
			return encoded, nil
		}
		shrink(line)
		if encoded, err = json.Marshal(line); err != nil {
			return nil, err
		}
	}
	kind, _ := line["type"].(string)
	field, cuttable := cutFields[kind]
	if len(encoded) <= eventChars || !cuttable {
		return encoded, nil
	}
	line[cutField] = true
	if encoded, err = json.Marshal(line); err != nil {
		return nil, err
	}
	if watches, ok := line[field].([]any); ok {
		return withoutLastWatches(line, watches)
	}
	words, _ := line[field].(string)
	said := []rune(words)
	line[field] = string(said[:max(0, len(said)-(len(encoded)-eventChars))])
	return json.Marshal(line)
}

func withoutLastWatches(line map[string]any, watches []any) ([]byte, error) {
	for {
		encoded, err := json.Marshal(line)
		if err != nil || len(encoded) <= eventChars || len(watches) == 0 {
			return encoded, err
		}
		watches = watches[:len(watches)-1]
		line[watchesField] = watches
	}
}

func withoutCardPicks(line map[string]any) {
	delete(line, captureField)
	delete(line, optionsField)
	if answers, ok := line[answersField].(map[string]any); ok {
		delete(answers, optionsField)
	}
}

func withoutCardWords(line map[string]any) {
	delete(line, questionField)
	if answers, ok := line[answersField].(map[string]any); ok {
		delete(answers, questionField)
	}
}

func withoutLinkedWords(line map[string]any) {
	if linked, ok := line[linkedField].(map[string]any); ok {
		delete(linked, textField)
		delete(linked, answersField)
	}
}

func withoutPermalink(line map[string]any) {
	delete(line, permalinkName)
}

func clippedLine(text string) string {
	said := []rune(text)
	return string(said[:min(len(said), eventChars)])
}
