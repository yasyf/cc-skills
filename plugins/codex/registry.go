package main

import (
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"
)

type cmdSpec struct {
	Argv     []string `json:"argv"`
	Question string   `json:"question"`
	Reply    string   `json:"reply"`
	ReplyTmp string   `json:"reply_tmp"`
	Log      string   `json:"log"`
	// Owner/Session/Scope/ClaudePID are captured at --dispatch time (the worker is
	// orphaned to PID 1 and cannot derive them) so the worker can wake the owner
	// subagent on completion. Absent for a foreground/top-level dispatch.
	Owner     string `json:"owner,omitempty"`
	Session   string `json:"session,omitempty"`
	Scope     string `json:"scope,omitempty"`
	ClaudePID int    `json:"claude_pid,omitempty"`
}

type laneInfo struct {
	state     string
	reply     string
	log       string
	replySize int64
	excerpt   string
	qcount    int
	pid       *int
	info      map[string]any
}

// Unknown fields are omitted, never null: consumers read these records with
// `.get(key, default)` / jq defaults, and a present-but-null key defeats both.
type psRecord struct {
	Dir       string   `json:"dir"`
	State     string   `json:"state"`
	Pid       *int     `json:"pid,omitempty"`
	Started   *float64 `json:"started,omitempty"`
	LogAgeS   *float64 `json:"log_age_s,omitempty"`
	Cwd       *string  `json:"cwd,omitempty"`
	Session   *string  `json:"session,omitempty"`
	ReplyFile *string  `json:"reply_file,omitempty"`
}

// runsBase: the fixed registry root; every run lands here so the filesystem is the
// registry. $CODEX_ASK_RUNS_DIR overrides but must clear the same guards as -s — a
// relative or in-repo value would let --ps prune rmtree arbitrary source dirs.
func runsBase() string {
	override := os.Getenv("CODEX_ASK_RUNS_DIR")
	var base string
	if override != "" {
		if !strings.HasPrefix(override, "/") {
			die("codex-ask: CODEX_ASK_RUNS_DIR must be an absolute path", 2)
		}
		rejectOutsideScratch(override, "CODEX_ASK_RUNS_DIR")
		base = override
	} else {
		xdg := os.Getenv("XDG_CACHE_HOME")
		if xdg == "" {
			home, _ := os.UserHomeDir()
			xdg = filepath.Join(home, ".cache")
		}
		base = filepath.Join(xdg, "codex-ask", "runs")
	}
	_ = os.MkdirAll(base, 0o755) //nolint:gosec // 0o755 matches the Python spec's runs-dir mode
	return base
}

func mintScratch(prefix string) string {
	d, err := os.MkdirTemp(runsBase(), prefix+".")
	if err != nil {
		die("codex-ask: cannot mint scratch dir: "+err.Error(), 1)
	}
	return d
}

func acquireLaneLock(sdir string, exclusive bool) *os.File {
	f, err := os.OpenFile(join(sdir, "lane.lock"), os.O_CREATE|os.O_RDWR, 0o600) //nolint:gosec // per-lane coordination file
	if err != nil {
		die("codex-ask: cannot open lane lock: "+err.Error(), 1)
	}
	how := syscall.LOCK_SH
	if exclusive {
		how = syscall.LOCK_EX
	}
	if err := syscall.Flock(int(f.Fd()), how); err != nil {
		_ = f.Close()
		die("codex-ask: cannot acquire lane lock: "+err.Error(), 1)
	}
	return f
}

func releaseLaneLock(f *os.File) {
	if err := f.Close(); err != nil {
		die("codex-ask: cannot release lane lock: "+err.Error(), 1)
	}
}

// atomicWrite: a unique per-writer temp file, so two concurrent writers (e.g. two
// --await recoveries) never race on a shared "<name>.tmp".
func atomicWrite(path, text string) error {
	f, err := os.CreateTemp(filepath.Dir(path), filepath.Base(path)+".*.tmp")
	if err != nil {
		return err
	}
	tmp := f.Name()
	if _, err := f.WriteString(text); err != nil {
		_ = f.Close()
		_ = os.Remove(tmp) //nolint:gosec // best-effort cleanup of this writer's own temp file
		return err
	}
	if err := f.Close(); err != nil {
		_ = os.Remove(tmp) //nolint:gosec // best-effort cleanup of this writer's own temp file
		return err
	}
	if err := os.Rename(tmp, path); err != nil { //nolint:gosec // renames into the tool's own dispatch path, by design
		_ = os.Remove(tmp) //nolint:gosec // best-effort cleanup of this writer's own temp file
		return err
	}
	return nil
}

func jsonEscape(s string) string {
	s = strings.ReplaceAll(s, "\\", "\\\\")
	s = strings.ReplaceAll(s, "\"", "\\\"")
	s = strings.ReplaceAll(s, "\t", "\\t")
	s = strings.ReplaceAll(s, "\r", "\\r")
	s = strings.ReplaceAll(s, "\n", "\\n")
	// Strip the remaining raw C0 controls the JSONL can't carry.
	var b strings.Builder
	for _, c := range s {
		if c >= 0x20 {
			b.WriteRune(c)
		}
	}
	return b.String()
}

func shlexQuote(s string) string {
	if s == "" {
		return "''"
	}
	safe := true
	for _, c := range s {
		safeChar := c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' ||
			strings.ContainsRune("@%+=:,./-_", c)
		if !safeChar {
			safe = false
			break
		}
	}
	if safe {
		return s
	}
	return "'" + strings.ReplaceAll(s, "'", "'\"'\"'") + "'"
}

// classify: derive a run dir's state from disk alone — the shared truth for
// --collect and --ps. Read-only; never blocks or inlines reply contents.
func classify(d string) laneInfo {
	var r, log, excerpt string
	qcount := 0
	if entries, err := os.ReadDir(d); err == nil {
		for _, e := range entries {
			name := e.Name()
			if strings.HasPrefix(name, "codex-q-") && !strings.HasSuffix(name, ".log") {
				qcount++
			}
		}
	}
	meta := join(d, "meta")
	hasMeta := isFile(meta)
	info := map[string]any{}
	if hasMeta {
		lines := metaLines(meta)
		r = lineAt(lines, 0)
		log = lineAt(lines, 1)
		if len(lines) > 2 {
			var m map[string]any
			if json.Unmarshal([]byte(lines[2]), &m) == nil {
				info = m
			}
		}
		if strings.HasSuffix(log, ".log") {
			q := log[:len(log)-4]
			if isFile(q) {
				excerpt = firstLine(q)
			}
		}
	}
	var replySize int64
	if nonempty(r) {
		replySize = fileSize(r)
	}
	pidFile := join(d, "pid")
	var pid *int
	if isFile(pidFile) {
		if p, ok := readPid(pidFile); ok {
			pid = &p
		}
	}
	alive := isFile(pidFile) && pidAlive(d)
	status := join(d, "status")
	st := ""
	if nonempty(status) {
		st = strings.TrimSpace(readFile(status))
	}

	var state string
	switch {
	case !hasMeta:
		state = "no-run"
	case st != "":
		// A present, non-"0" status is failed unconditionally; turn markers are
		// consulted only when status is "0".
		if st == "0" {
			switch {
			case !nonempty(r):
				state = "failed"
			case turnStarted(log) && !hasCompletedMarker(log):
				state = "died"
			default:
				state = "completed"
			}
		} else {
			state = "failed"
		}
	case !isFile(pidFile):
		state = "pending"
	case alive:
		state = "running"
	case nonempty(r) && (!turnStarted(log) || hasCompletedMarker(log)):
		state = "completed"
	default:
		state = "died"
	}

	// Stale-.tmp sweep: a SIGKILLed worker can't run its cleanup, so reap the
	// orphaned "<reply>.tmp" once the run is no longer in flight.
	if r != "" && state != "running" && state != "pending" {
		_ = os.Remove(r + ".tmp") //nolint:gosec // best-effort sweep of the lane's own staged reply temp
	}
	return laneInfo{state, r, log, replySize, excerpt, qcount, pid, info}
}

func collectLane(d, lane string) {
	li := classify(d)
	awaitCmd := ""
	if !contains(terminal, li.state) {
		awaitCmd = shlexQuote(invokePath) + " --await " + shlexQuote(d)
	}
	record := `{"lane":"` + jsonEscape(lane) + `","state":"` + li.state + `",` +
		`"reply_file":"` + jsonEscape(li.reply) + `","reply_size":` + strconv.FormatInt(li.replySize, 10) + `,` +
		`"log_file":"` + jsonEscape(li.log) + `","question":"` + jsonEscape(li.excerpt) + `",` +
		`"question_files":` + strconv.Itoa(li.qcount)
	if awaitCmd != "" {
		record += `,"await":"` + jsonEscape(awaitCmd) + `"`
	}
	record += "}"
	fmt.Println(record)
}

func collectMode(root string) {
	if !strings.HasPrefix(root, "/") {
		die("codex-ask: --collect needs an absolute lane-root or lane dir", 2)
	}
	rejectOutsideScratch(root, "--collect root")
	if fi, err := os.Stat(root); err != nil || !fi.IsDir() { //nolint:gosec // stats the caller's own --collect scratch root
		die(fmt.Sprintf("codex-ask: --collect: no such directory: %s", root), 2)
	}
	if isFile(join(root, "meta")) || isFile(join(root, "status")) || isFile(join(root, "pid")) {
		collectLane(root, ".")
	} else {
		entries, _ := os.ReadDir(root) // sorted by name
		for _, e := range entries {
			if isDir(join(root, e.Name())) { // is_dir() follows symlinks; a symlinked lane counts
				collectLane(join(root, e.Name()), e.Name())
			}
		}
	}
	os.Exit(0)
}

func runMtime(d string) float64 {
	newest := 0.0
	for _, name := range []string{"status", "meta", "pid"} {
		if fi, err := os.Stat(join(d, name)); err == nil {
			if m := float64(fi.ModTime().UnixNano()) / 1e9; m > newest {
				newest = m
			}
		}
	}
	if newest == 0.0 {
		if fi, err := os.Stat(d); err == nil {
			newest = float64(fi.ModTime().UnixNano()) / 1e9
		} else {
			newest = nowSec()
		}
	}
	return newest
}

// psMode: the registry view. Walk the base, classify every run as one JSONL
// record, expand fan-out containers into lane children, reap only terminal-and-aged
// codex-ask dirs.
func psMode() {
	base := runsBase()
	now := nowSec()
	entries, _ := os.ReadDir(base)
	for _, e := range entries {
		if isDir(join(base, e.Name())) { // follow symlinks, like Python's is_dir()
			psWalk(join(base, e.Name()), now)
		}
	}
	os.Exit(0)
}

func psWalk(d string, now float64) {
	hasRunState := exists(join(d, "meta")) || exists(join(d, "status")) || exists(join(d, "pid"))
	if !hasRunState {
		// A fan-out container holds lane subdirs and no run state of its own:
		// classify its children, never the container itself (and never prune it).
		var subdirs []string
		if entries, err := os.ReadDir(d); err == nil {
			for _, e := range entries {
				if isDir(join(d, e.Name())) { // follow symlinks, like Python's is_dir()
					subdirs = append(subdirs, join(d, e.Name()))
				}
			}
		}
		if len(subdirs) > 0 {
			for _, c := range subdirs {
				psWalk(c, now)
			}
			return
		}
	}
	li := classify(d)
	// Prune-and-skip before emitting: reclassify once more right before rmtree to
	// close the classify->delete TOCTOU (a reuse mid-walk flips it non-terminal).
	if startsWithRunPrefix(filepath.Base(d)) && isFile(join(d, "meta")) &&
		contains(terminal, li.state) && now-runMtime(d) > pruneAgeS &&
		contains(terminal, classify(d).state) {
		_ = os.RemoveAll(d)
		return
	}
	var logAge *float64
	if li.log != "" && exists(li.log) {
		if fi, err := os.Stat(li.log); err == nil {
			a := math.Round((now-float64(fi.ModTime().UnixNano())/1e9)*10) / 10
			logAge = &a
		}
	}
	rec := psRecord{
		Dir:       d,
		State:     li.state,
		Pid:       li.pid,
		Started:   infoFloat(li.info, "ts"),
		LogAgeS:   logAge,
		Cwd:       infoString(li.info, "cwd"),
		Session:   infoString(li.info, "session"),
		ReplyFile: nilIfEmpty(li.reply),
	}
	b, _ := json.Marshal(rec)
	fmt.Println(string(b))
}

// watchMode: the registry stream for a top-level Monitor watch. Emits one JSONL
// psRecord per watched run when it settles (any state but pending/running) and
// exits once all have settled; a run already settled at arm time emits at once.
func watchMode(args []string) {
	all := false
	var targets []string
	for _, a := range args {
		switch {
		case a == "--all":
			all = true
		case strings.HasPrefix(a, "-"):
			usage()
		default:
			targets = append(targets, a)
		}
	}
	if all == (len(targets) > 0) {
		die("codex-ask: --watch takes run/root dirs or --all (one or the other)", 2)
	}
	var watched []string
	if all {
		watched = unsettledRuns()
		if len(watched) == 0 {
			fmt.Println(`{"state":"idle","note":"no pending or running runs to watch"}`)
			os.Exit(0)
		}
	} else {
		for _, tgt := range targets {
			watched = append(watched, watchTargets(tgt)...)
		}
	}
	settled := map[string]bool{}
	for {
		done := true
		for _, d := range watched {
			if settled[d] {
				continue
			}
			li := classify(d)
			if li.state == "pending" || li.state == "running" {
				done = false
				continue
			}
			settled[d] = true
			emitWatchRecord(d, li)
		}
		if done {
			os.Exit(0)
		}
		time.Sleep(2 * time.Second)
	}
}

// watchTargets resolves one --watch operand: a run dir watches itself, a fan-out
// root watches its lane children — the same split --collect makes.
func watchTargets(root string) []string {
	if !strings.HasPrefix(root, "/") {
		die("codex-ask: --watch needs absolute run or root dirs", 2)
	}
	rejectOutsideScratch(root, "--watch target")
	if fi, err := os.Stat(root); err != nil || !fi.IsDir() { //nolint:gosec // stats the caller's own --watch target
		die(fmt.Sprintf("codex-ask: --watch: no such directory: %s", root), 2)
	}
	if isFile(join(root, "meta")) || isFile(join(root, "status")) || isFile(join(root, "pid")) {
		return []string{root}
	}
	var lanes []string
	entries, _ := os.ReadDir(root) // sorted by name
	for _, e := range entries {
		if isDir(join(root, e.Name())) { // is_dir() follows symlinks; a symlinked lane counts
			lanes = append(lanes, join(root, e.Name()))
		}
	}
	if len(lanes) == 0 {
		die(fmt.Sprintf("codex-ask: --watch: %s has no run state and no lane dirs", root), 2)
	}
	return lanes
}

// unsettledRuns walks the registry base like --ps (containers expand into lane
// children) and returns every run still pending or running.
func unsettledRuns() []string {
	var runs []string
	var walk func(d string)
	walk = func(d string) {
		hasRunState := exists(join(d, "meta")) || exists(join(d, "status")) || exists(join(d, "pid"))
		if !hasRunState {
			kids := false
			if entries, err := os.ReadDir(d); err == nil {
				for _, e := range entries {
					if isDir(join(d, e.Name())) { // follow symlinks, like Python's is_dir()
						kids = true
						walk(join(d, e.Name()))
					}
				}
			}
			if kids {
				return
			}
		}
		if st := classify(d).state; st == "pending" || st == "running" {
			runs = append(runs, d)
		}
	}
	base := runsBase()
	entries, _ := os.ReadDir(base)
	for _, e := range entries {
		if isDir(join(base, e.Name())) { // follow symlinks, like Python's is_dir()
			walk(join(base, e.Name()))
		}
	}
	return runs
}

func emitWatchRecord(d string, li laneInfo) {
	now := nowSec()
	var logAge *float64
	if li.log != "" && exists(li.log) {
		if fi, err := os.Stat(li.log); err == nil {
			a := math.Round((now-float64(fi.ModTime().UnixNano())/1e9)*10) / 10
			logAge = &a
		}
	}
	rec := psRecord{
		Dir:       d,
		State:     li.state,
		Pid:       li.pid,
		Started:   infoFloat(li.info, "ts"),
		LogAgeS:   logAge,
		Cwd:       infoString(li.info, "cwd"),
		Session:   infoString(li.info, "session"),
		ReplyFile: nilIfEmpty(li.reply),
	}
	b, _ := json.Marshal(rec)
	fmt.Println(string(b))
}

func mintRootMode(lanes []string) {
	for _, lane := range lanes {
		if lane == "" || lane[0] == '.' || lane[0] == '-' || strings.Contains(lane, "/") || strings.Contains(lane, "\n") {
			die("codex-ask: --mint-root lanes must be plain path segments "+
				"(no /, newlines, empty, or leading . or -): "+lane, 2)
		}
	}
	// Case-insensitive dedup: case-insensitive filesystems collapse review/Review.
	seen := map[string]bool{}
	var dups []string
	for _, lane := range lanes {
		key := strings.ToLower(lane)
		if seen[key] {
			if !contains(dups, lane) {
				dups = append(dups, lane)
			}
		} else {
			seen[key] = true
		}
	}
	if len(dups) > 0 {
		die("codex-ask: --mint-root got duplicate lane names: "+strings.Join(dups, " "), 2)
	}
	root := mintScratch("codex-root")
	for _, lane := range lanes {
		if err := os.MkdirAll(join(root, lane), 0o755); err != nil { //nolint:gosec // 0o755 matches the Python spec's scratch-dir mode
			_ = os.RemoveAll(root)
			os.Exit(1)
		}
	}
	// All-or-nothing: nothing prints until the whole roster exists.
	fmt.Printf("ROOT: %s\n", root)
	for _, lane := range lanes {
		fmt.Printf("LANE: %s\n", join(root, lane))
	}
	os.Exit(0)
}

func rejectOutsideScratch(path, flag string) {
	if top := repoToplevelOf(path); top != "" {
		die(fmt.Sprintf("codex-ask: %s must be outside the repository at %s (lanes are never minted in-repo)", flag, top), 2)
	}
	home := os.Getenv("HOME")
	if home != "" && strings.HasPrefix(path+"/", strings.TrimRight(home, "/")+"/.claude/") {
		die(fmt.Sprintf("codex-ask: %s must be outside ~/.claude (config dir, not scratch space)", flag), 2)
	}
}

// repoToplevelOf: the nearest ancestor of the absolute path (itself included)
// with a .git entry (dir, or a worktree/submodule's file), "" when none. Lexical,
// no git subprocess — cwd-independent, never fails open on a git error — and it
// stops before $HOME so a versioned home dir doesn't poison the runs base.
func repoToplevelOf(path string) string {
	home := strings.TrimRight(os.Getenv("HOME"), "/")
	for d := filepath.Clean(path); ; d = filepath.Dir(d) {
		if d == home || d == filepath.Dir(d) {
			return ""
		}
		if _, err := os.Lstat(filepath.Join(d, ".git")); err == nil {
			return d
		}
	}
}

func startsWithRunPrefix(name string) bool {
	for _, p := range runPrefixes {
		if strings.HasPrefix(name, p) {
			return true
		}
	}
	return false
}

func infoFloat(info map[string]any, key string) *float64 {
	if v, ok := info[key]; ok {
		if f, ok := v.(float64); ok {
			return &f
		}
	}
	return nil
}

func infoString(info map[string]any, key string) *string {
	if v, ok := info[key]; ok {
		if s, ok := v.(string); ok {
			return &s
		}
	}
	return nil
}

func nilIfEmpty(s string) *string {
	if s == "" {
		return nil
	}
	return &s
}

func envWith(overrides ...string) []string {
	// os.Environ() with the override keys removed then re-appended; libc getenv on
	// a duplicate key is unspecified, so the old value must not linger.
	skip := map[string]bool{}
	for _, o := range overrides {
		if i := strings.IndexByte(o, '='); i >= 0 {
			skip[o[:i]] = true
		}
	}
	var env []string
	for _, e := range os.Environ() {
		if i := strings.IndexByte(e, '='); i >= 0 && skip[e[:i]] {
			continue
		}
		env = append(env, e)
	}
	return append(env, overrides...)
}
