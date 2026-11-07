package cardtext

import "testing"

func TestAPortNamesItsSourceInEitherOpening(t *testing.T) {
	for text, want := range map[string]int{
		"Port, old tree to new: dev #52007 (port of #52007)\n\n## Context":         52007,
		"port of #52125\n\nthe rest":                                               52125,
		"  Port, old tree to new: dev [#52082](https://github.com/o/r/pull/52082)": 52082,
		"sand-next: 🚩 Google is a connected app (port of #52007)":                  0,
		"Ports #52007 to the new tree":                                             0,
		"":                                                                         0,
	} {
		source, said := PortSource(text)
		if source != want || said != (want != 0) {
			t.Errorf("PortSource(%q) = %d, %v; want %d", text, source, said, want)
		}
	}
}
