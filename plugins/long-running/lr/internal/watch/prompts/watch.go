package prompts

import (
	"context"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"syscall"
	"time"
	"unicode/utf8"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/watch/screen"
)

const (
	Lane = "prompt-watch"

	runner      = "desk-runner"
	root        = "root"
	owner       = "owner"
	supervised  = "supervisor"
	coordinator = "coordinator"
	roleDesk    = "desk"
	roleWorker  = "worker"
	roleWorkers = "workers"
	roleRuntime = "runtime"
	roleWatch   = "watch"

	codeRuntimeUnavailable = "runtime_unavailable"
	codeRemoteUnavailable  = "remote_runtime_unavailable"
	codeTimeout            = "timeout"
	codeUnparseable        = "unparseable"
	terminalEnv            = "ORCA_TERMINAL_HANDLE"
	turnStarted            = "turn_started"
	inputAccepted          = "input_accepted"

	orcaTimeout   = 20 * time.Second
	showWorkers   = 8
	unseenPolls   = 3
	supervisorMax = 5 * time.Minute
	stillOpen     = 10 * time.Minute
	idleWaitMS    = "1000"
	submitSeconds = "5"
	textLimit     = 400
	episodeChars  = 12
	detailChars   = 200
	ownerOnly     = 0o600
	ownerOnlyDir  = 0o700
)

var (
	ErrHeld = errors.New("another prompt watch holds the lock")

	waitsOn = map[State]string{Approval: "an approval dialog", Question: "a question dialog"}
	reached = map[string]string{turnStarted: "its turn started", inputAccepted: "Orca accepted it and saw no turn start"}
)

type Notice struct {
	Kind     string
	To       string
	Topic    string
	Text     string
	Resolves int64
}

type Watch struct {
	Runner shell.Runner
	Post   func(context.Context, Notice) (int64, error)
	Now    func() time.Time
	Sleep  func(time.Duration)
	Reshow time.Duration

	receipts  receiptCache
	successor *os.File
}

type orcaError struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

type reply struct {
	OK     bool            `json:"ok"`
	Result json.RawMessage `json:"result"`
	Error  *orcaError      `json:"error"`
}

type agentWait struct {
	Source string `json:"source"`
	Since  *int64 `json:"since"`
}

type subject struct {
	key         string
	role        string
	name        string
	terminal    string
	environment string
	dispatch    string
	failure     *orcaError
}

type seen struct {
	subject   subject
	state     State
	detail    string
	since     *int64
	asked     string
	screen    *screen.Terminal
	title     string
	countdown string
	code      string
	reused    bool
}

func (w *Watch) orca(ctx context.Context, environment string, env []string, argv ...string) reply {
	full := append([]string{"orca"}, argv...)
	if argv[0] == "orchestration" {
		full = append(full, "--full")
	}
	if environment != "" {
		full = append(full, "--environment", environment)
	}
	result, _ := w.Runner.Run(ctx, shell.Command{Argv: append(full, "--json"), Env: env, Timeout: orcaTimeout})
	var parsed reply
	if err := json.Unmarshal([]byte(result.Stdout), &parsed); err != nil {
		code := codeUnparseable
		if result.Code == shell.CodeTimedOut {
			code = codeTimeout
		}
		message := strings.TrimSpace(result.Stdout)
		if message == "" {
			message = strings.TrimSpace(result.Stderr)
		}
		return reply{Error: &orcaError{code, message}}
	}
	if !parsed.OK && parsed.Error == nil {
		parsed.Error = &orcaError{codeUnparseable, strings.TrimSpace(result.Stdout)}
	}
	return parsed
}

func unseenState(e *orcaError) (State, string) {
	state := Unknown
	if e.Code == codeRemoteUnavailable {
		state = Unreachable
	}
	detail := e.Code + ": " + strings.Join(strings.Fields(e.Message), " ")
	if runes := []rune(detail); len(runes) > detailChars {
		detail = string(runes[:detailChars])
	}
	return state, detail
}

func unread(s subject, e *orcaError, since *int64) seen {
	state, detail := unseenState(e)
	return seen{subject: s, state: state, detail: detail, since: since, code: e.Code}
}

func unreadable(s subject, why error) seen {
	return seen{subject: s, state: Unknown, detail: fmt.Sprintf("unreadable Orca reply (%v)", why)}
}

func episodeOf(s seen) string {
	sum := sha256.Sum256([]byte(s.subject.key + "|" + string(s.state) + "|" + s.asked))
	return hex.EncodeToString(sum[:])[:episodeChars]
}

func readCommand(s subject) string {
	command := "orca terminal read --terminal " + s.terminal + " --screen"
	if s.environment != "" {
		command += " --environment " + s.environment
	}
	return command
}

func answerCommand(s seen, by string) string {
	target := "--terminal " + s.subject.terminal
	if s.subject.environment != "" {
		target += " --environment " + s.subject.environment
	}
	if s.subject.dispatch != "" {
		target = "--dispatch " + s.subject.dispatch
	}
	return fmt.Sprintf("lr watch prompts answer %s --episode %s --by %s yes|no", target, episodeOf(s), by)
}

func howToAnswer(s seen, by string) string {
	if s.state == Approval {
		return "Answer: " + answerCommand(s, by) + "."
	}
	return "The watch answers nothing."
}

type listedWorker struct {
	DispatchID string `json:"dispatchId"`
	RunID      string `json:"runId"`
	Terminal   string `json:"agentTerminalHandle"`
}

type workerList struct {
	Workers []listedWorker `json:"workers"`
	Page    struct {
		HasMore    bool   `json:"hasMore"`
		NextCursor string `json:"nextCursor"`
	} `json:"page"`
	Scope struct {
		Run    string `json:"run"`
		Source string `json:"source"`
	} `json:"scope"`
}

func listFailed(failure *orcaError) []subject {
	return []subject{{key: roleWorkers, role: roleWorkers, name: "task-list", failure: failure}}
}

func (w *Watch) workers(ctx context.Context, e Entry, rows map[string]*Row) ([]subject, error) {
	run := e.OrcaRun
	if run == "" && e.RootTerminal == "" {
		return nil, nil
	}
	if run == "" {
		current := w.orca(ctx, "", []string{terminalEnv + "=" + e.RootTerminal}, "orchestration", "run-current")
		if !current.OK {
			return listFailed(current.Error), nil
		}
		var bound struct {
			Run *struct {
				ID string `json:"id"`
			} `json:"run"`
		}
		if err := json.Unmarshal(current.Result, &bound); err != nil {
			return listFailed(&orcaError{codeUnparseable, err.Error()}), nil
		}
		if bound.Run == nil {
			return nil, nil
		}
		run = bound.Run.ID
	}
	listed := w.orca(ctx, "", nil, "orchestration", "task-list", "--run", run, "--status", "dispatched", "--brief")
	if !listed.OK {
		return listFailed(listed.Error), nil
	}
	var result struct {
		Tasks []struct {
			Dispatch string `json:"dispatch_id"`
		} `json:"tasks"`
	}
	if err := json.Unmarshal(listed.Result, &result); err != nil {
		return listFailed(&orcaError{codeUnparseable, err.Error()}), nil
	}
	dispatches := make([]string, 0, len(result.Tasks))
	for _, task := range result.Tasks {
		dispatches = append(dispatches, task.Dispatch)
	}
	return w.namedWorkers(dispatches, run, rows)
}

func (w *Watch) namedWorkers(dispatches []string, run string, rows map[string]*Row) ([]subject, error) {
	var lanes map[string]string
	found := make([]subject, 0, len(dispatches))
	for _, dispatch := range dispatches {
		key := "dispatch:" + dispatch
		name := dispatch
		if row, known := rows[key]; known && row.Name != "" {
			name = row.Name
		}
		if name == dispatch {
			if lanes == nil {
				read, err := w.receipts.lanes(run)
				if err != nil {
					return nil, err
				}
				lanes = read
			}
			if lane, named := lanes[dispatch]; named {
				name = lane
			}
		}
		found = append(found, subject{key: key, role: roleWorker, name: name, dispatch: dispatch})
	}
	return found, nil
}

func (w *Watch) subjects(ctx context.Context, e Entry, rows map[string]*Row) ([]subject, error) {
	var found []subject
	if e.RootTerminal != "" {
		found = append(found, subject{key: e.RootTerminal, role: coordinator, name: coordinator, terminal: e.RootTerminal})
	}
	for _, name := range e.deskNames() {
		desk := e.Desks[name]
		role := roleDesk
		if desk.Supervisor {
			role = supervised
		}
		found = append(found, subject{key: desk.Terminal, role: role, name: name, terminal: desk.Terminal, environment: desk.Environment})
	}
	workers, err := w.workers(ctx, e, rows)
	if err != nil {
		return nil, err
	}
	return append(found, workers...), nil
}

func (w *Watch) observe(ctx context.Context, s subject, always bool) seen {
	if s.failure != nil {
		return unread(s, s.failure, nil)
	}
	wait, s, early, done := w.waitOf(ctx, s)
	if done {
		return early
	}
	var since *int64
	if wait != nil {
		since = wait.Since
	}
	if s.role == roleWorker && s.environment == "" && wait == nil && !always {
		return seen{subject: s, state: Clear}
	}
	read := w.orca(ctx, s.environment, nil, "terminal", "read", "--terminal", s.terminal, "--screen")
	if !read.OK {
		return unread(s, read.Error, since)
	}
	var result struct {
		Terminal *screen.Terminal `json:"terminal"`
	}
	if err := json.Unmarshal(read.Result, &result); err != nil {
		return unreadable(s, err)
	}
	if result.Terminal == nil {
		return unreadable(s, errors.New("no terminal"))
	}
	shown := result.Terminal
	if shown.Source != "screen" {
		source := shown.Source
		if source == "" {
			source = "absent"
		}
		return seen{subject: s, state: Unknown, detail: fmt.Sprintf("no rendered screen (source %s)", source), since: since}
	}
	if found, open := screen.FindDialog(*shown); open {
		return seen{subject: s, state: State(found.Kind), since: since, asked: found.Asked, screen: shown, title: found.Title(), countdown: found.Countdown}
	}
	if wait == nil {
		return seen{subject: s, state: Clear}
	}
	source := wait.Source
	if source == "" {
		source = "unknown"
	}
	if _, box := screen.Composer(shown.Tail); box {
		return seen{subject: s, state: Stale, detail: fmt.Sprintf("agentWait via %s is set and the input box is back", source), since: since}
	}
	return seen{subject: s, state: Unknown, detail: fmt.Sprintf("agentWait via %s is set and the screen shows neither a known dialog nor an input box", source), since: since, screen: shown}
}

func (w *Watch) waitOf(ctx context.Context, s subject) (*agentWait, subject, seen, bool) {
	if s.dispatch == "" {
		shown := w.orca(ctx, s.environment, nil, "terminal", "show", "--terminal", s.terminal)
		if !shown.OK {
			return nil, s, unread(s, shown.Error, nil), true
		}
		var result struct {
			Terminal *struct {
				AgentWait *agentWait `json:"agentWait"`
			} `json:"terminal"`
		}
		if err := json.Unmarshal(shown.Result, &result); err != nil {
			return nil, s, unreadable(s, err), true
		}
		if result.Terminal == nil {
			return nil, s, unreadable(s, errors.New("no terminal")), true
		}
		return result.Terminal.AgentWait, s, seen{}, false
	}
	shown := w.orca(ctx, "", nil, "orchestration", "worker-show", "--dispatch", s.dispatch)
	if !shown.OK {
		return nil, s, unread(s, shown.Error, nil), true
	}
	var result struct {
		Terminal *struct {
			Handle string `json:"handle"`
		} `json:"terminal"`
		Server *struct {
			Name string `json:"name"`
		} `json:"server"`
		Observation *struct {
			AgentWait *agentWait `json:"agentWait"`
		} `json:"observation"`
	}
	if err := json.Unmarshal(shown.Result, &result); err != nil {
		return nil, s, unreadable(s, err), true
	}
	if result.Terminal == nil || result.Terminal.Handle == "" {
		return nil, s, seen{subject: s, state: Unknown, detail: "worker-show names no terminal"}, true
	}
	s.terminal = result.Terminal.Handle
	if result.Server != nil {
		s.environment = result.Server.Name
	}
	if result.Observation == nil {
		return nil, s, unreadable(s, errors.New("no observation")), true
	}
	return result.Observation.AgentWait, s, seen{}, false
}

func (w *Watch) capture(e Entry, name string, s seen) (string, error) {
	path := filepath.Join(e.dir(), "screens", name+".json")
	if err := os.MkdirAll(filepath.Dir(path), ownerOnlyDir); err != nil {
		return "", err
	}
	data, err := encode(map[string]any{
		"at": stamp(w.Now()), "role": s.subject.role, "name": s.subject.name, "terminal": s.subject.terminal,
		"environment": s.subject.environment, "state": s.state, "since": s.since, "asked": s.asked, "tail": s.screen.Tail,
	})
	if err != nil {
		return "", err
	}
	return path, os.WriteFile(path, data, ownerOnly)
}

func (w *Watch) post(ctx context.Context, e Entry, kind, to, name, text string, resolves int64) (int64, error) {
	if utf8.RuneCountInString(text) > textLimit {
		sum := sha256.Sum256([]byte(text))
		body := filepath.Join(e.dir(), "notices", hex.EncodeToString(sum[:])[:episodeChars]+".txt")
		if err := os.MkdirAll(filepath.Dir(body), ownerOnlyDir); err != nil {
			return 0, err
		}
		if err := os.WriteFile(body, []byte(text+"\n"), ownerOnly); err != nil {
			return 0, err
		}
		whole := " Whole line: " + body
		text = string([]rune(text)[:max(0, textLimit-1-utf8.RuneCountInString(whole))]) + "…" + whole
	}
	seq, refused := w.Post(ctx, Notice{kind, to, "prompt:" + name, text, resolves})
	if refused != nil {
		return 0, nil
	}
	return seq, nil
}

func (w *Watch) card(ctx context.Context, e Entry, status, title, body string) bool {
	card, err := json.Marshal(map[string]string{"status": status, "title": title, "body": body, "drive": e.Bus()})
	if err != nil {
		return false
	}
	_, err = w.Runner.Run(ctx, shell.Command{Argv: []string{"cc-slack", "dm-status", "--session", e.Sessions[len(e.Sessions)-1], "--card", string(card)}, Timeout: orcaTimeout})
	return err == nil
}

func (w *Watch) closeRow(ctx context.Context, e Entry, row *Row, why string) error {
	cleared := fmt.Sprintf("CLEARED %s (%s) terminal=%s: %s", row.Name, row.Role, row.Terminal, why)
	left := map[string]Alert{}
	names := make([]string, 0, len(row.Alerts))
	for who := range row.Alerts {
		names = append(names, who)
	}
	slices.Sort(names)
	for _, who := range names {
		alert := row.Alerts[who]
		if who == owner {
			if !w.card(ctx, e, "info", fmt.Sprintf("The %s terminal no longer waits on a prompt", row.Name), cleared) {
				left[who] = alert
			}
			continue
		}
		seq, err := w.post(ctx, e, "unblock", "", row.Name, cleared, alert.Seq)
		if err != nil {
			return err
		}
		if seq == 0 {
			left[who] = alert
		}
	}
	row.Alerts = left
	if len(left) == 0 {
		row.Episode, row.ToldAt, row.Reminded, row.Wake, row.Capture, row.SupervisorInput = "", "", 0, nil, "", ""
	}
	return nil
}

func (w *Watch) wake(ctx context.Context, supervisor subject, text string) (Wake, string) {
	waited := w.orca(ctx, supervisor.environment, nil, "terminal", "wait", "--terminal", supervisor.terminal, "--for", "tui-idle", "--timeout-ms", idleWaitMS)
	read := w.orca(ctx, supervisor.environment, nil, "terminal", "read", "--terminal", supervisor.terminal, "--screen")
	shows := string(Unknown)
	if read.OK {
		var result struct {
			Terminal screen.Terminal `json:"terminal"`
		}
		if json.Unmarshal(read.Result, &result) == nil {
			if input := screen.InputState(result.Terminal); input != screen.NoInput {
				shows = string(input)
			}
		}
	} else {
		state, _ := unseenState(read.Error)
		shows = string(state)
	}
	var satisfied struct {
		Wait struct {
			Satisfied bool `json:"satisfied"`
		} `json:"wait"`
	}
	if waited.OK {
		_ = json.Unmarshal(waited.Result, &satisfied)
	}
	if shows != string(screen.Idle) {
		return Wake{}, shows
	}
	if !satisfied.Wait.Satisfied {
		return Wake{}, string(screen.Busy)
	}
	sent := w.orca(ctx, supervisor.environment, nil, "terminal", "send", "--terminal", supervisor.terminal, "--text", text, "--enter", "--wait-submit", submitSeconds)
	at := stamp(w.Now())
	if !sent.OK {
		_, detail := unseenState(sent.Error)
		return Wake{At: at, Error: detail}, ""
	}
	var result struct {
		Send struct {
			Accepted      bool   `json:"accepted"`
			RefusedReason string `json:"refusedReason"`
			Prompt        struct {
				RequestID string   `json:"requestId"`
				Stages    []string `json:"stages"`
			} `json:"prompt"`
		} `json:"send"`
	}
	if err := json.Unmarshal(sent.Result, &result); err != nil {
		return Wake{At: at, Error: codeUnparseable + ": " + err.Error()}, ""
	}
	if !result.Send.Accepted {
		reason := result.Send.RefusedReason
		if reason == "" {
			reason = "Orca gave no reason"
		}
		return Wake{At: at, Error: "input_refused: " + reason}, ""
	}
	stage := inputAccepted
	if slices.Contains(result.Send.Prompt.Stages, turnStarted) {
		stage = turnStarted
	}
	return Wake{At: at, Request: result.Send.Prompt.RequestID, Stages: result.Send.Prompt.Stages, Reached: stage}, ""
}

func promptText(row *Row, s seen, tail string) string {
	deny := ""
	if s.countdown != "" {
		deny = "; auto-denies in " + s.countdown
	}
	return fmt.Sprintf("PROMPT %s (%s) waits on %s (%s%s). %s Screen: %s. Read: %s.", s.subject.name, s.subject.role, waitsOn[s.state], s.title, deny, tail, row.Capture, readCommand(s.subject))
}

func (w *Watch) alertWorker(ctx context.Context, e Entry, row *Row, s seen) error {
	if _, told := row.Alerts[root]; !told {
		seq, err := w.post(ctx, e, "blocker", root, s.subject.name, promptText(row, s, howToAnswer(s, root)), 0)
		if err != nil {
			return err
		}
		if seq != 0 {
			row.Alerts[root] = Alert{Seq: seq}
			row.ToldAt = stamp(w.Now())
		}
	}
	if row.ToldAt == "" {
		row.ToldAt = stamp(w.Now())
	}
	alert, told := row.Alerts[root]
	if !told || row.Reminded != 0 || w.Now().Sub(parseStamp(row.ToldAt)) < stillOpen {
		return nil
	}
	text := promptText(row, s, fmt.Sprintf("Still open %dm after #%d. %s", int(stillOpen.Minutes()), alert.Seq, howToAnswer(s, root)))
	seq, err := w.post(ctx, e, "report", root, s.subject.name, text, 0)
	if err != nil {
		return err
	}
	row.Reminded = seq
	return nil
}

func (w *Watch) alert(ctx context.Context, e Entry, row *Row, s seen, supervisor *seen) error {
	if s.subject.role != coordinator {
		return w.alertWorker(ctx, e, row, s)
	}
	if _, carded := row.Alerts[owner]; carded || s.state == Question {
		return nil
	}
	ready := supervisor != nil && (supervisor.state == Clear || supervisor.state == Stale)
	by := supervised
	if supervisor != nil {
		by = supervisor.subject.name
	}
	if _, told := row.Alerts[supervised]; !told && ready {
		text := promptText(row, s, "Answer only within what the owner already authorized, else ask the owner. "+howToAnswer(s, by))
		seq, err := w.post(ctx, e, "blocker", by, s.subject.name, text, 0)
		if err != nil {
			return err
		}
		if seq == 0 {
			return nil
		}
		row.Alerts[supervised] = Alert{Seq: seq}
		row.ToldAt = stamp(w.Now())
	}
	_, told := row.Alerts[supervised]
	if told && ready && row.Wake == nil {
		text := promptText(row, s, "Answer only within what the owner already authorized, else ask the owner. "+howToAnswer(s, by))
		woken, blocked := w.wake(ctx, supervisor.subject, text)
		if blocked != "" {
			row.SupervisorInput = blocked
		} else {
			row.Wake = &woken
			row.SupervisorInput = ""
		}
	}
	failed := ""
	if row.Wake != nil {
		failed = row.Wake.Error
	}
	if told && ready && failed == "" && w.Now().Sub(parseStamp(row.ToldAt)) < supervisorMax {
		return nil
	}
	minutes := int(supervisorMax.Minutes())
	var why string
	switch {
	case supervisor == nil:
		why = "no supervisor desk is registered"
	case !ready:
		why = fmt.Sprintf("supervisor %s is %s", supervisor.subject.name, supervisor.state)
	case failed != "":
		why = fmt.Sprintf("the wake to supervisor %s failed with %s", supervisor.subject.name, failed)
	case row.Wake == nil:
		why = fmt.Sprintf("supervisor %s stayed %s for %dm", supervisor.subject.name, row.SupervisorInput, minutes)
	default:
		why = fmt.Sprintf("supervisor %s got the line %dm ago, %s, and it is still open", supervisor.subject.name, minutes, reached[row.Wake.Reached])
	}
	text := promptText(row, s, fmt.Sprintf("It froze the coordinator; %s. Answer it in that terminal.", why))
	if e.SlackCards && w.card(ctx, e, "needs_you", fmt.Sprintf("The %s terminal waits on %s", s.subject.name, waitsOn[s.state]), text) {
		row.Alerts[owner] = Alert{Episode: row.Episode}
	}
	return nil
}

func supervisorNote(row *Row) string {
	switch {
	case row.Wake != nil && row.Wake.Reached != "":
		return "; supervisor got one line and " + reached[row.Wake.Reached]
	case row.Wake != nil:
		return "; the supervisor's wake failed with " + row.Wake.Error
	case row.SupervisorInput != "":
		return "; supervisor is " + row.SupervisorInput
	}
	return ""
}

func (w *Watch) settleUnseen(ctx context.Context, e Entry, row *Row, s seen, quiet bool) error {
	row.Readable = 0
	if row.UnseenState == s.state {
		row.Unseen++
	} else {
		row.Unseen = 1
	}
	row.UnseenState = s.state
	if s.screen != nil {
		if row.Unseen == 1 || row.UnreadCapture == "" {
			sum := sha256.Sum256([]byte(s.subject.key))
			path, err := w.capture(e, "unknown-"+hex.EncodeToString(sum[:])[:episodeChars], s)
			if err != nil {
				return err
			}
			row.UnreadCapture = path
		}
		row.Detail = fmt.Sprintf("%s; screen saved at %s", s.detail, row.UnreadCapture)
	}
	if row.Unseen < unseenPolls || row.Reported == s.state || quiet {
		return nil
	}
	text := fmt.Sprintf("%s %s (%s) %s: %s; unread for %d polls, so whether it waits on a prompt is not known", strings.ToUpper(string(s.state)), s.subject.name, s.subject.role, where(row), row.Detail, row.Unseen)
	seq, err := w.post(ctx, e, "report", runner, s.subject.name, text, 0)
	if err != nil {
		return err
	}
	if seq != 0 {
		row.Reported = s.state
	}
	return nil
}

func (w *Watch) settle(ctx context.Context, e Entry, row *Row, s seen, supervisor *seen, quiet bool) error {
	if row.State != s.state {
		row.StateAt = stamp(w.Now())
	}
	row.Role, row.Name, row.Terminal, row.Environment = s.subject.role, s.subject.name, s.subject.terminal, s.subject.environment
	row.State, row.Detail, row.Since = s.state, s.detail, s.since
	if row.Alerts == nil {
		row.Alerts = map[string]Alert{}
	}
	if s.state.unseen() {
		return w.settleUnseen(ctx, e, row, s, quiet)
	}
	row.Unseen, row.UnseenState, row.UnreadCapture = 0, "", ""
	if row.Reported != "" {
		row.Readable++
		if row.Readable >= unseenPolls {
			text := fmt.Sprintf("READABLE %s (%s) %s: read for %d polls in a row since the watch reported it %s", s.subject.name, s.subject.role, where(row), row.Readable, row.Reported)
			seq, err := w.post(ctx, e, "report", runner, s.subject.name, text, 0)
			if err != nil {
				return err
			}
			if seq != 0 {
				row.Reported, row.Readable = "", 0
			}
		}
	}
	if !s.state.prompt() {
		return w.closeRow(ctx, e, row, "the prompt left the screen")
	}
	episode := episodeOf(s)
	if row.Episode != episode {
		if err := w.closeRow(ctx, e, row, "the prompt left the screen"); err != nil {
			return err
		}
		if len(row.Alerts) > 0 {
			return nil
		}
		row.Episode = episode
	}
	if row.Capture == "" {
		path, err := w.capture(e, episode, s)
		if err != nil {
			return err
		}
		row.Capture = path
	}
	if err := w.alert(ctx, e, row, s, supervisor); err != nil {
		return err
	}
	row.Detail = "screen saved at " + row.Capture + supervisorNote(row)
	return nil
}

func (w *Watch) unchanged(s subject, row *Row) bool {
	return w.Reshow > 0 && s.role == roleWorker && row != nil && row.State == Clear && row.Reported == "" &&
		len(row.Alerts) == 0 && w.Now().Sub(parseStamp(row.ShownAt)) < w.reshowOf(s.key)
}

func (w *Watch) reshowOf(key string) time.Duration {
	sum := sha256.Sum256([]byte(key))
	spread := time.Duration(binary.BigEndian.Uint32(sum[:4])) % (w.Reshow / 2)
	return w.Reshow*3/4 + spread
}

func (w *Watch) observeAll(ctx context.Context, subjects []subject, rows map[string]*Row) []seen {
	found := make([]seen, len(subjects))
	limit := make(chan struct{}, showWorkers)
	var group sync.WaitGroup
	for i, s := range subjects {
		if row := rows[s.key]; w.unchanged(s, row) {
			s.terminal, s.environment = row.Terminal, row.Environment
			found[i] = seen{subject: s, state: Clear, reused: true}
			continue
		}
		group.Go(func() {
			limit <- struct{}{}
			defer func() { <-limit }()
			found[i] = w.observe(ctx, s, false)
		})
	}
	group.Wait()
	return found
}

func downCodes(found []seen) []string {
	if len(found) == 0 {
		return nil
	}
	codes := []string{}
	for _, s := range found {
		if s.code != codeRuntimeUnavailable && s.code != codeTimeout {
			return nil
		}
		if !slices.Contains(codes, s.code) {
			codes = append(codes, s.code)
		}
	}
	slices.Sort(codes)
	return codes
}

func workerNames(rows map[string]*Row) []string {
	names := []string{}
	for _, row := range rows {
		if row.Role == roleWorker {
			names = append(names, row.Name)
		}
	}
	slices.Sort(names)
	return names
}

func (w *Watch) settleRuntime(ctx context.Context, e Entry, rows, fresh map[string]*Row, down, listed []string, supervisor *seen) error {
	row, known := rows[roleRuntime]
	if !known {
		row = &Row{}
	}
	delete(rows, roleRuntime)
	orca := subject{key: roleRuntime, role: roleRuntime, name: "orca"}
	if len(down) > 0 && len(listed) > 0 {
		detail := fmt.Sprintf("Orca answers no watched terminal (%s); %s were in progress at the last task-list and may wait on a prompt", strings.Join(down, ", "), strings.Join(listed, ", "))
		fresh[roleRuntime] = row
		return w.settle(ctx, e, row, seen{subject: orca, state: Unknown, detail: detail}, supervisor, false)
	}
	if row.Reported == "" {
		return nil
	}
	if err := w.settle(ctx, e, row, seen{subject: orca, state: Clear}, supervisor, false); err != nil {
		return err
	}
	if row.Reported != "" {
		fresh[roleRuntime] = row
	}
	return nil
}

func (w *Watch) settleGone(ctx context.Context, e Entry, rows, fresh map[string]*Row, unlisted, supervisor *seen) error {
	for key, row := range rows {
		switch {
		case unlisted != nil && row.Role == roleWorker:
			if !row.State.unseen() {
				row.Detail = fmt.Sprintf("last read %s; task-list failed with %s", row.State, unlisted.detail)
				row.State, row.StateAt = unlisted.state, stamp(w.Now())
			}
			fresh[key] = row
		case row.Role == roleWorkers && row.Reported != "":
			if err := w.settle(ctx, e, row, seen{subject: subject{key: key, role: roleWorkers, name: row.Name}, state: Clear}, supervisor, false); err != nil {
				return err
			}
			if row.Reported != "" {
				fresh[key] = row
			}
		default:
			if err := w.closeRow(ctx, e, row, "the terminal left the watch"); err != nil {
				return err
			}
			if len(row.Alerts) > 0 {
				fresh[key] = row
			}
		}
	}
	return nil
}

func (w *Watch) poll(ctx context.Context, e Entry) error {
	state, err := loadState(e.statePath())
	if err != nil {
		return err
	}
	rows := state.Subjects
	listed := workerNames(rows)
	subjects, err := w.subjects(ctx, e, rows)
	if err != nil {
		return err
	}
	found := w.observeAll(ctx, subjects, rows)
	down := downCodes(found)
	var supervisor, unlisted *seen
	for i := range found {
		if supervisor == nil && found[i].subject.role == supervised {
			supervisor = &found[i]
		}
		if unlisted == nil && found[i].subject.role == roleWorkers {
			unlisted = &found[i]
		}
	}
	fresh := map[string]*Row{}
	for _, s := range found {
		row, known := rows[s.subject.key]
		if !known {
			row = &Row{}
		}
		delete(rows, s.subject.key)
		fresh[s.subject.key] = row
		if !s.reused {
			row.ShownAt = stamp(w.Now())
		}
		if err := w.settle(ctx, e, row, s, supervisor, len(down) > 0); err != nil {
			return err
		}
	}
	if err := w.settleRuntime(ctx, e, rows, fresh, down, listed, supervisor); err != nil {
		return err
	}
	if err := w.settleGone(ctx, e, rows, fresh, unlisted, supervisor); err != nil {
		return err
	}
	return writeAtomic(e.statePath(), saved{Drive: e.Drive, At: stamp(w.Now()), PID: os.Getpid(), Subjects: fresh})
}

func (w *Watch) Check(ctx context.Context, e Entry) error {
	if err := os.MkdirAll(e.dir(), ownerOnlyDir); err != nil {
		return err
	}
	lock, err := os.OpenFile(filepath.Join(e.dir(), "lock"), os.O_CREATE|os.O_RDWR, ownerOnly)
	if err != nil {
		return err
	}
	defer lock.Close()
	if err := flock(lock); err != nil {
		if errors.Is(err, syscall.EWOULDBLOCK) {
			return w.succeed(e)
		}
		return err
	}
	if w.successor != nil {
		_ = w.successor.Close()
		w.successor = nil
	}
	return w.poll(ctx, e)
}

func (w *Watch) succeed(e Entry) error {
	if w.successor != nil {
		return ErrHeld
	}
	queue, err := os.OpenFile(filepath.Join(e.dir(), "successor"), os.O_CREATE|os.O_RDWR, ownerOnly)
	if err != nil {
		return err
	}
	if err := flock(queue); err != nil {
		_ = queue.Close()
		if errors.Is(err, syscall.EWOULDBLOCK) {
			return ErrHeld
		}
		return err
	}
	w.successor = queue
	return ErrHeld
}

func flock(file *os.File) error {
	return syscall.Flock(int(file.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
}
