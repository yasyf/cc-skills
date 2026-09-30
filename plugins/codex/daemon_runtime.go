package main

import (
	"fmt"

	"github.com/yasyf/cc-interact/daemon"
	"github.com/yasyf/daemonkit"
)

const codexServiceLabel = "com.yasyf.codex-ask"

func appSpec() (daemonkit.Daemon, error) {
	program, err := daemonkit.Stable()
	if err != nil {
		return daemonkit.Daemon{}, fmt.Errorf("build stable daemon program: %w", err)
	}
	return daemon.Spec(daemonkit.Daemon{
		Label:   codexServiceLabel,
		Program: program,
		Args:    []string{"daemon"},
		Log:     appPaths().LogPath(),
		Restart: daemonkit.RestartOnFailure,
		Trust:   appTrust(),
	}), nil
}
