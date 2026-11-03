package plans

import (
	"cmp"
	"context"
	"encoding/json"
	"fmt"
	"iter"
	"regexp"
	"slices"
	"strings"
	"sync"
)

const (
	Done       = "done"
	InProgress = "in_progress"
	Blocked    = "blocked"
	Open       = "open"
	Unmapped   = "unmapped"
	Unassigned = "unassigned"

	cancelled  = "cancelled"
	met        = "met"
	failed     = "failed"
	stepLabel  = "step:"
	titleChars = 60
	shortID    = 7
	workers    = 4
)

var (
	stepHeading = regexp.MustCompile(`(?m)^### (?:Step )?(\d+(?:\.\d+)*)\.\s+(.+?)\s*$`)
	everyTask   = []string{"task", "list", "--all", "--all-branches", "--include-archived", "--json"}
	stateOrder  = []string{Blocked, InProgress, Open, Done}
)

type Ccn func(ctx context.Context, args ...string) (string, error)

type Task struct {
	ID       string
	Title    string
	State    string
	Owner    string
	Since    string
	Waiting  string
	Priority int
}

type Step struct {
	Number string
	Title  string
	Tasks  []Task
}

func (s *Step) Count(state string) int {
	count := 0
	for _, task := range s.Tasks {
		if task.State == state {
			count++
		}
	}
	return count
}

type Plan struct {
	ID     string
	Title  string
	Status string
	Steps  []*Step
}

func (p *Plan) count(state string) int {
	count := 0
	for _, step := range p.Steps {
		count += step.Count(state)
	}
	return count
}

func (p *Plan) unmapped() *Step {
	if len(p.Steps) == 0 || p.Steps[len(p.Steps)-1].Number != Unmapped {
		p.Steps = append(p.Steps, &Step{Number: Unmapped, Title: "No step label"})
	}
	return p.Steps[len(p.Steps)-1]
}

type IdlePlan struct {
	ID    string `json:"id"`
	Title string `json:"title"`
}

type Progress struct {
	Plans      []*Plan
	Unassigned *Step
	Cancelled  int
	Idle       []IdlePlan
}

func (p *Progress) Steps() iter.Seq2[*Plan, *Step] {
	return func(yield func(*Plan, *Step) bool) {
		for _, plan := range p.Plans {
			for _, step := range plan.Steps {
				if !yield(plan, step) {
					return
				}
			}
		}
		if len(p.Unassigned.Tasks) > 0 {
			yield(nil, p.Unassigned)
		}
	}
}

func (p *Progress) Counts() map[string]int {
	counts := map[string]int{Done: 0, InProgress: 0, Blocked: 0, Open: 0}
	for _, step := range p.Steps() {
		for state := range counts {
			counts[state] += step.Count(state)
		}
	}
	return counts
}

type criterion struct {
	Text   string `json:"text"`
	Status string `json:"status"`
}

type taskRecord struct {
	ID        string      `json:"id"`
	Title     string      `json:"title"`
	Status    string      `json:"status"`
	Plan      string      `json:"plan"`
	Assignee  string      `json:"assignee"`
	BlockedBy []string    `json:"blocked_by"`
	Labels    []string    `json:"labels"`
	Priority  int         `json:"priority"`
	CreatedAt string      `json:"created_at"`
	UpdatedAt string      `json:"updated_at"`
	ClosedAt  string      `json:"closed_at"`
	StartedAt string      `json:"started_at"`
	Criteria  []criterion `json:"criteria"`
}

func (t taskRecord) stepLabel() string {
	for _, label := range t.Labels {
		if number, ok := strings.CutPrefix(label, stepLabel); ok {
			return number
		}
	}
	return ""
}

func (t taskRecord) failedCriterion() (criterion, bool) {
	for _, item := range t.Criteria {
		if item.Status == failed {
			return item, true
		}
	}
	return criterion{}, false
}

type planRecord struct {
	ID     string `json:"id"`
	Title  string `json:"title"`
	Status string `json:"status"`
	Body   string `json:"body"`
}

func Clip(text string, limit int) string {
	text = strings.Join(strings.Fields(text), " ")
	runes := []rune(text)
	if len(runes) <= limit {
		return text
	}
	return string(runes[:limit-1]) + "…"
}

func short(id string) string {
	return id[:min(shortID, len(id))]
}

func listed[T any](ctx context.Context, ccn Ccn, args ...string) ([]T, error) {
	out, err := ccn(ctx, args...)
	if err != nil {
		return nil, err
	}
	var found []T
	if strings.TrimSpace(out) == "" {
		return found, nil
	}
	return found, json.Unmarshal([]byte(out), &found)
}

func shown(ctx context.Context, ccn Ccn, summaries []taskRecord) (map[string]taskRecord, error) {
	full := make([]taskRecord, len(summaries))
	errs := make([]error, len(summaries))
	slots := make(chan struct{}, workers)
	var running sync.WaitGroup
	for at, summary := range summaries {
		running.Go(func() {
			slots <- struct{}{}
			defer func() { <-slots }()
			out, err := ccn(ctx, "task", "show", summary.ID, "--json")
			if err != nil {
				errs[at] = err
				return
			}
			errs[at] = json.Unmarshal([]byte(out), &full[at])
		})
	}
	running.Wait()
	byID := make(map[string]taskRecord, len(summaries))
	for at, summary := range summaries {
		if errs[at] != nil {
			return nil, errs[at]
		}
		byID[summary.ID] = full[at]
	}
	return byID, nil
}

func StepsOf(body string) []*Step {
	seen := map[string]bool{}
	steps := []*Step{}
	for _, match := range stepHeading.FindAllStringSubmatch(body, -1) {
		if !seen[match[1]] {
			seen[match[1]] = true
			steps = append(steps, &Step{Number: match[1], Title: match[1] + ". " + match[2]})
		}
	}
	return steps
}

func ownerOf(assignee string) string {
	owner, _, _ := strings.Cut(assignee, " <")
	return owner
}

func waitingOn(full taskRecord, blockers []taskRecord) string {
	if len(blockers) > 0 {
		names := make([]string, 0, len(blockers))
		for _, blocker := range blockers {
			names = append(names, fmt.Sprintf("%s (%s)", Clip(blocker.Title, titleChars/2), short(blocker.ID)))
		}
		return "blocked by " + strings.Join(names, ", ")
	}
	if item, ok := full.failedCriterion(); ok {
		return "failed: " + item.Text
	}
	for _, item := range full.Criteria {
		if item.Status != met {
			return item.Text
		}
	}
	return ""
}

func taskOf(full taskRecord, summaries map[string]taskRecord) (Task, error) {
	var blockers []taskRecord
	if full.Status != Done {
		for _, id := range full.BlockedBy {
			blocker, ok := summaries[id]
			if !ok {
				return Task{}, fmt.Errorf("task %s is blocked by %s, which no task list names", short(full.ID), short(id))
			}
			if blocker.Status != Done && blocker.Status != cancelled {
				blockers = append(blockers, blocker)
			}
		}
	}
	_, hasFailed := full.failedCriterion()
	state := full.Status
	if full.Status != Done && (len(blockers) > 0 || hasFailed) {
		state = Blocked
	}
	since := ""
	switch full.Status {
	case Done:
		since = full.ClosedAt
	case InProgress:
		since = full.StartedAt
	}
	waiting := ""
	if full.Status != Done {
		waiting = waitingOn(full, blockers)
	}
	return Task{
		ID:       full.ID,
		Title:    full.Title,
		State:    state,
		Owner:    ownerOf(full.Assignee),
		Since:    cmp.Or(since, full.CreatedAt, full.UpdatedAt),
		Waiting:  waiting,
		Priority: full.Priority,
	}, nil
}

func registered(summaries []taskRecord, labelled []taskRecord, planIDs []string) []taskRecord {
	byLabel := make(map[string]bool, len(labelled))
	for _, summary := range labelled {
		byLabel[summary.ID] = true
	}
	found := []taskRecord{}
	for _, summary := range summaries {
		onPlan := summary.Plan != "" && slices.ContainsFunc(planIDs, func(plan string) bool { return strings.HasPrefix(summary.Plan, plan) })
		if byLabel[summary.ID] || onPlan {
			found = append(found, summary)
		}
	}
	return found
}

func wantedPlans(pinned []string, programPlans []IdlePlan, tasks []taskRecord) []string {
	held := map[string]bool{}
	for _, summary := range tasks {
		if summary.Plan != "" {
			held[summary.Plan] = true
		}
	}
	wanted := slices.Clone(pinned)
	for _, plan := range programPlans {
		if held[plan.ID] {
			wanted = append(wanted, plan.ID)
		}
	}
	rest := make([]string, 0, len(held))
	for plan := range held {
		rest = append(rest, plan)
	}
	slices.Sort(rest)
	seen := map[string]bool{}
	unique := make([]string, 0, len(wanted)+len(rest))
	for _, ident := range append(wanted, rest...) {
		if !seen[ident] {
			seen[ident] = true
			unique = append(unique, ident)
		}
	}
	return unique
}

func Read(ctx context.Context, ccn Ccn, program string, pinned ...string) (*Progress, error) {
	all, err := listed[taskRecord](ctx, ccn, everyTask...)
	if err != nil {
		return nil, err
	}
	summaries := make(map[string]taskRecord, len(all))
	for _, summary := range all {
		summaries[summary.ID] = summary
	}
	var labelled []taskRecord
	var programPlans []IdlePlan
	if program != "" {
		label := "program:" + program
		if labelled, err = listed[taskRecord](ctx, ccn, append(slices.Clone(everyTask), "--label", label)...); err != nil {
			return nil, err
		}
		if programPlans, err = listed[IdlePlan](ctx, ccn, "plan", "list", "--label", label, "--json"); err != nil {
			return nil, err
		}
	}
	planIDs := slices.Clone(pinned)
	for _, plan := range programPlans {
		planIDs = append(planIDs, plan.ID)
	}
	tasks := registered(all, labelled, planIDs)
	full, err := shown(ctx, ccn, tasks)
	if err != nil {
		return nil, err
	}
	progress := &Progress{Unassigned: &Step{Number: Unassigned, Title: "Unassigned"}}
	byPlan := map[string]*Plan{}
	for _, ident := range wantedPlans(pinned, programPlans, tasks) {
		out, err := ccn(ctx, "plan", "show", ident, "--json")
		if err != nil {
			return nil, err
		}
		var record planRecord
		if err := json.Unmarshal([]byte(out), &record); err != nil {
			return nil, err
		}
		if byPlan[record.ID] == nil {
			byPlan[record.ID] = &Plan{ID: record.ID, Title: record.Title, Status: record.Status, Steps: StepsOf(record.Body)}
			progress.Plans = append(progress.Plans, byPlan[record.ID])
		}
	}
	for _, summary := range tasks {
		record := full[summary.ID]
		if record.Status == cancelled {
			progress.Cancelled++
			continue
		}
		task, err := taskOf(record, summaries)
		if err != nil {
			return nil, err
		}
		place(progress, byPlan[record.Plan], record, task)
	}
	for _, plan := range programPlans {
		if byPlan[plan.ID] == nil {
			progress.Idle = append(progress.Idle, plan)
		}
	}
	for _, step := range progress.Steps() {
		slices.SortStableFunc(step.Tasks, func(a, b Task) int {
			return cmp.Or(
				cmp.Compare(slices.Index(stateOrder, a.State), slices.Index(stateOrder, b.State)),
				cmp.Compare(a.Priority, b.Priority),
				cmp.Compare(a.Since, b.Since),
			)
		})
	}
	return progress, nil
}

func place(progress *Progress, plan *Plan, record taskRecord, task Task) {
	if plan == nil {
		progress.Unassigned.Tasks = append(progress.Unassigned.Tasks, task)
		return
	}
	number := record.stepLabel()
	at := slices.IndexFunc(plan.Steps, func(step *Step) bool { return step.Number == number })
	if at < 0 {
		step := plan.unmapped()
		step.Tasks = append(step.Tasks, task)
		return
	}
	plan.Steps[at].Tasks = append(plan.Steps[at].Tasks, task)
}

func SummaryLines(progress *Progress) []string {
	lines := make([]string, 0, len(progress.Plans)+1)
	for _, plan := range progress.Plans {
		var left []string
		total := 0
		for _, step := range plan.Steps {
			total += len(step.Tasks)
			if done := step.Count(Done); len(step.Tasks) > 0 && done < len(step.Tasks) {
				name := step.Number
				if name == Unmapped {
					name = "no step"
				}
				left = append(left, fmt.Sprintf("%s %d/%d", name, done, len(step.Tasks)))
			}
		}
		line := fmt.Sprintf("- Plan `%s` %s: %d of %d tasks done, %d blocked", short(plan.ID), Clip(plan.Title, titleChars), plan.count(Done), total, plan.count(Blocked))
		if len(left) > 0 {
			line += "; steps open: " + strings.Join(left, ", ")
		}
		lines = append(lines, line)
	}
	if tasks := progress.Unassigned.Tasks; len(tasks) > 0 {
		lines = append(lines, fmt.Sprintf("- %d of %d drive tasks outside any plan done", progress.Unassigned.Count(Done), len(tasks)))
	}
	return lines
}
