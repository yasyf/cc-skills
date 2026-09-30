package main

import (
	"github.com/spf13/cobra"
	"github.com/yasyf/daemonkit"
)

const (
	codexSigningTeamID     = "SXKCTF23Q2"
	codexSigningIdentifier = "com.yasyf.codex-ask"
)

func appTrust() daemonkit.Trust {
	requirement := daemonkit.Requirement{TeamID: codexSigningTeamID, SigningIdentifier: codexSigningIdentifier}
	return daemonkit.Trust{
		Control: &requirement,
		Serving: daemonkit.ServingSameUser(),
	}
}

func platformCmds() []*cobra.Command { return nil }
