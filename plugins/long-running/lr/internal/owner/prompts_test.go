package owner

import (
	"bytes"
	"strings"
	"testing"
)

func TestPromptsPrintsOnlyTheCapturesAfterTheCursor(t *testing.T) {
	stored := typedLine + "\n" + cutLine + wholeLine
	for _, c := range []struct{ since, want string }{
		{"", typedLine + cutLine + wholeLine},
		{"629293ae14388dc9", cutLine + wholeLine},
		{"72d2b9383e1ac624", ""},
	} {
		var out bytes.Buffer
		if err := capturesAfter(stored, c.since, &out); err != nil || out.String() != c.want {
			t.Fatalf("captures after %q printed %q, %v, want %q", c.since, out.String(), err, c.want)
		}
	}
}

func TestPromptsRefusesACursorNoCaptureHolds(t *testing.T) {
	var out bytes.Buffer
	err := capturesAfter(typedLine+cutLine, "ffffffffffffffff", &out)
	if err == nil || !strings.Contains(err.Error(), "ffffffffffffffff") || out.Len() != 0 {
		t.Fatalf("an unknown cursor printed %q and returned %v, want a refusal naming it and no captures", out.String(), err)
	}
}
