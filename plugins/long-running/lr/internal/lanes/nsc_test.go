package lanes

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"os"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
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

func deadlineRecord(t *testing.T, seq int64, deadline time.Time, attrs ...string) client.Record {
	t.Helper()
	var record client.Record
	line := fmt.Sprintf(`{"seq":%d,"lane":%q,"kind":"evidence","topic":"nsc:%s","fields":{"attrs":{"deadline":%q%s}}}`, seq, perfLane, perfBox, deadline.Format(time.RFC3339), strings.Join(attrs, ""))
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
		return shell.Result{Stdout: `{"cluster_id":"sh4pma4mccmt4"}`}, os.WriteFile(path, []byte(`{"cluster_id":"sh4pma4mccmt4","created":"2026-11-03T18:00:00.042Z","deadline":"2026-11-04T02:00:00.123Z"}`), 0o600)
	})

	printed, err := MakeNscBox(context.Background(), runner, bus, testDrive, perfLane, []string{"--duration", "8h", "--machine_type", "macos/arm64:6x14"})
	if err != nil {
		t.Fatal(err)
	}

	equal(t, ran[:8], []string{"nsc", "create", "--duration", "8h", "--machine_type", "macos/arm64:6x14", "--label", "lane=devbox-perf-1"})
	equal(t, printed, `{"cluster_id":"sh4pma4mccmt4"}`)
	equal(t, len(bus.posted), 1)
	equal(t, []string{bus.posted[0].Kind, bus.posted[0].Lane, bus.posted[0].Topic, bus.posted[0].Attrs["deadline"], bus.posted[0].Attrs["duration"]}, []string{nscKind, perfLane, "nsc:sh4pma4mccmt4", "2026-11-04T02:00:00Z", "8h0m0s"})
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

func renewing(t *testing.T, state store.LaneState, extend func(argv []string) (shell.Result, error)) (*NscDeadlines, *fakeBus) {
	t.Helper()
	s, err := store.Memory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	if _, err := s.WantLane(store.Lane{Name: perfLane, Brief: "/briefs/" + perfLane + ".md", Model: "opus", Effort: "xhigh", State: store.LaneDesired}, t0.Add(-time.Hour)); err != nil {
		t.Fatal(err)
	}
	if err := s.SetLaneState(perfLane, state, t0.Add(-mins(30)), "", ""); err != nil {
		t.Fatal(err)
	}
	bus := &fakeBus{tail: []client.Record{deadlineRecord(t, 1, t0.Add(mins(59)), `,"duration":"8h0m0s"`)}}
	env := Env{Bus: cursorBus{bus}, Config: config.Config{Drive: testDrive}, Runner: scripted(func(argv []string) (shell.Result, error) {
		if argv[1] == "list" {
			return shell.Result{Stdout: labeledBox}, nil
		}
		return extend(argv)
	})}
	return &NscDeadlines{Env: env, Store: s, Log: slog.Default()}, bus
}

func TestABoxWithACreateDurationIsExtendedByItAndItsLaneIsTold(t *testing.T) {
	renewed := t0.Add(mins(59)).Add(8 * time.Hour)
	var extended []string
	watch, bus := renewing(t, store.LaneDesired, func(argv []string) (shell.Result, error) {
		extended = argv
		return shell.Result{Stdout: "New deadline: " + renewed.Format(time.RFC3339) + "\n"}, nil
	})

	checkAt(t, watch, t0)
	checkAt(t, watch, t0.Add(gcEvery))

	equal(t, extended, []string{"nsc", "extend", perfBox, "--duration", "8h0m0s"})
	equal(t, len(bus.posted), 2)
	record, told := bus.posted[0], bus.posted[1]
	equal(t, []string{record.Kind, record.Lane, record.Attrs["deadline"], record.Attrs["duration"]}, []string{nscKind, Self, renewed.Format(time.RFC3339), "8h0m0s"})
	equal(t, []string{told.Kind, told.To[0]}, []string{nscRenewed, perfLane})
	equal(t, told.Text, "NSC-EXTENDED sh4pma4mccmt4: this Namespace box was to die at 10:59 AM, so the supervisor extended it by its create duration, 8h0m0s; it now dies at 6:59 PM. Destroy it with `nsc destroy sh4pma4mccmt4` once its work is done.")
}

func TestABoxNscRefusesToExtendWarnsItsLaneToRecreateIt(t *testing.T) {
	watch, bus := renewing(t, store.LaneDesired, func(argv []string) (shell.Result, error) {
		return shell.Result{Code: 1}, &shell.ExitError{Argv: argv, Code: 1, Stderr: "Failed: maximum duration reached"}
	})

	checkAt(t, watch, t0)

	equal(t, len(bus.posted), 1)
	equal(t, []string{bus.posted[0].Kind, bus.posted[0].To[0]}, []string{nscWarning, perfLane})
	contains(t, bus.posted[0].Text, "nsc refused to extend it: ")
	contains(t, bus.posted[0].Text, "Recreate it with `lr nsc create --drive d1 --lane devbox-perf-1 -- <nsc create flags>`")
}

func TestABoxWhoseLaneRetiredIsLeftToExpireWithOneWarningToTheCoordinator(t *testing.T) {
	watch, bus := renewing(t, store.LaneRetired, func(argv []string) (shell.Result, error) {
		t.Fatalf("extended a retired lane's box: %v", argv)
		return shell.Result{}, nil
	})

	checkAt(t, watch, t0)
	checkAt(t, watch, t0.Add(gcEvery))

	equal(t, len(bus.posted), 1)
	equal(t, []string{bus.posted[0].Kind, bus.posted[0].To[0]}, []string{nscWarning, Root})
	contains(t, bus.posted[0].Text, "its lane devbox-perf-1 has no live registry row, so the supervisor leaves it to expire")
}
