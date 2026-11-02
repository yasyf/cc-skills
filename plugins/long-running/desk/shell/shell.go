// Package shell is the landing desk's single side-effect boundary: every subprocess, the clock, sleeps, the load average and
// the process environment pass through a Shell, mirroring desk-runner.py's Shell method for method.
package shell

import (
	"path/filepath"
	"slices"
	"strings"
	"time"
)

const (
	CommandTimeoutSeconds = 1800
	EnqueueSeconds        = 300
	MergeTaskSeconds      = 2 * EnqueueSeconds
	LedgerSeconds         = 300
	FetchSeconds          = 120
	QuickSeconds          = 60
	CheckPRSeconds        = 180
	GateCheckSeconds      = EnqueueSeconds
	EachCheckSeconds      = 2 * GateCheckSeconds
	QueueStatusSeconds    = 300
	BusProbeSeconds       = 15
	ReapSeconds           = 5
	TimedOut              = 124
	MissingBinary         = 127
	DeadlineEnv           = "LONG_RUNNING_DEADLINE"
)

var (
	MissingBackoff = []int{2, 5, 10, 20, 30}
	RereadPauses   = []int{2, 6}
	BusProbe       = []string{"cci", "tail", "-n", "1"}
	Gateway        = []string{"ccx", "vcs", "gh"}
	QuickPrograms  = []string{"gh", "cci", "ccx"}
	Shims          = map[string]string{"cci": "cc-inbox", "ccx": "cc-context"}
)

// Done is one finished command: its exit code, stdout and stderr.
type Done struct {
	Code int    `json:"code"`
	Out  string `json:"out"`
	Err  string `json:"err"`
}

// Process is a child Spawn started.
type Process interface {
	Pid() int
	Poll() (int, bool)
}

// Shell is every side effect the landing desk has. Stdout and Stderr write the desk's own output, flushed at once.
type Shell interface {
	Run(argv []string, stdin *string, env map[string]string, cwd string) Done
	CciQuery(argv []string, env map[string]string, cwd string) Done
	PassCost() string
	InFlight() (string, float64, bool)
	Adopt(values map[string]string)
	Reexec(argv []string)
	Launchctl(args ...string) Done
	Spawn(argv []string, out string, env map[string]string, appending bool) Process
	RunsJob(pid int, directory string) bool
	Stat(path string) (string, bool)
	Now() time.Time
	Monotonic() float64
	Sleep(seconds float64)
	Wait(stop <-chan struct{}, seconds float64) bool
	Load() float64
	Cores() int
	Env(name string) string
	Getenv(name string) string
	Pid() int
	Stdout(text string)
	Stderr(text string)
}

func prefix(argv []string, words ...string) bool {
	return len(argv) >= len(words) && slices.Equal(argv[:len(words)], words)
}

func quick(argv []string) bool {
	return slices.Contains(QuickPrograms, filepath.Base(argv[0]))
}

// TimeBound is desk-runner.py's time_bound: the seconds `argv` may run before it is killed.
func TimeBound(argv []string) int {
	switch {
	case filepath.Base(argv[0]) == "sprite-job.sh" && filepath.Base(argv[2]) == "merge_task.py":
		return MergeTaskSeconds
	case prefix(argv, BusProbe...):
		return BusProbeSeconds
	case len(argv) > 2 && filepath.Base(argv[1]) == "rulings.py" && argv[2] == "check-pr":
		return CheckPRSeconds
	case slices.Contains(argv, "--each"):
		return EachCheckSeconds
	case slices.Contains(argv, "--check"):
		return GateCheckSeconds
	case prefix(argv, "ccx", "vcs", "pr", "status"):
		return QueueStatusSeconds
	case filepath.Base(argv[0]) == "stack-enqueue" || prefix(argv, "ccx", "vcs", "ship"):
		return EnqueueSeconds
	case len(argv) > 1 && filepath.Base(argv[1]) == "ledger.py":
		return LedgerSeconds
	case argv[0] == "git" && slices.Contains(argv, "fetch"):
		return FetchSeconds
	case quick(argv):
		return QuickSeconds
	}
	return CommandTimeoutSeconds
}

// Rereadable is desk-runner.py's rereadable: whether `argv` only reads, so a timed-out or transiently failed run runs again.
func Rereadable(argv []string) bool {
	name := filepath.Base(argv[0])
	return ((prefix(argv, "cci", "tail") || prefix(argv, "cci", "who")) && !prefix(argv, BusProbe...)) ||
		prefix(argv, Gateway...) ||
		prefix(argv, "ccx", "vcs", "pr", "status") || prefix(argv, "ccx", "vcs", "auth", "status") ||
		(name == "ci" && len(argv) >= 3 && argv[1] == "trunk" && argv[2] == "status") ||
		(name == "stack-enqueue" && (slices.Contains(argv, "--check") || slices.Contains(argv, "--status")) && !slices.Contains(argv, "--each"))
}

// Pauses is desk-runner.py's reread_pauses.
func Pauses(argv []string) []int {
	if !Rereadable(argv) {
		return nil
	}
	if filepath.Base(argv[0]) == "stack-enqueue" {
		return RereadPauses[:1]
	}
	return RereadPauses
}

// GhArgs is ghread.gh_args: the plain gh command a gateway read runs.
func GhArgs(argv []string) []string {
	if !prefix(argv, Gateway...) {
		return argv
	}
	return append([]string{"gh"}, argv[slices.Index(argv, "--")+1:]...)
}

// CommandName is desk-runner.py's command_name.
func CommandName(argv []string) string {
	words := argv
	if strings.HasPrefix(filepath.Base(argv[0]), "python") {
		words = argv[1:]
	}
	named := make([]string, len(words))
	for i, word := range words {
		if strings.HasPrefix(word, "/") {
			word = filepath.Base(word)
		}
		named[i] = word
	}
	return Clip(strings.Join(named, " "), 120)
}

// CommandFamily is desk-runner.py's command_family, what a pass's cost line groups by.
func CommandFamily(argv []string) string {
	words := GhArgs(argv)
	if strings.HasPrefix(filepath.Base(argv[0]), "python") {
		words = argv[1:]
	}
	var verbs []string
	for _, word := range words[1:] {
		if strings.HasPrefix(word, "-") || strings.Contains(word, "/") || digits(strings.ReplaceAll(word, ",", "")) {
			if len(verbs) > 0 {
				break
			}
			continue
		}
		verbs = append(verbs, word)
		if len(verbs) == 3 {
			break
		}
	}
	family := append([]string{filepath.Base(words[0])}, verbs...)
	if slices.Contains(words, "--each") {
		family = append(family, "--each")
	}
	if slices.Contains(words, "--check") {
		family = append(family, "--check")
	}
	return strings.Join(family, " ")
}

func digits(word string) bool {
	if word == "" {
		return false
	}
	for _, r := range word {
		if r < '0' || r > '9' {
			return false
		}
	}
	return true
}

// Clip is Python's text[:limit] on code points.
func Clip(text string, limit int) string {
	runes := []rune(text)
	if len(runes) <= limit {
		return text
	}
	return string(runes[:limit])
}
