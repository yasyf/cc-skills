package reply

import (
	"cmp"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	communicator = "owner-relay"
	storeDir     = "replies"
	postTimeout  = 30 * time.Second
	maxTitle     = 140
	maxPick      = 61
	maxPicks     = 4
	maxLinks     = 5
	maxPRs       = 10
	maxSlackBody = 2800
	withdrawn    = " withdrawn"
)

var (
	statuses   = []string{"info", "progress", "needs_you", "incident", "done"}
	replyID    = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,47}$`)
	pullURL    = regexp.MustCompile(`^https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)$`)
	hiddenCite = regexp.MustCompile(`(?s)\s*<!--.*?-->`)
	slackSent  = regexp.MustCompile(`(?m)^sent: \S+ ts=\d{10}\.\d{6} (\S+)`)
	mdLink     = regexp.MustCompile(`\[([^\]\n]+)\]\((https?://[^)\s]+)\)`)
	mdBold     = regexp.MustCompile(`\*\*([^*\n]+)\*\*`)
	mdHeading  = regexp.MustCompile(`^#{1,6} +(.+)$`)
)

type Link struct {
	Label string `json:"label"`
	URL   string `json:"url"`
}

type Build struct {
	Repo   string `json:"repo,omitempty"`
	Branch string `json:"branch,omitempty"`
	None   string `json:"none,omitempty"`
}

type Reply struct {
	ID          string   `json:"id"`
	Title       string   `json:"title"`
	MD          string   `json:"md"`
	Status      string   `json:"status,omitempty"`
	Picks       []string `json:"picks,omitempty"`
	Recommended string   `json:"recommended,omitempty"`
	Build       *Build   `json:"build,omitempty"`
	Links       []Link   `json:"links,omitempty"`
	PRs         []string `json:"prs,omitempty"`
	Thread      string   `json:"thread,omitempty"`
}

type Outcome struct {
	Surface string `json:"surface"`
	Ref     string `json:"ref,omitempty"`
	Error   string `json:"error,omitempty"`
}

type delivery struct {
	Reply    Reply     `json:"reply"`
	At       time.Time `json:"at"`
	Surfaces []Outcome `json:"surfaces"`
}

type Surface func(ctx context.Context, r Reply, sent []Outcome) []Outcome

type Fanout struct {
	Runner shell.Runner
	Entry  drive.Entry
	Now    func() time.Time
}

func (f Fanout) Send(ctx context.Context, r Reply) []Outcome {
	seen := r
	seen.MD = hiddenCite.ReplaceAllString(r.MD, "")
	sent := make([]Outcome, 0, 5)
	for _, step := range []struct {
		surface Surface
		reply   Reply
	}{{f.board, seen}, {f.slack, seen}, {f.record, r}, {f.dashboard, seen}} {
		sent = append(sent, step.surface(ctx, step.reply, sent)...)
	}
	return sent
}

func (r Reply) check() error {
	switch {
	case !replyID.MatchString(r.ID):
		return fmt.Errorf("id %q is not one an earlier reply returned", r.ID)
	case strings.TrimSpace(r.Title) == "" || strings.Contains(r.Title, "\n") || utf8.RuneCountInString(r.Title) > maxTitle:
		return fmt.Errorf("title is one line of 1 to %d characters", maxTitle)
	case strings.TrimSpace(r.MD) == "":
		return errors.New("md carries the reply itself")
	case r.Status != "" && !slices.Contains(statuses, r.Status):
		return fmt.Errorf("status %q is not one of %s", r.Status, strings.Join(statuses, ", "))
	case len(r.Picks) == 1 || len(r.Picks) > maxPicks:
		return fmt.Errorf("picks offer 2 to %d answers, not %d", maxPicks, len(r.Picks))
	case len(r.Picks) == 0 && r.Recommended != "":
		return errors.New("recommended names one of picks, and this reply has none")
	case len(r.Picks) > 0 && !slices.Contains(r.Picks, r.Recommended):
		return fmt.Errorf("recommended %q is not one of picks", r.Recommended)
	case r.Build != nil && r.Recommended == "":
		return errors.New("build names what the recommended pick is built on, and this reply recommends none")
	case len(slices.Compact(slices.Sorted(slices.Values(r.Picks)))) != len(r.Picks):
		return errors.New("each pick is a different answer")
	case len(r.Links) > maxLinks:
		return fmt.Errorf("a reply carries at most %d links", maxLinks)
	case len(r.PRs) > maxPRs:
		return fmt.Errorf("a reply carries at most %d PRs", maxPRs)
	}
	for _, pick := range r.Picks {
		if pick == "" || utf8.RuneCountInString(pick) > maxPick {
			return fmt.Errorf("pick %q is 1 to %d characters, the width of a button", pick, maxPick)
		}
	}
	for _, link := range r.Links {
		if link.Label == "" || !strings.HasPrefix(link.URL, "https://") && !strings.HasPrefix(link.URL, "http://") {
			return fmt.Errorf("link %q needs a label and an http or https url", link.URL)
		}
	}
	for _, pr := range r.PRs {
		if !pullURL.MatchString(pr) {
			return fmt.Errorf("pr %q is not a https://github.com/<owner>/<repo>/pull/<n> url", pr)
		}
	}
	return nil
}

func (r Reply) markdown() string {
	lines := []string{strings.TrimSpace(r.MD)}
	if len(r.Picks) > 0 {
		lines = append(lines, "")
	}
	for _, pick := range r.Picks {
		switch {
		case pick != r.Recommended:
		case r.Build == nil:
			pick += " (recommended)"
		case r.Build.None != "":
			pick += " (recommended; nothing to build: " + r.Build.None + ")"
		default:
			pick += " (recommended; built on " + r.Build.Repo + " " + r.Build.Branch + ")"
		}
		lines = append(lines, "- "+pick)
	}
	if len(r.Links)+len(r.PRs) > 0 {
		lines = append(lines, "")
	}
	for _, link := range r.Links {
		lines = append(lines, "- ["+link.Label+"]("+link.URL+")")
	}
	for _, pr := range r.PRs {
		lines = append(lines, "- "+pr)
	}
	return strings.Join(lines, "\n")
}

func (f Fanout) board(ctx context.Context, r Reply, _ []Outcome) []Outcome {
	if len(r.Picks) == 0 {
		return f.withdraw(ctx, r)
	}
	id := "card-" + r.ID
	options := make([]map[string]any, len(r.Picks))
	for n, pick := range r.Picks {
		options[n] = map[string]any{"id": fmt.Sprintf("%s-%d", r.ID, n+1), "label": pick}
		if pick == r.Recommended {
			options[n]["recommended"] = true
			if r.Build != nil {
				options[n]["build"] = r.Build
			}
		}
	}
	prose := strings.TrimSpace(r.MD)
	for _, link := range r.Links {
		prose += "\n- [" + link.Label + "](" + link.URL + ")"
	}
	children := []map[string]any{
		{"id": r.ID + "-md", "type": "markdown", "md": prose},
		{"id": r.ID + "-choice", "type": "choice", "options": options},
	}
	for _, pr := range r.PRs {
		named := pullURL.FindStringSubmatch(pr)
		children = append(children, map[string]any{"id": r.ID + "-pr-" + named[2], "type": "pr", "repo": named[1], "number": json.Number(named[2])})
	}
	lede, _, _ := strings.Cut(strings.TrimSpace(r.MD), "\n\n")
	block, err := json.Marshal(map[string]any{"id": id, "type": "card", "title": r.Title, "summary": strings.Join(strings.Fields(lede), " "), "children": children})
	if err != nil {
		return []Outcome{{Surface: "board", Error: err.Error()}}
	}
	done, err := f.Runner.Run(ctx, shell.Command{
		Argv:    []string{"cc-present", "update-block", "-", "--session", f.Entry.Root(), "--round", "current"},
		Stdin:   string(block),
		Dir:     f.Entry.Checkout,
		Timeout: postTimeout,
	})
	if err != nil {
		return []Outcome{failed("board", err), {Surface: "slack", Error: "the board card did not post, so cc-present sent no Slack card"}}
	}
	posted := slackSent.FindStringSubmatch(done.Stdout)
	if posted == nil {
		return []Outcome{{Surface: "board", Ref: id}, {Surface: "slack", Error: "cc-present put " + id + " on the board and sent no Slack card"}}
	}
	return []Outcome{{Surface: "board", Ref: id}, {Surface: "slack", Ref: posted[1]}}
}

func (f Fanout) withdraw(ctx context.Context, r Reply) []Outcome {
	id := "card-" + r.ID
	was, err := f.delivered(r.ID)
	if err != nil {
		return []Outcome{{Surface: "board", Error: err.Error()}, {Surface: "slack", Error: "the earlier reply did not read, so no Slack card was edited"}}
	}
	board := surface(was.Surfaces, "board")
	if board.Surface == "" {
		return nil
	}
	slack := Outcome{Surface: "slack", Ref: surface(was.Surfaces, "slack").Ref}
	if board.Ref != id+withdrawn {
		if _, err := f.Runner.Run(ctx, shell.Command{
			Argv:    []string{"cc-present", "remove-block", id, "--session", f.Entry.Root(), "--reason", "edited: " + r.Title},
			Dir:     f.Entry.Checkout,
			Timeout: postTimeout,
		}); err != nil {
			slack.Error = id + " is still on the board, so its Slack card keeps its buttons"
			return []Outcome{failed("board", err), slack}
		}
	}
	if slack.Ref == "" {
		slack.Error = id + " never reached Slack, so there is no Slack card to edit"
	}
	return []Outcome{{Surface: "board", Ref: id + withdrawn}, slack}
}

func (f Fanout) slack(ctx context.Context, r Reply, sent []Outcome) []Outcome {
	if surface(sent, "slack").Surface != "" || r.Thread == "" && len(r.Links) == 0 && len(r.PRs) == 0 && r.Status == "" {
		return nil
	}
	body := mrkdwn(r.MD)
	for _, pr := range r.PRs {
		body += "\n" + pr
	}
	if r.Thread != "" {
		for _, link := range r.Links {
			body += "\n<" + link.URL + "|" + link.Label + ">"
		}
		return f.dmStatus(ctx, "--thread", r.Thread, "*"+r.Title+"*\n"+body)
	}
	buttons := make([]Link, 0, len(r.Links))
	for _, link := range r.Links {
		if strings.HasPrefix(link.URL, "https://") {
			buttons = append(buttons, link)
			continue
		}
		body += "\n<" + link.URL + "|" + link.Label + ">"
	}
	card, err := json.Marshal(struct {
		Key    string `json:"key"`
		Status string `json:"status"`
		Title  string `json:"title"`
		Body   string `json:"body"`
		Drive  string `json:"drive"`
		Links  []Link `json:"links,omitempty"`
	}{r.ID, cmp.Or(r.Status, "info"), r.Title, fitted(body, maxSlackBody), f.Entry.Bus(), buttons})
	if err != nil {
		return []Outcome{{Surface: "slack", Error: err.Error()}}
	}
	return f.dmStatus(ctx, "--card", string(card))
}

func (f Fanout) dmStatus(ctx context.Context, args ...string) []Outcome {
	done, err := f.Runner.Run(ctx, shell.Command{Argv: append([]string{"cc-slack", "dm-status"}, args...), Timeout: postTimeout})
	if err != nil {
		return []Outcome{failed("slack", err)}
	}
	var posted struct {
		Permalink string `json:"permalink"`
	}
	if err := json.Unmarshal([]byte(done.Stdout), &posted); err != nil || posted.Permalink == "" {
		return []Outcome{{Surface: "slack", Error: "cc-slack dm-status named no posted message: " + strings.TrimSpace(done.Stdout)}}
	}
	return []Outcome{{Surface: "slack", Ref: posted.Permalink}}
}

func (f Fanout) record(ctx context.Context, r Reply, _ []Outcome) []Outcome {
	content, err := json.Marshal(r)
	if err != nil {
		return []Outcome{{Surface: "record", Error: err.Error()}}
	}
	sum := sha256.Sum256(content)
	argv := []string{
		"cci", "post", "--drive", f.Entry.Bus(), "--lane", communicator, "--kind", "note", "--topic", "reply:" + r.ID,
		"--to", "owner", "--id", "reply:" + r.ID + ":" + hex.EncodeToString(sum[:6]), "--path", "-", "--json",
	}
	for _, pr := range r.PRs {
		argv = append(argv, "--pr", pr)
	}
	done, err := f.Runner.Run(ctx, shell.Command{Argv: argv, Stdin: r.Title + "\n\n" + r.markdown(), Timeout: postTimeout})
	if err != nil {
		return []Outcome{failed("record", err)}
	}
	var posted struct {
		Num string `json:"num"`
	}
	if err := json.Unmarshal([]byte(done.Stdout), &posted); err != nil || posted.Num == "" {
		return []Outcome{{Surface: "record", Error: "cci post named no record: " + strings.TrimSpace(done.Stdout)}}
	}
	return []Outcome{{Surface: "record", Ref: posted.Num}}
}

func (f Fanout) dashboard(_ context.Context, r Reply, sent []Outcome) []Outcome {
	path := f.stored(r.ID)
	data, err := json.MarshalIndent(delivery{r, f.Now().UTC(), sent}, "", "  ")
	if err == nil {
		err = os.MkdirAll(filepath.Dir(path), 0o750)
	}
	if err == nil {
		err = os.WriteFile(path+".staged", append(data, '\n'), 0o600)
	}
	if err == nil {
		err = os.Rename(path+".staged", path)
	}
	if err != nil {
		return []Outcome{{Surface: "dashboard", Error: err.Error()}}
	}
	return []Outcome{{Surface: "dashboard", Ref: path}}
}

func (f Fanout) delivered(id string) (delivery, error) {
	var was delivery
	data, err := os.ReadFile(f.stored(id))
	if errors.Is(err, fs.ErrNotExist) {
		return was, nil
	}
	if err != nil {
		return was, err
	}
	return was, json.Unmarshal(data, &was)
}

func (f Fanout) stored(id string) string {
	return filepath.Join(f.Entry.StateDir, storeDir, id+".json")
}

func surface(sent []Outcome, name string) Outcome {
	for _, o := range sent {
		if o.Surface == name {
			return o
		}
	}
	return Outcome{}
}

func failed(surface string, err error) Outcome {
	var exit *shell.ExitError
	if errors.As(err, &exit) {
		return Outcome{Surface: surface, Error: fmt.Sprintf("%s %s exited %d: %s", exit.Argv[0], exit.Argv[1], exit.Code, strings.TrimSpace(exit.Stderr))}
	}
	return Outcome{Surface: surface, Error: err.Error()}
}

func mrkdwn(md string) string {
	out := make([]string, 0, strings.Count(md, "\n")+1)
	table := make([]string, 0, cap(out))
	flush := func() {
		if len(table) > 0 {
			out = append(out, "```\n"+strings.Join(table, "\n")+"\n```")
			table = table[:0]
		}
	}
	for line := range strings.SplitSeq(strings.TrimSpace(md), "\n") {
		if strings.HasPrefix(strings.TrimSpace(line), "|") {
			table = append(table, line)
			continue
		}
		flush()
		line = mdBold.ReplaceAllString(mdLink.ReplaceAllString(line, "<$2|$1>"), "*$1*")
		out = append(out, mdHeading.ReplaceAllString(line, "*$1*"))
	}
	flush()
	return strings.Join(out, "\n")
}

func fitted(text string, limit int) string {
	runes := []rune(text)
	if len(runes) <= limit {
		return text
	}
	cut := string(runes[:limit-1])
	if at := strings.LastIndex(cut, "\n\n"); at > 0 {
		cut = cut[:at]
	}
	return cut + "…"
}

func receipt(r Reply, sent []Outcome) string {
	lines := []string{r.markdown(), "", "Reply " + r.ID + "; pass this id to edit it on every surface."}
	for _, o := range sent {
		if o.Error != "" {
			lines = append(lines, "- "+o.Surface+" FAILED: "+o.Error)
			continue
		}
		lines = append(lines, "- "+o.Surface+": "+o.Ref)
	}
	return strings.Join(lines, "\n")
}
