package reply

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"maps"
	"os"
	"regexp"
	"slices"
	"strconv"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/board"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const laneEnv = "CLAUDE_LONG_RUNNING_LANE"

var (
	titledPR  = regexp.MustCompile(`#(\d+)\b`)
	ownedCard = regexp.MustCompile(`lane (\S+) owns it`)
)

func refused(warning string, leads ...string) bool {
	return slices.ContainsFunc(leads, func(lead string) bool { return strings.Contains(warning, lead) })
}

func refusal(warning string) string {
	return "REFUSED: " + warning + "; nothing went out, so fix it and send again"
}

func (r Reply) unpickable() error {
	var labels []string
	explained := map[string]bool{}
	for _, pick := range r.Picks {
		whole, said, _ := strings.Cut(pick, pickSplit)
		whole = strings.TrimSpace(whole)
		label := shortened(whole, maxPick)
		if label == "" || slices.Contains(labels, label) {
			continue
		}
		labels = append(labels, label)
		explained[label] = label != whole || strings.TrimSpace(said) != "" || r.Words[label] != ""
	}
	switch {
	case len(labels) == 1:
		return fmt.Errorf("ONE PICK: %q alone is no choice, so nothing posted; give him two to four picks, or send it with no picks", labels[0])
	case len(labels) > maxPicks:
		return fmt.Errorf("TOO MANY PICKS: a card offers at most %d picks and this has %d, so nothing posted", maxPicks, len(labels))
	}
	for _, label := range labels {
		if !explained[label] {
			return fmt.Errorf("UNSAID PICK: pick %q does not say what picking it does, so nothing posted; write it as 'Keep it%swhat picking it does'", label, pickSplit)
		}
	}
	recommended, _, _ := strings.Cut(r.Recommended, pickSplit)
	if len(labels) > 0 && !slices.Contains(labels, shortened(strings.TrimSpace(recommended), maxPick)) {
		return errors.New("UNRECOMMENDED: no pick is recommended, so nothing posted; name one of the picks in recommended")
	}
	return nil
}

func (r Reply) alertless() error {
	if r.Alert == "" && (r.Status == incident || r.Type == incident) {
		return errors.New("ALERTLESS: this incident line names no alert, so nothing posted; pass alert, the incident id its lanes are named for, such as sentry-7830378865 for sentry-7830378865-fix, on every line of that incident")
	}
	return nil
}

func (f Fanout) onePostPerPR(ctx context.Context, r Reply) (Reply, error) {
	named := map[int]string{}
	for _, pr := range r.PRs {
		if found := pullURL.FindStringSubmatch(pr); found != nil {
			number, _ := strconv.Atoi(found[2])
			named[number] = pr
		}
	}
	if len(r.Picks) > 0 {
		for _, found := range titledPR.FindAllStringSubmatch(r.Title, -1) {
			number, _ := strconv.Atoi(found[1])
			if _, listed := named[number]; !listed {
				named[number] = "#" + found[1]
			}
		}
	}
	if len(named) == 0 || len(r.Picks) == 0 && f.postedRef(r.ID) != "" {
		return r, nil
	}
	outcomes, err := board.ReadOutcomes(ctx, f.Runner, f.Entry.Root())
	if err != nil {
		return r, nil
	}
	own := cardPrefix + r.ID
	var seconds []string
	for _, card := range outcomes.OpenCards() {
		if card.ID == own {
			continue
		}
		for _, number := range slices.Sorted(maps.Keys(named)) {
			if !cardShows(card, number) {
				continue
			}
			if len(r.Picks) > 0 {
				seconds = append(seconds, fmt.Sprintf("#%d already has its card %s (%s)", number, card.URL, card.ID))
				continue
			}
			if pr := named[number]; strings.HasPrefix(pr, "https://") && !slices.Contains(r.live, pr) {
				r.live = append(r.live, pr)
				if !strings.Contains(r.MD, card.URL) {
					r.MD += fmt.Sprintf("\n\n[#%d's card](%s)", number, card.URL)
				}
			}
		}
	}
	if len(seconds) > 0 {
		return r, fmt.Errorf("SECOND POST: %s; a PR shows on one card, so nothing posted: ask on that card by editing it with its id, or take it down first with `lr withdraw <its id> --duplicate-of %s`", strings.Join(seconds, "; "), own)
	}
	return r, nil
}

func cardShows(card board.OpenCard, number int) bool {
	if strings.HasPrefix(card.ID, fmt.Sprintf("merge:%d:", number)) || strings.HasPrefix(card.ID, fmt.Sprintf("card-tap-%d-", number)) {
		return true
	}
	if slices.ContainsFunc(titledPR.FindAllStringSubmatch(card.Title, -1), func(found []string) bool { return found[1] == strconv.Itoa(number) }) {
		return true
	}
	var block struct {
		Children []cardChild `json:"children"`
	}
	if json.Unmarshal(card.Block, &block) != nil {
		return false
	}
	return slices.ContainsFunc(block.Children, func(child cardChild) bool {
		return child.Type == "pr" && child.Number.String() == strconv.Itoa(number)
	})
}

type cardChild struct {
	Type   string      `json:"type"`
	Number json.Number `json:"number"`
}

func (f Fanout) drawCard(ctx context.Context, r Reply, block string) (shell.Result, error) {
	argv := []string{"cc-present", "update-block", "-", "--session", f.Entry.Root(), "--round", "current"}
	lane := os.Getenv(laneEnv)
	if r.TakeOver {
		if lane == "" {
			return shell.Result{}, fmt.Errorf("take_over needs the caller's lane in %s", laneEnv)
		}
		argv = append(argv, "--take-over", "--lane", lane)
	}
	draw := func(argv []string) (shell.Result, error) {
		return f.Runner.Run(ctx, shell.Command{Argv: argv, Stdin: block, Dir: f.Entry.Checkout, Timeout: postTimeout})
	}
	done, err := draw(argv)
	var exit *shell.ExitError
	if err == nil || r.TakeOver || lane == "" || !errors.As(err, &exit) {
		return done, err
	}
	owner := ownedCard.FindStringSubmatch(exit.Stderr)
	if owner == nil || !f.retired(owner[1]) {
		return done, err
	}
	return draw(append(argv, "--take-over", "--lane", lane))
}

func (f Fanout) retired(lane string) bool {
	s, err := store.Open(f.Entry.Store())
	if err != nil {
		return false
	}
	defer func() { _ = s.Close() }()
	found, ok, err := s.Lane(lane)
	return err == nil && ok && found.State == store.LaneRetired
}
