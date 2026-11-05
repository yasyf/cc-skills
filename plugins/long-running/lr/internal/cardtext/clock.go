package cardtext

import (
	"encoding/json"
	"regexp"
)

var clock = regexp.MustCompile(`(?i)\b(?:1[0-2]|0?[1-9])(?::[0-5]\d)?\s?[ap]\.?m\b\.?`)

func NamesAClock(card json.RawMessage) string {
	return clock.FindString(string(card))
}
