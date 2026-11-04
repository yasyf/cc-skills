package incident

import (
	"context"

	"github.com/yasyf/cc-inbox/client"
)

type Bus interface {
	Post(ctx context.Context, d client.Draft) (client.Record, error)
	Tail(ctx context.Context, q client.Query) ([]client.Record, error)
}

type CCI struct{}

func (CCI) Post(ctx context.Context, d client.Draft) (client.Record, error) {
	return client.Post(ctx, d)
}

func (CCI) Tail(ctx context.Context, q client.Query) ([]client.Record, error) {
	return client.Tail(ctx, q)
}
