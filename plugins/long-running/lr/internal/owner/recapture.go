package owner

import (
	"cmp"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	capturesFile   = "owner-prompts.jsonl"
	slackSource    = "slack"
	slackRecord    = "slack:"
	captureStamp   = "2006-01-02T15:04:05Z"
	sinceDay       = "2006-01-02"
	digestChars    = 16
	recaptureLane  = "owner-recapture"
	correctionKind = "correction"
)

type slackCapture struct {
	Digest  string `json:"digest"`
	At      string `json:"at"`
	Session string `json:"session"`
	Source  string `json:"source"`
	Text    string `json:"text"`
	TS      string `json:"ts"`
	Thread  string `json:"thread"`
	Channel string `json:"channel"`
}

type recaptured struct {
	capture  slackCapture
	record   client.Record
	recorded bool
	whole    string
}

func (r recaptured) post(ctx context.Context, runner shell.Runner, bus string) error {
	if !r.recorded {
		return nil
	}
	_, err := runner.Run(ctx, shell.Command{
		Argv: []string{
			"cci", "post", "--drive", bus, "--lane", recaptureLane, "--kind", correctionKind, "--to", communicator,
			"--re-seq", strconv.FormatInt(r.record.Seq, 10), "--id", "recapture:" + r.record.ID,
			"--attr", "ts=" + r.capture.TS, "--attr", "thread=" + r.capture.Thread, "--quoted", r.whole,
		},
		Timeout: rowWait,
	})
	return err
}

func recaptureCommand(a *app.App, driveName *string) *cobra.Command {
	var since string
	var dryRun bool
	cmd := &cobra.Command{
		Use:   "recapture --since DATE",
		Short: "Read each Slack capture of his since DATE whole from Slack, one thread read per message, and rewrite each one stored cut and correct its owner record; a message that does not read stays as stored",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			from, err := time.ParseInLocation(sinceDay, since, pacific)
			if err != nil {
				return fmt.Errorf("--since takes a date such as 2026-11-04, not %q", since)
			}
			entry, err := drive.Named(*driveName)
			if err != nil {
				return err
			}
			records, err := client.Tail(cmd.Context(), client.Query{Drive: entry.Bus(), Kinds: []string{ownerKind}, Since: from})
			if err != nil {
				return err
			}
			return recapture(cmd.Context(), a.Runner, entry, from, records, dryRun, cmd.OutOrStdout())
		},
	}
	cmd.Flags().StringVar(&since, "since", "", "the first day, Pacific, whose Slack captures are read, such as 2026-11-04")
	cmd.Flags().BoolVar(&dryRun, "dry-run", false, "print what would be rewritten and change nothing")
	_ = cmd.MarkFlagRequired("since")
	return cmd
}

func recapture(ctx context.Context, runner shell.Runner, entry drive.Entry, from time.Time, records []client.Record, dryRun bool, out io.Writer) error {
	path := filepath.Join(entry.StateDir, capturesFile)
	stored, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	bySecond := map[string][]client.Record{}
	for _, record := range records {
		if strings.HasPrefix(record.ID, slackRecord) {
			second, _, _ := strings.Cut(record.Fields.Attrs["ts"], ".")
			at, err := strconv.ParseInt(second, 10, 64)
			if err != nil {
				return fmt.Errorf("owner record %s has ts %q: %w", record.Name(), record.Fields.Attrs["ts"], err)
			}
			stamp := time.Unix(at, 0).UTC().Format(captureStamp)
			bySecond[stamp] = append(bySecond[stamp], record)
		}
	}
	dm, err := ownerDM(ctx, runner)
	if err != nil {
		return err
	}
	var found []recaptured
	read, left := 0, 0
	for line := range strings.Lines(string(stored)) {
		if strings.TrimSpace(line) == "" {
			continue
		}
		var capture slackCapture
		if err := json.Unmarshal([]byte(line), &capture); err != nil {
			return fmt.Errorf("%s: %w", capturesFile, err)
		}
		if capture.Source != slackSource {
			continue
		}
		at, err := time.Parse(captureStamp, capture.At)
		if err != nil {
			return fmt.Errorf("%s: capture %s: %w", capturesFile, capture.Digest, err)
		}
		if at.Before(from) {
			continue
		}
		read++
		record, recorded := capture.recordIn(bySecond[capture.At])
		if capture.TS == "" && recorded {
			capture.TS, capture.Thread, capture.Channel = record.Fields.Attrs["ts"], record.Fields.Attrs["thread"], dm
		}
		if capture.TS == "" {
			left++
			fmt.Fprintf(out, "%s %s: names no Slack ts and no owner record names it, left as stored\n", capture.Digest, capture.At)
			continue
		}
		name := slackRecord + capture.TS
		message, err := messageIn(ctx, runner, capture.TS, "--channel", capture.Channel, "--thread", cmp.Or(capture.Thread, capture.TS))
		if err != nil {
			left++
			fmt.Fprintf(out, "%s %s: did not read, left as stored: %v\n", capture.Digest, name, err)
			continue
		}
		whole := redact(message.Text)
		if whole == capture.Text {
			continue
		}
		if !strings.HasPrefix(whole, capture.Text) {
			left++
			fmt.Fprintf(out, "%s %s: Slack's words differ beyond a cut, left as stored\n", capture.Digest, name)
			continue
		}
		fmt.Fprintf(out, "%s %s: %d of %d characters stored, adds %q\n", capture.Digest, name, len([]rune(capture.Text)), len([]rune(whole)), strings.TrimPrefix(whole, capture.Text))
		found = append(found, recaptured{capture: capture, record: record, recorded: recorded, whole: whole})
	}
	if dryRun {
		fmt.Fprintf(out, "would rewrite %d of %d Slack captures since %s; %d left as stored\n", len(found), read, from.Format(sinceDay), left)
		return nil
	}
	whole := map[string]string{}
	for _, change := range found {
		if err := change.post(ctx, runner, entry.Bus()); err != nil {
			left++
			fmt.Fprintf(out, "%s %s: its correction did not post, left as stored: %v\n", change.capture.Digest, change.record.ID, err)
			continue
		}
		whole[change.capture.Digest] = change.whole
	}
	if err := rewriteCaptures(path, whole); err != nil {
		return err
	}
	fmt.Fprintf(out, "rewrote %d of %d Slack captures since %s; %d left as stored\n", len(whole), read, from.Format(sinceDay), left)
	return nil
}

func (c slackCapture) recordIn(records []client.Record) (client.Record, bool) {
	for _, record := range records {
		sum := sha256.Sum256([]byte(c.Session + "\x00" + record.Fields.Attrs["ts"]))
		if hex.EncodeToString(sum[:])[:digestChars] == c.Digest {
			return record, true
		}
	}
	return client.Record{}, false
}

func ownerDM(ctx context.Context, runner shell.Runner) (string, error) {
	shown, err := runner.Run(ctx, shell.Command{Argv: []string{"cc-slack", "whoami"}, Timeout: slackCall})
	if err != nil {
		return "", err
	}
	var me struct {
		DM string `json:"dm_channel_id"`
	}
	if err := json.Unmarshal([]byte(shown.Stdout), &me); err != nil {
		return "", fmt.Errorf("cc-slack whoami printed %q: %w", shown.Stdout, err)
	}
	return me.DM, nil
}

func rewriteCaptures(path string, whole map[string]string) error {
	if len(whole) == 0 {
		return nil
	}
	stored, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	var rewritten strings.Builder
	for line := range strings.Lines(string(stored)) {
		var capture map[string]json.RawMessage
		var digest string
		if strings.TrimSpace(line) != "" {
			if err := json.Unmarshal([]byte(line), &capture); err != nil {
				return fmt.Errorf("%s: %w", capturesFile, err)
			}
			if err := json.Unmarshal(capture["digest"], &digest); err != nil {
				return fmt.Errorf("%s: %w", capturesFile, err)
			}
		}
		said, ok := whole[digest]
		if !ok {
			rewritten.WriteString(line)
			continue
		}
		text, err := json.Marshal(said)
		if err != nil {
			return err
		}
		capture["text"] = text
		encoded, err := json.Marshal(capture)
		if err != nil {
			return err
		}
		rewritten.Write(append(encoded, '\n'))
	}
	staged := path + ".staged"
	if err := os.WriteFile(staged, []byte(rewritten.String()), 0o600); err != nil {
		return err
	}
	return os.Rename(staged, path)
}
