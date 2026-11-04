package orcarpc

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	protocolVersion    = 3
	contractCapability = "orchestration.contract.v1"
	verifiedFile       = "verified.json"
	failedSuffix       = ".failed"
	lockFile           = "parity.lock"
	staleLock          = 5 * time.Minute
)

type Served struct {
	Stdout []byte
	Code   int
	Retry  string
}

func (c *Client) Serve(ctx context.Context, args []string) (Served, bool) {
	path, in, ok := parseInvocation(args)
	if !ok {
		return Served{}, false
	}
	spec, known := verbs[path]
	if !known || !spec.accepts(in) {
		return Served{}, false
	}
	var request call
	if spec.build != nil {
		if request, ok = spec.build(c, in); !ok {
			return Served{}, false
		}
	}
	if in.has(flagEnvironment) {
		return c.serveRemote(ctx, in.flags[flagEnvironment], path, spec, in, request)
	}
	conn, err := c.Dial(ctx)
	if err != nil {
		return Served{}, false
	}
	defer func() { _ = conn.Close() }()
	if c.Gate != nil && !c.Gate.allows(ctx, conn) {
		return Served{}, false
	}
	if spec.offline != nil {
		reply, listed := spec.offline(c)
		if !listed {
			return Served{}, false
		}
		return rendered(reply, 0)
	}
	var sent Served
	var reply Response
	if check, isCheck := request.params.(CheckParams); isCheck && check.mutates() {
		if sent.Retry, err = newUUID(); err != nil {
			return Served{}, false
		}
		reply, err = conn.mutateCheck(ctx, check, request.timeout, sent.Retry)
	} else {
		reply, err = conn.call(ctx, request.method, request.params, request.timeout)
	}
	if err != nil {
		return sent, false
	}
	if !reply.OK {
		if sent.Retry != "" {
			return sent, false
		}
		return refusal(path, in, reply, false)
	}
	code := 0
	if spec.finish != nil {
		if reply.Result, code, ok = spec.finish(c, conn, in, reply.Result); !ok {
			return sent, false
		}
	}
	if request.method == "status.get" {
		if reply, err = projectStatus(conn.metadata.PID, reply); err != nil {
			return sent, false
		}
	}
	stdout, err := Render(reply)
	if err != nil {
		return sent, false
	}
	return Served{Stdout: stdout, Code: code}, true
}

func (s Served) RetryFlags() []string {
	if s.Retry == "" {
		return nil
	}
	return []string{"--retry-request", s.Retry}
}

func (c *Client) omittedSelectors(hostIDs []string) (json.RawMessage, error) {
	dir, err := c.userDataPath()
	if err != nil {
		return nil, err
	}
	raw, err := os.ReadFile(filepath.Join(dir, "orca-environments.json"))
	if err != nil {
		return nil, err
	}
	var store struct {
		Environments []struct {
			ID   string `json:"id"`
			Name string `json:"name"`
		} `json:"environments"`
	}
	if err := json.Unmarshal(raw, &store); err != nil {
		return nil, err
	}
	type selector struct {
		HostID   string  `json:"hostId"`
		Selector *string `json:"selector"`
	}
	selectors := make([]selector, 0, len(hostIDs))
	for _, hostID := range hostIDs {
		entry := selector{HostID: hostID}
		if id, isRuntime := strings.CutPrefix(hostID, "runtime:"); isRuntime {
			for _, environment := range store.Environments {
				if environment.ID == id {
					named := "--environment " + environment.Name
					entry.Selector = &named
				}
			}
		}
		selectors = append(selectors, entry)
	}
	return marshal(selectors)
}

type Gate struct {
	Spawn   func()
	mu      sync.Mutex
	spawned bool
	admits  map[string]bool
}

func (g *Gate) allows(ctx context.Context, conn *Conn) bool {
	g.mu.Lock()
	defer g.mu.Unlock()
	if admitted, known := g.admits[conn.metadata.RuntimeID]; known {
		return admitted
	}
	reply, err := conn.Call(ctx, "status.get", nil)
	if err != nil || !reply.OK {
		return false
	}
	var status runtimeStatus
	if json.Unmarshal(reply.Result, &status) != nil {
		return false
	}
	if !slices.Contains(status.Capabilities, contractCapability) || status.MinClientVersion > protocolVersion || status.ProtocolVersion < protocolVersion {
		g.remember(conn, false)
		return false
	}
	switch parityOf(status.AppVersion) {
	case parityPassed:
		g.remember(conn, true)
		return true
	case parityFailed:
		g.remember(conn, false)
		return false
	}
	if g.Spawn != nil && !g.spawned {
		g.spawned = true
		g.Spawn()
	}
	return false
}

func (g *Gate) remember(conn *Conn, admitted bool) {
	if g.admits == nil {
		g.admits = map[string]bool{}
	}
	g.admits[conn.metadata.RuntimeID] = admitted
}

type parityState int

const (
	parityUnknown parityState = iota
	parityPassed
	parityFailed
)

func parityOf(version string) parityState {
	state, err := StateDir()
	if err != nil {
		return parityUnknown
	}
	raw, err := os.ReadFile(filepath.Join(state, verifiedFile))
	if err == nil {
		var verified struct {
			Version string `json:"version"`
		}
		if json.Unmarshal(raw, &verified) == nil && verified.Version == version {
			return parityPassed
		}
	}
	if _, err := os.Stat(filepath.Join(state, version+failedSuffix)); err == nil {
		return parityFailed
	}
	return parityUnknown
}

func RecordParity(ctx context.Context, client *Client, cli shell.Runner) error {
	state, err := StateDir()
	if err != nil {
		return err
	}
	if err := os.MkdirAll(state, 0o700); err != nil {
		return err
	}
	lock := filepath.Join(state, lockFile)
	if info, err := os.Stat(lock); err == nil && time.Since(info.ModTime()) > staleLock {
		_ = os.Remove(lock)
	}
	held, err := os.OpenFile(lock, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	_ = held.Close()
	defer func() { _ = os.Remove(lock) }()
	conn, err := client.Dial(ctx)
	if err != nil {
		return err
	}
	reply, err := conn.Call(ctx, "status.get", nil)
	_ = conn.Close()
	if err != nil {
		return err
	}
	var status runtimeStatus
	if err := json.Unmarshal(reply.Result, &status); err != nil {
		return err
	}
	if err := Parity(ctx, client, cli); err != nil {
		if errors.Is(err, errParityDiffers) {
			_ = os.WriteFile(filepath.Join(state, status.AppVersion+failedSuffix), []byte(err.Error()+"\n"), 0o600)
		}
		return err
	}
	recorded, err := marshal(map[string]string{"version": status.AppVersion})
	if err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(state, verifiedFile), recorded, 0o600)
}

func SpawnParity() {
	self, err := os.Executable()
	if err != nil {
		return
	}
	cmd := exec.Command(self, "orca", "parity")
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	if cmd.Start() == nil {
		_ = cmd.Process.Release()
	}
}

type Runner struct {
	Next shell.Runner
	Gate *Gate
}

func NewRunner(next shell.Runner) Runner {
	return Runner{Next: next, Gate: &Gate{Spawn: SpawnParity}}
}

func (r Runner) Run(ctx context.Context, c shell.Command) (shell.Result, error) {
	if len(c.Argv) == 0 || c.Argv[0] != "orca" {
		return r.Next.Run(ctx, c)
	}
	attempt := ctx
	if c.Timeout > 0 {
		var cancel context.CancelFunc
		attempt, cancel = context.WithTimeout(ctx, c.Timeout)
		defer cancel()
	}
	client := &Client{Getenv: overlaid(c.Env), Timeout: c.Timeout, Gate: r.Gate}
	served, ok := client.Serve(attempt, c.Argv[1:])
	if !ok {
		if attempt.Err() != nil {
			return shell.Result{Code: shell.CodeTimedOut}, fmt.Errorf("%s: %w", strings.Join(c.Argv, " "), attempt.Err())
		}
		c.Argv = append(slices.Clone(c.Argv), served.RetryFlags()...)
		return r.Next.Run(ctx, c)
	}
	result := shell.Result{Code: served.Code}
	if c.Stdout != nil {
		if _, err := c.Stdout.Write(served.Stdout); err != nil {
			return result, err
		}
	} else {
		result.Stdout = string(served.Stdout)
	}
	if served.Code != 0 {
		return result, &shell.ExitError{Argv: c.Argv, Code: served.Code}
	}
	return result, nil
}

func overlaid(env []string) func(string) string {
	return func(name string) string {
		for index := len(env) - 1; index >= 0; index-- {
			if value, found := strings.CutPrefix(env[index], name+"="); found {
				return value
			}
		}
		return os.Getenv(name)
	}
}

func StateDir() (string, error) {
	if dir := os.Getenv("XDG_STATE_HOME"); dir != "" {
		return filepath.Join(dir, "orca-rpc"), nil
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, ".local", "state", "orca-rpc"), nil
}

func Passthrough(path string, args []string) error {
	return syscall.Exec(path, append([]string{"orca"}, args...), os.Environ())
}
