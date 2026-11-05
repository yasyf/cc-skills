package orca

import (
	"bytes"
	"cmp"
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	hookDaemon     = "capt-hookd serve"
	hookWorker     = "-m captain_hook.worker"
	heldOpenInside = "a live process has a file or its cwd open inside"
	heldByCommand  = "a live process names it on its command line"
	detachedTTY    = "??"
	sessionColumns = "pid=,ppid=,pgid=,lstart=,command="
	sessionFields  = 9
	loginPrefix    = "-"
)

var (
	holderScripts = []string{"*.sh", "*.zsh", "*.py"}
	agentCommands = []string{"claude", "codex"}
	paneKey       = environmentKey("ORCA_PANE_KEY")
	terminalKey   = environmentKey("ORCA_TERMINAL_HANDLE")
	workspaceKey  = environmentKey("ORCA_WORKTREE_ID")
)

func environmentKey(name string) *regexp.Regexp {
	return regexp.MustCompile(`\b` + name + `=([^\s'"]+)`)
}

type Processes struct {
	Runner shell.Runner
	Home   string
	Dev    string
}

func (p Processes) stdout(ctx context.Context, argv ...string) (string, error) {
	result, err := p.Runner.Run(ctx, shell.Command{Argv: argv})
	var exit *shell.ExitError
	if err != nil && !errors.As(err, &exit) {
		return "", err
	}
	return result.Stdout, nil
}

func fields(line string, count int) []string {
	parts := make([]string, 0, count)
	rest := strings.TrimLeft(line, " \t")
	for len(parts) < count-1 {
		at := strings.IndexAny(rest, " \t")
		if at < 0 {
			break
		}
		parts = append(parts, rest[:at])
		rest = strings.TrimLeft(rest[at:], " \t")
	}
	return append(parts, rest)
}

func within(path, root string) bool {
	return path == root || strings.HasPrefix(path, strings.TrimRight(root, "/")+"/")
}

func resolved(path string) string {
	if target, err := filepath.EvalSymlinks(path); err == nil {
		return target
	}
	return filepath.Clean(path)
}

func (p Processes) idleLoginShells(ctx context.Context) (map[string]bool, error) {
	out, err := p.stdout(ctx, "ps", "-axo", "pid=,ppid=,pgid=,tpgid=,comm=")
	if err != nil {
		return nil, err
	}
	parents := map[string]bool{}
	var rows [][]string
	for line := range strings.Lines(out) {
		row := fields(strings.TrimRight(line, "\n"), 5)
		if len(row) == 5 {
			rows = append(rows, row)
			parents[row[1]] = true
		}
	}
	shells := map[string]bool{}
	for _, row := range rows {
		if strings.HasPrefix(row[4], "-") && row[0] == row[2] && row[0] == row[3] && !parents[row[0]] {
			shells[row[0]] = true
		}
	}
	return shells, nil
}

func (p Processes) hookWorkers(ctx context.Context) (map[string]bool, error) {
	out, err := p.stdout(ctx, "ps", "-axo", "pid=,ppid=,command=")
	if err != nil {
		return nil, err
	}
	daemons := map[string]bool{}
	var rows [][]string
	for line := range strings.Lines(out) {
		row := fields(strings.TrimRight(line, "\n"), 3)
		if len(row) == 3 {
			rows = append(rows, row)
			if strings.HasSuffix(row[2], hookDaemon) {
				daemons[row[0]] = true
			}
		}
	}
	workers := map[string]bool{}
	for _, row := range rows {
		if daemons[row[1]] && strings.HasSuffix(row[2], hookWorker) {
			workers[row[0]] = true
		}
	}
	return workers, nil
}

func (p Processes) descendants(ctx context.Context, roots map[string]bool) (map[string]bool, error) {
	out, err := p.stdout(ctx, "ps", "-axo", "pid=,ppid=,sess=")
	if err != nil {
		return nil, err
	}
	session := map[string]string{}
	children := map[string][]string{}
	for line := range strings.Lines(out) {
		row := strings.Fields(line)
		if len(row) == 3 {
			session[row[0]] = row[2]
			children[row[1]] = append(children[row[1]], row[0])
		}
	}
	found := map[string]bool{}
	pending := make([]string, 0, len(roots))
	for root := range roots {
		pending = append(pending, root)
	}
	for len(pending) > 0 {
		parent := pending[len(pending)-1]
		pending = pending[:len(pending)-1]
		for _, child := range children[parent] {
			if !found[child] && session[child] == session[parent] {
				found[child] = true
				pending = append(pending, child)
			}
		}
	}
	return found, nil
}

func sortedKeys(set map[string]bool) []string {
	keys := make([]string, 0, len(set))
	for key := range set {
		keys = append(keys, key)
	}
	slices.Sort(keys)
	return keys
}

func (p Processes) paneProcesses(ctx context.Context, pids, panes map[string]bool) (map[string]bool, error) {
	found := map[string]bool{}
	if len(panes) == 0 {
		return found, nil
	}
	out, err := p.stdout(ctx, "ps", "-E", "-ww", "-o", "pid=,tty=,command=", "-p", strings.Join(sortedKeys(pids), ","))
	if err != nil {
		return nil, err
	}
	for line := range strings.Lines(out) {
		row := fields(strings.TrimRight(line, "\n"), 3)
		if len(row) != 3 || row[1] == detachedTTY {
			continue
		}
		if key := paneKey.FindStringSubmatch(row[2]); key != nil && panes[key[1]] {
			found[row[0]] = true
		}
	}
	return found, nil
}

func (p Processes) openInside(ctx context.Context, worktree string) (map[string]bool, error) {
	root := resolved(worktree)
	out, err := p.stdout(ctx, "lsof", "-n", "-P", "-Fpn")
	if err != nil {
		return nil, err
	}
	pids := map[string]bool{}
	pid := ""
	for line := range strings.Lines(out) {
		line = strings.TrimRight(line, "\n")
		switch {
		case strings.HasPrefix(line, "p"):
			pid = line[1:]
		case strings.HasPrefix(line, "n/") && within(line[1:], root):
			pids[pid] = true
		}
	}
	return pids, nil
}

func (p Processes) unscanned() []string {
	return []string{filepath.Join(p.Home, "Library"), "/private/var/folders"}
}

func (p Processes) scannedCwds(listing, worktree string) []string {
	cwds := map[string]bool{}
	for line := range strings.Lines(listing) {
		line = strings.TrimRight(line, "\n")
		if !strings.HasPrefix(line, "n/") {
			continue
		}
		cwd := line[1:]
		if within(cwd, worktree) || slices.ContainsFunc(p.unscanned(), func(root string) bool { return within(cwd, root) }) {
			continue
		}
		cwds[cwd] = true
	}
	return sortedKeys(cwds)
}

func scriptNaming(cwd, name string) string {
	entries, err := os.ReadDir(cwd)
	if err != nil {
		return ""
	}
	for _, pattern := range holderScripts {
		for _, entry := range entries {
			if matched, err := filepath.Match(pattern, entry.Name()); err != nil || !matched {
				continue
			}
			path := filepath.Join(cwd, entry.Name())
			if raw, err := os.ReadFile(path); err == nil && bytes.Contains(raw, []byte(name)) {
				return path
			}
		}
	}
	return ""
}

func (p Processes) Holder(ctx context.Context, worktree string, panes map[string]bool) (string, error) {
	pids, err := p.openInside(ctx, worktree)
	if err != nil {
		return "", err
	}
	if len(pids) > 0 {
		held, err := p.liveHolder(ctx, pids, panes)
		if err != nil {
			return "", err
		}
		if held {
			return heldOpenInside, nil
		}
	}
	commands, err := p.stdout(ctx, "ps", "-axo", "command=")
	if err != nil {
		return "", err
	}
	if strings.Contains(commands, worktree) {
		return heldByCommand, nil
	}
	listing, err := p.stdout(ctx, "lsof", "-a", "-d", "cwd", "-Fn")
	if err != nil {
		return "", err
	}
	for _, cwd := range p.scannedCwds(listing, worktree) {
		if script := scriptNaming(cwd, filepath.Base(worktree)); script != "" {
			return "live process script " + script + " names it", nil
		}
	}
	return "", nil
}

type Session struct {
	PID     string
	PPID    string
	PGID    string
	Start   string
	Command string
}

func (s Session) said() string {
	return fmt.Sprintf("pid %s ppid %s pgid %s started %s %s", s.PID, s.PPID, s.PGID, s.Start, commandName(s.Command))
}

func commandName(command string) string {
	return filepath.Base(strings.Fields(command)[0])
}

func sessionOf(line string) (Session, bool) {
	row := fields(strings.TrimRight(line, "\n"), sessionFields)
	if len(row) != sessionFields {
		return Session{}, false
	}
	return Session{PID: row[0], PPID: row[1], PGID: row[2], Start: strings.Join(row[3:8], " "), Command: row[8]}, true
}

type Sessions map[string][]Session

func (s Sessions) agent(worker Worker) (Session, string) {
	if worker.Owner != worker.Dispatch {
		return Session{}, "Orca records its owner as " + cmp.Or(worker.Owner, "nobody")
	}
	bearers := s[worker.Terminal]
	shells := map[string]bool{}
	for _, process := range bearers {
		if strings.HasPrefix(process.Command, loginPrefix) {
			shells[process.PID] = true
		}
	}
	var agents []Session
	for _, process := range bearers {
		if shells[process.PPID] && slices.Contains(agentCommands, commandName(process.Command)) {
			agents = append(agents, process)
		}
	}
	if len(agents) != 1 {
		return Session{}, fmt.Sprintf("%d agent processes run under its login shell", len(agents))
	}
	agent := agents[0]
	if agent.PGID != agent.PID {
		return Session{}, fmt.Sprintf("pid %s leads no process group of its own", agent.PID)
	}
	if workspace := workspaceKey.FindStringSubmatch(agent.Command); workspace == nil || workspace[1] != worker.Workspace {
		return Session{}, fmt.Sprintf("pid %s runs for another worktree", agent.PID)
	}
	return agent, ""
}

func (p Processes) Sessions(ctx context.Context) (Sessions, error) {
	out, err := p.stdout(ctx, "ps", "-axE", "-ww", "-o", sessionColumns)
	if err != nil {
		return nil, err
	}
	bearers := Sessions{}
	for line := range strings.Lines(out) {
		process, ok := sessionOf(line)
		if !ok {
			continue
		}
		if handle := terminalKey.FindStringSubmatch(process.Command); handle != nil {
			bearers[handle[1]] = append(bearers[handle[1]], process)
		}
	}
	return bearers, nil
}

func (p Processes) StillRuns(ctx context.Context, session Session) (bool, error) {
	out, err := p.stdout(ctx, "ps", "-E", "-ww", "-o", sessionColumns, "-p", session.PID)
	if err != nil {
		return false, err
	}
	read, ok := sessionOf(out)
	return ok && read == session, nil
}

func (p Processes) liveHolder(ctx context.Context, pids, panes map[string]bool) (bool, error) {
	stopping, err := p.paneProcesses(ctx, pids, panes)
	if err != nil {
		return false, err
	}
	shells, err := p.idleLoginShells(ctx)
	if err != nil {
		return false, err
	}
	workers, err := p.hookWorkers(ctx)
	if err != nil {
		return false, err
	}
	children, err := p.descendants(ctx, stopping)
	if err != nil {
		return false, err
	}
	for pid := range pids {
		if !shells[pid] && !workers[pid] && !stopping[pid] && !children[pid] {
			return true, nil
		}
	}
	return false, nil
}
