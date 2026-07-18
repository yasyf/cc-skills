// Command {{PROJECT_NAME}}d: the {{PROJECT_NAME}} daemon.
package main

import (
	"context"
	"fmt"
	"os"
	"os/signal"
	"syscall"

	"github.com/yasyf/daemonkit/proc"

	"{{MODULE_PATH}}/internal/daemon"
)

func main() {
	// Sweep inherited fds before anything else: a daemonkit contract, since a
	// detached child inherits every non-CLOEXEC descriptor across fork+exec.
	if err := proc.CloseInheritedFDs(); err != nil {
		fmt.Fprintln(os.Stderr, "{{PROJECT_NAME}}d:", err)
		os.Exit(1)
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	if err := daemon.NewRootCmd().ExecuteContext(ctx); err != nil {
		fmt.Fprintln(os.Stderr, "{{PROJECT_NAME}}d:", err)
		os.Exit(1)
	}
}
