package orcarpc

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"runtime"
	"strings"
)

type WorkerListParams struct {
	Paginate      bool   `json:"paginate"`
	Run           string `json:"run,omitempty"`
	TerminalState string `json:"terminalState,omitempty"`
	IncludeRemote bool   `json:"includeRemote,omitempty"`
	Cursor        string `json:"cursor,omitempty"`
	Limit         int    `json:"limit,omitempty"`
}

type TerminalListParams struct {
	Worktree             string `json:"worktree,omitempty"`
	Limit                int    `json:"limit,omitempty"`
	IncludeVisualLayouts bool   `json:"includeVisualLayouts"`
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
}

func (c *Client) CheckRead(ctx context.Context, params CheckParams) (Response, error) {
	if params.Peek {
		unread := false
		params.Unread = &unread
	}
	params.CompatibilityCLI = c.cliCommand()
	switch session := strings.TrimSpace(c.getenv("ORCA_AGENT_SESSION_ID")); {
	case session != "":
		address := sessionAddressPrefix + session
		switch {
		case params.Terminal == "", params.Terminal == session, params.Terminal == address:
			params.Terminal = ""
		case !strings.HasPrefix(params.Terminal, sessionAddressPrefix):
			return Response{}, fmt.Errorf("%w: %s", ErrOtherCaller, params.Terminal)
		}
	case params.Terminal == "":
		params.Terminal = bounded(c.getenv("ORCA_TERMINAL_HANDLE"))
		if params.Terminal == "" {
			return Response{}, ErrNoCaller
		}
		params.TerminalPaneKey = bounded(c.getenv("ORCA_PANE_KEY"))
	}
	return c.Call(ctx, checkMethod, params)
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

func (c *Client) WorkerList(ctx context.Context, params WorkerListParams) (Response, error) {
	params.Paginate = true
	reply, err := c.Call(ctx, "orchestration.workerList", params)
	if err != nil || !reply.OK || params.Run == "" {
		return reply, err
	}
	scope, err := marshal(map[string]string{"run": params.Run, "source": "flag"})
	if err != nil {
		return Response{}, err
	}
	reply.Result = withField(reply.Result, "scope", scope)
	return reply, nil
}

func (c *Client) WorkerShow(ctx context.Context, dispatch string) (Response, error) {
	return c.Call(ctx, "orchestration.workerShow", map[string]string{"dispatch": dispatch})
}

func (c *Client) TerminalList(ctx context.Context, params TerminalListParams) (Response, error) {
	return c.Call(ctx, "terminal.list", params)
}

func (c *Client) TerminalShow(ctx context.Context, terminal string) (Response, error) {
	return c.Call(ctx, "terminal.show", map[string]string{"terminal": terminal})
}

func (c *Client) WorktreeShow(ctx context.Context, worktree string) (Response, error) {
	return c.Call(ctx, "worktree.show", map[string]string{"worktree": worktree})
}

type runtimeStatus struct {
	RuntimeID           string          `json:"runtimeId"`
	GraphStatus         string          `json:"graphStatus"`
	AppVersion          string          `json:"appVersion"`
	DesktopWindowStatus string          `json:"desktopWindowStatus"`
	AuthoritativeWindow *int64          `json:"authoritativeWindowId"`
	RemoteUpdateSupport json.RawMessage `json:"remoteUpdateSupport"`
	Capabilities        json.RawMessage `json:"capabilities"`
	Degradations        json.RawMessage `json:"degradations"`
	RemoteControl       *remoteControl  `json:"remoteControl"`
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
	Capabilities        json.RawMessage `json:"capabilities,omitempty"`
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

func (c *Client) Status(ctx context.Context) (Response, error) {
	probe := *c
	probe.Timeout = statusRequestTimeout
	conn, err := probe.Dial(ctx)
	if err != nil {
		return Response{}, err
	}
	defer func() { _ = conn.Close() }()
	reply, err := conn.Call(ctx, "status.get", nil)
	if err != nil || !reply.OK {
		return reply, err
	}
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
		App:    statusApp{Running: true, PID: conn.Metadata().PID, DesktopWindowStatus: status.windowStatus()},
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
