// Package pyre runs Python re patterns, verbatim, on Go strings with Python's semantics.
//
// A pattern keeps its Python source byte for byte; pyre translates it to a regexp2 (.NET)
// pattern at compile time and wraps the result in Python's API. The translation closes every
// semantic gap between the two engines that a str pattern can reach:
//
//   - (?P<name>...) and (?P=name) become numbered .NET groups, so group numbers follow Python's
//     left-to-right order even where .NET would number named groups after unnamed ones.
//   - \Z becomes \z; $ already matches before a final newline in both engines.
//   - \w, \W and \b use Python's word set, Unicode letters and numbers plus "_", where .NET adds
//     nonspacing marks, connector punctuation and the zero-width joiners and drops Nl and No.
//   - \s and \S add Python's \x1c-\x1f separators; \d is Unicode Nd in both engines.
//   - {,n} and {,} are quantifiers, as in Python; a brace that does not open a Python quantifier
//     is a literal, and a class's "[" is escaped so .NET never reads class subtraction.
//   - Under IGNORECASE, letters Python folds together beyond simple lowercasing (i and ı,
//     s and ſ, µ and μ, and the rest of Python's re._casefix table) match each other.
//   - VERBOSE whitespace and comments are stripped during translation, so a trailing comment can
//     never swallow the anchors Match and FullMatch add.
//   - FindAll, FindIter, Sub and Split step past empty matches the way Python 3.7+ does: a
//     non-empty match may start where an empty one just matched.
//
// Constructs with no exact equivalent fail to compile instead of drifting: \B, \N{name},
// possessive quantifiers, scoped inline flags, \W or \S inside a character set, and the ASCII,
// LOCALE and DEBUG flags. Unicode classification comes from Go's unicode tables, so code points
// assigned after Go's Unicode version and before Python's can classify differently.
//
// Offsets on a Match are Python code-point offsets (Start, End) or byte offsets into the Go
// string (ByteStart, ByteEnd).
package pyre

import (
	"fmt"
	"maps"
	"strconv"
	"strings"
	"sync"

	"github.com/dlclark/regexp2"
)

// Python re flag values, as they appear in re.Pattern.flags.
const (
	IgnoreCase = 2
	Locale     = 4
	Multiline  = 8
	DotAll     = 16
	Unicode    = 32
	Verbose    = 64
	ASCII      = 256
)

// Pattern is the Python source and re.Pattern.flags a key was registered with.
type Pattern struct {
	Source string
	Flags  int
}

var registry = struct {
	sync.Mutex
	patterns map[string]Pattern
}{patterns: map[string]Pattern{}}

// Registered returns every pattern registered through Compile or MustCompile, by key.
func Registered() map[string]Pattern {
	registry.Lock()
	defer registry.Unlock()
	return maps.Clone(registry.patterns)
}

func register(key string, pattern Pattern) {
	registry.Lock()
	defer registry.Unlock()
	if existing, ok := registry.patterns[key]; ok && existing != pattern {
		panic(fmt.Sprintf("pyre: %s registered as %q flags %d, then as %q flags %d", key, existing.Source, existing.Flags, pattern.Source, pattern.Flags))
	}
	registry.patterns[key] = pattern
}

// Compile registers source and flags under key, the golden key such as
// "desk-runner.py:MUTE", and compiles them. Source is the Python pattern string verbatim and
// flags its re.Pattern.flags. Registering a key again with a different source or flags panics.
func Compile(key, source string, flags int) (*Regexp, error) {
	register(key, Pattern{Source: source, Flags: flags})
	return CompileDynamic(source, flags)
}

// MustCompile is Compile that panics when the pattern does not compile.
func MustCompile(key, source string, flags int) *Regexp {
	re, err := Compile(key, source, flags)
	if err != nil {
		panic(err)
	}
	return re
}

// CompileDynamic compiles a Python pattern without registering it, for patterns built at run
// time the way Python builds f-string patterns. Flags may omit Unicode.
func CompileDynamic(source string, flags int) (*Regexp, error) {
	translated, err := translate(source, flags)
	if err != nil {
		return nil, err
	}
	search, err := regexp2.Compile(translated.source, translated.options)
	if err != nil {
		return nil, fmt.Errorf("pyre: %q translated to %q: %w", source, translated.source, err)
	}
	re := &Regexp{pattern: source, translation: translated, search: search}
	re.anchored = re.variant(`\A(?:`, `)`)
	re.full = re.variant(`\A(?:`, `)\z`)
	re.advancing = re.variant(`\G(?:`, `)(?!\G)`)
	return re, nil
}

// MustCompileDynamic is CompileDynamic that panics when the pattern does not compile.
func MustCompileDynamic(source string, flags int) *Regexp {
	re, err := CompileDynamic(source, flags)
	if err != nil {
		panic(err)
	}
	return re
}

// Escape is Python's re.escape.
func Escape(s string) string {
	var out strings.Builder
	for _, c := range s {
		if strings.ContainsRune("()[]{}?*+-|^$\\.&~# \t\n\r\v\f", c) {
			out.WriteByte('\\')
		}
		out.WriteRune(c)
	}
	return out.String()
}

// Regexp is a compiled Python pattern. It is safe for concurrent use.
type Regexp struct {
	pattern     string
	translation translation
	search      *regexp2.Regexp
	anchored    func() *regexp2.Regexp
	full        func() *regexp2.Regexp
	advancing   func() *regexp2.Regexp
}

func (re *Regexp) variant(prefix, suffix string) func() *regexp2.Regexp {
	return sync.OnceValue(func() *regexp2.Regexp {
		return regexp2.MustCompile(prefix+re.translation.source+suffix, re.translation.options)
	})
}

// Pattern returns the Python source the pattern was compiled from.
func (re *Regexp) Pattern() string {
	return re.pattern
}

// Flags returns Python's re.Pattern.flags: the given flags, inline flags, and Unicode.
func (re *Regexp) Flags() int {
	return re.translation.flags
}

// Groups returns the number of capturing groups, Python's re.Pattern.groups.
func (re *Regexp) Groups() int {
	return re.translation.groups
}

// GroupIndex maps each named group to its number, Python's re.Pattern.groupindex.
func (re *Regexp) GroupIndex() map[string]int {
	return maps.Clone(re.translation.names)
}

func (re *Regexp) index(name string) int {
	group, ok := re.translation.names[name]
	if !ok {
		panic(fmt.Sprintf("pyre: %q has no group named %q", re.pattern, name))
	}
	return group
}

// Search returns the first match anywhere in s, Python's re.Pattern.search, or nil.
func (re *Regexp) Search(s string) *Match {
	return re.find(re.search, newSubject(s), 0)
}

// Match returns the match that starts at the beginning of s, Python's re.Pattern.match, or nil.
func (re *Regexp) Match(s string) *Match {
	return re.find(re.anchored(), newSubject(s), 0)
}

// FullMatch returns the match that spans all of s, Python's re.Pattern.fullmatch, or nil.
func (re *Regexp) FullMatch(s string) *Match {
	return re.find(re.full(), newSubject(s), 0)
}

// FindIter returns every non-overlapping match in s, Python's re.Pattern.finditer.
func (re *Regexp) FindIter(s string) []*Match {
	var matches []*Match
	re.each(newSubject(s), 0, func(m *Match) {
		matches = append(matches, m)
	})
	return matches
}

// FindAll is Python's re.Pattern.findall for a pattern with at most one group: each match's
// text, or its one group's text ("" where the group did not participate). It panics for a
// pattern with more groups, whose Python findall returns tuples; use FindAllGroups.
func (re *Regexp) FindAll(s string) []string {
	if re.Groups() > 1 {
		panic(fmt.Sprintf("pyre: %q has %d groups; FindAll returns tuples, use FindAllGroups", re.pattern, re.Groups()))
	}
	var found []string
	re.each(newSubject(s), 0, func(m *Match) {
		found = append(found, m.Group(re.Groups()))
	})
	return found
}

// FindAllGroups is Python's re.Pattern.findall for a pattern with groups: each match's groups,
// "" where a group did not participate.
func (re *Regexp) FindAllGroups(s string) [][]string {
	var found [][]string
	re.each(newSubject(s), 0, func(m *Match) {
		found = append(found, m.Groups())
	})
	return found
}

// Sub replaces every match in s with the Python replacement template repl, Python's
// re.Pattern.sub. It panics on a template Python rejects.
func (re *Regexp) Sub(repl, s string) string {
	out, _ := re.SubN(repl, s, 0)
	return out
}

// SubN replaces up to count matches (all when count is 0) with the Python replacement
// template repl and reports how many it replaced, Python's re.Pattern.subn.
func (re *Regexp) SubN(repl, s string, count int) (string, int) {
	template := re.template(repl)
	return re.SubFuncN(func(m *Match) string { return m.expand(template) }, s, count)
}

// SubFunc replaces every match in s with fn's result, Python's re.Pattern.sub with a callable.
func (re *Regexp) SubFunc(fn func(*Match) string, s string) string {
	out, _ := re.SubFuncN(fn, s, 0)
	return out
}

// SubFuncN replaces up to count matches (all when count is 0) with fn's result and reports how
// many it replaced, Python's re.Pattern.subn with a callable.
func (re *Regexp) SubFuncN(fn func(*Match) string, s string, count int) (string, int) {
	subject := newSubject(s)
	var out strings.Builder
	last, n := 0, 0
	re.each(subject, count, func(m *Match) {
		out.WriteString(subject.slice(last, m.spans[0]))
		out.WriteString(fn(m))
		last = m.spans[1]
		n++
	})
	out.WriteString(subject.slice(last, len(subject.runes)))
	return out.String(), n
}

// Split splits s around up to maxsplit matches (all when maxsplit is 0), Python's
// re.Pattern.split: each match's groups are kept between the pieces, "" where a group did not
// participate.
func (re *Regexp) Split(s string, maxsplit int) []string {
	subject := newSubject(s)
	var pieces []string
	last := 0
	re.each(subject, maxsplit, func(m *Match) {
		pieces = append(pieces, subject.slice(last, m.spans[0]))
		pieces = append(pieces, m.Groups()...)
		last = m.spans[1]
	})
	return append(pieces, subject.slice(last, len(subject.runes)))
}

func (re *Regexp) each(subject *subject, limit int, yield func(*Match)) {
	pos, n := 0, 0
	advance := false
	for (limit == 0 || n < limit) && pos <= len(subject.runes) {
		var m *Match
		if advance {
			m = re.find(re.advancing(), subject, pos)
			if m == nil && pos < len(subject.runes) {
				m = re.find(re.search, subject, pos+1)
			}
		} else {
			m = re.find(re.search, subject, pos)
		}
		if m == nil {
			return
		}
		yield(m)
		n++
		advance = m.spans[0] == m.spans[1]
		pos = m.spans[1]
	}
}

func (re *Regexp) find(rx *regexp2.Regexp, subject *subject, pos int) *Match {
	found, err := rx.FindRunesMatchStartingAt(subject.runes, pos)
	if err != nil {
		panic(fmt.Sprintf("pyre: %q: %v", re.pattern, err))
	}
	if found == nil {
		return nil
	}
	spans := make([]int, 2*(re.Groups()+1))
	for group := range re.Groups() + 1 {
		spans[2*group], spans[2*group+1] = -1, -1
		if captured := found.GroupByNumber(group); captured != nil && len(captured.Captures) > 0 {
			spans[2*group], spans[2*group+1] = captured.Index, captured.Index+captured.Length
		}
	}
	return &Match{re: re, subject: subject, spans: spans}
}

type subject struct {
	s       string
	runes   []rune
	offsets []int
}

func newSubject(s string) *subject {
	runes := make([]rune, 0, len(s))
	offsets := make([]int, 0, len(s)+1)
	for i, c := range s {
		runes = append(runes, c)
		offsets = append(offsets, i)
	}
	return &subject{s: s, runes: runes, offsets: append(offsets, len(s))}
}

func (s *subject) slice(start, end int) string {
	return s.s[s.offsets[start]:s.offsets[end]]
}

// Match is one match of a Regexp, Python's re.Match. Group 0 is the whole match.
type Match struct {
	re      *Regexp
	subject *subject
	spans   []int
}

// Group returns group i's text, "" when it did not participate (Python's None).
func (m *Match) Group(i int) string {
	text, _ := m.Lookup(i)
	return text
}

// Named returns the named group's text, "" when it did not participate (Python's None).
func (m *Match) Named(name string) string {
	return m.Group(m.re.index(name))
}

// Lookup returns group i's text and whether it participated; Python's None is false.
func (m *Match) Lookup(i int) (string, bool) {
	start, end := m.Start(i), m.End(i)
	if start < 0 {
		return "", false
	}
	return m.subject.slice(start, end), true
}

// LookupNamed returns the named group's text and whether it participated.
func (m *Match) LookupNamed(name string) (string, bool) {
	return m.Lookup(m.re.index(name))
}

// Groups returns groups 1 through Groups(), "" for each that did not participate, Python's
// re.Match.groups(default="").
func (m *Match) Groups() []string {
	groups := make([]string, m.re.Groups())
	for i := range groups {
		groups[i] = m.Group(i + 1)
	}
	return groups
}

// GroupDict returns each named group that participated, by name; a group that did not
// participate (Python's None) is absent.
func (m *Match) GroupDict() map[string]string {
	dict := map[string]string{}
	for name, group := range m.re.translation.names {
		if text, ok := m.Lookup(group); ok {
			dict[name] = text
		}
	}
	return dict
}

// Start returns group i's start as a code-point offset, Python's re.Match.start, or -1 when
// the group did not participate.
func (m *Match) Start(i int) int {
	m.check(i)
	return m.spans[2*i]
}

// End returns group i's end as a code-point offset, Python's re.Match.end, or -1 when the
// group did not participate.
func (m *Match) End(i int) int {
	m.check(i)
	return m.spans[2*i+1]
}

// ByteStart returns group i's start as a byte offset into the Go string, or -1 when the group
// did not participate.
func (m *Match) ByteStart(i int) int {
	if start := m.Start(i); start >= 0 {
		return m.subject.offsets[start]
	}
	return -1
}

// ByteEnd returns group i's end as a byte offset into the Go string, or -1 when the group did
// not participate.
func (m *Match) ByteEnd(i int) int {
	if end := m.End(i); end >= 0 {
		return m.subject.offsets[end]
	}
	return -1
}

func (m *Match) check(i int) {
	if i < 0 || i > m.re.Groups() {
		panic(fmt.Sprintf("pyre: %q has no group %d", m.re.pattern, i))
	}
}

// Expand returns the Python replacement template filled from this match, Python's
// re.Match.expand. It panics on a template Python rejects.
func (m *Match) Expand(template string) string {
	return m.expand(m.re.template(template))
}

type piece struct {
	text  string
	group int
}

func (m *Match) expand(template []piece) string {
	var out strings.Builder
	for _, part := range template {
		if part.group < 0 {
			out.WriteString(part.text)
		} else {
			out.WriteString(m.Group(part.group))
		}
	}
	return out.String()
}

func (re *Regexp) template(repl string) []piece {
	var pieces []piece
	var literal strings.Builder
	flush := func() {
		if literal.Len() > 0 {
			pieces = append(pieces, piece{text: literal.String(), group: -1})
			literal.Reset()
		}
	}
	group := func(n int) {
		if n > re.Groups() {
			panic(fmt.Sprintf("pyre: template %q: invalid group reference %d", repl, n))
		}
		flush()
		pieces = append(pieces, piece{group: n})
	}
	src := []rune(repl)
	for i := 0; i < len(src); i++ {
		if src[i] != '\\' {
			literal.WriteRune(src[i])
			continue
		}
		i++
		if i == len(src) {
			panic(fmt.Sprintf("pyre: template %q ends with a lone backslash", repl))
		}
		c := src[i]
		switch {
		case c == 'g':
			end := i + 1
			for end < len(src) && src[end] != '>' {
				end++
			}
			if i+1 >= len(src) || src[i+1] != '<' || end == len(src) {
				panic(fmt.Sprintf("pyre: template %q: malformed \\g<...>", repl))
			}
			name := string(src[i+2 : end])
			if n, err := strconv.Atoi(name); err == nil && strings.Trim(name, "0123456789") == "" {
				group(n)
			} else {
				group(re.index(name))
			}
			i = end
		case c == '0':
			digits := "0"
			for len(digits) < 3 && i+1 < len(src) && isOctal(src[i+1]) {
				i++
				digits += string(src[i])
			}
			value, _ := strconv.ParseUint(digits, 8, 32)
			literal.WriteRune(rune(value & 0xff))
		case c >= '1' && c <= '9':
			digits := string(c)
			if i+1 < len(src) && src[i+1] >= '0' && src[i+1] <= '9' {
				if isOctal(c) && isOctal(src[i+1]) && i+2 < len(src) && isOctal(src[i+2]) {
					value, _ := strconv.ParseUint(string(src[i:i+3]), 8, 32)
					if value > 0o377 {
						panic(fmt.Sprintf("pyre: template %q: octal escape outside 0o0-0o377", repl))
					}
					literal.WriteRune(rune(value))
					i += 2
					continue
				}
				i++
				digits += string(src[i])
			}
			n, _ := strconv.Atoi(digits)
			group(n)
		default:
			if escaped, ok := templateEscapes[c]; ok {
				literal.WriteRune(escaped)
			} else if c < 0x80 && ('a' <= c && c <= 'z' || 'A' <= c && c <= 'Z') {
				panic(fmt.Sprintf("pyre: template %q: bad escape \\%c", repl, c))
			} else {
				literal.WriteRune('\\')
				literal.WriteRune(c)
			}
		}
	}
	flush()
	return pieces
}

var templateEscapes = map[rune]rune{'a': '\a', 'b': '\b', 'f': '\f', 'n': '\n', 'r': '\r', 't': '\t', 'v': '\v', '\\': '\\'}
