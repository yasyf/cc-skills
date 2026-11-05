package grading

import (
	"cmp"
	"context"
	"encoding/json"
	"fmt"
	"maps"
	"path"
	"path/filepath"
	"slices"
	"strconv"
	"strings"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/grading/grader"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	runnerLane    = bus.RunnerLane
	defaultModel  = "sol"
	optionsBranch = "options/"
	docChars      = 7
)

type job struct {
	spec    any
	sources string
}

type livePR struct {
	Title       string `json:"title"`
	Body        string `json:"body"`
	HeadRefName string `json:"headRefName"`
}

func (p *JobPool) readPR(ctx context.Context, repo, pr string) livePR {
	argv := []string{"ccx", "vcs", "gh", "--max-age", "0s", "--", "pr", "view", pr, "--repo", repo, "--json", "title,body,headRefName"}
	var live livePR
	result, err := p.Runner.Run(ctx, shell.Command{Argv: argv, Timeout: readTimeout})
	if err == nil {
		err = json.Unmarshal([]byte(result.Stdout), &live)
	}
	if err != nil {
		p.Log.Warn("the graders did not read the PR; its pass reads it itself", "pr", pr, "error", err)
		return livePR{}
	}
	return live
}

func (p *JobPool) baseJob(d desk, facts machine.Facts, sprite string, settled map[string]int64) grader.Job {
	to := []string{runnerLane}
	if facts.Lane != "" {
		to = append(to, facts.Lane)
	}
	for _, lane := range d.spec.To {
		if stem := client.Stem(lane); !slices.Contains(to, stem) {
			to = append(to, stem)
		}
	}
	checkout := d.spec.Checkout
	if checkout == "" {
		checkout = p.landing.Checkout
	}
	model := d.spec.Model
	if model == "" {
		model = defaultModel
	}
	return grader.Job{
		Drive:    p.drive,
		Repo:     facts.Repo,
		PR:       strconv.Itoa(facts.Number),
		Head:     facts.Head,
		Lane:     facts.Lane,
		To:       to,
		Checkout: checkout,
		Model:    model,
		Fast:     d.spec.Fast == nil || *d.spec.Fast,
		Settled:  settled,
		Sprite:   sprite,
		Picks:    p.picks,
	}
}

func (p *JobPool) finderJob(ctx context.Context, d desk, facts machine.Facts, sprite string) (job, error) {
	pr := strconv.Itoa(facts.Number)
	pull := facts.Repo + "#" + pr
	found, err := client.Tail(ctx, client.Query{Drive: p.drive, Kinds: []string{kindReview, kindHead}, PR: pull})
	if err != nil {
		return job{}, err
	}
	reviews := finderReviews(found, facts.Repo, pr)
	graded := gradedReviews(reviews)
	if slices.ContainsFunc(graded, func(r review) bool { return grader.NamesHead(r.head, facts.Head) }) {
		return job{}, ErrAnswered
	}
	last, prior := since(graded, found, facts.Head, facts.Repo)
	ruled, err := client.Tail(ctx, client.Query{Drive: p.drive, Kinds: []string{kindDecide}, Lanes: []string{rootLane}, PR: pull})
	if err != nil {
		return job{}, err
	}
	named := make([]client.Record, 0, len(reviews))
	for _, r := range reviews {
		named = append(named, r.Record)
	}
	settled, err := rootSettled(ctx, p.drive, facts.Repo, pr, named)
	if err != nil {
		return job{}, err
	}
	previous := ""
	if len(named) > 0 {
		previous = previousHead(found, facts.Repo, pr, facts.Head)
	}
	return job{spec: grader.FinderJob{
		Job:      p.baseJob(d, facts, sprite, settled),
		Since:    last,
		Prior:    prior,
		Previous: previous,
		History:  jobRecords(named),
		Ruled:    jobRecords(ruled),
	}}, nil
}

func jobRecords(records []client.Record) []grader.Record {
	listed := make([]grader.Record, 0, len(records))
	for _, r := range records {
		listed = append(listed, grader.Record{Seq: r.Seq, Lane: r.Lane, Text: r.Text, Path: r.Refs.Path})
	}
	return listed
}

func (p *JobPool) fidelityJob(ctx context.Context, d desk, facts machine.Facts, sprite, directory string, live livePR) (job, error) {
	pr := strconv.Itoa(facts.Number)
	pull := facts.Repo + "#" + pr
	found, err := client.Tail(ctx, client.Query{Drive: p.drive, Kinds: []string{kindReview, kindHead}, PR: pull})
	if err != nil {
		return job{}, err
	}
	lanes := withSuccessors(bus.FidelityLanes(), found)
	grades := fidelityGrades(found, lanes, facts.Repo, pr)
	sent := slices.DeleteFunc(slices.Clone(found), func(r client.Record) bool {
		return r.Kind != kindReview || r.Lane != grader.FidelityLane || !strings.HasPrefix(r.Text, fidelityReader)
	})
	if anyNaming(grades, facts.Head) || anyNaming(sent, facts.Head) {
		return job{}, ErrAnswered
	}
	contract, err := p.contract(ctx, facts.Lane, facts.Repo, pr)
	if err != nil {
		return job{}, err
	}
	rulings, err := client.Tail(ctx, client.Query{Drive: p.drive, Kinds: []string{kindDecide, kindAnswer}, Lanes: []string{rootLane}, PR: pull})
	if err != nil {
		return job{}, err
	}
	settled, err := rootSettled(ctx, p.drive, facts.Repo, pr, grades)
	if err != nil {
		return job{}, err
	}
	sources, err := p.fidelitySources(ctx, d.spec, facts.Lane, live)
	if err != nil {
		return job{}, err
	}
	gos, err := client.Tail(ctx, client.Query{Drive: p.drive, Kinds: []string{kindGo}, PR: pull})
	if err != nil {
		return job{}, err
	}
	spec := grader.FidelityJob{
		Job:            p.baseJob(d, facts, sprite, settled),
		Readers:        slices.DeleteFunc(slices.Clone(lanes), func(lane string) bool { return lane == grader.FidelityLane }),
		Lanes:          lanes,
		Grades:         gradeRecords(grades),
		OldApps:        oldApps(found, lanes, facts.Repo, pr),
		Corrected:      corrected(rulings),
		Rulings:        jobRulings(rulings),
		Contract:       contract,
		Exempt:         p.classes(),
		ExemptGrades:   p.exemptGrades(),
		Baseline:       d.spec.Baseline,
		Claims:         claimedClasses(gos, facts),
		ServiceTouches: d.spec.ServiceTouches,
	}
	if !facts.Tapped() {
		spec.Since = facts.TapFrom
	}
	if sources != "" {
		spec.Sources = filepath.Join(directory, sourcesFile)
	}
	return job{spec: spec, sources: sources}, nil
}

func (p *JobPool) contract(ctx context.Context, lane, repo, pr string) (string, error) {
	if lane == "" {
		return "", nil
	}
	contracts, err := client.Tail(ctx, client.Query{Drive: p.drive, Kinds: []string{kindContract}, Lanes: []string{lane}})
	if err != nil {
		return "", err
	}
	return newestContract(contracts, repo, pr), nil
}

func (p *JobPool) classes() map[string]string {
	classes := map[string]string{"port": cmp.Or(p.landing.PortPhrase, gates.PortGrade), "test-only": gates.TestOnlyGrade}
	for _, phrase := range p.exemptGrades() {
		if !slices.Contains(slices.Collect(maps.Values(classes)), phrase) {
			classes[gates.ClassOf(phrase)] = phrase
		}
	}
	return classes
}

func claimedClasses(gos []client.Record, facts machine.Facts) []string {
	var claims []string
	for _, record := range gos {
		for _, fact := range bus.LegacyFacts(record, facts.Repo, []string{record.Lane}) {
			if fact.Kind == "exempt" && fact.PR == facts.Number && strings.HasPrefix(facts.Head, fact.Head) && !slices.Contains(claims, fact.Value) {
				claims = append(claims, fact.Value)
			}
		}
	}
	return claims
}

func (p *JobPool) exemptGrades() []string {
	return gates.Grades(p.landing.ExemptGrades, p.landing.GoClasses, p.landing.PortPhrase)
}

func gradeRecords(grades []client.Record) []grader.Record {
	listed := make([]grader.Record, 0, len(grades))
	for _, r := range grades {
		listed = append(listed, grader.Record{Seq: r.Seq, Lane: r.Lane, Text: r.Text})
	}
	return listed
}

func corrected(rulings []client.Record) []int64 {
	answered := make([]int64, 0, len(rulings))
	for _, r := range rulings {
		if r.Kind == kindDecide && r.Re != 0 && classWrong.MatchString(r.Text) {
			answered = append(answered, r.Re)
		}
	}
	return answered
}

func jobRulings(rulings []client.Record) []grader.Ruling {
	listed := make([]grader.Ruling, 0, len(rulings))
	for _, r := range rulings {
		listed = append(listed, grader.Ruling{Seq: r.Seq, Text: fullText(r)})
	}
	return listed
}

func (p *JobPool) fidelitySources(ctx context.Context, spec Spec, lane string, live livePR) (string, error) {
	sections, err := p.docSections(ctx, laneSources(spec.Sources.Docs, lane))
	if err != nil {
		return "", err
	}
	named := laneSources(spec.Sources.Records, lane)
	cited := slices.DeleteFunc(citedSeqs(live.Body), func(seq int64) bool { return slices.Contains(named, seq) })
	records, err := client.Among(ctx, p.drive, append(slices.Clone(named), cited...))
	if err != nil {
		return "", err
	}
	for _, r := range records {
		if slices.Contains(cited, r.Seq) && (r.Lane != rootLane || (r.Kind != kindDefect && r.Kind != kindDecide && r.Kind != kindAnswer)) {
			continue
		}
		sections = append(sections, fmt.Sprintf("## cci #%d, %s from %s\n\n%s", r.Seq, r.Kind, r.Lane, fullText(r)))
	}
	card, err := p.boardCard(ctx, live.HeadRefName)
	if err != nil {
		return "", err
	}
	if card != "" {
		sections = append(sections, card)
	}
	return strings.Join(sections, "\n\n"), nil
}

func (p *JobPool) docSections(ctx context.Context, idents []string) ([]string, error) {
	sections := make([]string, 0, len(idents))
	for _, ident := range idents {
		result, err := p.Runner.Run(ctx, shell.Command{Argv: []string{"ccn", "-R", p.briefs, "doc", "show", ident, "--json"}, Timeout: readTimeout})
		if err != nil || strings.TrimSpace(result.Stdout) == "" {
			p.Log.Warn("the graders did not read a cc-notes source", "doc", ident, "error", err)
			continue
		}
		var doc struct {
			ID    string `json:"id"`
			Title string `json:"title"`
			Body  string `json:"body"`
		}
		if err := json.Unmarshal([]byte(result.Stdout), &doc); err != nil {
			return nil, err
		}
		sections = append(sections, fmt.Sprintf("## cc-notes doc %s: %s\n\n%s", doc.ID[:min(docChars, len(doc.ID))], doc.Title, strings.TrimSpace(doc.Body)))
	}
	return sections, nil
}

func (p *JobPool) boardCard(ctx context.Context, branch string) (string, error) {
	name, onOption := strings.CutPrefix(branch, optionsBranch)
	at := strings.LastIndex(name, "-")
	if !onOption || at < 0 {
		return "", nil
	}
	session, err := grader.RootSession(p.Config, p.drive)
	if err != nil || session == "" {
		return "", err
	}
	card, option := name[:at], name[at+1:]
	result, err := p.Runner.Run(ctx, shell.Command{Argv: []string{"cc-present", "block", "show", card, "--session", session}, Timeout: readTimeout})
	if err != nil {
		p.Log.Warn("the graders did not read a board card", "card", card, "error", err)
		return "", nil
	}
	return fmt.Sprintf("## Board card %s, whose option %s this branch builds\n\n%s", card, option, strings.TrimSpace(result.Stdout)), nil
}

func laneSources[T comparable](table map[string][]T, lane string) []T {
	var entries []T
	for _, pattern := range slices.Sorted(maps.Keys(table)) {
		if matched, err := path.Match(pattern, lane); err != nil || !matched {
			continue
		}
		for _, entry := range table[pattern] {
			if !slices.Contains(entries, entry) {
				entries = append(entries, entry)
			}
		}
	}
	return entries
}
