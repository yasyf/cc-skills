package cardtext

import (
	"encoding/json"
	"fmt"
	"maps"
	"regexp"
	"slices"
	"strings"
)

const (
	background  = "background"
	explainMore = "Explain more"
)

var (
	Link       = regexp.MustCompile(`!?\[[^\]\n]*\]\([^)\n]*\)|<?https?://[^\s>)]+>?`)
	HiddenCite = regexp.MustCompile(`(?s)\s*<!--.*?-->`)
	word       = regexp.MustCompile(`[\p{L}\p{N}][\p{L}\p{N}'’-]*`)
	section    = regexp.MustCompile(`(?im)^[ \t>*_#-]*(explain more|background)(?:\*\*|__)?[ \t]*:(?:\*\*|__)?`)
	nextName   = regexp.MustCompile(`(?m)^[ \t]*(?:\*\*|__)?[A-Za-z][\w' -]{0,40}:(?:\*\*|__)?(?:\s|$)`)
	pointing   = func() map[string]bool {
		words := map[string]bool{}
		for _, each := range strings.Fields(`a above all an and at below click design details detail doc docs document explainer for
			full go here in it its link linked more of on one-pager onepager open our page plan please read section see the this to whole write-up writeup`) {
			words[each] = true
		}
		return words
	}()
)

func Bare(card json.RawMessage) string {
	var block map[string]any
	if json.Unmarshal(card, &block) != nil || block["type"] != "card" {
		return ""
	}
	told, _ := block["summary"].(string)
	children, _ := block["children"].([]any)
	for _, child := range children {
		fields, _ := child.(map[string]any)
		kind, _ := fields["type"].(string)
		if kind == "diagram" || strings.HasPrefix(kind, "display.") {
			return ""
		}
		if md, isText := fields["md"].(string); isText && kind == "markdown" {
			told += "\n" + md
		}
	}
	if strings.TrimSpace(told) != "" && onlyPoints(told) {
		return background
	}
	return bareIn(block)
}

func Refusal(name string) error {
	return fmt.Errorf("refused: its %s is only a link, and he reads cards where they are: put the write-up's section in the card, using cc-present's display.designdoc block or the architecture diagram where it has one; a link may sit under that text", name)
}

func bareIn(value any) string {
	switch held := value.(type) {
	case []any:
		for _, each := range held {
			if name := bareIn(each); name != "" {
				return name
			}
		}
	case map[string]any:
		for _, key := range slices.Sorted(maps.Keys(held)) {
			if text, isText := held[key].(string); isText && (key == "md" || key == "summary") {
				if name := bareSection(text); name != "" {
					return name
				}
			}
			if detail, isDetail := held[key].(map[string]any); isDetail && key == "detail" {
				if md, _ := detail["md"].(string); strings.TrimSpace(md) != "" && onlyPoints(md) {
					return explainMore
				}
			}
			if name := bareIn(held[key]); name != "" {
				return name
			}
		}
	}
	return ""
}

func bareSection(text string) string {
	for _, at := range section.FindAllStringSubmatchIndex(text, -1) {
		first, rest, _ := strings.Cut(text[at[1]:], "\n")
		if next := nextName.FindStringIndex(rest); next != nil {
			rest = rest[:next[0]]
		}
		if onlyPoints(first + "\n" + rest) {
			if strings.EqualFold(text[at[2]:at[3]], background) {
				return background
			}
			return explainMore
		}
	}
	return ""
}

func onlyPoints(text string) bool {
	for _, each := range word.FindAllString(Link.ReplaceAllString(text, " "), -1) {
		if !pointing[strings.ToLower(each)] {
			return false
		}
	}
	return true
}
