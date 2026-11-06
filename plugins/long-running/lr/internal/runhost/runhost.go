package runhost

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/launchd"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	Unit         = "orca-serve"
	readyEvent   = "orca_server_ready"
	forwardTries = 30
	readTries    = 60
	orcaTimeout  = time.Minute
	awsTimeout   = 2 * time.Minute
)

type Settings struct {
	Host        string
	Profile     string
	Region      string
	Environment string
	Port        int
}

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

func Label(drive string) string {
	return "com.long-running." + drive + ".run-host"
}

func Dial(address string) error {
	conn, err := net.DialTimeout("tcp", address, time.Second)
	if err != nil {
		return err
	}
	return conn.Close()
}

func (h Host) Install(ctx context.Context, s Settings, drive, binary, log string) error {
	up, err := h.ready(ctx, s)
	if err != nil {
		return err
	}
	fmt.Fprintf(h.Out, "lr run-host: %s serves Orca %s on port %d as unit %s\n", s.Host, up.Health.BuildVersion, s.Port, Unit)
	port := strconv.Itoa(s.Port)
	job := launchd.Job{
		Label:     Label(drive),
		Arguments: []string{binary, "run-host", "forward", "--host", s.Host, "--profile", s.Profile, "--region", s.Region, "--port", port},
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
	_, err = fmt.Fprintf(h.Out, "lr run-host: the Mac %s it as Orca environment %s through an SSM port forward on 127.0.0.1:%d\n", said, s.Environment, s.Port)
	return err
}

func ForwardArgv(ctx context.Context, runner shell.Runner, s Settings) ([]string, error) {
	listed, err := aws(ctx, runner, s, "ec2", "describe-instances", "--filters", "Name=tag:Name,Values="+s.Host, "Name=instance-state-name,Values=running", "--query", "Reservations[].Instances[].InstanceId", "--output", "text")
	if err != nil {
		return nil, err
	}
	ids := strings.Fields(listed)
	if len(ids) != 1 {
		return nil, fmt.Errorf("%d running instances are named %s, want exactly one", len(ids), s.Host)
	}
	return []string{
		"aws", "--profile", s.Profile, "--region", s.Region, "ssm", "start-session", "--target", ids[0], "--document-name", "AWS-StartPortForwardingSession",
		"--parameters", fmt.Sprintf(`{"portNumber":["%d"],"localPortNumber":["%d"]}`, s.Port, s.Port),
	}, nil
}

func (h Host) ready(ctx context.Context, s Settings) (ready, error) {
	script := fmt.Sprintf(`{"commands":["journalctl -u %s -o cat --no-pager | grep -F %s | tail -n 1"]}`, Unit, readyEvent)
	command, err := aws(ctx, h.Runner, s, "ssm", "send-command", "--document-name", "AWS-RunShellScript", "--targets", "Key=tag:Name,Values="+s.Host, "--parameters", script, "--query", "Command.CommandId", "--output", "text")
	if err != nil {
		return ready{}, err
	}
	for range readTries {
		said, err := aws(ctx, h.Runner, s, "ssm", "list-command-invocations", "--command-id", strings.TrimSpace(command), "--details", "--query", "CommandInvocations[0].{status:Status,output:CommandPlugins[0].Output}", "--output", "json")
		if err != nil {
			return ready{}, err
		}
		var invocation struct {
			Status string `json:"status"`
			Output string `json:"output"`
		}
		if err := json.Unmarshal([]byte(said), &invocation); err != nil {
			return ready{}, fmt.Errorf("aws ssm list-command-invocations printed %q", said)
		}
		switch {
		case invocation.Status == "Success":
			return readyOf(invocation.Output, s.Port)
		case slices.Contains([]string{"Failed", "Cancelled", "TimedOut"}, invocation.Status):
			return ready{}, fmt.Errorf("reading %s's Orca server ended %s: %s", s.Host, invocation.Status, strings.TrimSpace(invocation.Output))
		}
		h.Sleep(time.Second)
	}
	return ready{}, fmt.Errorf("reading %s's Orca server did not finish within %d s", s.Host, readTries)
}

func readyOf(printed string, port int) (ready, error) {
	var up ready
	if err := json.Unmarshal([]byte(strings.TrimSpace(printed)), &up); err != nil || up.Type != readyEvent {
		return ready{}, fmt.Errorf("unit %s on the host has logged no %s event; start it, then run this again", Unit, readyEvent)
	}
	switch {
	case up.Health.BuildVersion == "":
		return ready{}, fmt.Errorf("the server fell back to Electron instead of Orca's managed server; its reason is in journalctl -u %s on the host", Unit)
	case !strings.HasSuffix(up.BoundEndpoint, ":"+strconv.Itoa(port)):
		return ready{}, fmt.Errorf("the server bound %s, not port %d", up.BoundEndpoint, port)
	case !up.Pairing.Available || up.Pairing.URL == "":
		return ready{}, errors.New("the server offers no pairing")
	}
	return up, nil
}

func (h Host) forwarded(address, log string) error {
	for range forwardTries {
		if h.Dial(address) == nil {
			return nil
		}
		h.Sleep(time.Second)
	}
	return fmt.Errorf("the SSM port forward opened no %s within %d s; read %s", address, forwardTries, log)
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

func aws(ctx context.Context, runner shell.Runner, s Settings, args ...string) (string, error) {
	done, err := runner.Run(ctx, shell.Command{Argv: slices.Concat([]string{"aws", "--profile", s.Profile, "--region", s.Region}, args), Timeout: awsTimeout})
	if err != nil {
		var exit *shell.ExitError
		if errors.As(err, &exit) {
			return "", fmt.Errorf("aws %s: exit %d: %s", strings.Join(args[:min(len(args), 2)], " "), exit.Code, strings.TrimSpace(exit.Stderr))
		}
		return "", err
	}
	return done.Stdout, nil
}
