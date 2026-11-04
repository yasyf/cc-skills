package asks

import (
	"encoding/json"
	"fmt"
	"maps"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"
)

type State string

const (
	Live          State = "LIVE"
	LandedNotLive State = "LANDED-NOT-LIVE"
	InPR          State = "IN-PR"
	Lost          State = "LOST"
	InProgress    State = "IN-PROGRESS"
	Stale         State = "STALE"
	Backlog       State = "BACKLOG"
	Dropped       State = "dropped"
	Answered      State = "answered"
	Fresh         State = "fresh"

	Prefix         = "ask/"
	Dashboard      = "dashboard"
	liveKey        = "live"
	progressPrefix = "progress_"
	stateLanded    = "landed"
	stateOpen      = "open"
	mergeLabel     = "merge"
	verdictHeld    = "held"
	testFailure    = "failure"
	lostAfter      = 30 * time.Minute
)

var (
	prKey     = regexp.MustCompile(`^(?:([\w.-]+/[\w.-]+)#)?(\d+)$`)
	overrides = map[string]State{"backlog": Backlog, "live": Live, "lost": Lost}
)

type Progress struct {
	At        time.Time `json:"at"`
	Note      string    `json:"note"`
	Artifacts []string  `json:"artifacts"`
}

type Ask struct {
	Key           string
	Lane          string
	Text          string
	AskedAt       time.Time
	Due           time.Time
	StandingHours int
	Override      string
	Answered      bool
	Dropped       bool
	PRs           []string
	Surface       string
	Newest        *Progress
}

func (a Ask) Line() string {
	standing := ""
	if a.StandingHours > 0 {
		standing = fmt.Sprintf(" standing %dh", a.StandingHours)
	}
	return fmt.Sprintf("%s %s%s: %s", a.Key, a.Lane, standing, a.Text)
}

type PR struct {
	Key              string
	Repo             string
	Number           int
	State            string
	LandedAt         time.Time
	LandedSHA        string
	Lane             string
	Title            string
	Head             string
	ReportedHead     string
	ReportedVerdict  string
	Registered       bool
	Labels           string
	LabelHead        string
	LabelledAt       string
	LabelPulledAt    string
	LabelRefusedHead string
	HoldUntil        string
	TestState        string
	MergeableState   string
}

func (p PR) Open() bool {
	return p.State == stateOpen
}

func (p PR) Landed() bool {
	return p.State == stateLanded
}

func (p PR) CurrentHead() string {
	if p.Head != "" {
		return p.Head
	}
	return p.ReportedHead
}

func (p PR) CarriesLabel() bool {
	onHead := p.LabelHead != "" && p.LabelHead == p.Head && p.LabelledAt != "" && p.LabelPulledAt == ""
	return slices.Contains(strings.Split(p.Labels, ","), mergeLabel) || onHead
}

func (p PR) WaitingReason() string {
	switch {
	case !p.Open() || (p.ReportedHead == "" && !p.Registered) || p.CarriesLabel():
		return ""
	case p.HoldUntil != "" || (p.ReportedVerdict == verdictHeld && p.ReportedHead == p.CurrentHead()):
		return "held"
	case p.TestState == testFailure || p.MergeableState == "dirty" || p.MergeableState == "blocked":
		return "red"
	case p.LabelRefusedHead != "" && p.LabelRefusedHead == p.CurrentHead():
		return "refused"
	}
	return "ungraded"
}

type Book struct {
	Asks      []Ask
	PRs       map[string]PR
	LiveSince time.Time
}

func Read(rows map[string]map[string]string) (Book, error) {
	book := Book{PRs: map[string]PR{}}
	for _, key := range slices.Sorted(maps.Keys(rows)) {
		fields := rows[key]
		switch {
		case key == liveKey:
			at, err := when(fields, "at")
			if err != nil {
				return Book{}, fmt.Errorf("row %s: %w", key, err)
			}
			book.LiveSince = at
		case prKey.MatchString(key):
			pr, err := readPR(key, fields)
			if err != nil {
				return Book{}, fmt.Errorf("row %s: %w", key, err)
			}
			book.PRs[key] = pr
		case strings.HasPrefix(key, Prefix) && isDigits(key[len(Prefix):]):
			ask, err := readAsk(key, fields)
			if err != nil {
				return Book{}, fmt.Errorf("row %s: %w", key, err)
			}
			book.Asks = append(book.Asks, ask)
		}
	}
	return book, nil
}

func isDigits(text string) bool {
	_, err := strconv.ParseUint(text, 10, 64)
	return err == nil
}

func when(fields map[string]string, name string) (time.Time, error) {
	text := fields[name]
	if text == "" {
		return time.Time{}, nil
	}
	return time.Parse(time.RFC3339, text)
}

func readPR(key string, fields map[string]string) (PR, error) {
	found := prKey.FindStringSubmatch(key)
	number, err := strconv.Atoi(found[2])
	if err != nil {
		return PR{}, err
	}
	landed, err := when(fields, "landed_at")
	if err != nil {
		return PR{}, err
	}
	state := fields["state"]
	if state == "" {
		state = stateOpen
	}
	return PR{
		Key: key, Repo: found[1], Number: number, State: state, LandedAt: landed, LandedSHA: fields["landed_sha"],
		Lane: fields["lane"], Title: fields["title"],
		Head: fields["head"], ReportedHead: fields["reported_head"], ReportedVerdict: fields["reported_verdict"], Registered: fields["registered"] != "",
		Labels: fields["labels"], LabelHead: fields["label_head"], LabelledAt: fields["labelled_at"], LabelPulledAt: fields["label_pulled_at"], LabelRefusedHead: fields["label_refused_head"],
		HoldUntil: fields["hold_until"], TestState: fields["test_state"], MergeableState: fields["mergeable_state"],
	}, nil
}

func readAsk(key string, fields map[string]string) (Ask, error) {
	asked, err := when(fields, "asked_at")
	if err != nil {
		return Ask{}, err
	}
	ask := Ask{
		Key: key, Lane: fields["lane"], Text: fields["text"], AskedAt: asked, Override: fields["state"],
		Answered: fields["answered_at"] != "", Dropped: fields["dropped_at"] != "",
		PRs:     slices.DeleteFunc(strings.Split(fields["prs"], ","), func(pr string) bool { return pr == "" }),
		Surface: fields["surface"],
	}
	if ask.Due, err = when(fields, "due"); err != nil {
		return Ask{}, err
	}
	if hours := fields["standing_hours"]; hours != "" {
		if ask.StandingHours, err = strconv.Atoi(hours); err != nil {
			return Ask{}, fmt.Errorf("standing_hours: %w", err)
		}
	}
	var names []string
	for name := range fields {
		if strings.HasPrefix(name, progressPrefix) {
			names = append(names, name)
		}
	}
	if len(names) > 0 {
		var newest Progress
		if err := json.Unmarshal([]byte(fields[slices.Max(names)]), &newest); err != nil {
			return Ask{}, fmt.Errorf("progress: %w", err)
		}
		ask.Newest = &newest
	}
	return ask, nil
}

func (b Book) State(a Ask, now time.Time) State {
	switch {
	case a.Dropped:
		return Dropped
	case a.Answered:
		return Answered
	}
	if override, found := overrides[a.Override]; found {
		return override
	}
	moved := a.AskedAt
	if a.Newest != nil {
		moved = a.Newest.At
	}
	if a.StandingHours > 0 {
		if now.Sub(moved) < time.Duration(a.StandingHours)*time.Hour {
			return InProgress
		}
		return Stale
	}
	if landed, delivered := b.landedAt(a); delivered {
		if !b.LiveSince.IsZero() && !b.LiveSince.Before(landed) {
			return Live
		}
		return LandedNotLive
	}
	if b.hasOpenPR(a) {
		return InPR
	}
	if now.Sub(moved) >= lostAfter {
		return Lost
	}
	if a.Newest != nil {
		return InProgress
	}
	return Fresh
}

func (b Book) landedAt(a Ask) (time.Time, bool) {
	var newest time.Time
	for _, key := range a.PRs {
		pr, found := b.PRs[key]
		if !found || pr.State != stateLanded {
			return time.Time{}, false
		}
		if pr.LandedAt.After(newest) {
			newest = pr.LandedAt
		}
	}
	return newest, len(a.PRs) > 0
}

func (b Book) hasOpenPR(a Ask) bool {
	return slices.ContainsFunc(a.PRs, func(key string) bool {
		pr, found := b.PRs[key]
		return !found || pr.Open()
	})
}

type Graded struct {
	Ask
	State State
}

func (b Book) Open(now time.Time) []Graded {
	var open []Graded
	for _, ask := range b.Asks {
		if state := b.State(ask, now); state != Dropped && state != Answered {
			open = append(open, Graded{ask, state})
		}
	}
	slices.SortStableFunc(open, func(one, other Graded) int {
		return boolOrder(other.State == LandedNotLive) - boolOrder(one.State == LandedNotLive)
	})
	return open
}

func boolOrder(value bool) int {
	if value {
		return 1
	}
	return 0
}

func (b Book) OpenPRs() []PR {
	var open []PR
	for _, pr := range b.PRs {
		if pr.Open() {
			open = append(open, pr)
		}
	}
	slices.SortFunc(open, func(one, other PR) int {
		if order := strings.Compare(strings.ToLower(one.Repo), strings.ToLower(other.Repo)); order != 0 {
			return order
		}
		return one.Number - other.Number
	})
	return open
}
