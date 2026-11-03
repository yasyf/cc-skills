package board

import (
	"bufio"
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"testing"
)

func TestEachAnswerClassifiesByWhetherItCarriesTheOwnersWords(t *testing.T) {
	for _, test := range []struct{ event, want string }{
		{`{"type":"choice.selected","optionIds":["yes"]}`, answerStructured},
		{`{"type":"choice.selected","optionIds":[],"other":"only for the team"}`, answerFreeText},
		{`{"type":"choice.selected","optionIds":["yes"],"other":"only for the team"}`, answerFreeText},
		{`{"type":"choice.selected","optionIds":[]}`, answerNone},
		{`{"type":"decision.created","verdict":"approved"}`, answerStructured},
		{`{"type":"decision.created","verdict":"rejected","note":"keep the old text"}`, answerNoted},
		{`{"type":"triage.decided","verdicts":{"202":{"verdict":"approved"}}}`, answerStructured},
		{`{"type":"triage.decided","verdicts":{"202":{"verdict":"rejected","note":"split it"}}}`, answerNoted},
		{`{"type":"input.submitted","text":"also check the share menu"}`, answerFreeText},
		{`{"type":"feedback.created","text":"say who can open it"}`, answerFreeText},
		{`{"type":"pack.interaction","payload":{"row":"a"}}`, answerFreeText},
		{`{"type":"submit","revision":3}`, answerNone},
	} {
		got, err := ClassifyAnswer([]byte(test.event))
		if err != nil {
			t.Fatalf("%s: %v", test.event, err)
		}
		if got != test.want {
			t.Errorf("%s: got %s, want %s", test.event, got, test.want)
		}
	}
}

func TestARecordedBoardStreamRoutesEachOwnerEvent(t *testing.T) {
	file, err := os.Open(filepath.Join("..", "..", "..", "tests", "fixtures", "board_watch", "stream.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	var got []string
	lines := bufio.NewScanner(file)
	for lines.Scan() {
		var event struct {
			BlockID string `json:"blockId"`
		}
		if err := json.Unmarshal(lines.Bytes(), &event); err != nil {
			t.Fatal(err)
		}
		if event.BlockID == "" {
			continue
		}
		answer, err := ClassifyAnswer(lines.Bytes())
		if err != nil {
			t.Fatal(err)
		}
		got = append(got, answer)
	}
	want := []string{answerFreeText, answerStructured, answerFreeText, answerFreeText, answerStructured, answerNoted, answerStructured}
	if !slices.Equal(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestAnEventThatIsNotJSONIsRefused(t *testing.T) {
	if _, err := ClassifyAnswer([]byte("not json")); err == nil {
		t.Fatal("got no error")
	}
}
