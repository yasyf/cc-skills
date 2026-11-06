package grader

import (
	"fmt"
	"regexp"
	"strconv"
	"strings"
)

var testFile = regexp.MustCompile(`(?:^|/)(?:tests?|testing|testdata|fixtures|__snapshots__|__mocks__|e2e|evals|fakes?|mocks?|testkit|testutils?|test-scripts|vcr)/|(?:^|/)[a-z0-9_-]+(?:test|testkit|testutils?|harness)/|(?:^|/)[a-z0-9]+ForTests?/|(?:^|/)tests?\.\w+$|(?:^|/)test_[^/]*$|_test\.\w+$|\.(?:test|spec|e2e)\.\w+$|(?:^|/)(?:fake|mock)s?_[^/]*$|_(?:fake|mock)s?\.\w+$|(?:^|/)[^/]*test-timings\.json$`)

type span struct {
	start int
	end   int
}

type fileSpans struct {
	path  string
	spans []span
}

type touched []fileSpans

func hunks(diff string) touched {
	return hunkSpans(diff, 1)
}

func hunkSpans(diff string, least int) touched {
	var files touched
	path := ""
	for line := range strings.SplitSeq(diff, "\n") {
		if found := diffFile.FindStringSubmatch(line); found != nil {
			path = found[1]
			continue
		}
		found := hunkHeader.FindStringSubmatch(line)
		if found == nil {
			continue
		}
		start, _ := strconv.Atoi(found[1])
		count := 1
		if found[2] != "" {
			count, _ = strconv.Atoi(found[2])
		}
		files = files.with(path, span{start, start + max(count, least) - 1})
	}
	return files
}

func (t touched) with(path string, added span) touched {
	for index := range t {
		if t[index].path == path {
			t[index].spans = append(t[index].spans, added)
			return t
		}
	}
	return append(t, fileSpans{path, []span{added}})
}

func samePath(path, named string) bool {
	return path == named || strings.HasSuffix(path, "/"+named) || strings.HasSuffix(named, "/"+path)
}

func (t touched) holds(citation string) bool {
	for _, found := range cite.FindAllStringSubmatch(citation, -1) {
		first, _ := strconv.Atoi(found[2])
		last := first
		if found[3] != "" {
			last, _ = strconv.Atoi(found[3])
		}
		for _, file := range t {
			if !samePath(file.path, found[1]) {
				continue
			}
			for _, covered := range file.spans {
				if covered.start <= last && first <= covered.end {
					return true
				}
			}
		}
	}
	return false
}

func (t touched) lines() string {
	lines := make([]string, 0, len(t))
	for _, file := range t {
		ranges := make([]string, 0, len(file.spans))
		for _, covered := range file.spans {
			if covered.start == covered.end {
				ranges = append(ranges, strconv.Itoa(covered.start))
				continue
			}
			ranges = append(ranges, fmt.Sprintf("%d-%d", covered.start, covered.end))
		}
		lines = append(lines, "- "+file.path+": "+strings.Join(ranges, ", "))
	}
	return strings.Join(lines, "\n")
}

func (t touched) area() []string {
	paths := make([]string, 0, len(t))
	for _, file := range t {
		if !testFile.MatchString(file.path) {
			paths = append(paths, file.path)
		}
	}
	return paths
}
