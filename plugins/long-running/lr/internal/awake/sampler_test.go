package awake

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"sync/atomic"
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
		42:  strings.Replace(statLine(42, 41, "go build (x)", 1024000), ") S ", ") D ", 1),
		43:  statLine(43, 41, "kworker", 0),
		999: statLine(999, 7, "codex", 2560),
	} {
		writeFixture(t, filepath.Join(proc, fmt.Sprint(pid), "stat"), stat)
	}
	writeFixture(t, filepath.Join(proc, "42", "cmdline"), "go\x00build\x00./...\x00")
	writeFixture(t, filepath.Join(root, "memory.events"), "low 0\nmax 3\noom 1\noom_kill 1\n")
	return Sampler{
		Proc: proc, Events: filepath.Join(root, "memory.events"), Path: filepath.Join(root, "samples.log"),
		Kernel: func() ([]byte, error) { return nil, nil },
		Every:  time.Millisecond, Now: func() time.Time { return time.Date(2026, 11, 4, 9, 0, 0, 0, time.UTC) },
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
		DiskWaiting:    1,
		Top:            []Process{{"go build (x)", 42, 4000, "go build ./..."}, {"claude", 41, 2000, ""}, {"orcad", 40, 100, ""}},
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

func TestAKernelLineOnTheHomeDiskOrAtErrorLevelIsRecordedOnceAndOtherLinesNever(t *testing.T) {
	s := sprite(t)
	log := "<6>[ 1.000] nbd0: detected capacity change\n<4>[ 2.000] audit: unrelated\n<3>[ 1453.100000] rcu: INFO: rcu_preempt detected stalls on CPUs/tasks:\n<3>[ 1453.196886] block nbd0: Send data failed (result -11)\n"
	s.Kernel = func() ([]byte, error) { return []byte(log), nil }
	for range 2 {
		if err := s.append(); err != nil {
			t.Fatal(err)
		}
	}
	log += "<3>[ 1453.200063] I/O error, dev nbd0, sector 105302904\n"
	if err := s.append(); err != nil {
		t.Fatal(err)
	}
	got := samplesIn(t, s.Path)
	want := []string{"<6>[ 1.000] nbd0: detected capacity change", "<3>[ 1453.100000] rcu: INFO: rcu_preempt detected stalls on CPUs/tasks:", "<3>[ 1453.196886] block nbd0: Send data failed (result -11)"}
	if !reflect.DeepEqual(got[0].Kernel, want) || got[1].Kernel != nil {
		t.Fatalf("first %q, second %q", got[0].Kernel, got[1].Kernel)
	}
	if want := []string{"<3>[ 1453.200063] I/O error, dev nbd0, sector 105302904"}; !reflect.DeepEqual(got[2].Kernel, want) {
		t.Fatalf("third %q", got[2].Kernel)
	}
}

func TestAFloodOfKernelLinesKeepsTheNewestAndAFailedReadLeavesTheSample(t *testing.T) {
	s := sprite(t)
	var flood strings.Builder
	for i := 1; i <= 2*kernelKeep; i++ {
		fmt.Fprintf(&flood, "<3>[ %d.000] I/O error, dev nbd0, sector %d\n", i, i)
	}
	s.Kernel = func() ([]byte, error) { return []byte(flood.String()), nil }
	if err := s.append(); err != nil {
		t.Fatal(err)
	}
	s.Kernel = func() ([]byte, error) { return nil, errors.New("not permitted") }
	if err := s.append(); err != nil {
		t.Fatal(err)
	}
	got := samplesIn(t, s.Path)
	if len(got[0].Kernel) != kernelKeep || !strings.Contains(got[0].Kernel[kernelKeep-1], "sector 100") {
		t.Fatalf("%d lines: %q", len(got[0].Kernel), got[0].Kernel)
	}
	if got[1].Kernel != nil || got[1].MemAvailableMB != 2048 {
		t.Fatalf("%+v", got[1])
	}
}

func TestALongCommandLineIsClipped(t *testing.T) {
	s := sprite(t)
	writeFixture(t, filepath.Join(s.Proc, "42", "cmdline"), strings.Repeat("x", 2*argsKeep))
	if err := s.append(); err != nil {
		t.Fatal(err)
	}
	if got := samplesIn(t, s.Path)[0].Top[0].Args; len(got) != argsKeep {
		t.Fatalf("%d bytes", len(got))
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

func sampleLine(t *testing.T, sample Sample) string {
	t.Helper()
	line, err := json.Marshal(sample)
	if err != nil {
		t.Fatal(err)
	}
	return string(line) + "\n"
}

func TestLastWordsReadTheBootThatStoppedAndNotTheWakeThatReadsIt(t *testing.T) {
	stopped := Sample{Time: "2026-11-04T21:15:18Z", UptimeSeconds: 2995, MemAvailableMB: 5558, Load: "12.46 9 6", DiskWaiting: 2, Agents: []Agent{{Comm: "claude"}}, Kernel: []string{"<6>[ 3.0] nbd0: detected capacity change", "<3>[ 2990.0] block nbd0: Dead connection"}}
	woken := Sample{Time: "2026-11-04T21:18:35Z", UptimeSeconds: 25, MemAvailableMB: 8517, Load: "1.61 1 1"}
	want := "its last sample before the stop, 2026-11-04T21:15:18Z at uptime 2995 s, read 5558 MB free, 0 OOM kills, load 12.46, 2 waiting on disk, 1 agents; <3>[ 2990.0] block nbd0: Dead connection"

	for name, samples := range map[string]string{
		"no sample since the wake": sampleLine(t, stopped),
		"a sample since the wake":  sampleLine(t, stopped) + "not json\n" + sampleLine(t, woken),
	} {
		if got := LastWords(samples); got != want {
			t.Errorf("%s: %s", name, got)
		}
	}
}

func TestLastWordsSayWhenNoKernelErrorOrNoSampleWasLeft(t *testing.T) {
	quiet := sampleLine(t, Sample{Time: "t", UptimeSeconds: 1331, Load: "7.26 5 2", Kernel: []string{"<6>[ 3.2] EXT4-fs (nbd0): mounted filesystem"}})
	if got := LastWords(quiet); !strings.HasSuffix(got, "; no kernel error") {
		t.Error(got)
	}
	if got := LastWords(""); !strings.Contains(got, "left no sample") {
		t.Error(got)
	}
}

func TestAKernelLineReachesTheFileWellBeforeTheNextSample(t *testing.T) {
	s := sprite(t)
	s.Every = time.Hour
	var log atomic.Value
	log.Store("")
	s.Kernel = func() ([]byte, error) { return []byte(log.Load().(string)), nil }
	ctx, stop := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		s.Run(ctx)
		close(done)
	}()
	waitFor := func(want int) []Sample {
		t.Helper()
		deadline := time.Now().Add(30 * time.Second)
		for time.Now().Before(deadline) {
			if got := samplesIn(t, s.Path); len(got) >= want {
				return got
			}
			time.Sleep(20 * time.Millisecond)
		}
		t.Fatalf("fewer than %d samples", want)
		return nil
	}
	waitFor(1)
	log.Store("<3>[ 1453.2] block nbd0: Dead connection, failed to find a fallback\n")

	got := waitFor(2)
	stop()
	<-done

	if len(got[1].Kernel) != 1 || !strings.Contains(got[1].Kernel[0], "Dead connection") {
		t.Fatalf("%+v", got[1])
	}
}
