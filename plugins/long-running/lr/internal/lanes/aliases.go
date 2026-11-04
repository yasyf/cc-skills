package lanes

import (
	"regexp"
	"strings"

	"github.com/yasyf/cc-inbox/client"
)

var laneToken = regexp.MustCompile(`https?://[^\s)>|]+|[\w./~-]+`)

type Aliases map[string]string

func AliasesOf(names []string) Aliases {
	aliases := Aliases{}
	for _, name := range names {
		if alias := client.Stem(name); alias != name {
			aliases[name] = alias
		}
	}
	return aliases
}

func (a Aliases) In(text string) string {
	return laneToken.ReplaceAllStringFunc(text, func(token string) string {
		word := strings.TrimRight(token, ".")
		if alias, numbered := a[word]; numbered {
			return alias + token[len(word):]
		}
		return token
	})
}

func (a Aliases) AllIn(lines []string) []string {
	out := make([]string, len(lines))
	for n, line := range lines {
		out[n] = a.In(line)
	}
	return out
}
