package grading

import (
	"context"
	"encoding/json"
	"fmt"
	"regexp"
	"slices"
	"strconv"
	"strings"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/board"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/grading/grader"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	ownerTag   = "from:owner"
	sessionTag = "board-session:"
	blockTag   = "board-block:"
	picked     = "picked “"
)

var (
	namedCard  = regexp.MustCompile(`\bcard-[a-z0-9]+(?:-[a-z0-9]+)*`)
	askOption  = regexp.MustCompile(`^(\d+)[.)]\s+(.+)$`)
	pickedWord = regexp.MustCompile(`^` + picked + `(.+)”$`)
)

type boardAnswer struct {
	Body string   `json:"body"`
	Tags []string `json:"tags"`
}

func (a boardAnswer) tag(prefix string) string {
	for _, tag := range a.Tags {
		if value, found := strings.CutPrefix(tag, prefix); found {
			return value
		}
	}
	return ""
}

type cardOption struct {
	Label string `json:"label"`
	MD    string `json:"md"`
	Hint  string `json:"hint"`
}

type tapped struct {
	grader.Tap
	position int
	options  int
}

func (p *JobPool) taps(ctx context.Context, live livePR) ([]grader.Tap, error) {
	var found []tapped
	var cards []string
	for _, card := range namedCard.FindAllString(live.Text(), -1) {
		if slices.Contains(cards, card) {
			continue
		}
		cards = append(cards, card)
		answers, err := p.boardAnswers(ctx, card)
		if err != nil {
			return nil, err
		}
		for _, answer := range answers {
			label := pickedWord.FindStringSubmatch(strings.SplitN(answer.Body, "\n", 2)[0])
			if label == nil || !slices.Contains(answer.Tags, ownerTag) {
				continue
			}
			tap := tapped{Tap: grader.Tap{Card: card, Label: label[1], Text: answer.Body}}
			p.withOption(ctx, &tap, answer.tag(sessionTag), answer.tag(blockTag))
			found = append(found, tap)
		}
	}
	if len(found) == 0 {
		return nil, nil
	}
	if err := p.withAsks(ctx, found); err != nil {
		return nil, err
	}
	taps := make([]grader.Tap, 0, len(found))
	for _, tap := range found {
		taps = append(taps, tap.Tap)
	}
	return taps, nil
}

func (p *JobPool) boardAnswers(ctx context.Context, card string) ([]boardAnswer, error) {
	listed, err := p.Runner.Run(ctx, shell.Command{Argv: []string{"ccn", "-R", p.briefs, "answer", "list", "--label", "board-card:" + card, "--json", "--full"}, Timeout: readTimeout})
	if err != nil {
		return nil, err
	}
	var answers []boardAnswer
	if err := json.Unmarshal([]byte(listed.Stdout), &answers); err != nil {
		return nil, fmt.Errorf("answers on %s: %w", card, err)
	}
	return answers, nil
}

func (p *JobPool) withOption(ctx context.Context, tap *tapped, session, block string) {
	shown, err := p.Runner.Run(ctx, shell.Command{Argv: []string{"cc-present", "outcomes", "--block", tap.Card, "--session", session}, Timeout: cardTimeout})
	if err != nil {
		p.Log.Warn("a tapped card did not read, so its grade has his answer without the option's own words", "card", tap.Card, "error", err)
		return
	}
	var outcomes struct {
		Doc struct {
			Blocks []struct {
				ID       string `json:"id"`
				Children []struct {
					ID      string       `json:"id"`
					Options []cardOption `json:"options"`
				} `json:"children"`
			} `json:"blocks"`
		} `json:"doc"`
	}
	if err := json.Unmarshal([]byte(shown.Stdout), &outcomes); err != nil {
		p.Log.Warn("a tapped card did not parse, so its grade has his answer without the option's own words", "card", tap.Card, "error", err)
		return
	}
	for _, top := range outcomes.Doc.Blocks {
		for _, child := range top.Children {
			if top.ID != tap.Card || child.ID != block {
				continue
			}
			at := slices.IndexFunc(child.Options, func(option cardOption) bool { return option.Label == tap.Label })
			if at < 0 {
				continue
			}
			option := child.Options[at]
			tap.Text += fmt.Sprintf("\nThe option he tapped, “%s”, said: %s", option.Label, strings.Join(slices.DeleteFunc([]string{option.MD, option.Hint}, func(said string) bool { return said == "" }), " "))
			tap.position, tap.options = at+1, len(child.Options)
		}
	}
}

func (p *JobPool) withAsks(ctx context.Context, found []tapped) error {
	state, err := grader.DriveState(p.Config, p.drive)
	if err != nil || state == "" {
		return err
	}
	ledger, err := board.CardsLedger(state)
	if err != nil || ledger == "" {
		return err
	}
	rows, err := p.Ledger(ledger)
	if err != nil {
		return err
	}
	var seqs []int64
	for _, tap := range found {
		if seq, err := strconv.ParseInt(board.CardAsk(rows, tap.Card), 10, 64); err == nil && tap.position > 0 {
			seqs = append(seqs, seq)
		}
	}
	if len(seqs) == 0 {
		return nil
	}
	asks, err := client.Among(ctx, p.drive, seqs)
	if err != nil {
		return err
	}
	for i, tap := range found {
		at := slices.IndexFunc(asks, func(ask client.Record) bool { return strconv.FormatInt(ask.Seq, 10) == board.CardAsk(rows, tap.Card) })
		if at < 0 || tap.position == 0 {
			continue
		}
		options := askOptions(bus.FullText(asks[at]))
		if len(options) == tap.options {
			found[i].Text += fmt.Sprintf("\nThe card carried option %d of %s's ask, cci %s: %s", tap.position, asks[at].Lane, asks[at].Name(), options[tap.position-1])
		}
	}
	return nil
}

func askOptions(text string) []string {
	var options []string
	open := false
	for line := range strings.Lines(text) {
		said := strings.TrimSpace(line)
		numbered := askOption.FindStringSubmatch(said)
		switch {
		case numbered != nil && numbered[1] == "1":
			options, open = append(options[:0], numbered[2]), true
		case numbered != nil && numbered[1] == strconv.Itoa(len(options)+1):
			options, open = append(options, numbered[2]), true
		case said == "":
			open = false
		case open:
			options[len(options)-1] += " " + said
		}
	}
	return options
}
