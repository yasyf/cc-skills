package pyre

import (
	"fmt"
	"maps"
	"slices"
	"strconv"
	"strings"
	"unicode"

	"github.com/dlclark/regexp2"
)

const (
	wordMembers  = `\p{L}\p{N}_`
	spaceMembers = `\s\x1c-\x1f`
	word         = `[` + wordMembers + `]`
	notWord      = `[^` + wordMembers + `]`
	space        = `[` + spaceMembers + `]`
	notSpace     = `[^` + spaceMembers + `]`
	boundary     = `(?:(?<=` + word + `)(?!` + word + `)|(?<!` + word + `)(?=` + word + `))`
)

var caseExtras = map[rune][]rune{
	0x0049: {0x0069, 0x0131}, 0x0053: {0x0073, 0x017f}, 0x0069: {0x0131}, 0x0073: {0x017f}, 0x00b5: {0x03bc},
	0x0130: {0x0069, 0x0131}, 0x0131: {0x0069}, 0x017f: {0x0073}, 0x0345: {0x03b9, 0x1fbe}, 0x0390: {0x1fd3},
	0x0392: {0x03b2, 0x03d0}, 0x0395: {0x03b5, 0x03f5}, 0x0398: {0x03b8, 0x03d1},
	0x0399: {0x0345, 0x03b9, 0x1fbe}, 0x039a: {0x03ba, 0x03f0}, 0x039c: {0x00b5, 0x03bc},
	0x03a0: {0x03c0, 0x03d6}, 0x03a1: {0x03c1, 0x03f1}, 0x03a3: {0x03c2, 0x03c3}, 0x03a6: {0x03c6, 0x03d5},
	0x03b0: {0x1fe3}, 0x03b2: {0x03d0}, 0x03b5: {0x03f5}, 0x03b8: {0x03d1}, 0x03b9: {0x0345, 0x1fbe},
	0x03ba: {0x03f0}, 0x03bc: {0x00b5}, 0x03c0: {0x03d6}, 0x03c1: {0x03f1}, 0x03c2: {0x03c3}, 0x03c3: {0x03c2},
	0x03c6: {0x03d5}, 0x03d0: {0x03b2}, 0x03d1: {0x03b8}, 0x03d5: {0x03c6}, 0x03d6: {0x03c0}, 0x03f0: {0x03ba},
	0x03f1: {0x03c1}, 0x03f4: {0x03b8, 0x03d1}, 0x03f5: {0x03b5}, 0x0412: {0x0432, 0x1c80},
	0x0414: {0x0434, 0x1c81}, 0x041e: {0x043e, 0x1c82}, 0x0421: {0x0441, 0x1c83},
	0x0422: {0x0442, 0x1c84, 0x1c85}, 0x042a: {0x044a, 0x1c86}, 0x0432: {0x1c80}, 0x0434: {0x1c81},
	0x043e: {0x1c82}, 0x0441: {0x1c83}, 0x0442: {0x1c84, 0x1c85}, 0x044a: {0x1c86}, 0x0462: {0x0463, 0x1c87},
	0x0463: {0x1c87}, 0x1c80: {0x0432}, 0x1c81: {0x0434}, 0x1c82: {0x043e}, 0x1c83: {0x0441},
	0x1c84: {0x0442, 0x1c85}, 0x1c85: {0x0442, 0x1c84}, 0x1c86: {0x044a}, 0x1c87: {0x0463}, 0x1c88: {0xa64b},
	0x1e60: {0x1e61, 0x1e9b}, 0x1e61: {0x1e9b}, 0x1e9b: {0x1e61}, 0x1fbe: {0x0345, 0x03b9}, 0x1fd3: {0x0390},
	0x1fe3: {0x03b0}, 0xa64a: {0x1c88, 0xa64b}, 0xa64b: {0x1c88}, 0xfb05: {0xfb06}, 0xfb06: {0xfb05},
}

var caseExtraKeys = slices.Sorted(maps.Keys(caseExtras))

type translation struct {
	source  string
	options regexp2.RegexOptions
	flags   int
	groups  int
	names   map[string]int
}

type translator struct {
	src    []rune
	pos    int
	flags  int
	out    strings.Builder
	groups int
	names  map[string]int
}

func translate(pattern string, flags int) (translation, error) {
	t := &translator{src: []rune(pattern), flags: flags, names: map[string]int{}}
	if err := t.run(); err != nil {
		return translation{}, fmt.Errorf("pyre: %q: %w", pattern, err)
	}
	if unsupported := t.flags &^ (IgnoreCase | Multiline | DotAll | Unicode | Verbose); unsupported != 0 {
		return translation{}, fmt.Errorf("pyre: %q: flags %#x have no regexp2 equivalent", pattern, unsupported)
	}
	var options regexp2.RegexOptions
	if t.flags&IgnoreCase != 0 {
		options |= regexp2.IgnoreCase
	}
	if t.flags&Multiline != 0 {
		options |= regexp2.Multiline
	}
	if t.flags&DotAll != 0 {
		options |= regexp2.Singleline
	}
	return translation{source: t.out.String(), options: options, flags: t.flags | Unicode, groups: t.groups, names: t.names}, nil
}

func (t *translator) peek(offset int) rune {
	if t.pos+offset < len(t.src) {
		return t.src[t.pos+offset]
	}
	return -1
}

func (t *translator) next() (rune, error) {
	if t.pos >= len(t.src) {
		return 0, fmt.Errorf("pattern ends inside a construct")
	}
	c := t.src[t.pos]
	t.pos++
	return c, nil
}

func (t *translator) folding() bool {
	return t.flags&IgnoreCase != 0
}

func (t *translator) run() error {
	for t.pos < len(t.src) {
		c := t.src[t.pos]
		t.pos++
		if t.flags&Verbose != 0 && strings.ContainsRune(" \t\n\r\v\f", c) {
			continue
		}
		if t.flags&Verbose != 0 && c == '#' {
			t.skipComment()
			continue
		}
		var err error
		switch c {
		case '\\':
			err = t.escape()
		case '[':
			err = t.class()
		case '(':
			err = t.group()
		case ')', '|', '.', '^', '$':
			t.out.WriteRune(c)
		case '*', '+', '?':
			err = t.repeat(string(c))
		case '{':
			if quantifier, ok := t.braces(); ok {
				err = t.repeat(quantifier)
			} else {
				t.literal('{')
			}
		default:
			t.literal(c)
		}
		if err != nil {
			return err
		}
	}
	return nil
}

func (t *translator) skipComment() {
	for t.pos < len(t.src) {
		c := t.src[t.pos]
		t.pos++
		if c == '\\' {
			t.pos++
		} else if c == '\n' {
			return
		}
	}
}

func (t *translator) repeat(quantifier string) error {
	t.out.WriteString(quantifier)
	switch t.peek(0) {
	case '?':
		t.pos++
		t.out.WriteByte('?')
	case '+':
		return fmt.Errorf("possessive quantifier %s+ has no regexp2 equivalent", quantifier)
	}
	return nil
}

func (t *translator) braces() (string, bool) {
	i := t.pos
	if i < len(t.src) && t.src[i] == '}' {
		return "", false
	}
	digits := func() string {
		start := i
		for i < len(t.src) && t.src[i] >= '0' && t.src[i] <= '9' {
			i++
		}
		return string(t.src[start:i])
	}
	low := digits()
	comma := i < len(t.src) && t.src[i] == ','
	high := low
	if comma {
		i++
		high = digits()
	}
	if i >= len(t.src) || t.src[i] != '}' {
		return "", false
	}
	t.pos = i + 1
	if low == "" {
		low = "0"
	}
	if !comma {
		return "{" + low + "}", true
	}
	return "{" + low + "," + high + "}", true
}

func (t *translator) literal(c rune) {
	if extras, ok := caseExtras[c]; ok && t.folding() {
		t.out.WriteByte('[')
		t.out.WriteString(classRune(c))
		for _, extra := range extras {
			t.out.WriteString(classRune(extra))
		}
		t.out.WriteByte(']')
		return
	}
	switch {
	case strings.ContainsRune(`\*+?|{}[]()^$.#`, c):
		t.out.WriteByte('\\')
		t.out.WriteRune(c)
	case c == ' ' || unicode.IsPrint(c):
		t.out.WriteRune(c)
	default:
		t.out.WriteString(hexRune(c))
	}
}

func classRune(c rune) string {
	switch {
	case strings.ContainsRune(`\]-[^`, c):
		return `\` + string(c)
	case c == ' ' || unicode.IsPrint(c):
		return string(c)
	default:
		return hexRune(c)
	}
}

func hexRune(c rune) string {
	switch {
	case c < 0x100:
		return fmt.Sprintf(`\x%02x`, c)
	case c < 0x10000:
		return fmt.Sprintf(`\u%04x`, c)
	default:
		return string(c)
	}
}

func (t *translator) escape() error {
	c, err := t.next()
	if err != nil {
		return fmt.Errorf("pattern ends with a lone backslash")
	}
	switch c {
	case 'A':
		t.out.WriteString(`\A`)
	case 'Z':
		t.out.WriteString(`\z`)
	case 'b':
		t.out.WriteString(boundary)
	case 'B':
		return fmt.Errorf(`\B is unsupported: Python 3.13 and 3.14 disagree on it over empty input`)
	case 'd':
		t.out.WriteString(`\d`)
	case 'D':
		t.out.WriteString(`\D`)
	case 's':
		t.out.WriteString(space)
	case 'S':
		t.out.WriteString(notSpace)
	case 'w':
		t.out.WriteString(word)
	case 'W':
		t.out.WriteString(notWord)
	case '1', '2', '3', '4', '5', '6', '7', '8', '9':
		return t.reference(c)
	default:
		r, err := t.charEscape(c, false)
		if err != nil {
			return err
		}
		t.literal(r)
	}
	return nil
}

func (t *translator) reference(first rune) error {
	digits := string(first)
	if second := t.peek(0); second >= '0' && second <= '9' {
		if isOctal(first) && isOctal(second) && isOctal(t.peek(1)) {
			value, _ := strconv.ParseUint(string(t.src[t.pos-1:t.pos+2]), 8, 32)
			if value > 0o377 {
				return fmt.Errorf("octal escape \\%s is outside 0o0-0o377", string(t.src[t.pos-1:t.pos+2]))
			}
			t.pos += 2
			t.literal(rune(value))
			return nil
		}
		digits += string(second)
		t.pos++
	}
	group, _ := strconv.Atoi(digits)
	if group > t.groups {
		return fmt.Errorf("invalid group reference %d", group)
	}
	fmt.Fprintf(&t.out, `\k<%d>`, group)
	return nil
}

func isOctal(c rune) bool {
	return c >= '0' && c <= '7'
}

func (t *translator) charEscape(c rune, inClass bool) (rune, error) {
	switch c {
	case 'a':
		return '\a', nil
	case 'f':
		return '\f', nil
	case 'n':
		return '\n', nil
	case 'r':
		return '\r', nil
	case 't':
		return '\t', nil
	case 'v':
		return '\v', nil
	case 'x':
		return t.hex(2)
	case 'u':
		return t.hex(4)
	case 'U':
		return t.hex(8)
	case 'N':
		return 0, fmt.Errorf(`\N{...} named escapes are unsupported`)
	}
	if isOctal(c) && (c == '0' || inClass) {
		digits := string(c)
		for len(digits) < 3 && isOctal(t.peek(0)) {
			digits += string(t.src[t.pos])
			t.pos++
		}
		value, _ := strconv.ParseUint(digits, 8, 32)
		if value > 0o377 {
			return 0, fmt.Errorf("octal escape \\%s is outside 0o0-0o377", digits)
		}
		return rune(value), nil
	}
	if c < 0x80 && (unicode.IsLetter(c) || unicode.IsDigit(c)) {
		return 0, fmt.Errorf("bad escape \\%c", c)
	}
	return c, nil
}

func (t *translator) hex(width int) (rune, error) {
	if t.pos+width > len(t.src) {
		return 0, fmt.Errorf("incomplete hex escape")
	}
	digits := string(t.src[t.pos : t.pos+width])
	value, err := strconv.ParseUint(digits, 16, 32)
	if err != nil || value > unicode.MaxRune {
		return 0, fmt.Errorf("bad hex escape %q", digits)
	}
	t.pos += width
	return rune(value), nil
}

func (t *translator) group() error {
	if t.peek(0) != '?' {
		t.groups++
		fmt.Fprintf(&t.out, "(?<%d>", t.groups)
		return nil
	}
	t.pos++
	c, err := t.next()
	if err != nil {
		return err
	}
	switch c {
	case ':', '=', '!', '>':
		t.out.WriteString("(?" + string(c))
	case '<':
		look, err := t.next()
		if err != nil {
			return err
		}
		if look != '=' && look != '!' {
			return fmt.Errorf("unknown extension (?<%c", look)
		}
		t.out.WriteString("(?<" + string(look))
	case 'P':
		return t.named()
	case '#':
		for {
			c, err := t.next()
			if err != nil {
				return fmt.Errorf("unterminated comment group")
			}
			if c == '\\' {
				t.pos++
			} else if c == ')' {
				return nil
			}
		}
	case '(':
		name, err := t.until(')')
		if err != nil {
			return err
		}
		group, err := t.groupNumber(name)
		if err != nil {
			return err
		}
		fmt.Fprintf(&t.out, "(?(%d)", group)
	default:
		t.pos--
		return t.inlineFlags()
	}
	return nil
}

func (t *translator) named() error {
	c, err := t.next()
	if err != nil {
		return err
	}
	switch c {
	case '<':
		name, err := t.until('>')
		if err != nil {
			return err
		}
		if _, ok := t.names[name]; ok {
			return fmt.Errorf("redefinition of group name %q", name)
		}
		t.groups++
		t.names[name] = t.groups
		fmt.Fprintf(&t.out, "(?<%d>", t.groups)
	case '=':
		name, err := t.until(')')
		if err != nil {
			return err
		}
		group, ok := t.names[name]
		if !ok {
			return fmt.Errorf("unknown group name %q", name)
		}
		fmt.Fprintf(&t.out, `\k<%d>`, group)
	default:
		return fmt.Errorf("unknown extension (?P%c", c)
	}
	return nil
}

func (t *translator) until(end rune) (string, error) {
	start := t.pos
	for t.pos < len(t.src) && t.src[t.pos] != end {
		t.pos++
	}
	if t.pos >= len(t.src) {
		return "", fmt.Errorf("missing %c", end)
	}
	t.pos++
	return string(t.src[start : t.pos-1]), nil
}

func (t *translator) groupNumber(name string) (int, error) {
	if group, err := strconv.Atoi(name); err == nil {
		return group, nil
	}
	group, ok := t.names[name]
	if !ok {
		return 0, fmt.Errorf("unknown group name %q", name)
	}
	return group, nil
}

func (t *translator) inlineFlags() error {
	letters := map[rune]int{'a': ASCII, 'i': IgnoreCase, 'L': Locale, 'm': Multiline, 's': DotAll, 'u': Unicode, 'x': Verbose}
	start := t.pos - 2
	for {
		c, err := t.next()
		if err != nil {
			return err
		}
		if c == ')' {
			if t.out.Len() != 0 {
				return fmt.Errorf("global flags not at the start of the expression")
			}
			return nil
		}
		flag, ok := letters[c]
		if !ok {
			return fmt.Errorf("scoped inline flags (?%s are unsupported", string(t.src[start+2:t.pos]))
		}
		t.flags |= flag
	}
}

func (t *translator) class() error {
	var members strings.Builder
	var singles []rune
	var ranges [][2]rune
	t.out.WriteByte('[')
	if t.peek(0) == '^' {
		t.pos++
		t.out.WriteByte('^')
	}
	first := true
	for {
		c, err := t.next()
		if err != nil {
			return fmt.Errorf("unterminated character set")
		}
		if c == ']' && !first {
			break
		}
		first = false
		low, category, err := t.classItem(c)
		if err != nil {
			return err
		}
		if t.peek(0) != '-' || t.peek(1) == -1 {
			members.WriteString(category)
			if category == "" {
				members.WriteString(classRune(low))
				singles = append(singles, low)
			}
			continue
		}
		t.pos++
		if t.peek(0) == ']' {
			members.WriteString(category)
			if category == "" {
				members.WriteString(classRune(low))
				singles = append(singles, low)
			}
			members.WriteString(`\-`)
			t.pos++
			break
		}
		upper, _ := t.next()
		high, highCategory, err := t.classItem(upper)
		if err != nil {
			return err
		}
		if category != "" || highCategory != "" || high < low {
			return fmt.Errorf("bad character range")
		}
		members.WriteString(classRune(low) + "-" + classRune(high))
		ranges = append(ranges, [2]rune{low, high})
	}
	t.out.WriteString(members.String())
	if t.folding() {
		for _, key := range caseExtraKeys {
			if slices.Contains(singles, key) || slices.ContainsFunc(ranges, func(r [2]rune) bool { return r[0] <= key && key <= r[1] }) {
				for _, extra := range caseExtras[key] {
					t.out.WriteString(classRune(extra))
				}
			}
		}
	}
	t.out.WriteByte(']')
	return nil
}

func (t *translator) classItem(c rune) (rune, string, error) {
	if c != '\\' {
		return c, "", nil
	}
	c, err := t.next()
	if err != nil {
		return 0, "", fmt.Errorf("unterminated character set")
	}
	switch c {
	case 'd':
		return 0, `\d`, nil
	case 'D':
		return 0, `\D`, nil
	case 'w':
		return 0, wordMembers, nil
	case 's':
		return 0, spaceMembers, nil
	case 'W', 'S':
		return 0, "", fmt.Errorf(`\%c inside a character set is unsupported`, c)
	case 'b':
		return '\b', "", nil
	}
	r, err := t.charEscape(c, true)
	return r, "", err
}
