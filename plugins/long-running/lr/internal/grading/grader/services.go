package grader

import (
	"fmt"
	"log/slog"
	"path"
	"regexp"
	"strconv"
	"strings"
)

type ServiceTouch struct {
	Service string `json:"service"`
	Path    string `json:"path"`
	Added   string `json:"added"`
}

type serviceRule struct {
	ServiceTouch
	added *regexp.Regexp
}

func (s *fidelityPassState) serviceTouches(start string) ([]string, error) {
	if len(s.job.ServiceTouches) == 0 {
		return nil, nil
	}
	diff, err := s.git("diff", "--unified=0", "--no-color", "--no-ext-diff", start, s.job.Head)
	if err != nil {
		return nil, err
	}
	return addedTouches(diff, s.job.ServiceTouches, s.Log), nil
}

func addedTouches(diff string, entries []ServiceTouch, log *slog.Logger) []string {
	var rules []serviceRule
	for _, entry := range entries {
		added, err := regexp.Compile(entry.Added)
		if err == nil {
			_, err = path.Match(entry.Path, "")
		}
		if err != nil {
			log.Warn("a service-touch entry is skipped, since its pattern does not compile", "service", entry.Service, "path", entry.Path, "error", err)
			continue
		}
		rules = append(rules, serviceRule{entry, added})
	}
	var found []string
	file, line, inHunk := "", 0, false
	for text := range strings.SplitSeq(diff, "\n") {
		if named := diffFile.FindStringSubmatch(text); named != nil {
			file, inHunk = named[1], false
			continue
		}
		if hunk := hunkHeader.FindStringSubmatch(text); hunk != nil {
			line, _ = strconv.Atoi(hunk[1])
			inHunk = true
			continue
		}
		if !inHunk || testFile.MatchString(file) {
			continue
		}
		switch {
		case strings.HasPrefix(text, "+"):
			for _, rule := range rules {
				if underGlob(rule.Path, file) && rule.added.MatchString(text[1:]) {
					found = append(found, fmt.Sprintf("%s:%d touches %s", file, line, rule.Service))
				}
			}
			line++
		case strings.HasPrefix(text, " "):
			line++
		}
	}
	return found
}

func underGlob(glob, file string) bool {
	parts := strings.Split(file, "/")
	for end := range parts {
		if matched, _ := path.Match(glob, strings.Join(parts[:end+1], "/")); matched {
			return true
		}
	}
	return false
}

func (s *fidelityPassState) touchedOldApp() (string, bool) {
	if len(s.touches) == 0 {
		return "", false
	}
	return fmt.Sprintf("old-app: CHANGES %s baseline %s; %s", short(s.job.Head, 7), s.baseline(), strings.Join(s.touches, "; ")), true
}
