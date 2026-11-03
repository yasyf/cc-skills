package lanes

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"os"
	"slices"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	perfBox      = "sh4pma4mccmt4"
	perfLane     = "devbox-perf-1"
	labeledBox   = `[{"cluster_id":"sh4pma4mccmt4","labels":{"lane":"devbox-perf-1"}}]`
	unlabeledBox = `[{"cluster_id":"sh4pma4mccmt4","labels":{}}]`
)

type cursorBus struct{ *fakeBus }

func (b cursorBus) Tail(ctx context.Context, q client.Query) ([]client.Record, error) {
	records, err := b.fakeBus.Tail(ctx, q)
	return slices.DeleteFunc(slices.Clone(records), func(r client.Record) bool { return r.Seq <= q.After }), err
}

func nscWatch(t *testing.T, listed string, recorded ...client.Record) (*NscDeadlines, *fakeBus) {
	t.Helper()
	bus := &fakeBus{tail: recorded}
	env := Env{Bus: cursorBus{bus}, Config: config.Config{Drive: testDrive}, Runner: scripted(func(argv []string) (shell.Result, error) {
		equal(t, argv, []string{"nsc", "list", "-o", "json"})
		return shell.Result{Stdout: listed}, nil
	})}
	return &NscDeadlines{Env: env, Log: slog.Default()}, bus
}

func deadlineRecord(t *testing.T, seq int64, deadline time.Time) client.Record {
	t.Helper()
	var record client.Record
	line := fmt.Sprintf(`{"seq":%d,"lane":%q,"kind":"evidence","topic":"nsc:%s","fields":{"attrs":{"deadline":%q}}}`, seq, perfLane, perfBox, deadline.Format(time.RFC3339))
	if err := json.Unmarshal([]byte(line), &record); err != nil {
		t.Fatal(err)
	}
	return record
}

func checkAt(t *testing.T, watch *NscDeadlines, at time.Time) {
	t.Helper()
	if err := watch.Check(context.Background(), at); err != nil {
		t.Fatal(err)
	}
}

func TestABox59MinutesFromItsDeadlineWarnsItsLaneOnce(t *testing.T) {
	watch, bus := nscWatch(t, labeledBox, deadlineRecord(t, 1, t0.Add(mins(59))))

	checkAt(t, watch, t0)
	checkAt(t, watch, t0.Add(gcEvery))

	equal(t, len(bus.posted), 1)
	warning := bus.posted[0]
	equal(t, []string{warning.Kind, warning.ID}, []string{nscWarning, fmt.Sprintf("nsc-deadline:%s:%d", perfBox, t0.Add(mins(59)).Unix())})
	equal(t, warning.To, []string{perfLane})
	equal(t, warning.Text, "NSC-DEADLINE sh4pma4mccmt4: this Namespace box dies at 10:59 AM, in 59 minutes. Extend it with `lr nsc extend sh4pma4mccmt4 --drive d1 --lane devbox-perf-1 -- --duration <how much longer>`, or finish its work and destroy it.")
}

func TestABox61MinutesFromItsDeadlineDoesNotWarn(t *testing.T) {
	watch, bus := nscWatch(t, labeledBox, deadlineRecord(t, 1, t0.Add(mins(61))))

	checkAt(t, watch, t0)

	equal(t, len(bus.posted), 0)
}

func TestAnExtendedDeadlineWarnsAgainAnHourBeforeTheNewOne(t *testing.T) {
	first := t0.Add(mins(59))
	extended := first.Add(2 * time.Hour)
	watch, bus := nscWatch(t, labeledBox, deadlineRecord(t, 1, first))
	checkAt(t, watch, t0)
	bus.tail = append(bus.tail, deadlineRecord(t, 2, extended))

	checkAt(t, watch, extended.Add(-mins(65)))
	checkAt(t, watch, extended.Add(-mins(59)))

	equal(t, len(bus.posted), 2)
	equal(t, bus.posted[1].ID, fmt.Sprintf("nsc-deadline:%s:%d", perfBox, extended.Unix()))
	equal(t, bus.posted[1].To, []string{perfLane})
}

func TestAnUnlabeledBoxWarnsTheCoordinator(t *testing.T) {
	watch, bus := nscWatch(t, unlabeledBox, deadlineRecord(t, 1, t0.Add(mins(30))))

	checkAt(t, watch, t0)

	equal(t, len(bus.posted), 1)
	equal(t, bus.posted[0].To, []string{Root})
	contains(t, bus.posted[0].Text, "It carries no lane label, so the warning goes to the coordinator.")
}

func TestABoxWithNoRecordedDeadlineDrawsNoWarning(t *testing.T) {
	watch, bus := nscWatch(t, labeledBox)

	checkAt(t, watch, t0)

	equal(t, len(bus.posted), 0)
}

func TestACreatedBoxCarriesItsLaneAndRecordsTheDeadlineNscReturns(t *testing.T) {
	bus := &fakeBus{}
	var ran []string
	runner := scripted(func(argv []string) (shell.Result, error) {
		ran = argv
		path := argv[slices.Index(argv, "--output_json_to")+1]
		return shell.Result{Stdout: `{"cluster_id":"sh4pma4mccmt4"}`}, os.WriteFile(path, []byte(`{"cluster_id":"sh4pma4mccmt4","deadline":"2026-11-04T02:00:00.123Z"}`), 0o600)
	})

	printed, err := MakeNscBox(context.Background(), runner, bus, testDrive, perfLane, []string{"--duration", "8h", "--machine_type", "macos/arm64:6x14"})
	if err != nil {
		t.Fatal(err)
	}

	equal(t, ran[:8], []string{"nsc", "create", "--duration", "8h", "--machine_type", "macos/arm64:6x14", "--label", "lane=devbox-perf-1"})
	equal(t, printed, `{"cluster_id":"sh4pma4mccmt4"}`)
	equal(t, len(bus.posted), 1)
	equal(t, []string{bus.posted[0].Kind, bus.posted[0].Lane, bus.posted[0].Topic, bus.posted[0].Attrs["deadline"]}, []string{nscKind, perfLane, "nsc:sh4pma4mccmt4", "2026-11-04T02:00:00Z"})
}

func TestAnExtendedBoxRecordsTheNewDeadlineNscPrints(t *testing.T) {
	bus := &fakeBus{}
	runner := scripted(func(argv []string) (shell.Result, error) {
		equal(t, argv, []string{"nsc", "extend", perfBox, "--duration", "2h"})
		return shell.Result{Stdout: "New deadline: 2026-11-04T04:00:00Z\n"}, nil
	})

	if _, err := ExtendNscBox(context.Background(), runner, bus, testDrive, perfLane, perfBox, []string{"--duration", "2h"}); err != nil {
		t.Fatal(err)
	}

	equal(t, len(bus.posted), 1)
	equal(t, []string{bus.posted[0].Topic, bus.posted[0].Attrs["deadline"]}, []string{"nsc:sh4pma4mccmt4", "2026-11-04T04:00:00Z"})
}
