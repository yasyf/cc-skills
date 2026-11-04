package board

import (
	"context"
	"strings"
	"testing"
	"testing/synctest"
	"time"

	"github.com/yasyf/cc-inbox/client"
)

type pickBatch struct {
	at   time.Duration
	seqs []int64
}

func batchesOf(t *testing.T, offsets []time.Duration, end time.Duration) []pickBatch {
	t.Helper()
	var batches []pickBatch
	synctest.Test(t, func(t *testing.T) {
		start := time.Now()
		ctx, stop := context.WithDeadline(t.Context(), start.Add(end))
		defer stop()
		records := make(chan client.Record)
		go func() {
			for i, offset := range offsets {
				time.Sleep(time.Until(start.Add(offset)))
				select {
				case records <- client.Record{Seq: int64(i + 1)}:
				case <-ctx.Done():
				}
			}
		}()
		err := batchPicks(ctx, records, time.Minute, func(batch []client.Record) error {
			seqs := make([]int64, len(batch))
			for i, record := range batch {
				seqs[i] = record.Seq
			}
			batches = append(batches, pickBatch{time.Since(start), seqs})
			return nil
		})
		if err != nil {
			t.Fatal(err)
		}
	})
	return batches
}

func TestNinePicksInFifteenMinutesPrintAtMostOneBatchAMinuteAndLoseNone(t *testing.T) {
	offsets := []time.Duration{0, 10 * time.Second, 20 * time.Second, 30 * time.Second, 40 * time.Second, 5 * time.Minute, 5*time.Minute + 5*time.Second, 14 * time.Minute, 14*time.Minute + 30*time.Second}
	batches := batchesOf(t, offsets, 20*time.Minute)
	if len(batches) >= len(offsets) {
		t.Fatalf("%d picks printed as %d batches", len(offsets), len(batches))
	}
	var next int64 = 1
	for i, batch := range batches {
		if i > 0 && batch.at-batches[i-1].at < time.Minute {
			t.Fatalf("batches %d and %d are %s apart: %v", i-1, i, batch.at-batches[i-1].at, batches)
		}
		for _, seq := range batch.seqs {
			if seq != next {
				t.Fatalf("seq %d printed where %d was due: %v", seq, next, batches)
			}
			next++
		}
	}
	if next != int64(len(offsets))+1 {
		t.Fatalf("printed %d of %d picks: %v", next-1, len(offsets), batches)
	}
}

func TestALonePickAfterAQuietMinutePrintsWithinTheSettleAndTheRowsOfOneTapStayTogether(t *testing.T) {
	batches := batchesOf(t, []time.Duration{10 * time.Minute, 10*time.Minute + time.Second}, 15*time.Minute)
	if len(batches) != 1 || len(batches[0].seqs) != 2 || batches[0].at > 10*time.Minute+picksSettle {
		t.Fatalf("batches = %v", batches)
	}
}

func TestPicksHeldWhenTheRunEndsStillPrint(t *testing.T) {
	batches := batchesOf(t, []time.Duration{0, 10 * time.Second}, 30*time.Second)
	if len(batches) != 2 || batches[1].seqs[0] != 2 || batches[1].at != 30*time.Second {
		t.Fatalf("batches = %v", batches)
	}
}

func TestOnlyThisBoardsTapsCountAsPicks(t *testing.T) {
	pick := func(lane, session, event string) client.Record {
		record := client.Record{Lane: lane}
		record.Fields.Attrs = map[string]string{"session": session, "event": event}
		return record
	}
	for _, tt := range []struct {
		name   string
		record client.Record
		want   bool
	}{
		{"a choice on this board", pick(boardWatch, "s1", "choice.selected"), true},
		{"a merge row on this board", pick(boardWatch, "s1", "triage.decided"), true},
		{"another board", pick(boardWatch, "s2", "choice.selected"), false},
		{"a submit, which carries no pick", pick(boardWatch, "s1", submitEvent), false},
		{"an owner record from elsewhere", pick("owner-watch", "s1", "choice.selected"), false},
	} {
		if got := boardPick(tt.record, "s1"); got != tt.want {
			t.Errorf("%s: %v", tt.name, got)
		}
	}
}

func TestABatchPrintsOneHeaderAndEachPickOnItsOwnLine(t *testing.T) {
	text := picksText([]client.Record{{Seq: 7, Text: "picked\n“Leave them”"}, {Seq: 8, Text: "picked “Wait”"}})
	lines := strings.Split(strings.TrimSpace(text), "\n")
	if len(lines) != 3 || lines[0] != "BOARD PICKS 2" || !strings.HasSuffix(lines[1], "picked “Leave them”") || !strings.HasPrefix(lines[2], "#8 ") {
		t.Fatalf("text = %q", text)
	}
}
