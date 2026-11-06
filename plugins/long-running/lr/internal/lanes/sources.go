package lanes

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/deletions"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/owner"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/watch/screen"
)

const (
	readTimeout       = 60 * time.Second
	tailBytes         = 1_000_000
	measureEvery      = 5 * time.Minute
	openEvery         = 10 * time.Minute
	prBatch           = 50
	laneWindow        = 333_000
	outputReserve     = 20_000
	autocompactBuffer = 13_000
	rotatePercent     = 70
	rotateAt          = (laneWindow - outputReserve - autocompactBuffer) * rotatePercent / 100
	wakeSender        = "long-running"
	adoptWindow       = 72 * time.Hour
)

var (
	unsafeName  = regexp.MustCompile(`[^a-zA-Z0-9_-]`)
	launchOrder = regexp.MustCompile(`\borca-desk: launch (\S+)`)
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

	MonitorLane func(ctx context.Context, monitor, lane string) (string, error)
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

func (e Env) PinnedBinary() string {
	data, err := os.ReadFile(e.tool("lr.binrun"))
	if err != nil {
		return ""
	}
	_, descriptor, _ := bytes.Cut(data, []byte("\n"))
	var pin struct {
		Version struct {
			Static string `json:"static"`
		} `json:"version"`
	}
	if json.Unmarshal(descriptor, &pin) != nil {
		return ""
	}
	return pin.Version.Static
}

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

func (e Env) Ledger(ctx context.Context, argv ...string) (string, error) {
	return e.Run(ctx, append([]string{e.tool("ledger.py"), "-C", e.Config.Checkout}, argv...)...)
}

func recordOf(r client.Record) Record {
	refs := Refs{Lanes: r.Refs.Lanes, Targets: r.Refs.Targets, CCN: r.Refs.CCN}
	for _, pr := range r.Refs.PRs {
		refs.PRs = append(refs.PRs, PRRef(pr.Number))
	}
	return Record{
		Seq: r.Seq, Origin: r.Origin, Lane: r.Lane, Kind: string(r.Kind), At: r.At.UTC(), Text: r.Text, Topic: r.Topic, To: r.To,
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
		threads, err := e.unboundThreads(ctx, rows)
		if err != nil {
			return nil, err
		}
		rows = Rostered(append(rows, threads...), filepath.Join(e.Claude, "teams"))
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

func (e Env) unboundThreads(ctx context.Context, rows []Row) ([]Row, error) {
	leads := map[string]bool{}
	for _, row := range rows {
		if row.Live && row.Agent == "" && row.Session != "" {
			leads[row.Session] = true
		}
	}
	if len(leads) == 0 {
		return nil, nil
	}
	out, err := e.Run(ctx, "cci", "who", "--role", "agent", "--json", "--fields", "session,agent,agent_name,lane,live,seen_at")
	if err != nil {
		return nil, err
	}
	var threads []Row
	decoder := json.NewDecoder(strings.NewReader(out))
	for decoder.More() {
		var thread struct {
			Session string    `json:"session"`
			Agent   string    `json:"agent"`
			Name    string    `json:"agent_name"`
			Lane    string    `json:"lane"`
			Live    bool      `json:"live"`
			SeenAt  time.Time `json:"seen_at"`
		}
		if err := decoder.Decode(&thread); err != nil {
			return nil, fmt.Errorf("%w: cci who printed a thread that does not parse: %w", ErrUnread, err)
		}
		if leads[thread.Session] && thread.Lane == "" && thread.Name != "" {
			threads = append(threads, Row{Lane: thread.Name, Session: thread.Session, Agent: thread.Agent, Live: thread.Live, SeenAt: thread.SeenAt})
		}
	}
	return threads, nil
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
			row.Live, row.Idle, row.Roster = true, !row.Live, teamDir(teams, row.Session)
		}
		out = append(out, row)
	}
	return out
}

type launchReceipt struct {
	Result struct {
		DispatchID string `json:"dispatchId"`
		RunID      string `json:"runId"`
		Server     struct {
			Name string `json:"name"`
		} `json:"server"`
		Effects []struct {
			Kind string `json:"kind"`
			Role string `json:"role"`
			ID   string `json:"id"`
		} `json:"effects"`
	} `json:"result"`
}

func (r launchReceipt) agentTerminal() string {
	for _, effect := range r.Result.Effects {
		if effect.Kind == "terminal" && effect.Role == "agent" {
			return effect.ID
		}
	}
	return ""
}

func receiptOf(receipts, lane string) launchReceipt {
	data, err := os.ReadFile(filepath.Join(receipts, lane+".json"))
	if err != nil {
		return launchReceipt{}
	}
	var receipt launchReceipt
	if json.Unmarshal(data, &receipt) != nil {
		return launchReceipt{}
	}
	return receipt
}

func dispatchOf(receipts, lane string) string {
	return receiptOf(receipts, lane).Result.DispatchID
}

func spriteOf(receipts, lane string) string {
	return or(receiptOf(receipts, lane).Result.Server.Name, lane)
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
	statuses, err := e.dispatchStatuses(ctx)
	if err != nil {
		return nil, err
	}
	lanes := make([]string, 0, len(dispatches))
	for lane := range dispatches {
		lanes = append(lanes, lane)
	}
	slices.Sort(lanes)
	var rows []Row
	for _, lane := range lanes {
		read, known := statuses[dispatches[lane]]
		if !known {
			continue
		}
		settled := slices.Contains(settledDispatch, read.Status)
		if !settled && !slices.Contains(agentVerdicts, read.Liveness) {
			return nil, fmt.Errorf("%w: orca worker-list gives dispatch %s agent liveness %q, not one of %s", ErrUnread, dispatches[lane], read.Liveness, strings.Join(agentVerdicts, ", "))
		}
		row := Row{Lane: lane, Live: !settled && read.Liveness == agentLive, SeenAt: e.Now().UTC(), Dispatch: dispatches[lane], Succeeded: read.Worker == workerSucceeded, Terminal: read.Terminal, Environment: read.Environment}
		if !row.Live {
			info, err := os.Stat(filepath.Join(receipts, lane+".json"))
			if err != nil {
				return nil, err
			}
			row.SeenAt = info.ModTime().UTC()
		}
		if !settled && read.Liveness == agentUnverifiable {
			row.Unverified = fmt.Sprintf("has an Orca agent that reads unverifiable (%s)", or(read.Why, "no reason given"))
			row.HostGone = read.Why == hostUnavailable
		}
		rows = append(rows, row)
	}
	return rows, nil
}

type dispatchRead struct {
	Status      string
	Worker      string
	Liveness    string
	Why         string
	Terminal    string
	Environment string
}

func (e Env) dispatchStatuses(ctx context.Context) (map[string]dispatchRead, error) {
	statuses := map[string]dispatchRead{}
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
					WorkerState    string `json:"workerState"`
					Terminal       string `json:"agentTerminalHandle"`
					Projection     struct {
						Host struct {
							Kind string `json:"kind"`
							ID   string `json:"id"`
						} `json:"host"`
						Liveness struct {
							Verdict string `json:"verdict"`
							Reason  string `json:"reason"`
						} `json:"liveness"`
					} `json:"projection"`
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
			read := dispatchRead{Status: worker.DispatchStatus, Worker: worker.WorkerState, Liveness: worker.Projection.Liveness.Verdict, Why: worker.Projection.Liveness.Reason, Terminal: worker.Terminal}
			if worker.Projection.Host.Kind == remoteHost {
				read.Environment = worker.Projection.Host.ID
			}
			statuses[worker.DispatchID] = read
		}
		if page.Result.Page.NextCursor == "" {
			return statuses, nil
		}
		cursor = []string{"--cursor", page.Result.Page.NextCursor}
	}
}

func appendInbox(inbox string, message map[string]any) (bool, error) {
	if err := os.MkdirAll(filepath.Dir(inbox), 0o750); err != nil {
		return false, err
	}
	lock := inbox + ".lock"
	if err := os.Mkdir(lock, 0o750); err != nil {
		if errors.Is(err, fs.ErrExist) {
			return false, nil
		}
		return false, err
	}
	defer func() { _ = os.Remove(lock) }()
	messages := make([]json.RawMessage, 0, 1)
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
	if err := os.WriteFile(staged, out.Bytes(), 0o600); err != nil {
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
	Drive  string
	Clock  func() time.Time
	Send   func(dispatch, subject, body string) error
	Unread func(lane, subject string) (bool, error)
	Type   func(terminal, environment, text string) (bool, error)
}

func (w *Waker) Wake(lane string, row Row, page map[string]any) (bool, error) {
	text := fmt.Sprintf("WAKE %s: %s Read your mail with `cci tail --drive %s --reader %s --to %s`, then continue the work in hand from your ledger; do not restart your brief.", lane, pageText(page, "why"), w.Drive, lane, lane)
	summary := fmt.Sprintf("WAKE %s: continue the work in hand", lane)
	if row.Environment != "" {
		typed, err := w.Type(row.Terminal, row.Environment, text)
		if err != nil {
			slog.Warn("wake not delivered", "lane", lane, "environment", row.Environment, "err", err)
		}
		return typed, nil
	}
	if row.Dispatch != "" {
		if pending, err := w.Unread(lane, summary); err == nil && pending {
			return true, nil
		}
		if err := w.Send(row.Dispatch, summary, text); err != nil {
			slog.Warn("wake not delivered", "lane", lane, "dispatch", row.Dispatch, "err", err)
			return false, nil
		}
		return true, nil
	}
	inbox := filepath.Join(row.Roster, "inboxes", unsafeName.ReplaceAllString(lane, "-")+".json")
	pending, err := inboxHoldsUnread(inbox, summary)
	if err != nil || pending {
		return pending, err
	}
	message := map[string]any{
		"from":      wakeSender,
		"text":      text,
		"summary":   summary,
		"timestamp": w.Clock().UTC().Format("2006-01-02T15:04:05.000Z"),
		"msgV":      1,
		"msg_id":    randomHex(16),
		"type":      "message",
		"read":      false,
	}
	return appendInbox(inbox, message)
}

func inboxHoldsUnread(inbox, summary string) (bool, error) {
	data, err := os.ReadFile(inbox)
	if err != nil && !errors.Is(err, fs.ErrNotExist) {
		return false, err
	}
	if len(bytes.TrimSpace(data)) == 0 {
		return false, nil
	}
	var messages []struct {
		Summary string `json:"summary"`
		Read    bool   `json:"read"`
	}
	if err := json.Unmarshal(data, &messages); err != nil {
		return false, fmt.Errorf("%s: %w", inbox, err)
	}
	for _, message := range messages {
		if message.Summary == summary && !message.Read {
			return true, nil
		}
	}
	return false, nil
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
	if node.Mergeable == "CONFLICTING" || rollup == "FAILURE" || rollup == "ERROR" {
		return Broken
	}
	if node.ReviewDecision != "APPROVED" {
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

func namesHead(named, sha string) bool {
	return sha != "" && (strings.HasPrefix(sha, named) || strings.HasPrefix(named, sha))
}

func (e Env) openWork(ctx context.Context) ([]Record, error) {
	records, err := e.Bus.Tail(ctx, client.Query{Drive: e.Config.Drive, Since: e.Now().Add(-24 * time.Hour), Kinds: []string{"defect", "ask", "blocker"}})
	if err != nil {
		return nil, fmt.Errorf("%w: cci tail: %w", ErrUnread, err)
	}
	open := make([]Record, 0, len(records))
	for _, record := range records {
		if record.Status == "open" {
			open = append(open, recordOf(record))
		}
	}
	return open, nil
}

func (e Env) held(ctx context.Context) (map[int]bool, error) {
	records, err := e.Bus.Open(ctx, e.Config.Drive, "hold")
	if err != nil {
		return nil, fmt.Errorf("%w: cci open holds: %w", ErrUnread, err)
	}
	holds := map[int]bool{}
	for _, record := range records {
		for _, n := range recordOf(record).PRs() {
			holds[n] = true
		}
	}
	stdout, err := e.Ledger(ctx, "list", "--drive", e.Config.Drive, "--open", "--json")
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

func attach(log string, argv ...string) error {
	output, err := os.OpenFile(log, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	defer func() { _ = output.Close() }()
	var said bytes.Buffer
	cmd := exec.Command(argv[0], argv[1:]...)
	cmd.Stdout = io.MultiWriter(output, &said)
	cmd.Stderr = cmd.Stdout
	if err := cmd.Run(); err != nil {
		lines := strings.Split(strings.TrimSpace(said.String()), "\n")
		return fmt.Errorf("%s (%w)", lines[len(lines)-1], err)
	}
	return nil
}

func detach(log string, argv ...string) error {
	output, err := os.OpenFile(log, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o600)
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

type deskReader struct {
	env   Env
	ctx   context.Context
	after int64
	desks map[string]string
}

func (r *deskReader) all() (map[string]string, error) {
	records, err := r.env.Bus.Tail(r.ctx, client.Query{Drive: r.env.Config.Drive, After: r.after, Kinds: []string{"go", "decide"}})
	if err != nil {
		return nil, fmt.Errorf("%w: cci tail: %w", ErrUnread, err)
	}
	for _, raw := range records {
		r.after = raw.Seq
		record := recordOf(raw)
		if owner := record.Attr("owned-by"); owner != "" && len(record.To) > 0 {
			r.desks[record.To[0]] = owner
			continue
		}
		if found := launchOrder.FindStringSubmatch(record.Text); found != nil && record.Kind == "go" {
			r.desks[found[1]] = record.Lane
		}
	}
	return r.desks, nil
}

func (e Env) sendDispatch(ctx context.Context, dispatch, subject, body string) error {
	stdout, err := e.Run(ctx, "orca", "orchestration", "run-show", "--id", e.Config.OrcaRun, "--json", "--full")
	if err != nil {
		return err
	}
	var shown struct {
		Result struct {
			Run struct {
				Coordinator string `json:"coordinator_handle"`
			} `json:"run"`
		} `json:"result"`
	}
	if err := json.Unmarshal([]byte(stdout), &shown); err != nil {
		return fmt.Errorf("%w: orca run-show: %w", ErrUnread, err)
	}
	_, err = e.Run(ctx, "orca", "orchestration", "send", "--from", shown.Result.Run.Coordinator, "--to", "dispatch:"+dispatch, "--run", e.Config.OrcaRun, "--type", "dispatch", "--subject", subject, "--body", body, "--json")
	return err
}

func (e Env) typeWake(ctx context.Context, terminal, environment, text string) (bool, error) {
	stdout, err := e.Run(ctx, "orca", "terminal", "read", "--terminal", terminal, "--environment", environment, "--screen", "--json")
	if err != nil {
		return false, err
	}
	var shown struct {
		Result struct {
			Terminal screen.Terminal `json:"terminal"`
		} `json:"result"`
	}
	if err := json.Unmarshal([]byte(stdout), &shown); err != nil {
		return false, fmt.Errorf("%w: orca terminal read: %w", ErrUnread, err)
	}
	if !screen.Wakeable(shown.Result.Terminal) {
		return false, nil
	}
	_, err = e.Run(ctx, "orca", "terminal", "send", "--terminal", terminal, "--environment", environment, "--text", strings.Join(strings.Fields(text), " "), "--enter", "--json")
	return err == nil, err
}

func (e Env) dispatchHoldsUnread(ctx context.Context, lane, subject string) (bool, error) {
	terminal, err := os.ReadFile(filepath.Join(e.receipts(), lane+".terminal"))
	if err != nil {
		return false, err
	}
	stdout, err := e.Run(ctx, "orca", "orchestration", "check", "--terminal", strings.TrimSpace(string(terminal)), "--run", e.Config.OrcaRun, "--peek", "--json", "--full")
	if err != nil {
		return false, err
	}
	var peek struct {
		OK     bool `json:"ok"`
		Error  any  `json:"error"`
		Result struct {
			Messages []struct {
				Subject string `json:"subject"`
			} `json:"messages"`
		} `json:"result"`
	}
	if err := json.Unmarshal([]byte(stdout), &peek); err != nil {
		return false, fmt.Errorf("%w: orca check: %w", ErrUnread, err)
	}
	if !peek.OK {
		return false, fmt.Errorf("%w: orca check: %v", ErrUnread, peek.Error)
	}
	for _, message := range peek.Result.Messages {
		if message.Subject == subject {
			return true, nil
		}
	}
	return false, nil
}

func (e Env) Deps(ctx context.Context) (Deps, error) {
	briefs := filepath.Join(e.State, "lanes")
	if err := os.MkdirAll(briefs, 0o750); err != nil {
		return Deps{}, err
	}
	projects := filepath.Join(e.Claude, "projects")
	tokens := &tokenReader{projects: projects, clock: e.Now, measured: map[string]tokenRead{}}
	states := &prReader{env: e, ctx: ctx, read: map[int]prRead{}}
	desks := &deskReader{env: e, ctx: ctx, desks: map[string]string{}}
	notesToken := sync.OnceValues(func() (string, error) {
		token, err := e.Run(ctx, "gh", "auth", "token")
		return strings.TrimSpace(token), err
	})
	publishing := &laneLocks{held: map[string]*sync.Mutex{}}
	waker := &Waker{Drive: e.Config.Drive, Clock: e.Now, Send: func(dispatch, subject, body string) error {
		return e.sendDispatch(ctx, dispatch, subject, body)
	}, Unread: func(lane, subject string) (bool, error) {
		return e.dispatchHoldsUnread(ctx, lane, subject)
	}, Type: func(terminal, environment, text string) (bool, error) {
		return e.typeWake(ctx, terminal, environment, text)
	}}
	launcher := e.tool("desk-launch.py")
	trunk := deletions.Of(e.Runner, e.Config)
	landing, err := ReadRunnerLanding(e.State)
	if err != nil {
		return Deps{}, err
	}
	return Deps{
		Drive: e.Config.Drive, Parent: e.Config.Parent, Checkout: e.Config.Checkout, State: e.State, Briefs: briefs, Bin: filepath.Join(e.plugin(), "bin"),
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
		Launch: func(name, model, effort, brief, key string) error {
			return attach(filepath.Join(briefs, name+".launch.log"), launcher, "lane", name, model, effort, brief, "--drive", e.Config.Drive, "--lane", Self, "--timeout-seconds", "0", "--now", "--id", key)
		},
		Withdraw: func(name string) error {
			return detach(filepath.Join(briefs, name+".launch.log"), launcher, "withdraw", name, "--drive", e.Config.Drive, "--lane", Self)
		},
		Wake:     waker.Wake,
		PRStates: states.states,
		Held:     func() (map[int]bool, error) { return e.held(ctx) },
		Desks:    desks.all,
		OpenWork: func() ([]Record, error) { return e.openWork(ctx) },
		Owed:     func() ([]deletions.Owed, error) { return trunk.List(ctx) },
		Present:  func(rows []deletions.Owed) ([]deletions.Owed, error) { return trunk.Present(ctx, rows) },
		PublishNotes: func(lane string) error {
			defer publishing.hold(lane)()
			return e.publishNotes(ctx, notesToken, lane)
		},
		Gaps: func() ([]string, error) { return trunk.Gaps(ctx, e.tool("rulings.py"), e.Config.Drive) },
		Orders: func(s *store.Store) ([]owner.Row, error) {
			return OpenOrders(ctx, e.Bus, s, e.Config.Drive, e.Config.Repo, e.State)
		},
		Undelivered:           func() ([]owner.Row, error) { return UndeliveredOrders(ctx, e.Bus, e.Config.Drive, e.State) },
		RotateAt:              rotateAt,
		RetireAfter:           e.Config.RetireSilent,
		Repo:                  e.Config.Repo,
		Ledger:                landing.Ledger,
		OpenBranches:          func(s *store.Store) (map[string][]int, error) { return openBranches(s, e.Config.Repo) },
		Ours:                  func(s *store.Store, prs []int) ([]int, error) { return s.DrivePRs(e.Config.Repo, e.Config.Author, prs) },
		Sprites:               func(names []string) (map[string]Sprite, error) { return e.laneSprites(ctx, names) },
		Hosts:                 func(names []string, sprites map[string]Sprite) map[string]Host { return e.hosts(ctx, names, sprites) },
		LastWords:             func(sprite string) string { return e.lastWords(ctx, sprite) },
		Carried:               func(lane string) (Carried, error) { return e.carried(ctx, lane) },
		BuildRecommendedOrder: e.Config.BuildRecommendedOrder,
	}, nil
}

func openBranches(s *store.Store, repo string) (map[string][]int, error) {
	rows, err := s.ReviewRows(repo)
	if err != nil {
		return nil, err
	}
	branches := map[string][]int{}
	for _, row := range rows {
		if !prrow.Terminal(row.State) && row.State != prrow.StateSuperseded {
			branches[row.Branch] = append(branches[row.Branch], row.Number)
		}
	}
	return branches, nil
}
