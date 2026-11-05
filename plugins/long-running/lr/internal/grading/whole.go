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
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
)

const (
	wholeMark   = "-whole-"
	wholeAsked  = "finder: whole"
	bodyEdited  = "body edit"
	wholeRead   = "over whole "
	pendingDir  = "pending"
	kindAsk     = "ask"
	kindRead    = "github"
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
	Head string `json:"head,omitempty"`
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

func edited(r client.Record, repo string) map[string]string {
	if string(r.Kind) != kindHead || !slices.Contains(r.To, grader.FinderLane) || !strings.Contains(strings.ToLower(r.Text), bodyEdited) {
		return nil
	}
	heads := map[string]string{}
	for pr, entry := range headEntries(r, repo) {
		heads[pr] = entry.commit
	}
	return heads
}

func bodyEdit(r client.Record, repo string, snapshot map[int]machine.Facts) (machine.Facts, bool) {
	if !prrow.ReadsNewBody(r) || len(r.Refs.PRs) != 1 || r.Refs.PRs[0].Repo != "" && !strings.EqualFold(r.Refs.PRs[0].Repo, repo) {
		return machine.Facts{}, false
	}
	facts, open := snapshot[r.Refs.PRs[0].Number]
	head := r.Fields.Attrs["head"]
	return facts, open && head != "" && strings.HasPrefix(facts.Head, head) && facts.Finder == machine.FinderAsk
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

func (p *JobPool) readAsks(ctx context.Context, snapshot map[int]machine.Facts) error {
	cursor, err := p.Cursors.Cursor(cursorAsks)
	if err != nil {
		return err
	}
	found, err := client.Tail(ctx, client.Query{Drive: p.drive, Kinds: []string{kindAsk, kindDecide, kindDefect, kindHead, kindRead}, After: cursor})
	if err != nil {
		return err
	}
	newest := max(cursor, cursorBegun)
	for _, r := range found {
		newest = max(newest, r.Seq)
		if cursor == 0 {
			continue
		}
		key := strconv.FormatInt(r.Seq, 10)
		for _, pr := range requested(r, p.repo()) {
			if err := p.queueWhole(wholeAsk{PR: pr, Lane: r.Lane, Key: key}); err != nil {
				return err
			}
		}
		for pr, head := range edited(r, p.repo()) {
			if err := p.queueWhole(wholeAsk{PR: pr, Lane: r.Lane, Key: key, Head: head}); err != nil {
				return err
			}
		}
		if facts, answers := bodyEdit(r, p.repo(), snapshot); answers {
			if err := p.queueWhole(wholeAsk{PR: strconv.Itoa(facts.Number), Lane: facts.Lane, Key: key, Head: facts.Head}); err != nil {
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
	return p.queueWhole(wholeAsk{PR: spec.PR, Lane: spec.Lane, Key: short(spec.Head)})
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
	if err := p.readAsks(ctx, snapshot); err != nil {
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
	directory := filepath.Join(p.root, d.lane, attemptBase(base, plan.number))
	built, err := p.wholeJob(ctx, d, facts, ask, sprite, directory)
	if errors.Is(err, ErrAnswered) {
		return "", p.finishWhole(ask)
	}
	if err != nil {
		return "", err
	}
	return p.start(ctx, GraderFinder, d, sprite, directory, built)
}

func (p *JobPool) wholeJob(ctx context.Context, d desk, facts machine.Facts, ask wholeAsk, sprite, directory string) (job, error) {
	pr := strconv.Itoa(facts.Number)
	pull := facts.Repo + "#" + pr
	found, err := client.Tail(ctx, client.Query{Drive: p.drive, Kinds: []string{kindReview, kindHead}, PR: pull})
	if err != nil {
		return job{}, err
	}
	reviews := finderReviews(found, facts.Repo, pr)
	if ask.Head != "" {
		return p.recheckJob(ctx, d, facts, ask, sprite, directory, reviews)
	}
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
	live := p.readPR(ctx, facts.Repo, pr)
	items, err := p.bugItems(ctx, live.Body)
	if err != nil {
		return job{}, err
	}
	landed, err := p.landedRows()
	if err != nil {
		return job{}, err
	}
	answers, err := p.answersOwed(ctx, live, recordTexts(ruled))
	if err != nil {
		return job{}, err
	}
	bodies := map[string][]byte{}
	history, unreadHistory := jobRecords(named, directory, bodies)
	rulings, unreadRulings := jobRecords(ruled, directory, bodies)
	base := p.baseJob(d, facts, sprite, settled)
	base.Lane, base.BugItems, base.Unread = ask.Lane, items, slices.Concat(unreadHistory, unreadRulings)
	return job{landed: landed, answers: answers, bodies: bodies, spec: grader.FinderJob{Job: base, History: history, Ruled: rulings, Full: true}}, nil
}

func (p *JobPool) recheckJob(ctx context.Context, d desk, facts machine.Facts, ask wholeAsk, sprite, directory string, reviews []review) (job, error) {
	asked, err := strconv.ParseInt(ask.Key, 10, 64)
	if err != nil {
		return job{}, err
	}
	graded := slices.DeleteFunc(gradedReviews(reviews), func(r review) bool { return !grader.NamesHead(r.head, ask.Head) })
	if len(graded) == 0 {
		return job{}, ErrAnswered
	}
	last := graded[len(graded)-1]
	if last.Seq > asked || last.grade == "ok" {
		return job{}, ErrAnswered
	}
	named := make([]client.Record, 0, len(reviews))
	for _, r := range reviews {
		named = append(named, r.Record)
	}
	settled, err := rootSettled(ctx, p.drive, facts.Repo, strconv.Itoa(facts.Number), named)
	if err != nil {
		return job{}, err
	}
	items, err := p.bugItems(ctx, p.readPR(ctx, facts.Repo, strconv.Itoa(facts.Number)).Body)
	if err != nil {
		return job{}, err
	}
	bodies := map[string][]byte{}
	history, unread := jobRecords(named, directory, bodies)
	base := p.baseJob(d, facts, sprite, settled)
	base.Head, base.Lane, base.BugItems, base.Unread = ask.Head, ask.Lane, items, unread
	return job{bodies: bodies, spec: grader.FinderJob{Job: base, Prior: &grader.Record{Seq: last.Seq, Lane: last.Lane, Head: last.head, Text: last.Text}, History: history}}, nil
}
