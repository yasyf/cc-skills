package orca

import (
	"bytes"
	"context"
	"os"
	"path/filepath"
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
	r.write(filepath.Join(r.root, "runner.json"), rendered(object{"orca": object{"sprite": object{"env": object{sponsorEnv: sponsorBox}}}}), 0o600)
	return r
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
	return Rejoin{Runner: r.workerFixture, Store: r.store, Config: filepath.Join(r.root, "runner.json"), Sleep: func(wait time.Duration) { r.slept = append(r.slept, wait) }, Out: &r.out}.Pass(context.Background())
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
	r.lane("lane-local", routeLocal, store.LaneDesired, relayFailure)
	r.lane("lane-retired", routeSprite, store.LaneRetired, relayFailure)
	r.reply("sprite exec", workerReply{code: 1, out: noMeshSaid}, workerReply{out: "the sponsor key"}, workerReply{out: "joined mesh default\n"})
	if err := r.pass(); err != nil {
		t.Fatal(err)
	}
	r.wantCalls(append([][]string{statusCall("box-lane-stranded")}, relayCalls("box-lane-stranded")...)...)
	if r.stdins[strings.Join(joinCall("box-lane-stranded"), " ")] != "the sponsor key" {
		t.Fatal(r.stdins)
	}
	said, err := readFileText(receipt)
	if err != nil || !strings.Contains(said, joinedMesh) || strings.Contains(said, "the sponsor key") {
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
	r.reply("sprite exec", workerReply{code: 1, out: noMeshSaid}, workerReply{out: "the sponsor key"}, failing, failing, failing, failing)
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

func TestARunnerWithNoSponsorRepairsNothing(t *testing.T) {
	r := newRejoinFixture(t)
	r.lane("lane-a", routeSprite, store.LaneDesired, relayFailure)
	r.write(filepath.Join(r.root, "runner.json"), rendered(object{"orca": object{}}), 0o600)
	if err := r.pass(); err != nil {
		t.Fatal(err)
	}
	r.wantCalls()
}
