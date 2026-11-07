package deskcreds

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	pushEvery   = 15 * time.Minute
	secretLife  = 3 * pushEvery
	stepTimeout = 2 * time.Minute
)

type Host struct {
	Sprite      string              `json:"sprite"`
	AWSProfiles []string            `json:"aws_profiles"`
	Secrets     map[string][]string `json:"secrets"`
}

type Runner struct {
	Drive string `json:"drive"`
	Host  Host   `json:"host"`
}

type Env struct {
	Runner shell.Runner
	Out    io.Writer
	Err    io.Writer
	Now    func() time.Time
}

func readRunner(path string) (Runner, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return Runner{}, err
	}
	var runner Runner
	if err := json.Unmarshal(data, &runner); err != nil {
		return Runner{}, fmt.Errorf("%s: %w", path, err)
	}
	return runner, nil
}

func tail(err error) string {
	said := err.Error()
	var exit *shell.ExitError
	if errors.As(err, &exit) {
		said = exit.Stderr
	}
	said = strings.TrimSpace(said)
	return said[max(0, len(said)-stderrLimit):]
}

func (e Env) send(ctx context.Context, sprite string, port int, key, body string) error {
	_, err := e.Runner.Run(ctx, shell.Command{
		Argv:    []string{"sprite", "exec", "-s", sprite, "--", "curl", "-sS", "-f", "-X", "PUT", "-H", "Content-Type: application/json", "--data-binary", "@-", fmt.Sprintf("http://127.0.0.1:%d/%s", port, key)},
		Stdin:   body,
		Timeout: stepTimeout,
	})
	return err
}

func (e Env) Push(ctx context.Context, config string, port int) error {
	runner, err := readRunner(config)
	if err != nil {
		return err
	}
	_, err = e.push(ctx, runner.Host, port)
	return err
}

func (e Env) push(ctx context.Context, host Host, port int) (bool, error) {
	reached, unheld, failed := 0, []string{}, []string{}
	for _, profile := range host.AWSProfiles {
		exported, err := e.Runner.Run(ctx, shell.Command{Argv: []string{"aws", "configure", "export-credentials", "--profile", profile, "--format", "process"}, Timeout: stepTimeout})
		if err != nil {
			_, _ = fmt.Fprintf(e.Err, "desk-creds push: %s did not export: %s\n", profile, tail(err))
			unheld = append(unheld, profile)
			continue
		}
		if err := e.send(ctx, host.Sprite, port, profile, exported.Stdout); err != nil {
			_, _ = fmt.Fprintf(e.Err, "desk-creds push: %s did not reach %s: %s\n", profile, host.Sprite, tail(err))
			failed = append(failed, profile)
			continue
		}
		var until struct {
			Expiration string `json:"Expiration"`
		}
		if err := json.Unmarshal([]byte(exported.Stdout), &until); err != nil {
			return false, fmt.Errorf("aws export-credentials for %s printed no JSON", profile)
		}
		reached++
		_, _ = fmt.Fprintf(e.Out, "desk-creds push: %s on %s until %s\n", profile, host.Sprite, until.Expiration)
	}
	until := e.Now().Add(secretLife).UTC().Format(time.RFC3339)
	for _, name := range sortedKeys(host.Secrets) {
		printed, err := e.Runner.Run(ctx, shell.Command{Argv: host.Secrets[name], Timeout: stepTimeout})
		value := strings.TrimSpace(printed.Stdout)
		if err != nil || value == "" {
			_, _ = fmt.Fprintf(e.Err, "desk-creds push: %s's command printed nothing, exit %d\n", name, printed.Code)
			unheld = append(unheld, name)
			continue
		}
		body, err := json.Marshal(map[string]string{"Value": value, expiration: until})
		if err != nil {
			return false, err
		}
		if err := e.send(ctx, host.Sprite, port, secretPrefix+name, string(body)); err != nil {
			_, _ = fmt.Fprintf(e.Err, "desk-creds push: %s did not reach %s: %s\n", name, host.Sprite, tail(err))
			failed = append(failed, name)
			continue
		}
		reached++
		_, _ = fmt.Fprintf(e.Out, "desk-creds push: %s on %s until %s\n", name, host.Sprite, until)
	}
	var faults []string
	if len(unheld) > 0 {
		faults = append(faults, "this host holds no "+strings.Join(unheld, ", "))
	}
	if len(failed) > 0 {
		faults = append(faults, strings.Join(failed, ", ")+" did not reach it")
	}
	if len(faults) > 0 {
		return reached == 0 && len(failed) > 0, fmt.Errorf("desk-creds push to %s: %s", host.Sprite, strings.Join(faults, "; "))
	}
	return false, nil
}
