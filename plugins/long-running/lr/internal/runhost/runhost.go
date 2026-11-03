package runhost

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/install"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/launchd"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	Service      = "orca-run-host"
	State        = install.SpriteHome + "/.local/state/long-running/run-host"
	serveSeconds = 180
	forwardTries = 30
	orcaTimeout  = time.Minute
	readyEvent   = "orca_server_ready"
	serveUp      = `dir=$1 port=$2 wait=$3 service=$4
others=$(pgrep -af '[ ]serve --port ' | grep -v -- " serve --port $port " || true)
if [ -n "$others" ]; then printf 'another Orca server runs on this host, and two would share its state:\n%s\n' "$others" >&2; exit 3; fi
mkdir -p "$dir"
cat >"$dir/serve.sh" <<'EOF'
#!/bin/sh
set -eu
umask 077
export PATH="$HOME/.config/orca/cli/bin:$HOME/.local/bin:$PATH"
cd "$(dirname "$0")"
exec orca serve --port "$1" --pairing-address 127.0.0.1 --json >serve.json 2>>serve.log
EOF
chmod 700 "$dir/serve.sh"
sprite-env services list | grep -q "\"name\":\"$service\"" || { rm -f "$dir/serve.json"; sprite-env services create "$service" --cmd "$dir/serve.sh" --args "$port" --no-stream >/dev/null; }
deadline=$(($(date +%s) + wait))
until grep -q '"type":"orca_server_ready"' "$dir/serve.json" 2>/dev/null; do
  if [ "$(date +%s)" -ge "$deadline" ]; then tail -n 5 "$dir/serve.log" >&2; exit 4; fi
  sleep 2
done
grep '"type":"orca_server_ready"' "$dir/serve.json" | tail -n 1`
)

type Settings struct {
	Sprite      string
	Port        int
	Environment string
}

func Load(runner string) (Settings, error) {
	data, err := os.ReadFile(runner)
	if err != nil {
		return Settings{}, err
	}
	var file struct {
		Host struct {
			Sprite string `json:"sprite"`
			Server struct {
				Port        int    `json:"port"`
				Environment string `json:"environment"`
			} `json:"orca_server"`
		} `json:"host"`
	}
	if err := json.Unmarshal(data, &file); err != nil {
		return Settings{}, fmt.Errorf("%s: %w", runner, err)
	}
	host := file.Host
	switch {
	case host.Sprite == "":
		return Settings{}, fmt.Errorf("%s names no host.sprite, the desk host that serves the Run", runner)
	case host.Server.Port <= 0 || host.Server.Environment == "":
		return Settings{}, fmt.Errorf("%s names no host.orca_server port and environment", runner)
	}
	return Settings{Sprite: host.Sprite, Port: host.Server.Port, Environment: host.Server.Environment}, nil
}

func Label(drive string) string { return "com.long-running." + drive + ".run-host" }

type ready struct {
	Type          string `json:"type"`
	BoundEndpoint string `json:"boundEndpoint"`
	Pairing       struct {
		Available bool   `json:"available"`
		URL       string `json:"url"`
	} `json:"pairing"`
	Health struct {
		BuildVersion string `json:"buildVersion"`
	} `json:"health"`
}

func readyOf(printed string, port int) (ready, error) {
	var up ready
	if err := json.Unmarshal([]byte(strings.TrimSpace(printed)), &up); err != nil || up.Type != readyEvent {
		return ready{}, fmt.Errorf("the host printed no %s event", readyEvent)
	}
	switch {
	case up.Health.BuildVersion == "":
		return ready{}, fmt.Errorf("the server fell back to Electron instead of Orca's managed server; its reason is the [serve] line in %s/serve.log on the host", State)
	case !strings.HasSuffix(up.BoundEndpoint, ":"+strconv.Itoa(port)):
		return ready{}, fmt.Errorf("the server bound %s, not port %d", up.BoundEndpoint, port)
	case !up.Pairing.Available || up.Pairing.URL == "":
		return ready{}, errors.New("the server offers no pairing")
	}
	return up, nil
}

type Agents interface {
	Install(ctx context.Context, job launchd.Job) error
}

type Host struct {
	Runner shell.Runner
	Agents Agents
	Dial   func(address string) error
	Sleep  func(time.Duration)
	Out    io.Writer
}

func Dial(address string) error {
	conn, err := net.DialTimeout("tcp", address, time.Second)
	if err != nil {
		return err
	}
	return conn.Close()
}

func stderrOf(err error) string {
	var exit *shell.ExitError
	if errors.As(err, &exit) {
		return fmt.Sprintf("exit %d: %s", exit.Code, strings.TrimSpace(exit.Stderr))
	}
	return err.Error()
}

func (h Host) Install(ctx context.Context, s Settings, drive, log, path string) error {
	port := strconv.Itoa(s.Port)
	served, err := h.Runner.Run(ctx, shell.Command{
		Argv:    install.SpriteExec(s.Sprite, nil, "sh", "-c", serveUp, "sh", State, port, strconv.Itoa(serveSeconds), Service),
		Timeout: (serveSeconds + 60) * time.Second,
	})
	if err != nil {
		return fmt.Errorf("the Orca server on %s did not come up: %s", s.Sprite, stderrOf(err))
	}
	up, err := readyOf(served.Stdout, s.Port)
	if err != nil {
		return fmt.Errorf("%s: %w", s.Sprite, err)
	}
	fmt.Fprintf(h.Out, "lr run-host: %s serves Orca %s on port %d as Sprite service %s\n", s.Sprite, up.Health.BuildVersion, s.Port, Service)
	job := launchd.Job{
		Label:     Label(drive),
		Env:       [][2]string{{"PATH", path}},
		Arguments: []string{"/usr/bin/env", "sprite", "proxy", "-s", s.Sprite, port + ":" + port},
		Log:       log,
	}
	if err := h.Agents.Install(ctx, job); err != nil {
		return err
	}
	if err := h.forwarded(net.JoinHostPort("127.0.0.1", port), log); err != nil {
		return err
	}
	added, err := h.pair(ctx, s.Environment, up.Pairing.URL)
	if err != nil {
		return err
	}
	said := "already reaches"
	if added {
		said = "now reaches"
	}
	_, err = fmt.Fprintf(h.Out, "lr run-host: the Mac %s it as Orca environment %s through sprite proxy on 127.0.0.1:%d\n", said, s.Environment, s.Port)
	return err
}

func (h Host) forwarded(address, log string) error {
	for range forwardTries {
		if h.Dial(address) == nil {
			return nil
		}
		h.Sleep(time.Second)
	}
	return fmt.Errorf("sprite proxy opened no %s within %d s; read %s", address, forwardTries, log)
}

func (h Host) pair(ctx context.Context, environment, code string) (bool, error) {
	if _, err := h.Runner.Run(ctx, shell.Command{Argv: []string{"orca", "environment", "show", "--environment", environment, "--json"}, Timeout: orcaTimeout}); err == nil {
		return false, nil
	}
	added, err := h.Runner.Run(ctx, shell.Command{Argv: []string{"orca", "environment", "add", "--name", environment, "--pairing-code", code, "--json"}, Timeout: orcaTimeout})
	if err == nil {
		return true, nil
	}
	var refused struct {
		Error struct {
			Code    string `json:"code"`
			Message string `json:"message"`
		} `json:"error"`
	}
	_ = json.Unmarshal([]byte(added.Stdout), &refused)
	return false, fmt.Errorf("orca environment add --name %s refused, exit %d: %s: %s", environment, added.Code, refused.Error.Code, refused.Error.Message)
}
