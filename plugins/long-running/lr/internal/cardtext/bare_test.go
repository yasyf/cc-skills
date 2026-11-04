package cardtext

import (
	"encoding/json"
	"testing"
)

const page = "https://docs.poetic.design/one-pagers/agent-message-schema/"

func markdownCard(t *testing.T, summary, md string, more ...map[string]any) json.RawMessage {
	t.Helper()
	children := []map[string]any{{"id": "why", "type": "markdown", "md": md}, {"id": "pick", "type": "choice", "options": []map[string]any{{"id": "a", "label": "A"}, {"id": "b", "label": "B"}}}}
	card, err := json.Marshal(map[string]any{"id": "card-x", "type": "card", "title": "Can a message carry a file?", "summary": summary, "children": append(children, more...)})
	if err != nil {
		t.Fatal(err)
	}
	return card
}

func TestACardThatOnlyPointsAtItsWriteUpIsNamed(t *testing.T) {
	for name, tc := range map[string]struct {
		summary, md, bare string
	}{
		"the agent-inboxes card":      {"Text only is built.", "**From:** New.\n\n**Explain more:** The whole design: " + page, explainMore},
		"a bare url":                  {"Text only is built.", "**Explain more:** " + page, explainMore},
		"see a link":                  {"Text only is built.", "**Explain more:** see " + page, explainMore},
		"see a markdown link":         {"Text only is built.", "**Explain more:** See [the one-pager](" + page + ") for details.", explainMore},
		"the link on its own line":    {"Text only is built.", "**Explain more:**\n\n" + page + "\n\n**Ties to:** The six-column message.", explainMore},
		"a plain label":               {"Text only is built.", "Explain more: " + page, explainMore},
		"the colon outside the bold":  {"Text only is built.", "**Background**: " + page, background},
		"an empty section":            {"Text only is built.", "**Explain more:**\n\n**Ties to:** The six-column message.", explainMore},
		"a background and no summary": {"", page, background},
		"a summary that is a link":    {"see " + page, "", background},
	} {
		if bare := Bare(markdownCard(t, tc.summary, tc.md)); bare != tc.bare {
			t.Errorf("%s: Bare = %q, want %q", name, bare, tc.bare)
		}
	}
}

func TestAnExplanationWithALinkUnderItPasses(t *testing.T) {
	for name, tc := range map[string]struct{ summary, md string }{
		"the section then its page":   {"Text only is built.", "**From:** New.\n\n**Explain more:** A message is six columns, and text only means no seventh.\n\nThe whole design: " + page},
		"text before a label":         {"Text only is built.", "**Explain more:** Two ways: keep the column or drop it. " + page},
		"a summary over a link":       {"Text only is built, because nothing sends a file today.", "- [The page](" + page + ")"},
		"no explanation asked for":    {"", ""},
		"a section naming no pointer": {"", "**Today:** No sender sets the field."},
	} {
		if bare := Bare(markdownCard(t, tc.summary, tc.md)); bare != "" {
			t.Errorf("%s: Bare = %q on a card that explains itself", name, bare)
		}
	}
}

func TestAnEmbeddedDiagramOrDesignDocCarriesTheExplanation(t *testing.T) {
	for _, kind := range []string{"diagram", "display.designdoc"} {
		if bare := Bare(markdownCard(t, "", "**Explain more:** "+page, map[string]any{"id": "embedded", "type": kind})); bare != "" {
			t.Errorf("%s: Bare = %q on a card embedding the section", kind, bare)
		}
	}
}

func TestAnOptionsDrillDownThatOnlyPointsIsNamed(t *testing.T) {
	card, err := json.Marshal(map[string]any{"type": "card", "summary": "Text only is built.", "children": []map[string]any{{"id": "pick", "type": "choice", "options": []map[string]any{
		{"id": "a", "label": "A", "detail": map[string]any{"md": "A message keeps six columns."}},
		{"id": "b", "label": "B", "detail": map[string]any{"md": "see " + page}},
	}}}})
	if err != nil {
		t.Fatal(err)
	}
	if bare := Bare(card); bare != explainMore {
		t.Errorf("Bare = %q, want %q", bare, explainMore)
	}
}
