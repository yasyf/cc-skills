package lanes

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	notesLogin    = "mac-github-login"
	notesCheckout = "/home/sprite/monorepo"
	notesSprite   = "pool-box"
)

type notesRunner func(shell.Command) (shell.Result, error)

func (r notesRunner) Run(_ context.Context, c shell.Command) (shell.Result, error) { return r(c) }

func notesEnv(t *testing.T, lane, receipt string, runner notesRunner) Env {
	t.Helper()
	env := Env{Runner: runner, Local: t.TempDir(), Config: config.Config{OrcaRun: "run_1"}}
	if err := os.MkdirAll(env.receipts(), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(env.receipts(), lane+".json"), []byte(receipt), 0o600); err != nil {
		t.Fatal(err)
	}
	return env
}

func spriteReceipt() string {
	return fmt.Sprintf(`{"result":{"server":{"name":%q},"effects":[{"kind":"terminal","id":"t"},{"kind":"worktree","id":"env::%s"}]}}`, notesSprite, notesCheckout)
}

func givenToken() (string, error) { return notesLogin, nil }

func TestALaneOnTheMacPublishesNothingAndReadsNoToken(t *testing.T) {
	env := notesEnv(t, "desk", `{"result":{"effects":[]}}`, func(c shell.Command) (shell.Result, error) {
		t.Fatalf("ran %v for a lane with no Sprite", c.Argv)
		return shell.Result{}, nil
	})
	if err := env.publishNotes(context.Background(), func() (string, error) { return "", errors.New("read a token") }, "desk"); err != nil {
		t.Fatal(err)
	}
}

func TestASpriteLanesNotesSyncOnItsBoxWithTheTokenOnStdinAlone(t *testing.T) {
	var ran shell.Command
	env := notesEnv(t, lane2, spriteReceipt(), func(c shell.Command) (shell.Result, error) {
		ran = c
		return shell.Result{Stdout: `{"pushed":1}`}, nil
	})
	if err := env.publishNotes(context.Background(), givenToken, lane2); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(strings.Join(ran.Argv, " "), notesLogin) {
		t.Fatalf("the token is in the arguments: %v", ran.Argv)
	}
	equal(t, ran.Stdin, notesLogin+"\n")
	equal(t, ran.Argv[:4], []string{"sprite", "exec", "-s", notesSprite})
	equal(t, ran.Argv[len(ran.Argv)-1], notesCheckout)
}

func TestANoteADefaultKeepsOnTheSpriteFailsItsSync(t *testing.T) {
	env := notesEnv(t, lane2, spriteReceipt(), func(shell.Command) (shell.Result, error) {
		return shell.Result{Stdout: `{"withheld":1,"defaulted":[{"kind":"note","id":"a24d471","reason":"label lane-brief"}]}`}, nil
	})
	err := env.publishNotes(context.Background(), givenToken, lane2)
	if err == nil {
		t.Fatal("a default-withheld note read as published")
	}
	contains(t, err.Error(), "note a24d471 (label lane-brief)")
	contains(t, err.Error(), "ccn local unmark")
}

func TestAFailedSyncOnASpriteStillListedIsItsOwnError(t *testing.T) {
	env := notesEnv(t, lane2, spriteReceipt(), func(c shell.Command) (shell.Result, error) {
		if c.Argv[1] == "api" {
			return shell.Result{Stdout: fmt.Sprintf(`{"sprites":[{"name":%q}]}`, notesSprite)}, nil
		}
		return shell.Result{}, &shell.ExitError{Argv: []string{"sprite"}, Code: 1, Stderr: "could not read Username"}
	})
	err := env.publishNotes(context.Background(), givenToken, lane2)
	if err == nil || errors.Is(err, ErrSpriteGone) {
		t.Fatalf("publish = %v, want the sync failure", err)
	}
	contains(t, err.Error(), "could not read Username")
}

func TestAFailedSyncOnASpriteNoLongerListedSaysItIsGone(t *testing.T) {
	env := notesEnv(t, lane2, spriteReceipt(), func(c shell.Command) (shell.Result, error) {
		if c.Argv[1] == "api" {
			return shell.Result{Stdout: `{"sprites":[]}`}, nil
		}
		return shell.Result{}, &shell.ExitError{Argv: []string{"sprite"}, Code: 1, Stderr: "not found"}
	})
	if err := env.publishNotes(context.Background(), givenToken, lane2); !errors.Is(err, ErrSpriteGone) {
		t.Fatalf("publish = %v, want ErrSpriteGone", err)
	}
}

func TestALaneWhoseNotesCannotPublishIsHeldThenRetiresOnceTheyDo(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.notes = map[string]error{lane2: errors.New("refs differing from origin: refs/cc-notes/notes/a24d471")}
	f.feed = append(f.feed, rec(2, lane2, "done", t0, "research finished", Root))
	f.peers = []Row{who(lane2, true, t0)}

	tick(t, s, f, t0.Add(mins(1)))

	equal(t, row(t, s, lane2).State, store.LaneHeld)
	blockers := f.ofKind("blocker")
	equal(t, len(blockers), 1)
	contains(t, blockers[0].Text, "refs/cc-notes/notes/a24d471")
	equal(t, blockers[0].To, []string{Root, lane2})

	f.notes = nil
	f.feed = append(f.feed, rec(3, lane2, "done", t0.Add(mins(2)), "research finished", Root))
	tick(t, s, f, t0.Add(mins(3)))

	equal(t, row(t, s, lane2).State, store.LaneRetired)
}

func TestAStandDownByOrderRetiresALaneWhoseNotesCannotPublishAndSaysSo(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.notes = map[string]error{lane2: errors.New("Storage not ready")}
	f.feed = append(f.feed, ask(2, "stand-down", lane2, t0, nil, "", map[string]string{"last-read": "1"}))
	f.peers = []Row{who(lane2, true, t0)}

	tick(t, s, f, t0.Add(mins(1)))

	equal(t, row(t, s, lane2).State, store.LaneRetired)
	blockers := f.ofKind("blocker")
	equal(t, len(blockers), 1)
	contains(t, blockers[0].Text, "Storage not ready")
}

func TestALaneWhoseSpriteIsGoneRetiresAndSaysItsNotesAreLost(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.notes = map[string]error{lane2: fmt.Errorf("%w: %s", ErrSpriteGone, notesSprite)}
	f.feed = append(f.feed, rec(2, lane2, "done", t0, "research finished", Root))
	f.peers = []Row{who(lane2, true, t0)}

	tick(t, s, f, t0.Add(mins(1)))

	equal(t, row(t, s, lane2).State, store.LaneRetired)
	blockers := f.ofKind("blocker")
	equal(t, len(blockers), 1)
	contains(t, blockers[0].Text, "lost")
	contains(t, blockers[0].Text, notesSprite)
}

func TestARecordFromALiveLanePublishesItsNotesOnceAndAFailureBlocksOnce(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.published = nil
	f.notes = map[string]error{lane2: errors.New("Storage not ready")}
	f.feed = append(f.feed, rec(2, lane2, "report", t0, "report, ccn a24d471", Root), rec(3, lane2, "note", t0, "and another", Root))
	f.peers = []Row{who(lane2, true, t0)}

	tick(t, s, f, t0.Add(mins(1)))

	equal(t, f.published, []string{lane2})
	equal(t, row(t, s, lane2).State, store.LaneDesired)
	equal(t, len(f.ofKind("blocker")), 1)
	contains(t, f.ofKind("blocker")[0].Text, "no other lane can read them")

	f.feed = append(f.feed, rec(4, lane2, "report", t0.Add(mins(2)), "still at it", Root))
	tick(t, s, f, t0.Add(mins(3)))
	equal(t, len(f.ofKind("blocker")), 1)

	f.notes = nil
	f.feed = append(f.feed, rec(5, lane2, "report", t0.Add(mins(4)), "fixed", Root))
	tick(t, s, f, t0.Add(mins(5)))
	f.notes = map[string]error{lane2: errors.New("Storage not ready")}
	f.feed = append(f.feed, rec(6, lane2, "report", t0.Add(mins(6)), "broken again", Root))
	tick(t, s, f, t0.Add(mins(7)))
	equal(t, len(f.ofKind("blocker")), 2)
}

func TestATickWithNoRecordsPublishesNothing(t *testing.T) {
	s := memory(t)
	f := adopted(t, s, writeBrief(t), "")
	f.published = nil
	f.peers = []Row{who(lane2, true, t0)}

	tick(t, s, f, t0.Add(mins(1)))

	equal(t, len(f.published), 0)
}

func notesRepo(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	notesGit(t, dir, "init", "-q")
	notesGit(t, dir, "-c", "user.name=n", "-c", "user.email=n@example.com", "commit", "-q", "--allow-empty", "-m", "one")
	return dir
}

func notesGit(t *testing.T, dir string, args ...string) string {
	t.Helper()
	cmd := exec.Command("git", append([]string{"-C", dir}, args...)...)
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("git %v: %v\n%s", args, err, out)
	}
	return strings.TrimSpace(string(out))
}

func fakeCCN(t *testing.T, exit int, stdout string) (bin, called string) {
	t.Helper()
	bin = t.TempDir()
	called = filepath.Join(bin, "called")
	body := fmt.Sprintf("#!/bin/sh\nprintf 'token=%%s helper=%%s args=%%s\\n' \"$NOTES_TOKEN\" \"$GIT_CONFIG_VALUE_1\" \"$*\" >> %q\nprintf '%%s' %q\n[ %d = 0 ] || echo 'sync failed' >&2\nexit %d\n", called, stdout, exit, exit)
	if err := os.WriteFile(filepath.Join(bin, "ccn"), []byte(body), 0o700); err != nil { //nolint:gosec // the stub has to execute
		t.Fatal(err)
	}
	return bin, called
}

func runScript(t *testing.T, script, bin, dir string) (stdout, stderr string, err error) {
	t.Helper()
	cmd := exec.Command("sh", "-c", script, "sh", dir)
	cmd.Env = append(os.Environ(), "PATH="+bin+":"+os.Getenv("PATH"))
	cmd.Stdin = strings.NewReader(notesLogin + "\n")
	var out, errOut strings.Builder
	cmd.Stdout, cmd.Stderr = &out, &errOut
	err = cmd.Run()
	return out.String(), errOut.String(), err
}

func TestTheScriptSkipsARefOriginAlreadyHasAtOrAheadOfItsTip(t *testing.T) {
	dir := notesRepo(t)
	notesGit(t, dir, "update-ref", "refs/cc-notes/notes/same", "HEAD")
	notesGit(t, dir, "update-ref", "refs/cc-notes-sync/origin/notes/same", "HEAD")
	notesGit(t, dir, "update-ref", "refs/cc-notes/notes/behind", "HEAD")
	notesGit(t, dir, "-c", "user.name=n", "-c", "user.email=n@example.com", "commit", "-q", "--allow-empty", "-m", "two")
	notesGit(t, dir, "update-ref", "refs/cc-notes-sync/origin/notes/behind", "HEAD")
	bin, called := fakeCCN(t, 0, `{}`)

	if _, _, err := runScript(t, publishNotesScript, bin, dir); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(called); !errors.Is(err, os.ErrNotExist) {
		seen, _ := os.ReadFile(called)
		t.Fatalf("ccn sync ran with nothing to publish: %s", seen)
	}
}

func TestTheScriptSyncsARefOriginLacksByItselfWithTheTokenInTheEnvironmentOnly(t *testing.T) {
	dir := notesRepo(t)
	notesGit(t, dir, "update-ref", "refs/cc-notes/notes/mine", "HEAD")
	notesGit(t, dir, "update-ref", "refs/cc-notes/notes/same", "HEAD")
	notesGit(t, dir, "update-ref", "refs/cc-notes-sync/origin/notes/same", "HEAD")
	bin, called := fakeCCN(t, 0, `{"pushed":1}`)

	stdout, _, err := runScript(t, publishNotesScript, bin, dir)
	if err != nil {
		t.Fatal(err)
	}
	equal(t, stdout, `{"pushed":1}`)
	seen, err := os.ReadFile(called)
	if err != nil {
		t.Fatal(err)
	}
	contains(t, string(seen), "token="+notesLogin)
	contains(t, string(seen), `password=$NOTES_TOKEN`)
	contains(t, string(seen), "args=sync --prefix refs/cc-notes/notes/mine --json")
	if strings.Contains(string(seen), "helper="+notesLogin) {
		t.Fatalf("the helper holds the token itself: %s", seen)
	}
}

func TestTheScriptSyncsARefThatDivergedFromOrigin(t *testing.T) {
	dir := notesRepo(t)
	first := notesGit(t, dir, "rev-parse", "HEAD")
	notesGit(t, dir, "-c", "user.name=n", "-c", "user.email=n@example.com", "commit", "-q", "--allow-empty", "-m", "theirs")
	notesGit(t, dir, "update-ref", "refs/cc-notes-sync/origin/notes/shared", "HEAD")
	notesGit(t, dir, "reset", "-q", "--hard", first)
	notesGit(t, dir, "-c", "user.name=n", "-c", "user.email=n@example.com", "commit", "-q", "--allow-empty", "-m", "mine")
	notesGit(t, dir, "update-ref", "refs/cc-notes/notes/shared", "HEAD")
	bin, called := fakeCCN(t, 0, `{}`)

	if _, _, err := runScript(t, publishNotesScript, bin, dir); err != nil {
		t.Fatal(err)
	}
	seen, err := os.ReadFile(called)
	if err != nil {
		t.Fatal("no sync for a ref with a local commit origin lacks:", err)
	}
	contains(t, string(seen), "args=sync --prefix refs/cc-notes/notes/shared --json")
}

func TestTheScriptNamesTheRefThatFailedAndStillTriesTheRest(t *testing.T) {
	dir := notesRepo(t)
	notesGit(t, dir, "update-ref", "refs/cc-notes/notes/a24d471", "HEAD")
	notesGit(t, dir, "update-ref", "refs/cc-notes/notes/b35e582", "HEAD")
	bin, called := fakeCCN(t, 1, `{}`)

	_, stderr, err := runScript(t, publishNotesScript, bin, dir)
	var exit *exec.ExitError
	if !errors.As(err, &exit) || exit.ExitCode() != 1 {
		t.Fatalf("exit = %v, want 1", err)
	}
	contains(t, stderr, "sync failed")
	contains(t, stderr, "refs/cc-notes/notes/a24d471 did not reach origin")
	contains(t, stderr, "refs/cc-notes/notes/b35e582 did not reach origin")
	seen, err := os.ReadFile(called)
	if err != nil {
		t.Fatal(err)
	}
	contains(t, string(seen), "b35e582")
}

func TestEveryRefsReportIsReadForWhatADefaultKept(t *testing.T) {
	env := notesEnv(t, lane2, spriteReceipt(), func(shell.Command) (shell.Result, error) {
		return shell.Result{Stdout: `{"pushed":1}` + "\n" + `{"defaulted":[{"kind":"log","id":"b35e582","reason":"attachment run.log matches *.log"}]}` + "\n"}, nil
	})
	err := env.publishNotes(context.Background(), givenToken, lane2)
	if err == nil {
		t.Fatal("the second ref's default read as published")
	}
	contains(t, err.Error(), "log b35e582")
}

func TestASpriteWithNothingToPublishPrintsNothingAndPasses(t *testing.T) {
	env := notesEnv(t, lane2, spriteReceipt(), func(shell.Command) (shell.Result, error) { return shell.Result{}, nil })
	if err := env.publishNotes(context.Background(), givenToken, lane2); err != nil {
		t.Fatal(err)
	}
}
