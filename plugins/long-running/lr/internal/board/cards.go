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

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ledger"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	cardPrefix     = "card/"
	cardStateFile  = "board_cards.json"
	mergeTriage    = "card-merge-triage"
	answeredReason = "you answered this"
	answerRow      = "board-answer"
	blockLabel     = "board-block:"
	pathsField     = "paths"
	byField        = "by"
	releaseField   = "release"
	questionField  = "question"
	metField       = "met"
	deliveredAttr  = "delivered"
	promiseToken   = "card:"
	mirrorFresh    = 10 * time.Minute
)

var proofKinds = []string{"landed", "done", "report"}

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
	Reports  func(ctx context.Context) ([]ownerAnswer, error)
	Withdraw func(ctx context.Context, lane, card, text string) error
	Report   func(ctx context.Context, lane string, to []string, topic, text string) error
	Tail     func(ctx context.Context, q client.Query) ([]client.Record, error)
	Among    func(ctx context.Context, drive string, seqs []int64) ([]client.Record, error)
	Post     func(ctx context.Context, d client.Draft) (client.Record, error)
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

func (c Cards) knownLedger() (string, error) {
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
	return state.Ledger, nil
}

func (c Cards) ledgerID(ctx context.Context) (string, error) {
	if known, err := c.knownLedger(); err != nil || known != "" {
		return known, err
	}
	path := filepath.Join(c.StateDir, cardStateFile)
	var state struct {
		Ledger string `json:"ledger"`
	}
	title := "board cards: " + c.Bus
	listed, err := c.ccn(ctx, "ledger", "list", "--json")
	if err != nil {
		return "", err
	}
	var ledgers []struct {
		ID    string `json:"id"`
		Title string `json:"title"`
	}
	if err := json.Unmarshal([]byte(listed), &ledgers); err != nil {
		return "", fmt.Errorf("ccn ledger list: %w", err)
	}
	for _, held := range ledgers {
		if held.Title == title {
			state.Ledger = held.ID
			break
		}
	}
	if state.Ledger == "" {
		created, err := c.ccn(ctx, "ledger", "add", title, "--json")
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
	}
	encoded, err := json.Marshal(state)
	if err != nil {
		return "", err
	}
	if err := os.MkdirAll(c.StateDir, 0o700); err != nil {
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
	maps.DeleteFunc(known, func(_ string, fields map[string]string) bool {
		return fields["withdrawn"] != "" || fields["until"] == ""
	})
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

func (c Cards) Releases() (map[string]string, error) {
	id, err := c.knownLedger()
	if err != nil || id == "" {
		return nil, err
	}
	rows, err := c.Store.LedgerRows(id)
	if err != nil {
		return nil, err
	}
	releases := map[string]string{}
	for key, fields := range rows {
		if card, isCard := strings.CutPrefix(key, cardPrefix); isCard && fields[releaseField] != "" {
			releases[card] = fields[releaseField]
		}
	}
	return releases, nil
}

func (c Cards) mirrored(ctx context.Context, card string, fields map[string]string) error {
	if err := c.record(ctx, card, fields); err != nil {
		return err
	}
	id, err := c.ledgerID(ctx)
	if err != nil {
		return err
	}
	merged, _, err := c.Store.LedgerRow(id, cardPrefix+card)
	if err != nil {
		return err
	}
	if merged == nil {
		merged = map[string]string{}
	}
	maps.Copy(merged, fields)
	return c.Store.PutLedgerRow(id, cardPrefix+card, merged, c.Now())
}

func (c Cards) Promises(ctx context.Context) ([]lanes.Promise, error) {
	id, err := c.ledgerID(ctx)
	if err != nil {
		return nil, err
	}
	synced, err := c.Store.LedgerSynced(id)
	if err != nil {
		return nil, err
	}
	if c.Now().Sub(synced) >= mirrorFresh {
		rows, err := c.Ledger.Rows(ctx, id)
		if err != nil {
			return nil, err
		}
		byKey := make(map[string]map[string]string, len(rows))
		for _, row := range rows {
			byKey[row.Key] = row.Fields
		}
		if err := c.Store.PutLedgerRows(id, byKey, c.Now()); err != nil {
			return nil, err
		}
	}
	rows, err := c.Store.LedgerRows(id)
	if err != nil {
		return nil, err
	}
	open := map[string]lanes.Promise{}
	for key, fields := range rows {
		card, isCard := strings.CutPrefix(key, cardPrefix)
		if !isCard || fields[byField] == "" || fields[metField] != "" {
			continue
		}
		by, err := time.Parse(stampForm, fields[byField])
		if err != nil {
			return nil, fmt.Errorf("card %s by %q: %w", card, fields[byField], err)
		}
		open[card] = lanes.Promise{Card: card, Lane: fields[laneField], Question: fields[questionField], By: by}
	}
	if len(open) == 0 {
		return nil, nil
	}
	proofs, err := client.Tail(ctx, client.Query{Drive: c.Bus, Kinds: proofKinds})
	if err != nil {
		return nil, err
	}
	for _, proof := range proofs {
		for _, token := range strings.FieldsFunc(proof.Fields.Attrs[deliveredAttr], func(r rune) bool { return r == ',' || r == ' ' }) {
			card, named := strings.CutPrefix(token, promiseToken)
			if _, owed := open[card]; !named || !owed {
				continue
			}
			if err := c.mirrored(ctx, card, map[string]string{metField: strconv.FormatInt(proof.Seq, 10)}); err != nil {
				return nil, err
			}
			delete(open, card)
		}
	}
	return slices.SortedFunc(maps.Values(open), func(a, b lanes.Promise) int { return a.By.Compare(b.By) }), nil
}

func (c Cards) onBoard(ctx context.Context) ([]string, map[string]bool, map[string][]End, error) {
	shown, err := c.present(ctx, "outcomes", "--all")
	if err != nil {
		return nil, nil, nil, err
	}
	var outcomes struct {
		Doc struct {
			Blocks []struct {
				ID       string `json:"id"`
				Type     string `json:"type"`
				Children []struct {
					Type    string       `json:"type"`
					Repo    string       `json:"repo"`
					Number  int          `json:"number"`
					Options []cardOption `json:"options"`
				} `json:"children"`
			} `json:"blocks"`
		} `json:"doc"`
	}
	if err := json.Unmarshal([]byte(shown), &outcomes); err != nil {
		return nil, nil, nil, fmt.Errorf("cc-present outcomes: %w", err)
	}
	var cards []string
	questions := map[string]bool{}
	lands := map[string][]End{}
	for _, block := range outcomes.Doc.Blocks {
		if block.Type != "card" || block.ID == mergeTriage {
			continue
		}
		cards = append(cards, block.ID)
		var shown []End
		offersLand := false
		for _, child := range block.Children {
			questions[block.ID] = questions[block.ID] || child.Type == "choice"
			offersLand = offersLand || child.Type == "choice" && slices.ContainsFunc(child.Options, func(option cardOption) bool { return bus.ReadsAsLand(option.Label) })
			if child.Type == "pr" {
				shown = append(shown, End{KindPR, fmt.Sprintf("%s#%d", child.Repo, child.Number)})
			}
		}
		if offersLand {
			lands[block.ID] = shown
		}
	}
	return cards, questions, lands, nil
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
	gated := map[string][]string{}
	for card, fields := range known {
		if paths := fields[pathsField]; paths != "" {
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
		if named, _, _, err = c.onBoard(ctx); err != nil {
			return nil, err
		}
	}
	return slices.DeleteFunc(slices.Clone(named), func(card string) bool {
		_, recorded := known[card]
		return recorded
	}), nil
}

func (c Cards) Drawn(ctx context.Context, named []string) (map[string]time.Time, error) {
	_, known, err := c.rows(ctx)
	if err != nil {
		return nil, err
	}
	drawn := make(map[string]time.Time, len(named))
	for _, card := range named {
		if undrawn(known[card]) {
			continue
		}
		at := known[card]["drawn"]
		parsed, err := time.Parse(stampForm, at)
		if err != nil {
			return nil, fmt.Errorf("card %s drawn %q: %w", card, at, err)
		}
		drawn[card] = parsed
	}
	return drawn, nil
}

func (c Cards) MarkDrawn(ctx context.Context, named []string) error {
	id, known, err := c.rows(ctx)
	if err != nil {
		return err
	}
	now := stamp(c.Now())
	var fresh []ledger.Row
	for _, card := range named {
		if undrawn(known[card]) {
			fresh = append(fresh, ledger.Row{Key: cardPrefix + card, Fields: map[string]string{"drawn": now}})
		}
	}
	if len(fresh) == 0 {
		return nil
	}
	return c.Ledger.Sync(ctx, id, fresh)
}

func undrawn(fields map[string]string) bool {
	return fields["drawn"] == "" || fields["withdrawn"] > fields["drawn"]
}

func (c Cards) facts(ctx context.Context, waiting map[string]End, out io.Writer) (EndFacts, error) {
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
			fmt.Fprintf(out, "WARNING each card waiting on a %s PR stays up, since its PRs did not read: %v\n", repo, err)
			continue
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

func (c Cards) answeredBlocks(ctx context.Context, card string) ([]string, error) {
	shown, err := c.present(ctx, "outcomes", "--block", card)
	if err != nil && strings.Contains(err.Error(), neverOnBoard) {
		return nil, nil
	}
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
	var blocks []string
	for _, answer := range outcomes.Answers {
		payload := answer.Payload
		said := len(payload.OptionIDs) > 0 || payload.Other != "" || payload.Text != "" || payload.Verdict != "" || len(payload.Payload) > 0
		if answer.Origin == "human" && said && !slices.Contains(blocks, payload.BlockID) {
			blocks = append(blocks, payload.BlockID)
		}
	}
	return blocks, nil
}

func (c Cards) unrowed(ctx context.Context, card string) ([]string, error) {
	blocks, err := c.answeredBlocks(ctx, card)
	if err != nil || len(blocks) == 0 {
		return nil, err
	}
	answered := make(map[string]bool, len(blocks))
	for _, block := range blocks {
		answered[block] = true
	}
	listed, err := c.ccn(ctx, "answer", "list", "--label", answerRow, "--label", "board-session:"+c.Session, "--label", boardCardLabel+card, "--limit", "0", "--full", "--json")
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
	return errors.Join(c.Sweep(ctx, lane, out), c.closeAnswered(ctx, lane, out), c.Unask(ctx, lane, judge, out), c.Finished(ctx, lane, judge, out), c.Corrected(ctx, lane))
}

func (c Cards) closeAnswered(ctx context.Context, lane string, out io.Writer) error {
	outcomes, err := ReadOutcomes(ctx, c.Runner, c.Session)
	if err != nil {
		return err
	}
	var shown []string
	tapped := map[string]bool{}
	for _, card := range typed(outcomes.Doc, "card") {
		if id := text(card, "id"); id != mergeTriage {
			shown = append(shown, id)
			tapped[id] = !asksOpen(card, outcomes)
		}
	}
	if len(shown) == 0 {
		return nil
	}
	rows, err := c.answers(ctx)
	if err != nil {
		return err
	}
	known, err := c.ends(ctx)
	if err != nil {
		return err
	}
	for _, card := range shown {
		if !slices.ContainsFunc(rows, func(row ownerAnswer) bool { return closesCard(row, card) && (tapped[card] || inThread(row)) }) {
			continue
		}
		if err := c.sweepOff(ctx, lane, card, known[card], answeredReason, answeredReason, out); err != nil {
			return err
		}
	}
	return nil
}

func (c Cards) Sweep(ctx context.Context, lane string, out io.Writer) error {
	known, err := c.ends(ctx)
	if err != nil {
		return err
	}
	cards, questions, lands, err := c.onBoard(ctx)
	if err != nil {
		return err
	}
	now := c.Now()
	waiting := map[string]End{}
	for _, card := range cards {
		for n, pr := range lands[card] {
			waiting[fmt.Sprintf("%s lands %d", card, n)] = pr
		}
		fields, recorded := known[card]
		if !recorded {
			fmt.Fprintf(out, "PROBLEM %s: no end condition; record one with lr board cards until %s --until COND\n", card, card)
			continue
		}
		end, err := ParseEnd(fields["until"], now)
		if err != nil {
			return fmt.Errorf("card %s: %w", card, err)
		}
		if end.Kind == KindAt && questions[card] {
			continue
		}
		waiting[card] = end
	}
	facts, err := c.facts(ctx, waiting, out)
	if err != nil {
		return err
	}
	for _, card := range cards {
		if ended := facts.allReached(lands[card], now); ended != "" {
			if err := c.sweepOff(ctx, lane, card, known[card], ended, ended, out); err != nil {
				return err
			}
			continue
		}
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
	return c.retire(ctx, lane, reason, named, out)
}

func (c Cards) Replace(ctx context.Context, lane, reason, replacedBy string, named []string, out io.Writer) error {
	return c.retire(ctx, lane, reason, named, out, "--replaced-by", replacedBy)
}

func (c Cards) retire(ctx context.Context, lane, reason string, named []string, out io.Writer, removeFlags ...string) error {
	known, err := c.ends(ctx)
	if err != nil {
		return err
	}
	cards, _, _, err := c.onBoard(ctx)
	if err != nil {
		return err
	}
	for _, card := range named {
		if !slices.Contains(cards, card) {
			fmt.Fprintf(out, "UNCHANGED %s: not on the owner's board\n", card)
			continue
		}
		if err := c.close(ctx, lane, card, known[card], reason, reason, out, removeFlags...); err != nil {
			return err
		}
		if err := c.freeTapRow(card); err != nil {
			return err
		}
	}
	return nil
}

func (c Cards) close(ctx context.Context, lane, card string, prior map[string]string, outcome, why string, out io.Writer, removeFlags ...string) error {
	owed, err := c.unrowed(ctx, card)
	if err != nil {
		return err
	}
	if len(owed) > 0 {
		fmt.Fprintf(out, "PROBLEM %s: the owner's answer on %s has no ccn answer row; run board_watch.py backfill --session %s --program %s before it leaves the board\n", card, strings.Join(owed, ", "), c.Session, c.Bus)
		return nil
	}
	if _, err := c.present(ctx, append([]string{"remove-block", card, "--reason", why, "--lane", lane, "--take-over"}, removeFlags...)...); err != nil {
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
