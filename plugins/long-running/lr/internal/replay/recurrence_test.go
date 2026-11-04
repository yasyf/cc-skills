package replay

import (
	"context"
	"encoding/json"
	"errors"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
)

func recurrenceRecords(t *testing.T, lines string, queries *[]client.Query) func(context.Context, client.Query) ([]client.Record, error) {
	t.Helper()
	var records []client.Record
	for _, line := range nonEmptyLines(lines) {
		var record client.Record
		if err := json.Unmarshal([]byte(line), &record); err != nil {
			t.Fatal(err)
		}
		records = append(records, record)
	}
	return func(_ context.Context, query client.Query) ([]client.Record, error) {
		*queries = append(*queries, query)
		var found []client.Record
		for _, record := range records {
			if record.At.Before(query.Since) || len(query.Lanes) > 0 && !slices.Contains(query.Lanes, record.Lane) || len(query.Kinds) > 0 && !slices.Contains(query.Kinds, string(record.Kind)) {
				continue
			}
			found = append(found, record)
		}
		return found, nil
	}
}

func recurrenceLine(t *testing.T, seq int64, at time.Time, lane, kind, id, text string, attrs map[string]string) string {
	t.Helper()
	data, err := json.Marshal(map[string]any{"seq": seq, "at": at, "lane": lane, "kind": kind, "id": id, "text": text, "fields": map[string]any{"attrs": attrs}})
	if err != nil {
		t.Fatal(err)
	}
	return string(data) + "\n"
}

func TestRecurrenceReadsOnlyItsIndexedLaneWhenNothingIsDue(t *testing.T) {
	for _, at := range []time.Time{now.Add(-day + time.Second), now.Add(-4 * day)} {
		t.Run(at.String(), func(t *testing.T) {
			runner := &fakeRunner{}
			var out strings.Builder
			d := gateDeps(t, runner, &out)
			var queries []client.Query
			d.Tail = recurrenceRecords(t, recurrenceLine(t, 1, at, lane, "state", "", passed, map[string]string{"version": "missing", "classes": "x"}), &queries)
			if err := d.Recur(context.Background(), "d1"); err != nil {
				t.Fatal(err)
			}
			want := []client.Query{{Drive: "d1", Since: now.Add(-3 * day), Lanes: []string{lane}}}
			if !reflect.DeepEqual(queries, want) || len(runner.calls) != 0 {
				t.Fatalf("queries %v calls %v", queries, runner.calls)
			}
		})
	}
}

func TestCompletedRecurrenceNeedsNeitherHistoryNorInstalledFixtures(t *testing.T) {
	runner := &fakeRunner{}
	var out strings.Builder
	d := gateDeps(t, runner, &out)
	var queries []client.Query
	lines := recurrenceLine(t, 1, now.Add(-25*time.Hour), lane, "state", "", passed, map[string]string{"version": "missing", "classes": "x"}) +
		recurrenceLine(t, 2, now, lane, "report", recurrenceKey("missing", "x"), "RECURRENCE x", nil)
	d.Tail = recurrenceRecords(t, lines, &queries)
	if err := d.Recur(context.Background(), "d1"); err != nil {
		t.Fatal(err)
	}
	if len(queries) != 1 || len(runner.calls) != 0 {
		t.Fatalf("queries %v calls %v", queries, runner.calls)
	}
}

func TestRecurrenceReadsOnlyUnfinishedKindsAndPreservesDayBoundaries(t *testing.T) {
	start := now.Add(-day)
	attrs := map[string]string{"version": "v1", "classes": "finished,x"}
	lines := recurrenceLine(t, 1, start, lane, "state", "", passed, attrs) +
		recurrenceLine(t, 2, start, lane, "state", "", passed, attrs) +
		recurrenceLine(t, 3, now, lane, "report", recurrenceKey("v1", "finished"), "RECURRENCE finished", nil) +
		recurrenceLine(t, 4, start.Add(-time.Second), "root", "defect", "", "came back", nil) +
		recurrenceLine(t, 5, start, "root", "defect", "", "came back", nil) +
		recurrenceLine(t, 6, now.Add(-time.Nanosecond), "root", "report", "", "CAME BACK", nil) +
		recurrenceLine(t, 7, now, "root", "defect", "", "came back", nil) +
		recurrenceLine(t, 8, start.Add(time.Hour), lane, "report", "", "came back", nil) +
		recurrenceLine(t, 9, start.Add(time.Hour), "root", "note", "", "came back", nil)
	runner := &fakeRunner{}
	var out strings.Builder
	d := gateDeps(t, runner, &out)
	write(t, filepath.Join(d.Installs, "v1", "tests", "fixtures", "replay", "x.json"), `{"defect":12,"fix":"o/r#3","recurs":{"kinds":["report","defect","report"],"text":"came back"}}`)
	var queries []client.Query
	d.Tail = recurrenceRecords(t, lines, &queries)
	if err := d.Recur(context.Background(), "d1"); err != nil {
		t.Fatal(err)
	}
	want := []client.Query{
		{Drive: "d1", Since: now.Add(-3 * day), Lanes: []string{lane}},
		{Drive: "d1", Since: start, Kinds: []string{"defect", "report"}},
	}
	if !reflect.DeepEqual(queries, want) {
		t.Fatalf("queries %v", queries)
	}
	posts := runner.posts()
	if len(posts) != 1 || argAfter(posts[0], "--text") != "RECURRENCE x: 2 in the 24 hours after v1 installed (defect #12, fix o/r#3): #5 #6" {
		t.Fatalf("posts %v", posts)
	}
}

func TestRecurrenceStopsWhenIndexedDiscoveryFails(t *testing.T) {
	runner := &fakeRunner{}
	var out strings.Builder
	d := gateDeps(t, runner, &out)
	want := errors.New("unavailable")
	d.Tail = func(context.Context, client.Query) ([]client.Record, error) { return nil, want }
	if err := d.Recur(context.Background(), "d1"); !errors.Is(err, want) || len(runner.calls) != 0 {
		t.Fatalf("error %v calls %v", err, runner.calls)
	}
}
