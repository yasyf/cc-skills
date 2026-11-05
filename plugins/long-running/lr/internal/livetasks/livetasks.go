// Package livetasks reads a session's transcript for the background tasks it
// started and never heard end, with the call that starts each again.
package livetasks

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"regexp"
	"strconv"
	"strings"
	"time"
)

const (
	tailBytes        = 64 << 20
	maxLine          = 64 << 20
	defaultMonitorMs = 300_000
	selfRearmed      = "cc-slack watch --until-proven"
)

var (
	notification = regexp.MustCompile(`<task-notification>.*?</task-notification>`)
	taskID       = regexp.MustCompile(`<task-id>([^<]+)</task-id>`)
	toolUseID    = regexp.MustCompile(`<tool-use-id>([^<]+)</tool-use-id>`)
	status       = regexp.MustCompile(`<status>([^<]+)</status>`)
	interesting  = [][]byte{[]byte(`"tool_use"`), []byte(`toolUseResult`), []byte(`<task-notification>`), []byte(`"type":"assistant"`)}
)

// Task is one background task a transcript shows started with no ended notification.
type Task struct {
	Kind        string
	ID          string
	Description string
	Command     string
	At          time.Time
	Rearm       string
}

// Line renders the task for a handoff or a session's context.
func (t Task) Line() string {
	name := fmt.Sprintf("%s %q", t.Kind, t.Description)
	if t.ID != "" {
		name += " (task " + t.ID + ")"
	}
	return "- " + name + ": " + t.Rearm
}

type entry struct {
	Type      string    `json:"type"`
	Timestamp time.Time `json:"timestamp"`
	Process   string    `json:"session_id"`
	Session   string    `json:"sessionId"`
	Message   struct {
		Content json.RawMessage `json:"content"`
	} `json:"message"`
	Result json.RawMessage `json:"toolUseResult"`
}

type block struct {
	Type      string          `json:"type"`
	ID        string          `json:"id"`
	Name      string          `json:"name"`
	Input     json.RawMessage `json:"input"`
	ToolUseID string          `json:"tool_use_id"`
}

type call struct {
	Command         string `json:"command"`
	Description     string `json:"description"`
	Name            string `json:"name"`
	TimeoutMs       scalar `json:"timeout_ms"`
	RunInBackground scalar `json:"run_in_background"`
}

// scalar reads a tool input a transcript may record as a JSON string or as its
// plain value: Claude Code keeps the form the model sent.
type scalar string

func (s *scalar) UnmarshalJSON(raw []byte) error {
	*s = scalar(strings.Trim(string(raw), `"`))
	return nil
}

type started struct {
	call    call
	kind    string
	raw     string
	process string
	at      time.Time
	task    string
}

type result struct {
	TaskID           string `json:"taskId"`
	BackgroundTaskID string `json:"backgroundTaskId"`
	AgentID          string `json:"agentId"`
}

// Read returns the tasks live in the session's last process, oldest first, from
// a fixed tail of the transcript so a long session costs the same as a short one.
// A Monitor past its own timeout is gone whatever the transcript says.
func Read(path string, now time.Time) ([]Task, error) {
	file, err := os.Open(path) //nolint:gosec // G304: the hook names this session's own transcript.
	if err != nil {
		return nil, fmt.Errorf("open transcript: %w", err)
	}
	defer func() { _ = file.Close() }()
	info, err := file.Stat()
	if err != nil {
		return nil, fmt.Errorf("stat transcript: %w", err)
	}
	offset := max(info.Size()-tailBytes, 0)
	if _, err := file.Seek(offset, io.SeekStart); err != nil {
		return nil, fmt.Errorf("seek transcript: %w", err)
	}
	reader := bufio.NewReaderSize(file, 1<<20)
	if offset > 0 {
		if _, err := reader.ReadBytes('\n'); err != nil {
			return nil, nil
		}
	}
	return scan(reader, now)
}

func scan(reader *bufio.Reader, now time.Time) ([]Task, error) {
	var (
		order       []string
		calls       = map[string]*started{}
		endedTools  = map[string]bool{}
		endedTasks  = map[string]bool{}
		lastProcess string
	)
	for {
		line, err := reader.ReadBytes('\n')
		if len(line) > 0 && len(line) <= maxLine && mayMatter(line) {
			var e entry
			if json.Unmarshal(line, &e) == nil {
				switch e.Type {
				case "assistant":
					lastProcess = processOf(e)
					for _, b := range blocksOf(e) {
						if s, ok := background(b); ok {
							s.process, s.at = lastProcess, e.Timestamp
							calls[b.ID] = s
							order = append(order, b.ID)
						}
					}
				case "user":
					noteResult(e, calls)
					noteEnded(line, endedTools, endedTasks)
				case "queue-operation":
					noteEnded(line, endedTools, endedTasks)
				}
			}
		}
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, fmt.Errorf("read transcript: %w", err)
		}
	}
	var tasks []Task
	for _, id := range order {
		s := calls[id]
		if s.process != lastProcess || endedTools[id] || (s.task != "" && endedTasks[s.task]) || s.expired(now) {
			continue
		}
		if strings.Contains(s.call.Command, selfRearmed) {
			continue
		}
		tasks = append(tasks, s.render())
	}
	return tasks, nil
}

func mayMatter(line []byte) bool {
	for _, marker := range interesting {
		if bytes.Contains(line, marker) {
			return true
		}
	}
	return false
}

func processOf(e entry) string {
	if e.Process != "" {
		return e.Process
	}
	return e.Session
}

func blocksOf(e entry) []block {
	var blocks []block
	if json.Unmarshal(e.Message.Content, &blocks) != nil {
		return nil
	}
	return blocks
}

func background(b block) (*started, bool) {
	if b.Type != "tool_use" {
		return nil, false
	}
	var c call
	if json.Unmarshal(b.Input, &c) != nil {
		return nil, false
	}
	switch {
	case b.Name == "Monitor":
		return &started{call: c, kind: "Monitor", raw: string(b.Input)}, true
	case (b.Name == "Bash" || b.Name == "Agent") && c.RunInBackground == "true":
		return &started{call: c, kind: b.Name, raw: string(b.Input)}, true
	}
	return nil, false
}

func noteResult(e entry, calls map[string]*started) {
	var r result
	if len(e.Result) == 0 || json.Unmarshal(e.Result, &r) != nil {
		return
	}
	for _, b := range blocksOf(e) {
		if s, ok := calls[b.ToolUseID]; ok {
			s.task = firstOf(r.TaskID, r.BackgroundTaskID, r.AgentID)
		}
	}
}

func noteEnded(line []byte, tools, tasks map[string]bool) {
	for _, found := range notification.FindAll(line, -1) {
		state := status.FindSubmatch(found)
		if state == nil || string(state[1]) == "running" {
			continue
		}
		if m := toolUseID.FindSubmatch(found); m != nil {
			tools[string(m[1])] = true
		}
		if m := taskID.FindSubmatch(found); m != nil {
			tasks[string(m[1])] = true
		}
	}
}

func firstOf(values ...string) string {
	for _, value := range values {
		if value != "" {
			return value
		}
	}
	return ""
}

func (s *started) expired(now time.Time) bool {
	if s.kind != "Monitor" {
		return false
	}
	timeout, err := strconv.ParseInt(string(s.call.TimeoutMs), 10, 64)
	if err != nil || timeout == 0 {
		timeout = defaultMonitorMs
	}
	return s.at.Add(time.Duration(timeout) * time.Millisecond).Before(now)
}

func (s *started) render() Task {
	task := Task{Kind: s.kind, ID: s.task, Description: firstOf(s.call.Description, s.call.Name, s.call.Command), Command: s.call.Command, At: s.at}
	switch s.kind {
	case "Bash":
		task.Rearm = "`Bash` with `run_in_background: true` and `" + s.raw + "`"
	case "Agent":
		task.Rearm = "`SendMessage` to `" + firstOf(s.call.Name, s.task) + "`, which resumes it"
	default:
		task.Rearm = "`Monitor` with `" + s.raw + "`"
	}
	return task
}

// Section renders the tasks under a heading line, or nothing when there are none.
func Section(heading string, tasks []Task) string {
	if len(tasks) == 0 {
		return ""
	}
	var out strings.Builder
	out.WriteString(heading + "\n")
	for _, task := range tasks {
		out.WriteString(task.Line() + "\n")
	}
	return out.String()
}
