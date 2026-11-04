package cardtext

import (
	"encoding/json"
	"regexp"
)

var (
	endsProcess  = regexp.MustCompile(`(?i)\b(?:restart|kill|end|stop|terminate|relaunch)\b[^.?!"]{0,60}\b(?:process|pid|daemon|recorder|runner|server|service|watcher|supervisor|worker)\b`)
	namesProcess = regexp.MustCompile(`\bpid \d+\b|[\w-]+\.(?:py|js|mjs|cjs|ts|sh|rb|pl)\b`)
)

func EndsAnUnnamedProcess(card json.RawMessage) string {
	if namesProcess.Match(card) {
		return ""
	}
	return endsProcess.FindString(string(card))
}
