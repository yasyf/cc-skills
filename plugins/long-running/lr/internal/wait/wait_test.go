package wait

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

const armed = 300 * time.Millisecond

func ninePM() time.Time { return time.Date(2026, 11, 3, 21, 0, 0, 0, time.Local) }

type run struct {
	out, errs bytes.Buffer
	code      int
	done      chan struct{}
}

func start(t *testing.T, o Options) *run {
	t.Helper()
	r := &run{done: make(chan struct{})}
	o.Stdout, o.Stderr = &r.out, &r.errs
	if o.Runner == nil {
		o.Runner = &shelltest.Fake{}
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	t.Cleanup(cancel)
	go func() {
		r.code = Run(ctx, o)
		close(r.done)
	}()
	return r
}

func (r *run) wait(t *testing.T) *run {
	t.Helper()
	select {
	case <-r.done:
	case <-time.After(15 * time.Second):
		t.Fatal("lr wait did not return")
	}
	return r
}

type member struct {
	name     string
	joinedAt int
}

func writeRoster(t *testing.T, claude, team string, members ...member) {
	t.Helper()
	dir := filepath.Join(claude, "teams", team)
	if err := os.MkdirAll(filepath.Join(dir, "inboxes"), 0o750); err != nil {
		t.Fatal(err)
	}
	rows := make([]map[string]any, 0, len(members))
	for _, m := range members {
		rows = append(rows, map[string]any{"name": m.name, "joinedAt": m.joinedAt})
	}
	content, _ := json.Marshal(map[string]any{"members": rows})
	if err := os.WriteFile(filepath.Join(dir, "config.json"), content, 0o600); err != nil {
		t.Fatal(err)
	}
}

func writeJSON(t *testing.T, path string, value any) {
	t.Helper()
	content, _ := json.MarshalIndent(value, "", "  ")
	if err := os.WriteFile(path+".tmp", content, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(path+".tmp", path); err != nil {
		t.Fatal(err)
	}
}

func TestTheNewestRosterEntryNamesTheMailbox(t *testing.T) {
	claude := t.TempDir()
	writeRoster(t, claude, "session-old", member{"team-lead", 1}, member{"desk.v2", 2})
	writeRoster(t, claude, "session-d8680ece", member{"team-lead", 3}, member{"desk.v2", 4})

	path, err := Mailbox(claude, "desk.v2")
	if err != nil {
		t.Fatal(err)
	}
	if want := filepath.Join(claude, "teams", "session-d8680ece", "inboxes", "desk-v2.json"); path != want {
		t.Fatalf("got %s, want %s", path, want)
	}
	if path, _ := Mailbox(claude, "desk-3"); path != "" {
		t.Fatalf("an unlisted lane resolved to %s", path)
	}
}

func TestAnUnlistedMailboxLaneExits1(t *testing.T) {
	r := start(t, Options{Seconds: 5, Sources: []string{"mailbox:nobody=" + filepath.Join(t.TempDir(), "c")}, Claude: t.TempDir()}).wait(t)
	if r.code != CodeNoRoster || !strings.Contains(r.errs.String(), "no team roster lists nobody") {
		t.Fatalf("got %d %q", r.code, r.errs.String())
	}
}

func TestAMailboxEntryWakesTheWaitMarksItReadAndKeepsItsKey(t *testing.T) {
	claude := t.TempDir()
	writeRoster(t, claude, "session-a", member{"desk", 1})
	mailbox := filepath.Join(claude, "teams", "session-a", "inboxes", "desk.json")
	writeJSON(t, mailbox, []map[string]any{{"from": "root", "text": "old", "timestamp": "t0", "read": true, "msg_id": "m0"}})
	cursor := filepath.Join(t.TempDir(), "desk.cursor")
	r := start(t, Options{Seconds: 10, Sources: []string{"mailbox:desk=" + cursor}, Claude: claude})
	time.Sleep(armed)

	writeJSON(t, mailbox, []map[string]any{
		{"from": "root", "text": "old", "timestamp": "t0", "read": true, "msg_id": "m0"},
		{"from": "root", "text": "do the thing", "timestamp": "2026-11-03T05:05:53.686Z", "read": false, "msg_id": "m1"},
	})
	r.wait(t)

	if r.code != 0 {
		t.Fatalf("exit %d: %s", r.code, r.errs.String())
	}
	if want := "MAILBOX 1 unread\n== root 2026-11-03T05:05:53.686Z\ndo the thing\n"; r.out.String() != want {
		t.Fatalf("got %q, want %q", r.out.String(), want)
	}
	if got := readText(cursor); got != "m1\n" {
		t.Fatalf("cursor holds %q", got)
	}
	var entries []entry
	if err := json.Unmarshal([]byte(readText(mailbox)), &entries); err != nil {
		t.Fatal(err)
	}
	if read, _ := entries[1]["read"].(bool); !read {
		t.Fatal("the printed entry was not marked read")
	}
	if _, err := os.Stat(mailbox + ".lock"); !os.IsNotExist(err) {
		t.Fatal("the harness lock was left behind")
	}
}

func TestAnEntryAlreadyInTheCursorNeverWakesAgain(t *testing.T) {
	claude := t.TempDir()
	writeRoster(t, claude, "session-a", member{"desk", 1})
	mailbox := filepath.Join(claude, "teams", "session-a", "inboxes", "desk.json")
	writeJSON(t, mailbox, []map[string]any{{"from": "root", "text": "seen", "timestamp": "t1", "read": false, "msg_id": "m1"}})
	cursor := filepath.Join(t.TempDir(), "desk.cursor")
	if err := os.WriteFile(cursor, []byte("3\nm1\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	fixed := time.Date(2026, 11, 2, 21, 5, 0, 0, time.UTC)
	r := start(t, Options{Seconds: 1, Sources: []string{"mailbox:desk=" + cursor}, Claude: claude, Now: func() time.Time { return fixed }}).wait(t)
	if r.code != 0 || r.out.String() != "QUIET 1:05 PM\n" {
		t.Fatalf("got %d %q", r.code, r.out.String())
	}
}

func TestAFileSourcePrintsNewLinesPastTheCursorAndCountsArchives(t *testing.T) {
	dir := t.TempDir()
	inbox := filepath.Join(dir, "landing-desk.md")
	if err := os.MkdirAll(inbox+".archive", 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(inbox+".archive", "2026-11-01.md"), []byte("L1 read\nL2 read\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(inbox, []byte("  R1 raw \\text  \n\n"+strings.Repeat("x", 2600)+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	cursor := filepath.Join(dir, "landing-desk.cursor")
	if err := os.WriteFile(cursor, []byte("2\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	r := start(t, Options{Seconds: 5, Sources: []string{inbox + "=" + cursor}}).wait(t)

	if r.code != 0 {
		t.Fatalf("exit %d: %s", r.code, r.errs.String())
	}
	if want := "  R1 raw \\text  \n" + strings.Repeat("x", 399) + "…\n"; r.out.String() != want {
		t.Fatalf("got %q", r.out.String())
	}
	if got := readText(cursor); got != "5\n" {
		t.Fatalf("cursor holds %q", got)
	}
}

func TestANewLineWakesAFileSource(t *testing.T) {
	dir := t.TempDir()
	inbox := filepath.Join(dir, "orca-desk.md")
	cursor := filepath.Join(dir, "orca-desk.cursor")
	if err := os.WriteFile(inbox, []byte("R1 old\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(cursor, []byte("1\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	r := start(t, Options{Seconds: 10, Sources: []string{inbox + "=" + cursor}})
	time.Sleep(armed)

	f, err := os.OpenFile(inbox, os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := f.WriteString("R2 orca-desk: launch x\n"); err != nil {
		t.Fatal(err)
	}
	_ = f.Close()
	r.wait(t)

	if r.code != 0 || r.out.String() != "R2 orca-desk: launch x\n" || readText(cursor) != "2\n" {
		t.Fatalf("got %d %q cursor %q", r.code, r.out.String(), readText(cursor))
	}
}

func TestAGuardedCommandsExitEndsTheWaitWithItsCode(t *testing.T) {
	dir := t.TempDir()
	cursor := filepath.Join(dir, "c")
	r := start(t, Options{Seconds: 10, Sources: []string{filepath.Join(dir, "in.md") + "=" + cursor}, Command: []string{"sh", "-c", "exit 3"}}).wait(t)
	if r.code != 3 {
		t.Fatalf("exit %d: %s", r.code, r.errs.String())
	}
}

func TestAWakeEndsTheGuardedCommandsWholeProcessGroup(t *testing.T) {
	dir := t.TempDir()
	inbox := filepath.Join(dir, "in.md")
	cursor := filepath.Join(dir, "c")
	pidFile := filepath.Join(dir, "sleeper.pid")
	script := fmt.Sprintf("sleep 30 & echo $! > %q; wait", pidFile)
	r := start(t, Options{Seconds: 10, Sources: []string{inbox + "=" + cursor}, Command: []string{"sh", "-c", script}})
	deadline := time.Now().Add(5 * time.Second)
	for readText(pidFile) == "" && time.Now().Before(deadline) {
		time.Sleep(20 * time.Millisecond)
	}
	sleeper, err := strconv.Atoi(strings.TrimSpace(readText(pidFile)))
	if err != nil {
		t.Fatalf("no sleeper pid: %v", err)
	}

	if err := os.WriteFile(inbox, []byte("wake\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	r.wait(t)

	if r.code != 0 || r.out.String() != "wake\n" {
		t.Fatalf("got %d %q", r.code, r.out.String())
	}
	for time.Now().Before(deadline) && syscall.Kill(sleeper, 0) == nil {
		time.Sleep(20 * time.Millisecond)
	}
	if syscall.Kill(sleeper, 0) == nil {
		t.Fatalf("the sleeper %d survived the wake", sleeper)
	}
}

func TestAWakeEndsAnOrphanedDescendantInItsOwnProcessGroup(t *testing.T) {
	dir := t.TempDir()
	inbox := filepath.Join(dir, "in.md")
	cursor := filepath.Join(dir, "c")
	pidFile := filepath.Join(dir, "orphan.pid")
	script := fmt.Sprintf("(perl -e 'setpgrp(0,0); exec \"sleep\", \"30\"' & echo $! > %q); sleep 30", pidFile)
	r := start(t, Options{Seconds: 10, Sources: []string{inbox + "=" + cursor}, Command: []string{"sh", "-c", script}})
	deadline := time.Now().Add(5 * time.Second)
	for readText(pidFile) == "" && time.Now().Before(deadline) {
		time.Sleep(20 * time.Millisecond)
	}
	orphan, err := strconv.Atoi(strings.TrimSpace(readText(pidFile)))
	if err != nil {
		t.Fatalf("no orphan pid: %v", err)
	}
	time.Sleep(armed)

	if err := os.WriteFile(inbox, []byte("wake\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	r.wait(t)

	if r.code != 0 {
		t.Fatalf("exit %d: %s", r.code, r.errs.String())
	}
	deadline = time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) && syscall.Kill(orphan, 0) == nil {
		time.Sleep(20 * time.Millisecond)
	}
	if syscall.Kill(orphan, 0) == nil {
		_ = syscall.Kill(orphan, syscall.SIGKILL)
		t.Fatalf("the orphan %d, reparented and in its own group, survived the wake", orphan)
	}
}

func TestAGuardedCommandAlreadyRunningAboveTheWaitIsRefused(t *testing.T) {
	command := []string{"sh", "-c", "sleep 30"}
	sum := sha256.Sum256([]byte(strings.Join(command, "\x00")))
	t.Setenv(boundedTag, "4242."+hex.EncodeToString(sum[:])[:12])
	dir := t.TempDir()
	r := start(t, Options{Seconds: 5, Sources: []string{filepath.Join(dir, "in.md") + "=" + filepath.Join(dir, "c")}, Command: command}).wait(t)
	if r.code != CodeRecursive || !strings.Contains(r.errs.String(), "already runs above this process") {
		t.Fatalf("got %d %q", r.code, r.errs.String())
	}
}

type stream struct {
	mu       sync.Mutex
	requests []string
	emit     chan struct{}
}

func (s *stream) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	s.mu.Lock()
	s.requests = append(s.requests, r.URL.RawQuery)
	s.mu.Unlock()
	w.Header().Set("Content-Type", "text/event-stream")
	w.WriteHeader(http.StatusOK)
	w.(http.Flusher).Flush()
	for {
		select {
		case <-r.Context().Done():
			return
		case <-s.emit:
			_, _ = fmt.Fprint(w, "id: 5\nevent: record\ndata: {\"seq\":5}\n\n")
			w.(http.Flusher).Flush()
		}
	}
}

func (s *stream) served() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return len(s.requests)
}

type tailRunner struct {
	mu     sync.Mutex
	calls  []shell.Command
	record string
}

func (r *tailRunner) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.calls = append(r.calls, c)
	return shell.Result{Stdout: r.record}, nil
}

func (r *tailRunner) deliver(record string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.record = record
}

type laneRunner struct {
	mu      sync.Mutex
	records map[string]string
}

func (r *laneRunner) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	return shell.Result{Stdout: r.records[c.Argv[slices.Index(c.Argv, "--to")+1]]}, nil
}

func (r *laneRunner) deliver(records map[string]string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.records = records
}

func (r *tailRunner) seen() []shell.Command {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]shell.Command(nil), r.calls...)
}

func TestACciSourceFollowsTheDaemonStreamAndReadsThroughCciTail(t *testing.T) {
	s := &stream{emit: make(chan struct{}, 1)}
	server := httptest.NewServer(s)
	t.Cleanup(server.Close)
	runner := &tailRunner{}
	r := start(t, Options{Seconds: 10, Sources: []string{"cci:dar:landing-desk"}, Runner: runner, Stream: server.URL, Now: ninePM})
	time.Sleep(armed)

	runner.deliver("#5 9:00 PM NOTE root -> landing-desk go\n")
	s.emit <- struct{}{}
	r.wait(t)

	if r.code != 0 || r.out.String() != "#5 9:00 PM NOTE root -> landing-desk go\n" {
		t.Fatalf("got %d %q %s", r.code, r.out.String(), r.errs.String())
	}
	calls := runner.seen()
	if len(calls) != 2 {
		t.Fatalf("cci tail ran %d times", len(calls))
	}
	want := []string{"cci", "tail", "--drive", "dar", "--cursor", "landing-desk-wake", "--seed", "landing-desk", "--to", "landing-desk", "--mail"}
	if strings.Join(calls[1].Argv, " ") != strings.Join(want, " ") {
		t.Fatalf("got %q", calls[1].Argv)
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if len(s.requests) != 1 || s.requests[0] != "drive=dar&to=landing-desk" {
		t.Fatalf("stream requests %q", s.requests)
	}
}

func TestAnUnrelatedFileInAWatchedDirectoryRunsNoCciTail(t *testing.T) {
	dir := t.TempDir()
	inbox := filepath.Join(dir, "inbox.md")
	if err := os.WriteFile(inbox, nil, 0o600); err != nil {
		t.Fatal(err)
	}
	s := &stream{emit: make(chan struct{}, 1)}
	server := httptest.NewServer(s)
	t.Cleanup(server.Close)
	runner := &tailRunner{}
	r := start(t, Options{Seconds: 1, Sources: []string{inbox + "=" + filepath.Join(dir, "cursor"), "cci:dar:landing-desk"}, Runner: runner, Stream: server.URL, Now: ninePM})
	time.Sleep(armed)
	before := len(runner.seen())
	for i := range 5 {
		if err := os.WriteFile(filepath.Join(dir, fmt.Sprintf("noise-%d", i)), nil, 0o600); err != nil {
			t.Fatal(err)
		}
		time.Sleep(20 * time.Millisecond)
	}
	if after := len(runner.seen()); after != before {
		t.Fatalf("cci tail ran %d times for files no source names", after-before)
	}
	r.wait(t)
}

func TestARecordAddressedToTwoSourcesPrintsOnce(t *testing.T) {
	s := &stream{emit: make(chan struct{}, 1)}
	server := httptest.NewServer(s)
	t.Cleanup(server.Close)
	runner := &tailRunner{}
	r := start(t, Options{Seconds: 10, Sources: []string{"cci:dar:meta", "cci:dar:meta-desk-2"}, Runner: runner, Stream: server.URL, Now: ninePM})
	time.Sleep(armed)

	runner.deliver("@9:00 PM\n5 ask root go\n  its body\n#6@ab12 9:00 PM NOTE root seen\n")
	s.emit <- struct{}{}
	r.wait(t)

	if want := "@9:00 PM\n5 ask root go\n  its body\n#6@ab12 9:00 PM NOTE root seen\n"; r.code != 0 || r.out.String() != want {
		t.Fatalf("got %d %q, want each record once", r.code, r.out.String())
	}
}

func TestAWaitOnANumberedLaneWakesOnMailToItsStem(t *testing.T) {
	s := &stream{emit: make(chan struct{}, 1)}
	server := httptest.NewServer(s)
	t.Cleanup(server.Close)
	runner := &laneRunner{}
	r := start(t, Options{Seconds: 10, Sources: []string{"cci:dar:journal-open-query-bound-1"}, Runner: runner, Stream: server.URL, Now: ninePM})
	time.Sleep(armed)

	verdict := "#7 9:00 PM REVIEW fidelity-desk-29 -> journal-open-query-bound fidelity: ok\n"
	runner.deliver(map[string]string{"journal-open-query-bound": verdict})
	s.emit <- struct{}{}
	r.wait(t)

	if r.code != 0 || r.out.String() != verdict {
		t.Fatalf("got %d %q, want the verdict its grader sent to the stem", r.code, r.out.String())
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if !slices.Contains(s.requests, "drive=dar&to=journal-open-query-bound") || !slices.Contains(s.requests, "drive=dar&to=journal-open-query-bound-1") {
		t.Fatalf("stream requests %q, want the lane and its stem", s.requests)
	}
}

func TestARecordAfterARepeatPrintsUnderItsOwnMinute(t *testing.T) {
	s := &stream{emit: make(chan struct{}, 1)}
	server := httptest.NewServer(s)
	t.Cleanup(server.Close)
	runner := &laneRunner{}
	r := start(t, Options{Seconds: 10, Sources: []string{"cci:dar:meta", "cci:dar:desk"}, Runner: runner, Stream: server.URL, Now: ninePM})
	time.Sleep(armed)

	runner.deliver(map[string]string{
		"meta": "@9:00 PM\n5 ask root go\n",
		"desk": "@8:59 PM\n4 note api early\n@9:00 PM\n5 ask root go\n6 note api later\n",
	})
	s.emit <- struct{}{}
	r.wait(t)

	if want := "@9:00 PM\n5 ask root go\n@8:59 PM\n4 note api early\n@9:00 PM\n6 note api later\n"; r.code != 0 || r.out.String() != want {
		t.Fatalf("got %d %q, want each record under its own minute", r.code, r.out.String())
	}
}

func TestARecordThatArrivedWhileNoWaiterRanWakesTheWait(t *testing.T) {
	s := &stream{emit: make(chan struct{}, 1)}
	server := httptest.NewServer(s)
	t.Cleanup(server.Close)
	runner := &tailRunner{}
	r := start(t, Options{Seconds: 10, Sources: []string{"cci:dar:desk"}, Runner: runner, Stream: server.URL, Now: ninePM})
	time.Sleep(armed)

	runner.deliver("@8:10 PM\n3 ask sender old ask\n")
	s.emit <- struct{}{}
	r.wait(t)

	if want := "@8:10 PM\n3 ask sender old ask\n"; r.code != 0 || r.out.String() != want {
		t.Fatalf("got %d %q, want the unread record to wake the wait", r.code, r.out.String())
	}
}

func TestATrailerWithNoRecordPrintsOnlyAtTheDeadline(t *testing.T) {
	s := &stream{emit: make(chan struct{}, 1)}
	server := httptest.NewServer(s)
	t.Cleanup(server.Close)
	runner := &tailRunner{}
	r := start(t, Options{Seconds: 1, Sources: []string{"cci:dar:desk"}, Runner: runner, Stream: server.URL, Now: ninePM})
	time.Sleep(armed)

	runner.deliver("... 2 more; resume with cci tail\n")
	s.emit <- struct{}{}
	r.wait(t)

	if r.code != 0 || !strings.HasPrefix(r.out.String(), "... 2 more; resume with cci tail\nQUIET ") {
		t.Fatalf("got %d %q, want the trailer and then QUIET", r.code, r.out.String())
	}
}

func TestADaemonRestartPastTheGraceReconnectsInsteadOfEndingTheWait(t *testing.T) {
	s := &stream{emit: make(chan struct{}, 1)}
	server := httptest.NewServer(s)
	t.Cleanup(server.Close)
	var elapsed atomic.Int64
	now := func() time.Time { return time.Unix(0, elapsed.Load()) }
	runner := &tailRunner{}
	r := start(t, Options{Seconds: 10, Sources: []string{"cci:dar:landing-desk"}, Runner: runner, Stream: server.URL, Now: now})
	time.Sleep(armed)

	elapsed.Store(int64(time.Hour))
	server.CloseClientConnections()
	deadline := time.Now().Add(5 * time.Second)
	for s.served() < 2 {
		if time.Now().After(deadline) {
			t.Fatalf("the stream never reconnected: %q", r.errs.String())
		}
		time.Sleep(10 * time.Millisecond)
	}
	runner.deliver("#6 9:01 PM NOTE root -> landing-desk go\n")
	s.emit <- struct{}{}
	r.wait(t)

	if r.code != 0 || r.out.String() != "#6 9:01 PM NOTE root -> landing-desk go\n" {
		t.Fatalf("got %d %q %s", r.code, r.out.String(), r.errs.String())
	}
}

func TestAMissingCciEndsTheWaitWith127AtTheDeadline(t *testing.T) {
	server := httptest.NewServer(&stream{emit: make(chan struct{})})
	t.Cleanup(server.Close)
	runner := &shelltest.Fake{}
	runner.Fail(shell.CodeMissingBinary, "cci: not found", "cci", "tail")
	r := start(t, Options{Seconds: 1, Sources: []string{"cci:dar:desk"}, Runner: runner, Stream: server.URL}).wait(t)
	if r.code != CodeCCIMissing || !strings.Contains(r.errs.String(), "cci not found on PATH") {
		t.Fatalf("got %d %q", r.code, r.errs.String())
	}
}

func TestAMalformedSourceIsAUsageError(t *testing.T) {
	r := start(t, Options{Seconds: 1, Sources: []string{"landing-desk"}}).wait(t)
	if r.code != CodeUsage || !strings.Contains(r.errs.String(), "usage: lr wait") {
		t.Fatalf("got %d %q", r.code, r.errs.String())
	}
	r = start(t, Options{Seconds: 1, Sources: []string{"relative.md=" + filepath.Join(t.TempDir(), "c")}}).wait(t)
	if r.code != CodeUsage || !strings.Contains(r.errs.String(), "not an absolute path") {
		t.Fatalf("got %d %q", r.code, r.errs.String())
	}
}
