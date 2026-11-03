package rules

import (
	"fmt"
	"regexp"
	"slices"
	"strings"
)

var (
	fileHeader = regexp.MustCompile(`^diff --git a/\S+ b/(\S+)(?:\n|$)`)
	fileStart  = regexp.MustCompile(`(?m)^diff --git `)
	testPaths  = compile("*/tests/*", "*/test/*", "*_test.go", "*_test.py", "*.test.ts", "*.test.tsx", "*.spec.ts", "test_*.py", "*/__tests__/*", "*/testdata/*", ".github/*", "*/ci/*", "*.ci.yml")
)

const lineBreaks = "\n\r\v\f\x1c\x1d\x1e\u0085\u2028\u2029"

type Change struct {
	Path    string
	Removed []string
	Added   []string
}

type Touch string

const (
	Deletes   Touch = "deletes"
	Adds      Touch = "adds"
	Changes   Touch = "changes"
	Untouched Touch = "untouched"
)

type Check struct {
	Rule string
	Met  bool
	How  string
}

func compile(globs ...string) []*regexp.Regexp {
	compiled := make([]*regexp.Regexp, len(globs))
	for i, glob := range globs {
		compiled[i] = regexp.MustCompile(`(?s)^` + strings.ReplaceAll(regexp.QuoteMeta(glob), `\*`, `.*`) + `$`)
	}
	return compiled
}

func Parse(diff string) []Change {
	starts := fileStart.FindAllStringIndex(diff, -1)
	out := make([]Change, 0, len(starts))
	for i, start := range starts {
		end := len(diff)
		if i+1 < len(starts) {
			end = starts[i+1][0]
		}
		out = append(out, parseFile(diff[start[0]:end]))
	}
	return slices.DeleteFunc(out, func(change Change) bool { return change.Path == "" })
}

func parseFile(text string) Change {
	found := fileHeader.FindStringSubmatch(text)
	if found == nil {
		return Change{}
	}
	change := Change{Path: found[1]}
	_, body, _ := strings.Cut(text, "\n")
	for _, line := range strings.FieldsFunc(body, func(r rune) bool { return strings.ContainsRune(lineBreaks, r) }) {
		switch {
		case strings.HasPrefix(line, "---"), strings.HasPrefix(line, "+++"):
		case strings.HasPrefix(line, "-"):
			change.Removed = append(change.Removed, line[1:])
		case strings.HasPrefix(line, "+"):
			change.Added = append(change.Added, line[1:])
		}
	}
	return change
}

func TouchOf(changes []Change, symbol string) Touch {
	word := regexp.MustCompile(`\b` + regexp.QuoteMeta(symbol) + `\b`)
	var removed, added bool
	for _, change := range changes {
		removed = removed || slices.ContainsFunc(change.Removed, word.MatchString)
		added = added || slices.ContainsFunc(change.Added, word.MatchString)
	}
	switch {
	case removed && !added:
		return Deletes
	case added && !removed:
		return Adds
	case added:
		return Changes
	}
	return Untouched
}

func testPath(path string) bool {
	return slices.ContainsFunc(testPaths, func(glob *regexp.Regexp) bool {
		return glob.MatchString(path) || glob.MatchString("/"+path)
	})
}

func TestOnly(changes []Change) bool {
	return len(changes) > 0 && !slices.ContainsFunc(changes, func(change Change) bool { return !testPath(change.Path) })
}

func Empty(changes []Change) bool {
	return !slices.ContainsFunc(changes, func(change Change) bool { return len(change.Removed) > 0 || len(change.Added) > 0 })
}

func Evaluate(rule, ref string, changes []Change) (Check, error) {
	name, argument, _ := strings.Cut(ref, ":")
	switch name {
	case "symbol-kept":
		if argument == "" {
			return Check{}, fmt.Errorf("rule %s: symbol-kept needs a symbol", rule)
		}
		touch := TouchOf(changes, argument)
		return Check{rule, touch != Deletes, fmt.Sprintf("%s %s", touch, argument)}, nil
	case "test-only":
		if TestOnly(changes) {
			return Check{rule, true, "touches only tests and CI"}, nil
		}
		return Check{rule, false, "touches " + strings.Join(sourcePaths(changes), ", ")}, nil
	case "empty-diff":
		if Empty(changes) {
			return Check{rule, true, "changes nothing against its base"}, nil
		}
		return Check{rule, false, "changes files against its base"}, nil
	}
	return Check{}, fmt.Errorf("rule %s: no predicate %q", rule, name)
}

func sourcePaths(changes []Change) []string {
	var paths []string
	for _, change := range changes {
		if !testPath(change.Path) {
			paths = append(paths, change.Path)
		}
	}
	slices.Sort(paths)
	return paths[:min(3, len(paths))]
}
