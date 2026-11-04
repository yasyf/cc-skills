package talk

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/owner"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/talkboard"
)

const (
	Section      = talkboard.Section
	Links        = talkboard.Links
	lane         = "talk"
	communicator = "owner-relay"
	noteEvent    = "feedback.created"
	opened       = "open"
	callTimeout  = 30 * time.Second
	rewatchAfter = 5 * time.Second
	maxEvent     = 1 << 20
)

type Follower struct {
	Runner  shell.Runner
	Entry   drive.Entry
	Writers []string
	Now     func() time.Time
	Log     *slog.Logger

	seen map[string]bool
}

func (f *Follower) Run(ctx context.Context) {
	for ctx.Err() == nil {
		if err := f.follow(ctx); err != nil && ctx.Err() == nil {
			f.Log.Warn("the talk follower stopped and starts again", "drive", f.Entry.Drive, "err", err)
		}
		select {
		case <-ctx.Done():
		case <-time.After(rewatchAfter):
		}
	}
}

func (f *Follower) follow(ctx context.Context) error {
	if err := f.Open(ctx); err != nil {
		return err
	}
	watch := exec.CommandContext(ctx, "cc-present", "watch", "--session", talkboard.Session(f.Entry))
	events, err := watch.StdoutPipe()
	if err != nil {
		return err
	}
	if err := watch.Start(); err != nil {
		return err
	}
	lines := bufio.NewScanner(events)
	lines.Buffer(make([]byte, 0, 64*1024), maxEvent)
	for lines.Scan() {
		if err := f.Handle(ctx, lines.Bytes()); err != nil {
			f.Log.Warn("a talk note did not reach the communicator; its ledger row holds it", "drive", f.Entry.Drive, "err", err)
		}
	}
	return errors.Join(lines.Err(), watch.Wait())
}

func (f *Follower) Open(ctx context.Context) error {
	session := talkboard.Session(f.Entry)
	started, err := f.Runner.Run(ctx, shell.Command{
		Argv:    []string{"cc-present", "start", "--session", session, "--title", "Talk: " + f.Entry.Drive},
		Dir:     f.Entry.Checkout,
		Timeout: callTimeout,
	})
	if err != nil {
		return err
	}
	section, err := json.Marshal(struct {
		ID      string   `json:"id"`
		Type    string   `json:"type"`
		Title   string   `json:"title"`
		Writers []string `json:"writers,omitempty"`
		Href    string   `json:"href,omitempty"`
	}{Section, "section", "Talk", f.Writers, dashboardLink(f.Entry)})
	if err != nil {
		return err
	}
	if _, err := f.Runner.Run(ctx, shell.Command{
		Argv:    []string{"cc-present", "update-block", "-", "--session", session, "--round", "current"},
		Stdin:   string(section),
		Dir:     f.Entry.Checkout,
		Timeout: callTimeout,
	}); err != nil {
		return err
	}
	return writeLinks(f.Entry, started.Stdout)
}

func dashboardLink(entry drive.Entry) string {
	body, err := os.ReadFile(filepath.Join(entry.StateDir, "dashboard.json"))
	if err != nil {
		return ""
	}
	var config struct {
		Host string `json:"host"`
		Port int    `json:"port"`
	}
	if err := json.Unmarshal(body, &config); err != nil {
		return ""
	}
	return fmt.Sprintf("http://%s:%d/", config.Host, config.Port)
}

func writeLinks(entry drive.Entry, started string) error {
	links := struct {
		URL     string `json:"url"`
		Tailnet string `json:"tailnet,omitempty"`
		Thread  string `json:"thread"`
	}{Thread: Section}
	for line := range strings.Lines(started) {
		key, value, _ := strings.Cut(strings.TrimSpace(line), ": ")
		switch {
		case key == "url":
			links.URL = value
		case key == "tailnet" && links.Tailnet == "":
			links.Tailnet = value
		}
	}
	if links.URL == "" {
		return fmt.Errorf("cc-present start named no url: %s", strings.TrimSpace(started))
	}
	data, err := json.MarshalIndent(links, "", "  ")
	if err != nil {
		return err
	}
	path := filepath.Join(entry.StateDir, Links)
	if err := os.WriteFile(path+".staged", append(data, '\n'), 0o600); err != nil {
		return err
	}
	return os.Rename(path+".staged", path)
}

func (f *Follower) Handle(ctx context.Context, line []byte) error {
	var note struct {
		Type    string `json:"type"`
		ID      string `json:"id"`
		BlockID string `json:"blockId"`
		Text    string `json:"text"`
	}
	if err := json.Unmarshal(line, &note); err != nil {
		return fmt.Errorf("talk event %q: %w", line, err)
	}
	if note.Type != noteEvent || note.BlockID != Section {
		return nil
	}
	if f.seen == nil {
		notes, err := talkboard.ReadNotes(f.Entry)
		if err != nil {
			return err
		}
		f.seen = make(map[string]bool, len(notes))
		for _, id := range notes {
			f.seen[id] = true
		}
	}
	if f.seen[note.ID] {
		return nil
	}
	path := owner.Ledger(f.Entry.StateDir)
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		return err
	}
	row, err := owner.Add(path, f.Now(), note.Text, opened, 0)
	if err != nil {
		return err
	}
	if err := f.remember(note.ID); err != nil {
		return err
	}
	_, err = f.Runner.Run(ctx, shell.Command{
		Argv: []string{
			"cci", "post", "--drive", f.Entry.Bus(), "--lane", lane, "--kind", "owner", "--to", communicator,
			"--id", "talk:" + note.ID, "--topic", "talk:" + note.ID, "--path", "-",
		},
		Stdin:   fmt.Sprintf("%s, on Talk: %s\n\nAnswer with the reply tool; it lands beneath this note.", row, note.Text),
		Timeout: callTimeout,
	})
	return err
}

func (f *Follower) remember(id string) error {
	file, err := os.OpenFile(talkboard.NotesPath(f.Entry), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	defer file.Close()
	if _, err := fmt.Fprintln(file, id); err != nil {
		return err
	}
	f.seen[id] = true
	return nil
}

func Command(a *app.App) *cobra.Command {
	talk := &cobra.Command{Use: "talk", Short: "The owner's message thread to the communicator"}
	var name string
	var writers []string
	follow := &cobra.Command{
		Use:   "follow",
		Short: "Open this drive's Talk board and turn each note on it into a ledger row and a record for the communicator",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			entry, err := drive.Named(name)
			if err != nil {
				return err
			}
			(&Follower{Runner: a.Runner, Entry: entry, Writers: writers, Now: a.Now, Log: a.Log}).Run(cmd.Context())
			return nil
		},
	}
	follow.Flags().StringVar(&name, "drive", "", "the drive")
	_ = follow.MarkFlagRequired("drive")
	follow.Flags().StringSliceVar(&writers, "writers", nil, "tailnet device names that may post notes; empty leaves it to cc-present's own trust")
	talk.AddCommand(follow)
	return talk
}
