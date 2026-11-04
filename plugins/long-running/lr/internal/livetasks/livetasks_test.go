package livetasks

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

const (
	process = "ce184f34"
	session = "d50e80e8"
)

var now = time.Date(2026, 11, 3, 23, 0, 0, 0, time.UTC)

func assistantCall(t *testing.T, proc, id, name string, input map[string]any, at time.Time) string {
	t.Helper()
	line, err := json.Marshal(map[string]any{
		"type": "assistant", "session_id": proc, "sessionId": session, "timestamp": at.Format(time.RFC3339),
		"message": map[string]any{"content": []map[string]any{{"type": "tool_use", "id": id, "name": name, "input": input}}},
	})
	if err != nil {
		t.Fatal(err)
	}
	return string(line)
}

func startedResult(t *testing.T, id, task string) string {
	t.Helper()
	line, err := json.Marshal(map[string]any{
		"type": "user", "toolUseResult": map[string]any{"taskId": task},
		"message": map[string]any{"content": []map[string]any{{"type": "tool_result", "tool_use_id": id}}},
	})
	if err != nil {
		t.Fatal(err)
	}
	return string(line)
}

func ended(t *testing.T, id, task, state string) string {
	t.Helper()
	content := "<task-notification>\n<task-id>" + task + "</task-id>\n"
	if id != "" {
		content += "<tool-use-id>" + id + "</tool-use-id>\n"
	}
	content += "<status>" + state + "</status>\n</task-notification>"
	line, err := json.Marshal(map[string]any{"type": "queue-operation", "operation": "enqueue", "content": content})
	if err != nil {
		t.Fatal(err)
	}
	return string(line)
}

func read(t *testing.T, lines ...string) []Task {
	t.Helper()
	path := filepath.Join(t.TempDir(), "session.jsonl")
	if err := os.WriteFile(path, []byte(strings.Join(lines, "\n")+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	tasks, err := Read(path, now)
	if err != nil {
		t.Fatal(err)
	}
	return tasks
}

func TestReadListsEveryTaskWithNoEndedNotification(t *testing.T) {
	at := now.Add(-time.Minute)
	tasks := read(t,
		assistantCall(t, process, "m1", "Monitor", map[string]any{"command": "tail -f ci.log", "description": "ci log", "timeout_ms": 1800000}, at),
		startedResult(t, "m1", "aaa"),
		assistantCall(t, process, "b1", "Bash", map[string]any{"command": "lr wait 49", "description": "wait", "run_in_background": true}, at),
		startedResult(t, "b1", "bbb"),
		assistantCall(t, process, "a1", "Agent", map[string]any{"name": "update-desk", "description": "desk", "run_in_background": true}, at),
		assistantCall(t, process, "f1", "Bash", map[string]any{"command": "ls"}, at),
	)
	if len(tasks) != 3 {
		t.Fatalf("tasks = %+v, want the Monitor, the background Bash and the background Agent", tasks)
	}
	for i, want := range []string{"tail -f ci.log", "lr wait 49", "update-desk"} {
		if !strings.Contains(tasks[i].Rearm, want) {
			t.Fatalf("task %d re-arm = %q, want it to carry %q", i, tasks[i].Rearm, want)
		}
	}
	if !strings.Contains(tasks[0].Line(), "task aaa") {
		t.Fatalf("line = %q, want the task id", tasks[0].Line())
	}
}

func TestReadDropsATaskThatEnded(t *testing.T) {
	at := now.Add(-time.Minute)
	for name, end := range map[string]string{
		"by tool use": ended(t, "m1", "aaa", "completed"),
		"killed":      ended(t, "m1", "aaa", "killed"),
		"by task id":  ended(t, "", "aaa", "stopped"),
	} {
		t.Run(name, func(t *testing.T) {
			tasks := read(t,
				assistantCall(t, process, "m1", "Monitor", map[string]any{"command": "x", "timeout_ms": 1800000}, at),
				startedResult(t, "m1", "aaa"),
				end,
			)
			if len(tasks) != 0 {
				t.Fatalf("tasks = %+v, want none", tasks)
			}
		})
	}
}

func TestReadKeepsATaskWhoseNotificationSaysRunning(t *testing.T) {
	tasks := read(t,
		assistantCall(t, process, "m1", "Monitor", map[string]any{"command": "x", "timeout_ms": 1800000}, now.Add(-time.Minute)),
		ended(t, "m1", "aaa", "running"),
	)
	if len(tasks) != 1 {
		t.Fatalf("tasks = %+v, want the running task", tasks)
	}
}

func TestReadLeavesOutAnEarlierProcesssTasks(t *testing.T) {
	at := now.Add(-time.Minute)
	tasks := read(t,
		assistantCall(t, "older", "m1", "Monitor", map[string]any{"command": "died at the first restart", "timeout_ms": 1800000}, at),
		assistantCall(t, process, "m2", "Monitor", map[string]any{"command": "live at the second", "timeout_ms": 1800000}, at),
	)
	if len(tasks) != 1 || !strings.Contains(tasks[0].Rearm, "live at the second") {
		t.Fatalf("tasks = %+v, want only the last process's task", tasks)
	}
}

func TestReadDropsAMonitorPastItsTimeout(t *testing.T) {
	tasks := read(t,
		assistantCall(t, process, "m1", "Monitor", map[string]any{"command": "x", "timeout_ms": 60000}, now.Add(-time.Hour)),
	)
	if len(tasks) != 0 {
		t.Fatalf("tasks = %+v, want the expired Monitor gone", tasks)
	}
}

func TestReadLeavesOutTheSlackProofMonitor(t *testing.T) {
	tasks := read(t,
		assistantCall(t, process, "m1", "Monitor", map[string]any{"command": "cc-slack watch --until-proven --session s", "timeout_ms": 1800000}, now.Add(-time.Minute)),
	)
	if len(tasks) != 0 {
		t.Fatalf("tasks = %+v, want cc-slack's own proof Monitor left out", tasks)
	}
}

func TestReadStartsAtALineBoundaryInsideALongTranscript(t *testing.T) {
	path := filepath.Join(t.TempDir(), "session.jsonl")
	file, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := file.Truncate(tailBytes + 1024); err != nil {
		t.Fatal(err)
	}
	if _, err := file.Seek(0, 2); err != nil {
		t.Fatal(err)
	}
	line := "\n" + assistantCall(t, process, "m1", "Monitor", map[string]any{"command": "tail end", "timeout_ms": 1800000}, now.Add(-time.Minute)) + "\n"
	if _, err := file.WriteString(line); err != nil {
		t.Fatal(err)
	}
	if err := file.Close(); err != nil {
		t.Fatal(err)
	}
	tasks, err := Read(path, now)
	if err != nil {
		t.Fatal(err)
	}
	if len(tasks) != 1 {
		t.Fatalf("tasks = %+v, want the task in the tail", tasks)
	}
}
