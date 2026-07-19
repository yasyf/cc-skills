// Command {{PROJECT_NAME}}d: the {{PROJECT_NAME}} daemon.
package main

import (
	"context"
	"fmt"
	"os"

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

	if err := daemon.NewRootCmd().ExecuteContext(context.Background()); err != nil {
		fmt.Fprintln(os.Stderr, "{{PROJECT_NAME}}d:", err)
		os.Exit(1)
	}
}
