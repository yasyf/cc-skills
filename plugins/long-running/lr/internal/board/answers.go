package board

import (
	"encoding/json"
	"fmt"
	"io"

	"github.com/spf13/cobra"
)

const (
	answerStructured = "structured"
	answerNoted      = "noted"
	answerFreeText   = "free-text"
	answerNone       = "none"
)

type answerEvent struct {
	Type      string   `json:"type"`
	OptionIDs []string `json:"optionIds"`
	Other     string   `json:"other"`
	Note      string   `json:"note"`
	Verdicts  map[string]struct {
		Note string `json:"note"`
	} `json:"verdicts"`
}

func ClassifyAnswer(event []byte) (string, error) {
	var answer answerEvent
	if err := json.Unmarshal(event, &answer); err != nil {
		return "", err
	}
	switch answer.Type {
	case "choice.selected":
		switch {
		case answer.Other != "":
			return answerFreeText, nil
		case len(answer.OptionIDs) > 0:
			return answerStructured, nil
		}
		return answerNone, nil
	case "decision.created":
		if answer.Note != "" {
			return answerNoted, nil
		}
		return answerStructured, nil
	case "triage.decided":
		for _, verdict := range answer.Verdicts {
			if verdict.Note != "" {
				return answerNoted, nil
			}
		}
		return answerStructured, nil
	case "submit":
		return answerNone, nil
	}
	return answerFreeText, nil
}

func classifyCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "classify",
		Short: "Name how the board event on stdin routes: structured, noted, free-text or none",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			event, err := io.ReadAll(cmd.InOrStdin())
			if err != nil {
				return err
			}
			answer, err := ClassifyAnswer(event)
			if err != nil {
				return err
			}
			fmt.Fprintln(cmd.OutOrStdout(), answer)
			return nil
		},
	}
}
