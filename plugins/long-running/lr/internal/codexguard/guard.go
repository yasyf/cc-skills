package codexguard

import (
	"context"
	"encoding/json"
	"path"
	"regexp"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"mvdan.cc/sh/v3/syntax"
)

const (
	refusal     = "A lane never calls production as the owner or drives the owner's signed-in Orca browser, read or write, under any authority. Prove it with a test identity or a no-write probe, or ask for the owner's identity with `lr ask-owner`."
	readTimeout = 5 * time.Second
	pageFlag    = "--page"
)

var (
	hostsRead = []string{"git", "config", "--global", "--get-all", "long-running.productionHost"}
	hostWord  = regexp.MustCompile(`[A-Za-z0-9][A-Za-z0-9.-]*`)
	pageVerbs = []string{
		"back", "check", "clear", "click", "dblclick", "dialog", "download", "drag", "eval", "exec", "fill", "find", "focus", "forward", "get", "goto", "highlight", "hover",
		"inserttext", "is", "keypress", "mouse", "reload", "screenshot", "scroll", "scrollintoview", "select", "set", "snapshot", "storage", "type", "uncheck", "upload", "wait",
	}
	wrappers = []string{"command", "env", "exec", "sudo"}
	shells   = []string{"bash", "sh", "zsh"}
)

type Guard struct {
	Runner shell.Runner
	Getenv func(string) string
}

type hookCall struct {
	Cwd       string `json:"cwd"`
	AgentID   string `json:"agent_id"`
	ToolInput struct {
		Command string `json:"command"`
	} `json:"tool_input"`
}

func (g Guard) Refusal(ctx context.Context, payload []byte) string {
	if g.Getenv("CLAUDE_LONG_RUNNING_LANE") == "" && g.Getenv("CLAUDE_LONG_RUNNING_DRIVE") == "" {
		return ""
	}
	var call hookCall
	if json.Unmarshal(payload, &call) != nil {
		return ""
	}
	if g.drivesProduction(ctx, call.ToolInput.Command, call.Cwd) {
		return refusal
	}
	if g.launchesIrisAsHim(ctx, call.ToolInput.Command, call.Cwd, g.grantedLane(call)) {
		return irisRefusal
	}
	if g.writesACredential(ctx, call.ToolInput.Command, call.Cwd, g.grantedLane(call)) {
		return storeWriteRefusal
	}
	return ""
}

func (g Guard) grantedLane(call hookCall) string {
	if call.AgentID != "" {
		return ""
	}
	return g.Getenv("CLAUDE_LONG_RUNNING_LANE")
}

func grantsTo(lane string, entries []string) []string {
	var granted []string
	for _, entry := range entries {
		if named, grant, ok := strings.Cut(entry, "="); ok && lane != "" && named == lane {
			granted = append(granted, grant)
		}
	}
	return granted
}

func (g Guard) drivesProduction(ctx context.Context, line, cwd string) bool {
	driving := pageCalls(line)
	if len(driving) == 0 {
		return false
	}
	hosts := strings.Fields(g.read(ctx, hostsRead, cwd))
	if len(hosts) == 0 {
		return false
	}
	for _, args := range driving {
		if namesHost(args, hosts) || namesHost(g.pageURLs(ctx, args, cwd), hosts) {
			return true
		}
	}
	return false
}

func pageCalls(line string) [][]string {
	file, err := syntax.NewParser().Parse(strings.NewReader(line), "")
	if err != nil {
		return nil
	}
	var calls [][]string
	syntax.Walk(file, func(node syntax.Node) bool {
		if call, ok := node.(*syntax.CallExpr); ok {
			calls = append(calls, fromWords(call)...)
		}
		return true
	})
	return calls
}

func fromWords(call *syntax.CallExpr) [][]string {
	_, words := unwrapped(call)
	if payload, ok := shellPayload(words); ok {
		return pageCalls(payload)
	}
	if len(words) < 2 || path.Base(words[0]) != "orca" || !drivesAPage(words[1:]) {
		return nil
	}
	return [][]string{words[1:]}
}

func shellPayload(words []string) (string, bool) {
	if len(words) < 3 || !slices.Contains(shells, path.Base(words[0])) {
		return "", false
	}
	command := false
	for _, word := range words[1:] {
		if !strings.HasPrefix(word, "-") {
			return word, command
		}
		command = command || !strings.HasPrefix(word, "--") && strings.Contains(word, "c")
	}
	return "", false
}

func drivesAPage(args []string) bool {
	if args[0] == "tab" {
		return len(args) > 1 && args[1] == "create"
	}
	return slices.Contains(pageVerbs, args[0])
}

func literal(word *syntax.Word) string {
	var text strings.Builder
	for _, part := range word.Parts {
		switch piece := part.(type) {
		case *syntax.Lit:
			text.WriteString(piece.Value)
		case *syntax.SglQuoted:
			text.WriteString(piece.Value)
		case *syntax.DblQuoted:
			for _, inner := range piece.Parts {
				if lit, ok := inner.(*syntax.Lit); ok {
					text.WriteString(lit.Value)
				}
			}
		}
	}
	return text.String()
}

func namesHost(words, hosts []string) bool {
	for _, word := range words {
		for _, found := range hostWord.FindAllString(word, -1) {
			found = strings.TrimRight(found, ".-")
			if slices.ContainsFunc(hosts, func(host string) bool { return strings.EqualFold(host, found) }) {
				return true
			}
		}
	}
	return false
}

func (g Guard) pageURLs(ctx context.Context, args []string, cwd string) []string {
	if args[0] == "goto" || args[0] == "tab" {
		return nil
	}
	page := pageOf(args)
	verb := "current"
	if page != "" {
		verb = "list"
	}
	var said struct {
		Result struct {
			Tabs []tab `json:"tabs"`
			Tab  *tab  `json:"tab"`
		} `json:"result"`
	}
	if json.Unmarshal([]byte(g.read(ctx, []string{"orca", "tab", verb, "--json"}, cwd)), &said) != nil {
		return nil
	}
	tabs := said.Result.Tabs
	if said.Result.Tab != nil {
		tabs = append(tabs, *said.Result.Tab)
	}
	var urls []string
	for _, open := range tabs {
		if page == "" || page == open.Page {
			urls = append(urls, open.URL)
		}
	}
	return urls
}

type tab struct {
	Page string `json:"browserPageId"`
	URL  string `json:"url"`
}

func pageOf(args []string) string {
	for i, word := range args {
		if word == pageFlag && i+1 < len(args) {
			return args[i+1]
		}
		if value, ok := strings.CutPrefix(word, pageFlag+"="); ok {
			return value
		}
	}
	return ""
}

func (g Guard) read(ctx context.Context, argv []string, cwd string) string {
	done, err := g.Runner.Run(ctx, shell.Command{Argv: argv, Dir: cwd, Timeout: readTimeout})
	if err != nil {
		return ""
	}
	return done.Stdout
}

func denial(reason string) any {
	return map[string]any{"hookSpecificOutput": map[string]string{
		"hookEventName":            "PreToolUse",
		"permissionDecision":       "deny",
		"permissionDecisionReason": reason,
	}}
}
