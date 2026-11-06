package grading

import (
	"context"
	"errors"
	"fmt"
	"io"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/askdesk"
)

const spriteLane = "fidelity-shard-8"

type countedCCI struct {
	posts, follows int
}

func (c *countedCCI) Post(ctx context.Context, d client.Draft) (client.Record, error) {
	c.posts++
	return client.Post(ctx, d)
}

func (c *countedCCI) Follow(ctx context.Context, w client.Watch, emit func(client.Record) error) error {
	c.follows++
	return client.Follow(ctx, w, emit)
}

func servedDesk(t *testing.T, reads map[string]askdesk.Serve) {
	t.Helper()
	t.Setenv("CCI_HOME", t.TempDir())
	post(t, client.Draft{Lane: "earlier", Kind: askdesk.AskKind, Text: "bk build monorepo 1", To: []string{"bk-desk"}, Force: true})
	ctx, cancel := context.WithCancel(context.Background())
	stopped := make(chan struct{})
	go func() {
		askdesk.Desk{Lane: askdesk.StoreDesk, Drive: testDrive, Bodies: t.TempDir(), Now: time.Now, Log: io.Discard, Read: askdesk.StoreReads(reads)}.Follow(ctx)
		close(stopped)
	}()
	t.Cleanup(func() {
		cancel()
		<-stopped
	})
}

func askedFromASprite(t *testing.T, heads int) (*countedCCI, string) {
	t.Helper()
	now := time.Now()
	servedDesk(t, map[string]askdesk.Serve{queueRead: func(_ context.Context, args []string, out, _ io.Writer) error {
		if got := strings.Join(args, " "); got != "fidelity --shard even --owed" {
			return fmt.Errorf("the desk passed %q", got)
		}
		for i := range heads {
			fmt.Fprintln(out, QueueRow{Number: 2 * i, Head: queueHead, Owed: true, Pipeline: PassReader, Lane: "lane"}.line(now))
		}
		return nil
	}})
	bus := &countedCCI{}
	var out strings.Builder
	if err := askdesk.AskMac(context.Background(), bus, testDrive, spriteLane, queueQuery{grader: "fidelity", shard: shardEven, owed: true}.ask(), &out); err != nil {
		t.Fatal(err)
	}
	return bus, out.String()
}

func TestASpriteQueueReadMakesTheSameRelayCallsAtOneAndAThousandHeads(t *testing.T) {
	counted := map[int]countedCCI{}
	for _, heads := range []int{1, 1000} {
		t.Run(fmt.Sprint(heads), func(t *testing.T) {
			bus, printed := askedFromASprite(t, heads)
			if lines := strings.Count(printed, "\n"); lines != heads {
				t.Fatalf("printed %d rows for %d owed heads: %q", lines, heads, printed[:min(len(printed), 200)])
			}
			if !strings.HasPrefix(printed, "#0 "+short(queueHead)+" owed") {
				t.Fatalf("printed %q, want the Mac's rows", printed[:min(len(printed), 200)])
			}
			counted[heads] = *bus
		})
	}
	if counted[1] != counted[1000] || counted[1] != (countedCCI{posts: 1, follows: 1}) {
		t.Fatalf("relay calls at 1 head %+v and at 1,000 heads %+v, want one post and one follow at both", counted[1], counted[1000])
	}
}

func TestAReadTheMacFailsReturnsItsErrorAndPrintsNoRows(t *testing.T) {
	servedDesk(t, map[string]askdesk.Serve{queueRead: func(context.Context, []string, io.Writer, io.Writer) error {
		return errors.New("runner.json has no fidelity desk")
	}})
	var out strings.Builder
	err := askdesk.AskMac(context.Background(), &countedCCI{}, testDrive, spriteLane, queueQuery{grader: "fidelity"}.ask(), &out)
	if err == nil || !strings.Contains(err.Error(), "runner.json has no fidelity desk") || out.Len() != 0 {
		t.Fatalf("err %v printed %q, want the Mac's error and no rows", err, out.String())
	}
}

func TestTheStoreDeskRefusesAReadItDoesNotServeAndNamesTheOnesItDoes(t *testing.T) {
	served := func(context.Context, []string, io.Writer, io.Writer) error { return nil }
	servedDesk(t, map[string]askdesk.Serve{queueRead: served, "board rows": served})
	for _, read := range []string{"lanes", "grade first 12", "board"} {
		var out strings.Builder
		err := askdesk.AskMac(context.Background(), &countedCCI{}, testDrive, spriteLane, read, &out)
		if err == nil || !strings.Contains(err.Error(), "serves only lr board rows, lr grade queue") || out.Len() != 0 {
			t.Fatalf("%q returned %v printed %q, want a refusal naming the reads the desk serves", read, err, out.String())
		}
	}
}

func TestAnEmptyReadAnswersWithNoBodyFileSoASpritePrintsNothing(t *testing.T) {
	servedDesk(t, map[string]askdesk.Serve{queueRead: func(context.Context, []string, io.Writer, io.Writer) error { return nil }})
	var out strings.Builder
	if err := askdesk.AskMac(context.Background(), &countedCCI{}, testDrive, spriteLane, queueQuery{grader: "fidelity", owed: true}.ask(), &out); err != nil || out.Len() != 0 {
		t.Fatalf("err %v printed %q, want an empty queue printed as nothing", err, out.String())
	}
	answers, err := client.Tail(context.Background(), client.Query{Drive: testDrive, Kinds: []string{askdesk.AnswerKind}})
	if err != nil {
		t.Fatal(err)
	}
	if len(answers) != 1 || answers[0].Refs.Path != "" {
		t.Fatalf("answers %+v, want one answer naming no body file, which the relay drops when empty and a Sprite then cannot read", answers)
	}
}
