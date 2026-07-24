package main

import (
	"os"
	"path/filepath"

	"github.com/yasyf/cc-interact/daemon"
	"github.com/yasyf/daemonkit/service"
	"github.com/yasyf/daemonkit/trust"
)

const (
	codexServiceLabel      = "com.yasyf.codex-ask"
	codexSigningTeamID     = "SXKCTF23Q2"
	codexSigningIdentifier = "com.yasyf.codex-ask"
	codexLifecycleRole     = "com.yasyf.codex-ask.lifecycle.v1"
	codexStopControlRole   = "com.yasyf.codex-ask.stop.v1"
)

func appRoles() daemon.Roles {
	return daemon.Roles{
		Business:    trust.UnprotectedRole,
		Lifecycle:   codexLifecycleRole,
		StopControl: codexStopControlRole,
	}
}

func appTrustPolicy() (trust.TrustPolicy, error) {
	roles := appRoles()
	requirement := trust.Requirement{
		TeamID: codexSigningTeamID, SigningIdentifier: codexSigningIdentifier,
	}
	return trust.NewTrustPolicy(trust.TrustPolicyConfig{
		ExpectedUID:      os.Geteuid(),
		AllowUnprotected: true,
		Roles: map[trust.PeerRole]trust.Requirement{
			roles.Lifecycle:   requirement,
			roles.StopControl: requirement,
		},
		StopRoles:      []trust.PeerRole{roles.StopControl},
		ReceiptRoles:   []trust.PeerRole{roles.Lifecycle},
		ReadinessRoles: []trust.PeerRole{roles.Lifecycle},
	})
}

func appAgent() service.Agent {
	executable, err := os.Executable()
	if err != nil {
		panic(err)
	}
	executable, err = filepath.EvalSymlinks(executable)
	if err != nil {
		panic(err)
	}
	return service.Agent{
		Label: codexServiceLabel, Program: executable, Args: []string{"daemon"},
		LogPath: appPaths().LogPath(), RestartPolicy: service.RestartOnFailure,
	}
}
