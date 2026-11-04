package orcarpc

import (
	"bufio"
	"context"
	"crypto/rand"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

const (
	metadataFile           = "orca-runtime.json"
	defaultRequestTimeout  = 60 * time.Second
	statusRequestTimeout   = time.Second
	orchestrationPrefix    = "orchestration."
	orchestrationContract  = 1
	checkMethod            = "orchestration.check"
	sessionAddressPrefix   = "orca_session_id:"
	maxEvidenceFieldLength = 4096
)

var (
	ErrRemoteSelected = errors.New("orcarpc: a paired runtime is selected, and only the local socket is supported")
	ErrWriteMethod    = errors.New("orcarpc: only read methods are supported")
)

var readMethods = map[string]bool{
	"status.get":               true,
	"orchestration.workerList": true,
	"orchestration.workerShow": true,
	"orchestration.runList":    true,
	"orchestration.runShow":    true,
	"orchestration.taskList":   true,
	"orchestration.inbox":      true,
	"terminal.list":            true,
	"terminal.show":            true,
	"terminal.read":            true,
	"terminal.wait":            true,
	"worktree.show":            true,
	"worktree.list":            true,
	"repo.show":                true,
}

func readOnly(method string, params any) bool {
	if method == checkMethod {
		check, typed := params.(CheckParams)
		return typed && !check.mutates()
	}
	return readMethods[method]
}

type Transport struct {
	Kind     string `json:"kind"`
	Endpoint string `json:"endpoint"`
}

type Metadata struct {
	RuntimeID  string      `json:"runtimeId"`
	PID        int         `json:"pid"`
	Transports []Transport `json:"transports"`
	AuthToken  string      `json:"authToken"`
	StartedAt  int64       `json:"startedAt"`
}

func (m Metadata) socket() string {
	for _, transport := range m.Transports {
		if transport.Kind == "unix" {
			return transport.Endpoint
		}
	}
	return ""
}

type RPCError struct {
	Code    string          `json:"code"`
	Message string          `json:"message"`
	Data    json.RawMessage `json:"data,omitempty"`
}

func (e *RPCError) Error() string {
	return e.Code + ": " + e.Message
}

type Meta struct {
	RuntimeID *string `json:"runtimeId"`
}

type Response struct {
	ID        string          `json:"id"`
	OK        bool            `json:"ok"`
	Result    json.RawMessage `json:"result,omitempty"`
	Error     *RPCError       `json:"error,omitempty"`
	Meta      json.RawMessage `json:"_meta,omitempty"`
	Keepalive bool            `json:"_keepalive,omitempty"`
}

type expectedSource struct {
	SourceID  string `json:"sourceId"`
	RuntimeID string `json:"runtimeId"`
}

type request struct {
	ID                    string          `json:"id"`
	AuthToken             string          `json:"authToken"`
	Method                string          `json:"method"`
	Params                any             `json:"params,omitempty"`
	ContractVersion       int             `json:"orchestrationContractVersion,omitempty"`
	CompatibilityID       string          `json:"compatibilityInvocationId,omitempty"`
	CompatibilityEvidence map[string]any  `json:"orchestrationCompatibilityEvidence,omitempty"`
	RequestID             string          `json:"orchestrationRequestId,omitempty"`
	ExpectedRuntimeSource *expectedSource `json:"expectedRuntimeSource,omitempty"`
}

type Client struct {
	UserData string
	Getenv   func(string) string
	Timeout  time.Duration
	Gate     *Gate
}

func New() *Client {
	return &Client{Getenv: os.Getenv}
}

func (c *Client) getenv(name string) string {
	if c.Getenv == nil {
		return os.Getenv(name)
	}
	return c.Getenv(name)
}

func (c *Client) userDataPath() (string, error) {
	if c.UserData != "" {
		return c.UserData, nil
	}
	if path := c.getenv("ORCA_USER_DATA_PATH"); path != "" {
		return path, nil
	}
	return platformUserDataPath(c.getenv)
}

func platformUserDataPath(getenv func(string) string) (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	switch runtime.GOOS {
	case "darwin":
		return filepath.Join(home, "Library", "Application Support", "orca"), nil
	case "windows":
		return filepath.Join(getenv("APPDATA"), "orca"), nil
	}
	if config := getenv("XDG_CONFIG_HOME"); config != "" {
		return filepath.Join(config, "orca"), nil
	}
	return filepath.Join(home, ".config", "orca"), nil
}

func (c *Client) ReadMetadata() (Metadata, error) {
	dir, err := c.userDataPath()
	if err != nil {
		return Metadata{}, err
	}
	raw, err := os.ReadFile(filepath.Join(dir, metadataFile))
	if err != nil {
		return Metadata{}, fmt.Errorf("orcarpc: read runtime metadata: %w", err)
	}
	var metadata Metadata
	if err := json.Unmarshal(raw, &metadata); err != nil {
		return Metadata{}, fmt.Errorf("orcarpc: parse runtime metadata: %w", err)
	}
	if metadata.socket() == "" || metadata.AuthToken == "" {
		return Metadata{}, fmt.Errorf("orcarpc: runtime metadata in %s has no socket or token", dir)
	}
	return metadata, nil
}

func (c *Client) refuseRemote() error {
	for _, name := range []string{"ORCA_PAIRING_CODE", "ORCA_REMOTE_PAIRING", "ORCA_ENVIRONMENT"} {
		if c.getenv(name) != "" {
			return fmt.Errorf("%w: %s", ErrRemoteSelected, name)
		}
	}
	return nil
}

type Conn struct {
	client   *Client
	metadata Metadata
	socket   net.Conn
	reader   *bufio.Reader
	fence    *expectedSource
}

func (c *Client) Dial(ctx context.Context) (*Conn, error) {
	if err := c.refuseRemote(); err != nil {
		return nil, err
	}
	metadata, err := c.ReadMetadata()
	if err != nil {
		return nil, err
	}
	var dialer net.Dialer
	socket, err := dialer.DialContext(ctx, "unix", metadata.socket())
	if err != nil {
		return nil, fmt.Errorf("orcarpc: dial runtime: %w", err)
	}
	conn := &Conn{client: c, metadata: metadata, socket: socket, reader: bufio.NewReader(socket)}
	if err := conn.checkSource(ctx); err != nil {
		_ = socket.Close()
		return nil, err
	}
	return conn, nil
}

func (c *Conn) Close() error {
	return c.socket.Close()
}

func (c *Conn) Call(ctx context.Context, method string, params any) (Response, error) {
	return c.call(ctx, method, params, 0)
}

func (c *Conn) call(ctx context.Context, method string, params any, timeout time.Duration) (Response, error) {
	if !readOnly(method, params) {
		return Response{}, fmt.Errorf("%w: %s", ErrWriteMethod, method)
	}
	return c.send(ctx, method, params, timeout, "")
}

func (c *Conn) mutateCheck(ctx context.Context, check CheckParams, timeout time.Duration, requestID string) (Response, error) {
	return c.send(ctx, checkMethod, check, timeout, requestID)
}

func (c *Conn) send(ctx context.Context, method string, params any, timeout time.Duration, requestID string) (Response, error) {
	id, err := newUUID()
	if err != nil {
		return Response{}, err
	}
	outgoing := request{ID: id, AuthToken: c.metadata.AuthToken, Method: method, Params: params}
	if strings.HasPrefix(method, orchestrationPrefix) {
		outgoing.ContractVersion = orchestrationContract
		outgoing.CompatibilityEvidence = c.client.evidence()
		if outgoing.CompatibilityID, err = newUUID(); err != nil {
			return Response{}, err
		}
	}
	if requestID != "" {
		outgoing.RequestID, outgoing.CompatibilityID = requestID, requestID
	}
	if method != "status.get" {
		outgoing.ExpectedRuntimeSource = c.fence
	}
	if timeout == 0 {
		timeout = c.client.Timeout
	}
	if timeout == 0 {
		timeout = defaultRequestTimeout
	}
	return c.exchange(ctx, outgoing, timeout)
}

func (c *Conn) exchange(ctx context.Context, outgoing request, timeout time.Duration) (Response, error) {
	frame, err := json.Marshal(outgoing) //nolint:gosec // the frame carries the runtime token the Orca protocol requires
	if err != nil {
		return Response{}, err
	}
	deadline := time.Now().Add(timeout)
	if limit, ok := ctx.Deadline(); ok && limit.Before(deadline) {
		deadline = limit
	}
	if err := c.socket.SetDeadline(deadline); err != nil {
		return Response{}, err
	}
	if _, err := c.socket.Write(append(frame, '\n')); err != nil {
		return Response{}, fmt.Errorf("orcarpc: send %s: %w", outgoing.Method, err)
	}
	for {
		line, err := c.reader.ReadBytes('\n')
		if err != nil {
			return Response{}, fmt.Errorf("orcarpc: read %s: %w", outgoing.Method, err)
		}
		var response Response
		if err := json.Unmarshal(line, &response); err != nil {
			return Response{}, fmt.Errorf("orcarpc: bad frame for %s: %w", outgoing.Method, err)
		}
		if response.Keepalive {
			if err := c.socket.SetDeadline(time.Now().Add(timeout)); err != nil {
				return Response{}, err
			}
			continue
		}
		if response.ID != outgoing.ID {
			return Response{}, fmt.Errorf("orcarpc: %s answered with id %q, want %q", outgoing.Method, response.ID, outgoing.ID)
		}
		if err := c.sameRuntime(response); err != nil {
			return Response{}, err
		}
		return response, nil
	}
}

func (c *Conn) sameRuntime(response Response) error {
	var meta Meta
	if len(response.Meta) == 0 {
		return nil
	}
	if err := json.Unmarshal(response.Meta, &meta); err != nil {
		return fmt.Errorf("orcarpc: bad _meta: %w", err)
	}
	if meta.RuntimeID != nil && *meta.RuntimeID != "" && *meta.RuntimeID != c.metadata.RuntimeID {
		return fmt.Errorf("orcarpc: the runtime changed from %s to %s in flight", c.metadata.RuntimeID, *meta.RuntimeID)
	}
	return nil
}

func (c *Conn) checkSource(ctx context.Context) error {
	sourceID := bounded(c.client.getenv("ORCA_RUNTIME_SOURCE_ID"))
	incarnation := bounded(c.client.getenv("ORCA_RUNTIME_SOURCE_INCARNATION"))
	if sourceID == "" || incarnation == "" {
		return nil
	}
	fenced, err := c.client.fenced()
	if err != nil || !fenced {
		return err
	}
	c.fence = &expectedSource{SourceID: sourceID, RuntimeID: c.metadata.RuntimeID}
	if c.metadata.RuntimeID == incarnation {
		return nil
	}
	reply, err := c.Call(ctx, "status.get", nil)
	if err != nil {
		return err
	}
	if !reply.OK {
		return fmt.Errorf("orcarpc: runtime_source_unverifiable: %w", reply.Error)
	}
	var status struct {
		HostDescriptor struct {
			InstallationID string `json:"installationId"`
		} `json:"hostDescriptor"`
	}
	if err := json.Unmarshal(reply.Result, &status); err != nil {
		return fmt.Errorf("orcarpc: parse status: %w", err)
	}
	if status.HostDescriptor.InstallationID != sourceID {
		return fmt.Errorf("orcarpc: runtime_source_mismatch: running %q, launched by %q", status.HostDescriptor.InstallationID, sourceID)
	}
	return nil
}

func (c *Client) fenced() (bool, error) {
	profile := bounded(c.getenv("ORCA_RUNTIME_SOURCE_PROFILE_PATH"))
	dir, err := c.userDataPath()
	if err != nil {
		return false, err
	}
	if profile == "" || sameDir(dir, profile) {
		return true, nil
	}
	platform, err := platformUserDataPath(c.getenv)
	if err != nil {
		return false, nil
	}
	return sameDir(dir, platform), nil
}

func sameDir(left, right string) bool {
	return canonical(left) == canonical(right)
}

func canonical(path string) string {
	absolute, err := filepath.Abs(path)
	if err != nil {
		return path
	}
	resolved, err := filepath.EvalSymlinks(absolute)
	if err != nil {
		return absolute
	}
	return resolved
}

func (c *Client) evidence() map[string]any {
	evidence := map[string]any{}
	for field, name := range map[string]string{ //nolint:gosec // environment variable names, not credentials
		"terminalHandle": "ORCA_TERMINAL_HANDLE",
		"paneKey":        "ORCA_PANE_KEY",
		"launchToken":    "ORCA_AGENT_LAUNCH_TOKEN",
	} {
		if value := bounded(c.getenv(name)); value != "" {
			evidence[field] = value
		}
	}
	if host := c.hostStamp(); host != nil {
		evidence["host"] = host
	}
	if session := strings.TrimSpace(c.getenv("ORCA_AGENT_SESSION_ID")); session != "" {
		evidence["agentSessionId"] = session
	}
	if len(evidence) == 0 {
		return nil
	}
	return evidence
}

func (c *Client) hostStamp() map[string]string {
	kind := bounded(c.getenv("ORCA_ORCHESTRATION_COMPATIBILITY_HOST_KIND"))
	hostID := bounded(c.getenv("ORCA_ORCHESTRATION_COMPATIBILITY_HOST_ID"))
	incarnation := bounded(c.getenv("ORCA_ORCHESTRATION_COMPATIBILITY_HOST_INCARNATION"))
	attachment := bounded(c.getenv("ORCA_ORCHESTRATION_COMPATIBILITY_ATTACHMENT"))
	switch {
	case kind == "wsl" && hostID != "" && incarnation != "":
		return map[string]string{"kind": kind, "hostId": hostID, "distro": incarnation}
	case kind == "ssh" && hostID != "" && incarnation != "" && attachment != "":
		return map[string]string{"kind": kind, "targetId": hostID, "connectionIncarnation": incarnation, "attachmentId": attachment}
	}
	return nil
}

func bounded(value string) string {
	trimmed := strings.TrimSpace(value)
	if len(trimmed) > maxEvidenceFieldLength {
		return ""
	}
	return trimmed
}

func newUUID() (string, error) {
	var raw [16]byte
	if _, err := rand.Read(raw[:]); err != nil {
		return "", err
	}
	raw[6] = raw[6]&0x0f | 0x40
	raw[8] = raw[8]&0x3f | 0x80
	return fmt.Sprintf("%x-%x-%x-%x-%x", raw[:4], raw[4:6], raw[6:8], raw[8:10], raw[10:]), nil
}
