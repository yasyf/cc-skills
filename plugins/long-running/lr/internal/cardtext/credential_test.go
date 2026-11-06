package cardtext

import (
	"strings"
	"testing"
)

func TestACredentialAskCitesTheCodeThatReadsTheCredential(t *testing.T) {
	const tailnet = "Tailscale API key for cc-remote is invalid\nThe Tailscale API key cc-remote enrolls Sprites with is invalid: '500 invalid key: API key not valid'.\nNew key is in place :: you mint a new auth key and the lane re-runs the prepare.\nRun codex workers locally until then"
	for name, tc := range map[string]struct {
		asked   string
		refused bool
	}{
		"the Nov 1 tailnet key card":                {tailnet, true},
		"a mint after a newline in the card's JSON": {`{"type":"card","md":"The relay key is invalid.\nMint a new relay key?"}`, true},
		"a rotation with only an error message":     {"Rotate the Datadog API key? The api answered 403 Forbidden.", true},
		"a token to mint":                           {"Mint a fresh GitHub token for the relay?", true},
		"the tailnet card citing its code line":     {tailnet + "\ncc-remote reads it at internal/tailnet/enroll.go:88", false},
		"a rotation citing a GitHub line":           {"Rotate the Datadog API key? Read at https://github.com/Forge-AI/monorepo/blob/dev/go/ci/dd.go#L42", false},
		"a cache key is no credential":              {"Replace the cache key with the head sha?", false},
		"keeping keys asks him to act on none":      {"Keep the ai-oncall Datadog keys?", false},
		"a landing ask":                             {"Land #49774 now?", false},
		"a rotation of something that is not a key": {"Rotate the on-call schedule to Shrey?", false},
	} {
		t.Run(name, func(t *testing.T) {
			err := UncitedCredential(tc.asked)
			if tc.refused != (err != nil) || tc.refused && !strings.HasPrefix(err.Error(), "UNREAD CREDENTIAL") {
				t.Fatalf("refusal = %v, want refused %v", err, tc.refused)
			}
		})
	}
}
