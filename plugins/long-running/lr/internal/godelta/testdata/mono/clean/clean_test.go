package clean

import (
	"testing"

	"example.com/mono/internal/sandsql/component"
)

func TestParams(t *testing.T) {
	if got := Params("widget"); len(got) != 1 || got[0] != component.TaggedParamText("widget") {
		t.Fatal(got)
	}
}
