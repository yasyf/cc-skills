package lanes

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	readTimeout       = 60 * time.Second
	tailBytes         = 1_000_000
	measureEvery      = 5 * time.Minute
	openEvery         = 10 * time.Minute
	prBatch           = 50
	contextWindow     = 1_000_000
	outputReserve     = 20_000
	autocompactBuffer = 13_000
	rotatePercent     = 70
	wakeSender        = "long-running"
	adoptWindow       = 72 * time.Hour
)

var (
	unsafeName  = regexp.MustCompile(`[^a-zA-Z0-9_-]`)
	leadingHold = regexp.MustCompile(`(?i)^\W*hold\s+#(\d+)\b`)
)

type Bus interface {
	Post(ctx context.Context, d client.Draft) (client.Record, error)
	Tail(ctx context.Context, q client.Query) ([]client.Record, error)
	Open(ctx context.Context, drive string, kinds ...string) ([]client.Record, error)
	Who(ctx context.Context, drive string) ([]client.Peer, error)
}

type CCI struct{}

func (CCI) Post(ctx context.Context, d client.Draft) (client.Record, error) {
	return client.Post(ctx, d)
}

func (CCI) Tail(ctx context.Context, q client.Query) ([]client.Record, error) {
	return client.Tail(ctx, q)
}

func (CCI) Open(ctx context.Context, drive string, kinds ...string) ([]client.Record, error) {
	return client.Open(ctx, drive, kinds...)
}

func (CCI) Who(ctx context.Context, drive string) ([]client.Peer, error) {
	return client.Who(ctx, drive)
}

type Env struct {
	Bus    Bus
	Runner shell.Runner
	Config config.Config
	State  string
	Claude string
	Local  string
	Now    func() time.Time
}

func NewEnv(runner shell.Runner, cfg config.Config, state string, now func() time.Time) (Env, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return Env{}, err
	}
	claude := os.Getenv("CLAUDE_CONFIG_DIR")
	if claude == "" {
		claude = filepath.Join(home, ".claude")
	}
	local := os.Getenv("XDG_STATE_HOME")
	if local == "" {
		local = filepath.Join(home, ".local", "state")
	}
	return Env{Bus: CCI{}, Runner: runner, Config: cfg, State: state, Claude: claude, Local: local, Now: now}, nil
}

func (e Env) plugin() string { return filepath.Join(e.Claude, "long-running", "current") }

func (e Env) tool(name string) string { return filepath.Join(e.plugin(), "bin", name) }

func (e Env) receipts() string {
	return filepath.Join(e.Local, "long-running", "orca-launch", e.Config.OrcaRun)
}

func (e Env) Run(ctx context.Context, argv ...string) (string, error) {
	result, err := e.Runner.Run(ctx, shell.Command{Argv: argv, Timeout: readTimeout})
	if err != nil {
		return "", fmt.Errorf("%w: %w", ErrUnread, err)
	}
	return result.Stdout, nil
}

func recordOf(r client.Record) Record {
	refs := Refs{Lanes: r.Refs.Lanes, CCN: r.Refs.CCN}
	for _, pr := range r.Refs.PRs {
		refs.PRs = append(refs.PRs, PRRef(pr.Number))
	}
	return Record{
		Seq: r.Seq, Lane: r.Lane, Kind: string(r.Kind), At: r.At.UTC(), Text: r.Text, Topic: r.Topic, To: r.To,
		Re: r.Re, Resolves: r.Resolves, AlsoResolves: r.AlsoResolves, Refs: refs, Fields: Fields{Attrs: r.Fields.Attrs},
	}
}

func (e Env) Records(ctx context.Context) func(int64) ([]Record, error) {
	return func(since int64) ([]Record, error) {
		query := client.Query{Drive: e.Config.Drive, After: since}
		if since == 0 {
			query.Since = e.Now().Add(-adoptWindow)
		}
		rows, err := e.Bus.Tail(ctx, query)
		if err != nil {
			return nil, fmt.Errorf("%w: cci tail: %w", ErrUnread, err)
		}
		records := make([]Record, 0, len(rows))
		for _, row := range rows {
			records = append(records, recordOf(row))
		}
		return records, nil
	}
}

func (e Env) Who(ctx context.Context) func([]string) ([]Row, error) {
	return func(names []string) ([]Row, error) {
		peers, err := e.Bus.Who(ctx, e.Config.Drive)
		if err != nil {
			return nil, fmt.Errorf("%w: cci who: %w", ErrUnread, err)
		}
		rows := make([]Row, 0, len(peers))
		for _, peer := range peers {
			rows = append(rows, Row{Lane: peer.Lane, Session: peer.Session, Agent: peer.Agent, Live: peer.Live, SeenAt: peer.SeenAt, StoodDown: peer.StoodDown})
		}
		rows = Rostered(rows, filepath.Join(e.Claude, "teams"))
		if e.Config.OrcaRun == "" {
			return rows, nil
		}
		orca, err := e.orcaRows(ctx, names)
		if err != nil {
			return nil, err
		}
		return append(rows, orca...), nil
	}
}

func teamDir(teams, session string) string {
	return filepath.Join(teams, "session-"+session[:min(8, len(session))])
}

func Rostered(rows []Row, teams string) []Row {
	members := map[string]map[string]bool{}
	for _, row := range rows {
		if !row.Live || row.Session == "" || row.Agent != "" {
			continue
		}
		data, err := os.ReadFile(filepath.Join(teamDir(teams, row.Session), "config.json"))
		if err != nil {
			continue
		}
		var team struct {
			Members []struct {
				Name string `json:"name"`
			} `json:"members"`
		}
		if json.Unmarshal(data, &team) != nil {
			continue
		}
		names := map[string]bool{}
		for _, member := range team.Members {
			names[member.Name] = true
		}
		members[row.Session] = names
	}
	out := make([]Row, 0, len(rows))
	for _, row := range rows {
		if row.Agent != "" && members[row.Session][row.Lane] {
			row.Live, row.Roster = true, teamDir(teams, row.Session)
		}
		out = append(out, row)
	}
	return out
}

func dispatchOf(receipts, lane string) string {
	data, err := os.ReadFile(filepath.Join(receipts, lane+".json"))
	if err != nil {
		return ""
	}
	var receipt struct {
		Result struct {
			DispatchID string `json:"dispatchId"`
		} `json:"result"`
	}
	if json.Unmarshal(data, &receipt) != nil {
		return ""
	}
	return receipt.Result.DispatchID
}

func (e Env) orcaRows(ctx context.Context, names []string) ([]Row, error) {
	receipts := e.receipts()
	wanted := names
	if names == nil {
		terminals, err := filepath.Glob(filepath.Join(receipts, "*.terminal"))
		if err != nil {
			return nil, err
		}
		for _, path := range terminals {
			wanted = append(wanted, strings.TrimSuffix(filepath.Base(path), ".terminal"))
		}
	}
	dispatches := map[string]string{}
	for _, lane := range wanted {
		if dispatch := dispatchOf(receipts, lane); dispatch != "" {
			dispatches[lane] = dispatch
		}
	}
	if len(dispatches) == 0 {
		return nil, nil
	}
	statuses := map[string]string{}
	var cursor []string
	for {
		argv := append([]string{"orca", "orchestration", "worker-list", "--run", e.Config.OrcaRun, "--include-remote", "--limit", "100"}, cursor...)
		stdout, err := e.Run(ctx, append(argv, "--full", "--json")...)
		if err != nil {
			return nil, err
		}
		var page struct {
			OK     bool `json:"ok"`
			Error  any  `json:"error"`
			Result struct {
				Workers []struct {
					DispatchID     string `json:"dispatchId"`
					DispatchStatus string `json:"dispatchStatus"`
				} `json:"workers"`
				Page struct {
					NextCursor string `json:"nextCursor"`
				} `json:"page"`
			} `json:"result"`
		}
		if err := json.Unmarshal([]byte(stdout), &page); err != nil {
			return nil, fmt.Errorf("%w: orca worker-list: %w", ErrUnread, err)
		}
		if !page.OK {
			return nil, fmt.Errorf("%w: orca worker-list: %v", ErrUnread, page.Error)
		}
		for _, worker := range page.Result.Workers {
			statuses[worker.DispatchID] = worker.DispatchStatus
		}
		if page.Result.Page.NextCursor == "" {
			break
		}
		cursor = []string{"--cursor", page.Result.Page.NextCursor}
	}
	lanes := make([]string, 0, len(dispatches))
	for lane := range dispatches {
		lanes = append(lanes, lane)
	}
	slices.Sort(lanes)
	var rows []Row
	for _, lane := range lanes {
		status, known := statuses[dispatches[lane]]
		if !known {
			continue
		}
		live := status != "completed" && status != "failed"
		seen := e.Now().UTC()
		if !live {
			info, err := os.Stat(filepath.Join(receipts, lane+".json"))
			if err != nil {
				return nil, err
			}
			seen = info.ModTime().UTC()
		}
		rows = append(rows, Row{Lane: lane, Live: live, SeenAt: seen, Dispatch: dispatches[lane]})
	}
	return rows, nil
}

func taskOpen(tasks, lane string) bool {
	paths, err := filepath.Glob(filepath.Join(tasks, "*.json"))
	if err != nil {
		return false
	}
	for _, path := range paths {
		data, err := os.ReadFile(path)
		if err != nil {
			continue
		}
		var task struct {
			Status  string `json:"status"`
			Owner   string `json:"owner"`
			Subject string `json:"subject"`
		}
		if json.Unmarshal(data, &task) != nil {
			continue
		}
		if task.Status == "in_progress" && (task.Owner == lane || strings.HasPrefix(task.Subject, lane+":")) {
			return true
		}
	}
	return false
}

func appendInbox(inbox string, message map[string]any) (bool, error) {
	if err := os.MkdirAll(filepath.Dir(inbox), 0o755); err != nil {
		return false, err
	}
	lock := inbox + ".lock"
	if err := os.Mkdir(lock, 0o755); err != nil {
		if errors.Is(err, fs.ErrExist) {
			return false, nil
		}
		return false, err
	}
	defer func() { _ = os.Remove(lock) }()
	messages := []json.RawMessage{}
	data, err := os.ReadFile(inbox)
	if err != nil && !errors.Is(err, fs.ErrNotExist) {
		return false, err
	}
	if len(bytes.TrimSpace(data)) > 0 {
		if err := json.Unmarshal(data, &messages); err != nil {
			return false, fmt.Errorf("%s: %w", inbox, err)
		}
	}
	var encoded bytes.Buffer
	encoder := json.NewEncoder(&encoded)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(message); err != nil {
		return false, err
	}
	messages = append(messages, bytes.TrimSpace(encoded.Bytes()))
	var out bytes.Buffer
	encoder = json.NewEncoder(&out)
	encoder.SetEscapeHTML(false)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(messages); err != nil {
		return false, err
	}
	staged := fmt.Sprintf("%s.tmp.%s", inbox, randomHex(4))
	if err := os.WriteFile(staged, out.Bytes(), 0o644); err != nil {
		return false, err
	}
	return true, os.Rename(staged, inbox)
}

func randomHex(n int) string {
	raw := make([]byte, n)
	if _, err := rand.Read(raw); err != nil {
		panic(err)
	}
	return hex.EncodeToString(raw)
}

type Waker struct {
	Drive string
	Clock func() time.Time
	Tasks string
	woken map[string]time.Time
}

func (w *Waker) Wake(lane string, row Row, page map[string]any) (bool, error) {
	now := w.Clock()
	if page == nil {
		if last, found := w.woken[lane]; found && now.Sub(last) < StaleAfter {
			return false, nil
		}
		if !taskOpen(teamDir(w.Tasks, row.Session), lane) {
			return false, nil
		}
	}
	why := fmt.Sprintf("the lane supervisor has seen no record from you in %d minutes while your root task is in progress.", int(StaleAfter.Minutes()))
	if page != nil {
		why = pageText(page, "why")
	}
	message := map[string]any{
		"from":      wakeSender,
		"text":      fmt.Sprintf("WAKE %s: %s Read your mail with `cci tail --drive %s --reader %s --to %s`, then continue the work in hand from your ledger; do not restart your brief.", lane, why, w.Drive, lane, lane),
		"summary":   fmt.Sprintf("WAKE %s: continue the work in hand", lane),
		"timestamp": now.UTC().Format("2006-01-02T15:04:05.000Z"),
		"msgV":      1,
		"msg_id":    randomHex(16),
		"type":      "message",
		"read":      false,
	}
	written, err := appendInbox(filepath.Join(row.Roster, "inboxes", unsafeName.ReplaceAllString(lane, "-")+".json"), message)
	if err != nil || !written {
		return false, err
	}
	if w.woken == nil {
		w.woken = map[string]time.Time{}
	}
	w.woken[lane] = now
	return true, nil
}

func transcriptOf(projects string, row Row) string {
	if row.Session == "" {
		return ""
	}
	pattern := filepath.Join(projects, "*", row.Session+".jsonl")
	if row.Agent != "" {
		pattern = filepath.Join(projects, "*", row.Session, "subagents", "agent-"+row.Agent+".jsonl")
	}
	paths, err := filepath.Glob(pattern)
	if err != nil || len(paths) == 0 {
		return ""
	}
	slices.Sort(paths)
	return paths[0]
}

func lastTokens(path string) (int, error) {
	file, err := os.Open(path)
	if err != nil {
		return 0, err
	}
	defer func() { _ = file.Close() }()
	info, err := file.Stat()
	if err != nil {
		return 0, err
	}
	start := max(0, info.Size()-tailBytes)
	data := make([]byte, info.Size()-start)
	if _, err := file.ReadAt(data, start); err != nil {
		return 0, err
	}
	lines := bytes.Split(data, []byte("\n"))
	for _, line := range slices.Backward(lines) {
		if bytes.Contains(line, []byte(`"compact_boundary"`)) {
			return 0, nil
		}
		if !bytes.Contains(line, []byte(`"usage"`)) || !bytes.Contains(line, []byte(`"assistant"`)) {
			continue
		}
		var turn struct {
			Message *struct {
				Usage *struct {
					Input  int `json:"input_tokens"`
					Create int `json:"cache_creation_input_tokens"`
					Read   int `json:"cache_read_input_tokens"`
				} `json:"usage"`
			} `json:"message"`
		}
		if json.Unmarshal(line, &turn) != nil || turn.Message == nil || turn.Message.Usage == nil {
			continue
		}
		return turn.Message.Usage.Input + turn.Message.Usage.Create + turn.Message.Usage.Read, nil
	}
	return 0, nil
}

type tokenReader struct {
	projects string
	clock    func() time.Time
	measured map[string]tokenRead
}

type tokenRead struct {
	at    time.Time
	value int
}

func (t *tokenReader) read(row Row) int {
	now := t.clock()
	if last, found := t.measured[row.Lane]; found && now.Sub(last.at) < measureEvery {
		return last.value
	}
	value := 0
	if path := transcriptOf(t.projects, row); path != "" {
		if tokens, err := lastTokens(path); err == nil {
			value = tokens
		}
	}
	t.measured[row.Lane] = tokenRead{now, value}
	return value
}

type PRNode struct {
	State          string `json:"state"`
	Mergeable      string `json:"mergeable"`
	ReviewDecision string `json:"reviewDecision"`
	Commits        struct {
		Nodes []struct {
			Commit struct {
				StatusCheckRollup *struct {
					State string `json:"state"`
				} `json:"statusCheckRollup"`
			} `json:"commit"`
		} `json:"nodes"`
	} `json:"commits"`
}

func PRStateOf(node *PRNode) string {
	if node == nil || node.State != "OPEN" {
		return Closed
	}
	rollup := ""
	if len(node.Commits.Nodes) > 0 && node.Commits.Nodes[0].Commit.StatusCheckRollup != nil {
		rollup = node.Commits.Nodes[0].Commit.StatusCheckRollup.State
	}
	if node.Mergeable == "CONFLICTING" || rollup == "FAILURE" || rollup == "ERROR" || node.ReviewDecision != "APPROVED" {
		return NeedsWork
	}
	return Waiting
}

type prRead struct {
	at    time.Time
	state string
}

type prReader struct {
	env  Env
	ctx  context.Context
	read map[int]prRead
}

func (p *prReader) states(numbers []int) (map[int]string, error) {
	now := p.env.Now()
	var stale []int
	for _, n := range numbers {
		if last, found := p.read[n]; !found || now.Sub(last.at) >= openEvery {
			stale = append(stale, n)
		}
	}
	owner, name, _ := strings.Cut(p.env.Config.Repo, "/")
	for batch := range slices.Chunk(stale, prBatch) {
		var fields strings.Builder
		for _, n := range batch {
			fmt.Fprintf(&fields, "p%d: pullRequest(number: %d) { state mergeable reviewDecision commits(last: 1) { nodes { commit { statusCheckRollup { state } } } } } ", n, n)
		}
		query := fmt.Sprintf(`query=query { repository(owner: "%s", name: "%s") { %s} }`, owner, name, fields.String())
		result, err := p.env.Runner.Run(p.ctx, shell.Command{Argv: []string{"gh", "api", "graphql", "-f", query}, Timeout: readTimeout})
		var answer struct {
			Data struct {
				Repository map[string]*PRNode `json:"repository"`
			} `json:"data"`
		}
		decodeErr := json.Unmarshal([]byte(result.Stdout), &answer)
		if err != nil && (decodeErr != nil || len(answer.Data.Repository) == 0) {
			return nil, fmt.Errorf("%w: %w", ErrUnread, err)
		}
		if err == nil && decodeErr != nil {
			return nil, fmt.Errorf("%w: gh api graphql: %w", ErrUnread, decodeErr)
		}
		for _, n := range batch {
			p.read[n] = prRead{now, PRStateOf(answer.Data.Repository[fmt.Sprintf("p%d", n)])}
		}
	}
	out := make(map[int]string, len(numbers))
	for _, n := range numbers {
		out[n] = p.read[n].state
	}
	return out, nil
}

func heldBy(r Record) []int {
	if found := leadingHold.FindStringSubmatch(r.Text); found != nil {
		number, err := strconv.Atoi(found[1])
		if err != nil {
			return nil
		}
		return []int{number}
	}
	return r.PRs()
}

func namesHead(named, sha string) bool {
	return sha != "" && (strings.HasPrefix(sha, named) || strings.HasPrefix(named, sha))
}

func (e Env) held(ctx context.Context) (map[int]bool, error) {
	records, err := e.Bus.Open(ctx, e.Config.Drive, "hold")
	if err != nil {
		return nil, fmt.Errorf("%w: cci open holds: %w", ErrUnread, err)
	}
	holds := map[int]bool{}
	for _, record := range records {
		for _, n := range heldBy(recordOf(record)) {
			holds[n] = true
		}
	}
	stdout, err := e.Run(ctx, e.tool("ledger.py"), "-C", e.Config.Checkout, "list", "--drive", e.Config.Drive, "--open", "--json")
	if err != nil {
		return nil, err
	}
	var rows []struct {
		PR       json.Number `json:"pr"`
		Head     string      `json:"head"`
		Reported string      `json:"reported_head"`
		Verdict  string      `json:"reported_verdict"`
	}
	if err := json.Unmarshal([]byte(stdout), &rows); err != nil {
		return nil, fmt.Errorf("%w: ledger list: %w", ErrUnread, err)
	}
	for _, row := range rows {
		if row.Verdict != "held" || !namesHead(row.Reported, row.Head) {
			continue
		}
		number, err := row.PR.Int64()
		if err != nil {
			return nil, fmt.Errorf("ledger row pr %q: %w", row.PR, err)
		}
		holds[int(number)] = true
	}
	return holds, nil
}

func rotateAt(settings string) (int, error) {
	window := contextWindow
	data, err := os.ReadFile(settings)
	if err != nil && !errors.Is(err, fs.ErrNotExist) {
		return 0, err
	}
	if err == nil {
		var parsed struct {
			AutoCompactWindow int `json:"autoCompactWindow"`
		}
		if err := json.Unmarshal(data, &parsed); err != nil {
			return 0, fmt.Errorf("%s: %w", settings, err)
		}
		if parsed.AutoCompactWindow > 0 {
			window = min(parsed.AutoCompactWindow, contextWindow)
		}
	}
	return (window - outputReserve - autocompactBuffer) * rotatePercent / 100, nil
}

func detach(log string, argv ...string) error {
	output, err := os.OpenFile(log, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return err
	}
	defer func() { _ = output.Close() }()
	cmd := exec.Command(argv[0], argv[1:]...)
	cmd.Stdout, cmd.Stderr = output, output
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	if err := cmd.Start(); err != nil {
		return err
	}
	return cmd.Process.Release()
}

func (e Env) Deps(ctx context.Context) (Deps, error) {
	briefs := filepath.Join(e.State, "lanes")
	if err := os.MkdirAll(briefs, 0o755); err != nil {
		return Deps{}, err
	}
	at, err := rotateAt(filepath.Join(e.Claude, "settings.json"))
	if err != nil {
		return Deps{}, err
	}
	projects := filepath.Join(e.Claude, "projects")
	tokens := &tokenReader{projects: projects, clock: e.Now, measured: map[string]tokenRead{}}
	states := &prReader{env: e, ctx: ctx, read: map[int]prRead{}}
	waker := &Waker{Drive: e.Config.Drive, Clock: e.Now, Tasks: filepath.Join(e.Claude, "tasks")}
	launcher := e.tool("desk-launch.py")
	return Deps{
		Drive: e.Config.Drive, Checkout: e.Config.Checkout, Briefs: briefs,
		HandoffBrief: filepath.Join(e.plugin(), "skills", "long-running", "reference", "handoff-subagent-brief.md"),
		Records:      e.Records(ctx),
		Who:          e.Who(ctx),
		Tokens:       tokens.read,
		Transcript:   func(row Row) string { return transcriptOf(projects, row) },
		Post: func(p Post) error {
			_, err := e.Bus.Post(ctx, p.Draft(e.Config.Drive))
			if err != nil {
				return fmt.Errorf("%w: cci post: %w", ErrUnread, err)
			}
			return nil
		},
		Launch: func(name, model, effort, brief string) error {
			return detach(filepath.Join(briefs, name+".launch.log"), launcher, "lane", name, model, effort, brief, "--drive", e.Config.Drive, "--lane", Self)
		},
		Withdraw: func(name string) error {
			return detach(filepath.Join(briefs, name+".launch.log"), launcher, "withdraw", name, "--drive", e.Config.Drive, "--lane", Self)
		},
		Wake:     waker.Wake,
		PRStates: states.states,
		Held:     func() (map[int]bool, error) { return e.held(ctx) },
		RotateAt: at,
	}, nil
}
