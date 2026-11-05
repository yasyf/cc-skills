package grader

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	repoName    = "Forge-AI/monorepo"
	shaHead     = "7414944e30bfec89dee45328414dbc1016e35afa"
	shaOld      = "a269784e49009c5aa92b2341e08d6945f7ff8e95"
	shaBase     = "d011466aa1b2c3d4e5f60718293a4b5c6d7e8f90"
	shaStale    = "c3a3afc0307096aabf6d24513b2436d1bb23bbe8"
	shaOwn      = "e1f2a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4"
	shaOldOwn   = "f0e1d2c3b4a5968778695a4b3c2d1e0f9a8b7c6d"
	shaNewer    = "5be7c0de5be7c0de5be7c0de5be7c0de5be7c0de"
	shaMoved    = "8a6c4e2f0b9d7a5c3e1f8b6d4a2c0e9f7b5d3a1c"
	binDir      = "/plugin/bin"
	rulingsPath = binDir + "/rulings.py"
)

type Command = shell.Command

var (
	t0       = time.Date(2026, 11, 2, 21, 0, 0, 0, time.UTC)
	planPath string
)

func TestMain(m *testing.M) {
	code, err := runWithPlan(m)
	if err != nil {
		panic(err)
	}
	os.Exit(code)
}

func runWithPlan(m *testing.M) (int, error) {
	root, err := os.MkdirTemp("", "plans")
	if err != nil {
		return 0, err
	}
	defer os.RemoveAll(root)
	planPath = filepath.Join(root, "durable", "plan", "plan.md")
	if err := os.MkdirAll(filepath.Dir(planPath), 0o750); err != nil {
		return 0, err
	}
	if err := os.WriteFile(planPath, []byte("# plan\n"), 0o600); err != nil {
		return 0, err
	}
	return m.Run(), nil
}

type fakeShell struct {
	postError error
	mu        sync.Mutex
	dir       string
	config    string
	replies   []any
	state     string
	title     string
	body      string
	heads     []string
	comments  [][]map[string]any
	rows      string
	plan      string
	counting  bool
	failures  int
	gitFn     func(argv []string) (string, error)
	before    func(Command)
	calls     []Command
	posted    []map[string]string
	slept     []time.Duration
	timeouts  map[string]time.Duration
}

func (f *fakeShell) Run(_ context.Context, command Command) (shell.Result, error) {
	out, err := f.route(command)
	var exit *ExitError
	if errors.As(err, &exit) {
		return shell.Result{Code: exit.Code, Stdout: exit.Stdout, Stderr: exit.Stderr}, &shell.ExitError{Argv: command.Argv, Code: exit.Code, Stderr: exit.Stderr}
	}
	var timedOut *TimeoutError
	if errors.As(err, &timedOut) {
		return shell.Result{Code: shell.CodeTimedOut}, context.DeadlineExceeded
	}
	return shell.Result{Stdout: out}, err
}

func (f *fakeShell) route(command Command) (string, error) {
	if f.before != nil {
		f.before(command)
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	f.calls = append(f.calls, command)
	argv := command.Argv
	switch {
	case len(argv) > 3 && slices.Equal(argv[:3], []string{"ccx", "vcs", "gh"}):
		return f.view(argv)
	case argv[0] == "git" || argv[0] == "sh":
		return f.gitFn(expanded(command))
	case argv[0] == rulingsPath:
		f.timeouts[argv[1]] = command.Timeout
		if argv[1] == "slice" {
			return f.rows, nil
		}
		if f.plan != "" {
			return f.plan + "\n", nil
		}
		return planPath + "\n", nil
	case argv[0] == "codex-ask":
		return f.codex(argv)
	}
	return "", fmt.Errorf("unexpected call %v", argv)
}

func expanded(command Command) []string {
	at := slices.Index(command.Argv, "--stdin")
	if at < 0 {
		return command.Argv
	}
	return slices.Concat(command.Argv[:at], command.Argv[at+1:], strings.Split(strings.TrimSuffix(command.Stdin, "\n"), "\n"))
}

func (f *fakeShell) view(argv []string) (string, error) {
	if f.failures > 0 {
		f.failures--
		return "", &ExitError{Argv: argv, Code: 1, Stderr: `Post "https://api.github.com/graphql": net/http: TLS handshake timeout`}
	}
	head := f.heads[0]
	if len(f.heads) > 1 {
		f.heads = f.heads[1:]
	}
	comments := f.comments[0]
	if len(f.comments) > 1 {
		f.comments = f.comments[1:]
	}
	data, err := json.Marshal(map[string]any{
		"state":       f.state,
		"title":       f.title,
		"baseRefName": "dev",
		"baseRefOid":  shaStale,
		"headRefOid":  head,
		"body":        f.body,
		"comments":    comments,
		"author":      map[string]string{"login": "yasyf"},
	})
	return string(data), err
}

func (f *fakeShell) codex(argv []string) (string, error) {
	if len(f.replies) == 0 {
		return "", fmt.Errorf("no reply left for %v", argv)
	}
	answer := f.replies[0]
	f.replies = f.replies[1:]
	if err, failed := answer.(error); failed {
		return "", err
	}
	data, err := json.Marshal(answer)
	if err != nil {
		return "", err
	}
	path := filepath.Join(f.dir, fmt.Sprintf("reply-%d.json", len(f.calls)))
	if err := os.WriteFile(path, data, 0o600); err != nil {
		return "", err
	}
	return fmt.Sprintf("REPLY_FILE: %s\nLOG_FILE: %s.log\nAWAIT: codex-ask --await %s\n", path, path, argv[slices.Index(argv, "-s")+1]), nil
}

func (f *fakeShell) post(_ context.Context, draft client.Draft) (client.Record, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	post := map[string]string{"--drive": draft.Drive, "--lane": draft.Lane, "--kind": draft.Kind, "--id": draft.ID, "--text": draft.Text}
	if len(draft.PRs) > 0 {
		post["--pr"] = draft.PRs[0]
	}
	if len(draft.To) > 0 {
		post["--to"] = strings.Join(draft.To, ",")
	}
	if draft.Path != "" {
		post["--path"] = draft.Path
	}
	f.posted = append(f.posted, post)
	if f.postError != nil {
		return client.Record{}, f.postError
	}
	seq := int64(150001)
	if f.counting {
		seq += int64(len(f.posted))
	}
	return client.Record{Seq: seq}, nil
}

func (f *fakeShell) deps() Deps {
	return Deps{
		Runner: f,
		Post:   f.post,
		Sleep:  func(pause time.Duration) { f.mu.Lock(); defer f.mu.Unlock(); f.slept = append(f.slept, pause) },
		Now:    func() time.Time { return t0 },
		Log:    slog.New(slog.NewTextHandler(io.Discard, nil)),
		Bin:    binDir,
		Config: f.config,
	}
}

func (f *fakeShell) snapshot() []Command {
	f.mu.Lock()
	defer f.mu.Unlock()
	return slices.Clone(f.calls)
}

func (f *fakeShell) asks() [][]string {
	var asks [][]string
	for _, call := range f.snapshot() {
		if call.Argv[0] == "codex-ask" {
			asks = append(asks, call.Argv)
		}
	}
	return asks
}

func (f *fakeShell) texts() []string {
	texts := make([]string, 0, len(f.posted))
	for _, post := range f.posted {
		texts = append(texts, post["--text"])
	}
	return texts
}

func (f *fakeShell) gitCalls(prefix ...string) [][]string {
	var found [][]string
	for _, call := range f.snapshot() {
		if len(call.Argv) > len(prefix) && call.Argv[0] == "git" && slices.Equal(call.Argv[1:1+len(prefix)], prefix) {
			found = append(found, call.Argv)
		}
	}
	return found
}

func (f *fakeShell) gitStdins(prefix ...string) []string {
	var found []string
	for _, call := range f.snapshot() {
		if len(call.Argv) > len(prefix) && call.Argv[0] == "git" && slices.Equal(call.Argv[1:1+len(prefix)], prefix) {
			found = append(found, call.Stdin)
		}
	}
	return found
}

func writeJob(t *testing.T, directory string, spec map[string]any, changes map[string]any) string {
	t.Helper()
	if err := os.MkdirAll(directory, 0o750); err != nil {
		t.Fatal(err)
	}
	merged := map[string]any{}
	for key, value := range spec {
		merged[key] = value
	}
	for key, value := range changes {
		merged[key] = value
	}
	data, err := json.Marshal(merged)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(directory, JobFile), data, 0o600); err != nil {
		t.Fatal(err)
	}
	return directory
}

func resultOf(t *testing.T, directory string) map[string]any {
	t.Helper()
	data, err := os.ReadFile(filepath.Join(directory, ResultFile))
	if err != nil {
		t.Fatal(err)
	}
	var outcome map[string]any
	if err := json.Unmarshal(data, &outcome); err != nil {
		t.Fatal(err)
	}
	return outcome
}

func readText(t *testing.T, directory, name string) string {
	t.Helper()
	data, err := os.ReadFile(filepath.Join(directory, name))
	if err != nil {
		t.Fatal(err)
	}
	return string(data)
}

func holdsAll(t *testing.T, text string, wants ...string) {
	t.Helper()
	for _, want := range wants {
		if !strings.Contains(text, want) {
			t.Errorf("missing %q in:\n%s", want, text)
		}
	}
}

func holdsNone(t *testing.T, text string, unwanted ...string) {
	t.Helper()
	for _, word := range unwanted {
		if strings.Contains(text, word) {
			t.Errorf("unexpected %q in:\n%s", word, text)
		}
	}
}

func equal[T comparable](t *testing.T, got, want T) {
	t.Helper()
	if got != want {
		t.Errorf("got %v\nwant %v", got, want)
	}
}

func sameList(t *testing.T, got, want []string) {
	t.Helper()
	if !slices.Equal(got, want) {
		t.Errorf("got %q\nwant %q", got, want)
	}
}

func wantNoFile(t *testing.T, directory, name string) {
	t.Helper()
	if _, err := os.Stat(filepath.Join(directory, name)); err == nil {
		t.Errorf("%s exists in %s", name, directory)
	}
}

func subjectsOf(shas []string, named map[string]string) string {
	subjects := make([]string, 0, len(shas))
	for _, sha := range shas {
		subject, known := named[sha]
		if !known {
			subject = "commit " + sha
		}
		subjects = append(subjects, subject)
	}
	return strings.Join(subjects, "\n") + "\n"
}
