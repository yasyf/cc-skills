package main

import (
	"context"
	"errors"
	"fmt"
	"os"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/orcarpc"
)

const unsupportedExit = 64

func main() {
	reply, err := orcarpc.New().Read(context.Background(), os.Args[1:])
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		if errors.Is(err, orcarpc.ErrUnsupported) {
			os.Exit(unsupportedExit)
		}
		os.Exit(1)
	}
	out, err := orcarpc.Render(reply)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	_, _ = os.Stdout.Write(out)
	if !reply.OK {
		os.Exit(1)
	}
}
