package pyre

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
)

type span struct {
	start, end, byteStart, byteEnd int
	text                           string
}

type matchCase struct {
	name    string
	pattern string
	flags   int
	pyFlags int
	op      string
	input   string
	want    []span
}

type findAllCase struct {
	name    string
	pattern string
	flags   int
	input   string
	want    [][]string
}

type subCase struct {
	name    string
	pattern string
	flags   int
	repl    string
	input   string
	count   int
	want    string
	wantN   int
}

type splitCase struct {
	name     string
	pattern  string
	flags    int
	input    string
	maxsplit int
	want     []string
}

func TestMatchesAgreeWithPython(t *testing.T) {
	for _, c := range matchCases {
		t.Run(c.name, func(t *testing.T) {
			re := MustCompileDynamic(c.pattern, c.flags)
			if re.Flags() != c.pyFlags {
				t.Errorf("Flags() = %d, Python %d", re.Flags(), c.pyFlags)
			}
			var m *Match
			switch c.op {
			case "search":
				m = re.Search(c.input)
			case "match":
				m = re.Match(c.input)
			case "fullmatch":
				m = re.FullMatch(c.input)
			}
			if c.want == nil {
				if m != nil {
					t.Fatalf("%s(%q) matched %q, Python found none", c.op, c.input, m.Group(0))
				}
				return
			}
			if m == nil {
				t.Fatalf("%s(%q) found no match, Python matched %q", c.op, c.input, c.want[0].text)
			}
			if re.Groups() != len(c.want)-1 {
				t.Fatalf("Groups() = %d, Python %d", re.Groups(), len(c.want)-1)
			}
			for i, want := range c.want {
				text, ok := m.Lookup(i)
				got := span{m.Start(i), m.End(i), m.ByteStart(i), m.ByteEnd(i), text}
				if got != want || ok != (want.start >= 0) {
					t.Errorf("group %d = %+v (participated %v), Python %+v", i, got, ok, want)
				}
				if ok && c.input[m.ByteStart(i):m.ByteEnd(i)] != text {
					t.Errorf("group %d byte offsets slice %q, text %q", i, c.input[m.ByteStart(i):m.ByteEnd(i)], text)
				}
			}
		})
	}
}

func TestFindAllAgreesWithPython(t *testing.T) {
	for _, c := range findAllCases {
		t.Run(c.name, func(t *testing.T) {
			re := MustCompileDynamic(c.pattern, c.flags)
			var got [][]string
			if re.Groups() > 1 {
				got = re.FindAllGroups(c.input)
			} else {
				for _, found := range re.FindAll(c.input) {
					got = append(got, []string{found})
				}
			}
			if !slices.EqualFunc(got, c.want, slices.Equal) {
				t.Errorf("findall(%q) = %q, Python %q", c.input, got, c.want)
			}
			if n := len(re.FindIter(c.input)); n != len(c.want) {
				t.Errorf("finditer(%q) yields %d matches, Python %d", c.input, n, len(c.want))
			}
		})
	}
}

func TestSubAgreesWithPython(t *testing.T) {
	for _, c := range subCases {
		t.Run(c.name, func(t *testing.T) {
			re := MustCompileDynamic(c.pattern, c.flags)
			got, n := re.SubN(c.repl, c.input, c.count)
			if got != c.want || n != c.wantN {
				t.Errorf("subn(%q, %q, %d) = %q, %d; Python %q, %d", c.repl, c.input, c.count, got, n, c.want, c.wantN)
			}
			if c.count == 0 && re.Sub(c.repl, c.input) != c.want {
				t.Errorf("sub(%q, %q) = %q, Python %q", c.repl, c.input, re.Sub(c.repl, c.input), c.want)
			}
		})
	}
}

func TestSplitAgreesWithPython(t *testing.T) {
	for _, c := range splitCases {
		t.Run(c.name, func(t *testing.T) {
			if got := MustCompileDynamic(c.pattern, c.flags).Split(c.input, c.maxsplit); !slices.Equal(got, c.want) {
				t.Errorf("split(%q, %d) = %q, Python %q", c.input, c.maxsplit, got, c.want)
			}
		})
	}
}

func TestSubFuncSeesEachMatch(t *testing.T) {
	re := MustCompileDynamic(`(?P<n>\d+)`, Unicode)
	got, n := re.SubFuncN(func(m *Match) string { return "<" + m.Named("n") + ">" }, "a1b22c333", 2)
	if got != "a<1>b<22>c333" || n != 2 {
		t.Errorf("SubFuncN = %q, %d", got, n)
	}
	if got := re.SubFunc(func(m *Match) string { return m.Expand(`[\g<n>]`) }, "a1b22"); got != "a[1]b[22]" {
		t.Errorf("SubFunc with Expand = %q", got)
	}
}

func TestNamedGroupAccessors(t *testing.T) {
	re := MustCompileDynamic(`(?P<a>x)|(?P<b>y)`, Unicode)
	m := re.Search("zy")
	if got := m.GroupDict(); len(got) != 1 || got["b"] != "y" {
		t.Errorf("GroupDict() = %q, want only b", got)
	}
	if text, ok := m.LookupNamed("a"); ok || text != "" || m.Named("a") != "" {
		t.Errorf("LookupNamed(a) = %q, %v; want a non-participating group", text, ok)
	}
	if got := m.Groups(); !slices.Equal(got, []string{"", "y"}) {
		t.Errorf("Groups() = %q", got)
	}
	if got := re.GroupIndex(); len(got) != 2 || got["a"] != 1 || got["b"] != 2 {
		t.Errorf("GroupIndex() = %v", got)
	}
	if m.Start(1) != -1 || m.ByteEnd(1) != -1 {
		t.Errorf("non-participating group offsets = %d, %d", m.Start(1), m.ByteEnd(1))
	}
}

func TestFindAllRefusesTuples(t *testing.T) {
	defer func() {
		if recover() == nil {
			t.Error("FindAll on a two-group pattern did not panic")
		}
	}()
	MustCompileDynamic(`(a)(b)`, Unicode).FindAll("ab")
}

func TestTranslation(t *testing.T) {
	for _, c := range []struct {
		pattern string
		flags   int
		want    string
	}{
		{`(?P<x>a)(?P=x)`, Unicode, `(?<1>a)\k<1>`},
		{`(a)(?P<x>b)(c)\1`, Unicode, `(?<1>a)(?<2>b)(?<3>c)\k<1>`},
		{`a\Z`, Unicode, `a\z`},
		{`\A^a$`, Unicode, `\A^a$`},
		{`x{,3}y{,}z{2,}w{1,2}?`, Unicode, `x{0,3}y{0,}z{2,}w{1,2}?`},
		{`a{b}{}`, Unicode, `a\{b\}\{\}`},
		{`[[]`, Unicode, `[\[]`},
		{`[\w.-]`, Unicode, `[\p{L}\p{N}_.\-]`},
		{`[^\]\d\s]`, Unicode, `[^\]\d\s\x1c-\x1f]`},
		{`\w\W\s\S\d\D`, Unicode, word + notWord + space + notSpace + `\d\D`},
		{`\b`, Unicode, boundary},
		{"a # note\n b", Unicode | Verbose, `ab`},
		{`is`, Unicode | IgnoreCase, `[iı][sſ]`},
		{`[a-z]`, Unicode | IgnoreCase, `[a-zıſ]`},
		{`(?i)k`, Unicode, `k`},
		{`(?#note)a(?:b)(?=c)(?!d)(?<=e)(?<!f)(?>g)`, Unicode, `a(?:b)(?=c)(?!d)(?<=e)(?<!f)(?>g)`},
		{`(?P<o>a)?(?(o)b|c)`, Unicode, `(?<1>a)?(?(1)b|c)`},
		{"\\x1b\\n\\t\u00a0\u2028", Unicode, `\x1b\x0a\x09\xa0\u2028`},
		{`.*\+?|()#`, Unicode, `.*\+?|(?<1>)\#`},
	} {
		got, err := translate(c.pattern, c.flags)
		if err != nil {
			t.Errorf("translate(%q): %v", c.pattern, err)
			continue
		}
		if got.source != c.want {
			t.Errorf("translate(%q) = %q, want %q", c.pattern, got.source, c.want)
		}
	}
}

func TestRejectsWhatRegexp2CannotMatch(t *testing.T) {
	for _, c := range []struct {
		pattern string
		flags   int
		why     string
	}{
		{`\B`, Unicode, `Python 3.13 and 3.14 disagree on \B over empty input`},
		{`\N{DIGIT ONE}`, Unicode, "no Unicode name table"},
		{`a*+`, Unicode, "no possessive quantifiers in regexp2"},
		{`a{2}+`, Unicode, "no possessive quantifiers in regexp2"},
		{`(?i:a)b`, Unicode, "scoped flags would need per-scope translation"},
		{`(?-i:a)b`, Unicode | IgnoreCase, "scoped flags would need per-scope translation"},
		{`[\W.]`, Unicode, `\W inside a set is a union with a complement`},
		{`[\S.]`, Unicode, `\S inside a set is a union with a complement`},
		{`a`, Unicode | ASCII, "ASCII classes are not translated"},
		{`(?a)a`, Unicode, "ASCII classes are not translated"},
		{`a`, Unicode | Locale, "LOCALE has no equivalent"},
		{`a`, Unicode | 128, "DEBUG has no equivalent"},
		{`a(?i)`, Unicode, "Python rejects global flags after the start"},
	} {
		if _, err := CompileDynamic(c.pattern, c.flags); err == nil {
			t.Errorf("CompileDynamic(%q, %d) compiled; want a refusal: %s", c.pattern, c.flags, c.why)
		}
	}
}

func TestEscapeIsPythonReEscape(t *testing.T) {
	if got := Escape("a.b*c d-#é"); got != `a\.b\*c\ d\-\#é` {
		t.Errorf("Escape = %q", got)
	}
	tricky := "1.5*(x+y) [a-b] {2} ^$|? \\ #~&\t\n\v\f\r café"
	if MustCompileDynamic(Escape(tricky), Unicode).FullMatch(tricky) == nil {
		t.Errorf("Escape(%q) does not match itself", tricky)
	}
	if MustCompileDynamic(Escape(tricky), Unicode|Verbose).FullMatch(tricky) == nil {
		t.Errorf("Escape(%q) does not match itself under VERBOSE", tricky)
	}
}

func TestRegistryRefusesConflictingRegistration(t *testing.T) {
	MustCompile("pyre_test:same", "a", Unicode)
	MustCompile("pyre_test:same", "a", Unicode)
	if got := Registered()["pyre_test:same"]; got != (Pattern{Source: "a", Flags: Unicode}) {
		t.Errorf("Registered()[pyre_test:same] = %+v", got)
	}
	for _, conflict := range []Pattern{{"b", Unicode}, {"a", Unicode | IgnoreCase}} {
		func() {
			defer func() {
				if recover() == nil {
					t.Errorf("re-registering as %+v did not panic", conflict)
				}
			}()
			MustCompile("pyre_test:same", conflict.Source, conflict.Flags)
		}()
	}
}

type recorder struct {
	testing.TB
	errors []string
}

func (r *recorder) Errorf(format string, args ...any) {
	r.errors = append(r.errors, fmt.Sprintf(format, args...))
}

func TestCheckGoldenReportsDrift(t *testing.T) {
	MustCompile("golden_test:same", "a", Unicode)
	MustCompile("golden_test:source", "b", Unicode)
	MustCompile("golden_test:flags", "c", Unicode|IgnoreCase)
	MustCompile("golden_test:missing", "d", Unicode)
	data, err := json.Marshal(map[string]Golden{
		"golden_test:same":   {Pattern: "a", Flags: Unicode},
		"golden_test:source": {Pattern: "B", Flags: Unicode},
		"golden_test:flags":  {Pattern: "c", Flags: Unicode},
	})
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "patterns.json")
	if err := os.WriteFile(path, data, 0o644); err != nil {
		t.Fatal(err)
	}
	r := &recorder{TB: t}
	CheckGolden(r, path)
	var drifted []string
	for _, message := range r.errors {
		if strings.HasPrefix(message, "golden_test:") {
			drifted = append(drifted, strings.SplitN(message, ":", 3)[1])
		}
	}
	slices.Sort(drifted)
	if want := []string{"flags", "missing", "source"}; !slices.Equal(drifted, want) {
		t.Errorf("CheckGolden reported %q (%q), want %q", drifted, r.errors, want)
	}
}

var matchCases = []matchCase{
	{"numbering follows Python order", "(a)(?P<x>b)(c)", 0, 32, "search", "zabc", []span{{1, 4, 1, 4, "abc"}, {1, 2, 1, 2, "a"}, {2, 3, 2, 3, "b"}, {3, 4, 3, 4, "c"}}},
	{"named backref", "(?P<q>['\"]).*?(?P=q)", 0, 32, "search", "say \"hi' there\" ok", []span{{4, 15, 4, 15, "\"hi' there\""}, {4, 5, 4, 5, "\""}}},
	{"numbered backref", "(a)\\1", 0, 32, "search", "xaa", []span{{1, 3, 1, 3, "aa"}, {1, 2, 1, 2, "a"}}},
	{"optional backtick backref present", "^(?P<tick>`?)UNFIXED (?P<case>\\S.*?)(?P=tick)$", 0, 32, "search", "`UNFIXED x y`", []span{{0, 13, 0, 13, "`UNFIXED x y`"}, {0, 1, 0, 1, "`"}, {9, 12, 9, 12, "x y"}}},
	{"optional backtick backref absent", "^(?P<tick>`?)UNFIXED (?P<case>\\S.*?)(?P=tick)$", 0, 32, "search", "UNFIXED x y", []span{{0, 11, 0, 11, "UNFIXED x y"}, {0, 0, 0, 0, ""}, {8, 11, 8, 11, "x y"}}},
	{"backslash Z is absolute end", "a\\Z", 0, 32, "search", "a\n", nil},
	{"dollar matches before a final newline", "a$", 0, 32, "search", "a\n", []span{{0, 1, 0, 1, "a"}}},
	{"dollar is not before an inner newline", "a$", 0, 32, "search", "a\nb", nil},
	{"negative lookbehind and lookahead", "(?<![\\w/-])mut(?:e|ed)(?![\\w/-])", 0, 32, "search", "unmute /mute mute", []span{{13, 17, 13, 17, "mute"}}},
	{"positive lookbehind", "(?<=#)\\d+", 0, 32, "search", "pr #123", []span{{4, 7, 4, 7, "123"}}},
	{"negative lookahead", "foo(?!bar)", 0, 32, "search", "foobar foobaz", []span{{7, 10, 7, 10, "foo"}}},
	{"ignorecase", "hold\\s+#(\\d+)", 2, 34, "search", "please HOLD   #12 now", []span{{7, 17, 7, 17, "HOLD   #12"}, {15, 17, 15, 17, "12"}}},
	{"multiline caret", "^y$", 8, 40, "search", "x\ny\nz", []span{{2, 3, 2, 3, "y"}}},
	{"dotall dot crosses newlines", "a.b", 16, 48, "search", "a\nb", []span{{0, 3, 0, 3, "a\nb"}}},
	{"dot stops at newlines without dotall", "a.b", 0, 32, "search", "a\nb", nil},
	{"verbose strips whitespace and comments", "a b # comment\n c", 64, 96, "fullmatch", "abc", []span{{0, 3, 0, 3, "abc"}}},
	{"verbose trailing comment under fullmatch", "\\d+  # digits", 64, 96, "fullmatch", "123", []span{{0, 3, 0, 3, "123"}}},
	{"verbose trailing comment under fullmatch rejects", "\\d+  # digits", 64, 96, "fullmatch", "123x", nil},
	{"verbose trailing comment under match", "\\d+  # digits", 64, 96, "match", "123x", []span{{0, 3, 0, 3, "123"}}},
	{"verbose keeps escaped space and hash", "a\\ b\\#c", 64, 96, "fullmatch", "a b#c", []span{{0, 5, 0, 5, "a b#c"}}},
	{"verbose keeps class whitespace", "[ ]x", 64, 96, "fullmatch", " x", []span{{0, 2, 0, 2, " x"}}},
	{"verbose comment continues past escaped newline", "a#x\\\nb\nc", 64, 96, "fullmatch", "ac", []span{{0, 2, 0, 2, "ac"}}},
	{"inline global flags", "(?i)abc", 0, 34, "search", "xABC", []span{{1, 4, 1, 4, "ABC"}}},
	{"inline verbose flag", "(?x) a b", 0, 96, "fullmatch", "ab", []span{{0, 2, 0, 2, "ab"}}},
	{"match is anchored at the start", "b", 0, 32, "match", "ab", nil},
	{"fullmatch backtracks into alternatives", "a|ab", 0, 32, "fullmatch", "ab", []span{{0, 2, 0, 2, "ab"}}},
	{"fullmatch rejects a final newline", "a$", 0, 32, "fullmatch", "a\n", nil},
	{"word boundary ignores zero width joiner", "\\bTAP\\b", 0, 32, "search", "x\u200dTAP", []span{{2, 5, 4, 7, "TAP"}}},
	{"word boundary after combining mark", "\\bb", 0, 32, "search", "a\u0301b", []span{{2, 3, 3, 4, "b"}}},
	{"superscript digit is a word character", "\\w+", 0, 32, "search", "x²y", []span{{0, 3, 0, 4, "x²y"}}},
	{"connector punctuation is not a word character", "\\w+", 0, 32, "search", "‿ab", []span{{1, 3, 3, 5, "ab"}}},
	{"unit separator is whitespace", "a\\sb", 0, 32, "fullmatch", "a\x1fb", []span{{0, 3, 0, 3, "a\x1fb"}}},
	{"non-breaking space is whitespace", "a\\sb", 0, 32, "fullmatch", "a\u00a0b", []span{{0, 3, 0, 4, "a\u00a0b"}}},
	{"Arabic-Indic digits are digits", "\\d+", 0, 32, "fullmatch", "٣4", []span{{0, 2, 0, 3, "٣4"}}},
	{"ignorecase dotless i and long s", "mission", 2, 34, "fullmatch", "MıſSıON", []span{{0, 7, 0, 10, "MıſSıON"}}},
	{"ignorecase long s alone", "s", 2, 34, "fullmatch", "ſ", []span{{0, 1, 0, 2, "ſ"}}},
	{"ignorecase Kelvin sign", "k", 2, 34, "fullmatch", "\u212a", []span{{0, 1, 0, 3, "\u212a"}}},
	{"ignorecase class range extras", "[a-z]+", 2, 34, "fullmatch", "ıſ\u212aQ", []span{{0, 4, 0, 8, "ıſ\u212aQ"}}},
	{"ignorecase negated class extras", "[^s]", 2, 34, "search", "ſS", nil},
	{"ignorecase dotted capital I", "i", 2, 34, "fullmatch", "İ", []span{{0, 1, 0, 2, "İ"}}},
	{"min-omitted quantifier", "x{,2}y", 0, 32, "fullmatch", "xxy", []span{{0, 3, 0, 3, "xxy"}}},
	{"open quantifier", "a{,}", 0, 32, "fullmatch", "aaa", []span{{0, 3, 0, 3, "aaa"}}},
	{"exact quantifier", "x{2}", 0, 32, "fullmatch", "xx", []span{{0, 2, 0, 2, "xx"}}},
	{"unclosed brace is literal", "a{", 0, 32, "fullmatch", "a{", []span{{0, 2, 0, 2, "a{"}}},
	{"empty braces are literal", "a{}", 0, 32, "fullmatch", "a{}", []span{{0, 3, 0, 3, "a{}"}}},
	{"non-numeric braces are literal", "a{x}", 0, 32, "fullmatch", "a{x}", []span{{0, 4, 0, 4, "a{x}"}}},
	{"lazy bounded quantifier", "a{1,3}?", 0, 32, "match", "aaa", []span{{0, 1, 0, 1, "a"}}},
	{"leading bracket is literal in a class", "[]a]+", 0, 32, "fullmatch", "]a]", []span{{0, 3, 0, 3, "]a]"}}},
	{"trailing dash is literal in a class", "[\\w.-]+", 0, 32, "fullmatch", "a.b-c", []span{{0, 5, 0, 5, "a.b-c"}}},
	{"leading dash is literal in a class", "[-_.0-9]+", 0, 32, "fullmatch", "-_.09", []span{{0, 5, 0, 5, "-_.09"}}},
	{"open bracket in a class is literal", "[[a-]+", 0, 32, "fullmatch", "[a-[", []span{{0, 4, 0, 4, "[a-["}}},
	{"dash then bracket is not subtraction", "[a-c-[]+", 0, 32, "fullmatch", "b-[", []span{{0, 3, 0, 3, "b-["}}},
	{"backspace escape in a class", "[\\b]", 0, 32, "fullmatch", "\b", []span{{0, 1, 0, 1, "\b"}}},
	{"octal escape in a class", "[\\101]", 0, 32, "fullmatch", "A", []span{{0, 1, 0, 1, "A"}}},
	{"octal escape outside a class", "\\0101", 0, 32, "fullmatch", "\b1", []span{{0, 2, 0, 2, "\b1"}}},
	{"three-digit octal escape", "\\101", 0, 32, "fullmatch", "A", []span{{0, 1, 0, 1, "A"}}},
	{"hex and unicode escapes", "\\x41é\\U0001F600", 0, 32, "fullmatch", "Aé😀", []span{{0, 3, 0, 7, "Aé😀"}}},
	{"quote escapes", "[\\'\\\"]x\\'", 0, 32, "fullmatch", "\"x'", []span{{0, 3, 0, 3, "\"x'"}}},
	{"comment group", "a(?#note \\) still note)b", 0, 32, "fullmatch", "ab", []span{{0, 2, 0, 2, "ab"}}},
	{"atomic group", "(?>a+)b", 0, 32, "search", "aab", []span{{0, 3, 0, 3, "aab"}}},
	{"conditional on a named group", "(?P<o><)?x(?(o)>|!)", 0, 32, "fullmatch", "<x>", []span{{0, 3, 0, 3, "<x>"}, {0, 1, 0, 1, "<"}}},
	{"conditional on a missing group", "(?P<o><)?x(?(o)>|!)", 0, 32, "fullmatch", "x!", []span{{0, 2, 0, 2, "x!"}, {-1, -1, -1, -1, ""}}},
	{"lookahead captures survive", "(?=(\\w+))\\w", 0, 32, "search", "ab", []span{{0, 1, 0, 1, "a"}, {0, 2, 0, 2, "ab"}}},
	{"non-participating group", "(a)|(b)", 0, 32, "search", "b", []span{{0, 1, 0, 1, "b"}, {-1, -1, -1, -1, ""}, {0, 1, 0, 1, "b"}}},
	{"last iteration wins", "(?:(\\w)-)+", 0, 32, "search", "a-b-c", []span{{0, 4, 0, 4, "a-b-"}, {2, 3, 2, 3, "b"}}},
	{"code-point and byte offsets", "(?P<w>café)", 0, 32, "search", "naïve café ☕", []span{{6, 10, 7, 12, "café"}, {6, 10, 7, 12, "café"}}},
	{"astral offsets", "(\\S+)$", 0, 32, "search", "αβγ 😀z", []span{{4, 6, 7, 12, "😀z"}, {4, 6, 7, 12, "😀z"}}},
	{"desk-runner.py:MUTE muted", "(?<![\\w/-])mut(?:e|ed|es|ing)(?![\\w/-])[^.;\\n]{0,40}?\\b(?:smoke|teams?|monitors?|alerts?|pages?|runs?|failures?|checks?)\\b|\\bfalse alarm\\b|\\b(?:skips?|excludes?|scopes? out)\\b[^.\\n]{0,60}\\b(?:smoke|release[- ]check|teams?)\\b", 34, 34, "search", "Muted the smoke teams until fix-live.", []span{{0, 15, 0, 15, "Muted the smoke"}}},
	{"desk-runner.py:MUTE unmute", "(?<![\\w/-])mut(?:e|ed|es|ing)(?![\\w/-])[^.;\\n]{0,40}?\\b(?:smoke|teams?|monitors?|alerts?|pages?|runs?|failures?|checks?)\\b|\\bfalse alarm\\b|\\b(?:skips?|excludes?|scopes? out)\\b[^.\\n]{0,60}\\b(?:smoke|release[- ]check|teams?)\\b", 34, 34, "search", "unmute the release-check please", nil},
	{"desk-runner.py:MUTE we", "(?<![\\w/-])mut(?:e|ed|es|ing)(?![\\w/-])[^.;\\n]{0,40}?\\b(?:smoke|teams?|monitors?|alerts?|pages?|runs?|failures?|checks?)\\b|\\bfalse alarm\\b|\\b(?:skips?|excludes?|scopes? out)\\b[^.\\n]{0,60}\\b(?:smoke|release[- ]check|teams?)\\b", 34, 34, "search", "we skip the release check for teams", []span{{3, 35, 3, 35, "skip the release check for teams"}}},
	{"desk-runner.py:PR_NAMED see", "(?<![\\w/])#(\\d{3,})\\b|/pull/(\\d+)\\b", 32, 32, "search", "see owner/repo#1234 and /pull/56", []span{{24, 32, 24, 32, "/pull/56"}, {-1, -1, -1, -1, ""}, {30, 32, 30, 32, "56"}}},
	{"desk-runner.py:PR_NAMED x", "(?<![\\w/])#(\\d{3,})\\b|/pull/(\\d+)\\b", 32, 32, "search", "x #1234 /pull/56", []span{{2, 7, 2, 7, "#1234"}, {3, 7, 3, 7, "1234"}, {-1, -1, -1, -1, ""}}},
	{"merge_queue.py:EACH_SECTION ===", "^=== (?P<key>[\\d,]+)\\n(?P<out>.*?)(?:^=== (?P=key) stderr (?P<err>.*?)\\n)?^=== (?P=key) exit (?P<code>\\d+)$", 56, 56, "search", "=== 12,13\nout line\n=== 12,13 stderr err text\n=== 12,13 exit 0", []span{{0, 61, 0, 61, "=== 12,13\nout line\n=== 12,13 stderr err text\n=== 12,13 exit 0"}, {4, 9, 4, 9, "12,13"}, {10, 19, 10, 19, "out line\n"}, {36, 44, 36, 44, "err text"}, {60, 61, 60, 61, "0"}}},
	{"merge_queue.py:EACH_SECTION ===", "^=== (?P<key>[\\d,]+)\\n(?P<out>.*?)(?:^=== (?P=key) stderr (?P<err>.*?)\\n)?^=== (?P=key) exit (?P<code>\\d+)$", 56, 56, "search", "=== 7\nonly out\n=== 7 exit 3", []span{{0, 27, 0, 27, "=== 7\nonly out\n=== 7 exit 3"}, {4, 5, 4, 5, "7"}, {6, 15, 6, 15, "only out\n"}, {-1, -1, -1, -1, ""}, {26, 27, 26, 27, "3"}}},
	{"merge_queue.py:LABEL_CI_PENDING mergeable_state", "^(?:commit status pending|mergeable_state (?!dirty))", 32, 32, "search", "mergeable_state blocked", []span{{0, 16, 0, 16, "mergeable_state "}}},
	{"merge_queue.py:LABEL_CI_PENDING mergeable_state", "^(?:commit status pending|mergeable_state (?!dirty))", 32, 32, "search", "mergeable_state dirty", nil},
	{"desk-runner.py:UNFIXED >", "^[ \\t>*-]*(?P<tick>`?)UNFIXED (?P<lane>[\\w.-]+):[ \\t]*(?P<case>\\S.*?)(?P=tick)[ \\t]*$", 40, 40, "search", "> `UNFIXED api-fix: the case`  ", []span{{0, 31, 0, 31, "> `UNFIXED api-fix: the case`  "}, {2, 3, 2, 3, "`"}, {11, 18, 11, 18, "api-fix"}, {20, 28, 20, 28, "the case"}}},
	{"desk-runner.py:INBOX_DIRECTIVE -", "^(?:-\\s+)?(?:(?:1[0-2]|0?[1-9]):[0-5]\\d\\s?[AP]M\\s+)?(?:(?P<key>R\\d+)\\s+(?:\\([^)]*\\)\\s+)?)?orca-desk: (?P<verb>relay|launch|alert|incident|hold|unhold|withdraw)\\b(?P<rest>.*)$", 48, 48, "search", "- 9:41 AM R12 (9:41 AM) orca-desk: launch api-fix NOW sol xhigh brief=/x", []span{{0, 72, 0, 72, "- 9:41 AM R12 (9:41 AM) orca-desk: launch api-fix NOW sol xhigh brief=/x"}, {10, 13, 10, 13, "R12"}, {35, 41, 35, 41, "launch"}, {41, 72, 41, 72, " api-fix NOW sol xhigh brief=/x"}}},
	{"desk-runner.py:FAILED_TEST ok", "--- FAIL: (?P<go>[\\w.-]+)|^test (?P<cargo>\\S+) \\.\\.\\. FAILED|^\\s*FAIL \\[\\s*[\\d.]+s\\] \\S+ (?P<nextest>\\S+)", 40, 40, "search", "ok\ntest foo::bar ... FAILED\n", []span{{3, 27, 3, 27, "test foo::bar ... FAILED"}, {-1, -1, -1, -1, ""}, {8, 16, 8, 16, "foo::bar"}, {-1, -1, -1, -1, ""}}},
	{"desk-runner.py:FAILED_TEST fail", "--- FAIL: (?P<go>[\\w.-]+)|^test (?P<cargo>\\S+) \\.\\.\\. FAILED|^\\s*FAIL \\[\\s*[\\d.]+s\\] \\S+ (?P<nextest>\\S+)", 40, 40, "search", "    FAIL [   0.12s] crate tests::it_works\n", []span{{0, 41, 0, 41, "    FAIL [   0.12s] crate tests::it_works"}, {-1, -1, -1, -1, ""}, {-1, -1, -1, -1, ""}, {26, 41, 26, 41, "tests::it_works"}}},
	{"long_running/cci.py:INCIDENT_TAG see", "\\b([A-Z]{2,})(?:-([0-9A-Z]*\\d[0-9A-Z]*)|(\\d[0-9A-Z]*))\\b", 32, 32, "search", "see INC-4F2 and SEV2", []span{{4, 11, 4, 11, "INC-4F2"}, {4, 7, 4, 7, "INC"}, {8, 11, 8, 11, "4F2"}, {-1, -1, -1, -1, ""}}},
	{"desk-runner.py:RED_CONTRACT this", "red[- ]by[- ]design|\\bfail(?:s|ed|ing)? (?:in )?(?:run|build)\\b|\\bfailing (?:ci )?runs?\\b", 34, 34, "search", "This is RED BY DESIGN", []span{{8, 21, 8, 21, "RED BY DESIGN"}}},
	{"desk-runner.py:HANDED_BACK stood", "^(?:[\\w-]+:\\s*)?(?:NOT-OURS|stood down|stopped)\\b", 34, 34, "search", "Stood Down for now", []span{{0, 10, 0, 10, "Stood Down"}}},
}

var findAllCases = []findAllCase{
	{"empty match then a non-empty one at the same position", "^|\\w+", 32, "two words", [][]string{{""}, {"two"}, {"words"}}},
	{"whole matches", "\\d+", 32, "a1b22c333", [][]string{{"1"}, {"22"}, {"333"}}},
	{"single group", "#(\\d+)", 32, "#1 and #22", [][]string{{"1"}, {"22"}}},
	{"single group that did not participate", "#(\\d+)?", 32, "# #5", [][]string{{""}, {"5"}}},
	{"tuples", "(\\w)=(\\d)?", 32, "a=1 b=", [][]string{{"a", "1"}, {"b", ""}}},
	{"empty matches everywhere", "x*", 32, "axb", [][]string{{""}, {"x"}, {""}, {""}}},
	{"multiline anchors", "^x$", 40, "x\ny\nx", [][]string{{"x"}, {"x"}}},
	{"caret matches after a final newline", "^", 40, "a\n", [][]string{{""}, {""}}},
}

var subCases = []subCase{
	{"numbered and named template groups", "(?P<k>\\w+)=(\\w+)", 32, "\\2=\\g<k>", "a=b c=d", 0, "b=a d=c", 2},
	{"whole-match group", "\\d", 32, "<\\g<0>>", "a1b2", 0, "a<1>b<2>", 2},
	{"numbered g group", "(\\d)", 32, "[\\g<1>]", "a1", 0, "a[1]", 1},
	{"newline escape", ",", 32, "\\n", "a,b", 0, "a\nb", 1},
	{"unknown non-letter escape keeps its backslash", "a", 32, "\\&", "a", 0, "\\&", 1},
	{"non-participating group is empty", "(x)?y", 32, "[\\1]", "y", 0, "[]", 1},
	{"octal template escape", "a", 32, "\\101\\0", "a", 0, "A\x00", 1},
	{"backslash escape", "a", 32, "\\\\", "a", 0, "\\", 1},
	{"empty matches adjacent to a previous match", "x*", 32, "-", "abxd", 0, "-a-b--d-", 5},
	{"count limits replacements", "a", 32, "b", "aaa", 2, "bba", 2},
	{"non-ASCII replacement", "é", 32, "e", "café é", 0, "cafe e", 2},
}

var splitCases = []splitCase{
	{"groups are kept", "(,)|;", 32, "a,b;c", 0, []string{"a", ",", "b", "", "c"}},
	{"maxsplit", ",", 32, "a,b,c", 1, []string{"a", "b,c"}},
	{"split on word boundaries", "\\b", 32, "ab cd", 0, []string{"", "ab", " ", "cd", ""}},
	{"split on empty matches", "x*", 32, "axbc", 0, []string{"", "a", "", "b", "c", ""}},
	{"split on Python whitespace", "\\s+", 32, "a\x1cb  c", 0, []string{"a", "b", "c"}},
}
