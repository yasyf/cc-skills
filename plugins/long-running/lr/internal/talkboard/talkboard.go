package talkboard

import (
	"cmp"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	Section      = "talk"
	Links        = "talk.json"
	archivesFile = "talk-archives.json"
	Banner       = "talk-archives"
	Budget       = 512 << 10
	notesFile    = "talk-notes"
	timeout      = 30 * time.Second
)

type Archive struct {
	N       int    `json:"n"`
	Session string `json:"session"`
	URL     string `json:"url"`
	Tailnet string `json:"tailnet,omitempty"`
	Replies int    `json:"replies"`
}

func Session(entry drive.Entry) string {
	return "talk-" + entry.Drive
}

func Title(entry drive.Entry) string {
	return "Talk: " + entry.Drive
}

func Opened(entry drive.Entry) bool {
	_, err := os.Stat(filepath.Join(entry.StateDir, Links))
	return err == nil
}

func LastNote(entry drive.Entry) string {
	notes, err := ReadNotes(entry)
	if err != nil || len(notes) == 0 {
		return ""
	}
	return notes[len(notes)-1]
}

func NotesPath(entry drive.Entry) string {
	return filepath.Join(entry.StateDir, "owner-relay", notesFile)
}

func ReadNotes(entry drive.Entry) ([]string, error) {
	data, err := os.ReadFile(NotesPath(entry))
	if errors.Is(err, fs.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return strings.Fields(string(data)), nil
}

func URLs(started string) (url, tailnet string) {
	for line := range strings.Lines(started) {
		key, value, _ := strings.Cut(strings.TrimSpace(line), ": ")
		switch {
		case key == "url" && url == "":
			url = value
		case key == "tailnet" && tailnet == "":
			tailnet = value
		}
	}
	return url, tailnet
}

type liveDoc struct {
	Rev int64                      `json:"rev"`
	Doc map[string]json.RawMessage `json:"doc"`
}

type entry struct {
	id  string
	raw json.RawMessage
}

func Fit(ctx context.Context, runner shell.Runner, e drive.Entry, id string, card []byte) error {
	read, err := runner.Run(ctx, shell.Command{
		Argv:    []string{"cc-present", "outcomes", "--all", "--session", Session(e)},
		Dir:     e.Checkout,
		Timeout: timeout,
	})
	if err != nil {
		return err
	}
	var live liveDoc
	if err := json.Unmarshal([]byte(read.Stdout), &live); err != nil {
		return fmt.Errorf("the Talk board did not read: %w", err)
	}
	var blocks []entry
	if raw := live.Doc["blocks"]; raw != nil {
		var all []json.RawMessage
		if err := json.Unmarshal(raw, &all); err != nil {
			return fmt.Errorf("the Talk board did not read: %w", err)
		}
		for _, b := range all {
			var named struct {
				ID string `json:"id"`
			}
			if err := json.Unmarshal(b, &named); err != nil {
				return fmt.Errorf("the Talk board did not read: %w", err)
			}
			compact, err := json.Marshal(b)
			if err != nil {
				return err
			}
			blocks = append(blocks, entry{named.ID, compact})
		}
	}
	doc, err := json.Marshal(live.Doc)
	if err != nil {
		return err
	}
	size := len(doc) + len(card) + 1
	if at := slices.IndexFunc(blocks, func(b entry) bool { return b.id == id }); at >= 0 {
		size -= len(blocks[at].raw)
	}
	if size <= Budget {
		return nil
	}
	var kept, rolled []entry
	for _, b := range blocks {
		switch {
		case b.id == Banner:
			size -= len(b.raw) + 1
		case b.id == Section || b.id == id || size <= Budget/2:
			kept = append(kept, b)
		default:
			rolled = append(rolled, b)
			size -= len(b.raw) + 1
		}
	}
	if len(rolled) == 0 {
		return nil
	}
	rolls, err := readArchives(e)
	if err != nil {
		return err
	}
	link, err := liveLink(e)
	if err != nil {
		return err
	}
	for len(rolled) > 0 {
		n, total := 1, len(rolled[0].raw)
		for n < len(rolled) && total+len(rolled[n].raw) <= Budget {
			total += len(rolled[n].raw)
			n++
		}
		archived, err := archive(ctx, runner, e, live.Doc["version"], link, len(rolls)+1, rolled[:n])
		if err != nil {
			return err
		}
		rolls = append(rolls, archived)
		if err := writeArchives(e, rolls); err != nil {
			return err
		}
		rolled = rolled[n:]
	}
	return push(ctx, runner, e, live, kept, rolls)
}

func archive(ctx context.Context, runner shell.Runner, e drive.Entry, version json.RawMessage, link string, n int, replies []entry) (Archive, error) {
	head, err := json.Marshal(map[string]string{
		"id":   "talk-archive",
		"type": "markdown",
		"md":   fmt.Sprintf("Talk archive %d: %d older replies, moved off the live Talk page so it stays under its size limit. Newer replies are on [Talk](%s).", n, len(replies), link),
	})
	if err != nil {
		return Archive{}, err
	}
	blocks := []json.RawMessage{head}
	for _, r := range replies {
		blocks = append(blocks, r.raw)
	}
	doc, err := json.Marshal(map[string]any{"version": version, "title": "Talk archive " + strconv.Itoa(n) + ": " + e.Drive, "blocks": blocks})
	if err != nil {
		return Archive{}, err
	}
	session := Session(e) + "-archive-" + strconv.Itoa(n)
	started, err := runner.Run(ctx, shell.Command{
		Argv:    []string{"cc-present", "start", "--new", "--session", session, "--doc", "-"},
		Stdin:   string(doc),
		Dir:     e.Checkout,
		Timeout: timeout,
	})
	if err != nil {
		return Archive{}, err
	}
	url, tailnet := URLs(started.Stdout)
	if url == "" {
		return Archive{}, fmt.Errorf("cc-present start named no url for %s: %s", session, strings.TrimSpace(started.Stdout))
	}
	return Archive{N: n, Session: session, URL: url, Tailnet: tailnet, Replies: len(replies)}, nil
}

func push(ctx context.Context, runner shell.Runner, e drive.Entry, live liveDoc, kept []entry, archives []Archive) error {
	listed := make([]string, 0, len(archives))
	for _, a := range slices.Backward(archives) {
		listed = append(listed, fmt.Sprintf("[Archive %d](%s) (%d replies)", a.N, cmp.Or(a.Tailnet, a.URL), a.Replies))
	}
	banner, err := json.Marshal(map[string]string{"id": Banner, "type": "markdown", "md": "Older replies: " + strings.Join(listed, " · ")})
	if err != nil {
		return err
	}
	blocks := make([]json.RawMessage, 0, len(kept)+1)
	for _, b := range kept {
		blocks = append(blocks, b.raw)
	}
	section := slices.IndexFunc(kept, func(b entry) bool { return b.id == Section })
	blocks = slices.Insert(blocks, section+1, json.RawMessage(banner))
	doc := live.Doc
	if doc["blocks"], err = json.Marshal(blocks); err != nil {
		return err
	}
	if title := string(doc["title"]); title == "" || title == `""` {
		if doc["title"], err = json.Marshal(Title(e)); err != nil {
			return err
		}
	}
	body, err := json.Marshal(doc)
	if err != nil {
		return err
	}
	_, err = runner.Run(ctx, shell.Command{
		Argv:    []string{"cc-present", "push", "-", "--session", Session(e), "--rev", strconv.FormatInt(live.Rev, 10), "--round", "current"},
		Stdin:   string(body),
		Dir:     e.Checkout,
		Timeout: timeout,
	})
	return err
}

func readArchives(e drive.Entry) ([]Archive, error) {
	data, err := os.ReadFile(filepath.Join(e.StateDir, archivesFile))
	if errors.Is(err, fs.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var archives []Archive
	return archives, json.Unmarshal(data, &archives)
}

func writeArchives(e drive.Entry, archives []Archive) error {
	data, err := json.MarshalIndent(archives, "", "  ")
	if err != nil {
		return err
	}
	path := filepath.Join(e.StateDir, archivesFile)
	if err := os.WriteFile(path+".staged", append(data, '\n'), 0o600); err != nil {
		return err
	}
	return os.Rename(path+".staged", path)
}

func liveLink(e drive.Entry) (string, error) {
	var links struct {
		URL     string `json:"url"`
		Tailnet string `json:"tailnet"`
	}
	data, err := os.ReadFile(filepath.Join(e.StateDir, Links))
	if err != nil {
		return "", err
	}
	if err := json.Unmarshal(data, &links); err != nil {
		return "", fmt.Errorf("%s: %w", Links, err)
	}
	return cmp.Or(links.Tailnet, links.URL), nil
}
