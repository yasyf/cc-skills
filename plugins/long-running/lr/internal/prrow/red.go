package prrow

import (
	"regexp"
	"slices"
	"strings"
)

const (
	RedInherited    = "inherited"
	ShortestRedHead = 7
)

var (
	redField   = regexp.MustCompile(`\b(class|head|owner)=(\S+)`)
	redClasses = []string{RedInherited, "flake", "real", "stale"}
)

type RedClass struct {
	Class  string
	Head   string
	Owner  string
	Action string
}

func ParseRedClass(text string) (RedClass, bool) {
	fields := map[string]string{}
	for _, match := range redField.FindAllStringSubmatch(text, -1) {
		fields[match[1]] = match[2]
	}
	if !slices.Contains(redClasses, fields["class"]) || len(fields["head"]) < ShortestRedHead {
		return RedClass{}, false
	}
	action := strings.Join(strings.Fields(redField.ReplaceAllString(text, "")), " ")
	return RedClass{fields["class"], fields["head"], fields["owner"], action}, true
}
