package owner

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

const (
	aThread  = "1793831084.387379"
	itsLink  = "https://brook.slack.com/archives/D0B6Z/p1793831084582629?thread_ts=" + aThread + "&channel=D0B6Z&message_ts=1793831084.582629"
	nextLink = "https://brook.slack.com/archives/D0B6Z/p1793000000000002?thread_ts=1793000000.000001"
	inAChan  = "https://brook.slack.com/archives/C0B75AL4XEW/p1793782090255129?thread_ts=1793781346.890479&cid=C0B75AL4XEW"
)

func TestARowsWordsNameItsPlace(t *testing.T) {
	for words, want := range map[string]string{
		itsLink: aThread,
		"Slack thread " + aThread + " (card-x): \"B\"":           aThread,
		"Slack 12:06 AM (thread of card r1): \"why\" " + itsLink: aThread,
		"Owner on board d50e80e8, Land? (x): picked “Land”":      AtRoot,
		"why is the build red":                                   AtRoot,
		inAChan:                                                  AtRoot,
		"why are messages like this unanswered? " + itsLink:      "",
		itsLink + " " + nextLink:                                 "",
		"Slack 2:36 PM: \"is this live\"":                        "",
	} {
		if got := placeInWords(words); got != want {
			t.Errorf("placeInWords(%q) = %q, want %q", words, got, want)
		}
	}
}

func TestPlaceOfReadsTheLatestWordThatNamesARow(t *testing.T) {
	state := t.TempDir()
	path := Ledger(state)
	for _, word := range []wordRows{
		{TS: "1793000000.000009", Thread: "1793000000.000009", Rows: []string{"m1"}},
		{TS: "1793000000.000010", Thread: "1793000000.000001", Rows: []string{"m2.1", "m2.2"}},
		{TS: "1793000000.000011", Rows: []string{"m3"}},
		{TS: "1793000000.000012", Thread: "1793000000.000009", Rows: []string{"m4"}},
		{TS: "1793000000.000013", Thread: "1793000000.000013", Rows: []string{"m4"}},
	} {
		if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
			t.Fatal(err)
		}
		if err := rememberRows(path, word); err != nil {
			t.Fatal(err)
		}
	}
	for row, want := range map[string]string{"m1": AtRoot, "m2.2": "1793000000.000001", "m2": "1793000000.000001", "m3": "", "m4": AtRoot, "m5": ""} {
		got, err := PlaceOf(state, row)
		if err != nil || got != want {
			t.Errorf("PlaceOf(%s) = %q, %v; want %q", row, got, err, want)
		}
	}
	if got, err := RowIn(state, "1793000000.000001"); err != nil || got != "m2.2" {
		t.Errorf("RowIn = %q, %v; want the thread's latest row", got, err)
	}
	if got, err := RowIn(state, "1793000000.000099"); err != nil || got != "" {
		t.Errorf("RowIn = %q, %v; want none for a thread he wrote nothing in", got, err)
	}
}

func TestHisThreadWordRecordsTheThreadItsRowsAnswerIn(t *testing.T) {
	for name, tc := range map[string]struct {
		word threadWord
		want string
	}{
		"a DM thread":      {threadWord{Channel: "D0B6Z", Thread: aThread, TS: "1793831441.972589", Text: "explain how you built it"}, aThread},
		"his DM root":      {threadWord{Channel: "D0B6Z", Thread: "1793831441.972589", TS: "1793831441.972589", Text: "explain how you built it"}, AtRoot},
		"a channel thread": {threadWord{Channel: "C0B75AL4XEW", Thread: aThread, TS: "1793831441.972589", Text: "explain how you built it"}, ""},
	} {
		t.Run(name, func(t *testing.T) {
			state := t.TempDir()
			fake := &shelltest.Fake{}
			fake.Reply(`{}`, "cci", "post")
			rows, _, err := tc.word.rows(context.Background(), fake, drive.Entry{Drive: "d", CCIDrive: "dar", StateDir: state}, cardRoute{})
			if err != nil || len(rows) != 1 {
				t.Fatalf("rows = %v, %v", rows, err)
			}
			got, err := PlaceOf(state, rows[0])
			if err != nil || got != tc.want {
				t.Fatalf("PlaceOf = %q, %v; want %q", got, err, tc.want)
			}
		})
	}
}

func TestOwnerAddRecordsAHandRowHasNoPlace(t *testing.T) {
	state, config := t.TempDir(), t.TempDir()
	t.Setenv("CLAUDE_CONFIG_DIR", config)
	entry, err := json.Marshal(drive.Entry{Drive: "d", StateDir: state, Sessions: []string{"s"}})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(config, "long-running", "drives"), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(config, "long-running", "drives", "d.json"), entry, 0o600); err != nil {
		t.Fatal(err)
	}
	add := func(text string) string {
		var out bytes.Buffer
		cmd := Command(&app.App{Now: time.Now}, noLane)
		cmd.SetArgs([]string{"add", "--drive", "d"})
		cmd.SetIn(strings.NewReader(text))
		cmd.SetOut(&out)
		if err := cmd.Execute(); err != nil {
			t.Fatal(err)
		}
		return strings.TrimSpace(out.String())
	}
	hand, tap := add("is this live"), add("Owner on board d50e80e8, Land? (x): picked “Land”")
	if got, err := PlaceOf(state, hand); err != nil || got != "" {
		t.Errorf("hand row place = %q, %v; want none", got, err)
	}
	if got, err := PlaceOf(state, tap); err != nil || got != AtRoot {
		t.Errorf("tap row place = %q, %v; want his DM root", got, err)
	}
}
