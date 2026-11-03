package grading

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/grading/grader"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
)

const (
	wholeMark   = "-whole-"
	wholeAsked  = "finder: whole"
	wholeRead   = "over whole "
	pendingDir  = "pending"
	kindAsk     = "ask"
	cursorAsks  = "grading-whole-asks"
	cursorBegun = int64(1)
)

var (
	textPR      = regexp.MustCompile(`\b([\w.-]+/[\w.-]+)#(\d+)\b|(?:^|[^\w/])#(\d{4,})\b`)
	regradeWord = regexp.MustCompile(`(?i)\b` + regexp.QuoteMeta(grader.FinderLane) + `:\s*re-?grade\b([^;.]*)`)
)

type wholeAsk struct {
	PR   string `json:"pr"`
	Lane string `json:"lane"`
	Key  string `json:"key"`
}

func isWhole(worker string) bool {
	ref := refOf(worker)
	return ref.lane == grader.FinderLane && strings.Contains(ref.base, wholeMark)
}

func textPRs(text, repo string) []string {
	var named, bare []string
	for _, found := range textPR.FindAllStringSubmatch(text, -1) {
		switch {
		case found[3] != "":
			bare = append(bare, found[3])
		case strings.EqualFold(found[1], repo):
			named = append(named, found[2])
		}
	}
	if len(named) > 0 {
		return unique(named)
	}
	return unique(bare)
}

func requested(r client.Record, repo string) []string {
	if !slices.Contains(r.To, grader.FinderLane) {
		return nil
	}
	switch string(r.Kind) {
	case kindDecide, kindDefect:
		if r.Lane != rootLane {
			return nil
		}
		var prs []string
		for _, order := range regradeWord.FindAllStringSubmatch(r.Text, -1) {
			prs = append(prs, textPRs(order[1], repo)...)
		}
		return unique(prs)
	case kindAsk:
		if !strings.Contains(strings.ToLower(r.Text), wholeAsked) {
			return nil
		}
		if len(r.Refs.PRs) == 0 {
			return textPRs(r.Text, repo)
		}
		prs := make([]string, 0, len(r.Refs.PRs))
		for _, named := range r.Refs.PRs {
			if named.Repo == "" || strings.EqualFold(named.Repo, repo) {
				prs = append(prs, strconv.Itoa(named.Number))
			}
		}
		return prs
	}
	return nil
}

func unique(list []string) []string {
	kept := make([]string, 0, len(list))
	for _, entry := range list {
		if !slices.Contains(kept, entry) {
			kept = append(kept, entry)
		}
	}
	return kept
}

func (p *JobPool) readAsks(ctx context.Context) error {
	cursor, err := p.Cursors.Cursor(cursorAsks)
	if err != nil {
		return err
	}
	found, err := client.Tail(ctx, client.Query{Drive: p.drive, Kinds: []string{kindAsk, kindDecide, kindDefect}, After: cursor})
	if err != nil {
		return err
	}
	newest := max(cursor, cursorBegun)
	for _, r := range found {
		newest = max(newest, r.Seq)
		if cursor == 0 {
			continue
		}
		for _, pr := range requested(r, p.repo()) {
			if err := p.queueWhole(wholeAsk{pr, r.Lane, strconv.FormatInt(r.Seq, 10)}); err != nil {
				return err
			}
		}
	}
	return p.Cursors.Advance(cursorAsks, newest)
}

func (p *JobPool) queueWhole(ask wholeAsk) error {
	directory := filepath.Join(p.root, grader.FinderLane, pendingDir)
	if err := os.MkdirAll(directory, 0o750); err != nil {
		return err
	}
	path := filepath.Join(directory, ask.PR+wholeMark+ask.Key)
	if exists(path) {
		return nil
	}
	return writeAtomic(path, ask)
}

func (p *JobPool) queueUngraded(directory string) error {
	data, err := os.ReadFile(filepath.Join(directory, grader.JobFile))
	if err != nil {
		return err
	}
	var spec grader.FinderJob
	if err := json.Unmarshal(data, &spec); err != nil {
		return err
	}
	return p.queueWhole(wholeAsk{spec.PR, spec.Lane, short(spec.Head)})
}

func ungraded(directory string) bool {
	data, err := os.ReadFile(filepath.Join(directory, grader.ResultFile))
	if err != nil {
		return false
	}
	var outcome struct {
		Ungraded bool `json:"ungraded"`
	}
	return json.Unmarshal(data, &outcome) == nil && outcome.Ungraded
}

func (p *JobPool) Whole(ctx context.Context, snapshot map[int]machine.Facts) ([]string, error) {
	d, on := p.desks[GraderFinder]
	if !on {
		return nil, nil
	}
	if err := p.readAsks(ctx); err != nil {
		return nil, err
	}
	if err := p.postBeats(ctx); err != nil {
		return nil, err
	}
	pending, err := p.pendingWhole()
	if err != nil {
		return nil, err
	}
	var started []string
	for _, ask := range pending {
		name, err := p.startWhole(ctx, d, ask, snapshot)
		if errors.Is(err, ErrNoCapacity) {
			return started, nil
		}
		if err != nil {
			return started, err
		}
		if name != "" {
			started = append(started, name)
		}
	}
	return started, nil
}

func (p *JobPool) pendingWhole() ([]wholeAsk, error) {
	paths, err := filepath.Glob(filepath.Join(p.root, grader.FinderLane, pendingDir, "*"+wholeMark+"*"))
	if err != nil {
		return nil, err
	}
	asks := make([]wholeAsk, 0, len(paths))
	for _, path := range paths {
		data, err := os.ReadFile(path)
		if errors.Is(err, fs.ErrNotExist) {
			continue
		}
		if err != nil {
			return nil, err
		}
		var ask wholeAsk
		if err := json.Unmarshal(data, &ask); err != nil {
			return nil, fmt.Errorf("%s: %w", path, err)
		}
		asks = append(asks, ask)
	}
	return asks, nil
}

func (p *JobPool) finishWhole(ask wholeAsk) error {
	return os.Remove(filepath.Join(p.root, grader.FinderLane, pendingDir, ask.PR+wholeMark+ask.Key))
}

func (p *JobPool) startWhole(ctx context.Context, d desk, ask wholeAsk, snapshot map[int]machine.Facts) (string, error) {
	base := ask.PR + wholeMark + ask.Key
	plan, err := p.nextAttempt(ctx, d, base)
	if err != nil {
		return "", err
	}
	switch {
	case plan.running != "" || plan.held:
		return "", nil
	case plan.done:
		return "", p.finishWhole(ask)
	}
	workers, err := p.Workers()
	if err != nil {
		return "", err
	}
	number, err := strconv.Atoi(ask.PR)
	if err != nil {
		return "", fmt.Errorf("a whole pass names %q: %w", ask.PR, err)
	}
	sprite, err := p.place(d, workers, p.firsts[ask.PR])
	if err != nil {
		return "", err
	}
	facts := snapshot[number]
	facts.Repo, facts.Number, facts.Head = p.repo(), number, ""
	built, err := p.wholeJob(ctx, d, facts, ask, sprite)
	if errors.Is(err, ErrAnswered) {
		return "", p.finishWhole(ask)
	}
	if err != nil {
		return "", err
	}
	return p.start(ctx, GraderFinder, d, sprite, filepath.Join(p.root, d.lane, attemptBase(base, plan.number)), built)
}

func (p *JobPool) wholeJob(ctx context.Context, d desk, facts machine.Facts, ask wholeAsk, sprite string) (job, error) {
	pr := strconv.Itoa(facts.Number)
	pull := facts.Repo + "#" + pr
	found, err := client.Tail(ctx, client.Query{Drive: p.drive, Kinds: []string{kindReview, kindHead}, PR: pull})
	if err != nil {
		return job{}, err
	}
	reviews := finderReviews(found, facts.Repo, pr)
	moved := int64(0)
	for _, r := range found {
		if r.Kind == kindHead {
			moved = max(moved, r.Seq)
		}
	}
	answered := slices.ContainsFunc(gradedReviews(reviews), func(r review) bool {
		return r.Seq > moved && strings.Contains(r.Text, wholeRead)
	})
	if answered {
		return job{}, ErrAnswered
	}
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
	base := p.baseJob(d, facts, sprite, settled)
	base.Lane = ask.Lane
	return job{spec: grader.FinderJob{Job: base, History: jobRecords(named), Ruled: jobRecords(ruled), Full: true}}, nil
}
