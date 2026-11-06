package cardtext

const (
	CardLabel      = "card:"
	BoardCardLabel = "board-card:"
)

func AnswerLabels(card string) []string {
	return []string{CardLabel + card, BoardCardLabel + card}
}
