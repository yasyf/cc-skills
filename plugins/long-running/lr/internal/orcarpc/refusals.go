package orcarpc

import (
	"encoding/json"
	"fmt"
	"regexp"
	"strings"
)

const staleHandleHint = "Terminal handles belong to the Orca server that issued them. If this handle came from a paired server (for example `orca terminal list --environment <server>`), pass the same --environment <server> to this command."

var (
	plainRefusals = map[string]bool{
		"dispatch_not_found":         true,
		"remote_runtime_unavailable": true,
		"timeout":                    true,
	}
	selectorPrefixes = []string{"id:", "path:", "name:", "branch:", "identity:", "issue:"}
	selectorForms    = []string{
		"id:<repo-id>::<absolute-path>",
		"path:<absolute-path>",
		"name:<display-name>",
		"branch:<branch>",
		"identity:<identity-key>",
		"issue:<number>",
		"current",
		"active",
	}
	windowsPath = regexp.MustCompile(`^[A-Za-z]:[\\/]`)
)

func refusal(path string, in invocation, reply Response, remote bool) (Served, bool) {
	if reply.Error == nil {
		return Served{}, false
	}
	var recovery any
	switch code := reply.Error.Code; {
	case plainRefusals[code]:
	case code == "terminal_handle_stale":
		if !remote && strings.HasPrefix(path, "terminal") {
			recovery = map[string][]string{"nextSteps": {staleHandleHint}}
		}
	case code == "selector_not_found":
		if in.has(flagWorktree) {
			recovery = worktreeRecovery(in.flags[flagWorktree])
		}
	default:
		return Served{}, false
	}
	if recovery != nil {
		if len(reply.Error.Data) > 0 && string(reply.Error.Data) != "null" {
			return Served{}, false
		}
		data, err := marshal(recovery)
		if err != nil {
			return Served{}, false
		}
		failure := *reply.Error
		failure.Data = data
		reply.Error = &failure
	}
	return rendered(reply, 1)
}

type selectorRecovery struct {
	Selector          string   `json:"selector"`
	ValidSelectorForm []string `json:"validSelectorForms"`
	Suggestions       []string `json:"suggestions"`
	NextSteps         []string `json:"nextSteps"`
}

func worktreeRecovery(selector string) selectorRecovery {
	suggestions := suggestSelectorForms(selector)
	steps := []string{fmt.Sprintf("No Orca workspace matched the worktree selector %s.", quoted(selector))}
	if len(suggestions) > 0 {
		steps = append(steps, "Did you mean: "+strings.Join(suggestions, ", "))
	}
	steps = append(steps,
		"Valid selector forms: "+strings.Join(selectorForms, ", ")+".",
		"List the exact values with `orca worktree list --json`; a bare repository id is not a worktree id.",
	)
	return selectorRecovery{Selector: selector, ValidSelectorForm: selectorForms, Suggestions: suggestions, NextSteps: steps}
}

func quoted(selector string) string {
	return `"` + selector + `"`
}

func suggestSelectorForms(selector string) []string {
	if rest, isID := strings.CutPrefix(selector, "id:"); isID {
		if strings.Contains(selector, "::") {
			return []string{}
		}
		return []string{"id:" + rest + "::<absolute-path>", "path:<absolute-path>"}
	}
	for _, prefix := range selectorPrefixes {
		if strings.HasPrefix(selector, prefix) {
			return []string{}
		}
	}
	if strings.HasPrefix(selector, "/") || windowsPath.MatchString(selector) {
		return []string{"path:" + selector}
	}
	return []string{"id:" + selector + "::<absolute-path>", "name:" + selector, "branch:" + selector}
}

func (c *Client) environmentList() (Response, bool) {
	raw, ok := c.storeFile(environmentSidecar)
	if ok {
		var sidecar struct {
			Entries json.RawMessage `json:"entries"`
		}
		if json.Unmarshal(raw, &sidecar) != nil || !emptyJSON(sidecar.Entries) {
			return Response{}, false
		}
	}
	raw, ok = c.storeFile(environmentStore)
	if !ok {
		return Response{}, false
	}
	var store struct {
		Environments []json.RawMessage `json:"environments"`
	}
	if json.Unmarshal(raw, &store) != nil {
		return Response{}, false
	}
	redacted := make([]json.RawMessage, 0, len(store.Environments))
	for _, environment := range store.Environments {
		public, err := redactEnvironment(environment)
		if err != nil {
			return Response{}, false
		}
		redacted = append(redacted, public)
	}
	result, err := marshal(map[string][]json.RawMessage{"environments": redacted})
	if err != nil {
		return Response{}, false
	}
	return Response{ID: "local", OK: true, Result: result, Meta: json.RawMessage(`{"runtimeId":"local"}`)}, true
}

func emptyJSON(raw json.RawMessage) bool {
	trimmed := strings.TrimSpace(string(raw))
	return trimmed == "" || trimmed == "null" || trimmed == "{}" || trimmed == "[]"
}

func redactEnvironment(environment json.RawMessage) (json.RawMessage, error) {
	fields, err := orderedFields(environment)
	if err != nil {
		return nil, err
	}
	for index, field := range fields {
		if field.name != "endpoints" {
			continue
		}
		var endpoints []json.RawMessage
		if err := json.Unmarshal(field.value, &endpoints); err != nil {
			return nil, err
		}
		for at, endpoint := range endpoints {
			kept, err := orderedFields(endpoint)
			if err != nil {
				return nil, err
			}
			public := make([]jsonField, 0, len(kept))
			for _, entry := range kept {
				if entry.name != "deviceToken" && entry.name != "publicKeyB64" {
					public = append(public, entry)
				}
			}
			if endpoints[at], err = joinFields(public); err != nil {
				return nil, err
			}
		}
		if fields[index].value, err = marshal(endpoints); err != nil {
			return nil, err
		}
	}
	return joinFields(fields)
}
