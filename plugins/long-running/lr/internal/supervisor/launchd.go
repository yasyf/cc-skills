package supervisor

import (
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/launchd"
)

func Label(drive string) string {
	return "com.long-running." + drive + ".supervisor"
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
