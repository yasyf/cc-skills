package pyre

import (
	"encoding/json"
	"maps"
	"os"
	"slices"
	"testing"
)

// Golden is one entry of testdata/patterns.json, as dump_patterns.py writes it.
type Golden struct {
	Pattern string `json:"pattern"`
	Flags   int    `json:"flags"`
}

// ReadGolden reads the patterns.json golden at path, keyed "<file>:<NAME>".
func ReadGolden(path string) (map[string]Golden, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var golden map[string]Golden
	if err := json.Unmarshal(data, &golden); err != nil {
		return nil, err
	}
	return golden, nil
}

// CheckGolden fails t for every registered key missing from the patterns.json golden at path,
// and for every registered source or flags that differs from the golden byte for byte.
func CheckGolden(t testing.TB, path string) {
	t.Helper()
	golden, err := ReadGolden(path)
	if err != nil {
		t.Fatalf("pyre: read golden: %v", err)
	}
	registered := Registered()
	if len(registered) == 0 {
		t.Fatalf("pyre: no patterns registered; import the packages that define them")
	}
	for _, key := range slices.Sorted(maps.Keys(registered)) {
		got := registered[key]
		want, ok := golden[key]
		switch {
		case !ok:
			t.Errorf("%s: not in %s", key, path)
		case got.Source != want.Pattern:
			t.Errorf("%s: source %q, golden %q", key, got.Source, want.Pattern)
		case got.Flags != want.Flags:
			t.Errorf("%s: flags %d, golden %d", key, got.Flags, want.Flags)
		}
	}
}
