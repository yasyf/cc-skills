package main

import (
	"fmt"

	"github.com/yasyf/cc-interact/daemon"
	"github.com/yasyf/daemonkit"
)

const (
	codexServiceLabel      = "com.yasyf.codex-ask"
	codexSigningTeamID     = "SXKCTF23Q2"
	codexSigningIdentifier = "com.yasyf.codex-ask"
)

func appSpec() (daemonkit.Daemon, error) {
	program, err := daemonkit.Stable()
	if err != nil {
		return daemonkit.Daemon{}, fmt.Errorf("build stable daemon program: %w", err)
	}
	requirement := daemonkit.Requirement{TeamID: codexSigningTeamID, SigningIdentifier: codexSigningIdentifier}
	return daemon.Spec(daemonkit.Daemon{
		Label:   codexServiceLabel,
		Program: program,
		Args:    []string{"daemon"},
		Log:     appPaths().LogPath(),
		Restart: daemonkit.RestartOnFailure,
		Trust: daemonkit.Trust{
			Control: &requirement,
			Serving: daemonkit.ServingSameUser(),
		},
	}), nil
}
