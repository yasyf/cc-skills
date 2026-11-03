package deskhost

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"maps"
	"os"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/orca"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	unwritten   = "unset"
	deployment  = "/ci/desk-host/deployment"
	syncCommand = `commands=["systemctl restart desk-sync.service"]`
	pollEvery   = 5 * time.Second
	syncLimit   = 5 * time.Minute
	stepTimeout = 2 * time.Minute
)

type Keys struct {
	Region string            `json:"region"`
	Keys   map[string]string `json:"keys"`
}

type parameters struct {
	Parameters []struct {
		Name  string `json:"Name"`
		Value string `json:"Value"`
	} `json:"Parameters"`
	InvalidParameters []string `json:"InvalidParameters"`
}

func KeyEnv(ctx context.Context, runner shell.Runner, path string) ([]string, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var keys Keys
	if err := json.Unmarshal(raw, &keys); err != nil {
		return nil, fmt.Errorf("%s: %w", path, err)
	}
	names := slices.Sorted(maps.Values(keys.Keys))
	read, err := runner.Run(ctx, shell.Command{
		Argv:    slices.Concat([]string{"aws", "ssm", "get-parameters", "--region", keys.Region, "--with-decryption", "--output", "json", "--names"}, slices.Compact(names)),
		Timeout: stepTimeout,
	})
	if err != nil {
		return nil, errors.New("the desk's keys did not read by the host's role: " + refusal(err))
	}
	var got parameters
	if err := json.Unmarshal([]byte(read.Stdout), &got); err != nil {
		return nil, errors.New("aws ssm get-parameters printed no JSON")
	}
	if len(got.InvalidParameters) > 0 {
		return nil, fmt.Errorf("no such parameter: %s", strings.Join(got.InvalidParameters, ", "))
	}
	values := make(map[string]string, len(got.Parameters))
	for _, parameter := range got.Parameters {
		if parameter.Value == unwritten {
			return nil, fmt.Errorf("%s still holds its placeholder; the owner writes it", parameter.Name)
		}
		values[parameter.Name] = parameter.Value
	}
	env := make([]string, 0, len(keys.Keys))
	for _, name := range slices.Sorted(maps.Keys(keys.Keys)) {
		env = append(env, name+"="+values[keys.Keys[name]])
	}
	return env, nil
}

func refusal(err error) string {
	var exit *shell.ExitError
	if errors.As(err, &exit) {
		return strings.TrimSpace(exit.Stderr)
	}
	return err.Error()
}

type Deploy struct {
	Runner  shell.Runner
	Out     io.Writer
	Sleep   func(time.Duration)
	Now     func() time.Time
	Config  string
	Host    string
	Profile string
	Region  string
}

func (d Deploy) aws(ctx context.Context, args ...string) (string, error) {
	done, err := d.Runner.Run(ctx, shell.Command{
		Argv:    slices.Concat([]string{"aws", "--profile", d.Profile, "--region", d.Region}, args),
		Timeout: stepTimeout,
	})
	if err != nil {
		return "", fmt.Errorf("aws %s: %s", strings.Join(args[:min(len(args), 2)], " "), refusal(err))
	}
	return strings.TrimSpace(done.Stdout), nil
}

func (d Deploy) Run(ctx context.Context) error {
	plan, err := orca.HostDeployment(ctx, d.Runner, d.Config)
	if err != nil {
		return err
	}
	encoded, err := json.Marshal(plan)
	if err != nil {
		return err
	}
	if _, err := d.aws(ctx, "ssm", "put-parameter", "--name", deployment, "--type", "String", "--overwrite", "--value", string(encoded)); err != nil {
		return err
	}
	command, err := d.aws(ctx, "ssm", "send-command", "--document-name", "AWS-RunShellScript", "--targets", "Key=tag:Name,Values="+d.Host, "--parameters", syncCommand, "--query", "Command.CommandId", "--output", "text")
	if err != nil {
		return err
	}
	if err := d.await(ctx, command); err != nil {
		return err
	}
	_, err = fmt.Fprintf(d.Out, "desk deploy: %s runs %s and %s\n", d.Host, plan.LR.URL, plan.CCI.URL)
	return err
}

func (d Deploy) await(ctx context.Context, command string) error {
	deadline := d.Now().Add(syncLimit)
	for {
		said, err := d.aws(ctx, "ssm", "list-command-invocations", "--command-id", command, "--query", "CommandInvocations[].Status", "--output", "json")
		if err != nil {
			return err
		}
		var statuses []string
		if err := json.Unmarshal([]byte(said), &statuses); err != nil {
			return fmt.Errorf("aws ssm list-command-invocations printed %q", said)
		}
		settled := len(statuses) > 0 && !slices.ContainsFunc(statuses, func(status string) bool {
			return status == "Pending" || status == "InProgress" || status == "Delayed"
		})
		if settled {
			if slices.ContainsFunc(statuses, func(status string) bool { return status != "Success" }) {
				return fmt.Errorf("%s did not sync: %s; journalctl -u desk-sync on the host says why", d.Host, strings.Join(statuses, ", "))
			}
			return nil
		}
		if d.Now().After(deadline) {
			return fmt.Errorf("%s did not answer command %s in %s", d.Host, command, syncLimit)
		}
		d.Sleep(pollEvery)
	}
}
