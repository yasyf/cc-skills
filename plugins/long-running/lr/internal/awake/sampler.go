package awake

import (
	"cmp"
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"
)

const (
	SampleEvery = 10 * time.Second
	KernelEvery = time.Second
	SamplePath  = "/tmp/cc-remote-samples.log"
	sampleKeep  = 720
	trimEvery   = 60
	topByRSS    = 3
	pageKB      = 4
	statState   = 0
	statPPID    = 1
	statRSS     = 21
	diskWait    = "D"
	homeDisk    = "nbd0"
	kernelKeep  = 50
	argsKeep    = 200
)

var (
	kernelStamp = regexp.MustCompile(`^<\d+>\[\s*(\d+\.\d+)\]`)
	kernelError = regexp.MustCompile(`^<[0-3]>`)
)

type Sampler struct {
	Proc   string
	Events string
	Kernel func() ([]byte, error)
	Path   string
	Every  time.Duration
	Now    func() time.Time
	Log    *slog.Logger
	since  float64
	unsaid []string
}

type Process struct {
	Comm  string `json:"comm"`
	PID   int    `json:"pid"`
	RSSMB int64  `json:"rss_mb"`
	Args  string `json:"args"`
}

type Agent struct {
	Comm       string `json:"comm"`
	PID        int    `json:"pid"`
	ParentComm string `json:"parent_comm"`
	ParentPID  int    `json:"parent_pid"`
}

type Sample struct {
	Time           string           `json:"time"`
	UptimeSeconds  float64          `json:"uptime_s"`
	MemAvailableMB int64            `json:"mem_available_mb"`
	SwapUsedMB     int64            `json:"swap_used_mb"`
	BalloonMB      int64            `json:"balloon_mb"`
	OOMKills       int64            `json:"oom_kills"`
	Load           string           `json:"load"`
	DiskWaiting    int              `json:"disk_waiting"`
	Top            []Process        `json:"top"`
	Agents         []Agent          `json:"agents"`
	MemoryEvents   map[string]int64 `json:"memory_events,omitempty"`
	Kernel         []string         `json:"kernel,omitempty"`
}

type procEntry struct {
	Process
	ppid  int
	state string
}

func (s *Sampler) Run(ctx context.Context) {
	if err := s.trim(); err != nil {
		s.Log.Warn("the sample file was not trimmed", "path", s.Path, "err", err)
	}
	for tick := 1; ; tick++ {
		if err := s.append(); err != nil {
			s.Log.Warn("no sample was written", "path", s.Path, "err", err)
		}
		if tick%trimEvery == 0 {
			if err := s.trim(); err != nil {
				s.Log.Warn("the sample file was not trimmed", "path", s.Path, "err", err)
			}
		}
		if !s.waitOut(ctx) {
			return
		}
	}
}

func (s *Sampler) waitOut(ctx context.Context) bool {
	next := time.After(s.Every)
	check := time.NewTicker(KernelEvery)
	defer check.Stop()
	for {
		select {
		case <-ctx.Done():
			return false
		case <-next:
			return true
		case <-check.C:
			fresh := s.kernelLines()
			if len(fresh) == 0 {
				continue
			}
			s.unsaid = append(s.unsaid, fresh...)
			if err := s.append(); err != nil {
				s.Log.Warn("no sample was written", "path", s.Path, "err", err)
			}
		}
	}
}

func (s *Sampler) append() error {
	sample, err := s.read()
	if err != nil {
		return err
	}
	line, err := json.Marshal(sample)
	if err != nil {
		return err
	}
	file, err := os.OpenFile(s.Path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	defer func() { _ = file.Close() }()
	if _, err := file.Write(append(line, '\n')); err != nil {
		return err
	}
	return file.Sync()
}

func (s *Sampler) read() (Sample, error) {
	memory, err := counters(filepath.Join(s.Proc, "meminfo"))
	if err != nil {
		return Sample{}, err
	}
	vmstat, err := counters(filepath.Join(s.Proc, "vmstat"))
	if err != nil {
		return Sample{}, err
	}
	uptime, err := os.ReadFile(filepath.Join(s.Proc, "uptime"))
	if err != nil {
		return Sample{}, err
	}
	var seconds float64
	if _, err := fmt.Sscan(string(uptime), &seconds); err != nil {
		return Sample{}, fmt.Errorf("uptime: %w", err)
	}
	load, err := os.ReadFile(filepath.Join(s.Proc, "loadavg"))
	if err != nil {
		return Sample{}, err
	}
	processes, err := s.processes()
	if err != nil {
		return Sample{}, err
	}
	events, _ := counters(s.Events)
	kernel := append(s.unsaid, s.kernelLines()...)
	s.unsaid = nil
	return Sample{
		Time:           s.Now().UTC().Format(time.RFC3339),
		UptimeSeconds:  seconds,
		MemAvailableMB: memory["MemAvailable"] / 1024,
		SwapUsedMB:     (memory["SwapTotal"] - memory["SwapFree"]) / 1024,
		BalloonMB:      (vmstat["balloon_inflate"] - vmstat["balloon_deflate"]) * pageKB / 1024,
		OOMKills:       vmstat["oom_kill"],
		Load:           strings.TrimSpace(string(load)),
		DiskWaiting:    waiting(processes),
		Top:            s.largest(processes),
		Agents:         agents(processes),
		MemoryEvents:   events,
		Kernel:         kernel,
	}, nil
}

func (s *Sampler) processes() ([]procEntry, error) {
	entries, err := os.ReadDir(s.Proc)
	if err != nil {
		return nil, err
	}
	var found []procEntry
	for _, entry := range entries {
		pid, err := strconv.Atoi(entry.Name())
		if err != nil {
			continue
		}
		stat, err := os.ReadFile(filepath.Join(s.Proc, entry.Name(), "stat"))
		if err != nil {
			continue
		}
		process, err := parseStat(pid, string(stat))
		if err != nil {
			return nil, err
		}
		found = append(found, process)
	}
	return found, nil
}

func (s *Sampler) trim() error {
	raw, err := os.ReadFile(s.Path)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return err
	}
	lines := strings.SplitAfter(string(raw), "\n")
	if len(lines) <= sampleKeep+1 {
		return nil
	}
	kept := s.Path + ".tmp"
	if err := os.WriteFile(kept, []byte(strings.Join(lines[len(lines)-sampleKeep-1:], "")), 0o600); err != nil {
		return err
	}
	return os.Rename(kept, s.Path)
}

func parseStat(pid int, stat string) (procEntry, error) {
	open, shut := strings.IndexByte(stat, '('), strings.LastIndexByte(stat, ')')
	if open < 0 || shut < open {
		return procEntry{}, fmt.Errorf("process %d: no name in its stat line", pid)
	}
	fields := strings.Fields(stat[shut+1:])
	if len(fields) <= statRSS {
		return procEntry{}, fmt.Errorf("process %d: %d fields in its stat line", pid, len(fields))
	}
	ppid, err := strconv.Atoi(fields[statPPID])
	if err != nil {
		return procEntry{}, err
	}
	pages, err := strconv.ParseInt(fields[statRSS], 10, 64)
	if err != nil {
		return procEntry{}, err
	}
	return procEntry{Process{stat[open+1 : shut], pid, pages * pageKB / 1024, ""}, ppid, fields[statState]}, nil
}

func (s *Sampler) largest(processes []procEntry) []Process {
	sorted := slices.Clone(processes)
	slices.SortFunc(sorted, func(a, b procEntry) int { return cmp.Compare(b.RSSMB, a.RSSMB) })
	top := make([]Process, 0, topByRSS)
	for _, process := range sorted[:min(topByRSS, len(sorted))] {
		process.Args = s.args(process.PID)
		top = append(top, process.Process)
	}
	return top
}

func (s *Sampler) args(pid int) string {
	raw, err := os.ReadFile(filepath.Join(s.Proc, strconv.Itoa(pid), "cmdline"))
	if err != nil {
		return ""
	}
	joined := strings.TrimSpace(strings.ReplaceAll(string(raw), "\x00", " "))
	return joined[:min(argsKeep, len(joined))]
}

func (s *Sampler) kernelLines() []string {
	raw, err := s.Kernel()
	if err != nil {
		return nil
	}
	var fresh []string
	for line := range strings.Lines(string(raw)) {
		stamp := kernelStamp.FindStringSubmatch(line)
		if stamp == nil || (!strings.Contains(line, homeDisk) && !kernelError.MatchString(line)) {
			continue
		}
		at, err := strconv.ParseFloat(stamp[1], 64)
		if err != nil || at <= s.since {
			continue
		}
		s.since = at
		fresh = append(fresh, strings.TrimSpace(line))
	}
	return fresh[max(0, len(fresh)-kernelKeep):]
}

func waiting(processes []procEntry) int {
	count := 0
	for _, process := range processes {
		if process.state == diskWait {
			count++
		}
	}
	return count
}

func agents(processes []procEntry) []Agent {
	found := []Agent{}
	for _, process := range processes {
		if process.Comm != "claude" && process.Comm != "codex" {
			continue
		}
		parent := ""
		if at := slices.IndexFunc(processes, func(p procEntry) bool { return p.PID == process.ppid }); at >= 0 {
			parent = processes[at].Comm
		}
		found = append(found, Agent{process.Comm, process.PID, parent, process.ppid})
	}
	return found
}

func counters(path string) (map[string]int64, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	values := map[string]int64{}
	for _, line := range strings.Split(string(raw), "\n") {
		fields := strings.Fields(line)
		if len(fields) < 2 {
			continue
		}
		value, err := strconv.ParseInt(fields[1], 10, 64)
		if err != nil {
			return nil, fmt.Errorf("%s: %w", path, err)
		}
		values[strings.TrimSuffix(fields[0], ":")] = value
	}
	return values, nil
}
