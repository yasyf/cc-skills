package orcarpc

import (
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"time"

	"github.com/coder/websocket"
	"golang.org/x/crypto/nacl/box"
)

const (
	environmentStore     = "orca-environments.json"
	environmentSidecar   = "orca-environment-sidecar.json"
	flagEnvironment      = "environment"
	remoteConnectTimeout = 12 * time.Second
	remoteFrameLimit     = 64 << 20
	boxKeyBytes          = 32
	boxNonceBytes        = 24
	minServerProtocol    = 2
	connectFailure       = "Could not connect to the remote Orca runtime"
)

var remoteCapabilities = []string{
	"agent-session.background-task-stop.v1",
	"agent-session.background-task-row-stop.v1",
	"agent-session.background-task-child-views.v1",
	"agent-session.turn-item.v1",
	"session-tabs.close-intent.v1",
	"session-tabs.authoritative-inventory.v1",
	"agent-session.session-boundary.v1",
	"skills.install-result.v2",
	"worktree.github-pr-suppression.v1",
	"worktree.visibility-defaults.v1",
	"worktree.visibility-source-defaults.v1",
	"automation.owner-fencing.v1",
	"repo.search-qualified-refs.v1",
	"terminal.quick-commands.long-prompts.v1",
	"terminal.quick-commands.large-lists.v1",
	"agent.launch.v2",
}

type storedEndpoint struct {
	ID          string `json:"id"`
	Kind        string `json:"kind"`
	Endpoint    string `json:"endpoint"`
	DeviceToken string `json:"deviceToken"`
	PublicKey   string `json:"publicKeyB64"`
}

type storedEnvironment struct {
	ID        string           `json:"id"`
	Name      string           `json:"name"`
	RuntimeID string           `json:"runtimeId"`
	LastUsed  int64            `json:"lastUsedAt"`
	Preferred string           `json:"preferredEndpointId"`
	Endpoints []storedEndpoint `json:"endpoints"`
}

func (c *Client) storeFile(name string) ([]byte, bool) {
	dir, err := c.userDataPath()
	if err != nil {
		return nil, false
	}
	raw, err := os.ReadFile(filepath.Join(dir, name))
	return raw, err == nil
}

func (c *Client) environments() ([]storedEnvironment, error) {
	dir, err := c.userDataPath()
	if err != nil {
		return nil, err
	}
	raw, err := os.ReadFile(filepath.Join(dir, environmentStore))
	if err != nil {
		return nil, err
	}
	var store struct {
		Environments []storedEnvironment `json:"environments"`
	}
	if err := json.Unmarshal(raw, &store); err != nil {
		return nil, err
	}
	return store.Environments, nil
}

func (c *Client) environment(selector string) (storedEnvironment, storedEndpoint, bool) {
	environments, err := c.environments()
	if err != nil {
		return storedEnvironment{}, storedEndpoint{}, false
	}
	var named []storedEnvironment
	for _, environment := range environments {
		if environment.ID == selector {
			named = []storedEnvironment{environment}
			break
		}
		if environment.Name == selector {
			named = append(named, environment)
		}
	}
	if len(named) != 1 {
		return storedEnvironment{}, storedEndpoint{}, false
	}
	for _, endpoint := range named[0].Endpoints {
		if endpoint.ID == named[0].Preferred && endpoint.Kind == "websocket" {
			return named[0], endpoint, true
		}
	}
	return storedEnvironment{}, storedEndpoint{}, false
}

func (c *Client) recentEnvironments(count int) []string {
	environments, err := c.environments()
	if err != nil {
		return nil
	}
	slices.SortFunc(environments, func(left, right storedEnvironment) int {
		return int(right.LastUsed - left.LastUsed)
	})
	names := make([]string, 0, count)
	for _, environment := range environments[:min(count, len(environments))] {
		names = append(names, environment.Name)
	}
	return names
}

type unreachableError struct {
	endpoint string
	timedOut bool
}

func (e *unreachableError) Error() string {
	if !e.timedOut {
		return connectFailure + "."
	}
	return fmt.Sprintf("%s at %s: the host did not answer, so anything running on it is unverifiable.", connectFailure, displayEndpoint(e.endpoint))
}

func displayEndpoint(endpoint string) string {
	parsed, err := url.Parse(endpoint)
	if err != nil || parsed.Host == "" {
		return "the paired endpoint"
	}
	return parsed.Scheme + "://" + parsed.Host
}

type remoteConn struct {
	socket   *websocket.Conn
	key      [boxKeyBytes]byte
	token    string
	timeout  time.Duration
	evidence map[string]any
}

func (c *Client) dialRemote(ctx context.Context, endpoint storedEndpoint) (*remoteConn, error) {
	hostKey, err := base64.StdEncoding.DecodeString(endpoint.PublicKey)
	if err != nil || len(hostKey) != boxKeyBytes {
		return nil, fmt.Errorf("orcarpc: bad host key for %s", displayEndpoint(endpoint.Endpoint))
	}
	public, secret, err := box.GenerateKey(rand.Reader)
	if err != nil {
		return nil, err
	}
	timeout := c.Timeout
	if timeout == 0 {
		timeout = defaultRequestTimeout
	}
	remote := &remoteConn{token: endpoint.DeviceToken, timeout: timeout, evidence: c.evidence()}
	box.Precompute(&remote.key, (*[boxKeyBytes]byte)(hostKey), secret)
	connecting, cancel := context.WithTimeout(ctx, remoteConnectTimeout)
	defer cancel()
	socket, _, err := websocket.Dial(connecting, endpoint.Endpoint, &websocket.DialOptions{HTTPClient: &http.Client{Transport: &http.Transport{}}})
	if err != nil {
		return nil, &unreachableError{endpoint: endpoint.Endpoint, timedOut: connecting.Err() != nil}
	}
	socket.SetReadLimit(remoteFrameLimit)
	remote.socket = socket
	if err := remote.handshake(connecting, public); err != nil {
		_ = remote.Close()
		return nil, err
	}
	return remote, nil
}

func (r *remoteConn) Close() error {
	return r.socket.Close(websocket.StatusNormalClosure, "")
}

func (r *remoteConn) handshake(ctx context.Context, public *[boxKeyBytes]byte) error {
	hello, err := json.Marshal(map[string]string{"type": "e2ee_hello", "publicKeyB64": base64.StdEncoding.EncodeToString(public[:])})
	if err != nil {
		return err
	}
	if err := r.socket.Write(ctx, websocket.MessageText, hello); err != nil {
		return err
	}
	frame, err := r.readText(ctx)
	if err != nil {
		return err
	}
	if err := wantType(frame, "e2ee_ready"); err != nil {
		return err
	}
	auth, err := marshal(map[string]any{"type": "e2ee_auth", "deviceToken": r.token, "clientCapabilities": remoteCapabilities})
	if err != nil {
		return err
	}
	if err := r.writeSealed(ctx, auth); err != nil {
		return err
	}
	opened, err := r.readSealed(ctx)
	if err != nil {
		return err
	}
	return wantType(opened, "e2ee_authenticated")
}

func wantType(frame []byte, want string) error {
	var typed struct {
		Type string `json:"type"`
	}
	if err := json.Unmarshal(frame, &typed); err != nil {
		return fmt.Errorf("orcarpc: bad handshake frame: %w", err)
	}
	if typed.Type != want {
		return fmt.Errorf("orcarpc: handshake answered %q, want %q", typed.Type, want)
	}
	return nil
}

func (r *remoteConn) readText(ctx context.Context) ([]byte, error) {
	kind, frame, err := r.socket.Read(ctx)
	if err != nil {
		return nil, err
	}
	if kind != websocket.MessageText {
		return nil, errors.New("orcarpc: the remote runtime sent a binary frame")
	}
	return frame, nil
}

func (r *remoteConn) writeSealed(ctx context.Context, plaintext []byte) error {
	var nonce [boxNonceBytes]byte
	if _, err := rand.Read(nonce[:]); err != nil {
		return err
	}
	sealed := box.SealAfterPrecomputation(nonce[:], plaintext, &nonce, &r.key)
	return r.socket.Write(ctx, websocket.MessageText, []byte(base64.StdEncoding.EncodeToString(sealed)))
}

func (r *remoteConn) readSealed(ctx context.Context) ([]byte, error) {
	frame, err := r.readText(ctx)
	if err != nil {
		return nil, err
	}
	bundle, err := base64.StdEncoding.DecodeString(string(frame))
	if err != nil {
		return nil, fmt.Errorf("orcarpc: bad sealed frame: %w", err)
	}
	if len(bundle) < boxNonceBytes {
		return nil, errors.New("orcarpc: short sealed frame")
	}
	opened, authentic := box.OpenAfterPrecomputation(nil, bundle[boxNonceBytes:], (*[boxNonceBytes]byte)(bundle[:boxNonceBytes]), &r.key)
	if !authentic {
		return nil, errors.New("orcarpc: undecryptable frame")
	}
	return opened, nil
}

type remoteRequest struct {
	ID                    string         `json:"id"`
	DeviceToken           string         `json:"deviceToken"`
	Method                string         `json:"method"`
	Params                any            `json:"params,omitempty"`
	ContractVersion       int            `json:"orchestrationContractVersion,omitempty"`
	CompatibilityID       string         `json:"compatibilityInvocationId,omitempty"`
	CompatibilityEvidence map[string]any `json:"orchestrationCompatibilityEvidence,omitempty"`
}

func (r *remoteConn) call(ctx context.Context, method string, params any, timeout time.Duration) (Response, error) {
	if !readOnly(method, params) || (strings.HasPrefix(method, orchestrationPrefix) && method != checkMethod) {
		return Response{}, fmt.Errorf("%w: %s", ErrWriteMethod, method)
	}
	id, err := newUUID()
	if err != nil {
		return Response{}, err
	}
	outgoing := remoteRequest{ID: id, DeviceToken: r.token, Method: method, Params: params}
	if strings.HasPrefix(method, orchestrationPrefix) {
		outgoing.ContractVersion = orchestrationContract
		outgoing.CompatibilityEvidence = r.evidence
		if outgoing.CompatibilityID, err = newUUID(); err != nil {
			return Response{}, err
		}
	}
	frame, err := marshal(outgoing)
	if err != nil {
		return Response{}, err
	}
	if timeout == 0 {
		timeout = r.timeout
	}
	if err := r.writeSealed(ctx, frame); err != nil {
		return Response{}, err
	}
	for {
		reading, cancel := context.WithTimeout(ctx, timeout)
		opened, err := r.readSealed(reading)
		cancel()
		if err != nil {
			return Response{}, err
		}
		var response Response
		if err := json.Unmarshal(opened, &response); err != nil {
			return Response{}, fmt.Errorf("orcarpc: bad frame for %s: %w", method, err)
		}
		if response.Keepalive {
			continue
		}
		if response.ID != id {
			return Response{}, fmt.Errorf("orcarpc: %s answered with id %q, want %q", method, response.ID, id)
		}
		return response, nil
	}
}

func (c *Client) serveRemote(ctx context.Context, selector, path string, spec verb, in invocation, request call) (Served, bool) {
	if c.refuseRemote() != nil {
		return Served{}, false
	}
	environment, endpoint, known := c.environment(selector)
	if !known {
		return Served{}, false
	}
	if c.Gate != nil {
		local, err := c.Dial(ctx)
		if err != nil {
			return Served{}, false
		}
		admitted := c.Gate.allows(ctx, local)
		_ = local.Close()
		if !admitted {
			return Served{}, false
		}
	}
	remote, err := c.dialRemote(ctx, endpoint)
	var unreachable *unreachableError
	if errors.As(err, &unreachable) {
		return unreachableReply(unreachable)
	}
	if err != nil {
		return Served{}, false
	}
	defer func() { _ = remote.Close() }()
	status, err := remote.call(ctx, "status.get", nil, 0)
	if err != nil || !status.OK || !sameCompatibleRuntime(status, environment) {
		return Served{}, false
	}
	reply, err := remote.call(ctx, request.method, request.params, request.timeout)
	if err != nil {
		return Served{}, false
	}
	if !reply.OK {
		return refusal(path, in, reply, true)
	}
	code := 0
	if spec.finish != nil {
		var finished bool
		if reply.Result, code, finished = spec.finish(c, nil, in, reply.Result); !finished {
			return Served{}, false
		}
	}
	return rendered(reply, code)
}

func sameCompatibleRuntime(status Response, environment storedEnvironment) bool {
	var runtime runtimeStatus
	if json.Unmarshal(status.Result, &runtime) != nil {
		return false
	}
	return runtime.RuntimeID == environment.RuntimeID && runtime.ProtocolVersion >= minServerProtocol && runtime.MinClientVersion <= protocolVersion
}

func unreachableReply(unreachable *unreachableError) (Served, bool) {
	return rendered(Response{
		ID:    "local",
		Error: &RPCError{Code: "remote_runtime_unavailable", Message: unreachable.Error()},
		Meta:  json.RawMessage(`{"runtimeId":null}`),
	}, 1)
}

func rendered(reply Response, code int) (Served, bool) {
	stdout, err := Render(reply)
	if err != nil {
		return Served{}, false
	}
	return Served{Stdout: stdout, Code: code}, true
}
