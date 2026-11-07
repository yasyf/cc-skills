package cardtext

import (
	"regexp"
	"strconv"
)

const PortOpener = "Port, old tree to new: dev "

var portLine = regexp.MustCompile(`(?i)^\s*(?:port of |` + regexp.QuoteMeta(PortOpener) + `)\[?#(\d+)\b`)

func PortSource(text string) (int, bool) {
	said := portLine.FindStringSubmatch(text)
	if said == nil {
		return 0, false
	}
	source, err := strconv.Atoi(said[1])
	return source, err == nil
}
