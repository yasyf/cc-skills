// Package runner is the Go port of desk-runner.py's Runner and its landing loop, run_landing.
package runner

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"runtime/debug"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/yasyf/cc-skills/plugins/long-running/desk/shell"
)

const (
	Root           = "root"
	RunnerLane     = "desk-runner"
	LandingLane    = "desk-landing"
	Watchdog       = "drive-watchdog"
	CciText        = 400
	AliveEvery     = time.Minute
	RaisedBackoff  = time.Minute
	WakeSeconds    = 0.25
	DefaultSeconds = 180
)

var pacific = func() *time.Location {
	location, err := time.LoadLocation("America/Los_Angeles")
	if err != nil {
		panic(err)
	}
	return location
}()

// Config is the part of desk-runner.py's Config the landing loop reads.
type Config struct {
	Source    string
	Store     string
	Drive     string
	DeskInbox string
	Receipts  string
	Landing   map[string]any
	DeskEnv   map[string]map[string]string
}

// LoadConfig is Config.load.
func LoadConfig(path string, sh shell.Shell) Config {
	source, err := filepath.Abs(expand(path, sh))
	if err != nil {
		panic(err)
	}
	if resolved, err := filepath.EvalSymlinks(source); err == nil {
		source = resolved
	}
	raw := readJSON(source)
	orca := raw["orca"].(map[string]any)
	config := Config{
		Source:    source,
		Drive:     raw["drive"].(string),
		DeskInbox: expand(orca["desk_inbox"].(string), sh),
		Receipts:  filepath.Join(sh.Getenv("HOME"), ".claude", "scratch", "orca-launch", orca["run"].(string)),
		DeskEnv:   map[string]map[string]string{},
	}
	if store, ok := raw["store"].(string); ok && store != "" {
		config.Store = expand(store, sh)
	}
	if landing, ok := raw["landing"].(map[string]any); ok {
		config.Landing = landing
	}
	for _, desk := range []string{"orca", "landing"} {
		env := map[string]string{}
		if section, ok := raw[desk].(map[string]any); ok {
			if values, ok := section["env"].(map[string]any); ok {
				for name, value := range values {
					env[name] = value.(string)
				}
			}
		}
		config.DeskEnv[desk] = env
	}
	return config
}

func expand(path string, sh shell.Shell) string {
	if path == "~" || strings.HasPrefix(path, "~/") {
		return sh.Getenv("HOME") + path[1:]
	}
	return path
}

func readJSON(path string) map[string]any {
	text, err := os.ReadFile(path)
	if err != nil {
		panic(err)
	}
	var raw map[string]any
	if err := json.Unmarshal(text, &raw); err != nil {
		panic(err)
	}
	return raw
}

// Runner is the landing loop's share of desk-runner.py's Runner.
type Runner struct {
	Shell       shell.Shell
	Config      Config
	Version     string
	PassIO      *OnePassIO
	Passes      map[string]int
	Passing     map[string]bool
	raised      map[string]raisedAt
	alivePosted map[string]time.Time
}

type raisedAt struct {
	text string
	pass int
}

// New returns the Runner for `config`, running plugin version `version`.
func New(sh shell.Shell, config Config, version string) *Runner {
	trace, _ := config.Landing["trace_pass_io"].(bool)
	return &Runner{Shell: sh, Config: config, Version: version, PassIO: &OnePassIO{enabled: trace, seen: map[string]bool{}}, Passes: map[string]int{}, Passing: map[string]bool{}, raised: map[string]raisedAt{}, alivePosted: map[string]time.Time{}}
}

func (r *Runner) Now() time.Time { return r.Shell.Now() }

// Watchdog is the drive's own watchdog lane, by drive.
func (r *Runner) Watchdog() string { return Watchdog + "@" + r.Config.Drive }

func (r *Runner) runnerLog(desk string) string {
	return filepath.Join(filepath.Dir(filepath.Dir(r.Config.DeskInbox)), desk+"-runner.log")
}

func (r *Runner) restartCommand(desk string) string {
	var env strings.Builder
	// TODO: keep runner.json's member order, as Python's dict does, once the order-preserving JSON decoder lands.
	for _, name := range sortedKeys(r.Config.DeskEnv[desk]) {
		env.WriteString(name + "=" + Quote(r.Config.DeskEnv[desk][name]) + " ")
	}
	prefix := ""
	if env.Len() > 0 {
		prefix = "env " + env.String()
	}
	return fmt.Sprintf("nohup %sdesk-runner.py run --config %s --desk %s >> %s 2>&1 < /dev/null &", prefix, r.Config.Source, desk, r.runnerLog(desk))
}

var unsafe = regexp.MustCompile(`[^\w@%+=:,./-]`)

// Quote is shlex.quote.
func Quote(text string) string {
	if text == "" {
		return "''"
	}
	if !unsafe.MatchString(text) {
		return text
	}
	return "'" + strings.ReplaceAll(text, "'", `'"'"'`) + "'"
}

// PostResult is cci.PostResult.
type PostResult struct {
	shell.Done
	Records []Posted
}

// Posted is cci.Posted: one record's internal sequence and human number.
type Posted struct {
	Seq int `json:"seq"`
	Num int `json:"num"`
}

// CciPost is Runner.cci_post.
func (r *Runner) CciPost(kind, text, to, topic, key, message, pr, recordID string, flags []string, drive, lane string) PostResult {
	if drive == "" {
		drive = r.Config.Drive
	}
	if lane == "" {
		lane = RunnerLane
	}
	argv := append([]string{"cci", "post", "--drive", drive, "--lane", lane, "--kind", kind, "--to", to, "--topic", topic}, flags...)
	if pr != "" {
		argv = append(argv, "--pr", pr)
	}
	if recordID != "" {
		argv = append(argv, "--id", recordID)
	}
	long := utf8.RuneCountInString(text) > CciText
	if long || message != "" {
		digest := sha256.Sum256([]byte(key))
		body := filepath.Join(r.Config.Receipts, "cci", hex.EncodeToString(digest[:])[:16]+".txt")
		if err := os.MkdirAll(filepath.Dir(body), 0o755); err != nil {
			panic(err)
		}
		var parts []string
		for _, part := range []string{text, strings.TrimSpace(message)} {
			if part != "" {
				parts = append(parts, part)
			}
		}
		if err := os.WriteFile(body, []byte(strings.Join(parts, "\n\n")+"\n"), 0o644); err != nil {
			panic(err)
		}
		argv = append(argv, "--path", body)
	}
	if long {
		text = shell.Clip(text, CciText-1) + "…"
	}
	// TODO: forget the runner's cci tail here once the cci Tail is ported.
	done := r.Shell.Run(append(argv, "--text", text, "--json"), nil, nil, "")
	if done.Code != 0 {
		return PostResult{Done: done}
	}
	var records []Posted
	for _, line := range shell.SplitLines(done.Out) {
		var record Posted
		if err := json.Unmarshal([]byte(line), &record); err != nil {
			return PostResult{Done: shell.Done{Code: 1, Out: done.Out, Err: fmt.Sprintf("%s\ncci: invalid post receipt: %s: %s", done.Err, err, strings.TrimSpace(done.Out))}}
		}
		records = append(records, record)
	}
	return PostResult{Done: done, Records: records}
}

// PostAlive is Runner.post_alive.
func (r *Runner) PostAlive(desk string) {
	if r.Now().Sub(r.alivePosted[desk]) < AliveEvery {
		return
	}
	r.alivePosted[desk] = r.Now()
	name, seconds, flying := r.Shell.InFlight()
	doing := strconv.Itoa(r.Passes[desk])
	current := ""
	if flying {
		doing = fmt.Sprintf("%d running `%s` for %dm%02ds", r.Passes[desk]+1, name, int(seconds)/60, int(seconds)%60)
		current = name
	} else if r.Passing[desk] {
		current = desk + " pass"
	}
	beat := [][2]string{{"pid", strconv.Itoa(r.Shell.Pid())}, {"version", r.Version}, {"config", r.Config.Source}, {"terminal", r.Shell.Env("ORCA_TERMINAL_HANDLE")}, {"restart", r.restartCommand(desk)}, {"flight", current}}
	var attrs []string
	for _, pair := range beat {
		attrs = append(attrs, "--attr", pair[0]+"="+pair[1])
	}
	r.CciPost("state", fmt.Sprintf("%s alive %s pass %s", desk, r.Now().In(pacific).Format("3:04 PM"), doing), r.Watchdog(), desk, "alive:"+desk, "", "", "", attrs, "", "")
}

// KeepAlive is Runner.keep_alive: closing the returned channel stops the heartbeat.
func (r *Runner) KeepAlive(desk string) chan struct{} {
	stop := make(chan struct{})
	r.PostAlive(desk)
	go func() {
		for !r.Shell.Wait(stop, AliveEvery.Seconds()) {
			r.PostAlive(desk)
		}
	}()
	return stop
}

// Beat is Runner.beat.
func (r *Runner) Beat(desk string) {
	r.Passes[desk]++
	r.PostAlive(desk)
}

// Pinned is Runner.pinned: the version runner.json pins, read fresh.
func (r *Runner) Pinned() string {
	pinned, _ := readJSON(r.Config.Source)["version"].(string)
	return pinned
}

// Moved is Runner.moved.
func (r *Runner) Moved() bool {
	pinned := r.Pinned()
	return pinned != "" && pinned != r.Version
}

// Reexec is Runner.reexec.
func (r *Runner) Reexec(desk string) {
	r.Shell.Reexec([]string{runnerBin(r.Shell), "run", "--config", r.Config.Source, "--desk", desk})
}

func runnerBin(sh shell.Shell) string {
	return filepath.Join(sh.Getenv(PluginRootEnv), "bin", "desk-runner.py")
}

// StopMarker is Runner.stop_marker for this runner's own pid.
func (r *Runner) StopMarker() string {
	return fmt.Sprintf("%s.stop.%d", r.Config.Source, r.Shell.Pid())
}

// StopRequested is Runner.stop_requested.
func (r *Runner) StopRequested() bool {
	text, err := os.ReadFile(r.StopMarker())
	return err == nil && strings.TrimSpace(string(text)) == strconv.Itoa(r.Shell.Pid())
}

// Rest is Runner.rest: sleep `seconds` in WakeSeconds steps, true once a stop is requested.
func (r *Runner) Rest(seconds float64) bool {
	for range int(seconds / WakeSeconds) {
		if r.StopRequested() {
			return true
		}
		r.Shell.Sleep(WakeSeconds)
	}
	return r.StopRequested()
}

// StopRequestedError is desk-runner.py's StopRequested.
type StopRequestedError struct{}

func (StopRequestedError) Error() string { return "" }

// Checkpoint is Runner.checkpoint.
func (r *Runner) Checkpoint() {
	if r.StopRequested() {
		panic(StopRequestedError{})
	}
}

// PassRaised is Runner.pass_raised: true when the pass before raised the same.
func (r *Runner) PassRaised(desk string, failure any, stack []byte) bool {
	r.Shell.Stderr(fmt.Sprintf("%v\n%s\n", failure, stack))
	raised := describe(failure)
	again := r.raised[desk] == raisedAt{raised, r.Passes[desk] - 1}
	r.raised[desk] = raisedAt{raised, r.Passes[desk]}
	then := "the runner goes on with its next pass"
	if again {
		then = fmt.Sprintf("it raised the same on the pass before, so the runner restarts itself in %d s", int(RaisedBackoff.Seconds()))
	}
	r.CciPost("blocker", fmt.Sprintf("%s runner pass raised %s; %s", desk, shell.Clip(raised, 240), then), Root, desk, "pass-raised:"+desk, "", "", "", nil, "", "")
	return again
}

func describe(failure any) string {
	if raised, ok := failure.(*shell.Raised); ok {
		return raised.Error()
	}
	if err, ok := failure.(error); ok {
		return fmt.Sprintf("%T: %s", err, err)
	}
	return fmt.Sprintf("%T: %v", failure, failure)
}

func stack() []byte { return debug.Stack() }

func sortedKeys[V any](values map[string]V) []string {
	keys := make([]string, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	for i := 1; i < len(keys); i++ {
		for j := i; j > 0 && keys[j] < keys[j-1]; j-- {
			keys[j], keys[j-1] = keys[j-1], keys[j]
		}
	}
	return keys
}
