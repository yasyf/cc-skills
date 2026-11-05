package grading

import (
	"context"
	"errors"
	"fmt"
	"maps"
	"regexp"
	"slices"
	"strconv"
	"strings"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/grading/grader"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
)

const (
	kindOwner     = "owner"
	cursorAnswers = "grading-owner-answers"
	ownerPick     = "class owner-pick"
	oddDesk       = "fidelity-desk"
	evenDesk      = "fidelity-shard"
)

var cardNamed = regexp.MustCompile(`\bcard[- ]([\w.-]*\w)`)

func (p *JobPool) Reopen(ctx context.Context, snapshot map[int]machine.Facts) ([]string, error) {
	if _, on := p.desks[GraderFidelity]; !on {
		return nil, nil
	}
	cursor, err := p.Cursors.Cursor(cursorAnswers)
	if err != nil {
		return nil, err
	}
	answers, err := client.Tail(ctx, client.Query{Drive: p.drive, Kinds: []string{kindOwner}, After: cursor})
	if err != nil {
		return nil, err
	}
	if cursor == 0 {
		newest := cursorBegun
		for _, r := range answers {
			newest = max(newest, r.Seq)
		}
		return nil, p.Cursors.Advance(cursorAnswers, newest)
	}
	var grades []client.Record
	if slices.ContainsFunc(answers, func(r client.Record) bool { return len(cardsNamed(fullText(r))) > 0 }) {
		if grades, err = client.Tail(ctx, client.Query{Drive: p.drive, Kinds: []string{kindReview}}); err != nil {
			return nil, err
		}
	}
	var started []string
	for _, answer := range answers {
		for _, number := range slices.Sorted(maps.Keys(snapshot)) {
			facts := snapshot[number]
			grade, card, waits := awaitedGrade(grades, facts, cardsNamed(fullText(answer)))
			if !waits {
				continue
			}
			name, err := p.Regrade(ctx, facts, nil)
			if errors.Is(err, ErrNoCapacity) {
				return started, p.Cursors.Advance(cursorAnswers, answer.Seq-1)
			}
			if errors.Is(err, ErrAnswered) {
				continue
			}
			if err != nil {
				return started, err
			}
			started = append(started, name)
			if err := p.tellReopened(ctx, facts, answer, grade, card, name); err != nil {
				return started, err
			}
		}
		if err := p.Cursors.Advance(cursorAnswers, answer.Seq); err != nil {
			return started, err
		}
	}
	return started, nil
}

func cardsNamed(text string) []string {
	matches := cardNamed.FindAllStringSubmatch(text, -1)
	cards := make([]string, 0, len(matches))
	for _, found := range matches {
		cards = append(cards, found[1])
	}
	return unique(cards)
}

func awaitedGrade(records []client.Record, facts machine.Facts, answered []string) (client.Record, string, bool) {
	if facts.Fidelity != machine.FidelityFail || len(answered) == 0 {
		return client.Record{}, "", false
	}
	pr := strconv.Itoa(facts.Number)
	grades := fidelityGrades(records, withSuccessors(bus.FidelityLanes(), records), facts.Repo, pr)
	grades = slices.DeleteFunc(grades, func(r client.Record) bool {
		return !grader.NamesHead(shortSHA.FindString(r.Text), facts.Head)
	})
	if len(grades) == 0 {
		return client.Record{}, "", false
	}
	grade := grades[len(grades)-1]
	if !strings.HasPrefix(grade.Text, fidelityMissing) || !strings.Contains(grade.Text, ownerPick) {
		return client.Record{}, "", false
	}
	for _, card := range cardsNamed(grade.Text) {
		if slices.Contains(answered, card) {
			return grade, card, true
		}
	}
	return client.Record{}, "", false
}

func (p *JobPool) tellReopened(ctx context.Context, facts machine.Facts, answer, grade client.Record, card, pass string) error {
	pr := strconv.Itoa(facts.Number)
	desk := oddDesk
	if facts.Number%2 == 0 {
		desk = evenDesk
	}
	id := fmt.Sprintf("answer-regrades:%s#%s:%s:%d", facts.Repo, pr, facts.Head, answer.Seq)
	text := fmt.Sprintf("ANSWER-REGRADES #%s %s: his answer #%d to card %s re-opens the owner-pick grade #%d, which waited on that card. Pass %s re-grades this head with his answer; read the head when it hands it to you.", pr, short(facts.Head), answer.Seq, card, grade.Seq, pass)
	return p.notice(ctx, client.Draft{Lane: runnerLane, Kind: kindReport, Text: text, To: []string{desk}, Topic: "answer-regrades:" + pr, ID: id, PRs: []string{facts.Repo + "#" + pr}})
}
