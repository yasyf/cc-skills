package cardtext

import (
	"encoding/json"
	"regexp"
)

var (
	hisSteps     = regexp.MustCompile(`(?i)\bsteps? (?:of yours|for you|you (?:need|have) to)\b|\byour steps\b|\byou (?:need|have|must) to (?:log|sign|click|open|go to|create|enable|install|paste|add|set up|register|turn on)\b|\b(?:log|sign) in to\b|\bclick the\b`)
	hisAWSSignIn = regexp.MustCompile(`(?i)\baws (?:sso )?login\b|\bsso login\b|\b(?:aws|okta|sso) sign-?in\b|\b(?:log|sign) ?in (?:to|with) (?:aws|okta|sso)\b|awsapps\.com`)
)

func StepsForHim(card json.RawMessage) string {
	return hisSteps.FindString(string(card))
}

func AWSSignInForHim(card json.RawMessage) string {
	return hisAWSSignIn.FindString(string(card))
}
