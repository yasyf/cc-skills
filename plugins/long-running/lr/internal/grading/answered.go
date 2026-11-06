package grading

import (
	"context"
	"encoding/json"
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
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/cardtext"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/grading/grader"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	ownerPick     = "class owner-pick"
	oddDesk       = "fidelity-desk"
	evenDesk      = "fidelity-shard"
	kindWithdraw  = "withdraw"
	cardIDPrefix  = "card-"
	unansweredFor = time.Hour
)

var cardNamed = regexp.MustCompile(`\bcard[- ]([\w.-]*\w)`)

type cardAnswer struct {
	ID      string    `json:"id"`
	Created time.Time `json:"created_at"`
	card    string
}

func (p *JobPool) Reopen(ctx context.Context, snapshot map[int]machine.Facts) ([]string, map[int]string, error) {
	if _, on := p.desks[GraderFidelity]; !on || !slices.ContainsFunc(slices.Collect(maps.Values(snapshot)), func(f machine.Facts) bool { return f.Fidelity == machine.FidelityFail }) {
		return nil, nil, nil
	}
	found, err := client.Tail(ctx, client.Query{Drive: p.drive, Kinds: []string{kindReview, kindWithdraw}})
	if err != nil {
		return nil, nil, err
	}
	read := map[string][]cardAnswer{}
	var started []string
	waits := map[int]string{}
	for _, pick := range ownerPickWaits(found, snapshot) {
		answers, err := p.answersAfter(ctx, pick, read)
		if err != nil {
			return started, waits, err
		}
		if len(answers) == 0 {
			if line := waitLine(found, pick.grade, pick.cards, p.Now()); line != "" {
				waits[pick.facts.Number] = line
			}
			continue
		}
		name, err := p.reopen(ctx, pick, answers)
		if errors.Is(err, ErrNoCapacity) || errors.Is(err, ErrAnswered) {
			continue
		}
		if err != nil {
			return started, waits, err
		}
		started = append(started, name)
	}
	return started, waits, nil
}

func (p *JobPool) answersAfter(ctx context.Context, pick ownerPickWait, read map[string][]cardAnswer) ([]cardAnswer, error) {
	var after []cardAnswer
	for _, card := range pick.cards {
		answers, seen := read[card]
		if !seen {
			var err error
			if answers, err = p.answers(ctx, card); err != nil {
				return nil, err
			}
			read[card] = answers
		}
		after = append(after, slices.DeleteFunc(slices.Clone(answers), func(answer cardAnswer) bool { return !answer.Created.After(pick.grade.At) })...)
	}
	slices.SortStableFunc(after, func(one, other cardAnswer) int { return one.Created.Compare(other.Created) })
	return after, nil
}

func (p *JobPool) answers(ctx context.Context, card string) ([]cardAnswer, error) {
	var answers []cardAnswer
	for _, label := range cardtext.AnswerLabels(cardIDPrefix + card) {
		listed, err := p.Runner.Run(ctx, shell.Command{Argv: []string{"ccn", "-R", p.briefs, "answer", "list", "--label", label, "--json", "--full", "--limit", "0"}, Timeout: readTimeout})
		if err != nil {
			return nil, err
		}
		var labelled []cardAnswer
		if err := json.Unmarshal([]byte(listed.Stdout), &labelled); err != nil {
			return nil, fmt.Errorf("ccn answer list --label %s: %w", label, err)
		}
		for _, answer := range labelled {
			answer.card = card
			answers = append(answers, answer)
		}
	}
	return answers, nil
}

func (p *JobPool) reopen(ctx context.Context, pick ownerPickWait, answers []cardAnswer) (string, error) {
	pr := pick.facts.Repo + "#" + strconv.Itoa(pick.facts.Number)
	told, err := client.Tail(ctx, client.Query{Drive: p.drive, Kinds: []string{kindReport}, PR: pr})
	if err != nil {
		return "", err
	}
	id := func(answer cardAnswer) string {
		return fmt.Sprintf("answer-regrades:%s:%s:%s", pr, pick.facts.Head, answer.ID)
	}
	at := slices.IndexFunc(answers, func(answer cardAnswer) bool {
		return !slices.ContainsFunc(told, func(r client.Record) bool { return r.ID == id(answer) })
	})
	if at < 0 {
		return "", ErrAnswered
	}
	name, err := p.Regrade(ctx, pick.facts, nil)
	if err != nil {
		return "", err
	}
	return name, p.tellReopened(ctx, pick, answers[at], id(answers[at]), name)
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

func waitLine(records []client.Record, grade client.Record, cards []string, now time.Time) string {
	for _, card := range cards {
		if slices.ContainsFunc(records, func(r client.Record) bool {
			return r.Seq > grade.Seq && string(r.Kind) == kindWithdraw && strings.TrimPrefix(r.Topic, cardIDPrefix) == card
		}) {
			return fmt.Sprintf("fidelity waits on his answer to card %s, and that card was taken off his board before he answered, so nothing will answer it: root redraws the card or settles the grade", card)
		}
	}
	if waited := now.Sub(grade.At); waited >= unansweredFor {
		return fmt.Sprintf("fidelity waits on his answer to card %s, unanswered for %d hours", strings.Join(cards, " or card "), int(waited.Hours()))
	}
	return ""
}

func (p *JobPool) tellReopened(ctx context.Context, pick ownerPickWait, answer cardAnswer, id, pass string) error {
	pr := strconv.Itoa(pick.facts.Number)
	desk := oddDesk
	if pick.facts.Number%2 == 0 {
		desk = evenDesk
	}
	text := fmt.Sprintf("ANSWER-REGRADES #%s %s: his answer %s (cc-notes) to card %s re-opens the owner-pick grade %s, which waited on that card. Pass %s re-grades this head with his answer; read the head when it hands it to you.", pr, short(pick.facts.Head), answer.ID[:min(len(answer.ID), 7)], answer.card, pick.grade.Name(), pass)
	return p.notice(ctx, client.Draft{Lane: runnerLane, Kind: kindReport, Text: text, To: []string{desk}, Topic: "answer-regrades:" + pr, ID: id, PRs: []string{pick.facts.Repo + "#" + pr}})
}
