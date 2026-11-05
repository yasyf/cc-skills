// Package wait is the one way a lane blocks: one process that follows its
// sources and exits, in place of a shell loop, a Monitor or a timeout.
package wait

import (
	"bufio"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"maps"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"regexp"
	"slices"
	"sort"
	"strconv"
	"strings"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/fsnotify/fsnotify"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	DisplayChars   = 400
	CodeUsage      = 2
	CodeNoRoster   = 1
	CodeCCIMissing = shell.CodeMissingBinary
	Daemon         = "http://127.0.0.1:7377/v1/stream"
	Usage          = "usage: lr wait [--wake <file>] <seconds> [<file>=<cursor-file> | mailbox:<lane>=<cursor-file> | cci:<drive>:<lane>]... [-- <command> [<arg>...]]"

	CodeRecursive = 3

	mailboxPrefix = "mailbox:"
	boundedTag    = "LR_BOUNDED"
	seenKeys      = 1000
	childGrace    = 5 * time.Second
	treePoll      = 100 * time.Millisecond
	cciGrace      = 30 * time.Second
	reconnect     = time.Second
	lockStale     = 10 * time.Second
	lockPoll      = 10 * time.Millisecond
	readTimeout   = 60 * time.Second
)

var (
	sourceForm  = regexp.MustCompile(`(?s)\Acci:([^:=]+):([^:=]+)\z|\A([^=]+)=(.+)\z`)
	taggedChain = regexp.MustCompile(`(?:^|\s)` + boundedTag + `=(\S+)`)
	recordHead  = regexp.MustCompile(`^(?:#?(\d+(?:@\w+)?)\s|(@.*)$)`)
	pacific     = must(time.LoadLocation("America/Los_Angeles"))
)

func must[T any](value T, err error) T {
	if err != nil {
		panic(err)
	}
	return value
}

type ExitError struct{ Code int }

func (e *ExitError) Error() string { return fmt.Sprintf("lr wait exited %d", e.Code) }

type Options struct {
	Seconds int
	Sources []string
	Command []string
	Stdout  io.Writer
	Stderr  io.Writer
	Runner  shell.Runner
	Stream  string
	Claude  string
	Now     func() time.Time
}

type source interface {
	check(ctx context.Context, w *waiter) (bool, error)
	dir() string
	heard(name string) bool
}

type fileSource struct {
	path, cursor string
}

type mailboxSource struct {
	path, cursor string
}

type cciSource struct {
	drive, lane string
}

func (s fileSource) dir() string    { return filepath.Dir(s.path) }
func (s mailboxSource) dir() string { return filepath.Dir(s.path) }
func (cciSource) dir() string       { return "" }

func (s fileSource) heard(name string) bool    { return strings.HasPrefix(name, s.path) }
func (s mailboxSource) heard(name string) bool { return strings.HasPrefix(name, s.path) }
func (cciSource) heard(string) bool            { return false }

type waiter struct {
	Options
	sources      []source
	cciMissingAt time.Time
	wake         chan struct{}
	printed      map[string]bool
	repeat       bool
	held         []string
}

type child struct {
	cmd     *exec.Cmd
	done    <-chan int
	exited  atomic.Bool
	token   string
	started time.Time
}

// Run blocks until a source wakes it, its guarded command exits, or the
// deadline passes, printing what woke it, and returns the exit code.
func Run(ctx context.Context, o Options) int {
	w := &waiter{Options: o, wake: make(chan struct{}, 1), printed: map[string]bool{}}
	if w.Now == nil {
		w.Now = time.Now
	}
	if w.Stream == "" {
		w.Stream = Daemon
	}
	code, err := w.run(ctx)
	w.flush()
	if err != nil {
		fmt.Fprintln(w.Stderr, "lr wait:", err)
	}
	return code
}

func (w *waiter) run(ctx context.Context) (int, error) {
	if code, err := w.parse(); err != nil {
		return code, err
	}
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	watcher, err := w.watch(ctx)
	if err != nil {
		return CodeUsage, err
	}
	defer func() { _ = watcher.Close() }()
	streamErrs := w.follow(ctx)
	guarded, err := w.start()
	if err != nil {
		var recursive *recursiveError
		if errors.As(err, &recursive) {
			return CodeRecursive, err
		}
		return CodeUsage, err
	}
	signals := make(chan os.Signal, 1)
	signal.Notify(signals, syscall.SIGTERM, syscall.SIGHUP, syscall.SIGINT)
	defer signal.Stop(signals)
	deadline := time.NewTimer(time.Duration(w.Seconds) * time.Second)
	defer deadline.Stop()
	due := func(source) bool { return true }
	for {
		changed, err := w.check(ctx, due)
		if err != nil {
			guarded.end()
			return CodeUsage, err
		}
		if changed {
			guarded.end()
			return 0, nil
		}
		var missing <-chan time.Time
		if !w.cciMissingAt.IsZero() {
			missing = time.After(cciGrace - w.Now().Sub(w.cciMissingAt))
		}
		select {
		case <-w.wake:
			due = func(s source) bool {
				_, followed := s.(cciSource)
				return followed
			}
		case event := <-watcher.Events:
			names := drain(watcher.Events, event.Name)
			due = func(s source) bool { return slices.ContainsFunc(names, s.heard) }
		case err := <-watcher.Errors:
			guarded.end()
			return CodeUsage, err
		case err := <-streamErrs:
			guarded.end()
			return CodeCCIMissing, err
		case state := <-guarded.done:
			return state, nil
		case sig := <-signals:
			guarded.end()
			return 128 + int(sig.(syscall.Signal)), nil
		case <-missing:
			guarded.end()
			return CodeCCIMissing, w.cciMissing()
		case <-deadline.C:
			if !w.cciMissingAt.IsZero() {
				guarded.end()
				return CodeCCIMissing, w.cciMissing()
			}
			w.flush()
			fmt.Fprintf(w.Stdout, "QUIET %s\n", w.Now().In(pacific).Format("3:04 PM"))
			guarded.end()
			return 0, nil
		case <-ctx.Done():
			guarded.end()
			return CodeUsage, ctx.Err()
		}
	}
}

func drain(events chan fsnotify.Event, first string) []string {
	names := []string{first}
	for {
		select {
		case event := <-events:
			names = append(names, event.Name)
		default:
			return names
		}
	}
}

func (w *waiter) parse() (int, error) {
	if w.Seconds < 0 || (len(w.Sources) == 0 && len(w.Command) == 0) {
		return CodeUsage, errors.New(Usage)
	}
	for _, text := range w.Sources {
		match := sourceForm.FindStringSubmatch(text)
		switch {
		case match == nil:
			return CodeUsage, errors.New(Usage)
		case match[1] != "":
			w.sources = append(w.sources, cciSource{drive: match[1], lane: match[2]})
		default:
			path, cursor := match[3], match[4]
			if lane, ok := strings.CutPrefix(path, mailboxPrefix); ok {
				resolved, err := Mailbox(w.Claude, lane)
				if err != nil {
					return CodeUsage, err
				}
				if resolved == "" {
					return CodeNoRoster, fmt.Errorf("no team roster lists %s", lane)
				}
				path = resolved
			}
			if !filepath.IsAbs(path) {
				return CodeUsage, fmt.Errorf("%s is not an absolute path; name a team mailbox as %s<lane>", path, mailboxPrefix)
			}
			if isMailbox(path) {
				w.sources = append(w.sources, mailboxSource{path: path, cursor: cursor})
			} else {
				w.sources = append(w.sources, fileSource{path: path, cursor: cursor})
			}
		}
	}
	return 0, nil
}

func isMailbox(path string) bool {
	return filepath.Ext(path) == ".json" && filepath.Base(filepath.Dir(path)) == "inboxes"
}

func (w *waiter) watch(ctx context.Context) (*fsnotify.Watcher, error) {
	watcher, err := fsnotify.NewWatcher()
	if err != nil {
		return nil, err
	}
	dirs := map[string]bool{}
	for _, s := range w.sources {
		if dir := s.dir(); dir != "" && !dirs[dir] {
			dirs[dir] = true
			if err := os.MkdirAll(dir, 0o750); err != nil {
				return nil, err
			}
			if err := watcher.Add(dir); err != nil {
				return nil, err
			}
		}
	}
	go func() {
		<-ctx.Done()
		_ = watcher.Close()
	}()
	return watcher, nil
}

func (w *waiter) check(ctx context.Context, due func(source) bool) (bool, error) {
	changed := false
	for _, s := range w.sources {
		if !due(s) {
			continue
		}
		moved, err := s.check(ctx, w)
		if err != nil {
			return false, err
		}
		changed = changed || moved
	}
	return changed, nil
}

func (w *waiter) cciMissing() error {
	return fmt.Errorf("cci not found on PATH for %.0f s", w.Now().Sub(w.cciMissingAt).Seconds())
}

func clip(text string) string {
	runes := []rune(text)
	if len(runes) <= DisplayChars {
		return text
	}
	return string(runes[:DisplayChars-1]) + "…"
}

func (s fileSource) check(_ context.Context, w *waiter) (bool, error) {
	lines, err := inboxLines(s.path)
	if err != nil {
		return false, err
	}
	seen := seenCount(s.cursor)
	if len(lines) <= seen {
		return false, nil
	}
	for _, line := range lines[seen:] {
		if strings.TrimSpace(line) != "" {
			fmt.Fprintln(w.Stdout, clip(line))
		}
	}
	return true, os.WriteFile(s.cursor, []byte(strconv.Itoa(len(lines))+"\n"), 0o600)
}

func inboxLines(path string) ([]string, error) {
	dir, err := os.Open(filepath.Dir(path))
	if err != nil {
		return nil, err
	}
	defer func() { _ = dir.Close() }()
	if err := syscall.Flock(int(dir.Fd()), syscall.LOCK_SH); err != nil {
		return nil, err
	}
	defer func() { _ = syscall.Flock(int(dir.Fd()), syscall.LOCK_UN) }()
	archives, err := filepath.Glob(filepath.Join(path+".archive", "*.md"))
	if err != nil {
		return nil, err
	}
	sort.Strings(archives)
	var lines []string
	for _, part := range append(archives, path) {
		content, err := os.ReadFile(part)
		if errors.Is(err, os.ErrNotExist) {
			continue
		}
		if err != nil {
			return nil, err
		}
		for _, line := range strings.SplitAfter(string(content), "\n") {
			if line != "" {
				lines = append(lines, strings.TrimSuffix(line, "\n"))
			}
		}
	}
	return lines, nil
}

func seenCount(cursor string) int {
	tokens := strings.Fields(readText(cursor))
	if len(tokens) == 0 {
		return 0
	}
	count, err := strconv.Atoi(tokens[0])
	if err != nil {
		return 0
	}
	return count
}

func seenKeyList(cursor string) []string {
	tokens := strings.Fields(readText(cursor))
	keys := make([]string, 0, len(tokens))
	for _, token := range tokens {
		if _, err := strconv.Atoi(token); err != nil {
			keys = append(keys, token)
		}
	}
	return keys
}

func readText(path string) string {
	content, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	return string(content)
}

type entry = map[string]any

func messageKey(e entry) string {
	if id, ok := e["msg_id"].(string); ok && id != "" {
		return id
	}
	sum := sha256.Sum256([]byte(fmt.Sprintf("%v|%v|%v", e["from"], e["timestamp"], e["text"])))
	return hex.EncodeToString(sum[:])[:32]
}

func (s mailboxSource) check(_ context.Context, w *waiter) (bool, error) {
	content, err := os.ReadFile(s.path)
	if errors.Is(err, os.ErrNotExist) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	var entries []entry
	if json.Unmarshal(content, &entries) != nil {
		return false, nil
	}
	seen := seenKeyList(s.cursor)
	fresh := make([]entry, 0, len(entries))
	for _, e := range entries {
		if read, _ := e["read"].(bool); !read && !slices.Contains(seen, messageKey(e)) {
			fresh = append(fresh, e)
		}
	}
	if len(fresh) == 0 {
		return false, nil
	}
	fmt.Fprintf(w.Stdout, "MAILBOX %d unread\n", len(fresh))
	keys := make([]string, 0, len(fresh))
	for _, e := range fresh {
		fmt.Fprintf(w.Stdout, "== %v %v\n%v\n", e["from"], e["timestamp"], e["text"])
		keys = append(keys, messageKey(e))
	}
	kept := append(seen, keys...)
	if len(kept) > seenKeys {
		kept = kept[len(kept)-seenKeys:]
	}
	if err := os.WriteFile(s.cursor, []byte(strings.Join(kept, "\n")+"\n"), 0o600); err != nil {
		return false, err
	}
	return true, markRead(s.path, keys, w.Now)
}

func markRead(path string, keys []string, now func() time.Time) error {
	lock := path + ".lock"
	for {
		err := os.Mkdir(lock, 0o750)
		if err == nil {
			break
		}
		if !errors.Is(err, os.ErrExist) {
			return err
		}
		if info, err := os.Stat(lock); err == nil && now().Sub(info.ModTime()) > lockStale {
			_ = os.Remove(lock)
			continue
		}
		time.Sleep(lockPoll)
	}
	defer func() { _ = os.Remove(lock) }()
	content, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	var entries []entry
	if err := json.Unmarshal(content, &entries); err != nil {
		return err
	}
	for _, e := range entries {
		if slices.Contains(keys, messageKey(e)) {
			e["read"] = true
		}
	}
	encoded, err := json.MarshalIndent(entries, "", "  ")
	if err != nil {
		return err
	}
	staged := fmt.Sprintf("%s.lr-wait.%d", path, os.Getpid())
	if err := os.WriteFile(staged, encoded, 0o600); err != nil {
		return err
	}
	return os.Rename(staged, path)
}

func (s cciSource) check(ctx context.Context, w *waiter) (bool, error) {
	argv := []string{"cci", "tail", "--drive", s.drive, "--cursor", s.lane + "-wake", "--seed", s.lane, "--to", s.lane, "--mail"}
	result, err := w.Runner.Run(ctx, shell.Command{Argv: argv, Timeout: readTimeout})
	if err != nil {
		if result.Code == shell.CodeMissingBinary {
			if w.cciMissingAt.IsZero() {
				w.cciMissingAt = w.Now()
			}
			return false, nil
		}
		return false, err
	}
	w.cciMissingAt = time.Time{}
	woke := false
	minute := ""
	for _, line := range strings.Split(strings.TrimRight(result.Stdout, "\n"), "\n") {
		head := recordHead.FindStringSubmatch(line)
		if head != nil && head[2] != "" {
			minute = line
			continue
		}
		if line == "" || !w.fresh(line) {
			continue
		}
		if minute != "" {
			w.held = append(w.held, minute)
			minute = ""
		}
		w.held = append(w.held, clip(line))
		woke = woke || head != nil
	}
	if woke {
		w.flush()
	}
	return woke, nil
}

func (w *waiter) flush() {
	for _, line := range w.held {
		fmt.Fprintln(w.Stdout, line)
	}
	w.held = nil
}

// fresh reports whether line belongs to a record no other source of this wait
// printed, since a record addressed to two waited lanes reaches both.
func (w *waiter) fresh(line string) bool {
	head := recordHead.FindStringSubmatch(line)
	if head == nil {
		return !w.repeat
	}
	w.repeat = w.printed[head[1]]
	w.printed[head[1]] = true
	return !w.repeat
}

func (w *waiter) follow(ctx context.Context) <-chan error {
	errs := make(chan error, len(w.sources))
	for _, s := range w.sources {
		if c, ok := s.(cciSource); ok {
			go w.stream(ctx, c, errs)
		}
	}
	return errs
}

func (w *waiter) stream(ctx context.Context, s cciSource, errs chan<- error) {
	query := url.Values{"drive": {s.drive}, "to": {s.lane}}
	address := w.Stream + "?" + query.Encode()
	lost := w.Now()
	reconnecting := false
	for {
		connected, err := w.readStream(ctx, address, reconnecting)
		if ctx.Err() != nil {
			return
		}
		if connected {
			lost = w.Now()
			reconnecting = true
		}
		if w.Now().Sub(lost) >= cciGrace {
			errs <- fmt.Errorf("the cci daemon at %s gave no stream for %.0f s: %w", w.Stream, cciGrace.Seconds(), err)
			return
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(reconnect):
		}
	}
}

func (w *waiter) readStream(ctx context.Context, address string, reconnecting bool) (bool, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, address, nil)
	if err != nil {
		return false, err
	}
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		return false, err
	}
	defer func() { _ = response.Body.Close() }()
	if response.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(io.LimitReader(response.Body, 1024))
		return false, fmt.Errorf("stream answered %d: %s", response.StatusCode, strings.TrimSpace(string(body)))
	}
	if reconnecting {
		select {
		case w.wake <- struct{}{}:
		default:
		}
	}
	scanner := bufio.NewScanner(response.Body)
	scanner.Buffer(make([]byte, 0, 64*1024), 16*1024*1024)
	for scanner.Scan() {
		if scanner.Text() == "event: record" {
			select {
			case w.wake <- struct{}{}:
			default:
			}
		}
	}
	if err := scanner.Err(); err != nil {
		return true, err
	}
	return true, errors.New("the stream ended")
}

type recursiveError struct{ command string }

func (e *recursiveError) Error() string {
	return fmt.Sprintf("refusing to start %s: it already runs above this process, so each pass would stack another copy", e.command)
}

// start runs the guarded command in its own session, tagged through the
// environment so every descendant, however it regroups or reparents, is
// found by end; a command already in this process's own ancestry is refused.
func (w *waiter) start() (*child, error) {
	if len(w.Command) == 0 {
		return &child{}, nil
	}
	sum := sha256.Sum256([]byte(strings.Join(w.Command, "\x00")))
	digest := hex.EncodeToString(sum[:])[:12]
	chain := slices.DeleteFunc(strings.Split(os.Getenv(boundedTag), ":"), func(s string) bool { return s == "" })
	for _, entry := range chain {
		if strings.HasSuffix(entry, "."+digest) {
			return nil, &recursiveError{strings.Join(w.Command, " ")}
		}
	}
	token := fmt.Sprintf("%d.%s", os.Getpid(), digest)
	cmd := exec.Command(w.Command[0], w.Command[1:]...)
	cmd.Stdin = os.Stdin
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Env = append(os.Environ(), boundedTag+"="+strings.Join(append(chain, token), ":"))
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	started := w.Now()
	if err := cmd.Start(); err != nil {
		return nil, err
	}
	done := make(chan int, 1)
	c := &child{cmd: cmd, done: done, token: token, started: started}
	go func() {
		err := cmd.Wait()
		c.exited.Store(true)
		done <- exitStatus(cmd, err)
	}()
	return c, nil
}

type proc struct {
	ppid int
	age  time.Duration
}

func processTable() map[int]proc {
	out, err := exec.Command("ps", "-A", "-o", "pid=,ppid=,etime=").Output()
	if err != nil {
		return nil
	}
	table := map[int]proc{}
	for _, line := range strings.Split(string(out), "\n") {
		fields := strings.Fields(line)
		if len(fields) != 3 {
			continue
		}
		pid, _ := strconv.Atoi(fields[0])
		ppid, _ := strconv.Atoi(fields[1])
		table[pid] = proc{ppid: ppid, age: elapsed(fields[2])}
	}
	return table
}

func elapsed(etime string) time.Duration {
	days, clock := "0", etime
	if before, after, found := strings.Cut(etime, "-"); found {
		days, clock = before, after
	}
	total := 0
	for _, part := range strings.Split(clock, ":") {
		n, _ := strconv.Atoi(part)
		total = total*60 + n
	}
	d, _ := strconv.Atoi(days)
	return time.Duration(total+d*86400) * time.Second
}

func subtree(table map[int]proc, roots []int) map[int]bool {
	children := map[int][]int{}
	for pid, p := range table {
		children[p.ppid] = append(children[p.ppid], pid)
	}
	seen := map[int]bool{}
	stack := slices.Clone(roots)
	for len(stack) > 0 {
		pid := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		if !seen[pid] {
			seen[pid] = true
			stack = append(stack, children[pid]...)
		}
	}
	return seen
}

func environments(pids []int) map[int]string {
	envs := map[int]string{}
	if len(pids) == 0 {
		return envs
	}
	if info, err := os.Stat("/proc"); err == nil && info.IsDir() {
		for _, pid := range pids {
			if content, err := os.ReadFile(filepath.Join("/proc", strconv.Itoa(pid), "environ")); err == nil {
				envs[pid] = strings.ReplaceAll(string(content), "\x00", " ")
			}
		}
		return envs
	}
	list := make([]string, 0, len(pids))
	for _, pid := range pids {
		list = append(list, strconv.Itoa(pid))
	}
	out, _ := exec.Command("ps", "eww", "-o", "pid=,command=", "-p", strings.Join(list, ",")).Output()
	for _, line := range strings.Split(string(out), "\n") {
		pid, rest, found := strings.Cut(strings.TrimSpace(line), " ")
		if n, err := strconv.Atoi(pid); found && err == nil {
			envs[n] = rest
		}
	}
	return envs
}

// tree is the live child's descendants plus every process started since the
// run that carries its token, with their descendants: on macOS launchd's
// children, where every orphan lands; on Linux any process, since a subreaper
// may adopt one instead.
func (c *child) tree(now time.Time) []int {
	table := processTable()
	live := map[int]bool{}
	if !c.exited.Load() {
		live = subtree(table, []int{c.cmd.Process.Pid})
	}
	_, procErr := os.Stat("/proc")
	anyParent := procErr == nil
	window := now.Sub(c.started) + time.Second
	var candidates []int
	for pid, p := range table {
		if !live[pid] && p.age <= window && (anyParent || p.ppid == 1) {
			candidates = append(candidates, pid)
		}
	}
	var carriers []int
	for pid, env := range environments(candidates) {
		if match := taggedChain.FindStringSubmatch(env); match != nil && slices.Contains(strings.Split(match[1], ":"), c.token) {
			carriers = append(carriers, pid)
		}
	}
	victims := subtree(table, carriers)
	for pid := range live {
		victims[pid] = true
	}
	delete(victims, os.Getpid())
	return slices.Sorted(maps.Keys(victims))
}

func signalAll(pids []int, sig syscall.Signal) {
	for _, pid := range pids {
		_ = syscall.Kill(pid, sig)
	}
}

func (c *child) alive(pid int) bool {
	if pid == c.cmd.Process.Pid {
		return !c.exited.Load()
	}
	return syscall.Kill(pid, 0) == nil
}

func exitStatus(cmd *exec.Cmd, err error) int {
	if err == nil {
		return 0
	}
	if status, ok := cmd.ProcessState.Sys().(syscall.WaitStatus); ok && status.Signaled() {
		return 128 + int(status.Signal())
	}
	return cmd.ProcessState.ExitCode()
}

// end sends SIGTERM to the guarded command's whole tree, then SIGKILL to
// whatever is still alive after the grace, and reaps the command.
func (c *child) end() {
	if c.cmd == nil {
		return
	}
	victims := c.tree(time.Now())
	signalAll(victims, syscall.SIGTERM)
	deadline := time.Now().Add(childGrace)
	for time.Now().Before(deadline) && slices.ContainsFunc(victims, c.alive) {
		time.Sleep(treePoll)
	}
	survivors := slices.DeleteFunc(slices.Clone(victims), func(pid int) bool { return !c.alive(pid) })
	if len(survivors) > 0 {
		signalAll(append(survivors, c.tree(time.Now())...), syscall.SIGKILL)
	}
	<-c.done
}
