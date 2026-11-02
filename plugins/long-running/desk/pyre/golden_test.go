package pyre

import (
	"maps"
	"slices"
	"testing"
)

var rejectedGolden = map[string]string{}

func TestEveryGoldenPatternCompiles(t *testing.T) {
	golden, err := ReadGolden("../testdata/patterns.json")
	if err != nil {
		t.Fatal(err)
	}
	for _, key := range slices.Sorted(maps.Keys(golden)) {
		pattern := golden[key]
		t.Run(key, func(t *testing.T) {
			re, err := CompileDynamic(pattern.Pattern, pattern.Flags)
			if why, rejected := rejectedGolden[key]; rejected {
				if err == nil {
					t.Fatalf("compiled, but listed as rejected: %s", why)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			re.anchored()
			re.full()
			re.advancing()
			if re.Flags() != pattern.Flags {
				t.Errorf("Flags() = %d, Python %d", re.Flags(), pattern.Flags)
			}
		})
	}
}
