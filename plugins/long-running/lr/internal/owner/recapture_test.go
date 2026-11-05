package owner

import (
	"bytes"
	"context"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

const (
	cutTS      = "1793822112.000100"
	cutThread  = "1793822000.000001"
	wholeTS    = "1793822200.000200"
	cutWords   = "move the heuristics lane onto the new ru"
	wholeWords = "move the heuristics lane onto the new runner and keep the old one until Friday"
	typedLine  = `{"digest": "629293ae14388dc9", "at": "2026-11-04T19:50:00Z", "session": "s1", "source": "prompt", "text": "keep the nightly report"}` + "\n"
	cutLine    = `{"digest": "0a39425242033267", "at": "2026-11-04T19:55:12Z", "session": "s1", "source": "slack", "text": "` + cutWords + `"}` + "\n"
	wholeLine  = `{"digest": "72d2b9383e1ac624", "at": "2026-11-04T19:56:40Z", "session": "s1", "source": "slack", "text": "ship it"}` + "\n"
)

func ownerRecord(seq int64, ts, thread string) client.Record {
	record := client.Record{Seq: seq, ID: slackRecord + ts, Lane: communicator, Kind: ownerKind}
	record.Fields.Attrs = map[string]string{"ts": ts, "thread": thread}
	return record
}

func recaptureFixture(t *testing.T) (*shelltest.Fake, drive.Entry, []client.Record) {
	t.Helper()
	entry := drive.Entry{Checkout: "/drive", StateDir: t.TempDir(), Program: "rebuild"}
	if err := os.WriteFile(filepath.Join(entry.StateDir, capturesFile), []byte(typedLine+cutLine+wholeLine), 0o600); err != nil {
		t.Fatal(err)
	}
	fake := &shelltest.Fake{}
	fake.Reply(`{"user_id":"U1","dm_channel_id":"D0B6Z"}`, "cc-slack", "whoami")
	fake.Reply(`{"messages":[{"ts":"`+cutThread+`","text":"which runner?"},{"ts":"`+cutTS+`","text":"`+wholeWords+`"}]}`, "cc-slack", "thread", "--channel", "D0B6Z", "--thread", cutThread)
	fake.Reply(`{"messages":[{"ts":"`+wholeTS+`","text":"ship it"}]}`, "cc-slack", "thread", "--channel", "D0B6Z", "--thread", wholeTS)
	fake.Reply(`{}`, "cci", "post")
	return fake, entry, []client.Record{ownerRecord(7, cutTS, cutThread), ownerRecord(8, wholeTS, "")}
}

func since(t *testing.T) time.Time {
	t.Helper()
	from, err := time.ParseInLocation(sinceDay, "2026-11-04", pacific)
	if err != nil {
		t.Fatal(err)
	}
	return from
}

func captured(t *testing.T, entry drive.Entry) string {
	t.Helper()
	stored, err := os.ReadFile(filepath.Join(entry.StateDir, capturesFile))
	if err != nil {
		t.Fatal(err)
	}
	return string(stored)
}

func posts(fake *shelltest.Fake) [][]string {
	var found [][]string
	for _, call := range fake.Calls() {
		if call.Argv[0] == "cci" {
			found = append(found, call.Argv)
		}
	}
	return found
}

func TestACutCaptureIsRewrittenWholeAndItsOwnerRecordCorrected(t *testing.T) {
	fake, entry, records := recaptureFixture(t)
	var out bytes.Buffer

	if err := recapture(context.Background(), fake, entry, since(t), records, false, &out); err != nil {
		t.Fatal(err)
	}

	lines := strings.SplitAfter(captured(t, entry), "\n")
	if lines[0] != typedLine || lines[2] != wholeLine {
		t.Fatalf("captures = %q, want the typed and the whole capture untouched", lines)
	}
	if want := `{"at":"2026-11-04T19:55:12Z","digest":"0a39425242033267","session":"s1","source":"slack","text":"` + wholeWords + `"}` + "\n"; lines[1] != want {
		t.Fatalf("cut capture = %q, want %q", lines[1], want)
	}
	sent := posts(fake)
	if len(sent) != 1 {
		t.Fatalf("cci posts = %q, want one correction", sent)
	}
	for _, pair := range [][2]string{{"--kind", correctionKind}, {"--re-seq", "7"}, {"--id", "recapture:slack:" + cutTS}, {"--quoted", wholeWords}, {"--to", communicator}} {
		if at := slices.Index(sent[0], pair[0]); at < 0 || sent[0][at+1] != pair[1] {
			t.Fatalf("correction %q lacks %s %s", sent[0], pair[0], pair[1])
		}
	}
	if !strings.HasSuffix(out.String(), "rewrote 1 of 2 Slack captures since 2026-11-04; 0 left as stored\n") {
		t.Fatalf("printed %q, want one of two rewritten", out.String())
	}
}

func TestADryRunPrintsTheRewriteAndChangesNothing(t *testing.T) {
	fake, entry, records := recaptureFixture(t)
	var out bytes.Buffer

	if err := recapture(context.Background(), fake, entry, since(t), records, true, &out); err != nil {
		t.Fatal(err)
	}

	if got := captured(t, entry); got != typedLine+cutLine+wholeLine {
		t.Fatalf("captures = %q, want them unchanged", got)
	}
	if sent := posts(fake); len(sent) != 0 {
		t.Fatalf("cci posts = %q, want none", sent)
	}
	if !strings.Contains(out.String(), "0a39425242033267 slack:"+cutTS+": 40 of 78 characters stored") || !strings.HasSuffix(out.String(), "would rewrite 1 of 2 Slack captures since 2026-11-04; 0 left as stored\n") {
		t.Fatalf("printed %q, want the cut capture named and one of two to rewrite", out.String())
	}
}

func TestAMessageThatDoesNotReadLeavesItsCaptureAndSaysSo(t *testing.T) {
	_, entry, records := recaptureFixture(t)
	fake := &shelltest.Fake{}
	fake.Reply(`{"user_id":"U1","dm_channel_id":"D0B6Z"}`, "cc-slack", "whoami")
	fake.Fail(1, "ratelimited", "cc-slack", "thread")
	var out bytes.Buffer

	if err := recapture(context.Background(), fake, entry, since(t), records, false, &out); err != nil {
		t.Fatal(err)
	}

	if got := captured(t, entry); got != typedLine+cutLine+wholeLine {
		t.Fatalf("captures = %q, want them unchanged", got)
	}
	if sent := posts(fake); len(sent) != 0 {
		t.Fatalf("cci posts = %q, want none", sent)
	}
	if !strings.Contains(out.String(), "0a39425242033267 slack:"+cutTS+": did not read, left as stored") || !strings.HasSuffix(out.String(), "rewrote 0 of 2 Slack captures since 2026-11-04; 2 left as stored\n") {
		t.Fatalf("printed %q, want the unread message named and both left", out.String())
	}
}

func TestACaptureThatNamesItsSlackMessageReadsWithNoOwnerRecord(t *testing.T) {
	entry := drive.Entry{Checkout: "/drive", StateDir: t.TempDir(), Program: "rebuild"}
	named := `{"digest": "1111222233334444", "at": "2026-11-04T20:00:00Z", "session": "s1", "source": "slack", "text": "` + cutWords + `", "ts": "1793822400.000300", "thread": null, "channel": "D0OTHER"}` + "\n"
	if err := os.WriteFile(filepath.Join(entry.StateDir, capturesFile), []byte(named), 0o600); err != nil {
		t.Fatal(err)
	}
	fake := &shelltest.Fake{}
	fake.Reply(`{"user_id":"U1","dm_channel_id":"D0B6Z"}`, "cc-slack", "whoami")
	fake.Reply(`{"messages":[{"ts":"1793822400.000300","text":"`+wholeWords+`"}]}`, "cc-slack", "thread", "--channel", "D0OTHER", "--thread", "1793822400.000300")
	var out bytes.Buffer

	if err := recapture(context.Background(), fake, entry, since(t), nil, false, &out); err != nil {
		t.Fatal(err)
	}

	if got := captured(t, entry); !strings.Contains(got, `"text":"`+wholeWords+`"`) || !strings.Contains(got, `"channel":"D0OTHER"`) {
		t.Fatalf("captures = %q, want the capture whole with its Slack fields kept", got)
	}
	if sent := posts(fake); len(sent) != 0 {
		t.Fatalf("cci posts = %q, want none with no owner record to correct", sent)
	}
	if !strings.HasSuffix(out.String(), "rewrote 1 of 1 Slack captures since 2026-11-04; 0 left as stored\n") {
		t.Fatalf("printed %q, want the one capture rewritten", out.String())
	}
}
