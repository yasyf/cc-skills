package board

import (
	"fmt"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"
)

const (
	KindAt       = "at"
	KindPR       = "pr"
	KindRelease  = "release"
	KindRecord   = "record"
	KindAnswered = "answered"

	endForms    = "answered (only his answer ends it), at:TIME (an ISO 8601 time or a span such as 90m, 4h or 1d), pr:N or pr:OWNER/REPO#N, release:STACK@ENV:COMMIT, or record:SEQ"
	defaultRepo = "Forge-AI/monorepo"
	stampForm   = "2006-01-02T15:04:05Z"
	stateOpen   = "OPEN"
)

var (
	endSpan    = regexp.MustCompile(`^(\d+)([mhd])$`)
	endPR      = regexp.MustCompile(`^(?:([\w.-]+/[\w.-]+)#)?(\d+)$`)
	endRelease = regexp.MustCompile(`^([\w.-]+)@([\w.-]+):([0-9a-f]{7,40})$`)
	endRecord  = regexp.MustCompile(`^#?(\d+)$`)
	liveLine   = regexp.MustCompile(`^(\S+) \([^)]*\) on (\S+): .* at ([0-9a-f]{7,40}),`)
	spanUnits  = map[string]time.Duration{"m": time.Minute, "h": time.Hour, "d": 24 * time.Hour}
	allEnds    = []string{KindAnswered, KindAt, KindPR, KindRelease, KindRecord}
)

type End struct {
	Kind  string
	Value string
}

func (e End) String() string {
	if e.Kind == KindAnswered {
		return e.Kind
	}
	return e.Kind + ":" + e.Value
}

func stamp(at time.Time) string {
	return at.UTC().Format(stampForm)
}

func atTime(text string, now time.Time) (string, error) {
	if span := endSpan.FindStringSubmatch(text); span != nil {
		count, err := strconv.Atoi(span[1])
		if err != nil {
			return "", err
		}
		return stamp(now.Add(time.Duration(count) * spanUnits[span[2]])), nil
	}
	parsed, err := time.Parse(time.RFC3339, text)
	if err != nil {
		return "", fmt.Errorf("%q is neither an ISO 8601 time with its zone nor a span such as 90m, 4h or 1d", text)
	}
	return stamp(parsed), nil
}

func ParseEnd(text string, now time.Time) (End, error) {
	if strings.TrimSpace(text) == KindAnswered {
		return End{Kind: KindAnswered}, nil
	}
	kind, value, colon := strings.Cut(strings.TrimSpace(text), ":")
	if !colon || value == "" {
		return End{}, fmt.Errorf("%q names no end condition; give %s", text, endForms)
	}
	switch kind {
	case KindAt:
		at, err := atTime(value, now)
		if err != nil {
			return End{}, err
		}
		return End{KindAt, at}, nil
	case KindPR:
		if found := endPR.FindStringSubmatch(value); found != nil {
			repo := found[1]
			if repo == "" {
				repo = defaultRepo
			}
			return End{KindPR, repo + "#" + found[2]}, nil
		}
	case KindRelease:
		if endRelease.MatchString(value) {
			return End{KindRelease, value}, nil
		}
	case KindRecord:
		if found := endRecord.FindStringSubmatch(value); found != nil {
			return End{KindRecord, found[1]}, nil
		}
	}
	return End{}, fmt.Errorf("%q is not an end condition; give %s", text, endForms)
}

func Refusal(text string, now time.Time, kinds ...string) string {
	if len(kinds) == 0 {
		kinds = allEnds
	}
	if text == "" {
		return "no end condition; give " + endForms
	}
	end, err := ParseEnd(text, now)
	if err != nil {
		return err.Error()
	}
	if !slices.Contains(kinds, end.Kind) {
		return fmt.Sprintf("%q is a %s condition; this needs %s", text, end.Kind, strings.Join(kinds, " or "))
	}
	if end.Kind == KindAt && end.Value <= stamp(now) {
		return fmt.Sprintf("%q has already passed", text)
	}
	return ""
}

type EndFacts struct {
	PRs     map[string]string
	Live    []string
	Closed  map[string]bool
	Carries func(commit, live string) bool
}

func (f EndFacts) allReached(ends []End, now time.Time) string {
	reached := make([]string, 0, len(ends))
	for _, end := range ends {
		outcome := f.Reached(end, now)
		if outcome == "" {
			return ""
		}
		reached = append(reached, outcome)
	}
	return strings.Join(reached, ", ")
}

func (f EndFacts) Reached(end End, now time.Time) string {
	switch end.Kind {
	case KindAt:
		if end.Value <= stamp(now) {
			return fmt.Sprintf("its time %s passed", end.Value)
		}
	case KindPR:
		if state, read := f.PRs[end.Value]; read && state != "" && state != stateOpen {
			return fmt.Sprintf("%s is %s", end.Value, strings.ToLower(state))
		}
	case KindRelease:
		return f.released(endRelease.FindStringSubmatch(end.Value))
	case KindRecord:
		if f.Closed[end.Value] {
			return fmt.Sprintf("record #%s is closed", end.Value)
		}
	}
	return ""
}

func (f EndFacts) closedOf(ends []End) []string {
	var closed []string
	for _, end := range ends {
		if end.Kind == KindPR && strings.EqualFold(f.PRs[end.Value], "closed") {
			closed = append(closed, end.Value)
		}
	}
	return closed
}

func (f EndFacts) released(want []string) string {
	for _, line := range f.Live {
		live := liveLine.FindStringSubmatch(line)
		if live != nil && live[1] == want[1] && live[2] == want[2] && f.Carries(want[3], live[3]) {
			return fmt.Sprintf("%s on %s runs %s, which carries %s", want[1], want[2], live[3], want[3])
		}
	}
	return ""
}
