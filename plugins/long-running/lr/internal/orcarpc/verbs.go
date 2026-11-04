package orcarpc

import (
	"bytes"
	"encoding/json"
	"fmt"
	"runtime"
	"strconv"
	"strings"
	"time"
)

const (
	waitGrace     = 5 * time.Second
	flagFull      = "full"
	flagJSON      = "json"
	flagTerminal  = "terminal"
	flagWorktree  = "worktree"
	flagLimit     = "limit"
	flagRun       = "run"
	flagPeek      = "peek"
	flagAll       = "all"
	flagAck       = "ack"
	flagBrief     = "brief"
	flagScreen    = "screen"
	flagReady     = "ready"
	flagInclude   = "include-remote"
	flagTimeoutMS = "timeout-ms"
)

var booleanFlags = map[string]bool{flagFull: true, flagJSON: true, flagInclude: true, flagPeek: true, flagAll: true, flagBrief: true, flagScreen: true, flagReady: true}

type WorkerListParams struct {
	Paginate      bool   `json:"paginate"`
	Run           string `json:"run,omitempty"`
	IncludeRemote bool   `json:"includeRemote,omitempty"`
	Cursor        string `json:"cursor,omitempty"`
	Limit         int    `json:"limit,omitempty"`
}

type TerminalListParams struct {
	Worktree             string `json:"worktree,omitempty"`
	Limit                int    `json:"limit,omitempty"`
	IncludeVisualLayouts bool   `json:"includeVisualLayouts"`
}

type TerminalReadParams struct {
	Terminal string `json:"terminal"`
	Cursor   *int   `json:"cursor,omitempty"`
	Screen   bool   `json:"screen,omitempty"`
	Limit    int    `json:"limit,omitempty"`
}

type TerminalWaitParams struct {
	Terminal  string `json:"terminal"`
	For       string `json:"for"`
	TimeoutMS int    `json:"timeoutMs"`
}

type TaskListParams struct {
	Status string `json:"status,omitempty"`
	Ready  bool   `json:"ready,omitempty"`
	Brief  bool   `json:"brief,omitempty"`
	Run    string `json:"run"`
}

type WorktreeListParams struct {
	Repo  string `json:"repo,omitempty"`
	Limit int    `json:"limit,omitempty"`
}

type CheckParams struct {
	Terminal         string `json:"terminal,omitempty"`
	TerminalPaneKey  string `json:"terminalPaneKey,omitempty"`
	Unread           *bool  `json:"unread,omitempty"`
	Peek             bool   `json:"peek,omitempty"`
	All              bool   `json:"all,omitempty"`
	Types            string `json:"types,omitempty"`
	CompatibilityCLI string `json:"compatibilityCliCommand"`
	Run              string `json:"run,omitempty"`
	Ack              string `json:"ack,omitempty"`
}

func (c CheckParams) mutates() bool {
	return c.Ack != "" || (!c.Peek && !c.All)
}

type InboxParams struct {
	Limit    int    `json:"limit,omitempty"`
	Terminal string `json:"terminal,omitempty"`
}

type invocation struct {
	flags map[string]string
}

func (i invocation) has(name string) bool {
	_, set := i.flags[name]
	return set
}

func (i invocation) positive(name string) (int, bool) {
	if !i.has(name) {
		return 0, true
	}
	value, err := strconv.Atoi(i.flags[name])
	return value, err == nil && value > 0
}

type call struct {
	method  string
	params  any
	timeout time.Duration
}

type verb struct {
	allowed  []string
	required []string
	remote   bool
	offline  func(*Client) (Response, bool)
	build    func(*Client, invocation) (call, bool)
	finish   func(*Client, *Conn, invocation, json.RawMessage) (json.RawMessage, int, bool)
}

var verbs = map[string]verb{
	"orchestration worker-show": {
		allowed:  []string{"dispatch", flagFull, flagJSON},
		required: []string{"dispatch", flagFull, flagJSON},
		build: func(_ *Client, in invocation) (call, bool) {
			return call{method: "orchestration.workerShow", params: map[string]string{"dispatch": in.flags["dispatch"]}}, true
		},
	},
	"orchestration worker-list": {
		allowed:  []string{flagRun, flagInclude, flagLimit, "cursor", flagFull, flagJSON},
		required: []string{flagRun, flagFull, flagJSON},
		build: func(_ *Client, in invocation) (call, bool) {
			limit, ok := in.positive(flagLimit)
			return call{method: "orchestration.workerList", params: WorkerListParams{Paginate: true, Run: in.flags[flagRun], IncludeRemote: in.has(flagInclude), Cursor: in.flags["cursor"], Limit: limit}}, ok
		},
		finish: finishWorkerList,
	},
	"orchestration check": {
		allowed:  []string{flagTerminal, flagRun, flagPeek, flagAll, flagAck, "types", flagFull, flagJSON},
		required: []string{flagFull, flagJSON},
		build:    buildCheck,
		finish:   finishCheck,
	},
	"orchestration inbox": {
		allowed:  []string{flagTerminal, flagLimit, flagFull, flagJSON},
		required: []string{flagFull, flagJSON},
		build: func(_ *Client, in invocation) (call, bool) {
			limit, ok := in.positive(flagLimit)
			return call{method: "orchestration.inbox", params: InboxParams{Limit: limit, Terminal: in.flags[flagTerminal]}}, ok
		},
	},
	"orchestration run-show": {
		allowed:  []string{"id", flagFull, flagJSON},
		required: []string{"id", flagFull, flagJSON},
		build: func(_ *Client, in invocation) (call, bool) {
			return call{method: "orchestration.runShow", params: map[string]string{"id": in.flags["id"]}}, true
		},
	},
	"orchestration task-list": {
		allowed:  []string{flagRun, "status", flagBrief, flagReady, flagFull, flagJSON},
		required: []string{flagRun, flagFull, flagJSON},
		build: func(_ *Client, in invocation) (call, bool) {
			return call{method: "orchestration.taskList", params: TaskListParams{Status: in.flags["status"], Ready: in.has(flagReady), Brief: in.has(flagBrief), Run: in.flags[flagRun]}}, true
		},
		finish: finishTaskList,
	},
	"terminal list": {
		remote:   true,
		allowed:  []string{flagWorktree, flagLimit, flagJSON},
		required: []string{flagJSON},
		build: func(_ *Client, in invocation) (call, bool) {
			limit, ok := in.positive(flagLimit)
			return call{method: "terminal.list", params: TerminalListParams{Worktree: in.flags[flagWorktree], Limit: limit}}, ok && plainSelector(in.flags[flagWorktree])
		},
		finish: finishHostScope,
	},
	"terminal show": {
		remote:   true,
		allowed:  []string{flagTerminal, flagJSON},
		required: []string{flagTerminal, flagJSON},
		build: func(_ *Client, in invocation) (call, bool) {
			return call{method: "terminal.show", params: map[string]string{flagTerminal: in.flags[flagTerminal]}}, true
		},
	},
	"terminal read": {
		remote:   true,
		allowed:  []string{flagTerminal, flagScreen, "cursor", flagLimit, flagJSON},
		required: []string{flagTerminal, flagJSON},
		build:    buildTerminalRead,
		finish:   finishTerminalRead,
	},
	"terminal wait": {
		remote:   true,
		allowed:  []string{flagTerminal, "for", flagTimeoutMS, flagJSON},
		required: []string{flagTerminal, "for", flagTimeoutMS, flagJSON},
		build: func(_ *Client, in invocation) (call, bool) {
			timeout, ok := in.positive(flagTimeoutMS)
			return call{
				method:  "terminal.wait",
				params:  TerminalWaitParams{Terminal: in.flags[flagTerminal], For: in.flags["for"], TimeoutMS: timeout},
				timeout: time.Duration(timeout)*time.Millisecond + waitGrace,
			}, ok
		},
		finish: finishTerminalWait,
	},
	"worktree show": {
		remote:   true,
		allowed:  []string{flagWorktree, flagJSON},
		required: []string{flagWorktree, flagJSON},
		build: func(_ *Client, in invocation) (call, bool) {
			return call{method: "worktree.show", params: map[string]string{flagWorktree: in.flags[flagWorktree]}}, plainSelector(in.flags[flagWorktree])
		},
	},
	"environment list": {
		allowed:  []string{flagJSON},
		required: []string{flagJSON},
		offline:  (*Client).environmentList,
	},
	"worktree list": {
		allowed:  []string{"repo", flagLimit, flagJSON},
		required: []string{flagJSON},
		build: func(_ *Client, in invocation) (call, bool) {
			limit, ok := in.positive(flagLimit)
			return call{method: "worktree.list", params: WorktreeListParams{Repo: in.flags["repo"], Limit: limit}}, ok
		},
		finish: finishHostScope,
	},
	"repo show": {
		allowed:  []string{"repo", flagJSON},
		required: []string{"repo", flagJSON},
		build: func(_ *Client, in invocation) (call, bool) {
			return call{method: "repo.show", params: map[string]string{"repo": in.flags["repo"]}}, true
		},
	},
	"status": {
		allowed:  []string{flagJSON},
		required: []string{flagJSON},
		build: func(*Client, invocation) (call, bool) {
			return call{method: "status.get", timeout: statusRequestTimeout}, true
		},
	},
}

func plainSelector(selector string) bool {
	return selector != "active" && selector != "current" && !strings.HasPrefix(selector, "path:/mnt/") && !strings.Contains(selector, `\`)
}

func parseInvocation(args []string) (string, invocation, bool) {
	end := 0
	for end < len(args) && !strings.HasPrefix(args[end], "-") {
		end++
	}
	in := invocation{flags: map[string]string{}}
	for index := end; index < len(args); index++ {
		name, flag := strings.CutPrefix(args[index], "--")
		if !flag || strings.Contains(name, "=") || in.has(name) {
			return "", in, false
		}
		if booleanFlags[name] {
			in.flags[name] = ""
			continue
		}
		index++
		if index == len(args) || args[index] == "" || strings.HasPrefix(args[index], "-") {
			return "", in, false
		}
		in.flags[name] = args[index]
	}
	return strings.Join(args[:end], " "), in, true
}

func (v verb) accepts(in invocation) bool {
	for name := range in.flags {
		if !contains(v.allowed, name) && (name != flagEnvironment || !v.remote) {
			return false
		}
	}
	for _, name := range v.required {
		if !in.has(name) {
			return false
		}
	}
	return true
}

func contains(names []string, name string) bool {
	for _, candidate := range names {
		if candidate == name {
			return true
		}
	}
	return false
}

func buildCheck(c *Client, in invocation) (call, bool) {
	if in.has(flagPeek) && in.has(flagAll) {
		return call{}, false
	}
	params := CheckParams{Terminal: in.flags[flagTerminal], Peek: in.has(flagPeek), All: in.has(flagAll), Types: in.flags["types"], CompatibilityCLI: c.cliCommand(), Run: in.flags[flagRun], Ack: in.flags[flagAck]}
	if params.mutates() && params.Run == "" {
		return call{}, false
	}
	if params.Peek {
		unread := false
		params.Unread = &unread
	}
	switch session := strings.TrimSpace(c.getenv("ORCA_AGENT_SESSION_ID")); {
	case session != "":
		address := sessionAddressPrefix + session
		switch {
		case params.Terminal == "", params.Terminal == session, params.Terminal == address:
			params.Terminal = ""
		case !strings.HasPrefix(params.Terminal, sessionAddressPrefix):
			return call{}, false
		}
	case params.Terminal == "":
		params.Terminal = bounded(c.getenv("ORCA_TERMINAL_HANDLE"))
		if params.Terminal == "" {
			return call{}, false
		}
		params.TerminalPaneKey = bounded(c.getenv("ORCA_PANE_KEY"))
	}
	return call{method: checkMethod, params: params}, true
}

func (c *Client) cliCommand() string {
	switch configured := c.getenv("ORCA_CLI_COMMAND"); configured {
	case "orca", "orca-ide", "orca-dev":
		return configured
	}
	if runtime.GOOS == "linux" {
		return "orca-ide"
	}
	return "orca"
}

func buildTerminalRead(_ *Client, in invocation) (call, bool) {
	limit, ok := in.positive(flagLimit)
	if !ok || (in.has(flagScreen) && in.has("cursor")) {
		return call{}, false
	}
	params := TerminalReadParams{Terminal: in.flags[flagTerminal], Screen: in.has(flagScreen), Limit: limit}
	if in.has("cursor") {
		cursor, err := strconv.Atoi(in.flags["cursor"])
		if err != nil || cursor < 0 {
			return call{}, false
		}
		params.Cursor = &cursor
	}
	return call{method: "terminal.read", params: params}, true
}

func finishWorkerList(_ *Client, _ *Conn, in invocation, result json.RawMessage) (json.RawMessage, int, bool) {
	var listed struct {
		Page json.RawMessage `json:"page"`
	}
	if json.Unmarshal(result, &listed) != nil || len(listed.Page) == 0 || string(listed.Page) == "null" {
		return nil, 0, false
	}
	scope, err := marshal(map[string]string{flagRun: in.flags[flagRun], "source": "flag"})
	if err != nil {
		return nil, 0, false
	}
	return withField(result, "scope", scope), 0, true
}

func finishCheck(_ *Client, _ *Conn, in invocation, result json.RawMessage) (json.RawMessage, int, bool) {
	var checked struct {
		Messages []struct {
			Read *int `json:"read"`
		} `json:"messages"`
		Legacy *struct {
			Ack []json.RawMessage `json:"ackMessageIds"`
		} `json:"legacyCompatibility"`
	}
	if json.Unmarshal(result, &checked) != nil || (checked.Legacy != nil && len(checked.Legacy.Ack) > 0) {
		return nil, 0, false
	}
	if in.has(flagPeek) {
		for _, message := range checked.Messages {
			if message.Read != nil && *message.Read == 1 {
				return nil, 0, false
			}
		}
	}
	return result, 0, true
}

func finishTaskList(_ *Client, _ *Conn, in invocation, result json.RawMessage) (json.RawMessage, int, bool) {
	if !in.has(flagBrief) {
		return result, 0, true
	}
	var listed struct {
		Tasks []map[string]json.RawMessage `json:"tasks"`
	}
	if json.Unmarshal(result, &listed) != nil {
		return nil, 0, false
	}
	for _, task := range listed.Tasks {
		if _, reported := task["spec_truncated"]; !reported {
			return nil, 0, false
		}
	}
	return result, 0, true
}

func finishTerminalRead(_ *Client, _ *Conn, in invocation, result json.RawMessage) (json.RawMessage, int, bool) {
	if !in.has(flagScreen) {
		return result, 0, true
	}
	var read struct {
		Terminal struct {
			Source *string `json:"source"`
		} `json:"terminal"`
	}
	return result, 0, json.Unmarshal(result, &read) == nil && read.Terminal.Source != nil
}

func finishTerminalWait(_ *Client, _ *Conn, _ invocation, result json.RawMessage) (json.RawMessage, int, bool) {
	var waited struct {
		Wait struct {
			Satisfied *bool `json:"satisfied"`
		} `json:"wait"`
	}
	if json.Unmarshal(result, &waited) != nil {
		return nil, 0, false
	}
	if waited.Wait.Satisfied != nil && !*waited.Wait.Satisfied {
		return result, 1, true
	}
	return result, 0, true
}

func finishHostScope(c *Client, _ *Conn, _ invocation, result json.RawMessage) (json.RawMessage, int, bool) {
	fields, err := orderedFields(result)
	if err != nil {
		return nil, 0, false
	}
	for index, field := range fields {
		if field.name != "hostScope" {
			continue
		}
		var scope struct {
			Omitted []string `json:"omittedHostIds"`
		}
		if json.Unmarshal(field.value, &scope) != nil {
			return nil, 0, false
		}
		if len(scope.Omitted) == 0 {
			return result, 0, true
		}
		selectors, err := c.omittedSelectors(scope.Omitted)
		if err != nil {
			return nil, 0, false
		}
		fields[index].value = withField(field.value, "omittedHostSelectors", selectors)
		joined, err := joinFields(fields)
		return joined, 0, err == nil
	}
	return result, 0, true
}

type jsonField struct {
	name  string
	value json.RawMessage
}

func orderedFields(object json.RawMessage) ([]jsonField, error) {
	decoder := json.NewDecoder(bytes.NewReader(object))
	if _, err := decoder.Token(); err != nil {
		return nil, err
	}
	var fields []jsonField
	for decoder.More() {
		name, err := decoder.Token()
		if err != nil {
			return nil, err
		}
		var value json.RawMessage
		if err := decoder.Decode(&value); err != nil {
			return nil, err
		}
		fields = append(fields, jsonField{name: name.(string), value: value})
	}
	return fields, nil
}

func joinFields(fields []jsonField) (json.RawMessage, error) {
	var out bytes.Buffer
	out.WriteByte('{')
	for index, field := range fields {
		if index > 0 {
			out.WriteByte(',')
		}
		name, err := marshal(field.name)
		if err != nil {
			return nil, err
		}
		out.Write(name)
		out.WriteByte(':')
		out.Write(field.value)
	}
	out.WriteByte('}')
	return out.Bytes(), nil
}

type runtimeStatus struct {
	RuntimeID           string          `json:"runtimeId"`
	GraphStatus         string          `json:"graphStatus"`
	AppVersion          string          `json:"appVersion"`
	DesktopWindowStatus string          `json:"desktopWindowStatus"`
	AuthoritativeWindow *int64          `json:"authoritativeWindowId"`
	RemoteUpdateSupport json.RawMessage `json:"remoteUpdateSupport"`
	Capabilities        []string        `json:"capabilities"`
	Degradations        json.RawMessage `json:"degradations"`
	RemoteControl       *remoteControl  `json:"remoteControl"`
	ProtocolVersion     int             `json:"runtimeProtocolVersion"`
	MinClientVersion    int             `json:"minCompatibleRuntimeClientVersion"`
}

type remoteControl struct {
	State string `json:"state"`
}

func (s runtimeStatus) connectionState() string {
	if s.RemoteControl != nil {
		switch s.RemoteControl.State {
		case "reconnecting":
			return "reconnecting"
		case "closed":
			return "disconnected"
		case "ready":
		default:
			return "checking"
		}
	}
	if s.GraphStatus != "ready" && s.DesktopWindowStatus == "openable" {
		return "workspace-window-closed"
	}
	return "connected"
}

func (s runtimeStatus) windowStatus() string {
	if s.DesktopWindowStatus != "" {
		return s.DesktopWindowStatus
	}
	if s.AuthoritativeWindow != nil && *s.AuthoritativeWindow > 0 {
		return "available"
	}
	return ""
}

type statusApp struct {
	Running             bool   `json:"running"`
	PID                 int    `json:"pid"`
	DesktopWindowStatus string `json:"desktopWindowStatus,omitempty"`
}

type statusRuntime struct {
	State               string          `json:"state"`
	Reachable           bool            `json:"reachable"`
	ConnectionState     string          `json:"connectionState"`
	RuntimeID           string          `json:"runtimeId"`
	AppVersion          string          `json:"appVersion,omitempty"`
	RemoteUpdateSupport json.RawMessage `json:"remoteUpdateSupport,omitempty"`
	Capabilities        []string        `json:"capabilities,omitempty"`
	Degradations        json.RawMessage `json:"degradations,omitempty"`
}

type statusGraph struct {
	State string `json:"state"`
}

type statusTarget struct {
	Kind string `json:"kind"`
}

type cliStatus struct {
	Target  statusTarget  `json:"target"`
	App     statusApp     `json:"app"`
	Runtime statusRuntime `json:"runtime"`
	Graph   statusGraph   `json:"graph"`
}

func projectStatus(pid int, reply Response) (Response, error) {
	var status runtimeStatus
	if err := json.Unmarshal(reply.Result, &status); err != nil {
		return Response{}, fmt.Errorf("orcarpc: parse status: %w", err)
	}
	state := "graph_not_ready"
	if status.GraphStatus == "ready" {
		state = "ready"
	}
	projected, err := marshal(cliStatus{
		Target: statusTarget{Kind: "local"},
		App:    statusApp{Running: true, PID: pid, DesktopWindowStatus: status.windowStatus()},
		Runtime: statusRuntime{
			State:               state,
			Reachable:           true,
			ConnectionState:     status.connectionState(),
			RuntimeID:           status.RuntimeID,
			AppVersion:          status.AppVersion,
			RemoteUpdateSupport: status.RemoteUpdateSupport,
			Capabilities:        status.Capabilities,
			Degradations:        status.Degradations,
		},
		Graph: statusGraph{State: status.GraphStatus},
	})
	if err != nil {
		return Response{}, err
	}
	meta, err := marshal(map[string]string{"runtimeId": status.RuntimeID})
	if err != nil {
		return Response{}, err
	}
	return Response{ID: "local-status", OK: true, Result: projected, Meta: meta}, nil
}

func withField(object json.RawMessage, name string, value json.RawMessage) json.RawMessage {
	trimmed := bytes.TrimRight(object, " \n\t")
	if len(trimmed) < 2 || trimmed[len(trimmed)-1] != '}' {
		return object
	}
	var out bytes.Buffer
	out.Write(trimmed[:len(trimmed)-1])
	if len(bytes.TrimSpace(trimmed[1:len(trimmed)-1])) > 0 {
		out.WriteByte(',')
	}
	fmt.Fprintf(&out, "%q:", name)
	out.Write(value)
	out.WriteByte('}')
	return out.Bytes()
}

func Render(reply Response) ([]byte, error) {
	id, err := marshal(reply.ID)
	if err != nil {
		return nil, err
	}
	var compact bytes.Buffer
	if reply.OK {
		fmt.Fprintf(&compact, `{"id":%s,"ok":true,"result":%s,"_meta":%s}`, id, reply.Result, reply.Meta)
	} else {
		failure, err := marshal(reply.Error)
		if err != nil {
			return nil, err
		}
		fmt.Fprintf(&compact, `{"id":%s,"ok":false,"error":%s,"_meta":%s}`, id, failure, reply.Meta)
	}
	var pretty bytes.Buffer
	if err := json.Indent(&pretty, compact.Bytes(), "", "  "); err != nil {
		return nil, err
	}
	pretty.WriteByte('\n')
	return pretty.Bytes(), nil
}

func marshal(value any) ([]byte, error) {
	var out bytes.Buffer
	encoder := json.NewEncoder(&out)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(value); err != nil {
		return nil, err
	}
	return bytes.TrimRight(out.Bytes(), "\n"), nil
}
