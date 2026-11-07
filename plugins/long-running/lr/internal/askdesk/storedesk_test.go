package askdesk

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"maps"
	"os"
	"slices"
	"strconv"
	"strings"
	"sync"
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

func TestTheStoreDeskRunsOwnerPromptsSoASpriteLaneReadsTheMacsCaptures(t *testing.T) {
	var ran []string
	read := StoreRuns("/mac/lr.json", func(_ context.Context, argv []string, _ string) (string, string, int, error) {
		ran = argv
		return "{\"digest\": \"72d2b9383e1ac624\"}\n", "", 0, nil
	})

	answer := read(context.Background(), `{"verb": "owner prompts", "args": ["--since=629293ae14388dc9", "--"]}`)

	if want := []string{"owner", "prompts", "--config", "/mac/lr.json", "--since=629293ae14388dc9", "--"}; !slices.Equal(ran, want) || strings.HasPrefix(answer.Head, failedRead) {
		t.Fatalf("the desk ran %q and answered %q, want %q run", ran, answer.Head, want)
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

func TestAStoreDeskAnswerRepliesToItsAskSoEveryNodeRoutesItToTheAskerByThatLink(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	ask, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: "next-hold-fix-2", Kind: AskKind, Text: `{"verb":"lanes handoff-registry","args":["--","next-hold-fix-2"],"lane":"next-hold-fix-2"}`, To: []string{StoreDesk}, Force: true})
	if err != nil {
		t.Fatal(err)
	}
	desk := Desk{Lane: StoreDesk, Drive: "d", Bodies: t.TempDir(), Now: time.Now, Log: io.Discard, Read: StoreRuns("/mac/lr.json", func(context.Context, []string, string) (string, string, int, error) {
		return "{}\n", "", 0, nil
	})}

	if _, err := desk.Pass(context.Background(), client.Query{Drive: "d", Since: time.Now().Add(-CatchUp), Kinds: []string{AskKind}}); err != nil {
		t.Fatal(err)
	}

	answers, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{AnswerKind}})
	if err != nil {
		t.Fatal(err)
	}
	if len(answers) != 1 || answers[0].Re != ask.Seq || answers[0].Resolves != ask.Seq {
		t.Fatalf("ask #%d answered by %+v: want one answer that replies to and resolves it, since a Sprite whose registry gives next-hold-fix to another lane drops an answer addressed by name alone", ask.Seq, answers)
	}
}

func TestAHandoffAskIsAnsweredWhileTheDeskRunsAnotherVerbWithOneOrAThousandQueuedBehindIt(t *testing.T) {
	registry := `{"brief":"brief.md","notice_to":"root"}` + "\n"
	ranBeforeAnswer := map[int]map[string]int{}
	for _, queued := range []int{1, 1000} {
		t.Run(strconv.Itoa(queued), func(t *testing.T) {
			t.Setenv("CCI_HOME", t.TempDir())
			for n := range queued {
				if _, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: "lander-1", Kind: AskKind, Text: fmt.Sprintf(`{"verb":"landing gate","args":["--","%d"],"lane":"lander-1"}`, 50000+n), To: []string{StoreDesk}, Force: true}); err != nil {
					t.Fatal(err)
				}
			}
			if asks, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{AskKind}}); err != nil || len(asks) != queued {
				t.Fatalf("the store holds %d asks, %v: want the %d posted", len(asks), err, queued)
			}
			var mu sync.Mutex
			ran := map[string]int{}
			gating := make(chan struct{})
			ctx, cancel := context.WithCancel(context.Background())
			stopped := make(chan struct{})
			go func() {
				Desk{Lane: StoreDesk, Drive: "d", Bodies: t.TempDir(), Now: time.Now, Log: io.Discard, Apart: AsksHandoff, Read: StoreRuns("/mac/lr.json", func(ctx context.Context, argv []string, _ string) (string, string, int, error) {
					verb := strings.Join(argv[:2], " ")
					mu.Lock()
					ran[verb]++
					first := ran[verb] == 1
					mu.Unlock()
					if verb == HandoffRegistryVerb {
						return registry, "", 0, nil
					}
					if first {
						close(gating)
					}
					<-ctx.Done()
					return "", "", 0, ctx.Err()
				})}.Follow(ctx)
				close(stopped)
			}()
			t.Cleanup(func() {
				cancel()
				<-stopped
			})
			asking, stop := context.WithTimeout(context.Background(), 30*time.Second)
			defer stop()
			select {
			case <-gating:
			case <-asking.Done():
				t.Fatalf("the desk started none of the %d landing gate asks in 30s", queued)
			}

			var out, errs bytes.Buffer
			err := RunOnMac(asking, LiveCCI{}, "d", Asked{Verb: HandoffRegistryVerb, Args: []string{"--", "next-hold-fix-2"}, Lane: "next-hold-fix-2"}, &out, &errs)
			mu.Lock()
			ranBeforeAnswer[queued] = maps.Clone(ran)
			mu.Unlock()

			if err != nil || out.String() != registry {
				t.Fatalf("with %d landing gate asks queued, next-hold-fix-2 read %q, %v: want the registry while the gate still runs", queued, out.String(), err)
			}
		})
	}
	want := map[string]int{"landing gate": 1, HandoffRegistryVerb: 1}
	if !maps.Equal(ranBeforeAnswer[1], want) || !maps.Equal(ranBeforeAnswer[1000], want) {
		t.Fatalf("the desk ran %v before answering the handoff: want %v at either queue length", ranBeforeAnswer, want)
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

func TestARefusedAskIsAnsweredButStaysOpenForTheDesksLaneToServe(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	ask, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: "dd-339598808-fix", Kind: AskKind, Text: "retry job 0199 on build 27723", To: []string{"bk-desk"}, Force: true})
	if err != nil {
		t.Fatal(err)
	}
	desk := Desk{Lane: "bk-desk", Drive: "d", Bodies: t.TempDir(), Now: time.Now, Log: io.Discard, Read: func(context.Context, string) Reply {
		return Reply{Head: "bk read refused", Body: "retry is no read", Refused: true}
	}}

	if _, err := desk.Pass(context.Background(), client.Query{Drive: "d", Since: time.Now().Add(-CatchUp), Kinds: []string{AskKind}}); err != nil {
		t.Fatal(err)
	}

	answers, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{AnswerKind, PartKind}})
	if err != nil {
		t.Fatal(err)
	}
	if len(answers) != 1 || answers[0].Kind != PartKind || answers[0].Re != ask.Seq || answers[0].Resolves != 0 {
		t.Fatalf("ask #%d answered by %+v: want one note that replies to it and resolves nothing", ask.Seq, answers)
	}
	asks, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{AskKind}})
	if err != nil || len(asks) != 1 || asks[0].Status != openStatus {
		t.Fatalf("asks = %+v, %v: want the refused ask still open", asks, err)
	}
}

func TestADeskAnswersWhatItHoldsThenEndsOnceANewerLrIsInstalled(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	ask, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: "shared-open-speed-4", Kind: AskKind, Text: "bk retry test 119497 01a1b75b-cf2b-4f1e-9a52-6a1d2b3c4d5e", To: []string{"bk-desk"}, Force: true})
	if err != nil {
		t.Fatal(err)
	}
	installs := 0
	desk := Desk{Lane: "bk-desk", Drive: "d", Bodies: t.TempDir(), Now: time.Now, Log: io.Discard, Read: func(context.Context, string) Reply {
		return Reply{Head: "retried"}
	}, Released: func() bool {
		installs++
		return true
	}}

	if err := desk.Serve(context.Background()); !errors.Is(err, ErrReleased) || installs != 1 {
		t.Fatalf("Serve ended with %v after %d install checks, want ErrReleased after one", err, installs)
	}
	answers, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{AnswerKind}})
	if err != nil || len(answers) != 1 || answers[0].Re != ask.Seq {
		t.Fatalf("answers = %+v, %v: want ask #%d answered before the desk ends", answers, err, ask.Seq)
	}
}
