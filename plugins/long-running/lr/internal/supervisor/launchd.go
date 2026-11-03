package supervisor

import (
	"bytes"
	"context"
	"encoding/xml"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	throttleSeconds = 60
	openFiles       = 65536
	bootoutWait     = 60
	launchctlWait   = time.Minute
)

func Label(drive string) string {
	return "com.long-running." + drive + ".supervisor"
}

func escaped(text string) string {
	var out bytes.Buffer
	if err := xml.EscapeText(&out, []byte(text)); err != nil {
		panic(err)
	}
	return out.String()
}

type Agent struct {
	Drive  string
	Config string
	Binary string
	Path   string
	Log    string
}

func (a Agent) Plist() []byte {
	var out strings.Builder
	line := func(format string, args ...any) { fmt.Fprintf(&out, format+"\n", args...) }
	line(`<?xml version="1.0" encoding="UTF-8"?>`)
	line(`<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">`)
	line(`<plist version="1.0">`)
	line(`<dict>`)
	line(`	<key>EnvironmentVariables</key>`)
	line(`	<dict>`)
	line(`		<key>CLAUDE_LONG_RUNNING_DRIVE</key>`)
	line(`		<string>%s</string>`, escaped(a.Drive))
	line(`		<key>%s</key>`, lanes.LaneEnv)
	line(`		<string>%s</string>`, lanes.Self)
	line(`		<key>PATH</key>`)
	line(`		<string>%s</string>`, escaped(a.Path))
	line(`	</dict>`)
	line(`	<key>KeepAlive</key>`)
	line(`	<true/>`)
	line(`	<key>Label</key>`)
	line(`	<string>%s</string>`, escaped(Label(a.Drive)))
	line(`	<key>ProgramArguments</key>`)
	line(`	<array>`)
	for _, word := range []string{a.Binary, "supervisor", "run", "--config", a.Config} {
		line(`		<string>%s</string>`, escaped(word))
	}
	line(`	</array>`)
	line(`	<key>SoftResourceLimits</key>`)
	line(`	<dict>`)
	line(`		<key>NumberOfFiles</key>`)
	line(`		<integer>%d</integer>`, openFiles)
	line(`	</dict>`)
	line(`	<key>StandardErrorPath</key>`)
	line(`	<string>%s</string>`, escaped(a.Log))
	line(`	<key>StandardOutPath</key>`)
	line(`	<string>%s</string>`, escaped(a.Log))
	line(`	<key>ThrottleInterval</key>`)
	line(`	<integer>%d</integer>`, throttleSeconds)
	line(`</dict>`)
	line(`</plist>`)
	return []byte(out.String())
}

type Launchd struct {
	Runner shell.Runner
	Home   string
	UID    int
	Sleep  func(time.Duration)
	Out    io.Writer
}

func (l Launchd) ctl(ctx context.Context, args ...string) (int, string, error) {
	result, err := l.Runner.Run(ctx, shell.Command{Argv: append([]string{"launchctl"}, args...), Timeout: launchctlWait})
	var exit *shell.ExitError
	if err != nil && !errors.As(err, &exit) {
		return 0, "", err
	}
	return result.Code, strings.TrimSpace(result.Stderr + result.Stdout), nil
}

func (l Launchd) plist(drive string) string {
	return filepath.Join(l.Home, "Library", "LaunchAgents", Label(drive)+".plist")
}

func (l Launchd) target(drive string) string {
	return fmt.Sprintf("gui/%d/%s", l.UID, Label(drive))
}

func (l Launchd) bootout(ctx context.Context, target string) error {
	code, said, err := l.ctl(ctx, "bootout", target)
	if err != nil {
		return err
	}
	if code != 0 {
		return fmt.Errorf("launchctl bootout %s failed: %s", target, said)
	}
	return nil
}

func (l Launchd) Install(ctx context.Context, agent Agent) error {
	name, plist, target := Label(agent.Drive), l.plist(agent.Drive), l.target(agent.Drive)
	body := agent.Plist()
	code, _, err := l.ctl(ctx, "print", target)
	if err != nil {
		return err
	}
	if code == 0 {
		if have, err := os.ReadFile(plist); err == nil && bytes.Equal(have, body) {
			fmt.Fprintf(l.Out, "lr: launchd already runs %s from %s\n", name, plist)
			return nil
		}
		if err := l.bootout(ctx, target); err != nil {
			return err
		}
		if err := l.awaitBootout(ctx, target); err != nil {
			return err
		}
	}
	if err := os.MkdirAll(filepath.Dir(plist), 0o750); err != nil {
		return err
	}
	if err := os.WriteFile(plist, body, 0o600); err != nil {
		return err
	}
	code, said, err := l.ctl(ctx, "bootstrap", fmt.Sprintf("gui/%d", l.UID), plist)
	if err != nil {
		return err
	}
	if code != 0 {
		return fmt.Errorf("launchctl bootstrap %s failed: %s", plist, said)
	}
	fmt.Fprintf(l.Out, "lr: launchd runs %s from %s, logging to %s\n", name, plist, agent.Log)
	return nil
}

func (l Launchd) awaitBootout(ctx context.Context, target string) error {
	for range bootoutWait {
		code, _, err := l.ctl(ctx, "print", target)
		if err != nil {
			return err
		}
		if code != 0 {
			return nil
		}
		l.Sleep(time.Second)
	}
	return fmt.Errorf("%s was still loaded %d s after its bootout", target, bootoutWait)
}

func (l Launchd) Uninstall(ctx context.Context, drive string) error {
	target, plist := l.target(drive), l.plist(drive)
	code, _, err := l.ctl(ctx, "print", target)
	if err != nil {
		return err
	}
	if code == 0 {
		if err := l.bootout(ctx, target); err != nil {
			return err
		}
	}
	if err := os.Remove(plist); err != nil && !errors.Is(err, fs.ErrNotExist) {
		return err
	}
	fmt.Fprintf(l.Out, "lr: %s booted out and %s removed\n", Label(drive), plist)
	return nil
}
