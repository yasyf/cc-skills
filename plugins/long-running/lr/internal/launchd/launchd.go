package launchd

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
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	throttleSeconds = 60
	openFiles       = 65536
	bootoutWait     = 60
	launchctlWait   = time.Minute
)

func escaped(text string) string {
	var out bytes.Buffer
	if err := xml.EscapeText(&out, []byte(text)); err != nil {
		panic(err)
	}
	return out.String()
}

type Job struct {
	Label     string
	Env       [][2]string
	Arguments []string
	Directory string
	Log       string
}

func (j Job) Plist() []byte {
	var out strings.Builder
	line := func(format string, args ...any) { fmt.Fprintf(&out, format+"\n", args...) }
	line(`<?xml version="1.0" encoding="UTF-8"?>`)
	line(`<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">`)
	line(`<plist version="1.0">`)
	line(`<dict>`)
	line(`	<key>EnvironmentVariables</key>`)
	line(`	<dict>`)
	for _, pair := range j.Env {
		value := pair[1]
		if pair[0] == "PATH" {
			value = withoutLinkedWorktrees(value)
		}
		line(`		<key>%s</key>`, escaped(pair[0]))
		line(`		<string>%s</string>`, escaped(value))
	}
	line(`	</dict>`)
	line(`	<key>KeepAlive</key>`)
	line(`	<true/>`)
	line(`	<key>Label</key>`)
	line(`	<string>%s</string>`, escaped(j.Label))
	line(`	<key>ProgramArguments</key>`)
	line(`	<array>`)
	for _, word := range j.Arguments {
		line(`		<string>%s</string>`, escaped(word))
	}
	line(`	</array>`)
	line(`	<key>SoftResourceLimits</key>`)
	line(`	<dict>`)
	line(`		<key>NumberOfFiles</key>`)
	line(`		<integer>%d</integer>`, openFiles)
	line(`	</dict>`)
	line(`	<key>StandardErrorPath</key>`)
	line(`	<string>%s</string>`, escaped(j.Log))
	line(`	<key>StandardOutPath</key>`)
	line(`	<string>%s</string>`, escaped(j.Log))
	line(`	<key>ThrottleInterval</key>`)
	line(`	<integer>%d</integer>`, throttleSeconds)
	if j.Directory != "" {
		line(`	<key>WorkingDirectory</key>`)
		line(`	<string>%s</string>`, escaped(j.Directory))
	}
	line(`</dict>`)
	line(`</plist>`)
	return []byte(out.String())
}

func withoutLinkedWorktrees(path string) string {
	kept := slices.DeleteFunc(filepath.SplitList(path), inLinkedWorktree)
	return strings.Join(kept, string(os.PathListSeparator))
}

func inLinkedWorktree(dir string) bool {
	for dir = filepath.Clean(dir); ; dir = filepath.Dir(dir) {
		if info, err := os.Lstat(filepath.Join(dir, ".git")); err == nil {
			return info.Mode().IsRegular()
		}
		if dir == filepath.Dir(dir) {
			return false
		}
	}
}

type Launchd struct {
	Runner shell.Runner
	Home   string
	UID    int
	Sleep  func(time.Duration)
	Out    io.Writer
}

func New(runner shell.Runner, out io.Writer) (Launchd, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return Launchd{}, err
	}
	return Launchd{Runner: runner, Home: home, UID: os.Getuid(), Sleep: time.Sleep, Out: out}, nil
}

func (l Launchd) ctl(ctx context.Context, args ...string) (int, string, error) {
	result, err := l.Runner.Run(ctx, shell.Command{Argv: append([]string{"launchctl"}, args...), Timeout: launchctlWait})
	var exit *shell.ExitError
	if err != nil && !errors.As(err, &exit) {
		return 0, "", err
	}
	return result.Code, strings.TrimSpace(result.Stderr + result.Stdout), nil
}

func (l Launchd) plist(label string) string {
	return filepath.Join(l.Home, "Library", "LaunchAgents", label+".plist")
}

func (l Launchd) target(label string) string {
	return fmt.Sprintf("gui/%d/%s", l.UID, label)
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

func (l Launchd) Install(ctx context.Context, job Job) error {
	plist, target := l.plist(job.Label), l.target(job.Label)
	body := job.Plist()
	code, _, err := l.ctl(ctx, "print", target)
	if err != nil {
		return err
	}
	if code == 0 {
		if have, err := os.ReadFile(plist); err == nil && bytes.Equal(have, body) {
			fmt.Fprintf(l.Out, "lr: launchd already runs %s from %s\n", job.Label, plist)
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
	fmt.Fprintf(l.Out, "lr: launchd runs %s from %s, logging to %s\n", job.Label, plist, job.Log)
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

func (l Launchd) Uninstall(ctx context.Context, label string) error {
	target, plist := l.target(label), l.plist(label)
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
	fmt.Fprintf(l.Out, "lr: %s booted out and %s removed\n", label, plist)
	return nil
}

func (l Launchd) Restart(ctx context.Context, label string) error {
	target := l.target(label)
	code, _, err := l.ctl(ctx, "print", target)
	if err != nil {
		return err
	}
	if code != 0 {
		return fmt.Errorf("launchd has no %s loaded", target)
	}
	code, said, err := l.ctl(ctx, "kickstart", "-k", target)
	if err != nil {
		return err
	}
	if code != 0 {
		return fmt.Errorf("launchctl kickstart -k %s failed: %s", target, said)
	}
	return nil
}
