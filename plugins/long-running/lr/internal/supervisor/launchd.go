package supervisor

import (
	"path/filepath"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/deskcreds"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/launchd"
)

func Label(drive string) string {
	return "com.long-running." + drive + ".supervisor"
}

func RelayLabel(drive string) string {
	return "com.long-running." + drive + ".relay"
}

type Agent struct {
	Drive  string
	Config string
	Binary string
	Path   string
	Log    string
}

func (a Agent) Job() launchd.Job {
	return launchd.Job{
		Label:     Label(a.Drive),
		Env:       [][2]string{{"CLAUDE_LONG_RUNNING_DRIVE", a.Drive}, {lanes.LaneEnv, lanes.Self}, {"PATH", a.Path}},
		Arguments: []string{a.Binary, "supervisor", "run", "--config", a.Config},
		Log:       a.Log,
	}
}

func (a Agent) RelayJob(inbox string) launchd.Job {
	return launchd.Job{
		Label:     RelayLabel(a.Drive),
		Env:       [][2]string{{"CLAUDE_LONG_RUNNING_DRIVE", a.Drive}, {lanes.LaneEnv, relayLane}, {"PATH", a.Path}},
		Arguments: []string{a.Binary, "relay", "run", "--config", a.Config},
		Log:       relayLog(inbox),
	}
}

func (a Agent) DeskCredsJob(runner string) launchd.Job {
	return launchd.Job{
		Label:     deskcreds.Label(a.Drive),
		Env:       [][2]string{{lanes.LaneEnv, deskcreds.PushLane}, {"PATH", a.Path}},
		Arguments: []string{a.Binary, "desk-creds", "keep", "--config", runner},
		Log:       filepath.Join(filepath.Dir(runner), "desk-creds.log"),
	}
}
