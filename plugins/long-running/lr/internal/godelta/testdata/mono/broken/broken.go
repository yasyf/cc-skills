package broken

import "example.com/mono/internal/sandsql/component"

func Params() []component.TaggedParam {
	return nil
}

func Count() int {
	return "three"
}
