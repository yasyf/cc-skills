package orca

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/BurntSushi/toml"
	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	warmLane               = "tools/hooks/warm-lane.sh"
	agentClaude            = "claude"
	agentCodex             = "codex"
	agentIncident          = "incident"
	modelIncidentOpus      = "incident-opus"
	claudeFastSettings     = `"fastMode":true`
	startPoll              = 4 * time.Second
	stallRetry             = 30 * time.Second
	worktreeCacheSeconds   = 300
	coordinatorLane        = "root"
	coordinatorDenied      = ",Workflow,ScheduleWakeup,ReportFindings,ListAgents,Monitor,SendMessage,TaskGet"
	coordinatorListing     = `"skillListingBudgetFraction":0.001`
	coordinatorNoMemory    = `"autoMemoryEnabled":false,"claudeMdExcludes":["**/CLAUDE.md","**/AGENTS.md"],`
	coordinatorSections    = "rules-sections"
	coordinatorRules       = "rules.md"
	specLimit              = 500
	workerStartClaudeMS    = "600000"
	workerStartCodexMS     = "90000"
	refusalUnconfigured    = "agent_unconfigured"
	refusalFenced          = "consumer_fenced"
	bypassOn               = "bypass permissions on"
	updateAvailable        = "Update available!"
	skipUntilNext          = "Skip until next version"
	screenUnavailable      = "screen-unavailable"
	bypassReads            = 10
	worktreeCreates        = 4
	terminalListAttempts   = 3
	terminalCreateAttempts = 3
	saidEvery              = 30
	spritePreamble         = " The dispatch preamble's line against posting to GitHub or other channels means status chatter, so the brief's PR steps stand; Slack stays off."
	spriteBuildkite        = " tools/ci bk does not run on this Sprite. To read Buildkite, post `cci post --kind ask --to bk-desk \"bk build <pipeline> <number>\"`, or `bk builds <pipeline> --branch <glob> --since <date>`, or `bk annotations <pipeline> <number> [<context>]`, or `bk log <pipeline> <number> <job id or step key>`, or `bk api <path>`; the answer arrives within about two minutes and `cci show <seq>` prints its body. Ask once per need, never in a polling loop."
	standingDesk           = " Standing desk: loop until rotation; setup and quiet cycles are not done. Send worker_done only at rotation, naming the handoff doc. Mail reaches you only between tool calls, so no foreground command runs over five minutes: run a long read in the background and report it by record."
	noPRLine               = " This brief orders no PR: push the work as a branch and never open a PR for it."
	createTimedOut         = "Timed out"
	macInputsOnSprite      = " Orca was out of terminals on the Mac, so this lane runs on a Sprite though its brief named Mac inputs. If the work truly needs the Mac, post a `blocked` record to the lane that launched you naming what it needs, and stop: it relaunches you on the Mac."
	readOnSprite           = " The Mac was over its load limit or out of Orca terminals, so this read lane runs on a Sprite whose cci store starts at its launch: check on the Mac before you report anything absent."
	incidentOnSprite       = " This Sprite has no AWS identity, so `aws` and `tools/ci` fail here, no Sentry or Datadog, and no devbox CLI. For each Sentry, Datadog or AWS read and each devbox repro, post `cci post --kind ask` naming the one read or repro to a lane of this incident still on the Mac, or to root when none is."
)

var (
	briefDesk        = regexp.MustCompile(`(?m)^ccx:(.* )?role=(desk|watch)(\s|$)`)
	briefNoPR        = regexp.MustCompile(`(?m)^ccx:(.* )?pr=none(\s|$)`)
	briefPaths       = regexp.MustCompile(`(?m)^ccx:(?:.* )?paths=(\S+)`)
	markdownSection  = regexp.MustCompile(`(?m)^## `)
	briefRole        = regexp.MustCompile(`(?m)^ccx:(?:.*[ \t])?role=([\w-]+)`)
	briefReplaces    = regexp.MustCompile("(?m)^Replaces: `?([\\w.-]+)`?\\s*$")
	whitespace       = regexp.MustCompile(`\s+`)
	baseCone         = []string{".agents", ".claude", "tools", "infra/ci/hooks/lib", "infra/ci/src", "release", "infra/envs", "infra/rows/ci-infra/ci"}
	rollbackRefusals = []string{refusalFenced, "invalid_argument", "task_not_found", "worker_prompt_too_large", "runtime_unavailable", refusalUnconfigured}
)

type Failed struct {
	Line string
}

func (f *Failed) Error() string {
	return f.Line
}

type Bus interface {
	Post(ctx context.Context, d client.Draft) (client.Record, error)
	Tail(ctx context.Context, q client.Query) ([]client.Record, error)
}

type CCI struct{}

func (CCI) Post(ctx context.Context, d client.Draft) (client.Record, error) {
	return client.Post(ctx, d)
}

func (CCI) Tail(ctx context.Context, q client.Query) ([]client.Record, error) {
	return client.Tail(ctx, q)
}

type Starter struct {
	Runner shell.Runner
	Bus    Bus
	Env    func(string) string
	Sleep  func(time.Duration)
	Now    func() time.Time
	Stderr io.Writer
}

type launch struct {
	*Starter
	ctx context.Context

	lane, model, effort, brief, name, agent, modelID, parent string
	boot, worktreeWait, prepareWait, sparseFiles, stall      int
	prepare                                                  string
	sparseLanes                                              bool
	slackMCP                                                 bool

	run, receipts, state, receipt, repo, main, root, gitUser, runID, wt, base, drive string
	command, bin, desk, noPR, sparse, node, laneBrief, spec, rebind                  string
	repoRead                                                                         repoRecord
	parentArgs                                                                       []string

	worktreeName, branch          string
	rollingBack                   bool
	created, made, lost, startup  bool
	owned, terminal, undone, kept string
	stalled                       string
	stages                        launchStages
	waitFor                       string
	waitStart, said               int64
	listed, before                []string
	startCode                     int
	startTries                    int
}

type repoRecord struct {
	Result struct {
		Repo struct {
			Path         string   `json:"path"`
			GitUsername  string   `json:"gitUsername"`
			SymlinkPaths []string `json:"symlinkPaths"`
		} `json:"repo"`
	} `json:"result"`
}

func squeeze(text string) string {
	return whitespace.ReplaceAllString(text, " ")
}

func cutRunes(text string, limit int) string {
	runes := []rune(text)
	if len(runes) <= limit {
		return text
	}
	return string(runes[:limit])
}

func documents(raw string) []json.RawMessage {
	decoder := json.NewDecoder(strings.NewReader(raw))
	var docs []json.RawMessage
	for {
		var doc json.RawMessage
		if err := decoder.Decode(&doc); err != nil {
			return docs
		}
		docs = append(docs, doc)
	}
}

func orcaError(contents ...string) string {
	docs := documents(contents[0])
	said := make([]string, 0, len(docs))
	for _, doc := range docs {
		var reply struct {
			OK    *bool `json:"ok"`
			Error struct {
				Code    string `json:"code"`
				Message string `json:"message"`
			} `json:"error"`
		}
		if json.Unmarshal(doc, &reply) != nil || reply.OK == nil || *reply.OK {
			continue
		}
		message := reply.Error.Message
		if message == "" {
			message = "no message"
		}
		said = append(said, reply.Error.Code+": "+message)
	}
	if len(said) > 0 {
		return strings.Join(said, " ")
	}
	return strings.TrimSpace(cutRunes(squeeze(strings.Join(contents, "")), 300))
}

func (l *launch) envInt(name string, fallback int) int {
	if value, err := strconv.Atoi(l.Env(name)); err == nil {
		return value
	}
	return fallback
}

func (l *launch) fail(format string, args ...any) error {
	l.undo()
	line := l.lane + " failed " + squeeze(fmt.Sprintf(format, args...))
	if l.undone != "" {
		line += "; rolled back" + l.undone
	}
	if l.kept != "" {
		line += "; rollback left" + l.kept
	}
	return &Failed{line}
}

func (l *launch) pause(reason string) error {
	return l.pauseWithin(reason, l.stall)
}

func (l *launch) pauseWithin(reason string, ceiling int) error {
	now := l.Now().Unix()
	if reason != l.waitFor {
		l.waitFor, l.waitStart, l.said = reason, now, now
	}
	if now-l.waitStart >= int64(ceiling) {
		l.stalled = fmt.Sprintf("%s for %ds", reason, now-l.waitStart)
		return l.fail("LAUNCH-FAILED: %s", l.stalled)
	}
	if now-l.said >= saidEvery {
		fmt.Fprintf(l.Stderr, "%s waiting %ds: %s\n", l.lane, now-l.waitStart, reason)
		l.said = now
	}
	l.Sleep(startPoll)
	return nil
}

func (l *launch) exec(command shell.Command) (string, string, int) {
	result, err := l.Runner.Run(l.ctx, command)
	var exit *shell.ExitError
	switch {
	case err == nil:
		return result.Stdout, result.Stderr, 0
	case errors.As(err, &exit):
		return result.Stdout, result.Stderr, result.Code
	}
	code := result.Code
	if code == 0 {
		code = 1
	}
	return result.Stdout, result.Stderr + err.Error(), code
}

func (l *launch) tool(argv ...string) (string, int) {
	stdout, stderr, code := l.exec(shell.Command{Argv: argv})
	return stdout + stderr, code
}

func (l *launch) path(suffix string) string {
	return filepath.Join(l.state, l.lane+suffix)
}

func (l *launch) saved(suffix string, argv ...string) (string, bool) {
	said, code := l.tool(argv...)
	if err := os.WriteFile(l.path(suffix), []byte(said), 0o600); err != nil {
		return err.Error(), false
	}
	return said, code == 0
}

func (l *launch) savedFile(suffix string) string {
	raw, err := os.ReadFile(l.path(suffix))
	if err != nil {
		return ""
	}
	return string(raw)
}

func (l *launch) git(dir string, args ...string) (string, int) {
	return l.tool(append([]string{"git", "-C", dir}, args...)...)
}

func (l *launch) gitQuiet(dir string, args ...string) bool {
	_, code := l.git(dir, args...)
	return code == 0
}

func (l *launch) isCheckout() bool {
	stdout, _, code := l.exec(shell.Command{Argv: []string{"git", "-C", l.wt, "rev-parse", "--show-toplevel"}})
	if code != 0 {
		return false
	}
	return strings.TrimSpace(stdout) == resolved(l.wt)
}

func (l *launch) checkedOut() bool {
	return l.isCheckout() && l.gitQuiet(l.wt, "diff-index", "--cached", "--quiet", "HEAD", "--")
}

func (l *launch) registered() bool {
	said, _ := l.output(shell.Command{Argv: []string{"orca", "worktree", "show", "--worktree", "name:" + l.worktreeName, "--json"}})
	var shown struct {
		Result struct {
			Worktree struct {
				Path string `json:"path"`
			} `json:"worktree"`
		} `json:"result"`
	}
	if json.Unmarshal([]byte(said), &shown) != nil || shown.Result.Worktree.Path == "" {
		return false
	}
	l.wt = shown.Result.Worktree.Path
	return true
}

func (l *launch) removed() bool {
	var said string
	var code int
	if l.sparseLanes {
		stdout, stderr, status := l.exec(shell.Command{Argv: []string{"ccx", "vcs", "worktree", "rm", "--path", l.wt, "--force"}, Dir: l.main})
		said, code = stdout+stderr, status
	} else {
		said, code = l.tool("orca", "worktree", "rm", "--worktree", "path:"+l.wt, "--force", "--json")
	}
	if err := os.WriteFile(l.path(".rm.log"), []byte(said), 0o600); err != nil {
		return false
	}
	return code == 0
}

func (l *launch) undo() {
	if !l.rollingBack {
		return
	}
	l.rollingBack = false
	if l.terminal != "" {
		closed := false
		if l.terminal == l.owned {
			_, code := l.tool("orca", "terminal", "close", "--terminal", l.terminal, "--tab", "--json")
			closed = code == 0
		}
		if closed {
			l.undone += " terminal=" + l.terminal
		} else {
			l.kept += " terminal=" + l.terminal
		}
	}
	if l.holdsCommits() {
		l.kept += " worktree=" + l.wt + " branch=" + l.branch + " (it holds commits not on " + l.base + ")"
		return
	}
	queued := false
	switch {
	case l.made || (l.lost && l.registered()):
		switch {
		case !l.removed():
			l.kept += " worktree=" + l.wt + " (its removal failed: " + l.path(".rm.log") + ")"
		case l.removalQueued():
			queued = true
			_ = os.Remove(l.path(".worktree"))
			l.kept += " worktree=" + l.wt + " (its removal is queued: " + l.path(".rm.log") + ")"
		default:
			_ = os.Remove(l.path(".worktree"))
			l.undone += " worktree=" + l.wt
		}
	case l.lost && isDir(l.wt) && !l.isCheckout():
		if err := os.RemoveAll(l.wt); err == nil {
			l.undone += " worktree=" + l.wt
		} else {
			l.kept += " worktree=" + l.wt + " (" + err.Error() + ")"
		}
	}
	if l.created && l.gitQuiet(l.main, "show-ref", "--verify", "--quiet", "refs/heads/"+l.branch) {
		l.undoBranch(queued)
	}
	if !l.isCheckout() {
		_ = os.Remove(l.path(".worktree"))
	}
}

func (l *launch) undoBranch(queued bool) {
	if queued {
		l.kept += " branch=" + l.branch + " (its worktree's removal is queued)"
		return
	}
	if said, code := l.git(l.main, "branch", "-D", l.branch); code != 0 {
		l.kept += " branch=" + l.branch + " (" + tailLines(strings.TrimSpace(said), 1) + ")"
		return
	}
	l.undone += " branch=" + l.branch
}

func (l *launch) removalQueued() bool {
	if !l.sparseLanes {
		return false
	}
	listed, _ := l.output(shell.Command{Argv: []string{"git", "-C", l.main, "worktree", "list", "--porcelain"}})
	return slices.Contains(strings.Split(listed, "\n"), "worktree "+resolved(l.wt))
}

func (l *launch) holdsCommits() bool {
	if !l.created {
		return false
	}
	said, code := l.output(shell.Command{Argv: []string{"git", "-C", l.main, "rev-list", "--count", l.base + ".." + l.branch}})
	return code == 0 && strings.TrimSpace(said) != "0"
}

func modelRoute(model string) (string, string, bool) {
	switch {
	case model == "codex" || model == "sol":
		return agentCodex, "gpt-6.1-sol", true
	case model == "astra":
		return agentCodex, "gpt-6-astra", true
	case model == agentIncident:
		return agentIncident, "gpt-6.1-sol", true
	case strings.HasPrefix(model, "gpt-"):
		return agentCodex, model, true
	case model == "opus" || model == modelIncidentOpus:
		return agentClaude, "claude-opus-5-5", true
	case model == "sonnet":
		return agentClaude, "claude-sonnet-5-5", true
	case model == "fable":
		return agentClaude, "claude-fable-5-1", true
	case strings.HasPrefix(model, "claude-"):
		return agentClaude, model, true
	}
	return "", "", false
}

func validEffort(effort string) bool {
	return slices.Contains(efforts, effort)
}

func (l *launch) stateHome() string {
	if dir := l.Env("XDG_STATE_HOME"); dir != "" {
		return dir
	}
	return filepath.Join(l.Env("HOME"), ".local", "state")
}

func (s *Starter) Run(ctx context.Context, lane, model, effort, brief string) (string, error) {
	stages := stagesOf(s.Env, s.Bus, lane, s.Stderr)
	first := &launch{Starter: s, ctx: ctx, lane: lane, model: model, effort: effort, brief: brief, stages: stages}
	line, err := first.start()
	if first.stalled == "" || first.startTries > 0 || first.kept != "" || s.Env(fallbackEnv) == "1" {
		return line, err
	}
	fmt.Fprintf(s.Stderr, "%s retries once after a stall: %s\n", lane, first.stalled)
	if first.agent != agentIncident {
		s.Sleep(stallRetry)
	}
	retry := &launch{Starter: s, ctx: ctx, lane: lane, model: model, effort: effort, brief: brief, stages: stages}
	line, err = retry.start()
	var failed *Failed
	if errors.As(err, &failed) {
		failed.Line += "; this was its one retry"
	}
	return line, err
}

func (l *launch) readEnv() error {
	l.parent = l.Env("ORCA_LAUNCH_PARENT")
	if l.parent == "" {
		cwd, err := os.Getwd()
		if err != nil {
			return l.fail("cwd: %v", err)
		}
		l.parent = cwd
	}
	l.name = l.Env("ORCA_LAUNCH_PREFIX") + l.lane
	l.boot = l.envInt("ORCA_LAUNCH_BOOT_SECONDS", 180)
	l.worktreeWait = l.envInt("ORCA_LAUNCH_WORKTREE_SECONDS", 180)
	l.prepare = l.Env("ORCA_LAUNCH_PREPARE")
	l.prepareWait = l.envInt("ORCA_LAUNCH_PREPARE_SECONDS", 120)
	l.sparseLanes = l.Env("ORCA_LAUNCH_SPARSE") == "1"
	l.sparseFiles = l.envInt("ORCA_LAUNCH_SPARSE_FILES", 5000)
	l.stall = l.envInt("ORCA_LAUNCH_STALL_SECONDS", 180)
	l.bin = filepath.Join(l.Env("BINRUN_PLUGIN_ROOT"), "bin")
	return nil
}

func (l *launch) resolveRun() error {
	l.run = l.Env("ORCA_LAUNCH_RUN")
	if l.run == "" {
		if entry := ownDriveEntry(l.ctx, l.Env, l.Runner); entry != nil {
			l.run = entry.OrcaRun
		}
	}
	if l.run == "" {
		return l.fail("run: this session's drive binds no Orca run; set ORCA_LAUNCH_RUN or run drive.py start --orca-run <id>")
	}
	l.receipts = filepath.Join(l.stateHome(), "long-running", "orca-launch")
	l.state = l.Env("ORCA_LAUNCH_STATE")
	if l.state == "" {
		l.state = filepath.Join(l.receipts, l.run)
	}
	l.receipt = l.path(".json")
	if err := os.MkdirAll(l.state, 0o750); err != nil {
		return l.fail("state: %v", err)
	}
	return nil
}

func (l *launch) resolveLaunch() error {
	var known bool
	if l.agent, l.modelID, known = modelRoute(l.model); !known {
		return l.fail("model %s unknown: use opus, sonnet, fable, claude-*, sol, codex, incident, incident-opus, astra, or gpt-*", l.model)
	}
	if !validEffort(l.effort) {
		return l.fail("effort %s unknown: use low, medium, high, xhigh, or max", l.effort)
	}
	raw, err := os.ReadFile(l.brief)
	if err != nil {
		return l.fail("brief %s unreadable", l.brief)
	}
	if l.brief, err = filepath.Abs(l.brief); err != nil {
		return l.fail("brief: %v", err)
	}
	text := string(raw)
	if faults := (Order{Lane: l.lane, Model: l.model, Effort: l.effort, Brief: l.brief}).costFaults(text); len(faults) > 0 {
		return l.fail("%s", strings.Join(faults, "; "))
	}
	if err := l.resolveRepo(); err != nil {
		return err
	}
	l.drive = driveName(l.Env, l.Env("ORCA_LAUNCH_RUN"))
	l.slackMCP = briefSlack.MatchString(text)
	if err := l.composeCommand(); err != nil {
		return err
	}
	if briefDesk.MatchString(text) {
		l.desk = standingDesk
	}
	if briefNoPR.MatchString(text) {
		l.noPR = noPRLine
	}
	if err := l.writeLaneBrief(text); err != nil {
		return l.fail("brief copy: %v", err)
	}
	return nil
}

func (l *launch) resolveRepo() error {
	if id := l.Env("ORCA_LAUNCH_REPO"); id != "" {
		l.repo = "id:" + id
	} else {
		common, code := l.output(shell.Command{Argv: []string{"git", "-C", l.parent, "rev-parse", "--path-format=absolute", "--git-common-dir"}})
		if code != 0 {
			return l.fail("repo: %s is not a git checkout; set ORCA_LAUNCH_REPO", l.parent)
		}
		l.repo = "path:" + filepath.Dir(strings.TrimSpace(common))
	}
	said, _ := l.saved(".repo.json", "orca", "repo", "show", "--repo", l.repo, "--json")
	if json.Unmarshal([]byte(said), &l.repoRead) != nil || l.repoRead.Result.Repo.Path == "" {
		return l.fail("repo: orca repo show finds no repo at %s (%s); set ORCA_LAUNCH_REPO, or register the main checkout with orca repo add --path", l.repo, orcaError(said))
	}
	l.main = l.repoRead.Result.Repo.Path
	l.gitUser = l.repoRead.Result.Repo.GitUsername
	l.root = l.Env("ORCA_LAUNCH_ROOT")
	if l.root == "" && l.sparseLanes {
		if pool, code := l.output(shell.Command{Argv: []string{"git", "-C", l.main, "config", "ccx.worktreeRoot"}}); code == 0 {
			l.root = filepath.Join(strings.TrimSpace(pool), filepath.Base(l.main))
		}
	}
	if l.root == "" {
		l.root = filepath.Dir(l.parent)
	}
	l.runID = cutRunes(strings.TrimPrefix(l.run, "run_"), 8)
	if raw, err := os.ReadFile(l.path(".worktree")); err == nil {
		l.wt = strings.TrimSpace(string(raw))
	}
	l.base = l.Env("ORCA_LAUNCH_BASE")
	if l.base == "" {
		base, code := l.output(shell.Command{Argv: []string{"git", "-C", l.parent, "symbolic-ref", "--short", "refs/remotes/origin/HEAD"}})
		if code != 0 {
			return l.fail("base: %s has no origin/HEAD; set ORCA_LAUNCH_BASE", l.parent)
		}
		l.base = strings.TrimSpace(base)
	}
	return nil
}

func (l *launch) output(command shell.Command) (string, int) {
	stdout, _, code := l.exec(command)
	return stdout, code
}

func (l *launch) composeCommand() error {
	var extra strings.Builder
	if l.drive != "" {
		extra.WriteString(" CLAUDE_LONG_RUNNING_DRIVE=" + l.drive)
	}
	command := "env CLAUDE_LONG_RUNNING_LANE=" + l.lane + extra.String() + " " + claudeLaneEnv
	if l.lane == coordinatorLane {
		command += " CLAUDE_CODE_PROMPT_CACHE_TTL=1h"
	}
	command += " " + laneCompact
	denied := laneDeniedTools
	if l.lane == coordinatorLane {
		denied += coordinatorDenied
	}
	command += " claude --allow-dangerously-skip-permissions --permission-mode bypassPermissions --disallowedTools " + denied + " --strict-mcp-config --no-chrome"
	if config := l.Env("ORCA_LAUNCH_MCP_CONFIG"); config != "" {
		command += " --mcp-config " + config
	}
	if config := l.Env("ORCA_LAUNCH_SLACK_MCP_CONFIG"); config != "" && l.slackMCP {
		command += " --mcp-config " + config
	}
	if l.model == modelIncidentOpus {
		config, err := alertTelemetryMCP(l.Env)
		if err != nil {
			return l.fail("alert telemetry: %v", err)
		}
		command += " --mcp-config " + shellQuote(config)
	}
	if args := l.Env("ORCA_LAUNCH_CLAUDE_ARGS"); args != "" {
		command += " " + args
	}
	var settings []string
	if l.lane == coordinatorLane {
		fragment, flags, err := l.coordinatorFlags()
		if err != nil {
			return err
		}
		settings = append(settings, fragment)
		command += flags
	}
	if l.model == modelIncidentOpus {
		settings = append(settings, claudeFastSettings)
	}
	l.command = command + " --model " + l.modelID + " --effort " + l.effort + laneSettingsFlag(l.Env, settings...)
	if l.agent == agentClaude {
		return nil
	}
	tier := ""
	if l.agent == agentIncident {
		tier = " -c service_tier=fast"
	}
	mcp := l.Env("ORCA_LAUNCH_CODEX_MCP")
	if mcp == "" {
		mcp = "{}"
	}
	home := l.Env("CODEX_HOME")
	if home == "" {
		home = filepath.Join(l.Env("HOME"), ".codex")
	}
	off, err := codexDisabled(filepath.Join(home, "config.toml"), mcp)
	if err != nil {
		return l.fail("codex config: cannot read the mcp_servers of %s", filepath.Join(home, "config.toml"))
	}
	l.command = "sh -c 'PATH=" + l.bin + ":$PATH exec env CLAUDE_LONG_RUNNING_LANE=" + l.lane + extra.String() + " codex --dangerously-bypass-approvals-and-sandbox -c model=" + l.modelID + tier + " -c model_reasoning_effort=" + l.effort + codexCompact + codexLaneConfig + " -c mcp_servers=" + mcp + off + "'"
	return nil
}

func (l *launch) coordinatorFlags() (string, string, error) {
	sections := filepath.Join(filepath.Dir(l.brief), coordinatorSections)
	if _, err := os.Stat(sections); errors.Is(err, os.ErrNotExist) {
		return coordinatorListing, "", nil
	}
	rules, err := writeCoordinatorRules(sections, filepath.Join(l.Env("HOME"), ".claude", "CLAUDE.md"))
	if err != nil {
		return "", "", l.fail("coordinator rules: %v", err)
	}
	return coordinatorNoMemory + coordinatorListing, " --append-system-prompt-file " + rules, nil
}

func writeCoordinatorRules(sections, instructions string) (string, error) {
	wanted, err := os.ReadFile(sections)
	if err != nil {
		return "", err
	}
	text, err := os.ReadFile(instructions)
	if err != nil {
		return "", err
	}
	rules, err := sectionsOf(string(text), strings.Split(strings.TrimSpace(string(wanted)), "\n"))
	if err != nil {
		return "", err
	}
	path := filepath.Join(filepath.Dir(sections), coordinatorRules)
	return path, os.WriteFile(path, []byte(rules), 0o600)
}

func sectionsOf(text string, wanted []string) (string, error) {
	sections := map[string]string{}
	for _, part := range markdownSection.Split(text, -1)[1:] {
		heading, _, _ := strings.Cut(part, "\n")
		sections[strings.TrimSpace(heading)] = "## " + part
	}
	var kept strings.Builder
	for _, name := range wanted {
		section, found := sections[name]
		if !found {
			return "", fmt.Errorf("~/.claude/CLAUDE.md has no section %q", name)
		}
		kept.WriteString(section)
	}
	return kept.String(), nil
}

func codexDisabled(config, mcp string) (string, error) {
	var named struct {
		Named map[string]any `toml:"named"`
	}
	if _, err := toml.Decode("named = "+mcp, &named); err != nil {
		return "", err
	}
	raw, err := os.ReadFile(config)
	if errors.Is(err, os.ErrNotExist) {
		return "", nil
	}
	if err != nil {
		return "", err
	}
	var read struct {
		Servers map[string]any `toml:"mcp_servers"`
	}
	if _, err := toml.Decode(string(raw), &read); err != nil {
		return "", err
	}
	var off strings.Builder
	for _, name := range sortedAnyKeys(read.Servers) {
		if _, kept := named.Named[name]; !kept && name != "datadog" && name != "sentry" {
			off.WriteString(" -c mcp_servers." + name + ".enabled=false")
		}
	}
	return off.String(), nil
}

func sortedAnyKeys(table map[string]any) []string {
	keys := make([]string, 0, len(table))
	for key := range table {
		keys = append(keys, key)
	}
	slices.Sort(keys)
	return keys
}

func (l *launch) writeLaneBrief(text string) error {
	l.laneBrief = l.brief
	if l.agent == agentClaude {
		return nil
	}
	l.laneBrief = l.path(".brief.md")
	var copied strings.Builder
	if l.drive != "" {
		fmt.Fprintf(&copied, "## First command\n\n%s\n\n", fmt.Sprintf("Codex runs no session hook, so `cci who` lists you only once you post. Before anything else, run `cci post --drive '%s' --lane '%s' --kind state '%s: registered at its first command'`, so a claim made for you and the lane supervisor's launch check both find your session.", l.drive, l.lane, l.lane))
	}
	copied.WriteString(text)
	if l.agent == agentIncident {
		fmt.Fprintf(&copied, "\n\n## PR prose\n\n%s\n", "The fix outranks its prose. The moment the candidate is committed, push the branch and open the PR within 10 minutes of the candidate, with a short body you write yourself in plain words: what broke, what the change does, the test that proves it, the risk. Never wait for a red-baseline build, the coordinator, or an Orca answer. The fix lands fast with the body as opened; correct the body unasked with `gh pr edit <n> --body-file <file>` the moment it turns out false or evidence a grader reads arrives, such as the failing-first CI link or a scope change on a later push.")
	} else {
		fmt.Fprintf(&copied, "\n\n## PR prose\n\n%s\n", "You write your own PR title, PR body, and commit message. Never ask the coordinator, another lane, or an Orca question for prose.")
	}
	if l.drive != "" {
		fmt.Fprintf(&copied, "\n\n## PR record\n\n%s\n", fmt.Sprintf("Codex runs no PR hook, so `ccx vcs ship` neither registers your PR in the drive's ledger nor posts its `opened` record, and the incident clock and the landing desk read both. Right after each ship that opens a PR, run `'%s/drive.py' record --opened --drive '%s' --session \"$CODEX_THREAD_ID\" --lane '%s' --cwd \"$PWD\" --pr <number>=<head>` from your worktree.", l.bin, l.drive, l.lane))
	}
	staged := l.laneBrief + ".tmp"
	if err := os.WriteFile(staged, []byte(copied.String()), 0o600); err != nil {
		return err
	}
	return os.Rename(staged, l.laneBrief)
}

func (l *launch) pointer(brief string) string {
	return "Lane " + l.lane + ": read " + brief + " in full first and execute it exactly; it outranks Orca's preamble and any leave-uncommitted default, so commit, push, open PRs and post as it says. Worktree " + l.wt + ", bypass-permissions mode. Mail reaches you only between tool calls, so run anything over 5 minutes in the background. Ask, report and raise a blocker only with `cci post --kind ask|report|blocker`, since the preamble's `send` and `ask` carry the dispatch lifecycle alone (heartbeats, relay acks, worker_done); its Escalate rules hold." + l.node + l.sparse + l.desk + l.noPR
}

func (l *launch) composeSpec() error {
	l.spec = l.pointer(l.laneBrief)
	sum := sha256.Sum256([]byte(l.laneBrief))
	link := filepath.Join(l.receipts, hex.EncodeToString(sum[:])[:8])
	if len(l.spec) <= specLimit || len(link) >= len(l.laneBrief) {
		return nil
	}
	if err := os.MkdirAll(l.receipts, 0o750); err != nil {
		return err
	}
	if err := os.Remove(link); err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	if err := os.Symlink(l.laneBrief, link); err != nil {
		return err
	}
	l.spec = l.pointer(link)
	return nil
}

func (l *launch) checkBinding() error {
	l.rebind = fmt.Sprintf("post the launch to the drive's orca runner, whose terminal coordinates the Run: desk-launch.py lane %s %s %s %s; a drive with no runner rebinds from its coordinator's Orca terminal: orca orchestration run-use --id %s", l.lane, l.model, l.effort, l.brief, l.run)
	said, _ := l.saved(".binding.json", "orca", orchestration, "run-current", "--full", "--json")
	var current struct {
		Result struct {
			Run *struct {
				ID string `json:"id"`
			} `json:"run"`
		} `json:"result"`
	}
	bound := ""
	if json.Unmarshal([]byte(said), &current) == nil && current.Result.Run != nil {
		bound = current.Result.Run.ID
	}
	if bound == l.run {
		return nil
	}
	handle := l.Env("ORCA_TERMINAL_HANDLE")
	if handle == "" {
		handle = "unset"
	}
	coordinates := bound
	if coordinates == "" {
		coordinates = "no Run"
	}
	return l.fail("coordinator binding: terminal %s coordinates %s, not %s (%s); %s", handle, coordinates, l.run, orcaError(said), l.rebind)
}

func (l *launch) start() (string, error) {
	if err := l.readEnv(); err != nil {
		return "", err
	}
	if err := l.resolveRun(); err != nil {
		return "", err
	}
	if err := l.resolveLaunch(); err != nil {
		return "", err
	}
	if err := l.checkBinding(); err != nil {
		return "", err
	}
	l.parentArgs = []string{"--parent-worktree", "path:" + l.parent}
	if l.agent == agentIncident || l.Env("ORCA_LAUNCH_NO_PARENT") == "1" {
		l.parentArgs = []string{"--no-parent"}
	}
	l.rollingBack = true
	l.stages.say(l.ctx, "making a worktree")
	if err := l.placeWorktree(); err != nil {
		return "", err
	}
	if err := os.WriteFile(l.path(".worktree"), []byte(l.wt+"\n"), 0o600); err != nil {
		return "", l.fail("worktree receipt: %v", err)
	}
	if err := l.runPrepare(); err != nil {
		return "", l.fail("prepare: %v", err)
	}
	if l.hasWarmLane() {
		l.node = " node_modules is a shared symlink farm; never run yarn install, ask warm-lane to rebuild for a changed lockfile."
	}
	if out, _ := l.output(shell.Command{Argv: []string{"git", "-C", l.wt, "config", "--get", "--bool", "core.sparseCheckout"}}); strings.TrimSpace(out) == "true" {
		l.sparse = " This lane is sparse: widen with `lr sparse add <dir>` before reading outside it."
	}
	if err := l.composeSpec(); err != nil {
		return "", l.fail("spec: %v", err)
	}
	l.stages.say(l.ctx, "opening a terminal and booting the agent")
	if err := l.openTerminal(); err != nil {
		return "", err
	}
	if err := l.awaitAgent(); err != nil {
		return "", err
	}
	l.stages.say(l.ctx, "starting the worker")
	return l.startWorker()
}

func (l *launch) listedWorktree() error {
	for {
		if _, ok := l.saved(".worktree.json", "orca", "worktree", "show", "--worktree", "path:"+l.wt, "--json"); ok {
			return nil
		}
		if err := l.pauseWithin("Orca does not list worktree "+l.wt+" yet", l.stall+worktreeCacheSeconds); err != nil {
			return err
		}
	}
}

func (l *launch) fresh() error {
	namesPath := l.path(".names")
	used := map[string]bool{}
	if raw, err := os.ReadFile(namesPath); err == nil {
		for line := range strings.SplitSeq(string(raw), "\n") {
			used[line] = true
		}
	}
	for n := 1; ; n++ {
		l.worktreeName = strings.ReplaceAll(fmt.Sprintf("%s-%s-%d", l.name, l.runID, n), "/", "-")
		l.branch = l.worktreeName
		if l.gitUser != "" {
			l.branch = l.gitUser + "/" + l.worktreeName
		}
		l.wt = filepath.Join(l.root, l.worktreeName)
		if used[l.worktreeName] {
			continue
		}
		if _, err := os.Lstat(l.wt); err == nil {
			continue
		}
		if l.gitQuiet(l.main, "show-ref", "--verify", "--quiet", "refs/heads/"+l.branch) {
			continue
		}
		names, err := os.OpenFile(namesPath, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o600)
		if err != nil {
			return err
		}
		_, err = names.WriteString(l.worktreeName + "\n")
		if closed := names.Close(); err == nil {
			err = closed
		}
		return err
	}
}

func (l *launch) added() (string, bool) {
	remote, branch, _ := strings.Cut(l.base, "/")
	if l.gitQuiet(l.main, "remote", "get-url", remote) {
		l.exec(shell.Command{Argv: []string{"ccx", "vcs", "fetch", "--remote", remote, branch}, Dir: l.main})
	}
	said, code := l.git(l.main, "worktree", "add", "--no-checkout", "--no-track", "-b", l.branch, l.wt, l.base)
	return said, code == 0
}

func exists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}

func (l *launch) shared() error {
	for _, path := range l.repoRead.Result.Repo.SymlinkPaths {
		source, target := filepath.Join(l.main, path), filepath.Join(l.wt, path)
		if !exists(source) || exists(target) {
			continue
		}
		if err := os.MkdirAll(filepath.Dir(target), 0o750); err != nil {
			return err
		}
		if err := os.Symlink(source, target); err != nil {
			return err
		}
	}
	raw, err := os.ReadFile(filepath.Join(l.main, ".worktreeinclude"))
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	for line := range strings.SplitSeq(string(raw), "\n") {
		path := strings.TrimRight(strings.TrimPrefix(strings.TrimSpace(line), "./"), "/")
		if path == "" || strings.HasPrefix(path, "#") {
			continue
		}
		source, target := filepath.Join(l.main, path), filepath.Join(l.wt, path)
		if !exists(source) || exists(target) || !l.gitQuiet(l.main, "check-ignore", "-q", "--", path) {
			continue
		}
		if err := os.MkdirAll(filepath.Dir(target), 0o750); err != nil {
			return err
		}
		if said, code := l.tool("cp", "-R", source, target); code != 0 {
			return errors.New(strings.TrimSpace(said))
		}
	}
	return nil
}

func tailLines(text string, count int) string {
	lines := strings.Split(strings.TrimRight(text, "\n"), "\n")
	return strings.Join(lines[max(0, len(lines)-count):], "\n")
}

func (l *launch) sparseCone(briefText string) []string {
	cone := slices.Clone(baseCone)
	for _, match := range briefPaths.FindAllStringSubmatch(briefText, -1) {
		cone = append(cone, strings.Split(match[1], ",")...)
	}
	listing, _ := l.output(shell.Command{Argv: []string{"git", "-C", l.wt, "ls-tree", "-r", "--name-only", "HEAD"}})
	if strings.Count(listing, "\n") <= l.sparseFiles {
		return nil
	}
	return withGoModules(cone, func(goMod string) string {
		read, code := l.output(shell.Command{Argv: []string{"git", "-C", l.wt, "show", "HEAD:" + goMod}})
		if code != 0 {
			return ""
		}
		return read
	})
}

func (l *launch) addSparseWorktree() error {
	if err := l.fresh(); err != nil {
		return l.fail("names: %v", err)
	}
	l.created = true
	log := l.path(".worktree.log")
	said, ok := l.added()
	if err := os.WriteFile(log, []byte(said), 0o600); err != nil {
		return l.fail("worktree log: %v", err)
	}
	if !ok {
		return l.fail("worktree add %s: %s", l.wt, tailLines(said, 3))
	}
	l.made = true
	raw, err := os.ReadFile(l.brief)
	if err != nil {
		return l.fail("brief %s unreadable", l.brief)
	}
	cone := l.sparseCone(string(raw))
	var checkedOut strings.Builder
	checked := func() bool {
		if len(cone) > 0 {
			stdout, stderr, code := l.exec(shell.Command{Argv: []string{"git", "-C", l.wt, "sparse-checkout", "set", "--cone", "--stdin"}, Stdin: strings.Join(cone, "\n") + "\n"})
			checkedOut.WriteString(stdout + stderr)
			if code != 0 {
				return false
			}
		}
		out, code := l.git(l.wt, "reset", "--quiet", "--hard")
		checkedOut.WriteString(out)
		if code != 0 {
			return false
		}
		if err := l.shared(); err != nil {
			checkedOut.WriteString(err.Error())
			return false
		}
		return true
	}
	done := checked()
	if err := os.WriteFile(log, []byte(said+checkedOut.String()), 0o600); err != nil {
		return l.fail("worktree log: %v", err)
	}
	if !done {
		return l.fail("worktree checkout %s: %s", l.wt, tailLines(said+checkedOut.String(), 3))
	}
	if len(cone) > 0 {
		fmt.Fprintf(l.Stderr, "%s sparse: %s; widen with cd %s && lr sparse add <dir>\n", l.lane, strings.Join(cone, " "), l.wt)
	}
	if err := l.listedWorktree(); err != nil {
		return err
	}
	said, setOK := l.saved(".worktree.json", append(append([]string{"orca", "worktree", "set", "--worktree", "path:" + l.wt, "--display-name", l.worktreeName}, l.parentArgs...), "--json")...)
	if !setOK {
		return l.fail("worktree set %s: %s", l.wt, orcaError(said))
	}
	return nil
}

func (l *launch) createWorktree() error {
	if err := l.fresh(); err != nil {
		return l.fail("names: %v", err)
	}
	l.created, l.startup = true, true
	attempt := 0
	for !l.registered() {
		attempt++
		if attempt > worktreeCreates {
			return l.fail("worktree create: %s", orcaError(l.savedFile(".worktree.json")))
		}
		args := append([]string{"orca", "worktree", "create", "--name", l.worktreeName, "--repo", l.repo, "--base-branch", l.base}, l.parentArgs...)
		said, ok := l.saved(".worktree.json", append(args, "--setup", "run", "--json")...)
		var created struct {
			Result struct {
				Worktree struct {
					Path string `json:"path"`
				} `json:"worktree"`
			} `json:"result"`
			Error struct {
				Code string `json:"code"`
			} `json:"error"`
		}
		decoded := json.Unmarshal([]byte(said), &created)
		if ok && decoded == nil && created.Result.Worktree.Path != "" {
			l.made, l.wt = true, created.Result.Worktree.Path
			break
		}
		if decoded != nil || created.Error.Code != "worktree_exists" {
			l.lost = true
		}
		for waited := 0; !l.registered() && waited < l.worktreeWait; waited += int(startPoll / time.Second) {
			if err := l.pause("Orca has not registered worktree " + l.worktreeName); err != nil {
				return err
			}
		}
	}
	for waited := 0; !l.made && !l.checkedOut(); waited += int(startPoll / time.Second) {
		if waited >= l.worktreeWait {
			return l.fail("worktree checkout: %s registered, but its index still differs from HEAD after %ds", l.wt, l.worktreeWait)
		}
		if err := l.pause("worktree " + l.wt + " is registered, and its index still differs from HEAD"); err != nil {
			return err
		}
	}
	return nil
}

func (l *launch) placeWorktree() error {
	switch {
	case l.wt != "" && l.isCheckout():
		return l.listedWorktree()
	case l.sparseLanes:
		return l.addSparseWorktree()
	}
	return l.createWorktree()
}

func (l *launch) hasWarmLane() bool {
	info, err := os.Stat(filepath.Join(l.wt, warmLane))
	return err == nil && info.Mode().IsRegular()
}

func (l *launch) runPrepare() error {
	if l.prepare == "" && l.hasWarmLane() {
		l.prepare = warmLane
	}
	if !l.created || l.prepare == "" {
		return nil
	}
	stdout, stderr, code := l.exec(shell.Command{Argv: []string{"sh", "-c", l.prepare}, Dir: l.wt, Timeout: time.Duration(l.prepareWait) * time.Second})
	log := l.path(".prepare.log")
	if err := os.WriteFile(log, []byte(stdout+stderr), 0o600); err != nil {
		return err
	}
	if code == 0 {
		return nil
	}
	if l.laneBrief == l.brief {
		raw, err := os.ReadFile(l.brief)
		if err != nil {
			return err
		}
		if err := os.WriteFile(l.path(".brief.md"), raw, 0o600); err != nil {
			return err
		}
	}
	l.laneBrief = l.path(".brief.md")
	brief, err := os.OpenFile(l.laneBrief, os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	_, err = fmt.Fprintf(brief, "\n\n## Worktree preparation\n\nThe launch ran `%s` in this worktree and it failed; its output is in %s. Run it yourself before you build.\n", l.prepare, log)
	if closed := brief.Close(); err == nil {
		err = closed
	}
	return err
}

type listedTerminals struct {
	Result struct {
		Terminals []struct {
			Handle string `json:"handle"`
			Agent  string `json:"agentIdentity"`
		} `json:"terminals"`
	} `json:"result"`
}

func (l *launch) listTerminals() error {
	for listing := 1; ; listing++ {
		said, ok := l.saved(".terminals.json", "orca", "terminal", "list", "--worktree", "path:"+l.wt, "--json")
		var listed listedTerminals
		if ok && json.Unmarshal([]byte(said), &listed) == nil && listed.Result.Terminals != nil {
			l.listed = l.listed[:0]
			for _, terminal := range listed.Result.Terminals {
				l.listed = append(l.listed, terminal.Handle)
			}
			return nil
		}
		if listing >= terminalListAttempts {
			return l.fail("terminal list: %s", orcaError(said))
		}
		if err := l.pause("orca terminal list fails for " + l.wt); err != nil {
			return err
		}
	}
}

func (l *launch) adopt() error {
	for waited := 0; waited < l.boot; {
		if err := l.pause("no terminal has appeared in " + l.wt + " after orca terminal create"); err != nil {
			return err
		}
		waited += int(startPoll / time.Second)
		if err := l.listTerminals(); err != nil {
			return err
		}
		for _, handle := range l.listed {
			if !slices.Contains(l.before, handle) {
				l.terminal = handle
				return nil
			}
		}
	}
	return nil
}

func truthy(value any) bool {
	return value != nil && value != false
}

func (l *launch) closeStartupShells() error {
	var created struct {
		Result map[string]any `json:"result"`
	}
	if json.Unmarshal([]byte(l.savedFile(".worktree.json")), &created) != nil {
		return nil
	}
	receipt, _ := created.Result["setupReceipt"].(map[string]any)
	if !truthy(created.Result["worktree"]) || truthy(created.Result["setup"]) || truthy(created.Result["defaultTabs"]) || truthy(receipt["terminalHandle"]) {
		return nil
	}
	var listed listedTerminals
	if json.Unmarshal([]byte(l.savedFile(".terminals.json")), &listed) != nil {
		return nil
	}
	var said strings.Builder
	for _, terminal := range listed.Result.Terminals {
		if terminal.Agent == "" {
			out, _ := l.tool("orca", "terminal", "close", "--terminal", terminal.Handle, "--tab", "--json")
			said.WriteString(out)
		}
	}
	if said.Len() == 0 {
		return nil
	}
	return os.WriteFile(l.path(".startup.json"), []byte(said.String()), 0o600)
}

func (l *launch) openTerminal() error {
	if err := l.listTerminals(); err != nil {
		return err
	}
	l.before = slices.Clone(l.listed)
	if l.startup {
		if err := l.closeStartupShells(); err != nil {
			return l.fail("startup shells: %v", err)
		}
	}
	for attempt := 1; l.terminal == ""; attempt++ {
		if attempt > terminalCreateAttempts {
			return l.fail("terminal create: %s", orcaError(l.savedFile(".terminal.json"), l.savedFile(".terminal.err")))
		}
		stdout, stderr, _ := l.exec(shell.Command{Argv: []string{"orca", "terminal", "create", "--worktree", "path:" + l.wt, "--title", l.name, "--command", l.command, "--json"}})
		if err := os.WriteFile(l.path(".terminal.json"), []byte(stdout), 0o600); err != nil {
			return l.fail("terminal receipt: %v", err)
		}
		if err := os.WriteFile(l.path(".terminal.err"), []byte(stderr), 0o600); err != nil {
			return l.fail("terminal receipt: %v", err)
		}
		var created struct {
			Result struct {
				Terminal struct {
					Handle string `json:"handle"`
				} `json:"terminal"`
			} `json:"result"`
		}
		if json.Unmarshal([]byte(stdout), &created) == nil {
			l.terminal = created.Result.Terminal.Handle
		}
		l.owned = l.terminal
		if l.terminal != "" {
			break
		}
		if createRefused(stdout) {
			return l.fail("terminal create: %s", orcaError(stdout, stderr))
		}
		if err := l.adopt(); err != nil {
			return err
		}
	}
	return nil
}

func createRefused(raw string) bool {
	var reply struct {
		OK    *bool `json:"ok"`
		Error struct {
			Message string `json:"message"`
		} `json:"error"`
	}
	return json.Unmarshal([]byte(raw), &reply) == nil && reply.OK != nil && !*reply.OK && !strings.HasPrefix(reply.Error.Message, createTimedOut)
}

func (l *launch) screenLacksAgent(marker string) bool {
	said, code := l.output(shell.Command{Argv: []string{"orca", "terminal", "read", "--terminal", l.terminal, "--screen", "--json"}})
	if code != 0 {
		return false
	}
	var read struct {
		Result struct {
			Terminal struct {
				Source string   `json:"source"`
				Tail   []string `json:"tail"`
			} `json:"terminal"`
		} `json:"result"`
	}
	if json.Unmarshal([]byte(said), &read) != nil || read.Result.Terminal.Source == screenUnavailable || read.Result.Terminal.Tail == nil {
		return false
	}
	screen := strings.Join(read.Result.Terminal.Tail, "")
	return !strings.Contains(screen, marker) && !strings.Contains(screen, bypassOn)
}

type bootRead struct {
	Result *struct {
		Wait *struct {
			Satisfied bool   `json:"satisfied"`
			Status    string `json:"status"`
			Blocked   string `json:"blockedReason"`
		} `json:"wait"`
	} `json:"result"`
}

func (l *launch) booted(ms int) bool {
	said, ok := l.saved(".boot.json", "orca", "terminal", "wait", "--terminal", l.terminal, "--for", "tui-idle", "--timeout-ms", strconv.Itoa(ms), "--json")
	var read bootRead
	if !ok || json.Unmarshal([]byte(said), &read) != nil || read.Result == nil || read.Result.Wait == nil {
		return false
	}
	return read.Result.Wait.Satisfied && read.Result.Wait.Status == "running"
}

func (l *launch) bootReads() string {
	said := l.savedFile(".boot.json")
	var read bootRead
	if json.Unmarshal([]byte(said), &read) == nil && read.Result != nil && read.Result.Wait != nil {
		if read.Result.Wait.Blocked != "" {
			return read.Result.Wait.Blocked
		}
		return "status=" + read.Result.Wait.Status
	}
	return orcaError(said)
}

func (l *launch) awaitAgent() error {
	marker := "CLAUDE_LONG_RUNNING_LANE=" + l.lane
	if l.agent != agentClaude {
		marker = l.modelID
	}
	ceiling := l.boot * 1000
	if l.booted(ceiling / 3) {
		return nil
	}
	typed := ""
	if l.screenLacksAgent(marker) {
		if _, code := l.tool("orca", "terminal", "send", "--terminal", l.terminal, "--text", l.command, "--enter", "--json"); code != 0 {
			return l.fail("command send terminal=%s after Orca dropped the startup command", l.terminal)
		}
		typed = "; the command was typed into the terminal once"
	}
	if !l.booted(ceiling - ceiling/3) {
		return l.fail("boot terminal=%s: orca terminal wait --for tui-idle reads %s after %ds%s", l.terminal, l.bootReads(), l.boot, typed)
	}
	return nil
}

func (l *launch) retryable(task string) bool {
	for _, status := range []string{statusFailed, "blocked"} {
		said, _ := l.output(shell.Command{Argv: []string{"orca", orchestration, "task-list", "--run", l.run, "--status", status, "--brief", "--full", "--json"}})
		var listed struct {
			Result struct {
				Tasks []struct {
					ID string `json:"id"`
				} `json:"tasks"`
			} `json:"result"`
		}
		if json.Unmarshal([]byte(said), &listed) != nil {
			continue
		}
		for _, candidate := range listed.Result.Tasks {
			if candidate.ID == task {
				return true
			}
		}
	}
	return false
}

type workerReceipt struct {
	Result struct {
		TaskID      string `json:"taskId"`
		DispatchID  string `json:"dispatchId"`
		State       string `json:"state"`
		FailedStage string `json:"failedStage"`
	} `json:"result"`
}

func readReceipt(raw string) workerReceipt {
	var receipt workerReceipt
	if docs := documents(raw); len(docs) > 0 {
		_ = json.Unmarshal(docs[len(docs)-1], &receipt)
	}
	return receipt
}

func refusalOf(raw string) string {
	docs := documents(raw)
	if len(docs) != 1 {
		return ""
	}
	var reply struct {
		OK    *bool `json:"ok"`
		Error struct {
			Code string `json:"code"`
		} `json:"error"`
	}
	if json.Unmarshal(docs[0], &reply) != nil || reply.OK == nil || *reply.OK {
		return ""
	}
	return reply.Error.Code
}

func (l *launch) screenText() string {
	said, _ := l.output(shell.Command{Argv: []string{"orca", "terminal", "read", "--terminal", l.terminal, "--screen", "--json"}})
	var read struct {
		Result struct {
			Terminal struct {
				Tail any `json:"tail"`
			} `json:"terminal"`
		} `json:"result"`
	}
	if json.Unmarshal([]byte(said), &read) != nil {
		return ""
	}
	if text, isText := read.Result.Terminal.Tail.(string); isText {
		return text
	}
	var out strings.Builder
	encoder := json.NewEncoder(&out)
	encoder.SetEscapeHTML(false)
	if encoder.Encode(read.Result.Terminal.Tail) != nil {
		return ""
	}
	return strings.TrimRight(out.String(), "\n")
}

func (l *launch) workerArgs() []string {
	previous := readReceipt(l.savedFile(".json"))
	if task := previous.Result.TaskID; task != "" && l.retryable(task) {
		return []string{"--task", task, "--retry-of", previous.Result.DispatchID, "--terminal", l.terminal}
	}
	return []string{"--spec", l.spec, "--task-title", l.name, "--terminal", l.terminal}
}

func (l *launch) callWorkerStart(args []string) (string, string, error) {
	timeout := workerStartCodexMS
	if l.agent == agentClaude {
		timeout = workerStartClaudeMS
	}
	base := append(append([]string{"orca", orchestration, "worker-start", "--run", l.run}, args...), "--worktree", "path:"+l.wt, "--timeout-ms", timeout, "--full", "--json")
	argv := base
	for waited := 0; ; waited += int(startPoll / time.Second) {
		stdout, stderr, code := l.exec(shell.Command{Argv: argv})
		l.startCode = code
		l.startTries++
		if err := os.WriteFile(l.receipt+".new", []byte(stdout), 0o600); err != nil {
			return "", "", l.fail("worker receipt: %v", err)
		}
		if err := os.WriteFile(l.path(".worker.err"), []byte(stderr), 0o600); err != nil {
			return "", "", l.fail("worker receipt: %v", err)
		}
		request := replayable(stdout)
		if code == 0 || (request == "" && refusalOf(stdout) != refusalUnconfigured) || waited >= l.boot {
			return stdout, stderr, nil
		}
		argv = base
		reason := "worker-start refuses terminal " + l.terminal + " as agent_unconfigured"
		if request != "" {
			argv = slices.Concat(base, []string{"--retry-request", request})
			reason = "the Orca runtime did not answer worker-start request " + request
		}
		if err := l.pause(reason); err != nil {
			return "", "", err
		}
	}
}

func (l *launch) turnStarted(dispatch string) (bool, error) {
	for waited := 0; ; waited += int(startPoll / time.Second) {
		said, _ := l.output(shell.Command{Argv: []string{"orca", orchestration, "worker-show", "--dispatch", dispatch, "--full", "--json"}})
		var shown struct {
			Result struct {
				Projection struct {
					Stage struct {
						Activity string `json:"activity"`
					} `json:"stage"`
				} `json:"projection"`
				Worker struct {
					State string `json:"state"`
				} `json:"worker"`
			} `json:"result"`
		}
		if json.Unmarshal([]byte(said), &shown) == nil && (shown.Result.Projection.Stage.Activity == "working" || shown.Result.Worker.State == "ready" || shown.Result.Worker.State == "succeeded") {
			return true, nil
		}
		if waited >= l.boot {
			return false, nil
		}
		if err := l.pause("dispatch " + dispatch + " shows no turn started"); err != nil {
			return false, err
		}
	}
}

func (l *launch) startWorker() (string, error) {
	stdout, stderr, err := l.callWorkerStart(l.workerArgs())
	if err != nil {
		return "", err
	}
	l.rollingBack = false
	receipt := readReceipt(stdout)
	switch {
	case receipt.Result.TaskID != "" && receipt.Result.DispatchID != "":
		if err := os.Rename(l.receipt+".new", l.receipt); err != nil {
			return "", l.fail("worker receipt: %v", err)
		}
		if err := os.WriteFile(l.path(".terminal"), []byte(l.terminal+"\n"), 0o600); err != nil {
			return "", l.fail("terminal receipt: %v", err)
		}
	case l.startCode != 0:
		refused := refusalOf(stdout)
		if slices.Contains(rollbackRefusals, refused) {
			l.rollingBack = true
		} else {
			if l.terminal != "" {
				l.kept = " terminal=" + l.terminal
			}
			if l.made || l.lost {
				l.kept += " worktree=" + l.wt
			}
			if l.kept != "" {
				l.kept += " (worker-start may have dispatched a worker there)"
			}
		}
		fenced := ""
		if refused == refusalFenced {
			handle := l.Env("ORCA_TERMINAL_HANDLE")
			if handle == "" {
				handle = "unset"
			}
			fenced = "; terminal " + handle + " is not the Run's coordinator: " + l.rebind
		}
		named := ""
		if l.terminal != "" {
			named = " terminal=" + l.terminal
		}
		if l.startTries > 1 {
			named += fmt.Sprintf(" after %d tries", l.startTries)
		}
		return "", l.fail("worker-start%s: %s%s", named, orcaError(stdout, stderr), fenced)
	}
	receipt = readReceipt(l.savedFile(".json"))
	if l.agent != agentClaude && l.terminal != "" && receipt.Result.FailedStage == "agent_readiness" {
		screen := l.screenText()
		if strings.Contains(screen, updateAvailable) || strings.Contains(screen, skipUntilNext) {
			return "", l.fail("agent_readiness terminal=%s update prompt: '%s'", l.terminal, cutRunes(squeeze(screen), 300))
		}
		if _, code := l.tool("orca", "terminal", "send", "--terminal", l.terminal, "--text", l.spec, "--enter", "--json"); code != 0 {
			return "", l.fail("spec send terminal=%s after agent_readiness timeout", l.terminal)
		}
		return fmt.Sprintf("%s unsupervised task=%s dispatch=%s terminal=%s worktree=%s", l.lane, receipt.Result.TaskID, receipt.Result.DispatchID, l.terminal, l.wt), nil
	}
	switch receipt.Result.State {
	case "ready":
	case "outcome_unknown":
		started, err := l.turnStarted(receipt.Result.DispatchID)
		if err != nil {
			return "", err
		}
		if !started {
			return "", l.fail("worker-start outcome_unknown dispatch=%s terminal=%s worktree=%s: worker-show shows no turn started %ds after worker-start; Orca still supervises the dispatch, so run orca orchestration worker-abandon --dispatch %s --json before relaunching", receipt.Result.DispatchID, l.terminal, l.wt, l.boot, receipt.Result.DispatchID)
		}
	default:
		if l.startCode != 0 {
			return "", l.fail("worker-start terminal=%s worktree=%s: %s", l.terminal, l.wt, orcaError(l.savedFile(".json"), l.savedFile(".worker.err")))
		}
		return "", l.fail("worker-start state=%s terminal=%s worktree=%s", receipt.Result.State, l.terminal, l.wt)
	}
	if err := l.awaitBypass(); err != nil {
		return "", err
	}
	l.postSpawned()
	return fmt.Sprintf("%s ready task=%s dispatch=%s terminal=%s worktree=%s", l.lane, receipt.Result.TaskID, receipt.Result.DispatchID, l.terminal, l.wt), nil
}

func (l *launch) awaitBypass() error {
	if l.agent != agentClaude {
		return nil
	}
	for attempt := 1; ; attempt++ {
		if strings.Contains(l.screenText(), bypassOn) {
			return nil
		}
		if attempt >= bypassReads {
			return l.fail("terminal=%s does not show bypass permissions on; shift-tab it before the worker opens a plan", l.terminal)
		}
		if err := l.pause("terminal " + l.terminal + " does not show bypass permissions on yet"); err != nil {
			return err
		}
	}
}

func (l *launch) postSpawned() {
	if l.drive == "" {
		return
	}
	warn := func() {
		fmt.Fprintf(l.Stderr, "%s: the SPAWNED record did not post, so the lane supervisor will not keep this lane alive\n", l.lane)
	}
	entry := namedDrive(l.Env, l.drive)
	if entry == nil {
		warn()
		return
	}
	attrs := map[string]string{"brief": l.brief, "model": l.model, "effort": l.effort, "placement": "orca"}
	text := ""
	if raw, err := os.ReadFile(l.brief); err == nil {
		text = string(raw)
	}
	if role := l.Env("LAUNCH_ROLE"); role != "" {
		attrs["role"] = role
	} else if match := briefRole.FindStringSubmatch(text); match != nil {
		attrs["role"] = match[1]
	}
	if match := briefReplaces.FindStringSubmatch(text); match != nil {
		attrs["successor-of"] = match[1]
	}
	by := l.Env("CLAUDE_LONG_RUNNING_LANE")
	if by == "" {
		by = coordinatorLane
	}
	_, err := l.Bus.Post(l.ctx, client.Draft{
		Drive:    entry.Bus(),
		Lane:     by,
		Kind:     "state",
		Topic:    "spawn:" + l.lane,
		LaneRefs: []string{l.lane},
		Attrs:    attrs,
		Text:     "SPAWNED " + l.lane + " " + l.brief,
	})
	if err != nil {
		warn()
	}
}
