package grading

import (
	"context"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/grading/grader"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	stallAfter   = 15 * time.Minute
	stallCancel  = "its stall"
	progressFile = "progress"
	probeTimeout = 60 * time.Second
	killTimeout  = 2 * time.Minute
	jobKilled    = "killed"

	newestScript = `now=$(date +%s)
for dir in "$@"; do
  newest=$(find "$dir/" -maxdepth 2 -type f -printf "%Ts\n" 2>/dev/null | sort -n | tail -n 1)
  echo "$((now - ${newest:-0})) $dir"
done`

	killScript = `dir=$1
[ ! -d "$dir/" ] || printf "%s\n" "$2" >"$dir/cancelled"
killed=
if read -r pid start 2>/dev/null <"$dir/job-process" &&
  [ "$(cut -d" " -f22 "/proc/$pid/stat" 2>/dev/null)" = "$start" ] &&
  tr "\0" " " <"/proc/$pid/cmdline" | grep -qF "$dir" &&
  kill -KILL "-$pid"; then
  killed=1
fi
for run in "$dir"/codex-*; do
  [ ! -e "$run" ] || timeout 60 codex-ask --cancel "$run" >/dev/null 2>&1
done
[ -z "$killed" ] || echo killed`
)

func KillGroup(group int) error {
	return syscall.Kill(-group, syscall.SIGKILL)
}

func spriteExec(sprite, script string, args ...string) []string {
	return append([]string{"sprite", "exec", "-s", sprite, "--no-stdin", "--", "sh", "-c", script, "sh"}, args...)
}

type quietPass struct {
	name      string
	directory string
	seen      time.Time
}

func (p *JobPool) stalls(ctx context.Context, running []string) (map[string]time.Duration, error) {
	byBox := map[string][]quietPass{}
	for _, name := range running {
		ref := refOf(name)
		if _, on := p.desks[graderOf(ref.lane)]; !on {
			continue
		}
		directory := filepath.Join(p.root, ref.lane, ref.base)
		seen, err := lastSeen(directory)
		if err != nil {
			return nil, err
		}
		if p.Now().Sub(seen) >= stallAfter {
			byBox[ref.box] = append(byBox[ref.box], quietPass{name, directory, seen})
		}
	}
	var lock sync.Mutex
	stalled := map[string]time.Duration{}
	var failures []error
	var reading sync.WaitGroup
	for box, passes := range byBox {
		reading.Go(func() {
			newest, err := p.newest(ctx, box, passes)
			if err != nil {
				p.Log.Warn("a Sprite did not say what its quiet passes last wrote; the next pass asks again", "sprite", box, "error", err)
				return
			}
			for _, pass := range passes {
				at, read := newest[pass.directory]
				if !read {
					continue
				}
				if at.After(pass.seen) {
					pass.seen = at
				}
				idle := p.Now().Sub(pass.seen)
				lock.Lock()
				if idle >= stallAfter {
					stalled[pass.name] = idle
				} else if err := recordProgress(pass.directory, pass.seen); err != nil {
					failures = append(failures, err)
				}
				lock.Unlock()
			}
		})
	}
	reading.Wait()
	return stalled, errors.Join(failures...)
}

func lastSeen(directory string) (time.Time, error) {
	var seen time.Time
	for _, name := range []string{pidFile, logFile, progressFile} {
		info, err := os.Stat(filepath.Join(directory, name))
		if errors.Is(err, fs.ErrNotExist) {
			continue
		}
		if err != nil {
			return time.Time{}, err
		}
		if info.ModTime().After(seen) {
			seen = info.ModTime()
		}
	}
	return seen, nil
}

func recordProgress(directory string, at time.Time) error {
	path := filepath.Join(directory, progressFile)
	if err := os.WriteFile(path, nil, 0o600); err != nil {
		return err
	}
	return os.Chtimes(path, at, at)
}

func (p *JobPool) newest(ctx context.Context, box string, passes []quietPass) (map[string]time.Time, error) {
	if box == localBox {
		return newestHere(passes), nil
	}
	directories := make([]string, 0, len(passes))
	for _, pass := range passes {
		directories = append(directories, pass.directory)
	}
	result, err := p.Runner.Run(ctx, shell.Command{Argv: spriteExec(box, newestScript, directories...), Timeout: probeTimeout})
	if err != nil {
		return nil, err
	}
	newest := make(map[string]time.Time, len(passes))
	for line := range strings.Lines(result.Stdout) {
		age, directory, found := strings.Cut(strings.TrimSpace(line), " ")
		seconds, err := strconv.ParseInt(age, 10, 64)
		if !found || err != nil {
			return nil, fmt.Errorf("an unreadable progress line: %q", line)
		}
		newest[directory] = p.Now().Add(-time.Duration(seconds) * time.Second)
	}
	return newest, nil
}

func newestHere(passes []quietPass) map[string]time.Time {
	newest := make(map[string]time.Time, len(passes))
	for _, pass := range passes {
		for _, pattern := range []string{"*", filepath.Join("*", "*")} {
			paths, err := filepath.Glob(filepath.Join(pass.directory, pattern))
			if err != nil {
				panic(err)
			}
			for _, path := range paths {
				info, err := os.Stat(path)
				if err == nil && info.Mode().IsRegular() && info.ModTime().After(newest[pass.directory]) {
					newest[pass.directory] = info.ModTime()
				}
			}
		}
	}
	return newest
}

func (p *JobPool) markStalled(ref workerRef, directory string, idle time.Duration) error {
	started, err := os.Stat(filepath.Join(directory, pidFile))
	if err != nil {
		return err
	}
	why := fmt.Sprintf("killed on %s after %d min with no progress, %d min after it started", ref.box, int(idle.Minutes()), int(p.Now().Sub(started.ModTime()).Minutes()))
	return writeAtomic(filepath.Join(directory, grader.ResultFile), map[string]any{"error": why, "stalled": true})
}

func (p *JobPool) stop(ctx context.Context, ref workerRef, directory, by string) error {
	if err := p.cancel(ctx, directory, by); err != nil {
		return err
	}
	if ref.box != localBox {
		result, err := p.Runner.Run(ctx, shell.Command{Argv: spriteExec(ref.box, killScript, directory, by), Timeout: killTimeout})
		if err == nil && strings.TrimSpace(result.Stdout) == jobKilled {
			return nil
		}
		p.Log.Warn("a pass's process did not answer on its Sprite; its wrapper here goes instead", "job", directory, "error", err)
	}
	commands, err := p.commands(ctx)
	if err != nil {
		return err
	}
	if !runsJob(directory, commands) {
		return nil
	}
	said, err := os.ReadFile(filepath.Join(directory, pidFile))
	if err != nil {
		return err
	}
	group, err := strconv.Atoi(strings.TrimSpace(string(said)))
	if err != nil {
		return err
	}
	return p.KillGroup(group)
}
