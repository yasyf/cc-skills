package grading

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/grading/grader"
)

func needsProc(t *testing.T) {
	t.Helper()
	if _, err := os.Stat("/proc/self/stat"); err != nil {
		t.Skip("the Sprite scripts read /proc, which only Linux has")
	}
}

func recordedJob(t *testing.T) (*exec.Cmd, string) {
	t.Helper()
	directory := t.TempDir()
	cmd := exec.Command("sh", "-c", "sleep 300; :", "sh", directory)
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_ = KillGroup(cmd.Process.Pid)
		_ = cmd.Wait()
	})
	return cmd, directory
}

func startOf(t *testing.T, pid int) string {
	t.Helper()
	stat, err := os.ReadFile(fmt.Sprintf("/proc/%d/stat", pid))
	if err != nil {
		t.Fatal(err)
	}
	return strings.Fields(string(stat))[21]
}

func runScript(t *testing.T, script string, args ...string) string {
	t.Helper()
	out, err := exec.Command("sh", append([]string{"-c", script, "sh"}, args...)...).CombinedOutput()
	if err != nil {
		t.Fatalf("%s: %v", out, err)
	}
	return string(out)
}

func alive(cmd *exec.Cmd) bool {
	return syscall.Kill(cmd.Process.Pid, 0) == nil
}

func TestTheKillScriptEndsAJobWhoseRecordMatchesAndCancelsIt(t *testing.T) {
	needsProc(t)
	cmd, directory := recordedJob(t)
	record := fmt.Sprintf("%d %s\n", cmd.Process.Pid, startOf(t, cmd.Process.Pid))
	if err := os.WriteFile(filepath.Join(directory, "job-process"), []byte(record), 0o600); err != nil {
		t.Fatal(err)
	}

	if out := runScript(t, killScript, directory, stallCancel); strings.TrimSpace(out) != jobKilled {
		t.Fatalf("out = %q", out)
	}

	if err := cmd.Wait(); err == nil {
		t.Fatal("the job exited cleanly, so nothing killed it")
	}
	said, err := os.ReadFile(filepath.Join(directory, grader.CancelledFile))
	if err != nil || strings.TrimSpace(string(said)) != stallCancel {
		t.Fatalf("cancelled = %q, %v", said, err)
	}
}

func TestTheKillScriptSparesAProcessThatIsNotTheRecordedJob(t *testing.T) {
	needsProc(t)
	cases := map[string]func(cmd *exec.Cmd) string{
		"a reused pid has another start time": func(cmd *exec.Cmd) string { return strconv.Itoa(cmd.Process.Pid) + " 1" },
		"a pid running another job":           nil,
	}
	for name, record := range cases {
		t.Run(name, func(t *testing.T) {
			cmd, directory := recordedJob(t)
			written := fmt.Sprintf("%d %s", cmd.Process.Pid, startOf(t, cmd.Process.Pid))
			target := directory
			if record != nil {
				written = record(cmd)
			} else {
				target = t.TempDir()
			}
			if err := os.WriteFile(filepath.Join(target, "job-process"), []byte(written+"\n"), 0o600); err != nil {
				t.Fatal(err)
			}

			if out := runScript(t, killScript, target, stallCancel); out != "" {
				t.Fatalf("out = %q", out)
			}

			if !alive(cmd) {
				t.Fatal("a process that was not the job was killed")
			}
		})
	}
}

func TestTheKillScriptSaysNothingForAJobThatNeverStartedOrADirectoryThatIsGone(t *testing.T) {
	needsProc(t)
	if out := runScript(t, killScript, t.TempDir(), stallCancel); out != "" {
		t.Fatalf("no record: out = %q", out)
	}
	if out := runScript(t, killScript, filepath.Join(t.TempDir(), "gone"), stallCancel); out != "" {
		t.Fatalf("no directory: out = %q", out)
	}
}

func TestTheProgressScriptReadsTheNewestFileTwoLevelsDownAndAMissingDirectoryAsAncient(t *testing.T) {
	needsProc(t)
	directory := t.TempDir()
	run := filepath.Join(directory, "codex-1")
	if err := os.Mkdir(run, 0o750); err != nil {
		t.Fatal(err)
	}
	old := time.Now().Add(-time.Hour)
	log := filepath.Join(run, "codex-q-1.log")
	for path, at := range map[string]time.Time{filepath.Join(directory, "job.json"): old, log: time.Now().Add(-time.Minute)} {
		if err := os.WriteFile(path, nil, 0o600); err != nil {
			t.Fatal(err)
		}
		if err := os.Chtimes(path, at, at); err != nil {
			t.Fatal(err)
		}
	}
	gone := filepath.Join(directory, "gone")

	lines := strings.Split(strings.TrimSpace(runScript(t, newestScript, directory, gone)), "\n")

	age, named, _ := strings.Cut(lines[0], " ")
	seconds, err := strconv.Atoi(age)
	if err != nil || named != directory || seconds < 55 || seconds > 120 {
		t.Fatalf("line = %q", lines[0])
	}
	age, named, _ = strings.Cut(lines[1], " ")
	seconds, err = strconv.Atoi(age)
	if err != nil || named != gone || seconds < 1_000_000 {
		t.Fatalf("line = %q", lines[1])
	}
}

func TestKillGroupEndsAGroupAndWhatItStarted(t *testing.T) {
	log := filepath.Join(t.TempDir(), "job.log")
	pid, err := Detached(context.Background(), []string{"sh", "-c", "sleep 300; :"}, nil, log)
	if err != nil {
		t.Fatal(err)
	}

	if err := KillGroup(pid); err != nil {
		t.Fatal(err)
	}

	for syscall.Kill(pid, 0) == nil {
		time.Sleep(10 * time.Millisecond)
	}
}
