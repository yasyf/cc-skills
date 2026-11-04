package awake

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"
)

func statLine(pid, ppid int, comm string, rssPages int) string {
	fields := make([]string, 0, 23)
	fields = append(fields, "S", fmt.Sprint(ppid))
	for len(fields) < statRSS {
		fields = append(fields, "0")
	}
	fields = append(fields, fmt.Sprint(rssPages), "0")
	return fmt.Sprintf("%d (%s) %s\n", pid, comm, strings.Join(fields, " "))
}

func writeFixture(t *testing.T, path, body string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
}

func sprite(t *testing.T) Sampler {
	t.Helper()
	root := t.TempDir()
	proc := filepath.Join(root, "proc")
	writeFixture(t, filepath.Join(proc, "meminfo"), "MemTotal:       8388608 kB\nMemAvailable:   2097152 kB\nSwapTotal:      1048576 kB\nSwapFree:        524288 kB\n")
	writeFixture(t, filepath.Join(proc, "vmstat"), "oom_kill 2\nballoon_inflate 2628608\nballoon_deflate 531456\n")
	writeFixture(t, filepath.Join(proc, "uptime"), "4344.37 1000.00\n")
	writeFixture(t, filepath.Join(proc, "loadavg"), "0.50 0.25 0.10 1/237 2688\n")
	for pid, stat := range map[int]string{
		1:   statLine(1, 0, "init", 256),
		40:  statLine(40, 1, "orcad", 25600),
		41:  statLine(41, 40, "claude", 512000),
		42:  statLine(42, 41, "go build (x)", 1024000),
		43:  statLine(43, 41, "kworker", 0),
		999: statLine(999, 7, "codex", 2560),
	} {
		writeFixture(t, filepath.Join(proc, fmt.Sprint(pid), "stat"), stat)
	}
	writeFixture(t, filepath.Join(root, "memory.events"), "low 0\nmax 3\noom 1\noom_kill 1\n")
	return Sampler{
		Proc: proc, Events: filepath.Join(root, "memory.events"), Path: filepath.Join(root, "samples.log"),
		Every: time.Millisecond, Now: func() time.Time { return time.Date(2026, 11, 4, 9, 0, 0, 0, time.UTC) },
		Log: slog.New(slog.NewTextHandler(&bytes.Buffer{}, nil)),
	}
}

func samplesIn(t *testing.T, path string) []Sample {
	t.Helper()
	raw, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		t.Fatal(err)
	}
	var found []Sample
	for _, line := range strings.Split(strings.TrimRight(string(raw), "\n"), "\n") {
		var sample Sample
		if err := json.Unmarshal([]byte(line), &sample); err != nil {
			t.Fatalf("%q: %v", line, err)
		}
		found = append(found, sample)
	}
	return found
}

func TestASampleLineCarriesTheNumbersAStopWipes(t *testing.T) {
	s := sprite(t)
	if err := s.append(); err != nil {
		t.Fatal(err)
	}
	want := Sample{
		Time:           "2026-11-04T09:00:00Z",
		UptimeSeconds:  4344.37,
		MemAvailableMB: 2048,
		SwapUsedMB:     512,
		BalloonMB:      8192,
		OOMKills:       2,
		Load:           "0.50 0.25 0.10 1/237 2688",
		Top:            []Process{{"go build (x)", 42, 4000}, {"claude", 41, 2000}, {"orcad", 40, 100}},
		Agents:         []Agent{{"claude", 41, "orcad", 40}, {"codex", 999, "", 7}},
		MemoryEvents:   map[string]int64{"low": 0, "max": 3, "oom": 1, "oom_kill": 1},
	}
	got := samplesIn(t, s.Path)
	if len(got) != 1 || !reflect.DeepEqual(got[0], want) {
		t.Fatalf("got %+v\nwant %+v", got, want)
	}
}

func TestABoxWithNoAgentSaysSoSoTheAgentsExitShowsAgainstUptime(t *testing.T) {
	s := sprite(t)
	if err := os.RemoveAll(filepath.Join(s.Proc, "41")); err != nil {
		t.Fatal(err)
	}
	if err := os.RemoveAll(filepath.Join(s.Proc, "999")); err != nil {
		t.Fatal(err)
	}
	if err := s.append(); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(s.Path)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(raw), `"agents":[]`) || !strings.Contains(string(raw), `"uptime_s":4344.37`) {
		t.Fatal(string(raw))
	}
}

func TestUnreadableMemoryEventsLeaveTheRestOfTheLine(t *testing.T) {
	s := sprite(t)
	s.Events = filepath.Join(s.Proc, "absent")
	if err := s.append(); err != nil {
		t.Fatal(err)
	}
	got := samplesIn(t, s.Path)
	if len(got) != 1 || got[0].MemoryEvents != nil || got[0].MemAvailableMB != 2048 {
		t.Fatalf("%+v", got)
	}
}

func TestAProcessExitingMidScanDropsOnlyThatProcess(t *testing.T) {
	s := sprite(t)
	if err := os.Remove(filepath.Join(s.Proc, "42", "stat")); err != nil {
		t.Fatal(err)
	}
	if err := s.append(); err != nil {
		t.Fatal(err)
	}
	if top := samplesIn(t, s.Path)[0].Top; top[0].Comm != "claude" {
		t.Fatalf("%+v", top)
	}
}

func TestAFailedReadWritesNothingAndTheNextTickSamples(t *testing.T) {
	for _, broken := range []string{"meminfo", "vmstat", "uptime", "loadavg"} {
		t.Run(broken, func(t *testing.T) {
			s := sprite(t)
			path := filepath.Join(s.Proc, broken)
			held, err := os.ReadFile(path)
			if err != nil {
				t.Fatal(err)
			}
			if err := os.Remove(path); err != nil {
				t.Fatal(err)
			}
			var logged safeBuffer
			s.Log = slog.New(slog.NewTextHandler(&logged, nil))
			ctx, cancel := context.WithCancel(context.Background())
			finished := make(chan struct{})
			go func() {
				s.Run(ctx)
				close(finished)
			}()
			defer func() {
				cancel()
				<-finished
			}()
			waitFor(t, func() bool { return strings.Contains(logged.String(), "no sample was written") })
			if got := samplesIn(t, s.Path); got != nil {
				t.Fatalf("a failed read wrote %+v", got)
			}
			writeFixture(t, path, string(held))
			waitFor(t, func() bool { return strings.Contains(fileText(s.Path), "2026-11-04") })
		})
	}
}

func TestAnEmptyProcFileIsAFailedReadNotACrash(t *testing.T) {
	s := sprite(t)
	writeFixture(t, filepath.Join(s.Proc, "uptime"), "")
	if err := s.append(); err == nil {
		t.Fatal("an empty uptime wrote a sample")
	}
	if got := samplesIn(t, s.Path); got != nil {
		t.Fatalf("%+v", got)
	}
}

func TestTrimKeepsTheNewestTwoHoursAndLeavesAShorterFileAlone(t *testing.T) {
	s := sprite(t)
	var lines strings.Builder
	for i := range sampleKeep {
		fmt.Fprintf(&lines, "%d\n", i)
	}
	writeFixture(t, s.Path, lines.String())
	if err := s.trim(); err != nil {
		t.Fatal(err)
	}
	if raw, _ := os.ReadFile(s.Path); string(raw) != lines.String() {
		t.Fatalf("a file at the limit changed: %d bytes", len(raw))
	}
	fmt.Fprintf(&lines, "newest\n")
	writeFixture(t, s.Path, lines.String())
	if err := s.trim(); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(s.Path)
	if err != nil {
		t.Fatal(err)
	}
	kept := strings.Split(strings.TrimRight(string(raw), "\n"), "\n")
	if len(kept) != sampleKeep || kept[0] != "1" || kept[len(kept)-1] != "newest" {
		t.Fatalf("%d lines, first %q, last %q", len(kept), kept[0], kept[len(kept)-1])
	}
	if _, err := os.Stat(s.Path + ".tmp"); !os.IsNotExist(err) {
		t.Fatalf("the trim left its temporary file: %v", err)
	}
}

func TestARunningSamplerStaysBoundedAcrossARestart(t *testing.T) {
	s := sprite(t)
	var lines strings.Builder
	for range 2 * sampleKeep {
		lines.WriteString(`{"time":"old"}` + "\n")
	}
	writeFixture(t, s.Path, lines.String())
	ctx, cancel := context.WithCancel(context.Background())
	finished := make(chan struct{})
	go func() {
		s.Run(ctx)
		close(finished)
	}()
	waitFor(t, func() bool { return strings.Contains(fileText(s.Path), "2026-11-04") })
	cancel()
	<-finished
	if got := len(samplesIn(t, s.Path)); got > sampleKeep+trimEvery {
		t.Fatalf("%d lines after a restart", got)
	}
}

type safeBuffer struct {
	mu  sync.Mutex
	buf bytes.Buffer
}

func (b *safeBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.Write(p)
}

func (b *safeBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.String()
}

func fileText(path string) string {
	raw, _ := os.ReadFile(path)
	return string(raw)
}

func waitFor(t *testing.T, ready func() bool) {
	t.Helper()
	deadline := time.Now().Add(30 * time.Second)
	for !ready() {
		if time.Now().After(deadline) {
			t.Fatal("the sampler never got there")
		}
		time.Sleep(time.Millisecond)
	}
}
