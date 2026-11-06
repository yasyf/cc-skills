package orca

import (
	"bytes"
	"context"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const noMeshSaid = "cci: no relay mesh \"\" joined: run `cci relay join --url <relay url>`\n"

type rejoinFixture struct {
	*workerFixture
	store *store.Store
	out   bytes.Buffer
}

func newRejoinFixture(t *testing.T) *rejoinFixture {
	t.Helper()
	f := newWorkerFixture(t)
	f.sprite()
	s, err := store.Open(filepath.Join(t.TempDir(), "lr.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	r := &rejoinFixture{workerFixture: f, store: s}
	r.write(filepath.Join(r.root, "runner.json"), rendered(object{"orca": object{"sprite": object{"env": object{}}}}), 0o600)
	return r
}

var rejoinNow = time.Date(2026, 11, 6, 9, 0, 0, 0, time.UTC)

func (r *rejoinFixture) delegated(name string, left time.Duration) string {
	r.t.Helper()
	path := filepath.Join(attemptOf(filepath.Join(r.root, "briefs", name+".md")), delegationFile)
	r.write(path, rendered(object{"v": 1, "mesh": "default", "instance_pub": "aW5zdGFuY2U=", "drives": []string{"drive-a"}, "expires": rejoinNow.Add(left).Format(time.RFC3339Nano)}), 0o600)
	return path
}

func (r *rejoinFixture) lane(name, placement string, state store.LaneState, receipt string) string {
	r.t.Helper()
	brief := filepath.Join(r.root, "briefs", name+".md")
	if _, err := r.store.WantLane(store.Lane{Name: name, Brief: brief, Model: "opus", Effort: "xhigh", State: state, Placement: placement}, time.Now()); err != nil {
		r.t.Fatal(err)
	}
	attempt := attemptOf(brief)
	r.write(filepath.Join(attempt, joinReceipt), receipt, 0o600)
	r.write(filepath.Join(attempt, spriteMade), rendered(object{"workspace": "box-" + name}), 0o600)
	return filepath.Join(attempt, joinReceipt)
}

func (r *rejoinFixture) pass() error {
	return Rejoin{Runner: r.workerFixture, Store: r.store, Config: filepath.Join(r.root, "runner.json"), Sleep: func(wait time.Duration) { r.slept = append(r.slept, wait) }, Now: func() time.Time { return rejoinNow }, Out: &r.out}.Pass(context.Background())
}

func readFileText(path string) (string, error) {
	raw, err := os.ReadFile(path)
	return string(raw), err
}

func statusCall(workspace string) []string {
	return []string{"sprite", "exec", "-s", workspace, "--no-stdin", "--", "sh", "-c", relayStatus}
}

func TestAPassJoinsTheStrandedLaneAndRunsNothingOnAnyOther(t *testing.T) {
	r := newRejoinFixture(t)
	receipt := r.lane("lane-stranded", routeSprite, store.LaneDesired, relayFailure)
	r.lane("lane-joined", routeSprite, store.LaneDesired, "joined mesh default at https://relay.example.test/\n")
	r.delegated("lane-joined", 20*time.Hour)
	r.lane("lane-local", routeLocal, store.LaneDesired, relayFailure)
	r.lane("lane-retired", routeSprite, store.LaneRetired, relayFailure)
	r.reply("sprite exec", workerReply{code: 1, out: noMeshSaid}, spriteNodeReply, workerReply{out: "joined mesh default\n"})
	if err := r.pass(); err != nil {
		t.Fatal(err)
	}
	r.wantCalls(append([][]string{statusCall("box-lane-stranded")}, relayCalls("box-lane-stranded")...)...)
	if r.stdins[strings.Join(joinCall("box-lane-stranded"), " ")] != delegationSaid {
		t.Fatal(r.stdins)
	}
	said, err := readFileText(receipt)
	if err != nil || !strings.Contains(said, joinedMesh) {
		t.Fatal(said, err)
	}
	if !strings.Contains(r.out.String(), "lane-stranded rejoined on Sprite box-lane-stranded") {
		t.Fatal(r.out.String())
	}
}

func TestARefusedJoinEndsThePassNamingTheReasonAndLeavesTheNextLane(t *testing.T) {
	r := newRejoinFixture(t)
	r.lane("lane-a", routeSprite, store.LaneDesired, relayFailure)
	r.lane("lane-b", routeSprite, store.LaneDesired, relayFailure)
	failing := workerReply{code: 1, err: relayFailure}
	r.reply("sprite exec", workerReply{code: 1, out: noMeshSaid}, spriteNodeReply, failing, failing, failing, failing)
	err := r.pass()
	if err == nil || !strings.Contains(err.Error(), "the relay failed; retry") || !strings.Contains(err.Error(), "lane-a") {
		t.Fatal(err)
	}
	for _, call := range r.calls {
		if strings.Contains(strings.Join(call, " "), "box-lane-b") {
			t.Fatal("the pass went on past a refusal", call)
		}
	}
}

func TestASpriteAlreadyOnTheMeshGetsAReceiptAndNoJoin(t *testing.T) {
	r := newRejoinFixture(t)
	receipt := r.lane("lane-a", routeSprite, store.LaneDesired, relayFailure)
	r.reply("sprite exec", workerReply{out: "default https://relay.example.test/ node 1 service\n"})
	if err := r.pass(); err != nil {
		t.Fatal(err)
	}
	r.wantCalls(statusCall("box-lane-a"))
	if said, err := readFileText(receipt); err != nil || !strings.Contains(said, joinedMesh) {
		t.Fatal(said, err)
	}
	r.calls = nil
	if err := r.pass(); err != nil {
		t.Fatal(err)
	}
	r.wantCalls()
}

func TestASpriteThatDoesNotAnswerIsSkippedAndKeepsItsReceipt(t *testing.T) {
	r := newRejoinFixture(t)
	receipt := r.lane("lane-a", routeSprite, store.LaneDesired, relayFailure)
	r.reply("sprite exec", workerReply{code: 1, err: "Error: failed to start sprite command: sprite not found\n"})
	if err := r.pass(); err != nil {
		t.Fatal(err)
	}
	r.wantCalls(statusCall("box-lane-a"))
	if said, err := readFileText(receipt); err != nil || said != relayFailure {
		t.Fatal(said, err)
	}
}

func TestARunnerWithNoSpritesRepairsNothing(t *testing.T) {
	r := newRejoinFixture(t)
	r.lane("lane-a", routeSprite, store.LaneDesired, relayFailure)
	r.write(filepath.Join(r.root, "runner.json"), rendered(object{"orca": object{}}), 0o600)
	if err := r.pass(); err != nil {
		t.Fatal(err)
	}
	r.wantCalls()
}

func TestAPassRenewsOnlyTheDelegationsDueWithOneMacCallEachAndReachesNoSprite(t *testing.T) {
	calls := map[int]int{}
	for _, n := range []int{1, 100} {
		r := newRejoinFixture(t)
		r.reply("cci relay delegate", slices.Repeat([]workerReply{{out: delegationSaid}}, n)...)
		var due []string
		for i := range n {
			for _, left := range []time.Duration{time.Hour, 20 * time.Hour} {
				name := fmt.Sprintf("lane-%d-%d", i, int(left.Hours()))
				r.lane(name, routeSprite, store.LaneDesired, "joined mesh default\n")
				path := r.delegated(name, left)
				if left < renewWithin {
					due = append(due, path)
				}
			}
		}
		if err := r.pass(); err != nil {
			t.Fatal(err)
		}
		for _, call := range r.calls {
			if !slices.Equal(call, delegateCall("aW5zdGFuY2U=", "drive-a")) {
				t.Fatal("a renewal pass ran", call)
			}
		}
		calls[n] = len(r.calls)
		if calls[n] != len(due) {
			t.Fatalf("%d lanes due ran %d delegate calls", len(due), calls[n])
		}
		for _, path := range due {
			if said, err := readFileText(path); err != nil || said != delegationSaid {
				t.Fatal(path, said, err)
			}
		}
	}
	if calls[1]*100 != calls[100] {
		t.Fatalf("renewals at N=1: %d, at N=100: %d; want one per due lane", calls[1], calls[100])
	}
}

func TestAJoinedLaneOnTheSharedKeyMovesToItsOwnDelegation(t *testing.T) {
	r := newRejoinFixture(t)
	r.lane("lane-legacy", routeSprite, store.LaneDesired, "joined mesh default\n")
	r.reply("sprite exec", spriteNodeReply, workerReply{out: "joined mesh default\n"})
	if err := r.pass(); err != nil {
		t.Fatal(err)
	}
	r.wantCalls(relayCalls("box-lane-legacy")...)
	if r.stdins[strings.Join(joinCall("box-lane-legacy"), " ")] != delegationSaid {
		t.Fatal(r.stdins)
	}
}

func TestAFailedMoveOffTheSharedKeyLeavesTheLaneJoinedAndThePassGoesOn(t *testing.T) {
	r := newRejoinFixture(t)
	receipt := r.lane("lane-a-legacy", routeSprite, store.LaneDesired, "joined mesh default\n")
	r.lane("lane-b-stranded", routeSprite, store.LaneDesired, relayFailure)
	r.reply("cci relay delegate", workerReply{code: 1, err: "cci: relay: 404 not_found\n"}, workerReply{out: delegationSaid})
	r.reply("sprite exec", spriteNodeReply, workerReply{code: 1, out: noMeshSaid}, spriteNodeReply, workerReply{out: "joined mesh default\n"})
	if err := r.pass(); err != nil {
		t.Fatal(err)
	}
	if said, err := readFileText(receipt); err != nil || said != "joined mesh default\n" {
		t.Fatal(said, err)
	}
	if !strings.Contains(r.out.String(), "lane-a-legacy on Sprite box-lane-a-legacy stays on the shared service key") || !strings.Contains(r.out.String(), "lane-b-stranded rejoined") {
		t.Fatal(r.out.String())
	}
}
