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
	"time"

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
	kindWithdraw  = "withdraw"
	unansweredFor = time.Hour
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
	var picks []ownerPickWait
	if slices.ContainsFunc(answers, func(r client.Record) bool { return len(cardsNamed(bus.FullText(r))) > 0 }) {
		grades, err := client.Tail(ctx, client.Query{Drive: p.drive, Kinds: []string{kindReview}})
		if err != nil {
			return nil, err
		}
		picks = ownerPickWaits(grades, snapshot)
	}
	var started []string
	for _, answer := range answers {
		answered := cardsNamed(bus.FullText(answer))
		for _, pick := range picks {
			at := slices.IndexFunc(pick.cards, func(card string) bool { return slices.Contains(answered, card) })
			if at < 0 {
				continue
			}
			name, err := p.Regrade(ctx, pick.facts, nil)
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
			if err := p.tellReopened(ctx, pick.facts, answer, pick.grade, pick.cards[at], name); err != nil {
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

type ownerPickWait struct {
	facts machine.Facts
	grade client.Record
	cards []string
}

func ownerPickWaits(records []client.Record, snapshot map[int]machine.Facts) []ownerPickWait {
	lanes := withSuccessors(bus.FidelityLanes(), records)
	var picks []ownerPickWait
	for _, number := range slices.Sorted(maps.Keys(snapshot)) {
		facts := snapshot[number]
		if grade, cards, parked := ownerPickGrade(records, lanes, facts); parked {
			picks = append(picks, ownerPickWait{facts, grade, cards})
		}
	}
	return picks
}

func ownerPickGrade(records []client.Record, lanes []string, facts machine.Facts) (client.Record, []string, bool) {
	if facts.Fidelity != machine.FidelityFail {
		return client.Record{}, nil, false
	}
	pr := strconv.Itoa(facts.Number)
	grades := fidelityGrades(records, lanes, facts.Repo, pr)
	grades = slices.DeleteFunc(grades, func(r client.Record) bool {
		return !grader.NamesHead(shortSHA.FindString(r.Text), facts.Head)
	})
	if len(grades) == 0 {
		return client.Record{}, nil, false
	}
	grade := grades[len(grades)-1]
	cards := cardsNamed(grade.Text)
	if !strings.HasPrefix(grade.Text, fidelityMissing) || !strings.Contains(grade.Text, ownerPick) || len(cards) == 0 {
		return client.Record{}, nil, false
	}
	return grade, cards, true
}

func (p *JobPool) Parked(ctx context.Context, snapshot map[int]machine.Facts) (map[int]string, error) {
	if _, on := p.desks[GraderFidelity]; !on || !slices.ContainsFunc(slices.Collect(maps.Values(snapshot)), func(f machine.Facts) bool { return f.Fidelity == machine.FidelityFail }) {
		return nil, nil
	}
	found, err := client.Tail(ctx, client.Query{Drive: p.drive, Kinds: []string{kindReview, kindOwner, kindWithdraw}})
	if err != nil {
		return nil, err
	}
	waits := map[int]string{}
	for _, pick := range ownerPickWaits(found, snapshot) {
		if line := waitLine(found, pick.grade, pick.cards, p.Now()); line != "" {
			waits[pick.facts.Number] = line
		}
	}
	return waits, nil
}

func waitLine(records []client.Record, grade client.Record, cards []string, now time.Time) string {
	after := slices.DeleteFunc(slices.Clone(records), func(r client.Record) bool { return r.Seq <= grade.Seq })
	for _, card := range cards {
		if slices.ContainsFunc(after, func(r client.Record) bool {
			return string(r.Kind) == kindOwner && slices.Contains(cardsNamed(bus.FullText(r)), card)
		}) {
			return ""
		}
	}
	for _, card := range cards {
		if slices.ContainsFunc(after, func(r client.Record) bool {
			return string(r.Kind) == kindWithdraw && strings.TrimPrefix(r.Topic, "card-") == card
		}) {
			return fmt.Sprintf("fidelity waits on his answer to card %s, and that card was taken off his board before he answered, so nothing will answer it: root redraws the card or settles the grade", card)
		}
	}
	if waited := now.Sub(grade.At); waited >= unansweredFor {
		return fmt.Sprintf("fidelity waits on his answer to card %s, unanswered for %d hours", strings.Join(cards, " or card "), int(waited.Hours()))
	}
	return ""
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
