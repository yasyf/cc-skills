package grader

import (
	"fmt"
	"os"
	"regexp"
	"slices"
	"strconv"
	"strings"
)

const (
	acceptedMeans   = "a finder grade says the PR's change holds no blocker or major defect beyond the findings #%d accepted"
	secondFixRaised = "\nThe reply above is codex's. finder-desk's own second-fix check added this finding: %s\n"
)

var (
	lineNumbers = regexp.MustCompile(`:\d+(?:-\d+)?`)
	gradedAt    = regexp.MustCompile(`^finder: \w+ ([0-9a-f]{7,40})\b`)
)

type acceptedGrade struct {
	head string
	text string
	by   int64
}

type acceptedFinding struct {
	cite string
	by   int64
}

func (s *finderPassState) carryMeans() (string, error) {
	prior := s.job.Prior
	grade := FinderGrade(prior.Text)
	if grade == finderPass {
		return finderMeans, nil
	}
	by := s.closedBy(prior, s.job.Accepted)
	if grade != finderFail || by == 0 {
		return "", nil
	}
	holds, err := s.acceptHolds(prior)
	if err != nil || !holds {
		return "", err
	}
	return fmt.Sprintf(acceptedMeans, by), nil
}

func (s *finderPassState) acceptHolds(prior *Record) (bool, error) {
	origin := s.originOf(prior)
	if listed, known := s.historyBySeq()[origin.Seq]; known {
		origin = listed
	}
	text, err := wholeLine(origin)
	if err != nil {
		return false, err
	}
	return s.untouchedSince(origin.Text, text)
}

func (s *finderPassState) stillAccepted(findings []finding) ([]finding, []acceptedFinding, error) {
	grades, err := s.acceptedGrades()
	if err != nil || len(grades) == 0 {
		return findings, nil, err
	}
	open := make([]finding, 0, len(findings))
	var kept []acceptedFinding
	for _, f := range findings {
		closed, err := s.acceptedAt(f, grades)
		if err != nil {
			return nil, nil, err
		}
		if closed == nil {
			open = append(open, f)
			continue
		}
		kept = append(kept, *closed)
	}
	return open, kept, nil
}

func (s *finderPassState) acceptedGrades() ([]acceptedGrade, error) {
	var grades []acceptedGrade
	seen := map[int64]bool{}
	for index := range s.job.History {
		record := &s.job.History[index]
		by := s.job.Accepted[strconv.FormatInt(record.Seq, 10)]
		origin := s.originOf(record)
		if by == 0 || seen[origin.Seq] {
			continue
		}
		seen[origin.Seq] = true
		text, err := wholeLine(origin)
		if err != nil {
			return nil, err
		}
		grades = append(grades, acceptedGrade{origin.Text, text, by})
	}
	return grades, nil
}

func (s *finderPassState) acceptedAt(f finding, grades []acceptedGrade) (*acceptedFinding, error) {
	if !slices.Contains(majors, f.Severity) {
		return nil, nil
	}
	claim := claimOf(f)
	for _, grade := range grades {
		found := claim.FindStringSubmatch(grade.text)
		if found == nil {
			continue
		}
		untouched, err := s.untouchedSince(grade.head, found[1])
		if err != nil {
			return nil, err
		}
		if untouched {
			return &acceptedFinding{found[1], grade.by}, nil
		}
	}
	return nil, nil
}

func claimOf(f finding) *regexp.Regexp {
	cited := squeezed(f.Cite)
	var pattern strings.Builder
	pattern.WriteString(`(?:^|[:;] )(`)
	last := 0
	for _, at := range lineNumbers.FindAllStringIndex(cited, -1) {
		pattern.WriteString(regexp.QuoteMeta(cited[last:at[0]]) + lineNumbers.String())
		last = at[1]
	}
	pattern.WriteString(regexp.QuoteMeta(cited[last:]) + ") " + regexp.QuoteMeta(squeezed(f.Defect)) + `(?:;|$)`)
	return regexp.MustCompile(pattern.String())
}

func (s *finderPassState) untouchedSince(graded, cited string) (bool, error) {
	at := gradedAt.FindStringSubmatch(graded)
	var paths []string
	for _, found := range cite.FindAllStringSubmatch(cited, -1) {
		if !slices.Contains(paths, found[1]) {
			paths = append(paths, found[1])
		}
	}
	if at == nil || len(paths) == 0 {
		return false, nil
	}
	head, err := s.fetched(at[1])
	if err != nil || head == "" {
		return false, err
	}
	out, err := s.git(append([]string{"diff", "--unified=0", "--no-color", "--no-ext-diff", s.job.Head, head, "--"}, paths...)...)
	if err != nil {
		return false, err
	}
	return !hunkSpans(out, 0).holds(cited), nil
}

func wholeLine(r *Record) (string, error) {
	if r.Path == "" {
		return r.Text, nil
	}
	data, err := os.ReadFile(r.Path)
	if err != nil {
		return "", err
	}
	line, _, _ := strings.Cut(string(data), "\n")
	return line, nil
}

func acceptedLine(kept []acceptedFinding) string {
	named := make([]string, 0, len(kept))
	for _, k := range kept {
		named = append(named, fmt.Sprintf("%s (#%d)", k.cite, k.by))
	}
	return "accepted before, cited lines unchanged: " + strings.Join(named, ", ")
}
