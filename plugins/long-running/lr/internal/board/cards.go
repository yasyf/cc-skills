package board

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"maps"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ledger"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	cardPrefix    = "card/"
	cardStateFile = "board_cards.json"
	mergeTriage   = "card-merge-triage"
	answerRow     = "board-answer"
	blockLabel    = "board-block:"
	pathsField    = "paths"
)

type Cards struct {
	Runner   shell.Runner
	Ledger   ledger.Store
	CCX      ccx.Client
	Bus      string
	Checkout string
	Session  string
	StateDir string
	Program  string
	Store    *store.Store
	Now      func() time.Time
	Closed   func(ctx context.Context, seqs []string) (map[string]bool, error)
	Withdraw func(ctx context.Context, lane, card, text string) error
	Report   func(ctx context.Context, lane string, to []string, topic, text string) error
}

func (c Cards) run(ctx context.Context, command shell.Command) (string, error) {
	done, err := c.Runner.Run(ctx, command)
	return done.Stdout, err
}

func (c Cards) ccn(ctx context.Context, args ...string) (string, error) {
	return c.run(ctx, shell.Command{Argv: append([]string{"ccn", "-R", c.Checkout}, args...)})
}

func (c Cards) present(ctx context.Context, args ...string) (string, error) {
	return c.run(ctx, shell.Command{Argv: append(append([]string{"cc-present"}, args...), "--session", c.Session)})
}

func (c Cards) ledgerID(ctx context.Context) (string, error) {
	path := filepath.Join(c.StateDir, cardStateFile)
	var state struct {
		Ledger string `json:"ledger"`
	}
	data, err := os.ReadFile(path)
	switch {
	case err == nil:
		if err := json.Unmarshal(data, &state); err != nil {
			return "", fmt.Errorf("%s: %w", path, err)
		}
	case !errors.Is(err, os.ErrNotExist):
		return "", err
	}
	if state.Ledger != "" {
		return state.Ledger, nil
	}
	created, err := c.ccn(ctx, "ledger", "add", "board cards: "+c.Bus, "--json")
	if err != nil {
		return "", err
	}
	var row struct {
		ID string `json:"id"`
	}
	if err := json.Unmarshal([]byte(created), &row); err != nil {
		return "", fmt.Errorf("ccn ledger add: %w", err)
	}
	state.Ledger = row.ID
	encoded, err := json.Marshal(state)
	if err != nil {
		return "", err
	}
	scratch := path + ".tmp"
	if err := os.WriteFile(scratch, encoded, 0o600); err != nil {
		return "", err
	}
	return state.Ledger, os.Rename(scratch, path)
}

func (c Cards) rows(ctx context.Context) (string, map[string]map[string]string, error) {
	id, err := c.ledgerID(ctx)
	if err != nil {
		return "", nil, err
	}
	rows, err := c.Ledger.Rows(ctx, id)
	if err != nil {
		return "", nil, err
	}
	known := map[string]map[string]string{}
	for _, row := range rows {
		if strings.HasPrefix(row.Key, cardPrefix) {
			known[strings.TrimPrefix(row.Key, cardPrefix)] = row.Fields
		}
	}
	return id, known, nil
}

func (c Cards) ends(ctx context.Context) (map[string]map[string]string, error) {
	_, known, err := c.rows(ctx)
	if err != nil {
		return nil, err
	}
	maps.DeleteFunc(known, func(_ string, fields map[string]string) bool { return fields["withdrawn"] != "" })
	return known, nil
}

func (c Cards) record(ctx context.Context, card string, fields map[string]string) error {
	id, err := c.ledgerID(ctx)
	if err != nil {
		return err
	}
	merged := map[string]string{"moved": stamp(c.Now())}
	for name, value := range fields {
		merged[name] = value
	}
	return c.Ledger.Sync(ctx, id, []ledger.Row{{Key: cardPrefix + card, Fields: merged}})
}

func (c Cards) onBoard(ctx context.Context) ([]string, error) {
	shown, err := c.present(ctx, "outcomes", "--all")
	if err != nil {
		return nil, err
	}
	var outcomes struct {
		Doc struct {
			Blocks []struct {
				ID   string `json:"id"`
				Type string `json:"type"`
			} `json:"blocks"`
		} `json:"doc"`
	}
	if err := json.Unmarshal([]byte(shown), &outcomes); err != nil {
		return nil, fmt.Errorf("cc-present outcomes: %w", err)
	}
	var cards []string
	for _, block := range outcomes.Doc.Blocks {
		if block.Type == "card" && block.ID != mergeTriage {
			cards = append(cards, block.ID)
		}
	}
	return cards, nil
}

func (c Cards) Until(ctx context.Context, card, condition, lane string, paths ...string) (End, error) {
	now := c.Now()
	if why := Refusal(condition, now); why != "" {
		return End{}, fmt.Errorf("refused: %s", why)
	}
	end, err := ParseEnd(condition, now)
	if err != nil {
		return End{}, err
	}
	fields := map[string]string{"until": end.String(), "set": stamp(now)}
	if lane != "" {
		fields[laneField] = lane
	}
	if len(paths) > 0 {
		fields[pathsField] = strings.Join(paths, ",")
	}
	return end, c.record(ctx, card, fields)
}

func (c Cards) Paths(ctx context.Context) (map[string][]string, error) {
	known, err := c.ends(ctx)
	if err != nil {
		return nil, err
	}
	cards, err := c.onBoard(ctx)
	if err != nil {
		return nil, err
	}
	gated := map[string][]string{}
	for _, card := range cards {
		if paths := known[card][pathsField]; paths != "" {
			gated[card] = strings.Split(paths, ",")
		}
	}
	return gated, nil
}

func (c Cards) Missing(ctx context.Context, named []string) ([]string, error) {
	known, err := c.ends(ctx)
	if err != nil {
		return nil, err
	}
	if len(named) == 0 {
		if named, err = c.onBoard(ctx); err != nil {
			return nil, err
		}
	}
	return slices.DeleteFunc(slices.Clone(named), func(card string) bool {
		_, recorded := known[card]
		return recorded
	}), nil
}

func (c Cards) Drawn(ctx context.Context, named []string) (map[string]time.Time, error) {
	id, known, err := c.rows(ctx)
	if err != nil {
		return nil, err
	}
	now := stamp(c.Now())
	drawn := make(map[string]time.Time, len(named))
	var fresh []ledger.Row
	for _, card := range named {
		at := known[card]["drawn"]
		if at == "" || known[card]["withdrawn"] > at {
			at = now
			fresh = append(fresh, ledger.Row{Key: cardPrefix + card, Fields: map[string]string{"drawn": at}})
		}
		parsed, err := time.Parse(stampForm, at)
		if err != nil {
			return nil, fmt.Errorf("card %s drawn %q: %w", card, at, err)
		}
		drawn[card] = parsed
	}
	if len(fresh) == 0 {
		return drawn, nil
	}
	return drawn, c.Ledger.Sync(ctx, id, fresh)
}

func (c Cards) facts(ctx context.Context, waiting map[string]End) (EndFacts, error) {
	byRepo := map[string][]int{}
	var seqs []string
	release := false
	for _, end := range waiting {
		switch end.Kind {
		case KindPR:
			repo, text, _ := strings.Cut(end.Value, "#")
			number, err := strconv.Atoi(text)
			if err != nil {
				return EndFacts{}, err
			}
			byRepo[repo] = append(byRepo[repo], number)
		case KindRecord:
			seqs = append(seqs, end.Value)
		case KindRelease:
			release = true
		}
	}
	facts := EndFacts{PRs: map[string]string{}, Closed: map[string]bool{}, Carries: func(commit, live string) bool {
		_, err := c.Runner.Run(ctx, shell.Command{Argv: []string{"git", "-C", c.Checkout, "merge-base", "--is-ancestor", commit, live}})
		return err == nil
	}}
	for repo, numbers := range byRepo {
		rows, err := c.CCX.PRStatus(ctx, repo, numbers)
		if err != nil {
			return EndFacts{}, err
		}
		for _, row := range rows {
			facts.PRs[fmt.Sprintf("%s#%d", repo, row.Number)] = row.State
		}
	}
	if len(seqs) > 0 {
		closed, err := c.Closed(ctx, seqs)
		if err != nil {
			return EndFacts{}, err
		}
		facts.Closed = closed
	}
	if release {
		live, err := c.run(ctx, shell.Command{Argv: []string{"tools/ci", "release", "live"}, Dir: c.Checkout})
		if err != nil {
			return EndFacts{}, err
		}
		facts.Live = strings.Split(live, "\n")
	}
	return facts, nil
}

func (c Cards) unrowed(ctx context.Context, card string) ([]string, error) {
	shown, err := c.present(ctx, "outcomes", "--block", card)
	if err != nil {
		return nil, err
	}
	var outcomes struct {
		Answers []struct {
			Origin  string `json:"origin"`
			Payload struct {
				BlockID   string          `json:"blockId"`
				Type      string          `json:"type"`
				OptionIDs []string        `json:"optionIds"`
				Other     string          `json:"other"`
				Text      string          `json:"text"`
				Verdict   string          `json:"verdict"`
				Payload   json.RawMessage `json:"payload"`
			} `json:"payload"`
		} `json:"answers"`
	}
	if err := json.Unmarshal([]byte(shown), &outcomes); err != nil {
		return nil, fmt.Errorf("cc-present outcomes --block %s: %w", card, err)
	}
	answered := map[string]bool{}
	var blocks []string
	for _, answer := range outcomes.Answers {
		payload := answer.Payload
		said := len(payload.OptionIDs) > 0 || payload.Other != "" || payload.Text != "" || payload.Verdict != "" || len(payload.Payload) > 0
		if answer.Origin == "human" && said && !answered[payload.BlockID] {
			answered[payload.BlockID] = true
			blocks = append(blocks, payload.BlockID)
		}
	}
	if len(blocks) == 0 {
		return nil, nil
	}
	listed, err := c.ccn(ctx, "answer", "list", "--label", answerRow, "--label", "board-session:"+c.Session, "--label", "board-card:"+card, "--limit", "0", "--full", "--json")
	if err != nil {
		return nil, err
	}
	var rows []struct {
		Tags []string `json:"tags"`
	}
	if strings.TrimSpace(listed) != "" {
		if err := json.Unmarshal([]byte(listed), &rows); err != nil {
			return nil, fmt.Errorf("ccn answer list: %w", err)
		}
	}
	for _, row := range rows {
		for _, tag := range row.Tags {
			if strings.HasPrefix(tag, blockLabel) {
				delete(answered, strings.TrimPrefix(tag, blockLabel))
			}
		}
	}
	return slices.DeleteFunc(blocks, func(block string) bool { return !answered[block] }), nil
}

func (c Cards) Pass(ctx context.Context, lane string, judge Judge, out io.Writer) error {
	return errors.Join(c.Sweep(ctx, lane, out), c.Unask(ctx, lane, judge, out))
}

func (c Cards) Sweep(ctx context.Context, lane string, out io.Writer) error {
	known, err := c.ends(ctx)
	if err != nil {
		return err
	}
	cards, err := c.onBoard(ctx)
	if err != nil {
		return err
	}
	now := c.Now()
	waiting := map[string]End{}
	for _, card := range cards {
		fields, recorded := known[card]
		if !recorded {
			fmt.Fprintf(out, "PROBLEM %s: no end condition; record one with lr board cards until %s --until COND\n", card, card)
			continue
		}
		end, err := ParseEnd(fields["until"], now)
		if err != nil {
			return fmt.Errorf("card %s: %w", card, err)
		}
		waiting[card] = end
	}
	facts, err := c.facts(ctx, waiting)
	if err != nil {
		return err
	}
	for _, card := range cards {
		end, isWaiting := waiting[card]
		if !isWaiting {
			continue
		}
		outcome := facts.Reached(end, now)
		if outcome == "" {
			continue
		}
		if err := c.sweepOff(ctx, lane, card, known[card], outcome, fmt.Sprintf("%s (its end, %s)", outcome, end), out); err != nil {
			return err
		}
	}
	return nil
}

func (c Cards) sweepOff(ctx context.Context, lane, card string, prior map[string]string, outcome, why string, out io.Writer) error {
	shown, err := c.present(ctx, "outcomes", "--block", card)
	if err != nil {
		return err
	}
	var outcomes struct {
		Answers []struct {
			Origin string    `json:"origin"`
			At     time.Time `json:"at"`
		} `json:"answers"`
	}
	if err := json.Unmarshal([]byte(shown), &outcomes); err != nil {
		return fmt.Errorf("cc-present outcomes --block %s: %w", card, err)
	}
	for _, answer := range outcomes.Answers {
		if answer.Origin == "human" && c.Now().Sub(answer.At) < touchedHold {
			fmt.Fprintf(out, "HELD %s: he touched it at %s, and the next sweep takes it\n", card, answer.At.Format(time.Kitchen))
			return nil
		}
	}
	err = c.close(ctx, lane, card, prior, outcome, why, out)
	if err != nil && strings.Contains(err.Error(), boardHoldRefusal) {
		fmt.Fprintf(out, "HELD %s: the board holds a card he touched, and a later sweep takes it\n", card)
		return nil
	}
	return err
}

func (c Cards) Retire(ctx context.Context, lane, reason string, named []string, out io.Writer) error {
	known, err := c.ends(ctx)
	if err != nil {
		return err
	}
	cards, err := c.onBoard(ctx)
	if err != nil {
		return err
	}
	for _, card := range named {
		if !slices.Contains(cards, card) {
			fmt.Fprintf(out, "UNCHANGED %s: not on the owner's board\n", card)
			continue
		}
		if err := c.close(ctx, lane, card, known[card], reason, reason, out); err != nil {
			return err
		}
	}
	return nil
}

func (c Cards) close(ctx context.Context, lane, card string, prior map[string]string, outcome, why string, out io.Writer) error {
	owed, err := c.unrowed(ctx, card)
	if err != nil {
		return err
	}
	if len(owed) > 0 {
		fmt.Fprintf(out, "PROBLEM %s: the owner's answer on %s has no ccn answer row; run board_watch.py backfill --session %s --program %s before it leaves the board\n", card, strings.Join(owed, ", "), c.Session, c.Bus)
		return nil
	}
	if _, err := c.present(ctx, "remove-block", card, "--reason", why); err != nil {
		return err
	}
	if err := c.Withdraw(ctx, lane, card, fmt.Sprintf("Withdrew %s from the owner's board: %s.", card, why)); err != nil {
		return err
	}
	fields := map[string]string{"withdrawn": stamp(c.Now()), "outcome": outcome}
	for name, value := range prior {
		fields[name] = value
	}
	if err := c.record(ctx, card, fields); err != nil {
		return err
	}
	fmt.Fprintf(out, "WITHDRAWN %s: %s\n", card, outcome)
	return nil
}
