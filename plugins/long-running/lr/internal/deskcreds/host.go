package deskcreds

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/install"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	pushEvery    = 15 * time.Minute
	secretLife   = 3 * pushEvery
	stepTimeout  = 2 * time.Minute
	shmDir       = "/dev/shm"
	servicesUp   = `sprite-env services delete "$1" >/dev/null 2>&1; sprite-env services delete desk-creds >/dev/null 2>&1; sprite-env services create desk-creds --cmd "$2" --args serve --no-stream >/dev/null && timeout 30 sh -c "until curl -so /dev/null http://127.0.0.1:$3/; do sleep 1; done"`
	deskService  = `sprite-env services create "$1" --cmd "$2" --args "run,--config,$3,--desk,$4" --needs desk-creds --no-stream >/dev/null`
	writeFile    = `mkdir -p "$(dirname "$1")" && cat >"$1"`
	redirectExec = `log=$1; shift; exec "$@" >>"$log" 2>&1`
)

var shellSafe = regexp.MustCompile(`^[\w@%+=:,./-]+$`)

type Host struct {
	Sprite      string              `json:"sprite"`
	AWSProfiles []string            `json:"aws_profiles"`
	Secrets     map[string][]string `json:"secrets"`
	Region      string              `json:"region"`
	GitHubApp   map[string]any      `json:"github_app"`
	GHUser      string              `json:"gh_user"`
	TZ          string              `json:"tz"`
	SSM         map[string]string   `json:"ssm"`
	SSMProfile  string              `json:"ssm_profile"`
}

type Runner struct {
	Drive   string `json:"drive"`
	Version string `json:"version"`
	Host    Host   `json:"host"`
}

type Env struct {
	Runner shell.Runner
	Out    io.Writer
	Err    io.Writer
	Now    func() time.Time
	Plugin string
	Home   string
	UID    int
	Path   string
	SHM    string
	Port   int
	Exec   func(path string, argv, env []string) error
}

func merged(base []string, set ...string) []string {
	names := make(map[string]bool, len(set))
	for _, pair := range set {
		name, _, _ := strings.Cut(pair, "=")
		names[name] = true
	}
	out := make([]string, 0, len(base)+len(set))
	for _, pair := range base {
		name, _, _ := strings.Cut(pair, "=")
		if !names[name] {
			out = append(out, pair)
		}
	}
	return append(out, set...)
}

func readRunner(path string) (Runner, map[string]any, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return Runner{}, nil, err
	}
	var runner Runner
	if err := json.Unmarshal(data, &runner); err != nil {
		return Runner{}, nil, fmt.Errorf("%s: %w", path, err)
	}
	var raw map[string]any
	if err := json.Unmarshal(data, &raw); err != nil {
		return Runner{}, nil, fmt.Errorf("%s: %w", path, err)
	}
	return runner, raw, nil
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
	runner, _, err := readRunner(config)
	if err != nil {
		return err
	}
	host, failed := runner.Host, false
	for _, profile := range host.AWSProfiles {
		exported, err := e.Runner.Run(ctx, shell.Command{Argv: []string{"aws", "configure", "export-credentials", "--profile", profile, "--format", "process"}, Timeout: stepTimeout})
		if err != nil {
			_, _ = fmt.Fprintf(e.Err, "desk-creds push: %s did not export: %s\n", profile, tail(err))
			failed = true
			continue
		}
		if err := e.send(ctx, host.Sprite, port, profile, exported.Stdout); err != nil {
			_, _ = fmt.Fprintf(e.Err, "desk-creds push: %s did not reach %s: %s\n", profile, host.Sprite, tail(err))
			failed = true
			continue
		}
		var until struct {
			Expiration string `json:"Expiration"`
		}
		if err := json.Unmarshal([]byte(exported.Stdout), &until); err != nil {
			return fmt.Errorf("aws export-credentials for %s printed no JSON", profile)
		}
		_, _ = fmt.Fprintf(e.Out, "desk-creds push: %s on %s until %s\n", profile, host.Sprite, until.Expiration)
	}
	until := e.Now().Add(secretLife).UTC().Format(time.RFC3339)
	for _, name := range sortedKeys(host.Secrets) {
		printed, err := e.Runner.Run(ctx, shell.Command{Argv: host.Secrets[name], Timeout: stepTimeout})
		value := strings.TrimSpace(printed.Stdout)
		if err != nil || value == "" {
			_, _ = fmt.Fprintf(e.Err, "desk-creds push: %s's command printed nothing, exit %d\n", name, printed.Code)
			failed = true
			continue
		}
		body, err := json.Marshal(map[string]string{"Value": value, expiration: until})
		if err != nil {
			return err
		}
		if err := e.send(ctx, host.Sprite, port, secretPrefix+name, string(body)); err != nil {
			_, _ = fmt.Fprintf(e.Err, "desk-creds push: %s did not reach %s: %s\n", name, host.Sprite, tail(err))
			failed = true
			continue
		}
		_, _ = fmt.Fprintf(e.Out, "desk-creds push: %s on %s until %s\n", name, host.Sprite, until)
	}
	if failed {
		return errors.New("desk-creds push: a credential did not reach the host")
	}
	return nil
}

func (e Env) script(ctx context.Context, sprite, script string, args ...string) error {
	_, err := e.Runner.Run(ctx, shell.Command{Argv: install.SpriteExec(sprite, nil, append([]string{"sh", "-c", script, "sh"}, args...)...), Timeout: stepTimeout})
	return err
}

func (e Env) writeRemote(ctx context.Context, sprite, path string, body []byte) error {
	_, err := e.Runner.Run(ctx, shell.Command{Argv: []string{"sprite", "exec", "-s", sprite, "--", "sh", "-c", writeFile, "sh", path}, Stdin: string(body), Timeout: stepTimeout})
	return err
}

func stateNames(state, config string) ([]string, error) {
	entries, err := os.ReadDir(state)
	if err != nil {
		return nil, err
	}
	names := make([]string, 0, len(entries))
	for _, entry := range entries {
		switch {
		case entry.IsDir() && (entry.Name() == "runner" || entry.Name() == "cards"):
			names = append(names, entry.Name())
		case entry.Type().IsRegular() && filepath.Ext(entry.Name()) != ".log" && filepath.Join(state, entry.Name()) != config:
			names = append(names, entry.Name())
		}
	}
	return names, nil
}

func (e Env) Install(ctx context.Context, config, desk string) error {
	runner, raw, err := readRunner(config)
	if err != nil {
		return err
	}
	sprite, version := runner.Host.Sprite, runner.Version
	scratch, err := os.MkdirTemp("", "lr-desk-install")
	if err != nil {
		return err
	}
	defer func() { _ = os.RemoveAll(scratch) }()
	if err := install.CopyTree(ctx, e.Runner, sprite, scratch, filepath.Dir(e.Plugin), install.SpritePlugins, version); err != nil {
		return err
	}
	registryName := runner.Drive + ".json"
	registry, err := os.ReadFile(filepath.Join(e.Home, ".claude", "long-running", "drives", registryName))
	if err != nil {
		return err
	}
	if err := e.writeRemote(ctx, sprite, install.SpriteHome+"/.claude/long-running/drives/"+registryName, registry); err != nil {
		return err
	}
	if _, err := e.Runner.Run(ctx, shell.Command{Argv: install.SpriteExec(sprite, nil, "test", "-f", config), Timeout: stepTimeout}); err != nil {
		state := filepath.Dir(config)
		names, err := stateNames(state, config)
		if err != nil {
			return err
		}
		if err := install.CopyTree(ctx, e.Runner, sprite, scratch, state, state, names...); err != nil {
			return err
		}
	}
	onHost, err := install.HostConfig(raw)
	if err != nil {
		return err
	}
	encoded, err := install.EncodedConfig(onHost)
	if err != nil {
		return err
	}
	if err := e.writeRemote(ctx, sprite, config, encoded); err != nil {
		return err
	}
	service, entry := "desk-"+desk, install.SpritePlugins+"/"+version+"/bin/desk-creds.py"
	if err := e.script(ctx, sprite, servicesUp, service, entry, strconv.Itoa(DefaultPort)); err != nil {
		return err
	}
	if err := e.keepPushing(ctx, runner.Drive, config); err != nil {
		return err
	}
	pushed := e.Push(ctx, config, DefaultPort)
	if err := e.script(ctx, sprite, deskService, service, entry, config, desk); err != nil {
		return err
	}
	waiting := ""
	if pushed != nil {
		waiting = ", waiting on the next push"
	}
	_, err = fmt.Fprintf(e.Out, "desk-creds install: the %s desk runs on %s as Sprite service %s on %s%s\n", desk, sprite, service, version, waiting)
	return errors.Join(err, pushed)
}

func xmlEscaped(text string) string {
	return strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;").Replace(text)
}

func plist(label, tool, config, path, log string) []byte {
	var out bytes.Buffer
	out.WriteString(`<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
`)
	fmt.Fprintf(&out, "\t<key>EnvironmentVariables</key>\n\t<dict>\n\t\t<key>PATH</key>\n\t\t<string>%s</string>\n\t</dict>\n", xmlEscaped(path))
	fmt.Fprintf(&out, "\t<key>Label</key>\n\t<string>%s</string>\n\t<key>ProgramArguments</key>\n\t<array>\n", xmlEscaped(label))
	for _, word := range []string{tool, "push", "--config", config} {
		fmt.Fprintf(&out, "\t\t<string>%s</string>\n", xmlEscaped(word))
	}
	fmt.Fprintf(&out, "\t</array>\n\t<key>RunAtLoad</key>\n\t<true/>\n\t<key>StandardErrorPath</key>\n\t<string>%s</string>\n\t<key>StandardOutPath</key>\n\t<string>%s</string>\n\t<key>StartInterval</key>\n\t<integer>%d</integer>\n</dict>\n</plist>\n", xmlEscaped(log), xmlEscaped(log), int(pushEvery.Seconds()))
	return out.Bytes()
}

func (e Env) keepPushing(ctx context.Context, drive, config string) error {
	label := "com.long-running." + drive + ".desk-creds"
	path := filepath.Join(e.Home, "Library", "LaunchAgents", label+".plist")
	log := filepath.Join(e.Home, ".claude", "long-running", label+".log")
	wanted := plist(label, filepath.Join(e.Plugin, "bin", "desk-creds.py"), config, e.Path, log)
	target := fmt.Sprintf("gui/%d/%s", e.UID, label)
	_, printed := e.Runner.Run(ctx, shell.Command{Argv: []string{"launchctl", "print", target}, Timeout: stepTimeout})
	loaded := printed == nil
	current, err := os.ReadFile(path)
	if loaded && err == nil && bytes.Equal(current, wanted) {
		return nil
	}
	if loaded {
		if _, err := e.Runner.Run(ctx, shell.Command{Argv: []string{"launchctl", "bootout", target}, Timeout: stepTimeout}); err != nil {
			return err
		}
	}
	if err := writeBeside(path, string(wanted)); err != nil {
		return err
	}
	_, err = e.Runner.Run(ctx, shell.Command{Argv: []string{"launchctl", "bootstrap", fmt.Sprintf("gui/%d", e.UID), path}, Timeout: stepTimeout})
	return err
}

func shellQuote(word string) string {
	if shellSafe.MatchString(word) {
		return word
	}
	return "'" + strings.ReplaceAll(word, "'", `'"'"'`) + "'"
}

func (e Env) writeProfiles(host Host) error {
	var aws strings.Builder
	shim := shellQuote(filepath.Join(e.Plugin, "bin", "desk-creds.py"))
	for _, profile := range host.AWSProfiles {
		fmt.Fprintf(&aws, "[profile %s]\ncredential_process = %s get %s\nregion = %s\n\n", profile, shim, profile, host.Region)
	}
	if err := writeBeside(filepath.Join(e.Home, ".aws", "config"), aws.String()); err != nil {
		return err
	}
	var ccx strings.Builder
	for _, key := range sortedKeys(host.GitHubApp) {
		value, err := json.Marshal(host.GitHubApp[key])
		if err != nil {
			return err
		}
		fmt.Fprintf(&ccx, "%s = %s\n", key, value)
	}
	return writeBeside(filepath.Join(e.Home, ".config", "ccx", "github-app.toml"), ccx.String())
}

func writeBeside(path, text string) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		return err
	}
	return os.WriteFile(path, []byte(text), 0o600)
}

func (e Env) Run(ctx context.Context, config, desk string, preflight bool) error {
	runner, _, err := readRunner(config)
	if err != nil {
		return err
	}
	host := runner.Host
	if err := e.writeProfiles(host); err != nil {
		return err
	}
	set := []string{"HOME=" + e.Home, fmt.Sprintf("PATH=%s/.local/bin:%s/.bun/bin:/.sprite/bin:%s", e.Home, e.Home, e.Path)}
	token := ""
	for _, name := range sortedKeys(host.Secrets) {
		held, err := Fetch(ctx, secretPrefix+name, e.Port)
		if err != nil {
			return fmt.Errorf("the pushed secret %s did not read: %w", name, err)
		}
		var value string
		if err := json.Unmarshal(held["Value"], &value); err != nil {
			return fmt.Errorf("the pushed secret %s is no string", name)
		}
		if name == userToken {
			token = value
			continue
		}
		set = append(set, name+"="+value)
	}
	if token == "" {
		return fmt.Errorf("host.secrets in %s names no %s, the token gh writes with", config, userToken)
	}
	for _, name := range sortedKeys(host.SSM) {
		read, err := e.Runner.Run(ctx, shell.Command{Argv: []string{"aws", "ssm", "get-parameter", "--profile", host.SSMProfile, "--region", host.Region, "--name", host.SSM[name], "--with-decryption", "--query", "Parameter.Value", "--output", "text"}, Timeout: stepTimeout})
		if err != nil {
			return fmt.Errorf("the SSM parameter for %s did not read: %s", name, tail(err))
		}
		set = append(set, name+"="+strings.TrimSpace(read.Stdout))
	}
	gh := filepath.Join(e.SHM, runner.Drive+"-gh")
	if err := os.MkdirAll(gh, 0o700); err != nil {
		return err
	}
	hosts := fmt.Sprintf("github.com:\n    users:\n        %s:\n            oauth_token: %s\n    git_protocol: https\n    oauth_token: %s\n    user: %s\n", host.GHUser, token, token, host.GHUser)
	if err := os.WriteFile(filepath.Join(gh, "hosts.yml"), []byte(hosts), 0o600); err != nil {
		return err
	}
	env := merged(os.Environ(), append(set, "GH_CONFIG_DIR="+gh, "TZ="+host.TZ, "CCX_CALLER=desk-runner")...)
	tool := filepath.Join(e.Plugin, "bin", "desk-runner.py")
	if preflight {
		return e.Exec(tool, []string{tool, "preflight", "--config", config, "--desk", desk, "--json"}, env)
	}
	log := filepath.Join(filepath.Dir(config), desk+"-runner.log")
	file, err := os.OpenFile(log, os.O_WRONLY|os.O_CREATE|os.O_APPEND, 0o600)
	if err != nil {
		return err
	}
	if err := file.Close(); err != nil {
		return err
	}
	return e.Exec("/bin/sh", []string{"sh", "-c", redirectExec, "sh", log, tool, "run", "--config", config, "--desk", desk}, env)
}
