package clean

import "example.com/mono/internal/sandsql/component"

func Params(name string) []component.TaggedParam {
	return []component.TaggedParam{component.TaggedParamText(name)}
}

func Columns() (int, error) {
	client, err := component.Open()
	if err != nil {
		return 0, err
	}
	return len(client.Layout().Columns), nil
}

type Route struct {
	component.PresignedGrant
	URL string
}

func Token(route Route) string {
	return route.Token
}
