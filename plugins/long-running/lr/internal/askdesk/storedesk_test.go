package askdesk

import (
	"bytes"
	"context"
	"io"
	"os"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
)

func TestTheStoreDeskRunsOnlyAListedVerbAndNamesTheCardPathsWhenAskedForACard(t *testing.T) {
	var ran [][]string
	read := StoreRuns("/mac/lr.json", func(_ context.Context, argv []string, _ string) (string, string, int, error) {
		ran = append(ran, argv)
		return "", "", 0, nil
	})

	card := read(context.Background(), "Please make the owner tap card for landing diagnostic PR #51773 at 3a2d276")
	unlisted := read(context.Background(), `{"verb": "update render", "args": ["--publish", "--"]}`)
	listed := read(context.Background(), `{"verb": "board waits", "args": ["--pr=51766", "--"]}`)

	if !strings.HasPrefix(card.Head, failedRead) || !strings.HasSuffix(card.Head, cardPaths) {
		t.Fatalf("a card ask was answered %q, want a refusal naming the card paths", card.Head)
	}
	if !strings.HasPrefix(unlisted.Head, failedRead) || strings.Contains(unlisted.Head, cardPaths) {
		t.Fatalf("an unlisted verb was answered %q, want a plain refusal", unlisted.Head)
	}
	if want := [][]string{{"board", "waits", "--config", "/mac/lr.json", "--pr=51766", "--"}}; !slices.EqualFunc(ran, want, slices.Equal) || strings.HasPrefix(listed.Head, failedRead) {
		t.Fatalf("the desk ran %q and answered %q, want only %q", ran, listed.Head, want)
	}
}

func TestANumberedLaneReadsTheStoreDesksAnswerAddressedToIt(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	registry := `{"brief":"brief.md","notice_to":"root"}` + "\n"
	ctx, cancel := context.WithCancel(context.Background())
	stopped := make(chan struct{})
	go func() {
		Desk{Lane: StoreDesk, Drive: "d", Bodies: t.TempDir(), Now: time.Now, Log: io.Discard, Read: StoreRuns("/mac/lr.json", func(context.Context, []string, string) (string, string, int, error) {
			return registry, "", 0, nil
		})}.Follow(ctx)
		close(stopped)
	}()
	t.Cleanup(func() {
		cancel()
		<-stopped
	})

	asking, stop := context.WithTimeout(context.Background(), 30*time.Second)
	defer stop()
	var out, errs bytes.Buffer
	err := RunOnMac(asking, LiveCCI{}, "d", Asked{Verb: HandoffRegistryVerb, Args: []string{"--", "test-sleeps-ts-1"}, Lane: "test-sleeps-ts-1"}, &out, &errs)

	if err != nil || out.String() != registry {
		t.Fatalf("test-sleeps-ts-1 read %q, %v: want the answer the desk addressed to it", out.String(), err)
	}
}

func TestAStoreDeskAnswerPastTheRelaysBodyCapReachesTheSpriteWholeInBodiesTheRelayCarries(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	registry := `{"orders":["` + strings.Repeat("m", relayBody-13) + "…" + strings.Repeat("m704 Nov 6 1:32 AM: one open owner order of a standing lane", 1500) + `"],"notice_to":"root"}` + "\n"
	ctx, cancel := context.WithCancel(context.Background())
	stopped := make(chan struct{})
	go func() {
		Desk{Lane: StoreDesk, Drive: "d", Bodies: t.TempDir(), Now: time.Now, Log: io.Discard, Read: StoreRuns("/mac/lr.json", func(context.Context, []string, string) (string, string, int, error) {
			return registry, "", 0, nil
		})}.Follow(ctx)
		close(stopped)
	}()
	t.Cleanup(func() {
		cancel()
		<-stopped
	})

	var out, errs bytes.Buffer
	err := RunOnMac(context.Background(), LiveCCI{}, "d", Asked{Verb: HandoffRegistryVerb, Args: []string{"--", "pr-unblock"}, Lane: "pr-unblock"}, &out, &errs)

	if err != nil || out.String() != registry {
		t.Fatalf("read %d of %d bytes, %v: want the whole registry", out.Len(), len(registry), err)
	}
	answers, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{AnswerKind, PartKind}})
	if err != nil {
		t.Fatal(err)
	}
	if len(answers) < 2 {
		t.Fatalf("the desk posted %d records for a %d-byte answer, want it in parts", len(answers), len(registry))
	}
	for _, answer := range answers {
		body, err := os.Stat(answer.Refs.Path)
		if err != nil {
			t.Fatal(err)
		}
		if body.Size() > relayBody {
			t.Errorf("answer #%d carries a %d-byte body: the relay cuts a body past %d bytes", answer.Seq, body.Size(), relayBody)
		}
	}
}
