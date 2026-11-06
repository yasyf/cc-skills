package cardtext

import "testing"

func TestANumberPicksOnlyWhenItLeadsAndAsksNothing(t *testing.T) {
	for words, want := range map[string]bool{
		"1":                      true,
		"1.":                     true,
		"#1":                     true,
		"I pick 1":               true,
		"pick 2":                 true,
		"Option 2":               true,
		"option #2":              true,
		"1 but port it first":    true,
		"1, ship this one":       true,
		"#2 and tell the vendor": true,
		"1 and 2":                true,
		"1 + file it with the vendor, but look for a duplicate report first ": true,
		"":                                      false,
		"what does that word mean?":             false,
		"Sounds good do that":                   false,
		"why not 1?":                            false,
		"1 or 2?":                               false,
		"go with what you said about 2 earlier": false,
		"1.5 hours is too long":                 false,
		"1. this line is not needed, 2) there is no button here": false,
		"1) the markdown did not render":                         false,
	} {
		if got := NumberedPick(words); got != want {
			t.Errorf("NumberedPick(%q) = %v, want %v", words, got, want)
		}
	}
}
