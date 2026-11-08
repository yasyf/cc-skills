package cardtext

import (
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"strconv"
	"strings"
)

var (
	namedPR    = regexp.MustCompile(`(?:^|[^\w&/])#(\d{2,})\b|/pull/(\d+)`)
	changeKind = regexp.MustCompile(`(?i)\b(?:port(?:s|ed)?|fix(?:es|ed)?|new|clean ?ups?)\b`)
	portKind   = regexp.MustCompile(`(?i)\bport`)
	newTree    = regexp.MustCompile(`(?i)\bnew tree\b`)
)

func Unlabeled(title, body string, asks bool, ports func([]int) (bool, error)) error {
	shown := HiddenCite.ReplaceAllString(title+"\n"+body, " ")
	var numbers []int
	for _, found := range namedPR.FindAllStringSubmatch(shown, -1) {
		number, _ := strconv.Atoi(found[1] + found[2])
		numbers = append(numbers, number)
	}
	if len(numbers) == 0 {
		return nil
	}
	port, err := ports(numbers)
	if err != nil {
		return fmt.Errorf("the PR ledger did not read, so whether this card is about a port is unknown and it was not drawn: %w", err)
	}
	if port && !portKind.MatchString(title) {
		return fmt.Errorf("refused: the title %q is about a port and does not say so; open it with %q and the PR it ports, then what he is choosing", title, PortOpener+"#<n>")
	}
	if !changeKind.MatchString(title) {
		return fmt.Errorf("refused: the title %q names a PR but not what kind of change it is; open it with port, fix, new or cleanup, then what he is choosing", title)
	}
	if !asks || !portKind.MatchString(title) {
		return nil
	}
	if next, _, _ := strings.Cut(strings.TrimSpace(HiddenCite.ReplaceAllString(body, " ")), "\n"); !newTree.MatchString(next) {
		return errors.New("refused: a port that asks him something says on the line under its title what differs in the new tree that forces the choice")
	}
	return nil
}

func UnlabeledBlock(card json.RawMessage, ports func([]int) (bool, error)) error {
	var block struct {
		Title    string `json:"title"`
		Summary  string `json:"summary"`
		Children []struct {
			Type string `json:"type"`
			MD   string `json:"md"`
		} `json:"children"`
	}
	if err := json.Unmarshal(card, &block); err != nil {
		return err
	}
	body := []string{block.Summary}
	asks := false
	for _, child := range block.Children {
		switch child.Type {
		case "markdown":
			body = append(body, child.MD)
		case "choice":
			asks = true
		}
	}
	return Unlabeled(block.Title, strings.TrimSpace(strings.Join(body, "\n")), asks, ports)
}
