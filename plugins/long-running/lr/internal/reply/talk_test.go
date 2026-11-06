package reply

import (
	"context"
	"encoding/json"
	"fmt"
	"regexp"
	"slices"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/talkboard"
)

const (
	boardHost  = "http://board/p/"
	presentCap = 1 << 20
)

var archiveLink = regexp.MustCompile(`\(` + regexp.QuoteMeta(boardHost) + `([^)]+)\)`)

type talkBoards struct {
	*shelltest.Fake
	docs  map[string][]json.RawMessage
	calls int
}

func newTalkBoards(blocks ...string) *talkBoards {
	live := make([]json.RawMessage, 0, 1+len(blocks))
	live = append(live, json.RawMessage(`{"id":"talk","type":"section","title":"Talk"}`))
	for _, b := range blocks {
		live = append(live, json.RawMessage(b))
	}
	return &talkBoards{Fake: scripted(), docs: map[string][]json.RawMessage{"talk-d": live}}
}

func (b *talkBoards) Run(ctx context.Context, c shell.Command) (shell.Result, error) {
	at := slices.Index(c.Argv, "--session")
	if c.Argv[0] != "cc-present" || at < 0 || !strings.HasPrefix(c.Argv[at+1], "talk-") {
		return b.Fake.Run(ctx, c)
	}
	b.calls++
	session := c.Argv[at+1]
	refuse := func(why string) (shell.Result, error) {
		return shell.Result{Code: 1}, &shell.ExitError{Argv: c.Argv, Code: 1, Stderr: "cc-present: " + why}
	}
	switch c.Argv[1] {
	case "outcomes":
		out, err := json.Marshal(map[string]any{"rev": b.calls, "doc": map[string]any{"version": 1, "title": "", "blocks": b.docs[session]}})
		return shell.Result{Stdout: string(out)}, err
	case "update-block":
		blocks := slices.Clone(b.docs[session])
		if i := slices.IndexFunc(blocks, func(raw json.RawMessage) bool { return blockID(raw) == blockID(json.RawMessage(c.Stdin)) }); i >= 0 {
			blocks[i] = json.RawMessage(c.Stdin)
		} else {
			blocks = append(blocks, json.RawMessage(c.Stdin))
		}
		if size := docSize(blocks); size > presentCap {
			return refuse(fmt.Sprintf("doc is %d bytes, exceeds %d", size, presentCap))
		}
		b.docs[session] = blocks
	case "start", "push":
		if c.Argv[1] == "start" && b.docs[session] != nil {
			return refuse("session " + session + " has an open artifact")
		}
		var doc struct {
			Blocks []json.RawMessage `json:"blocks"`
		}
		if err := json.Unmarshal([]byte(c.Stdin), &doc); err != nil {
			return refuse(err.Error())
		}
		b.docs[session] = doc.Blocks
	}
	return shell.Result{Stdout: "url: " + boardHost + session + "\n"}, nil
}

func blockID(raw json.RawMessage) string {
	var block struct {
		ID string `json:"id"`
	}
	if err := json.Unmarshal(raw, &block); err != nil {
		panic(err)
	}
	return block.ID
}

func docSize(blocks []json.RawMessage) int {
	doc, err := json.Marshal(map[string]any{"version": 1, "title": "", "blocks": blocks})
	if err != nil {
		panic(err)
	}
	return len(doc)
}

func TestTalkRollsItsOldestRepliesIntoNumberedArchivesItLinksAndStaysUnderItsBudget(t *testing.T) {
	boards := newTalkBoards()
	f := fanout(t, boards)
	words := strings.Repeat("The relay holds under load. ", 300)
	sentIDs := make([]string, 0, 200)
	for n := range cap(sentIDs) {
		r := Reply{ID: fmt.Sprintf("r%d", n), Title: "Relay", MD: words}
		if talk := surface(f.Send(context.Background(), r), "talk"); talk.Error != "" || talk.Ref != "reply-"+r.ID {
			t.Fatalf("send %d: talk = %+v", n, talk)
		}
		if size := docSize(boards.docs["talk-d"]); size > talkboard.Budget {
			t.Fatalf("send %d left the live doc at %d bytes, past its %d budget", n, size, talkboard.Budget)
		}
		sentIDs = append(sentIDs, "reply-"+r.ID)
	}

	live := boards.docs["talk-d"]
	if blockID(live[0]) != talkboard.Section || blockID(live[1]) != talkboard.Banner {
		t.Fatalf("live doc opens %s, %s, want the Talk section and then the archive banner", blockID(live[0]), blockID(live[1]))
	}
	var banner struct {
		MD string `json:"md"`
	}
	if err := json.Unmarshal(live[1], &banner); err != nil {
		t.Fatal(err)
	}
	linked := archiveLink.FindAllStringSubmatch(banner.MD, -1)
	if len(linked) < 3 {
		t.Fatalf("banner %q links %d archives, want a roll every time the doc passes its budget", banner.MD, len(linked))
	}
	seen := map[string]int{}
	for _, b := range live[2:] {
		seen[blockID(b)]++
	}
	for n, link := range linked {
		if want := fmt.Sprintf("talk-d-archive-%d", len(linked)-n); link[1] != want {
			t.Fatalf("banner link %d goes to %s, want %s, newest first", n, link[1], want)
		}
		archived := boards.docs[link[1]]
		if size := docSize(archived); size > presentCap {
			t.Fatalf("%s is %d bytes, past cc-present's %d cap", link[1], size, presentCap)
		}
		for _, b := range archived[1:] {
			seen[blockID(b)]++
		}
	}
	for _, id := range sentIDs {
		if seen[id] != 1 {
			t.Fatalf("%s is on %d pages reachable from Talk, want exactly one", id, seen[id])
		}
	}
	if len(seen) != len(sentIDs) {
		t.Fatalf("pages reachable from Talk hold %d replies, want the %d sent", len(seen), len(sentIDs))
	}
}

func TestTalkMakesTheSameCallsWithOneReplyOnItOrAThousand(t *testing.T) {
	counts := map[int]int{}
	for _, n := range []int{1, 1000} {
		old := make([]string, n)
		for i := range old {
			old[i] = fmt.Sprintf(`{"id":"reply-old%d","type":"card","title":"Earlier","mirror":false,"children":[]}`, i)
		}
		boards := newTalkBoards(old...)
		if talk := surface(fanout(t, boards).Send(context.Background(), Reply{ID: "rnew", Title: "Relay", MD: "It holds."}), "talk"); talk.Error != "" {
			t.Fatalf("n=%d: talk = %+v", n, talk)
		}
		counts[n] = boards.calls
	}
	if counts[1] != 2 || counts[1000] != counts[1] {
		t.Fatalf("talk calls = %v, want one read and one write at either size", counts)
	}
}
