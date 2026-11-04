package cardtext

import (
	"encoding/json"
	"testing"
)

func TestACardThatHandsHimStepsIsNamed(t *testing.T) {
	for text, want := range map[string]string{
		"Six steps of yours for the phone approver, steps 1 to 4?": "steps of yours",
		"You need to sign in to the console and paste the key":     "You need to sign",
		"Log in to Apple and enable push":                          "Log in to",
		"Which store holds the topics?":                            "",
		"Land #49774 now?":                                         "",
	} {
		card, err := json.Marshal(map[string]any{"type": "card", "title": text})
		if err != nil {
			t.Fatal(err)
		}
		if got := StepsForHim(card); got != want {
			t.Errorf("%q named %q, want %q", text, got, want)
		}
	}
}
