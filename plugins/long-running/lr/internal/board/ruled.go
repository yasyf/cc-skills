package board

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ledger"
)

const (
	markOrder     = "RULED"
	markAnswer    = "ANSWERED"
	markEnded     = "ENDED"
	questionWords = 3
	kindOwner     = "owner"
)

var (
	rulingOrder   = regexp.MustCompile(`(?i)\b(?:just delete|jsut delete|delete (?:all|the|them|it|those|these)|get rid of|no such thing|not in scope|out of scope|kill (?:it|them|the))\b`)
	offeredOption = regexp.MustCompile(`(?i)^(?:options?(?: offered)?:|question:|hint:)|\(recommended\)`)
	sentenceEnd   = regexp.MustCompile(`[.!?\n]+\s+`)
	liftAsk       = regexp.MustCompile(`(?i)\b(?:lift(?:s|ed|ing)?|restat\w*|reaffirm\w*|your (?:order|rule) (?:was|is|says)|still (?:binds?|holds?|stands?|applies))\b`)
	overnight     = regexp.MustCompile(`(?i)\b(?:overnight|(?:before|until) (?:i|he|you) (?:wakes?|woke|wake up))\b`)
	untilPick     = regexp.MustCompile(`(?i)\buntil (?:i|he|you) (?:picks?|picked|selects?|selected|chooses?|chose)\b`)
	subjectWord   = regexp.MustCompile(`[A-Za-z][A-Za-z0-9_]+`)
	plainWords    = func() map[string]bool {
		words := map[string]bool{}
		for _, word := range strings.Fields(`the and for are its this that with from not should what which how your you our does did will can may when while has have had were was they them their there then than but about into over under per via only just also more most much many get gets got make makes made use used using need needs needed want wants still now today every some other same own after before once his him she her these those would could off down back again here very too etc yes all any each one who whose where why`) {
			words[word] = true
		}
		return words
	}()
)

type OpenAsk struct {
	ID    string
	Title string
	Slack bool
}

func (c Cards) OpenAsks(ctx context.Context) ([]OpenAsk, error) {
	shown, err := c.present(ctx, "outcomes", "--all")
	if err != nil {
		return nil, err
	}
	var outcomes struct {
		Doc struct {
			Blocks []struct {
				ID    string `json:"id"`
				Type  string `json:"type"`
				Title string `json:"title"`
				Slack *struct {
					URL string `json:"url"`
				} `json:"slack"`
				Children []struct {
					ID   string `json:"id"`
					Type string `json:"type"`
				} `json:"children"`
			} `json:"blocks"`
		} `json:"doc"`
		Interactions struct {
			Choices map[string]json.RawMessage `json:"choices"`
		} `json:"interactions"`
	}
	if err := json.Unmarshal([]byte(shown), &outcomes); err != nil {
		return nil, fmt.Errorf("cc-present outcomes: %w", err)
	}
	var asks []OpenAsk
	for _, block := range outcomes.Doc.Blocks {
		if block.Type != "card" || block.ID == mergeTriage {
			continue
		}
		asking := false
		for _, child := range block.Children {
			if child.Type != "choice" {
				continue
			}
			if _, answered := outcomes.Interactions.Choices[child.ID]; answered {
				asking = false
				break
			}
			asking = true
		}
		if asking {
			asks = append(asks, OpenAsk{block.ID, block.Title, block.Slack != nil})
		}
	}
	return asks, nil
}

type Ruling struct {
	ID     string
	Title  string
	Said   string
	At     string
	Mark   string
	answer bool
	row    map[string]string
}

func (r Ruling) Line() string {
	return fmt.Sprintf("%s %s %s %s: %q", r.Mark, r.ID[:min(len(r.ID), 12)], r.At[:min(len(r.At), 10)], r.Title, clip(r.Said))
}

func (c Cards) Ruled(ctx context.Context, title, text string) ([]Ruling, error) {
	var ended, orders, answers []Ruling
	spoken, unread := c.spoken(ctx)
	asks := liftAsk.MatchString(text)
	for _, said := range spoken {
		if said.Title == title {
			continue
		}
		if asks && leansOn(text, said) {
			if why := endOf(said, title, spoken, c.Now()); why != "" {
				said.Title, said.Mark = "ended: "+why, markEnded
				ended = append(ended, said)
				continue
			}
		}
		if sentence, ordered := orderOn(title, said); ordered {
			said.Said, said.Mark = sentence, markOrder
			orders = append(orders, said)
		} else if sameQuestion(title, sentenceEnd.Split(said.Title, 2)[0]) {
			said.Mark = markAnswer
			answers = append(answers, said)
		}
	}
	newestFirst := func(one, other Ruling) int { return strings.Compare(other.At, one.At) }
	slices.SortStableFunc(orders, newestFirst)
	slices.SortStableFunc(answers, newestFirst)
	return slices.Concat(ended, answers, orders), unread
}

func endOf(rule Ruling, title string, spoken []Ruling, now time.Time) string {
	switch rule.row["status"] {
	case "":
		return ""
	case "superseded":
		return "superseded by " + rule.row["superseded_by"]
	case "met":
		return "met: " + rule.row["evidence"]
	}
	if overnight.MatchString(rule.Said) {
		said, err := time.Parse(time.RFC3339, rule.At)
		if err != nil {
			return ""
		}
		local := said.In(pacificZone)
		end := time.Date(local.Year(), local.Month(), local.Day(), 9, 0, 0, 0, pacificZone)
		if !end.After(said) {
			end = end.AddDate(0, 0, 1)
		}
		if now.After(end) {
			return "its words hold it overnight, and " + end.Format("Jan 2 3:04 PM MST") + " has passed"
		}
	}
	if untilPick.MatchString(rule.Said) {
		for _, answer := range spoken {
			if answer.answer && answer.At > rule.At && SharedWordPairs(title, answer.Title) > 0 {
				return "its words hold it until he picks, and he answered " + answer.ID[:min(len(answer.ID), 12)]
			}
		}
	}
	return ""
}

func leansOn(text string, rule Ruling) bool {
	return strings.Contains(text, rule.ID) || SharedWordPairs(text, rule.Said) > 0 ||
		overnight.MatchString(text) && overnight.MatchString(rule.Said) || untilPick.MatchString(text) && untilPick.MatchString(rule.Said)
}

func orderOn(title string, said Ruling) (string, bool) {
	for _, sentence := range sentenceEnd.Split(said.Said, -1) {
		if !rulingOrder.MatchString(sentence) || offeredOption.MatchString(sentence) {
			continue
		}
		if SharedWordPairs(title, said.Title) > 0 || SharedWordPairs(title, sentence) > 0 {
			return sentence, true
		}
	}
	return "", false
}

func sameQuestion(card, asked string) bool {
	theirs := map[string]bool{}
	for _, word := range subjectWords(asked) {
		theirs[word] = true
	}
	mine := map[string]bool{}
	for _, word := range subjectWords(card) {
		mine[word] = true
	}
	shared := 0
	for word := range mine {
		if theirs[word] {
			shared++
		}
	}
	return shared >= questionWords && shared*2 >= len(mine)
}

func (c Cards) spoken(ctx context.Context) ([]Ruling, error) {
	var unread []error
	var answers []struct {
		ID    string `json:"id"`
		Title string `json:"title"`
		Body  string `json:"body"`
		At    string `json:"created_at"`
	}
	listed, err := c.ccn(ctx, "answer", "list", "--json", "--full")
	if err == nil {
		err = json.Unmarshal([]byte(listed), &answers)
	}
	if err != nil {
		unread = append(unread, fmt.Errorf("his ccn answers were not read: %w", err))
	}
	rows, err := ledger.Register(ctx, c.ccn, c.Ledger, c.Program)
	if err != nil {
		unread = append(unread, fmt.Errorf("the register was not read: %w", err))
	}
	lines, err := client.Tail(ctx, client.Query{Drive: c.Bus, Lanes: []string{lanes.OwnerRelay}, Kinds: []string{kindOwner}})
	if err != nil {
		unread = append(unread, fmt.Errorf("his Slack lines were not read: %w", err))
	}
	said := make([]Ruling, 0, len(answers)+len(rows)+len(lines))
	for _, answer := range answers {
		said = append(said, Ruling{ID: answer.ID, Title: answer.Title, Said: answer.Body, At: answer.At, answer: true})
	}
	for _, row := range rows {
		said = append(said, Ruling{ID: row.Key, Said: row.Fields["words"], At: row.Updated.Format(time.RFC3339), row: row.Fields})
	}
	for _, line := range lines {
		said = append(said, Ruling{ID: fmt.Sprintf("#%d", line.Seq), Title: clip(line.Text), Said: line.Text, At: line.At.Format(time.RFC3339)})
	}
	return said, errors.Join(unread...)
}

func subjectWords(text string) []string {
	var words []string
	for _, word := range subjectWord.FindAllString(strings.ToLower(text), -1) {
		if len(word) < 3 || plainWords[word] {
			continue
		}
		switch {
		case strings.HasSuffix(word, "ies") && len(word) > 4:
			word = strings.TrimSuffix(word, "ies") + "y"
		case strings.HasSuffix(word, "s") && !strings.HasSuffix(word, "ss") && len(word) > 3:
			word = strings.TrimSuffix(word, "s")
		}
		words = append(words, word)
	}
	return words
}

func wordPairs(text string) map[[2]string]bool {
	words := subjectWords(text)
	pairs := make(map[[2]string]bool, len(words))
	for i := 1; i < len(words); i++ {
		pairs[[2]string{words[i-1], words[i]}] = true
	}
	return pairs
}

func SharedWordPairs(one, other string) int {
	theirs := wordPairs(other)
	shared := 0
	for pair := range wordPairs(one) {
		if theirs[pair] {
			shared++
		}
	}
	return shared
}
