package shell

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
	"unicode/utf8"

	"github.com/yasyf/cc-skills/plugins/long-running/desk/pyre"
)

var TransientFailure = pyre.MustCompile("merge_queue.py:TRANSIENT_FAILURE", `left no result, a transient failure|TLS handshake timeout|i/o timeout|context deadline exceeded|urlopen error|Connection reset|Connection refused|reset by peer|Remote end closed|Temporary failure in name resolution|nodename nor servname|Name or service not known|HTTP(?: Error)? 5\d\d|[Oo]peration timed out|Failed to connect to .* port 443|Recv failure|Could not resolve host`, 32)

type flight struct {
	name    string
	started float64
}

// Counter is Python's collections.Counter over float tallies: it keeps first-increment order so ties sort as most_common does.
type Counter struct {
	order  []string
	counts map[string]float64
}

func (c *Counter) Add(key string, amount float64) {
	if c.counts == nil {
		c.counts = map[string]float64{}
	}
	if _, ok := c.counts[key]; !ok {
		c.order = append(c.order, key)
	}
	c.counts[key] += amount
}

func (c *Counter) Get(key string) float64 {
	return c.counts[key]
}

// MostCommon is Counter.most_common(n): highest first, ties in first-increment order.
func (c *Counter) MostCommon(n int) []string {
	keys := append([]string(nil), c.order...)
	sortStable(keys, func(a, b string) bool { return c.counts[a] > c.counts[b] })
	if n >= 0 && len(keys) > n {
		keys = keys[:n]
	}
	return keys
}

func sortStable(keys []string, less func(a, b string) bool) {
	for i := 1; i < len(keys); i++ {
		for j := i; j > 0 && less(keys[j], keys[j-1]); j-- {
			keys[j], keys[j-1] = keys[j-1], keys[j]
		}
	}
}

// Real is the Shell a production landing desk runs on.
type Real struct {
	started    time.Time
	main       uint64
	spending   sync.Mutex
	inProgress map[uint64]flight
	spent      Counter
	called     Counter
	waited     Counter
}

// NewReal returns the production Shell; the goroutine that calls it is the pass's own, as Python's main thread is.
func NewReal() *Real {
	return &Real{started: time.Now(), main: goroutine(), inProgress: map[uint64]flight{}}
}

// goroutine is the calling goroutine's id, parsed from its stack header, since Go exposes no thread identity
// and Python's Shell keys in-flight commands and the pass's own waits by thread.
func goroutine() uint64 {
	header := make([]byte, 64)
	header = header[:runtime.Stack(header, false)]
	id, _ := strconv.ParseUint(string(bytes.Fields(header)[1]), 10, 64)
	return id
}

func (s *Real) Run(argv []string, stdin *string, env map[string]string, cwd string) Done {
	return s.repeat(argv, func() Done { return s.runOnce(argv, stdin, env, cwd) })
}

func (s *Real) CciQuery(argv []string, env map[string]string, cwd string) Done {
	return s.repeat(argv, func() Done { return s.cciQueryOnce(argv, env, cwd) })
}

func (s *Real) repeat(argv []string, operation func() Done) Done {
	done := operation()
	for _, wait := range MissingBackoff {
		if done.Code != MissingBinary {
			break
		}
		s.Sleep(float64(wait))
		done = operation()
	}
	for _, pause := range Pauses(argv) {
		if done.Code == 0 || !(done.Code == TimedOut || TransientFailure.Search(done.Err+done.Out) != nil) {
			break
		}
		s.Stderr(fmt.Sprintf("RETRIED %s in %ds after: %s\n", CommandName(argv), pause, Clip(lastLine(done), 300)))
		s.Sleep(float64(pause))
		done = operation()
	}
	return done
}

func lastLine(done Done) string {
	text := done.Err
	if text == "" {
		text = done.Out
	}
	lines := SplitLines(strings.TrimSpace(text))
	if len(lines) == 0 {
		return fmt.Sprintf("exit %d", done.Code)
	}
	return lines[len(lines)-1]
}

func (s *Real) measuring(argv []string, operation func(timeout int) Done) Done {
	timeout := TimeBound(argv)
	started := s.Monotonic()
	id := goroutine()
	s.spending.Lock()
	s.inProgress[id] = flight{CommandName(argv), started}
	s.spending.Unlock()
	defer func() {
		s.spending.Lock()
		defer s.spending.Unlock()
		delete(s.inProgress, id)
		family, took := CommandFamily(argv), s.Monotonic()-started
		s.spent.Add(family, took)
		s.called.Add(family, 1)
		if id == s.main {
			s.waited.Add(family, took)
		}
	}()
	return operation(timeout)
}

func (s *Real) runOnce(argv []string, stdin *string, env map[string]string, cwd string) Done {
	return s.measuring(argv, func(timeout int) Done {
		feed, err := os.CreateTemp("", "desk-feed")
		if err != nil {
			panic(err)
		}
		defer os.Remove(feed.Name())
		defer feed.Close()
		if stdin != nil {
			if _, err := feed.WriteString(*stdin); err != nil {
				panic(err)
			}
			if _, err := feed.Seek(0, 0); err != nil {
				panic(err)
			}
		}
		ctx, cancel := context.WithTimeout(context.Background(), time.Duration(timeout)*time.Second)
		defer cancel()
		command := Command(argv, s.Getenv)
		cmd := exec.CommandContext(ctx, command[0], command[1:]...)
		cmd.Stdin = feed
		cmd.Dir = cwd
		cmd.Env = environ(env, map[string]string{DeadlineEnv: fmt.Sprintf("%d:%.3f", os.Getpid(), float64(time.Now().UnixNano())/1e9+float64(timeout-ReapSeconds))})
		cmd.WaitDelay = time.Second
		var stdout, stderr bytes.Buffer
		cmd.Stdout, cmd.Stderr = &stdout, &stderr
		err = cmd.Run()
		if ctx.Err() == context.DeadlineExceeded || signaled(err, syscall.SIGALRM) {
			s.Stderr(fmt.Sprintf("TIMED-OUT after %ds, skipped: %s\n", timeout, CommandName(argv)))
			return Done{TimedOut, "", fmt.Sprintf("%s timed out after %ds and was killed", strings.Join(argv, " "), timeout)}
		}
		var exit *exec.ExitError
		switch {
		case err == nil:
			return Done{0, Replace(stdout.Bytes()), Replace(stderr.Bytes())}
		case errors.As(err, &exit):
			return Done{returnCode(exit), Replace(stdout.Bytes()), Replace(stderr.Bytes())}
		case errors.Is(err, exec.ErrNotFound) || errors.Is(err, fs.ErrNotExist):
			return Done{MissingBinary, "", fmt.Sprintf("%s is not on PATH", argv[0])}
		}
		panic(err)
	})
}

func signaled(err error, signal syscall.Signal) bool {
	var exit *exec.ExitError
	if !errors.As(err, &exit) {
		return false
	}
	status, ok := exit.Sys().(syscall.WaitStatus)
	return ok && status.Signaled() && status.Signal() == signal
}

func returnCode(exit *exec.ExitError) int {
	if status, ok := exit.Sys().(syscall.WaitStatus); ok && status.Signaled() {
		return -int(status.Signal())
	}
	return exit.ExitCode()
}

func environ(layers ...map[string]string) []string {
	merged := os.Environ()
	for _, layer := range layers {
		for name, value := range layer {
			merged = append(merged, name+"="+value)
		}
	}
	return merged
}

// Command is release.command: argv with cci and ccx replaced by their plugin's shim when it is installed.
func Command(argv []string, getenv func(string) string) []string {
	plugin, ok := Shims[argv[0]]
	if !ok {
		return argv
	}
	config := getenv("CLAUDE_CONFIG_DIR")
	if config == "" {
		config = filepath.Join(getenv("HOME"), ".claude")
	}
	shim := filepath.Join(config, "plugins", "marketplaces", plugin, "plugin", "bin", argv[0])
	if _, err := os.Stat(shim); err != nil {
		return argv
	}
	return append([]string{shim}, argv[1:]...)
}

// Replace decodes `raw` as Python's bytes.decode(errors="replace") does: each maximal invalid subpart becomes one U+FFFD.
func Replace(raw []byte) string {
	if utf8.Valid(raw) {
		return string(raw)
	}
	var out strings.Builder
	for len(raw) > 0 {
		r, size := utf8.DecodeRune(raw)
		if r != utf8.RuneError || size > 1 {
			out.Write(raw[:size])
			raw = raw[size:]
			continue
		}
		out.WriteRune(utf8.RuneError)
		raw = raw[invalidSubpart(raw):]
	}
	return out.String()
}

func invalidSubpart(raw []byte) int {
	lead := raw[0]
	var need int
	low, high := byte(0x80), byte(0xBF)
	switch {
	case lead >= 0xC2 && lead <= 0xDF:
		need = 1
	case lead == 0xE0:
		need, low = 2, 0xA0
	case lead == 0xED:
		need, high = 2, 0x9F
	case lead >= 0xE1 && lead <= 0xEF:
		need = 2
	case lead == 0xF0:
		need, low = 3, 0x90
	case lead == 0xF4:
		need, high = 3, 0x8F
	case lead >= 0xF1 && lead <= 0xF3:
		need = 3
	default:
		return 1
	}
	size := 1
	for i := 1; i <= need && i < len(raw); i++ {
		if raw[i] < low || raw[i] > high {
			break
		}
		low, high = 0x80, 0xBF
		size++
	}
	return size
}

func (s *Real) cciQueryOnce(argv []string, env map[string]string, cwd string) Done {
	return s.measuring(argv, func(timeout int) Done {
		done, expired := NativeTail(argv, timeout, env, cwd, s.Getenv, s.Monotonic)
		if expired {
			s.Stderr(fmt.Sprintf("TIMED-OUT after %ds, skipped: %s\n", timeout, CommandName(argv)))
			return Done{TimedOut, "", fmt.Sprintf("%s timed out after %ds", strings.Join(argv, " "), timeout)}
		}
		return done
	})
}

func (s *Real) PassCost() string {
	s.spending.Lock()
	waited, spent, called := s.waited, s.spent, s.called
	s.spent, s.called, s.waited = Counter{}, Counter{}, Counter{}
	s.spending.Unlock()
	return FormatPassCost(waited, spent, called)
}

// FormatPassCost is the text of Shell.pass_cost over the tallies a pass kept.
func FormatPassCost(waited, spent, called Counter) string {
	var waits []string
	for _, family := range waited.MostCommon(4) {
		waits = append(waits, fmt.Sprintf("%s %.0fs", family, waited.Get(family)))
	}
	head := "nothing"
	if len(waits) > 0 {
		head = strings.Join(waits, ", ")
	}
	var costs []string
	for _, family := range spent.MostCommon(8) {
		costs = append(costs, fmt.Sprintf("%s %.0fs in %d", family, spent.Get(family), int(called.Get(family))))
	}
	return fmt.Sprintf("the pass waited on %s; all threads: %s", head, strings.Join(costs, ", "))
}

func (s *Real) InFlight() (string, float64, bool) {
	now := s.Monotonic()
	s.spending.Lock()
	defer s.spending.Unlock()
	if own, ok := s.inProgress[s.main]; ok {
		return own.name, now - own.started, true
	}
	var longest flight
	found := false
	for _, f := range s.inProgress {
		if !found || now-f.started > now-longest.started {
			longest, found = f, true
		}
	}
	return longest.name, now - longest.started, found
}

func (s *Real) Adopt(values map[string]string) {
	for name, value := range values {
		if err := os.Setenv(name, value); err != nil {
			panic(err)
		}
	}
}

// TODO: match drive.reexec's descriptor handling when the cutover makes Python exec into this binary.
func (s *Real) Reexec(argv []string) {
	os.Stdout.Sync()
	os.Stderr.Sync()
	path, err := exec.LookPath(argv[0])
	if err != nil {
		panic(err)
	}
	panic(syscall.Exec(path, argv, os.Environ()))
}

func (s *Real) Launchctl(args ...string) Done {
	ctx, cancel := context.WithTimeout(context.Background(), QuickSeconds*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, "launchctl", args...)
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	err := cmd.Run()
	var exit *exec.ExitError
	switch {
	case ctx.Err() == context.DeadlineExceeded:
		panic(fmt.Sprintf("Command %v timed out after %d seconds", append([]string{"launchctl"}, args...), QuickSeconds))
	case err == nil:
		return Done{0, Replace(stdout.Bytes()), Replace(stderr.Bytes())}
	case errors.As(err, &exit):
		return Done{returnCode(exit), Replace(stdout.Bytes()), Replace(stderr.Bytes())}
	case errors.Is(err, exec.ErrNotFound):
		return Done{MissingBinary, "", "launchctl is not on PATH"}
	}
	panic(err)
}

type child struct {
	cmd  *exec.Cmd
	mu   sync.Mutex
	code *int
}

func (c *child) Pid() int { return c.cmd.Process.Pid }

func (c *child) Poll() (int, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.code == nil {
		return 0, false
	}
	return *c.code, true
}

func (s *Real) Spawn(argv []string, out string, env map[string]string, appending bool) Process {
	if err := os.MkdirAll(filepath.Dir(out), 0o755); err != nil {
		panic(err)
	}
	flags := os.O_CREATE | os.O_WRONLY | os.O_TRUNC
	if appending {
		flags = os.O_CREATE | os.O_WRONLY | os.O_APPEND
	}
	sink, err := os.OpenFile(out, flags, 0o644)
	if err != nil {
		panic(err)
	}
	defer sink.Close()
	command := Command(argv, s.Getenv)
	cmd := exec.Command(command[0], command[1:]...)
	cmd.Stdout, cmd.Stderr = sink, sink
	cmd.Env = environ(env)
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	if err := cmd.Start(); err != nil {
		panic(err)
	}
	spawned := &child{cmd: cmd}
	go func() {
		err := cmd.Wait()
		code := 0
		var exit *exec.ExitError
		if errors.As(err, &exit) {
			code = returnCode(exit)
		}
		spawned.mu.Lock()
		spawned.code = &code
		spawned.mu.Unlock()
	}()
	return spawned
}

func (s *Real) RunsJob(pid int, directory string) bool {
	out, _ := exec.Command("ps", "-ww", "-o", "command=", "-p", strconv.Itoa(pid)).Output()
	return strings.Contains(string(out), directory)
}

func (s *Real) Stat(path string) (string, bool) {
	info, err := os.Stat(path)
	if errors.Is(err, fs.ErrNotExist) {
		return "", false
	}
	if err != nil {
		panic(err)
	}
	return fmt.Sprintf("[%d, %d]", info.Size(), info.ModTime().UnixNano()), true
}

func (s *Real) Now() time.Time { return time.Now().UTC() }

func (s *Real) Monotonic() float64 { return time.Since(s.started).Seconds() }

func (s *Real) Sleep(seconds float64) { time.Sleep(time.Duration(seconds * float64(time.Second))) }

func (s *Real) Wait(stop <-chan struct{}, seconds float64) bool {
	select {
	case <-stop:
		return true
	case <-time.After(time.Duration(seconds * float64(time.Second))):
		return false
	}
}

func (s *Real) Load() float64 { return loadAverage() }

func (s *Real) Cores() int { return runtime.NumCPU() }

func (s *Real) Env(name string) string { return os.Getenv(name) }

func (s *Real) Getenv(name string) string { return os.Getenv(name) }

func (s *Real) Pid() int { return os.Getpid() }

func (s *Real) Stdout(text string) { os.Stdout.WriteString(text) }

func (s *Real) Stderr(text string) { os.Stderr.WriteString(text) }
