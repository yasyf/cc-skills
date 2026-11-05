package screen

import (
	"encoding/json"
	"regexp"
	"slices"
	"strings"
	"unicode"
	"unicode/utf8"
)

type Kind string

const (
	Approval Kind = "approval"
	Question Kind = "question"
)

type Input string

const (
	Idle    Input = "idle"
	Busy    Input = "busy"
	Waiting Input = "waiting"
	NoInput Input = ""
)

const (
	rendered     = "screen"
	promptMark   = "❯"
	modeIndent   = 2
	margin       = 2
	codexRows    = 4
	runningRows  = 12
	titleChars   = 80
	frame        = "│☐ "
	askedJoin    = " / "
	keyHint      = `[\p{L}\p{N}_↑↓/+ ]+ to [\p{L}\p{N}_ ]+`
	space        = `[\s\p{Z}]`
	notSpace     = `[^\s\p{Z}]`
	numberedForm = `^` + space + `*[❯›]?` + space + `*\d+\.` + space + `+` + notSpace
)

var (
	footer        = regexp.MustCompile(`^` + space + `*` + keyHint + `(?:` + space + `*·` + space + `*` + keyHint + `)*` + space + `*$`)
	cancel        = regexp.MustCompile(`(?i)\besc to cancel\b`)
	picker        = regexp.MustCompile(`(?i)\bto (?:select|navigate)\b`)
	cursor        = regexp.MustCompile(`^` + space + `*[❯›]` + space + `*` + notSpace)
	numbered      = regexp.MustCompile(numberedForm)
	codexComposer = regexp.MustCompile(`^›(?:` + space + `|$)`)
	running       = regexp.MustCompile(`(?i)\besc to interrupt\b|\((?:\d+h )?(?:\d+m )?\d+s [·•]`)
	countdown     = regexp.MustCompile(`(?i)\bdeny\b.*?\bin (\d+:\d{2})\b`)
	word          = regexp.MustCompile(`[\p{L}\p{N}_]`)
	ruled         = regexp.MustCompile(`^─{2,}(?: [^─]+ ─+)?$`)
)

type Terminal struct {
	Source string          `json:"source"`
	Tail   []string        `json:"tail"`
	Draft  json.RawMessage `json:"draft"`
}

func (t Terminal) drafted() bool {
	return !slices.Contains([]string{"", "null", "false", `""`}, string(t.Draft))
}

type Dialog struct {
	Kind      Kind
	Asked     string
	Countdown string
}

func (d Dialog) Title() string {
	first, _, _ := strings.Cut(d.Asked, askedJoin)
	if runes := []rune(first); len(runes) > titleChars {
		return string(runes[:titleChars])
	}
	return first
}

func trimmed(tail []string) []string {
	lines := make([]string, len(tail))
	for i, line := range tail {
		lines[i] = strings.TrimRightFunc(line, unicode.IsSpace)
	}
	return lines
}

func indent(line string) int {
	return utf8.RuneCountInString(line) - utf8.RuneCountInString(strings.TrimLeftFunc(line, unicode.IsSpace))
}

func ruleRows(tail []string) []int {
	var rows []int
	for i, line := range tail {
		if IsRule(line) {
			rows = append(rows, i)
		}
	}
	return rows
}

func IsRule(line string) bool {
	return ruled.MatchString(strings.TrimSpace(line))
}

func Running(line string) bool {
	return running.MatchString(line)
}

func IdlePrompt(t Terminal) bool {
	if t.drafted() || t.Source != rendered {
		return false
	}
	tail := trimmed(t.Tail)
	rules := ruleRows(tail)
	if len(rules) < 2 || rules[len(rules)-1]-rules[len(rules)-2] != 2 || strings.TrimSpace(tail[rules[len(rules)-2]+1]) != promptMark {
		return false
	}
	last := rules[len(rules)-1]
	width := utf8.RuneCountInString(tail[last])
	for row, line := range tail[last+1:] {
		if line == "" || (row == 0 && indent(line) == modeIndent) || (indent(line) > modeIndent && utf8.RuneCountInString(line) == width-margin) {
			continue
		}
		return false
	}
	return true
}

func Composer(tail []string) (int, bool) {
	rules := ruleRows(tail)
	if len(rules) >= 2 {
		top := rules[len(rules)-2]
		if next := tail[top+1]; strings.HasPrefix(strings.TrimLeftFunc(next, unicode.IsSpace), promptMark) && !numbered.MatchString(next) {
			return top, true
		}
	}
	var shown []int
	for i, line := range tail {
		if strings.TrimSpace(line) != "" {
			shown = append(shown, i)
		}
	}
	shown = shown[max(0, len(shown)-codexRows):]
	slices.Reverse(shown)
	for _, i := range shown {
		if codexComposer.MatchString(tail[i]) && !numbered.MatchString(tail[i]) {
			return i, true
		}
	}
	return 0, false
}

func FindDialog(t Terminal) (Dialog, bool) {
	if t.Source != rendered {
		return Dialog{}, false
	}
	tail := trimmed(t.Tail)
	foot := lastIndex(tail, len(tail), func(line string) bool { return footer.MatchString(line) && cancel.MatchString(line) })
	if foot < 0 {
		return Dialog{}, false
	}
	if box, found := Composer(tail); found && box > foot {
		return Dialog{}, false
	}
	at := lastIndex(tail, foot, cursor.MatchString)
	if at < 0 {
		return Dialog{}, false
	}
	top := lastIndex(tail, at, IsRule) + 1
	var rows []string
	left := ""
	for _, line := range tail[top:foot] {
		if !word.MatchString(line) || running.MatchString(line) {
			continue
		}
		row := strings.Trim(line, frame)
		if found := countdown.FindStringSubmatch(row); found != nil {
			if left == "" {
				left = found[1]
			}
			continue
		}
		rows = append(rows, row)
	}
	kind := Approval
	if picker.MatchString(tail[foot]) {
		kind = Question
	}
	return Dialog{kind, strings.Join(rows, askedJoin), left}, true
}

func lastIndex(tail []string, before int, matches func(string) bool) int {
	for i := before - 1; i >= 0; i-- {
		if matches(tail[i]) {
			return i
		}
	}
	return -1
}

func Wakeable(t Terminal) bool {
	return IdlePrompt(t) || InputState(t) == Idle || restsOnSuggestion(t)
}

// restsOnSuggestion reads text in Claude's composer with no turn running as the
// grey suggestion Claude offers at rest: a screen read carries no color to tell
// it from typed text, and nothing types into a lane's terminal without Enter.
func restsOnSuggestion(t Terminal) bool {
	if t.Source != rendered || t.drafted() {
		return false
	}
	if _, open := FindDialog(t); open {
		return false
	}
	tail := trimmed(t.Tail)
	box, found := Composer(tail)
	return found && !codexComposer.MatchString(tail[box]) && !slices.ContainsFunc(tail[max(0, len(tail)-runningRows):], running.MatchString)
}

func InputState(t Terminal) Input {
	if t.Source != rendered {
		return NoInput
	}
	if _, open := FindDialog(t); open {
		return Waiting
	}
	tail := trimmed(t.Tail)
	box, found := Composer(tail)
	if !found {
		return NoInput
	}
	empty := IdlePrompt(t) || (codexComposer.MatchString(tail[box]) && !t.drafted())
	if !empty || slices.ContainsFunc(tail[max(0, len(tail)-runningRows):], running.MatchString) {
		return Busy
	}
	return Idle
}
