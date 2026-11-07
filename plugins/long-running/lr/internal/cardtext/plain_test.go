package cardtext

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestTheBarRefusesWordsHeCannotReadWithoutALookup(t *testing.T) {
	for name, said := range map[string]string{
		"a corpus row":        "C211 and C220 wait for the other Mac's chat.",
		"a regression row":    "It fixes REG-12.",
		"a Go test name":      "TestForkStartsBlank failed.",
		"a test file":         "irisServe.e2e.ts waits a minute.",
		"an incident lane":    "dd-337919253-fix asks whether to pair.",
		"a desk":              "meta-desk-4 owns it.",
		"the relay":           "owner-relay redrew it.",
		"a commit":            "It is green at b51dd19.",
		"a build":             "It failed on build #119124.",
		"a plan step":         "This is plan 9.1.",
		"an order row":        "It answers m1746.",
		"a replicated record": "See 399028@00624701.",
		"a card id":           "It replaces card-dd-337919253-smoke-boot-pairing.",
	} {
		if Unplain(said) == "" {
			t.Errorf("%s: %q passed the bar", name, said)
		}
	}
	for _, said := range []string{
		"A teammate's shared chat opens read-only, with no send box and no Fork.",
		"[#52452](https://github.com/Forge-AI/monorepo/pull/52452/commits/b51dd199) fixes it.",
		"Page load stays under the P95 budget. <!-- 581926 m1746 b51dd19 -->",
	} {
		if word := Unplain(said); word != "" {
			t.Errorf("%q was refused: %s", said, word)
		}
	}
}

func TestACardIsOneQuestionNoRepeatAndShortOptions(t *testing.T) {
	question, body := "Merge the fix that opens shared chats read-only?", "Teammates can no longer type into a chat that is not theirs."
	options := []string{"Merge: it lands tonight.", "Hold: it waits for the morning."}
	if err := PlainCard(question, body, options); err != nil {
		t.Fatalf("a plain card was refused: %v", err)
	}
	for name, tc := range map[string]struct {
		title, body string
		options     []string
		says        string
	}{
		"two sentences":       {"The fix is green. Merge it?", body, options, "one question in one sentence"},
		"no question":         {"Merge the fix", body, options, "one question in one sentence"},
		"a repeated title":    {question, "Merge the fix that opens shared chats read-only. It is green.", options, "repeating the title"},
		"a long option":       {question, body, []string{"Merge: it lands. It ships. It is done.", options[1]}, "two at most"},
		"a lane in an option": {question, body, []string{"Merge: card-bar lands it.", options[1]}, "plain words"},
	} {
		if err := PlainCard(tc.title, tc.body, tc.options); err == nil || !strings.Contains(err.Error(), tc.says) {
			t.Errorf("%s: err = %v, want it to say %q", name, err, tc.says)
		}
	}
}

func TestACorpusRowReadsAsWhatItsTestChecks(t *testing.T) {
	got := Described("C211 and C220 wait for the bucket", map[string]string{"C211": "a teammate's share opens read-only"})
	if got != "the test that a teammate's share opens read-only and a test wait for the bucket" {
		t.Fatalf("described = %q", got)
	}
	if word := Unplain(got); word != "" {
		t.Fatalf("the described words still fail the bar: %s", word)
	}
}

func TestABlockIsReadByItsTitleSummaryMarkdownAndOptions(t *testing.T) {
	card, err := json.Marshal(map[string]any{
		"type": "card", "title": "Pair the smoke boot?", "summary": "Smoke boots stop hanging.",
		"children": []map[string]any{
			{"type": "markdown", "md": "**From:** dd-337919253-fix"},
			{"type": "choice", "options": []map[string]any{{"label": "Pair", "md": "It boots."}, {"label": "Skip", "md": "It hangs."}}},
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	if err := PlainBlock(card); err == nil || !strings.Contains(err.Error(), "dd-337919253-fix") {
		t.Fatalf("err = %v, want the lane in its markdown refused", err)
	}
}

func TestACardRefusesCodeTermsCountsASecondQuestionAndARepeatedParagraph(t *testing.T) {
	question, options := "Pause fresh run indexing now?", []string{"Pause: search stops lagging.", "Wait: search lags an hour."}
	for name, tc := range map[string]struct{ body, says string }{
		"a method call":        {"Store.Facts() returns nothing for the row.", "code term"},
		"a package symbol":     {"It reads board.TapCard before it draws.", "code term"},
		"a snake_case name":    {"It holds safety_vest steps.", "code term"},
		"a code span":          {"Run `lr board` to see it.", "code term"},
		"a route":              {"Calls to /api/runs fail.", "code term"},
		"counts in prose":      {"5 of the 7 red builds and 52 failed calls since then.", "counts"},
		"a second question":    {"Search lags. Should we also stop the reindex?", "second question"},
		"a repeated paragraph": {"Search lags.\n\nSearch lags.", "twice"},
	} {
		if err := PlainCard(question, tc.body, options); err == nil || !strings.Contains(err.Error(), tc.says) {
			t.Errorf("%s: err = %v, want it to say %q", name, err, tc.says)
		}
	}
	if err := PlainCard(question, "Search on GitHub and macOS lags by an hour since 6:01 PM on Nov 6, behind #52189.", options); err != nil {
		t.Fatalf("a plain body with a brand, a time, a date and a PR was refused: %v", err)
	}
}

func TestACardNamingCustomersByNamePasses(t *testing.T) {
	body := "Runs recover on their own after a restart for every team below, and SoFi and GSK saw no failed run.\n\n| Team | Runs that recovered |\n|---|---|\n| SoFi | 41 |\n| AIG | 12 |\n| HSBC | 7 |\n| Chime | 3 |\n| GSK | 19 |"
	if err := PlainCard("Ship the restart fix to every team tonight?", body, []string{"Ship: every team gets it tonight.", "Wait: it ships with Monday's release."}); err != nil {
		t.Fatalf("a card naming SoFi, AIG, HSBC, Chime and GSK was refused: %v", err)
	}
}
