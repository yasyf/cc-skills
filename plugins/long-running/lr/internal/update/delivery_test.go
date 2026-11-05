package update

import (
	"context"
	"reflect"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/asks"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/scaletest"
)

const (
	chatVersionPR  = 48995
	chatVersionSHA = "43caf9bff798"
	chatVersionRow = "Chat versions and old builds"
	runningLine    = "release 12404 running, ETA 1:26 PM"
	liveLine       = "in release 12404, live at 1:40 PM"
)

func chatBook(t *testing.T) asks.Book {
	t.Helper()
	book, err := asks.Read(map[string]map[string]string{
		"48995": {"lane": "chat-divide-3", "state": "landed", "landed_sha": chatVersionSHA, "landed_at": ago(90).Format(time.RFC3339)},
		"49025": {"lane": "chat-divide-3", "title": "the next build reads a chat's version"},
		"49999": {"state": "landed", "landed_sha": "fff", "landed_at": ago(90).Format(time.RFC3339)},
	})
	if err != nil {
		t.Fatal(err)
	}
	return book
}

func chatPin() drive.Pin {
	return drive.Pin{Name: chatVersionRow, Delivers: "a chat carries its version and an old build does not choke on it", Words: []string{"chat-version"}, PRs: []int{chatVersionPR, 49025, 49999}}
}

func tableRow(t *testing.T, text, name string) string {
	t.Helper()
	for _, line := range strings.Split(text, "\n") {
		if strings.HasPrefix(line, "| "+name+" |") {
			return line
		}
	}
	t.Fatalf("no row %q in\n%s", name, text)
	return ""
}

func askedRecord(seq int64, times string) Record {
	asked := record(seq, bridge, "ask", "status of versioning chats and old builds choking on new chats", 30)
	asked.Topic, asked.PRs, asked.Attrs = "chat-version", []string{"Forge-AI/monorepo#48995"}, map[string]string{askedAttr: times}
	return asked
}

func TestAPinnedRowReadsTheReleaseCarryingItsPRsAndNamesNoOtherPersonsPR(t *testing.T) {
	for _, test := range []struct{ name, line, want string }{
		{"landed and running", runningLine, "#48995 release 12404 running, ETA 1:26 PM; #49025 still open"},
		{"landed and live", liveLine, "#48995 in release 12404, live at 1:40 PM; #49025 still open"},
		{"landed and uncut", "landed, no release has cut it yet", "#48995 landed, no release has cut it yet; #49025 still open"},
	} {
		t.Run(test.name, func(t *testing.T) {
			in := inputs(t)
			in.Book, in.Pins, in.Released = chatBook(t), []drive.Pin{chatPin()}, map[int]string{chatVersionPR: test.line}

			row := tableRow(t, Render(in).Text, chatVersionRow)

			if !strings.Contains(row, "| "+test.want+" |") {
				t.Fatalf("row = %s", row)
			}
			if strings.Contains(row, "49999") {
				t.Fatalf("a PR no drive lane holds is on the row: %s", row)
			}
		})
	}
}

func TestAFailedReleaseReadLeavesThePinnedRowAsItReadToday(t *testing.T) {
	in := inputs(t)
	in.Book, in.Pins = chatBook(t), []drive.Pin{chatPin()}

	row := tableRow(t, Render(in).Text, chatVersionRow)

	if !strings.Contains(row, "| no tasks; 0 lanes name it |") || strings.Contains(row, "#48995") {
		t.Fatalf("row = %s", row)
	}
}

func TestAStatusHeAskedThreeTimesLeadsTheUpdateWithItsReleaseAndLiveTime(t *testing.T) {
	in := inputs(t)
	in.Book, in.Released = chatBook(t), map[int]string{chatVersionPR: liveLine}
	in.Leading = leadingAsks([]Record{askedRecord(11, "3")})

	report := Render(in)

	if titles := headings(report.Text); titles[0] != "## "+leadTitle {
		t.Fatalf("titles = %v", titles)
	}
	row := tableRow(t, report.Text, "Asked 3 times: chat-version")
	if !strings.Contains(row, "| #48995 "+liveLine+" |") || !strings.Contains(row, "--re 11 --attr release=<n> --attr live=<time>") {
		t.Fatalf("row = %s", row)
	}
	if left := Missing(report.Text, report.Needs); len(left) != 0 {
		t.Fatalf("missing = %v", left)
	}
	if left := Missing("# an update with nothing in it", report.Needs); !strings.Contains(strings.Join(left, ","), leadTitle) {
		t.Fatalf("a written update may leave the row out: %v", left)
	}
}

func TestOnlyARecordStatingTheReleaseAndLiveTimeClearsAStatusThatLeads(t *testing.T) {
	answer := func(seq int64, attrs map[string]string) Record {
		answered := record(seq, "release-6", "answer", "update-release-row-1 owns it, due later", 20)
		answered.Re, answered.Attrs = 11, attrs
		return answered
	}
	resolved := answer(12, map[string]string{releaseAttr: "12404", liveAttr: "2026-11-03T21:40:00Z"})
	resolved.Re, resolved.Resolves = 0, []int64{11}
	for _, test := range []struct {
		name    string
		records []Record
		leads   int
	}{
		{"asked once is no row", []Record{askedRecord(11, "1")}, 0},
		{"an answer with no fact keeps it first", []Record{askedRecord(11, "3"), answer(12, nil)}, 1},
		{"a release with no live time keeps it first", []Record{askedRecord(11, "3"), answer(12, map[string]string{releaseAttr: "12404"})}, 1},
		{"the release and live time clear it", []Record{askedRecord(11, "3"), answer(12, map[string]string{releaseAttr: "12404", liveAttr: "2026-11-03T21:40:00Z"})}, 0},
		{"a resolving record states them too", []Record{askedRecord(11, "3"), resolved}, 0},
		{"its asker withdraws a status no release answers", []Record{askedRecord(11, "3"), answer(12, map[string]string{askedAttr: "0"})}, 0},
		{"asked again after the fact leads again", []Record{askedRecord(11, "3"), answer(12, map[string]string{releaseAttr: "12404", liveAttr: "2026-11-03T21:40:00Z"}), askedRecord(13, "4")}, 1},
		{"a topic asked three and four times is one row", []Record{askedRecord(11, "3"), askedRecord(13, "4")}, 1},
	} {
		t.Run(test.name, func(t *testing.T) {
			if got := leadingAsks(test.records); len(got) != test.leads {
				t.Fatalf("leading = %+v, want %d", got, test.leads)
			}
		})
	}
}

func TestCollectReadsTheReleaseOnceHoweverManyPRsTheRowsName(t *testing.T) {
	for _, count := range []int{1, scaletest.N()} {
		rows := map[string]map[string]string{}
		var numbers []int
		for number := 1; number <= count; number++ {
			numbers = append(numbers, number)
			rows[strconv.Itoa(number)] = map[string]string{"lane": "chat-divide-3", "state": "landed", "landed_sha": "abc", "landed_at": ago(90).Format(time.RFC3339)}
		}
		rows["49999"] = map[string]string{"state": "landed", "landed_sha": "fff", "landed_at": ago(90).Format(time.RFC3339)}
		book, err := asks.Read(rows)
		if err != nil {
			t.Fatal(err)
		}
		sources := src(nil, nil, nil, nil)
		sources.Book = func(context.Context) (asks.Book, error) { return book, nil }
		sources.Pins = []drive.Pin{{Name: chatVersionRow, PRs: append(numbers, 49999)}}
		reads := 0
		sources.Where = func(_ context.Context, shas map[int]string) map[int]string {
			reads++
			if len(shas) != count || shas[49999] != "" {
				t.Fatalf("read %d shas for %d drive-lane PRs: %v", len(shas), count, shas[49999])
			}
			return nil
		}

		if _, err := Collect(context.Background(), sources, testDrive, now); err != nil {
			t.Fatal(err)
		}
		if reads != 1 {
			t.Fatalf("%d PRs read the release %d times", count, reads)
		}
	}
}

func TestPinningARowAddsItAndAChangeKeepsWhatItDoesNotName(t *testing.T) {
	pins := withPin(nil, drive.Pin{Name: chatVersionRow, Delivers: "versions", Words: []string{"chat-version"}})
	pins = withPin(pins, drive.Pin{Name: chatVersionRow, PRs: []int{chatVersionPR}})

	want := []drive.Pin{{Name: chatVersionRow, Delivers: "versions", Words: []string{"chat-version"}, PRs: []int{chatVersionPR}}}
	if !reflect.DeepEqual(pins, want) {
		t.Fatalf("pins = %+v", pins)
	}
}
