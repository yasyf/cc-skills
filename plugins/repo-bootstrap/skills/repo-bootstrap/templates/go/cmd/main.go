// Command {{PROJECT_NAME}}: {{DESCRIPTION}}
package main

import (
	"context"
	"fmt"
	"os"
	"os/signal"
	"syscall"

	"{{MODULE_PATH}}/internal/cli"
	applog "{{MODULE_PATH}}/internal/log"
)

func main() {
	applog.Setup()

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	if err := cli.NewRootCmd().ExecuteContext(ctx); err != nil {
		// Minimal error handling: report on stderr and exit non-zero. As the CLI
		// grows, map typed errors to exit codes here (see STYLEGUIDE.md § Error Handling).
		fmt.Fprintln(os.Stderr, "{{PROJECT_NAME}}:", err)
		os.Exit(1)
	}
}
