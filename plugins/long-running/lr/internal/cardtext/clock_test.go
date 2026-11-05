package cardtext

import "testing"

func TestACardNamesTheClockInItsWords(t *testing.T) {
	for card, want := range map[string]string{
		`{"options":[{"label":"Wait for the root-cause lane's 6:40 PM report first"}]}`: "6:40 PM",
		`{"summary":"The rollout report lands by 9pm today."}`:                          "9pm",
		`{"summary":"I send it at 11 a.m. tomorrow."}`:                                  "11 a.m.",
		`{"summary":"Drawn at 2026-11-05T18:40:00Z, the 6:40 report is late."}`:         "",
		`{"title":"Which of 3 options, and am I right?"}`:                               "",
		`{"summary":"The 12 PMs on the list have no owner."}`:                           "",
	} {
		if got := NamesAClock([]byte(card)); got != want {
			t.Errorf("NamesAClock(%s) = %q, want %q", card, got, want)
		}
	}
}
