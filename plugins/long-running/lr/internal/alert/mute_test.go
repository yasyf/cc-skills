package alert

import (
	"context"
	"io"
	"slices"
	"strings"
	"testing"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

type runner func(shell.Command) (shell.Result, error)

func (r runner) Run(_ context.Context, c shell.Command) (shell.Result, error) { return r(c) }

type bus struct{ posted []client.Draft }

func (b *bus) Post(_ context.Context, d client.Draft) (client.Record, error) {
	b.posted = append(b.posted, d)
	return client.Record{Seq: int64(len(b.posted))}, nil
}

func (b *bus) Tail(context.Context, client.Query) ([]client.Record, error) { return nil, nil }

func TestFailedMuteChangesNothingElse(t *testing.T) {
	b := &bus{}
	err := Mute{Bus: b, Runner: runner(func(c shell.Command) (shell.Result, error) {
		return shell.Result{}, &shell.ExitError{Argv: c.Argv, Code: 1, Stderr: "403 Forbidden"}
	}), Monitor: "334629581", Why: "spam", Live: []string{"dd-334629581-fix"}, Out: io.Discard}.Run(context.Background())
	if err == nil || !strings.Contains(err.Error(), "not muted") {
		t.Fatalf("err = %v, want a not-muted failure", err)
	}
	if len(b.posted) != 0 {
		t.Fatalf("posted %d records after a failed mute", len(b.posted))
	}
}

func TestMuteRecordsTheDowntimeAndStandsDownOnlyItsLanes(t *testing.T) {
	b := &bus{}
	var stdin string
	err := Mute{Bus: b, Runner: runner(func(c shell.Command) (shell.Result, error) {
		stdin = c.Stdin
		return shell.Result{Stdout: `{"data":{"id":"dt-1","type":"downtime"}}`}, nil
	}), Drive: "d", Monitor: "334629581", Why: "spam", Live: []string{"dd-334629581-fix", "dd-334629581-evidence", "dd-3346295810-fix", "meta-desk"}, Out: io.Discard}.Run(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(stdin, `"monitor_id":334629581`) || strings.Contains(stdin, "schedule") {
		t.Fatalf("downtime body %s is not an indefinite mute of the monitor", stdin)
	}
	muted := b.posted[0]
	if muted.Lane != lanes.Root || muted.Attrs[muteAttr] != "dt-1" || muted.Attrs[monitorAttr] != "334629581" || !slices.Contains(muted.To, lanes.OwnerRelay) {
		t.Fatalf("owner-muted record = %+v", muted)
	}
	var stood []string
	for _, draft := range b.posted[1:] {
		if draft.Kind != "stand-down" || draft.Re != 1 {
			t.Fatalf("stand-down = %+v", draft)
		}
		stood = append(stood, draft.To...)
	}
	if !slices.Equal(stood, []string{"dd-334629581-fix", "dd-334629581-evidence"}) {
		t.Fatalf("stood down %v", stood)
	}
}

func TestMutedFindsOnlyTheCoordinatorsMuteOfThatMonitor(t *testing.T) {
	record := func(seq int64, lane, monitor, mute string) client.Record {
		r := client.Record{Seq: seq, Lane: lane, Kind: mutedKind}
		r.Fields.Attrs = map[string]string{monitorAttr: monitor, muteAttr: mute}
		return r
	}
	records := []client.Record{record(1, "dd-1-fix", "1", "dt-a"), record(2, lanes.Root, "2", "dt-b"), record(3, lanes.Root, "1", "")}
	if _, found := Muted(records, []string{"1"}); found {
		t.Fatal("a lane's own hold or a hold with no downtime counted as an owner mute")
	}
	found, ok := Muted(records, []string{"2", "9"})
	if !ok || found.Seq != 2 {
		t.Fatalf("Muted = %+v, %v; want #2", found, ok)
	}
}
