package cardtext

import (
	"regexp"
	"strings"
)

var numberedPick = regexp.MustCompile(`(?i)^(?:(?:i )?pick |option )?#?\d+(?:\.?$|[\s,:;+-])`)

func NumberedPick(words string) bool {
	said := strings.TrimSpace(words)
	return numberedPick.MatchString(said) && !strings.HasSuffix(said, "?")
}
