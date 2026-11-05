package bulk

import (
	"bytes"
	"context"
	_ "embed"
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"text/template"
	"time"

	"github.com/yasyf/cc-inbox/client"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/orca"
)

const (
	jobFile     = "bulk.json"
	briefFile   = "brief.md"
	readerModel = "sonnet"
	triageModel = "opus"
	readEffort  = "high"
	launchWait  = 10 * time.Minute
)

var (
	//go:embed reader.md.tmpl
	readerText string
	//go:embed triage.md.tmpl
	triageText string

	readerBrief = template.Must(template.New("reader").Parse(readerText))
	triageBrief = template.Must(template.New("triage").Parse(triageText))

	laneName = regexp.MustCompile(`^[a-z0-9][a-z0-9-]*$`)
	numbered = regexp.MustCompile(`^(.+)-(\d+)$`)
	claim    = regexp.MustCompile(`^\s*- `)
	cite     = regexp.MustCompile(`\[(?:#\d+(?:@\w+)?|([^\]\s:]+):\d+)\]`)
)

type Job struct {
	Drive       string   `json:"drive"`
	State       string   `json:"state"`
	Binary      string   `json:"binary"`
	Question    string   `json:"question"`
	Reader      string   `json:"reader"`
	Triage      string   `json:"triage"`
	Launcher    string   `json:"launcher"`
	Transcripts []string `json:"transcripts"`
	Records     []string `json:"records"`
	Logs        []string `json:"logs"`
	Contract    string   `json:"contract"`
}

type FixLane struct {
	Lane        string `json:"lane"`
	Deliverable string `json:"deliverable"`
	Due         string `json:"due"`
	Model       string `json:"model"`
	Effort      string `json:"effort"`
	Brief       string `json:"brief"`
}

func TriageLane(reader string) string {
	if match := numbered.FindStringSubmatch(reader); match != nil {
		return match[1] + "-triage-" + match[2]
	}
	return reader + "-triage"
}

func (j Job) brief(lane string) string { return filepath.Join(j.State, lane, briefFile) }

func (j Job) JobPath() string { return filepath.Join(j.State, j.Reader, jobFile) }

func (j Job) Findings() string { return filepath.Join(j.State, j.Reader, "findings.md") }

func (j Job) FixList() string { return filepath.Join(j.State, j.Triage, "fix-lanes.json") }

func (j Job) OnePager() string { return filepath.Join(j.State, j.Triage, "one-pager.md") }

func (j Job) launchList() string { return filepath.Join(j.State, j.Triage, "launch-list.md") }

func Load(path string) (Job, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return Job{}, err
	}
	var job Job
	if err := json.Unmarshal(raw, &job); err != nil {
		return Job{}, fmt.Errorf("%s: %w", path, err)
	}
	return job, nil
}

func fileFault(kind, path string) string {
	info, err := os.Stat(path)
	if err != nil {
		return fmt.Sprintf("%s %s: %v", kind, path, err)
	}
	if !info.Mode().IsRegular() {
		return fmt.Sprintf("%s %s is not a file; name each file, since a directory or a glob means a recursive search", kind, path)
	}
	return ""
}

func (j Job) startFaults() []string {
	var faults []string
	if !laneName.MatchString(j.Reader) {
		faults = append(faults, fmt.Sprintf("lane %q is not lowercase letters, digits and dashes", j.Reader))
	}
	if j.Launcher == "" {
		faults = append(faults, "no launcher: name it with --launcher, or run this from a lane")
	}
	if strings.TrimSpace(j.Question) == "" {
		faults = append(faults, "the question is blank")
	}
	if len(j.Transcripts)+len(j.Records)+len(j.Logs) == 0 {
		faults = append(faults, "the corpus is empty: name a --transcript, --records or --log")
	}
	for _, path := range j.Transcripts {
		if filepath.Ext(path) != ".jsonl" {
			faults = append(faults, "transcript "+path+" is not a .jsonl file; cc-transcript list prints the path")
		}
	}
	check := func(kind string, paths ...string) {
		for _, path := range paths {
			if fault := fileFault(kind, path); fault != "" {
				faults = append(faults, fault)
			}
		}
	}
	check("transcript", j.Transcripts...)
	check("log", j.Logs...)
	if j.Contract != "" {
		check("contract", j.Contract)
	}
	return faults
}

func refused(what string, faults []string) error {
	return fmt.Errorf("%s refused: %s", what, strings.Join(faults, "; "))
}

func render(brief *template.Template, job Job, path string) error {
	var text bytes.Buffer
	if err := brief.Execute(&text, job); err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		return err
	}
	return os.WriteFile(path, text.Bytes(), 0o600)
}

func (j Job) Start(ctx context.Context, poster orca.Poster) error {
	if faults := j.startFaults(); len(faults) > 0 {
		return refused("lr bulk read", faults)
	}
	if err := render(readerBrief, j, j.brief(j.Reader)); err != nil {
		return err
	}
	if err := render(triageBrief, j, j.brief(j.Triage)); err != nil {
		return err
	}
	raw, err := json.MarshalIndent(j, "", "  ")
	if err != nil {
		return err
	}
	if err := os.WriteFile(j.JobPath(), raw, 0o600); err != nil {
		return err
	}
	return poster.LaunchOrExit(ctx, orca.Order{Lane: j.Reader, Model: readerModel, Effort: readEffort, Brief: j.brief(j.Reader)}, launchWait)
}

func (j Job) findingsFaults(text string) []string {
	var faults []string
	known := map[string]bool{}
	for _, path := range slices.Concat(j.Transcripts, j.Logs) {
		known[filepath.Base(path)] = true
	}
	claims := 0
	for i, line := range strings.Split(text, "\n") {
		if !claim.MatchString(line) {
			continue
		}
		claims++
		cites := cite.FindAllStringSubmatch(line, -1)
		if len(cites) == 0 {
			faults = append(faults, fmt.Sprintf("line %d cites no record seq or file line", i+1))
		}
		for _, found := range cites {
			if found[1] != "" && !known[filepath.Base(found[1])] {
				faults = append(faults, fmt.Sprintf("line %d cites %s, which is not in the corpus", i+1, found[1]))
			}
		}
	}
	if claims == 0 {
		faults = append(faults, "it holds no claim; if the corpus has no answer, one claim says so and cites what was read")
	}
	return faults
}

func (j Job) StartTriage(ctx context.Context, poster orca.Poster) error {
	raw, err := os.ReadFile(j.Findings())
	if err != nil {
		return err
	}
	if faults := j.findingsFaults(string(raw)); len(faults) > 0 {
		return refused(j.Findings(), faults)
	}
	return poster.LaunchOrExit(ctx, orca.Order{Lane: j.Triage, Model: triageModel, Effort: readEffort, Brief: j.brief(j.Triage)}, launchWait)
}

func (j Job) laneFaults(ctx context.Context, poster orca.Poster, lane FixLane, seen map[string]bool) ([]string, error) {
	var faults []string
	if !laneName.MatchString(lane.Lane) {
		faults = append(faults, fmt.Sprintf("lane %q is not lowercase letters, digits and dashes", lane.Lane))
	}
	if seen[lane.Lane] {
		faults = append(faults, "lane "+lane.Lane+" is listed twice")
	}
	seen[lane.Lane] = true
	if strings.TrimSpace(lane.Deliverable) == "" {
		faults = append(faults, "lane "+lane.Lane+" has no deliverable")
	}
	due, err := time.Parse(time.RFC3339, lane.Due)
	switch {
	case err != nil:
		faults = append(faults, fmt.Sprintf("lane %s's due %q is not an RFC3339 time", lane.Lane, lane.Due))
	case !due.After(poster.Now()):
		faults = append(faults, fmt.Sprintf("lane %s's due %s has passed", lane.Lane, lane.Due))
	}
	if !filepath.IsAbs(lane.Brief) {
		faults = append(faults, fmt.Sprintf("lane %s's brief %q is not an absolute path", lane.Lane, lane.Brief))
	}
	runner, err := poster.Faults(ctx, orca.Order{Lane: lane.Lane, Model: lane.Model, Effort: lane.Effort, Brief: lane.Brief})
	if err != nil {
		return nil, err
	}
	faults = append(faults, runner...)
	if raw, err := os.ReadFile(lane.Brief); err == nil && j.Contract != "" && !bytes.Contains(raw, []byte(j.Contract)) {
		faults = append(faults, fmt.Sprintf("brief %s does not name the contract %s", lane.Brief, j.Contract))
	}
	return faults, nil
}

func (j Job) renderList(lanes []FixLane, link string) string {
	var list strings.Builder
	fmt.Fprintf(&list, "# Fix lanes from %s, for %s to launch\n\nFindings: %s\nOne-pager: %s\n", j.Reader, j.Launcher, j.Findings(), link)
	for _, lane := range lanes {
		fmt.Fprintf(&list, "\n## %s\n\n%s\n\n    lr launch lane %s %s %s %s --drive %s --lane %s\n", lane.Lane, lane.Deliverable, lane.Lane, lane.Model, lane.Effort, lane.Brief, j.Drive, j.Launcher)
	}
	return list.String()
}

func (j Job) filedText(names []string, link string) string {
	if len(names) == 0 {
		return fmt.Sprintf("%s: no fix lanes from %s; findings %s; one-pager %s", j.Triage, j.Reader, j.Findings(), link)
	}
	return fmt.Sprintf("%s: %d fix lanes to launch from %s: %s; commands in %s, each lane's deadline in its due attr; findings %s; one-pager %s", j.Triage, len(names), j.Reader, strings.Join(names, ", "), j.launchList(), j.Findings(), link)
}

func (j Job) File(ctx context.Context, poster orca.Poster, link string) error {
	parsed, err := url.ParseRequestURI(link)
	if err != nil || parsed.Host == "" || !strings.HasPrefix(parsed.Scheme, "http") {
		return fmt.Errorf("--one-pager %q is not a link", link)
	}
	raw, err := os.ReadFile(j.FixList())
	if err != nil {
		return err
	}
	var lanes []FixLane
	if err := json.Unmarshal(raw, &lanes); err != nil {
		return fmt.Errorf("%s: %w", j.FixList(), err)
	}
	var faults []string
	seen := map[string]bool{}
	names := make([]string, 0, len(lanes))
	dues := map[string]string{}
	for _, lane := range lanes {
		found, err := j.laneFaults(ctx, poster, lane, seen)
		if err != nil {
			return err
		}
		faults = append(faults, found...)
		names = append(names, lane.Lane)
		dues["due:"+lane.Lane] = lane.Due
	}
	if len(faults) > 0 {
		return refused(j.FixList(), faults)
	}
	if err := os.WriteFile(j.launchList(), []byte(j.renderList(lanes, link)), 0o600); err != nil {
		return err
	}
	_, err = poster.Bus.Post(ctx, client.Draft{
		Drive:    j.Drive,
		Lane:     j.Triage,
		Kind:     "done",
		To:       []string{j.Launcher},
		Topic:    j.Reader,
		ID:       "bulk-file:" + j.Reader,
		LaneRefs: names,
		Attrs:    dues,
		URL:      link,
		Path:     j.launchList(),
		Text:     j.filedText(names, link),
	})
	return err
}
