package landing

import (
	"context"
	"errors"
	"fmt"
)

var ErrHeld = errors.New("an open hold names it")

type HoldGuard struct {
	Queue
	Held func(ctx context.Context) (map[int]bool, error)
}

func (g HoldGuard) Enqueue(ctx context.Context, number int, head string) (string, error) {
	if err := g.refuse(ctx, number); err != nil {
		return "", err
	}
	return g.Queue.Enqueue(ctx, number, head)
}

func (g HoldGuard) Reenter(ctx context.Context, number int) (string, error) {
	if err := g.refuse(ctx, number); err != nil {
		return "", err
	}
	return g.Queue.Reenter(ctx, number)
}

func (g HoldGuard) refuse(ctx context.Context, number int) error {
	held, err := g.Held(ctx)
	if err != nil {
		return fmt.Errorf("#%d: read the open holds: %w", number, err)
	}
	if held[number] {
		return fmt.Errorf("#%d: %w", number, ErrHeld)
	}
	return nil
}
