package board

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

const linkDrive = "d"

func linkRun(t *testing.T, state string, from []string, fake *shelltest.Fake) {
	t.Helper()
	records, err := client.Tail(context.Background(), client.Query{Drive: linkDrive, Kinds: []string{cardKind}})
	if err != nil {
		t.Fatal(err)
	}
	last := strconv.FormatInt(records[len(records)-1].Seq, 10)
	ctx, stop := context.WithCancel(context.Background())
	defer stop()
	served := make(chan error, 1)
	go func() {
		served <- Link{Runner: fake, Drive: linkDrive, State: state, From: from}.Serve(ctx)
	}()
	deadline := time.Now().Add(30 * time.Second)
	for {
		raw, _ := os.ReadFile(filepath.Join(state, cursorFile))
		if string(raw) == last {
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("the cursor stayed at %q, never reaching card %s", raw, last)
		}
		time.Sleep(20 * time.Millisecond)
	}
	stop()
	if err := <-served; err != nil && !errors.Is(err, context.Canceled) {
		t.Fatalf("Serve = %v", err)
	}
}

func liveDrive(t *testing.T) {
	t.Helper()
	t.Setenv("CCI_HOME", t.TempDir())
	if _, err := client.Post(context.Background(), client.Draft{Drive: linkDrive, Lane: "root", Kind: "note", Text: "a drive has records before its first card"}); err != nil {
		t.Fatal(err)
	}
}

func draw(t *testing.T, key, block string) {
	t.Helper()
	if _, err := DrawCard(context.Background(), linkDrive, "pr-desk", key, "board-1", json.RawMessage(block), ""); err != nil {
		t.Fatalf("DrawCard %s: %v", key, err)
	}
}

func presented(fake *shelltest.Fake) []string {
	calls := fake.Calls()
	seen := make([]string, 0, len(calls))
	for _, call := range calls {
		seen = append(seen, strings.Join(call.Argv, " ")+" <"+call.Stdin)
	}
	return seen
}

func TestCardsPostedWhileTheMacIsAwayArriveInOrderWhenItReturns(t *testing.T) {
	liveDrive(t)
	draw(t, "owed-1", `{"id":"one"}`)
	draw(t, "owed-2", `{"id":"two"}`)
	fake := &shelltest.Fake{}
	fake.Reply("", "cc-present")

	linkRun(t, t.TempDir(), []string{"pr-desk"}, fake)

	want := []string{
		`cc-present update-block - --session board-1 <{"id":"one"}`,
		`cc-present update-block - --session board-1 <{"id":"two"}`,
	}
	if got := presented(fake); !slices.Equal(got, want) {
		t.Fatalf("presented %q, want %q", got, want)
	}
}

func TestARestartResumesFromItsCursorAndNeverRedrawsACard(t *testing.T) {
	liveDrive(t)
	state := t.TempDir()
	draw(t, "owed-1", `{"id":"one"}`)
	first := &shelltest.Fake{}
	first.Reply("", "cc-present")
	linkRun(t, state, []string{"pr-desk"}, first)

	draw(t, "owed-2", `{"id":"two"}`)
	second := &shelltest.Fake{}
	second.Reply("", "cc-present")
	linkRun(t, state, []string{"pr-desk"}, second)

	want := []string{`cc-present update-block - --session board-1 <{"id":"two"}`}
	if got := presented(second); !slices.Equal(got, want) {
		t.Fatalf("after the restart presented %q, want %q", got, want)
	}
}

func TestARepeatOfTheSameKeyIsOneCard(t *testing.T) {
	liveDrive(t)
	draw(t, "owed-1", `{"id":"one"}`)
	draw(t, "owed-1", `{"id":"one"}`)
	records, err := client.Tail(context.Background(), client.Query{Drive: linkDrive, Kinds: []string{cardKind}})
	if err != nil {
		t.Fatal(err)
	}
	if len(records) != 1 {
		t.Fatalf("two posts of one key stored %d records", len(records))
	}
}

func TestARemovalRemovesTheBlock(t *testing.T) {
	liveDrive(t)
	if _, err := DrawCard(context.Background(), linkDrive, "pr-desk", "owed-3", "board-1", nil, "one"); err != nil {
		t.Fatal(err)
	}
	fake := &shelltest.Fake{}
	fake.Reply("", "cc-present")
	linkRun(t, t.TempDir(), []string{"pr-desk"}, fake)
	want := []string{"cc-present remove-block one --session board-1 <"}
	if got := presented(fake); !slices.Equal(got, want) {
		t.Fatalf("presented %q, want %q", got, want)
	}
}

func TestAChangeMustBeOneDrawOrOneRemoval(t *testing.T) {
	liveDrive(t)
	for _, change := range []struct {
		block  json.RawMessage
		remove string
	}{{nil, ""}, {json.RawMessage(`{"id":"one"}`), "one"}} {
		if _, err := DrawCard(context.Background(), linkDrive, "pr-desk", "owed", "board-1", change.block, change.remove); err == nil {
			t.Fatalf("DrawCard(%s, %q) stored a card", change.block, change.remove)
		}
	}
}

func defectsTo(t *testing.T, lane string) []client.Record {
	t.Helper()
	records, err := client.Tail(context.Background(), client.Query{Drive: linkDrive, Kinds: []string{"defect"}})
	if err != nil {
		t.Fatal(err)
	}
	return slices.DeleteFunc(records, func(record client.Record) bool { return !slices.Contains(record.To, lane) })
}

func TestABoardRefusalTellsTheSenderAndTheNextCardStillDraws(t *testing.T) {
	liveDrive(t)
	draw(t, "owed-1", `{"id":"bad"}`)
	draw(t, "owed-2", `{"id":"good"}`)
	fake := &shelltest.Fake{}
	fake.Fail(1, "block has no title", "cc-present", "update-block")

	linkRun(t, t.TempDir(), []string{"pr-desk"}, fake)

	if got := len(fake.Calls()); got != 2 {
		t.Fatalf("a refusal stopped the link after %d calls, want 2", got)
	}
	defects := defectsTo(t, "pr-desk")
	if len(defects) != 2 || !strings.Contains(defects[0].Text, "block has no title") {
		t.Fatalf("defects %+v, want one per refused card naming cc-present's words", defects)
	}
}

func TestACardFromAnotherLaneNeverReachesTheBoard(t *testing.T) {
	liveDrive(t)
	if _, err := DrawCard(context.Background(), linkDrive, "some-lane", "owed-9", "board-1", json.RawMessage(`{"id":"forged"}`), ""); err != nil {
		t.Fatal(err)
	}
	draw(t, "owed-1", `{"id":"good"}`)
	fake := &shelltest.Fake{}
	fake.Reply("", "cc-present")

	linkRun(t, t.TempDir(), []string{"pr-desk"}, fake)

	want := []string{`cc-present update-block - --session board-1 <{"id":"good"}`}
	if got := presented(fake); !slices.Equal(got, want) {
		t.Fatalf("presented %q, want %q", got, want)
	}
	if len(defectsTo(t, "some-lane")) != 1 {
		t.Fatal("the refused lane was not told")
	}
}

func TestAMissingBoardToolStopsTheLinkAndKeepsTheCardOwed(t *testing.T) {
	liveDrive(t)
	state := t.TempDir()
	draw(t, "owed-1", `{"id":"one"}`)
	fake := &shelltest.Fake{}
	link := Link{Runner: fake, Drive: linkDrive, State: state, From: []string{"pr-desk"}}
	ctx, stop := context.WithTimeout(context.Background(), 30*time.Second)
	defer stop()
	if err := link.Serve(ctx); err == nil || errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("Serve = %v, want the missing tool's error", err)
	}
	if _, err := os.Stat(filepath.Join(state, cursorFile)); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("the cursor moved past an undelivered card: %v", err)
	}
}
