package owner

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"html"
	"io"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	replyLane      = "owner-relay"
	cardPrefix     = "card-"
	cardUpserted   = "block.upserted"
	cardType       = "card"
	choiceType     = "choice"
	threadsFilled  = "slack-threads-prs-backfill"
	rewatchThreads = 30 * time.Second
	linkReadLimit  = "200"
)

var (
	permalinkTS  = regexp.MustCompile(`/p(\d{10})(\d{6})(?:\D|$)`)
	permalinkIn  = regexp.MustCompile(`[?&]thread_ts=(\d+\.\d+)`)
	pullURL      = regexp.MustCompile(`^https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)$`)
	onlySlackURL = regexp.MustCompile(`^<?(https://[\w-]+\.slack\.com/archives/[^\s|>]+)(?:\|[^>]*)?>?$`)
	writtenBy    = regexp.MustCompile(`(?m)last written at rev \d+ by lane (\S+)`)
)

func MessageTS(permalink string) string {
	found := permalinkTS.FindStringSubmatch(permalink)
	if found == nil {
		return ""
	}
	return found[1] + "." + found[2]
}

func ThreadRoot(permalink string) string {
	if found := permalinkIn.FindStringSubmatch(permalink); found != nil {
		return found[1]
	}
	return MessageTS(permalink)
}

type boardCard struct {
	ID    string `json:"id"`
	Type  string `json:"type"`
	Title string `json:"title"`
	Slack *struct {
		URL string `json:"url"`
	} `json:"slack"`
	Children []struct {
		Type    string `json:"type"`
		Options []struct {
			Label string `json:"label"`
		} `json:"options"`
	} `json:"children"`
}

func (c boardCard) posted() bool {
	return c.Type == cardType && c.Slack != nil && ThreadRoot(c.Slack.URL) != ""
}

func (c boardCard) thread(lane string) store.SlackThread {
	thread := store.SlackThread{TS: ThreadRoot(c.Slack.URL), Card: c.ID, Lane: lane, Question: c.Title}
	for _, child := range c.Children {
		if child.Type != choiceType {
			continue
		}
		for _, option := range child.Options {
			thread.Options = append(thread.Options, option.Label)
		}
	}
	return thread
}

type CardThreads struct {
	Runner shell.Runner
	Entry  drive.Entry
	Log    *slog.Logger
}

func (c *CardThreads) Run(ctx context.Context) {
	for ctx.Err() == nil {
		err := c.follow(ctx)
		if errors.Is(err, exec.ErrNotFound) {
			c.Log.Warn("no board daemon on this host, so card threads stay unindexed", "drive", c.Entry.Drive, "err", err)
			return
		}
		if err != nil && ctx.Err() == nil {
			c.Log.Warn("the card thread follower stopped and restarts", "drive", c.Entry.Drive, "err", err)
		}
		select {
		case <-ctx.Done():
		case <-time.After(rewatchThreads):
		}
	}
}

func (c *CardThreads) follow(ctx context.Context) error {
	s, err := store.Open(c.Entry.Store())
	if err != nil {
		return err
	}
	defer func() { _ = s.Close() }()
	if err := c.fill(ctx, s); err != nil {
		return err
	}
	return streamLines(ctx, c.Runner, []string{"cc-present", "watch", "--session", c.Entry.Root()}, func(line []byte) error {
		return indexUpsert(s, line)
	})
}

func indexUpsert(s *store.Store, line []byte) error {
	var event struct {
		Type  string          `json:"type"`
		Lane  string          `json:"lane"`
		Block json.RawMessage `json:"block"`
	}
	if err := json.Unmarshal(line, &event); err != nil {
		return fmt.Errorf("cc-present watch printed %q: %w", line, err)
	}
	if event.Type != cardUpserted {
		return nil
	}
	var card boardCard
	if err := json.Unmarshal(event.Block, &card); err != nil {
		return fmt.Errorf("a card event's block: %w", err)
	}
	if !card.posted() {
		return nil
	}
	return s.PutSlackThread(card.thread(event.Lane))
}

func (c *CardThreads) fill(ctx context.Context, s *store.Store) error {
	filled, err := s.Cursor(threadsFilled)
	if err != nil {
		return err
	}
	if err := c.fillCards(ctx, s, filled == 0); err != nil {
		return err
	}
	if filled != 0 {
		return nil
	}
	if err := fillReplies(s, filepath.Join(c.Entry.StateDir, repliesDir), c.Entry.Repo); err != nil {
		return err
	}
	return s.Advance(threadsFilled, 1)
}

func (c *CardThreads) fillCards(ctx context.Context, s *store.Store, withLanes bool) error {
	shown, err := c.Runner.Run(ctx, shell.Command{Argv: []string{"cc-present", "outcomes", "--all", "--session", c.Entry.Root()}, Timeout: rowWait})
	var failed *shell.ExitError
	if errors.As(err, &failed) && strings.Contains(failed.Stderr, noBoard) {
		return nil
	}
	if err != nil {
		return err
	}
	start := strings.Index(shown.Stdout, "{")
	if start < 0 {
		return fmt.Errorf("cc-present outcomes printed %q", shown.Stdout)
	}
	var outcomes struct {
		Doc struct {
			Blocks []boardCard `json:"blocks"`
		} `json:"doc"`
	}
	if err := json.Unmarshal([]byte(shown.Stdout[start:]), &outcomes); err != nil {
		return fmt.Errorf("cc-present outcomes: %w", err)
	}
	for _, card := range outcomes.Doc.Blocks {
		if !card.posted() {
			continue
		}
		lane := ""
		if withLanes {
			if lane, err = c.cardLane(ctx, card.ID); err != nil {
				return err
			}
		}
		if err := s.PutSlackThread(card.thread(lane)); err != nil {
			return err
		}
	}
	return nil
}

func (c *CardThreads) cardLane(ctx context.Context, card string) (string, error) {
	shown, err := c.Runner.Run(ctx, shell.Command{Argv: []string{"cc-present", "block", "show", card, "--session", c.Entry.Root()}, Timeout: rowWait})
	if err != nil {
		return "", err
	}
	found := writtenBy.FindStringSubmatch(shown.Stdout)
	if found == nil {
		return "", nil
	}
	return found[1], nil
}

func fillReplies(s *store.Store, replies, repo string) error {
	paths, err := filepath.Glob(filepath.Join(replies, "*.json"))
	if err != nil {
		return err
	}
	type sent struct {
		Reply struct {
			ID    string   `json:"id"`
			Title string   `json:"title"`
			Order string   `json:"order"`
			Picks []string `json:"picks"`
			PRs   []string `json:"prs"`
		} `json:"reply"`
		At       time.Time `json:"at"`
		Surfaces []struct {
			Surface string `json:"surface"`
			Ref     string `json:"ref"`
		} `json:"surfaces"`
	}
	all := make([]sent, 0, len(paths))
	for _, path := range paths {
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		var one sent
		if err := json.Unmarshal(data, &one); err != nil {
			return fmt.Errorf("%s: %w", path, err)
		}
		all = append(all, one)
	}
	slices.SortFunc(all, func(one, other sent) int { return one.At.Compare(other.At) })
	for _, one := range all {
		for _, posted := range one.Surfaces {
			if !strings.HasPrefix(posted.Surface, "slack") || ThreadRoot(posted.Ref) == "" {
				continue
			}
			if err := s.PutSlackThread(ReplyThread(posted.Ref, one.Reply.ID, one.Reply.Order, one.Reply.Title, one.Reply.Picks, PullsIn(repo, one.Reply.PRs))); err != nil {
				return err
			}
		}
	}
	return nil
}

func ReplyThread(permalink, reply, order, title string, picks []string, prs []int) store.SlackThread {
	thread := store.SlackThread{TS: ThreadRoot(permalink), Reply: reply, Lane: replyLane, Row: order, PRs: prs}
	if len(picks) > 0 {
		thread.Card, thread.Question, thread.Options = cardPrefix+reply, title, picks
	}
	return thread
}

func PullsIn(repo string, urls []string) []int {
	var prs []int
	for _, url := range urls {
		found := pullURL.FindStringSubmatch(url)
		if found == nil || found[1] != repo {
			continue
		}
		pr, err := strconv.Atoi(found[2])
		if err != nil {
			panic(err)
		}
		prs = append(prs, pr)
	}
	return prs
}

type threadsAt func(ts string) (store.SlackThread, bool, error)

type threadMessage struct {
	TS       string `json:"ts"`
	UserName string `json:"user_name"`
	Text     string `json:"text"`
}

type linkedMessage struct {
	URL      string             `json:"url"`
	UserName string             `json:"user_name,omitempty"`
	Text     string             `json:"text"`
	Answers  *store.SlackThread `json:"answers,omitempty"`
}

func joined(ctx context.Context, runner shell.Runner, threads threadsAt, raw []byte, event slackEvent) ([]byte, []string) {
	var missed []string
	fields := map[string]any{}
	if event.Thread != "" && event.Thread != event.TS {
		found, ok, err := threads(event.Thread)
		if err != nil {
			missed = append(missed, fmt.Sprintf("thread %s did not read: %v", event.Thread, err))
		}
		if ok {
			fields["answers"] = found
		}
	}
	if link := onlySlackURL.FindStringSubmatch(strings.TrimSpace(event.Text)); link != nil {
		found, err := readLink(ctx, runner, threads, html.UnescapeString(link[1]))
		if err != nil {
			missed = append(missed, fmt.Sprintf("the link in %s did not read: %v", event.TS, err))
		} else {
			fields["linked"] = found
		}
	}
	if len(fields) == 0 {
		return raw, missed
	}
	line := map[string]any{}
	if err := json.Unmarshal(raw, &line); err != nil {
		return raw, append(missed, err.Error())
	}
	for key, value := range fields {
		line[key] = value
	}
	encoded, err := json.Marshal(line)
	if err != nil {
		return raw, append(missed, err.Error())
	}
	return encoded, missed
}

func readLink(ctx context.Context, runner shell.Runner, threads threadsAt, url string) (linkedMessage, error) {
	message, err := messageIn(ctx, runner, MessageTS(url), "--url", url)
	if err != nil {
		return linkedMessage{}, err
	}
	linked := linkedMessage{URL: url, UserName: message.UserName, Text: message.Text}
	found, ok, err := threads(ThreadRoot(url))
	if err != nil {
		return linked, err
	}
	if ok {
		linked.Answers = &found
	}
	return linked, nil
}

func messageIn(ctx context.Context, runner shell.Runner, ts string, thread ...string) (threadMessage, error) {
	argv := append(append([]string{"cc-slack", "thread"}, thread...), "--limit", linkReadLimit)
	shown, err := runner.Run(ctx, shell.Command{Argv: argv, Timeout: slackCall})
	if err != nil {
		return threadMessage{}, err
	}
	var read struct {
		Messages []threadMessage `json:"messages"`
	}
	if err := json.Unmarshal([]byte(shown.Stdout), &read); err != nil {
		return threadMessage{}, fmt.Errorf("cc-slack thread printed %q: %w", shown.Stdout, err)
	}
	at := slices.IndexFunc(read.Messages, func(message threadMessage) bool { return message.TS == ts })
	if at < 0 {
		return threadMessage{}, fmt.Errorf("%s is not in its thread's last %s messages", ts, linkReadLimit)
	}
	return read.Messages[at], nil
}

func streamLines(ctx context.Context, runner shell.Runner, argv []string, handle func([]byte) error) error {
	read, write := io.Pipe()
	go func() {
		_, err := runner.Run(ctx, shell.Command{Argv: argv, Stdout: write})
		_ = write.CloseWithError(err)
	}()
	events := bufio.NewScanner(read)
	events.Buffer(make([]byte, 0, bufio.MaxScanTokenSize), maxSlackEvent)
	for events.Scan() {
		if err := handle(events.Bytes()); err != nil {
			return err
		}
	}
	return events.Err()
}
