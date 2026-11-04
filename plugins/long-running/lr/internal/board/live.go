package board

import (
	"context"
	"encoding/json"
	"fmt"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"
	_ "time/tzdata"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	verdictApproved = "approved"
	verdictRejected = "rejected"
	justNow         = "just now"
	titleChars      = 120
	shortHead       = 7
	stderrChars     = 200
)

var (
	pacificZone = mustZone("America/Los_Angeles")
	rowID       = regexp.MustCompile(`^merge-row-(\d+)(?:-[0-9a-f]{7,40})?$`)
	digits      = regexp.MustCompile(`^\d+$`)
)

func mustZone(name string) *time.Location {
	zone, err := time.LoadLocation(name)
	if err != nil {
		panic(err)
	}
	return zone
}

type Verdict struct {
	Verdict string `json:"verdict"`
	Head    string `json:"head"`
	At      string `json:"at"`
}

type Wait struct {
	Kind    string `json:"kind"`
	Block   string `json:"block"`
	Subject string `json:"subject"`
	PR      string `json:"pr"`
	Head    string `json:"head"`
	Since   string `json:"since"`
	Title   string `json:"title"`
}

func (w Wait) Line() string {
	if w.Kind == "tap" {
		return fmt.Sprintf("tap #%s at %s, head since %s: %s", w.PR, abbreviate(w.Head), w.Since, w.Title)
	}
	return fmt.Sprintf("pick %s %s: %s, up since %s", w.Block, w.Subject, w.Title, w.Since)
}

// OpenCard is a question card waiting on his pick, with the Slack message that
// carries its buttons.
type OpenCard struct {
	ID    string
	Title string
	URL   string
}

type Live struct {
	Ready []Wait
	Taps  map[string]Verdict
	Picks []Wait
	Shown map[string]time.Time
}

type Outcomes struct {
	Rev          int64          `json:"rev"`
	Doc          map[string]any `json:"doc"`
	Interactions struct {
		Choices map[string]struct {
			OptionIDs []string `json:"optionIds"`
			Other     string   `json:"other"`
		} `json:"choices"`
		Triage map[string]map[string]Verdict `json:"triage"`
	} `json:"interactions"`
}

type Snapshot struct {
	Seq   int64
	At    time.Time
	Board string
}

func abbreviate(head string) string {
	return head[:min(shortHead, len(head))]
}

func pacific(at time.Time) string {
	return at.In(pacificZone).Format("3:04 PM")
}

func clip(text string) string {
	text = strings.Join(strings.Fields(text), " ")
	runes := []rune(text)
	if len(runes) <= titleChars {
		return text
	}
	return strings.TrimRight(string(runes[:titleChars-1]), " \t\n") + "…"
}

func namesHead(named, sha string) bool {
	return sha != "" && (strings.HasPrefix(sha, named) || strings.HasPrefix(named, sha))
}

func atHead(entry Verdict, verdict, head string) bool {
	return entry.Verdict == verdict && (entry.Head == "" || namesHead(entry.Head, head))
}

func typed(node any, kind string) []map[string]any {
	var found []map[string]any
	switch value := node.(type) {
	case map[string]any:
		if value["type"] == kind {
			found = append(found, value)
		}
		for _, child := range value {
			found = append(found, typed(child, kind)...)
		}
	case []any:
		for _, child := range value {
			found = append(found, typed(child, kind)...)
		}
	}
	return found
}

func text(node map[string]any, name string) string {
	value, _ := node[name].(string)
	return value
}

func listOf(node map[string]any, name string) []map[string]any {
	items, _ := node[name].([]any)
	out := make([]map[string]any, 0, len(items))
	for _, item := range items {
		if entry, isEntry := item.(map[string]any); isEntry {
			out = append(out, entry)
		}
	}
	return out
}

func prNumber(entry map[string]any) string {
	switch value := entry["number"].(type) {
	case float64:
		return strconv.FormatInt(int64(value), 10)
	case string:
		return value
	}
	return ""
}

func prOf(id string) string {
	if digits.MatchString(id) {
		return id
	}
	if named := rowID.FindStringSubmatch(id); named != nil {
		return named[1]
	}
	return ""
}

func newestByPR(verdicts map[string]map[string]Verdict) map[string]Verdict {
	newest := map[string]Verdict{}
	for _, byRow := range verdicts {
		for id, entry := range byRow {
			pr := prOf(id)
			if pr != "" && entry.At >= newest[pr].At {
				newest[pr] = entry
			}
		}
	}
	return newest
}

func firstShown(snapshots []Snapshot) (map[string]time.Time, error) {
	ordered := slices.Clone(snapshots)
	slices.SortFunc(ordered, func(one, other Snapshot) int { return int(one.Seq - other.Seq) })
	shown := map[string]time.Time{}
	for _, snapshot := range ordered {
		var board struct {
			Cards [][]any `json:"cards"`
		}
		if err := json.Unmarshal([]byte(snapshot.Board), &board); err != nil {
			return nil, fmt.Errorf("board snapshot #%d: %w", snapshot.Seq, err)
		}
		next := map[string]time.Time{}
		mark := func(key string) {
			if at, seen := shown[key]; seen {
				next[key] = at
			} else {
				next[key] = snapshot.At
			}
		}
		for _, card := range board.Cards {
			if len(card) >= 1 {
				mark(fmt.Sprintf("card:%v", card[0]))
			}
		}
		shown = next
	}
	return shown, nil
}

func LiveOf(outcomes Outcomes, snapshots []Snapshot, rows []store.ReviewRow) (Live, error) {
	shown, err := firstShown(snapshots)
	if err != nil {
		return Live{}, err
	}
	live := Live{Ready: readyWaits(rows), Taps: newestByPR(outcomes.Interactions.Triage), Shown: shown}
	for _, card := range typed(outcomes.Doc, "card") {
		for _, choice := range typed(card, "choice") {
			if outcomes.answered(choice) {
				continue
			}
			title := text(card, "title")
			if title == "" {
				title = text(choice, "prompt")
			}
			live.Picks = append(live.Picks, Wait{Kind: "pick", Block: text(card, "id"), Subject: text(choice, "id"), Since: live.since("card:" + text(card, "id")), Title: clip(title)})
		}
	}
	return live, nil
}

func (o Outcomes) answered(choice map[string]any) bool {
	answer := o.Interactions.Choices[text(choice, "id")]
	return len(answer.OptionIDs) > 0 || answer.Other != ""
}

// OpenCards lists each card on the board with a pick he has not made and a
// Slack message to make it on.
func (o Outcomes) OpenCards() []OpenCard {
	var open []OpenCard
	for _, card := range typed(o.Doc, "card") {
		slack, _ := card["slack"].(map[string]any)
		url := text(slack, "url")
		if url != "" && slices.ContainsFunc(typed(card, "choice"), func(choice map[string]any) bool { return !o.answered(choice) }) {
			open = append(open, OpenCard{text(card, "id"), clip(text(card, "title")), url})
		}
	}
	return open
}

func (l Live) since(key string) string {
	if at, found := l.Shown[key]; found {
		return pacific(at)
	}
	return justNow
}

func (l Live) Decided(pr, head string) string {
	entry := l.Taps[pr]
	for _, verdict := range []string{verdictApproved, verdictRejected} {
		if atHead(entry, verdict, head) {
			return verdict
		}
	}
	return ""
}

func readyWaits(rows []store.ReviewRow) []Wait {
	var waits []Wait
	for _, row := range rows {
		if row.ReadyForTap && !row.Tapped {
			pr := strconv.Itoa(row.Number)
			waits = append(waits, Wait{Kind: "tap", Subject: "#" + pr, PR: pr, Head: row.Head, Since: pacificStamp(row.HeadAt), Title: clip(row.Title)})
		}
	}
	return waits
}

func (l Live) Waits() []Wait {
	return slices.Concat(l.Ready, l.Picks)
}

func ReadOutcomes(ctx context.Context, runner shell.Runner, session string) (Outcomes, error) {
	argv := []string{"cc-present", "outcomes", "--all", "--session", session}
	done, err := runner.Run(ctx, shell.Command{Argv: argv})
	if err != nil {
		return Outcomes{}, err
	}
	start := strings.Index(done.Stdout, "{")
	if done.Code != 0 || start < 0 {
		said := strings.TrimSpace(done.Stderr + done.Stdout)
		return Outcomes{}, fmt.Errorf("cc-present outcomes exited %d: %s", done.Code, said[:min(stderrChars, len(said))])
	}
	var outcomes Outcomes
	if err := json.Unmarshal([]byte(done.Stdout[start:]), &outcomes); err != nil {
		return Outcomes{}, fmt.Errorf("cc-present outcomes: %w", err)
	}
	return outcomes, nil
}

func MainBoard(ctx context.Context, runner shell.Runner, session string) ([]string, error) {
	done, err := runner.Run(ctx, shell.Command{Argv: []string{"cc-present", "sessions"}})
	if err != nil {
		return nil, err
	}
	if done.Code != 0 {
		said := strings.TrimSpace(done.Stderr + done.Stdout)
		return nil, fmt.Errorf("cc-present sessions exited %d: %s", done.Code, said[:min(stderrChars, len(said))])
	}
	var names []string
	for _, line := range strings.Split(done.Stdout, "\n") {
		fields := strings.Fields(line)
		if at := slices.Index(fields, session); at >= 2 {
			names = append(names, strings.Join(fields[:at-1], " "), fields[at-1])
		}
	}
	return names, nil
}
