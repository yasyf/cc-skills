package config

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
)

type RunnerDeadlines struct {
	StartMinutes             int `json:"start_minutes"`
	LaunchMinutes            int `json:"launch_minutes"`
	EnqueueMinutes           int `json:"enqueue_minutes"`
	HoldMinutes              int `json:"hold_minutes"`
	ReleaseStartMinutes      int `json:"release_start_minutes"`
	IncidentReleaseMinutes   int `json:"incident_release_minutes"`
	CutMinutes               int `json:"cut_minutes"`
	IncidentMechanismMinutes int `json:"incident_mechanism_minutes"`
	IncidentPRMinutes        int `json:"incident_pr_minutes"`
	IncidentReadyMinutes     int `json:"incident_ready_minutes"`
	PRSilentMinutes          int `json:"pr_silent_minutes"`
}

type RunnerBriefs struct {
	Repo string `json:"repo"`
	Log  string `json:"log"`
}

const (
	DropClassLabel    = "drop-class"
	matchLabel        = "match:"
	refusalMeaningCap = 160
)

type Refusal struct {
	Match *regexp.Regexp
	Why   string
}

func RefusalsOf(listing string) ([]Refusal, error) {
	var answers []struct {
		ID   string   `json:"id"`
		Body string   `json:"body"`
		Tags []string `json:"tags"`
	}
	if err := json.Unmarshal([]byte(listing), &answers); err != nil {
		return nil, fmt.Errorf("ccn answer list: %w", err)
	}
	var refusals []Refusal
	for _, answer := range answers {
		var phrases []string
		for _, tag := range answer.Tags {
			if phrase, labelled := strings.CutPrefix(tag, matchLabel); labelled {
				phrases = append(phrases, regexp.QuoteMeta(phrase))
			}
		}
		if len(phrases) > 0 {
			refusals = append(refusals, Refusal{regexp.MustCompile("(?i)" + strings.Join(phrases, "|")), fmt.Sprintf("owner ruling %s: %s", answer.ID[:min(len(answer.ID), 7)], meaningOf(answer.Body))})
		}
	}
	return refusals, nil
}

func meaningOf(body string) string {
	first, _, _ := strings.Cut(strings.TrimSpace(body), "\n")
	first = first[:min(len(first), refusalMeaningCap)]
	if end := strings.LastIndex(first, ". "); end > 0 {
		return first[:end+1]
	}
	return first
}

func RefusedLine(refusals []Refusal, text string) (Refusal, string, bool) {
	for _, refusal := range refusals {
		for line := range strings.SplitSeq(text, "\n") {
			if refusal.Match.MatchString(line) {
				return refusal, line, true
			}
		}
	}
	return Refusal{}, "", false
}

type RunnerOrca struct {
	DeskInbox string            `json:"desk_inbox"`
	Run       string            `json:"run"`
	Briefs    RunnerBriefs      `json:"briefs"`
	LaunchEnv map[string]string `json:"launch_env"`
	GC        string            `json:"gc"`
}

type Runner struct {
	Source     string
	Store      string
	Drive      string
	Orca       RunnerOrca
	Deadlines  RunnerDeadlines
	CaseLanes  int
	AlertFacts string
	Sections   map[string]json.RawMessage
}

func LoadRunner(path string) (Runner, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return Runner{}, err
	}
	raw := struct {
		Store     string          `json:"store"`
		Drive     string          `json:"drive"`
		Orca      RunnerOrca      `json:"orca"`
		Deadlines RunnerDeadlines `json:"deadlines"`
		Incident  struct {
			CaseLanes int `json:"case_lanes"`
		} `json:"incident"`
		Alert struct {
			Facts string `json:"facts"`
		} `json:"alert"`
	}{Deadlines: RunnerDeadlines{
		StartMinutes: 10, LaunchMinutes: 15, EnqueueMinutes: 15, HoldMinutes: 15, ReleaseStartMinutes: 10,
		IncidentReleaseMinutes: 3, CutMinutes: 4, IncidentMechanismMinutes: 10, IncidentPRMinutes: 15, IncidentReadyMinutes: 20,
		PRSilentMinutes: 10,
	}}
	raw.Incident.CaseLanes = 2
	if err := json.Unmarshal(data, &raw); err != nil {
		return Runner{}, fmt.Errorf("%s: %w", path, err)
	}
	var sections map[string]json.RawMessage
	if err := json.Unmarshal(data, &sections); err != nil {
		return Runner{}, fmt.Errorf("%s: %w", path, err)
	}
	var missing []string
	for name, value := range map[string]string{"drive": raw.Drive, "orca.briefs.repo": raw.Orca.Briefs.Repo, "orca.desk_inbox": raw.Orca.DeskInbox, "orca.run": raw.Orca.Run} {
		if value == "" {
			missing = append(missing, name)
		}
	}
	if len(missing) > 0 {
		slices.Sort(missing)
		return Runner{}, fmt.Errorf("%s: missing %s", path, strings.Join(missing, ", "))
	}
	source, err := filepath.Abs(path)
	if err != nil {
		return Runner{}, err
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return Runner{}, err
	}
	expand := func(value string) string {
		if value == "~" {
			return home
		}
		if rest, found := strings.CutPrefix(value, "~/"); found {
			return filepath.Join(home, rest)
		}
		return value
	}
	raw.Orca.DeskInbox = expand(raw.Orca.DeskInbox)
	raw.Orca.Briefs.Repo = expand(raw.Orca.Briefs.Repo)
	return Runner{
		Source: source, Store: expand(raw.Store), Drive: raw.Drive, Orca: raw.Orca, Deadlines: raw.Deadlines,
		CaseLanes: raw.Incident.CaseLanes, AlertFacts: expand(raw.Alert.Facts), Sections: sections,
	}, nil
}
