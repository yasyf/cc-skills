package board

import (
	"encoding/json"
	"fmt"
	"strings"
)

const (
	MergeOption  = "merge"
	CardPRFields = "title,author,additions,deletions,reviewDecision"

	holdOption = "hold"
	waitsSHA   = 7
)

var reviewWords = map[string]string{"APPROVED": "approved", "CHANGES_REQUESTED": "changes requested", "REVIEW_REQUIRED": "waiting for review"}

type ReservedRow struct {
	Key      string
	Sentence string
}

type CardPR struct {
	Title  string `json:"title"`
	Author struct {
		Login string `json:"login"`
	} `json:"author"`
	Additions      int    `json:"additions"`
	Deletions      int    `json:"deletions"`
	ReviewDecision string `json:"reviewDecision"`
}

func MergeWaits(number int, head string) string {
	return fmt.Sprintf("merge:%d:%s", number, head[:min(waitsSHA, len(head))])
}

func AnswerBlock(waits string) string {
	return waits + "-answer"
}

func ReservedCard(repo string, number int, head string, rows []ReservedRow, pr CardPR) (string, error) {
	waits := MergeWaits(number, head)
	title := headed(number, pr.Title, func(named string) string { return "Merge " + named + "?" })
	var why strings.Builder
	if words := PlainTitle(pr.Title); words != "" && !strings.Contains(title, words) {
		fmt.Fprintf(&why, "#%d: %s.\n\n", number, strings.TrimSuffix(words, "."))
	}
	for _, row := range rows {
		fmt.Fprintf(&why, "It touches your rule \"%s\". ", row.Sentence)
	}
	words, known := reviewWords[pr.ReviewDecision]
	if !known {
		words = "none yet"
	}
	why.WriteString("Merge also allows it for this PR; Hold keeps it out of the queue.")
	fmt.Fprintf(&why, "\n\nBy %s, +%d \u2212%d lines, review: %s.", pr.Author.Login, pr.Additions, pr.Deletions, words)
	card, err := json.Marshal(map[string]any{
		"id":    waits,
		"type":  "card",
		"title": title,
		"children": []map[string]any{
			{"id": waits + "-context", "type": "markdown", "md": why.String()},
			{"id": waits + "-pr", "type": "pr", "repo": repo, "number": number},
			{"id": AnswerBlock(waits), "type": "choice", "options": []map[string]any{
				{"id": MergeOption, "label": "Merge", "recommended": true},
				{"id": holdOption, "label": "Hold"},
			}},
		},
	})
	return string(card), err
}
