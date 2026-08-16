package main

import (
	"github.com/yasyf/cc-interact/daemon"
	"github.com/yasyf/daemonkit"
)

const (
	codexServiceLabel      = "com.yasyf.codex-ask"
	codexSigningTeamID     = "SXKCTF23Q2"
	codexSigningIdentifier = "com.yasyf.codex-ask"
)

// appSpec is the one daemonkit identity the launcher and the daemon share.
// Stable's ~/.daemonkit/bin/<Label> survives the cask upgrade that strips a
// versioned Caskroom path out from under a resolved Program. The serving
// posture is the same-user waiver, because a dev build is unsigned.
func appSpec() (daemonkit.Daemon, error) {
	program, err := daemonkit.Stable()
	if err != nil {
		return daemonkit.Daemon{}, err
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
