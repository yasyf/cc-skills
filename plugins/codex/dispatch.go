package main

import (
	"embed"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"syscall"

	"github.com/yasyf/cc-interact/procs"
)

func askMode(args []string) {
	model := modelSol
	scratch := ""
	laneName := ""
	dispatch := false
	owner := ""
	lane := ""
	schemaName := ""
	var extraFlags []string
	var rest []string

	i, n := 0, len(args)
loop:
	for i < n {
		a := args[i]
		nxt := ""
		if i+1 < n {
			nxt = args[i+1]
		}
		switch {
		case a == "-m" || a == "--model":
			switch nxt {
			case "sol":
				model = modelSol
			case "luna":
				model = modelLuna
			default:
				die("codex-ask: -m takes sol or luna", 2)
			}
			i += 2
		case a == "-s" || a == "--scratch":
			if !strings.HasPrefix(nxt, "/") {
				die("codex-ask: --scratch must be an absolute path (never repo-relative)", 2)
			}
			scratch = nxt
			i += 2
		case a == "-l" || a == "--name":
			if nxt == "" {
				die("codex-ask: -l takes LANE or RUN/LANE", 2)
			}
			laneName = nxt
			i += 2
		case a == "--image":
			extraFlags = append(extraFlags, "--disable", "shell_tool")
			i++
		case a == "--lane":
			// Checked before any scratch dir is created, so a bad lane mints nothing.
			if !contains(laneNames, nxt) {
				die("codex-ask: --lane takes one of "+strings.Join(laneNames, ", "), 2)
			}
			lane = nxt
			i += 2
		case a == "--schema":
			// Checked before any scratch dir is created, so a bad schema mints nothing.
			switch {
			case strings.Contains(nxt, "/") || strings.HasSuffix(nxt, ".json"):
				if !readable(nxt) {
					die("codex-ask: --schema needs a readable JSON Schema file", 2)
				}
				extraFlags = append(extraFlags, "--output-schema", nxt)
			case contains(schemaNames, nxt):
				schemaName = nxt
			default:
				// A bare word is always a shipped name, never a cwd probe: a stray file named
				// verdict must not shadow the shipped schema. ./NAME reaches such a file.
				die("codex-ask: --schema takes one of "+strings.Join(schemaNames, ", ")+
					", or a file path (./NAME for an extensionless one)", 2)
			}
			i += 2
		case a == "--dispatch":
			dispatch = true
			i++
		case a == "--owner":
			if nxt == "" {
				die("codex-ask: --owner needs an agent id", 2)
			}
			owner = nxt
			i += 2
		case a == "-h" || a == "--help":
			fmt.Println(usageStr)
			os.Exit(0)
		case a == "--":
			rest = args[i+1:]
			break loop
		case a == "-":
			rest = args[i:]
			break loop
		case strings.HasPrefix(a, "-"):
			usage()
		default:
			rest = args[i:]
			break loop
		}
	}
	if len(rest) > 1 {
		usage()
	}
	if owner != "" && !dispatch {
		die("codex-ask: --owner requires --dispatch", 2)
	}
	if scratch != "" && laneName != "" {
		die("codex-ask: -l and -s are mutually exclusive", 2)
	}
	// Resolved before the question is read, so a bad name mints nothing.
	named := ""
	if laneName != "" {
		named = resolveLaneName(laneName)
	}
	// An unroutable wake (no session id, no claude ancestor) would enqueue
	// nothing, silently: refuse before anything is minted.
	ownerSession, ownerPID := "", 0
	if owner != "" {
		ownerSession = os.Getenv("CLAUDE_CODE_SESSION_ID")
		ownerPID = procs.ClaudePID()
		if ownerSession == "" && ownerPID == 0 {
			die("codex-ask: --owner is unroutable: no CLAUDE_CODE_SESSION_ID and no claude ancestor process", 2)
		}
	}

	// Read the question up front so an empty one refuses before anything is minted.
	var qbytes []byte
	switch {
	case len(rest) == 0 || rest[0] == "-":
		qbytes, _ = io.ReadAll(os.Stdin)
	case isRegularFile(rest[0]):
		// Python read_bytes() is unguarded: an unreadable file (mode 000) crashes
		// (exit 1); only a genuinely empty file falls through to "empty question".
		b, err := os.ReadFile(rest[0]) //nolint:gosec // reads the caller-supplied question file by path, by design
		if err != nil {
			die("codex-ask: cannot read question file "+rest[0]+": "+err.Error(), 1)
		}
		qbytes = b
	case rest[0] != "":
		qbytes = []byte(rest[0] + "\n")
	default:
		qbytes = []byte{}
	}
	if len(qbytes) == 0 {
		die("codex-ask: empty question", 2)
	}

	var sdir string
	switch {
	case scratch != "":
		rejectOutsideScratch(scratch, "--scratch")
		if err := os.MkdirAll(scratch, 0o755); err != nil { //nolint:gosec // 0o755 matches the Python spec's scratch-dir mode
			os.Exit(2)
		}
		sdir = scratch
	case named != "":
		if err := os.MkdirAll(named, 0o755); err != nil { //nolint:gosec // 0o755 matches the Python spec's scratch-dir mode
			os.Exit(2)
		}
		sdir = named
	default:
		sdir = mintScratch("codex-ask")
	}

	laneLock := acquireLaneLock(sdir, true)
	// Never runs (every path ends in os.Exit) but anchors laneLock against the
	// os.File finalizer, whose early close would release the flock mid-run.
	defer releaseLaneLock(laneLock)
	if scratch != "" || named != "" {
		// Refuse a still-alive lane; a meta <5s old with no pid is the launch window
		// before a worker checks in. A finished lane (status written) stays reusable.
		metaP := join(sdir, "meta")
		if isFile(metaP) && !nonempty(join(sdir, "status")) {
			recent := false
			if fi, err := os.Stat(metaP); err == nil { //nolint:gosec // stats the lane's own meta file, by design
				recent = nowSec()-float64(fi.ModTime().UnixNano())/1e9 < 5
			}
			if pidAlive(sdir) || recent {
				die(fmt.Sprintf("codex-ask: lane %s busy; --await it or mint a new lane", sdir), 1)
			}
		}
	}

	qf, err := os.CreateTemp(sdir, "codex-q-")
	if err != nil {
		die("codex-ask: cannot stage question: "+err.Error(), 1)
	}
	question := qf.Name()
	_ = qf.Close()
	rf, err := os.CreateTemp(sdir, "codex-r-")
	if err != nil {
		die("codex-ask: cannot stage reply: "+err.Error(), 1)
	}
	reply := rf.Name()
	_ = rf.Close()
	logf := question + ".log"
	if err := os.WriteFile(question, qbytes, 0o644); err != nil { //nolint:gosec // 0o644 matches the Python spec's question-file mode
		die("codex-ask: cannot write question: "+err.Error(), 1)
	}

	// Staged per dispatch like the question and reply: a fixed name would survive
	// the stale sweep below and outlive its generation in a reused --scratch lane.
	if schemaName != "" {
		sf, err := os.CreateTemp(sdir, "codex-schema-")
		if err != nil {
			die("codex-ask: cannot stage schema: "+err.Error(), 1)
		}
		schema := sf.Name()
		_ = sf.Close()
		if err := os.WriteFile(schema, readShipped(embeddedSchemas, "schemas/"+schemaName+".json"), 0o644); err != nil { //nolint:gosec // 0o644 matches the Python spec's question-file mode
			die("codex-ask: cannot write schema: "+err.Error(), 1)
		}
		extraFlags = append(extraFlags, "--output-schema", schema)
	}

	// developer_instructions carries the browser + ccx/MCP-off directives, resolved
	// relative to this binary's own path (not cwd).
	dev := readAgentsMd()
	// The lane contract lands after the baseline so the cached prefix stays stable.
	if lane != "" {
		dev += "\n\n" + strings.TrimRight(string(readShipped(embeddedLanes, "lanes/"+lane+".md")), "\n")
	}

	replyTmp := reply + ".tmp"
	argv := []string{
		"codex", "exec",
		"-c", "model=" + model,
		"-c", "model_reasoning_effort=" + effort,
		"-c", "service_tier=fast",
		// No MCP mounts in a lane: zero correctness gain, real overhead, wedges mid-call.
		"-c", "mcp_servers={}",
		"-c", "developer_instructions=" + dev,
		"-o", replyTmp,
		"--json", "--color", "never",
		"--sandbox", "danger-full-access",
		// A non-repo cwd is a normal ad-hoc lane; never fail on codex's
		// trusted-directory check.
		"--skip-git-repo-check",
	}
	argv = append(argv, extraFlags...)

	// Reap the prior generation's staged reply temp (a SIGKILLed worker can't run
	// its own cleanup) — but only a lane-local codex-r-* path, so a corrupt meta
	// can never name an arbitrary victim.
	if old := lineAt(metaLines(join(sdir, "meta")), 0); strings.HasPrefix(filepath.Base(old), "codex-r-") &&
		filepath.Dir(old) == filepath.Clean(sdir) {
		_ = os.Remove(old + ".tmp") //nolint:gosec // best-effort sweep of the lane's own staged reply temp
	}
	// The exclusive lane lock makes this reset and the replacement metadata one
	// publication: --await cannot observe the transient empty generation.
	for _, stale := range []string{"status", "pid", "lstart", "meta", "cmd", "register"} {
		_ = os.Remove(join(sdir, stale)) //nolint:gosec // best-effort sweep of the lane's own stale state files
	}
	cwd, _ := os.Getwd()
	cmd := cmdSpec{Argv: argv, Question: question, Reply: reply, ReplyTmp: replyTmp, Log: logf}
	// An owner subagent's async dispatch records who to wake and the routing keys
	// resolved now — the orphaned worker cannot recover them later.
	if owner != "" {
		cmd.Owner = owner
		cmd.Session = ownerSession
		cmd.Scope = cwd
		cmd.ClaudePID = ownerPID
	}
	cb, _ := json.Marshal(cmd)
	_ = os.WriteFile(join(sdir, "cmd"), cb, 0o644) //nolint:gosec // 0o644 matches the Python spec's cmd-file mode

	info := map[string]any{"ts": nowSec(), "cwd": cwd, "model": model}
	if sid := os.Getenv("CLAUDE_CODE_SESSION_ID"); sid != "" {
		info["session"] = sid
	}
	ib, _ := json.Marshal(info)
	if err := atomicWrite(join(sdir, "meta"), reply+"\n"+logf+"\n"+string(ib)+"\n"); err != nil {
		die(fmt.Sprintf("codex-ask: %s is in use by a concurrent run; pass a unique -l/-s lane or omit both", sdir), 1)
	}

	// Print recovery paths BEFORE codex starts: a killed Bash call still leaves the
	// --await recipe in the returned partial stdout.
	fmt.Printf("REPLY_FILE: %s\n", reply)
	fmt.Printf("LOG_FILE: %s\n", logf)
	fmt.Printf("AWAIT: %s --await %s\n", shlexQuote(invokePath), shlexQuote(sdir))

	detachWorker(sdir)
	// Async: the printed REPLY_FILE/LOG_FILE/AWAIT lines are the owner's recovery
	// contract, and the worker wakes the owner on completion — return at once
	// rather than blocking on the status file. Foreground dispatch deliberately
	// carries the exclusive lock to process exit so no replacement generation can
	// publish between its final poll and report.
	if dispatch {
		os.Exit(0)
	}
	pollStatus(sdir, reply, logf)
	reportStatus(readStatus(sdir), reply, logf)
}

func readable(path string) bool {
	return syscall.Access(path, 0x4) == nil // R_OK
}

func isRegularFile(path string) bool {
	fi, err := os.Stat(path) //nolint:gosec // stats a caller-supplied dispatch path
	return err == nil && fi.Mode().IsRegular()
}

//go:embed AGENTS.md
var embeddedAgentsMd string

// pluginRoots lists the plugin roots whose disk copies override the embedded
// fallbacks, most authoritative first: the root the binrun shim exports (under
// binrun the binary runs from a cache shard, so neither path layout resolves),
// then the invocation layout, then the resolved-binary layout.
func pluginRoots() []string {
	var roots []string
	if root := os.Getenv("BINRUN_PLUGIN_ROOT"); root != "" {
		roots = append(roots, root)
	}
	if exe, err := os.Executable(); err == nil {
		roots = append(roots, filepath.Dir(filepath.Dir(exe)))
	}
	return append(roots, filepath.Dir(filepath.Dir(selfPath)))
}

func readAgentsMd() string {
	for _, root := range pluginRoots() {
		if b, err := os.ReadFile(filepath.Join(root, "AGENTS.md")); err == nil { //nolint:gosec // reads the plugin's own AGENTS.md, by design
			return strings.TrimRight(string(b), "\n")
		}
	}
	return strings.TrimRight(embeddedAgentsMd, "\n")
}

//go:embed lanes/*.md
var embeddedLanes embed.FS

//go:embed schemas/*.json
var embeddedSchemas embed.FS

var laneNames = []string{"review", "refute", "security", "diagnose", "implement", "recon"}

var schemaNames = []string{"verdict", "findings", "refutations"}

// readShipped resolves one shipped file (slash-separated, relative to the plugin
// root) the way readAgentsMd resolves AGENTS.md: a disk copy under any
// pluginRoots entry overrides the always-present embedded copy.
func readShipped(embedded embed.FS, rel string) []byte {
	for _, root := range pluginRoots() {
		if b, err := os.ReadFile(filepath.Join(root, rel)); err == nil { //nolint:gosec // reads the plugin's own shipped file, by design
			return b
		}
	}
	b, _ := embedded.ReadFile(rel)
	return b
}
