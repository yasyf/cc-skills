package quotes

import (
	"cmp"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"unicode/utf8"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ledger"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	rootLane    = "root"
	lineBreak   = "¶"
	clauseMarks = `.!?,;:"()¶`
	shownWords  = 80
	quotedForm  = `"[^"\n]+"|“[^”\n]+”`
	noSuchNote  = 3
	scopeHits   = "20"
)

var (
	citation    = regexp.MustCompile(`(?i)\b(rule/[0-9a-f]{7,40}|prompt/\d{4}-\d{6}|answer\s+[0-9a-f]{7,40})\b`)
	quoted      = regexp.MustCompile(quotedForm)
	leadIn      = regexp.MustCompile(`^(?:[^"“.!?\n]|[.!?]\S)*?((?:` + quotedForm + `)(?:\s*(?:,\s*and|,|;|and)\s*(?:` + quotedForm + `))*)`)
	otherSource = regexp.MustCompile(`(?i)\b(?:your|root|relay|record)\s+#?\d{5,}`)
	elided      = regexp.MustCompile(`\s*(?:\.\.\.|…)\s*`)
	flatten     = strings.NewReplacer("’", "'", "‘", "'", "“", `"`, "”", `"`)
)

var joiners = []string{" and", " but", " or", " so"}

var ErrUnknownKey = errors.New("no such row or answer; an answer's key is `answer <id>`")

type Quote struct {
	Key   string
	Words string
}

func Cited(text string) []Quote {
	var out []Quote
	spans := citation.FindAllStringSubmatchIndex(text, -1)
	for i, span := range spans {
		end := len(text)
		if i+1 < len(spans) {
			end = spans[i+1][0]
		}
		found := leadIn.FindStringSubmatch(text[span[1]:end])
		if found == nil || otherSource.MatchString(strings.TrimSuffix(found[0], found[1])) {
			continue
		}
		key := strings.Join(strings.Fields(text[span[2]:span[3]]), " ")
		for _, words := range quoted.FindAllString(found[1], -1) {
			out = append(out, Quote{key, strings.Trim(words, `"“”`)})
		}
	}
	return out
}

func Misquote(key, words, source string) string {
	said := flat(source)
	parts := elided.Split(flat(words), -1)
	opened, from := -1, 0
	for _, part := range parts {
		at := strings.Index(said[from:], part)
		if at < 0 {
			return fmt.Sprintf("%s does not say %q", key, words)
		}
		if opened < 0 {
			opened = from + at
		}
		from += at + len(part)
	}
	if parts[0] != "" && !opensClause(strings.TrimSpace(said[:opened])) {
		return fmt.Sprintf("%s has more of that clause before %q: %q", key, words, tail(said[:opened]))
	}
	rest := strings.TrimSpace(said[from:])
	if parts[len(parts)-1] != "" && rest != "" && !strings.ContainsRune(clauseMarks, lastRune(words)) && !strings.ContainsRune(clauseMarks, firstRune(rest)) {
		return fmt.Sprintf("%s goes on after %q: %q", key, words, head(rest))
	}
	return ""
}

func flat(text string) string {
	return strings.Join(strings.Fields(flatten.Replace(strings.ReplaceAll(text, "\n", " "+lineBreak+" "))), " ")
}

func opensClause(before string) bool {
	return strings.ContainsRune(clauseMarks, lastRune(before)) || slices.ContainsFunc(joiners, func(joiner string) bool { return strings.HasSuffix(before, joiner) })
}

func firstRune(text string) rune {
	r, _ := utf8.DecodeRuneInString(text)
	return r
}

func lastRune(text string) rune {
	if text == "" {
		return []rune(lineBreak)[0]
	}
	r, _ := utf8.DecodeLastRuneInString(text)
	return r
}

func head(text string) string {
	if runes := []rune(text); len(runes) > shownWords {
		return string(runes[:shownWords]) + "…"
	}
	return text
}

func tail(text string) string {
	if runes := []rune(text); len(runes) > shownWords {
		return "…" + string(runes[len(runes)-shownWords:])
	}
	return text
}

type Reader struct {
	Runner shell.Runner
	Entry  func() (drive.Entry, error)

	entry   *drive.Entry
	rows    map[string]ledger.Row
	parents map[string]string
	answers map[string]string
	unread  error
}

func NewReader(runner shell.Runner, driveName string) *Reader {
	return &Reader{Runner: runner, Entry: func() (drive.Entry, error) { return drive.Named(driveName) }}
}

func (r *Reader) Check(ctx context.Context, text string) []string {
	var warnings []string
	cited := Cited(text)
	if words := quoted.FindString(text); words != "" && len(cited) == 0 {
		warnings = append(warnings, fmt.Sprintf("quotes %s with no row or answer key before it; an answer's key is `answer <id>`", words))
	}
	for _, quote := range cited {
		source, err := r.words(ctx, quote.Key)
		if err != nil {
			warnings = append(warnings, fmt.Sprintf("could not read %s: %v", quote.Key, err))
			continue
		}
		if warning := Misquote(quote.Key, quote.Words, source); warning != "" {
			warnings = append(warnings, warning)
		}
	}
	return warnings
}

func (r *Reader) Override(ctx context.Context, record client.Record) []string {
	if record.Lane != rootLane || len(bus.Overrides(record)) == 0 {
		return nil
	}
	return r.Check(ctx, bus.FullText(record))
}

func (r *Reader) Whole(ctx context.Context, key string) (string, error) {
	if answer, isAnswer := answerID(key); isAnswer {
		return r.answer(ctx, answer)
	}
	if err := r.load(ctx); err != nil {
		return "", err
	}
	parent, found := r.parents[key]
	if !found {
		return "", fmt.Errorf("%s: %w", key, ErrUnknownKey)
	}
	row := r.rows[parent]
	var out strings.Builder
	fmt.Fprintf(&out, "%s (%s)\n%s\n", parent, row.Fields["at"], row.Fields["words"])
	for _, repeat := range repeatsOf(row) {
		fmt.Fprintf(&out, "\nrepeat %s (%s)\n%s\n", repeat.Key, repeat.At, repeat.Words)
	}
	for _, implication := range implicationsOf(row) {
		fmt.Fprintf(&out, "\nimplication %d [%s]: %s\n", implication.N, implication.State, implication.Text)
	}
	return out.String(), nil
}

func (r *Reader) words(ctx context.Context, key string) (string, error) {
	if answer, isAnswer := answerID(key); isAnswer {
		return r.answer(ctx, answer)
	}
	if err := r.load(ctx); err != nil {
		return "", err
	}
	parent, found := r.parents[key]
	if !found {
		return "", ErrUnknownKey
	}
	row := r.rows[parent]
	var spoken []string
	if key == parent {
		spoken = append(spoken, row.Fields["words"])
	}
	for _, repeat := range repeatsOf(row) {
		if key == parent || repeat.Key == key {
			spoken = append(spoken, repeat.Words)
		}
	}
	return strings.Join(spoken, "\n"), nil
}

func answerID(key string) (string, bool) {
	id, found := strings.CutPrefix(strings.ToLower(key), "answer ")
	return id, found
}

type Answer struct {
	ID        string `json:"id"`
	Title     string `json:"title"`
	Body      string `json:"body"`
	UpdatedAt string `json:"updated_at"`
}

func (r *Reader) Answers(ctx context.Context, ids []string, scope string) ([]Answer, error) {
	found := []Answer{}
	for _, id := range ids {
		out, err := r.ccn(ctx, "answer", "show", id, "--json")
		var exit *shell.ExitError
		if errors.As(err, &exit) && exit.Code == noSuchNote {
			continue
		}
		if err != nil {
			return nil, err
		}
		var answer Answer
		if err := json.Unmarshal([]byte(out), &answer); err != nil {
			return nil, fmt.Errorf("answer %s: %w", id, err)
		}
		found = append(found, answer)
	}
	if scope == "" {
		return found, nil
	}
	out, err := r.ccn(ctx, "answer", "search", scope, "--label", "scope:durable", "--limit", scopeHits, "--json")
	if err != nil {
		return nil, err
	}
	var ranked []Answer
	if err := json.Unmarshal([]byte(out), &ranked); err != nil {
		return nil, fmt.Errorf("answers on %s: %w", scope, err)
	}
	return append(found, ranked...), nil
}

func (r *Reader) ccn(ctx context.Context, args ...string) (string, error) {
	entry, err := r.resolved()
	if err != nil {
		return "", err
	}
	result, err := r.Runner.Run(ctx, shell.Command{Argv: slices.Concat([]string{"ccn", "-R", entry.Checkout}, args)})
	return result.Stdout, err
}

func (r *Reader) answer(ctx context.Context, id string) (string, error) {
	if text, read := r.answers[id]; read {
		return text, nil
	}
	text, err := r.ccn(ctx, "answer", "show", id)
	if err != nil {
		return "", err
	}
	if r.answers == nil {
		r.answers = map[string]string{}
	}
	r.answers[id] = text
	return text, nil
}

func (r *Reader) resolved() (drive.Entry, error) {
	if r.entry != nil {
		return *r.entry, nil
	}
	entry, err := r.Entry()
	if err != nil {
		return drive.Entry{}, err
	}
	r.entry = &entry
	return entry, nil
}

func (r *Reader) load(ctx context.Context) error {
	if r.rows != nil || r.unread != nil {
		return r.unread
	}
	r.unread = r.read(ctx)
	return r.unread
}

func (r *Reader) read(ctx context.Context) error {
	entry, err := r.resolved()
	if err != nil {
		return err
	}
	program := cmp.Or(entry.Program, filepath.Base(entry.StateDir))
	ccn := func(ctx context.Context, args ...string) (string, error) {
		result, err := r.Runner.Run(ctx, shell.Command{Argv: slices.Concat([]string{"ccn", "-R", entry.Checkout}, args)})
		return result.Stdout, err
	}
	rows, err := ledger.Register(ctx, ccn, ledger.CLI{Runner: r.Runner, Repo: entry.Checkout}, program)
	if err != nil {
		return fmt.Errorf("the owner's register: %w", err)
	}
	if len(rows) == 0 {
		return fmt.Errorf("no owner's register for program %s", program)
	}
	r.rows, r.parents = map[string]ledger.Row{}, map[string]string{}
	for _, row := range rows {
		r.rows[row.Key], r.parents[row.Key] = row, row.Key
		for _, repeat := range repeatsOf(row) {
			if repeat.Key != "" {
				if _, own := r.parents[repeat.Key]; !own {
					r.parents[repeat.Key] = row.Key
				}
			}
		}
	}
	return nil
}

type repeat struct {
	Key   string `json:"key"`
	At    string `json:"at"`
	Words string `json:"words"`
}

type implication struct {
	N     int    `json:"n"`
	State string `json:"state"`
	Text  string `json:"text"`
}

func repeatsOf(row ledger.Row) []repeat {
	var repeats []repeat
	if err := json.Unmarshal([]byte(cmp.Or(row.Fields["repeats"], "[]")), &repeats); err != nil {
		panic(fmt.Sprintf("register row %s repeats: %v", row.Key, err))
	}
	return repeats
}

func implicationsOf(row ledger.Row) []implication {
	var implications []implication
	if err := json.Unmarshal([]byte(cmp.Or(row.Fields["implications"], "[]")), &implications); err != nil {
		panic(fmt.Sprintf("register row %s implications: %v", row.Key, err))
	}
	return implications
}
