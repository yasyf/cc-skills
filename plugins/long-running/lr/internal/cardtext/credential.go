package cardtext

import (
	"fmt"
	"regexp"
)

var (
	credentialAsk = regexp.MustCompile(`(?i)(?:mint|rotat|revok|reissu|regenerat|renew)\w*(?:\s+[\w-]+){0,4}?\s+(?:keys?|tokens?|secrets?|credentials?|passwords?|oauth clients?)\b|new\s+(?:[\w-]+\s+){0,3}?(?:keys?|tokens?|secrets?|credentials?|passwords?)\s+(?:is\s+|are\s+)?in place\b`)
	codeLine      = regexp.MustCompile(`[\w./-]+\.(?:go|rs|ts|tsx|js|py|sh|swift|ya?ml|toml|tf)(?::\d+|#L\d+)`)
)

func UncitedCredential(asked string) error {
	credential := credentialAsk.FindString(asked)
	if credential == "" || codeLine.MatchString(asked) {
		return nil
	}
	return fmt.Errorf("UNREAD CREDENTIAL: this asks him to act on a credential (%q) and cites no line of the code that reads it, so its diagnosis may rest on an error message alone, as the Nov 1 tailnet key card did; read the code path that loads that credential and cite it as file:line, or send the ask back to its lane for diagnosis", credential)
}
