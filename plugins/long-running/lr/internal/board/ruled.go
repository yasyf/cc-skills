package board

import (
	"context"
	"encoding/json"
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
	questionWords = 3
	kindOwner     = "owner"
)

var (
	rulingOrder   = regexp.MustCompile(`(?i)\b(?:just delete|jsut delete|delete (?:all|the|them|it|those|these)|get rid of|no such thing|not in scope|out of scope|kill (?:it|them|the))\b`)
	offeredOption = regexp.MustCompile(`(?i)^(?:options?(?: offered)?:|question:|hint:)|\(recommended\)`)
	sentenceEnd   = regexp.MustCompile(`[.!?\n]+\s+`)
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
	ID    string
	Title string
	Said  string
	At    string
	Mark  string
}

func (r Ruling) Line() string {
	return fmt.Sprintf("%s %s %s %s: %q", r.Mark, r.ID[:min(len(r.ID), 12)], r.At[:min(len(r.At), 10)], r.Title, clip(r.Said))
}

func (c Cards) Ruled(ctx context.Context, title string) []Ruling {
	var orders, answers []Ruling
	for _, said := range c.spoken(ctx) {
		if said.Title == title {
			continue
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
	return append(answers, orders...)
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

func (c Cards) spoken(ctx context.Context) []Ruling {
	var said []Ruling
	if listed, err := c.ccn(ctx, "answer", "list", "--json", "--full"); err == nil {
		var answers []struct {
			ID    string `json:"id"`
			Title string `json:"title"`
			Body  string `json:"body"`
			At    string `json:"created_at"`
		}
		if json.Unmarshal([]byte(listed), &answers) == nil {
			for _, answer := range answers {
				said = append(said, Ruling{ID: answer.ID, Title: answer.Title, Said: answer.Body, At: answer.At})
			}
		}
	}
	if rows, err := ledger.Register(ctx, c.ccn, c.Ledger, c.Program); err == nil {
		for _, row := range rows {
			said = append(said, Ruling{ID: row.Key, Said: row.Fields["words"], At: row.Updated.Format(time.RFC3339)})
		}
	}
	if lines, err := client.Tail(ctx, client.Query{Drive: c.Bus, Lanes: []string{lanes.OwnerRelay}, Kinds: []string{kindOwner}}); err == nil {
		for _, line := range lines {
			said = append(said, Ruling{ID: fmt.Sprintf("#%d", line.Seq), Title: clip(line.Text), Said: line.Text, At: line.At.Format(time.RFC3339)})
		}
	}
	return said
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
