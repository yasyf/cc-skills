package orca

import (
	"bytes"
	"context"
	"errors"
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
)

var (
	holderScripts = []string{"*.sh", "*.zsh", "*.py"}
	paneKey       = regexp.MustCompile(`\bORCA_PANE_KEY=(\S+)`)
)

type Processes struct {
	Runner shell.Runner
	Home   string
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
	if real, err := filepath.EvalSymlinks(path); err == nil {
		return real
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
