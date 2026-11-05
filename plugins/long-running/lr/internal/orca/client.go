package orca

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"slices"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	CodeUnregistered = "selector_not_found"
	CodeStaleHandle  = "terminal_handle_stale"
	codeTimedOut     = "timeout"
	CodeFenced       = "dispatch_inactive"
	hostUnavailable  = "host_unavailable"
	CodeRuntimeDown  = "remote_runtime_unavailable"
	orchestration    = "orchestration"
)

var replayableCodes = []string{"runtime_unavailable", CodeRuntimeDown, "runtime_timeout", "invalid_runtime_response"}

type ReplyError struct {
	Code    string `json:"code"`
	Message string `json:"message"`
	Data    struct {
		RequestID string `json:"orchestrationRequestId"`
	} `json:"data"`
}

type Reply struct {
	OK     bool            `json:"ok"`
	Result json.RawMessage `json:"result"`
	Error  ReplyError      `json:"error"`
	Meta   json.RawMessage `json:"_meta"`
	Stderr string          `json:"-"`
}

func (r Reply) Refusal() string {
	if r.Error.Message == "" {
		return r.Error.Code
	}
	return r.Error.Code + ": " + r.Error.Message
}

func replayable(said string) string {
	var reply Reply
	if json.Unmarshal([]byte(said), &reply) != nil || reply.OK || !slices.Contains(replayableCodes, reply.Error.Code) {
		return ""
	}
	return reply.Error.Data.RequestID
}

func (r Reply) Into(target any) error {
	if !r.OK {
		return fmt.Errorf("orca refused: %s", r.Refusal())
	}
	return json.Unmarshal(r.Result, target)
}

type Client struct {
	Runner shell.Runner
	Env    []string
}

func (c Client) Call(ctx context.Context, args ...string) (Reply, error) {
	argv := append([]string{"orca"}, args...)
	full := args[0] == orchestration
	if full {
		argv = append(argv, "--full")
	}
	result, err := c.Runner.Run(ctx, shell.Command{Argv: append(argv, "--json"), Env: c.Env})
	var exit *shell.ExitError
	if err != nil && !errors.As(err, &exit) {
		return Reply{}, err
	}
	said := result.Stdout
	if said == "" {
		said = result.Stderr
	}
	var reply Reply
	if decoded := json.Unmarshal([]byte(said), &reply); decoded != nil {
		return Reply{}, fmt.Errorf("orca %s printed no JSON: %w: %s", strings.Join(args[:2], " "), decoded, strings.TrimSpace(said))
	}
	if full && reply.OK && reply.Meta == nil {
		return Reply{}, fmt.Errorf("orca %s printed its concise view, not the full record", strings.Join(args[:2], " "))
	}
	reply.Stderr = strings.TrimSpace(result.Stderr)
	return reply, nil
}

func on(environment string) []string {
	if environment == "" {
		return nil
	}
	return []string{"--environment", environment}
}
