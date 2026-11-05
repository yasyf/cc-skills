package board

import (
	"context"
	"encoding/json"
	"regexp"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	pickedLead = "picked “"
	pickedEnd  = "”"
)

var (
	answerCite = regexp.MustCompile(`(?i)\b(?:(?:owner|his|your) )?(?:answers?|rulings?) ([0-9a-f]{7,40}(?:(?:, and |, | and )[0-9a-f]{7,40})*)\b`)
	answerID   = regexp.MustCompile(`[0-9a-f]{7,40}`)
)

// AnswerWords reads the answers a card's text cites by id, once per id for one
// draw.
type AnswerWords struct {
	runner   shell.Runner
	checkout string
	read     map[string]string
}

func NewAnswerWords(runner shell.Runner, checkout string) *AnswerWords {
	return &AnswerWords{runner: runner, checkout: checkout, read: map[string]string{}}
}

// Said replaces each cite such as "owner answers 02cd8a5 and 366f958" with what
// those answers were about and what he picked. A cite naming an id that reads as
// no answer stays as written.
func (w *AnswerWords) Said(ctx context.Context, text string) string {
	return answerCite.ReplaceAllStringFunc(text, func(cite string) string {
		ids := answerID.FindAllString(cite, -1)
		said := make([]string, len(ids))
		for i, id := range ids {
			said[i] = w.one(ctx, id)
			if said[i] == "" {
				return cite
			}
		}
		return strings.Join(said, "; ")
	})
}

func (w *AnswerWords) one(ctx context.Context, id string) string {
	if said, read := w.read[id]; read {
		return said
	}
	said := ""
	done, err := w.runner.Run(ctx, shell.Command{Argv: []string{"ccn", "-R", w.checkout, "answer", "show", id, "--json"}})
	var answer struct {
		Title string `json:"title"`
		Body  string `json:"body"`
	}
	if err == nil && json.Unmarshal([]byte(done.Stdout), &answer) == nil && answer.Title != "" {
		said = "your answer on “" + answer.Title + "”"
		first, _, _ := strings.Cut(answer.Body, "\n")
		if picked, found := strings.CutPrefix(first, pickedLead); found {
			said += ": " + strings.TrimSuffix(picked, pickedEnd)
		}
	}
	w.read[id] = said
	return said
}
