package incident

import (
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

var storeNow = time.Date(2026, 10, 1, 17, 20, 0, 0, time.UTC)

func newIncident(id string, facts map[string]any) Incident {
	return Incident{
		Header: Header{ID: id, Owner: "incident-" + id, OwnerGeneration: 1, OpenedAt: "2026-10-01T17:20:00Z", InputRevision: 1, Status: StatusOpen},
		Facts:  facts,
	}
}

func created(t *testing.T, facts map[string]any) Store {
	t.Helper()
	s := Store{Root: filepath.Join(t.TempDir(), "incidents")}
	if err := os.MkdirAll(s.Root, 0o750); err != nil {
		t.Fatal(err)
	}
	if err := s.Create(newIncident("i1", facts)); err != nil {
		t.Fatal(err)
	}
	return s
}

func pythonStore(t *testing.T) Store {
	t.Helper()
	root := t.TempDir()
	if err := os.CopyFS(root, os.DirFS("testdata")); err != nil {
		t.Fatal(err)
	}
	return Store{Root: root}
}

func TestCreateWritesThePythonSchema(t *testing.T) {
	s := created(t, map[string]any{"kind": "alert"})
	path, err := s.Path("i1")
	if err != nil {
		t.Fatal(err)
	}
	db, err := openDB(path, readSource)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	rows, err := db.Query("SELECT type, name, sql FROM sqlite_schema WHERE sql IS NOT NULL ORDER BY name")
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	type entry struct {
		Type string `json:"type"`
		Name string `json:"name"`
		SQL  string `json:"sql"`
	}
	var got []entry
	for rows.Next() {
		var row entry
		if err := rows.Scan(&row.Type, &row.Name, &row.SQL); err != nil {
			t.Fatal(err)
		}
		got = append(got, row)
	}
	raw, err := os.ReadFile(filepath.Join("testdata", "schema.golden.json"))
	if err != nil {
		t.Fatal(err)
	}
	var want []entry
	if err := json.Unmarshal(raw, &want); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(got, want) {
		for i := range want {
			if i >= len(got) || got[i] != want[i] {
				t.Fatalf("schema entry %d differs from Python's:\ngot  %+v\nwant %+v", i, got[min(i, len(got)-1)], want[i])
			}
		}
		t.Fatalf("Go created %d schema entries, Python %d", len(got), len(want))
	}
	var version int
	if err := db.QueryRow("PRAGMA user_version").Scan(&version); err != nil {
		t.Fatal(err)
	}
	if version != schemaVersion {
		t.Fatalf("user_version %d, want %d", version, schemaVersion)
	}
}

func TestCreateKeepsAnExistingIncident(t *testing.T) {
	s := created(t, map[string]any{"kind": "alert"})
	if err := s.Create(newIncident("i1", map[string]any{"kind": "pr-review"})); err != nil {
		t.Fatal(err)
	}
	facts, err := s.Facts("i1", "kind")
	if err != nil {
		t.Fatal(err)
	}
	if facts.Kind != "alert" {
		t.Fatalf("kind %q, want the first open's alert", facts.Kind)
	}
}

func TestReadsAPythonIncident(t *testing.T) {
	s := pythonStore(t)
	incident, err := s.Load("python-1")
	if err != nil {
		t.Fatal(err)
	}
	if incident.Owner != "incident-python-1" || incident.OwnerGeneration != 1 || incident.InputRevision != 2 || incident.Status != StatusOpen {
		t.Fatalf("header %+v", incident.Header)
	}
	if got := incident.Actions["comms:ack"]; got.Status != ActionCompleted || got.DispatchID != "7" || got.Response["seq"] != float64(7) {
		t.Fatalf("comms:ack %+v", got)
	}
	if got := incident.Actions["dispatch:fix"]; got.Status != ActionAccepted || got.AuthorityRef != "" {
		t.Fatalf("dispatch:fix %+v", got)
	}
	if open := incident.OpenDecisions(); len(open) != 1 || open[0].Key != "grant:sync" {
		t.Fatalf("open decisions %+v", open)
	}
	if entry, ok := incident.Reached("opened"); !ok || entry.Text != "alert outage" {
		t.Fatalf("milestone %+v", entry)
	}
	facts, err := s.Facts("python-1", "adopted", "grants", "routes", "rulings", "sources", "pipeline", "repair_pr", "human_review")
	if err != nil {
		t.Fatal(err)
	}
	if facts.Adopted["fix"] != "api-fix" || facts.Grants["thread"] != "g-thread" || facts.Routes["fix"][0] != "incident" {
		t.Fatalf("facts %+v", facts)
	}
	if facts.Rulings[0] != "r/1 ~ é" || facts.Sources["rebuild:1@abc"] != 72860 || facts.Pipeline != "" || facts.RepairPR != 0 {
		t.Fatalf("facts %+v", facts)
	}
}

func TestActionAndMetadataRecordsMatchPythonByteForByte(t *testing.T) {
	s := pythonStore(t)
	path, err := s.Path("python-1")
	if err != nil {
		t.Fatal(err)
	}
	db, err := openDB(path, readSource)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	rows, err := db.Query("SELECT record FROM action")
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	seen := 0
	for rows.Next() {
		var record string
		if err := rows.Scan(&record); err != nil {
			t.Fatal(err)
		}
		var action Action
		if err := json.Unmarshal([]byte(record), &action); err != nil {
			t.Fatal(err)
		}
		got, err := encode(action)
		if err != nil {
			t.Fatal(err)
		}
		if got != record {
			t.Fatalf("Go wrote\n%s\nPython wrote\n%s", got, record)
		}
		seen++
	}
	if seen != 2 {
		t.Fatalf("read %d actions, want 2", seen)
	}
	for name, into := range map[string]any{metaDecisions: &[]Decision{}, metaMilestone: &[]Milestone{}} {
		var value string
		if err := db.QueryRow("SELECT value FROM metadata WHERE name = ?", name).Scan(&value); err != nil {
			t.Fatal(err)
		}
		if err := json.Unmarshal([]byte(value), into); err != nil {
			t.Fatal(err)
		}
		got, err := encode(into)
		if err != nil {
			t.Fatal(err)
		}
		if got != value {
			t.Fatalf("%s: Go wrote\n%s\nPython wrote\n%s", name, got, value)
		}
	}
}

func TestWritesOnAPythonIncidentKeepItsFacts(t *testing.T) {
	s := pythonStore(t)
	err := s.Input("python-1", func(tx *Tx) error {
		if err := tx.SetFact("repair_pr", 28998); err != nil {
			return err
		}
		if err := tx.SetChild("grants", "sync", "g-sync"); err != nil {
			return err
		}
		return tx.SetChild("sources", "rebuild:2@def", 72861)
	})
	if err != nil {
		t.Fatal(err)
	}
	facts, err := s.Facts("python-1", "repair_pr", "grants", "sources", "adopted")
	if err != nil {
		t.Fatal(err)
	}
	if facts.RepairPR != 28998 || facts.Grants["sync"] != "g-sync" || facts.Grants["thread"] != "g-thread" {
		t.Fatalf("facts %+v", facts)
	}
	if facts.Sources["rebuild:1@abc"] != 72860 || facts.Sources["rebuild:2@def"] != 72861 || facts.Adopted["fix"] != "api-fix" {
		t.Fatalf("facts %+v", facts)
	}
	header, err := s.Header("python-1")
	if err != nil {
		t.Fatal(err)
	}
	if header.InputRevision != 3 {
		t.Fatalf("input revision %d, want 3", header.InputRevision)
	}
}

func TestFactsReadOnlyTheNamedKeys(t *testing.T) {
	s := created(t, map[string]any{"kind": "alert", "target": "api", "adopted": map[string]string{"fix": "f"}, "adoptedx": "not a child"})
	facts, err := s.Facts("i1", "adopted")
	if err != nil {
		t.Fatal(err)
	}
	if facts.Kind != "" || facts.Target != "" || facts.Adopted["fix"] != "f" || len(facts.Adopted) != 1 {
		t.Fatalf("facts %+v", facts)
	}
}

func TestWrittenSinceReadsOnlyIncidentsWrittenInTheWindow(t *testing.T) {
	s := created(t, map[string]any{"kind": "alert", "target": "executor", "alert": "Datadog 333856815", "repair_pr": 41555, "runbook": "5dbc941"})
	if err := s.Create(newIncident("i0", map[string]any{"target": "api"})); err != nil {
		t.Fatal(err)
	}
	for id, written := range map[string]time.Time{"i0": storeNow.Add(-7 * time.Hour), "i1": storeNow.Add(-time.Hour)} {
		path, err := s.Path(id)
		if err != nil {
			t.Fatal(err)
		}
		if err := os.Chtimes(path, written, written); err != nil {
			t.Fatal(err)
		}
	}
	got, err := s.WrittenSince(storeNow.Add(-6 * time.Hour))
	if err != nil {
		t.Fatal(err)
	}
	want := []Written{{Header: newIncident("i1", nil).Header, Facts: WrittenFacts{Target: "executor", Alert: "Datadog 333856815", RepairPR: 41555}}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("WrittenSince = %+v, want %+v", got, want)
	}
	none, err := Store{Root: filepath.Join(t.TempDir(), "absent")}.WrittenSince(storeNow.Add(-6 * time.Hour))
	if err != nil || len(none) != 0 || none == nil {
		t.Fatalf("WrittenSince on a machine with no store = %#v, %v; want an empty list", none, err)
	}
}

func TestSettingADictReplacesItsChildrenAndCounts(t *testing.T) {
	s := created(t, map[string]any{"accounting": map[string]any{"rerun": 1, "nested": map[string]any{"a": 1}}})
	if err := s.Input("i1", func(tx *Tx) error {
		return tx.SetFact("accounting", map[string]any{"not_ours": "x"})
	}); err != nil {
		t.Fatal(err)
	}
	facts, err := s.Facts("i1", "accounting")
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(facts.Accounting, map[string]any{"not_ours": "x"}) {
		t.Fatalf("accounting %+v", facts.Accounting)
	}
	path, _ := s.Path("i1")
	db, err := openDB(path, readSource)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	var children, total, topLevel int
	if err := db.QueryRow("SELECT child_count FROM fact WHERE path = '/accounting'").Scan(&children); err != nil {
		t.Fatal(err)
	}
	if err := db.QueryRow("SELECT COUNT(*) FROM fact").Scan(&total); err != nil {
		t.Fatal(err)
	}
	if err := db.QueryRow("SELECT fact_count FROM incident").Scan(&topLevel); err != nil {
		t.Fatal(err)
	}
	if children != 1 || total != 2 || topLevel != 1 {
		t.Fatalf("child_count %d, rows %d, fact_count %d", children, total, topLevel)
	}
}

func TestAFactPathEscapesSlashesAndTildes(t *testing.T) {
	if got := factPath("/a", "b/c~d"); got != "/a/b~1c~0d" {
		t.Fatalf("path %q", got)
	}
}

func TestAFormerOwnerWritesNothing(t *testing.T) {
	s := created(t, map[string]any{"kind": "alert"})
	err := s.Owned("i1", 2, func(tx *Tx) error {
		_, err := tx.Accept("x", "send", "t", "", storeNow)
		return err
	})
	if !errors.Is(err, ErrStaleGeneration) {
		t.Fatalf("got %v, want a stale generation", err)
	}
	found, err := s.Actions("i1", "")
	if err != nil {
		t.Fatal(err)
	}
	if len(found) != 0 {
		t.Fatalf("a stale write stored %+v", found)
	}
}

func TestAFailedTransactionWritesNothing(t *testing.T) {
	s := created(t, map[string]any{"kind": "alert"})
	boom := errors.New("boom")
	err := s.Input("i1", func(tx *Tx) error {
		if err := tx.SetFact("mechanism", "m"); err != nil {
			return err
		}
		tx.Status = StatusClosed
		return boom
	})
	if !errors.Is(err, boom) {
		t.Fatalf("got %v", err)
	}
	header, err := s.Header("i1")
	if err != nil {
		t.Fatal(err)
	}
	facts, err := s.Facts("i1", "mechanism")
	if err != nil {
		t.Fatal(err)
	}
	if header.Status != StatusOpen || header.InputRevision != 1 || facts.Mechanism != "" {
		t.Fatalf("rolled back to %+v, %+v", header, facts)
	}
}

func TestAcceptingAgainReturnsTheStoredAction(t *testing.T) {
	s := created(t, nil)
	err := s.Owned("i1", 1, func(tx *Tx) error {
		if _, err := tx.Accept("rebuild:1@abc", "rebuild", "#1", "g", storeNow); err != nil {
			return err
		}
		if _, err := tx.Start("rebuild:1@abc", storeNow, time.Minute); err != nil {
			return err
		}
		again, err := tx.Accept("rebuild:1@abc", "rebuild", "#1", "g", storeNow)
		if err != nil {
			return err
		}
		if again.Status != ActionStarted || again.Deadline != "2026-10-01T17:21:00Z" {
			return fmt.Errorf("accepted %+v", again)
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
}

func TestActionTransitionsRefuseTheWrongState(t *testing.T) {
	s := created(t, nil)
	err := s.Owned("i1", 1, func(tx *Tx) error {
		if _, err := tx.Accept("a", "send", "t", "", storeNow); err != nil {
			return err
		}
		if _, err := tx.Complete("a", nil, ""); err == nil {
			return errors.New("completed an accepted action")
		}
		if _, err := tx.Lose("a"); err == nil {
			return errors.New("lost an accepted action")
		}
		if _, err := tx.Verify("a", map[string]any{"ok": true}); err != nil {
			return err
		}
		if _, err := tx.Fail("a", "late"); err == nil {
			return errors.New("failed a verified action")
		}
		if _, err := tx.Verify("missing", nil); !errors.Is(err, ErrUnknownAction) {
			return fmt.Errorf("verified a missing action: %w", err)
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
}

func TestLoseStartedMarksOnlyStartedActions(t *testing.T) {
	s := created(t, nil)
	err := s.Owned("i1", 1, func(tx *Tx) error {
		for _, key := range []string{"started", "accepted"} {
			if _, err := tx.Accept(key, "send", "t", "", storeNow); err != nil {
				return err
			}
		}
		if _, err := tx.Start("started", storeNow, 0); err != nil {
			return err
		}
		return tx.LoseStarted()
	})
	if err != nil {
		t.Fatal(err)
	}
	lost, err := s.Action("i1", "started")
	if err != nil {
		t.Fatal(err)
	}
	if lost.Status != ActionUnverifiable || !strings.HasPrefix(string(lost.Reason), "response lost") {
		t.Fatalf("lost %+v", lost)
	}
	kept, err := s.Action("i1", "accepted")
	if err != nil {
		t.Fatal(err)
	}
	if kept.Status != ActionAccepted {
		t.Fatalf("kept %+v", kept)
	}
}

func TestAnActionFamilyIsItsBaseAndItsRetries(t *testing.T) {
	s := created(t, nil)
	err := s.Owned("i1", 1, func(tx *Tx) error {
		for _, key := range []string{"rebuild:1@a", "rebuild:1@ab", "rebuild:1@a#2", "rebuild:1@a#3"} {
			if _, err := tx.Accept(key, "rebuild", "t", "", storeNow); err != nil {
				return err
			}
		}
		count, err := tx.ActionFamilySize("rebuild:1@a")
		if err != nil {
			return err
		}
		if count != 3 {
			return fmt.Errorf("family size %d", count)
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	family, err := s.ActionFamily("i1", "rebuild:1@a")
	if err != nil {
		t.Fatal(err)
	}
	keys := make([]string, 0, len(family))
	for _, action := range family {
		keys = append(keys, action.ActionID)
	}
	if !reflect.DeepEqual(keys, []string{"rebuild:1@a", "rebuild:1@a#2", "rebuild:1@a#3"}) {
		t.Fatalf("family %v", keys)
	}
}

func TestActionsFilterByKindAndStatus(t *testing.T) {
	s := created(t, nil)
	err := s.Owned("i1", 1, func(tx *Tx) error {
		for key, kind := range map[string]string{"a": "comms", "b": "rebuild", "c": "rebuild"} {
			if _, err := tx.Accept(key, kind, "t", "", storeNow); err != nil {
				return err
			}
		}
		_, err := tx.Start("c", storeNow, 0)
		return err
	})
	if err != nil {
		t.Fatal(err)
	}
	found, err := s.Actions("i1", "rebuild", ActionStarted, ActionCompleted)
	if err != nil {
		t.Fatal(err)
	}
	if len(found) != 1 || found[0].ActionID != "c" {
		t.Fatalf("found %+v", found)
	}
	all, err := s.Actions("i1", "")
	if err != nil {
		t.Fatal(err)
	}
	if len(all) != 3 {
		t.Fatalf("all %+v", all)
	}
}

func TestOverdueOnlyCountsOpenActionsPastTheirDeadline(t *testing.T) {
	cases := []struct {
		status   ActionStatus
		deadline nullable
		want     bool
	}{
		{ActionStarted, "2026-10-01T17:20:00Z", true},
		{ActionStarted, "2026-10-01T17:20:01Z", false},
		{ActionStarted, "", false},
		{ActionVerified, "2026-10-01T17:00:00Z", false},
		{ActionUnverifiable, "2026-10-01T17:00:00Z", true},
	}
	for _, c := range cases {
		got, err := Action{ActionID: "a", Status: c.status, Deadline: c.deadline}.Overdue(storeNow)
		if err != nil {
			t.Fatal(err)
		}
		if got != c.want {
			t.Errorf("%s due %q: overdue %v, want %v", c.status, c.deadline, got, c.want)
		}
	}
}

func TestDecisionsAndMilestonesLandOncePerKey(t *testing.T) {
	s := created(t, nil)
	err := s.Owned("i1", 1, func(tx *Tx) error {
		for _, want := range []bool{true, false} {
			fresh, err := tx.Decide("k", "text", storeNow)
			if err != nil {
				return err
			}
			if fresh != want {
				return fmt.Errorf("decide fresh %v, want %v", fresh, want)
			}
			fresh, err = tx.Milestone("m", "text", storeNow)
			if err != nil {
				return err
			}
			if fresh != want {
				return fmt.Errorf("milestone fresh %v, want %v", fresh, want)
			}
		}
		return tx.Resolve("k", storeNow)
	})
	if err != nil {
		t.Fatal(err)
	}
	decisions, err := s.Decisions("i1")
	if err != nil {
		t.Fatal(err)
	}
	if len(decisions) != 1 || decisions[0].ResolvedAt != "2026-10-01T17:20:00Z" {
		t.Fatalf("decisions %+v", decisions)
	}
	milestones, err := s.Milestones("i1")
	if err != nil {
		t.Fatal(err)
	}
	if len(milestones) != 1 {
		t.Fatalf("milestones %+v", milestones)
	}
	err = s.Owned("i1", 1, func(tx *Tx) error {
		fresh, err := tx.Decide("k", "again", storeNow)
		if err != nil {
			return err
		}
		if fresh {
			return errors.New("a resolved decision was fresh again")
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
}

func TestConcurrentWritersSerialize(t *testing.T) {
	s := created(t, nil)
	const writers = 16
	var group sync.WaitGroup
	failures := make(chan error, writers)
	for range writers {
		group.Go(func() {
			if err := s.Input("i1", func(*Tx) error { return nil }); err != nil {
				failures <- err
			}
		})
	}
	group.Wait()
	close(failures)
	for err := range failures {
		t.Fatal(err)
	}
	header, err := s.Header("i1")
	if err != nil {
		t.Fatal(err)
	}
	if header.InputRevision != 1+writers {
		t.Fatalf("input revision %d, want %d", header.InputRevision, 1+writers)
	}
}

func TestAMissingIncidentIsNamed(t *testing.T) {
	s := Store{Root: t.TempDir()}
	_, err := s.Header("nope")
	if err == nil || !strings.Contains(err.Error(), "no incident nope") {
		t.Fatalf("got %v", err)
	}
	if err := s.Input("nope", func(*Tx) error { return nil }); err == nil || !strings.Contains(err.Error(), "no incident nope") {
		t.Fatalf("got %v", err)
	}
	if _, err := s.Path("a/b"); err == nil {
		t.Fatal("a path separator in an ID was accepted")
	}
}

func TestReadsRefuseAnotherSchemaVersion(t *testing.T) {
	s := created(t, nil)
	path, _ := s.Path("i1")
	db, err := sql.Open("sqlite", path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec("PRAGMA user_version = 3"); err != nil {
		t.Fatal(err)
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Header("i1"); err == nil || !strings.Contains(err.Error(), "unsupported action store version") {
		t.Fatalf("got %v", err)
	}
}

func TestAHeldStoreSeesAnotherWritersCommitsThroughOneConnection(t *testing.T) {
	s := created(t, map[string]any{"mechanism": nil})
	held, release := s.Held()
	defer release()
	for _, want := range []string{"first", "second"} {
		if err := s.Input("i1", func(tx *Tx) error { return tx.SetFact("mechanism", want) }); err != nil {
			t.Fatal(err)
		}
		facts, err := held.Facts("i1", "mechanism")
		if err != nil {
			t.Fatal(err)
		}
		if facts.Mechanism != want {
			t.Fatalf("mechanism %q, want %q", facts.Mechanism, want)
		}
	}
	if err := held.Input("i1", func(tx *Tx) error { return tx.SetFact("mechanism", "third") }); err != nil {
		t.Fatal(err)
	}
	facts, err := s.Facts("i1", "mechanism")
	if err != nil {
		t.Fatal(err)
	}
	if facts.Mechanism != "third" {
		t.Fatalf("mechanism %q", facts.Mechanism)
	}
	if opened := len(held.held.dbs); opened != 2 {
		t.Fatalf("a held store opened %d connections, want one reader and one writer", opened)
	}
}

func TestAHeldStoreInAPassReadsOnceAndSeesItsOwnAndLaterPassesSeeOtherWriters(t *testing.T) {
	s := created(t, nil)
	held, release := s.Held()
	defer release()
	held.StartPass()
	accept := func(store Store, key string) {
		t.Helper()
		if err := store.Edit("i1", func(tx *Tx) error {
			_, err := tx.Accept(key, "send", "t", "a", storeNow)
			return err
		}); err != nil {
			t.Fatal(err)
		}
	}
	count := func(want int) {
		t.Helper()
		actions, err := held.Actions("i1", "send", ActionAccepted)
		if err != nil {
			t.Fatal(err)
		}
		if len(actions) != want {
			t.Fatalf("%d actions, want %d", len(actions), want)
		}
	}

	count(0)
	path, err := held.Path("i1")
	if err != nil {
		t.Fatal(err)
	}
	if !held.held.known(path) {
		t.Fatal("a read in a pass was not kept")
	}
	accept(held, "own")
	count(1)
	accept(s, "other")
	count(1)
	held.StartPass()
	count(2)
}

func TestAHeldStoreKeepsOnlyItsLimitOfConnectionsOpen(t *testing.T) {
	s := created(t, nil)
	held, release := s.Held()
	defer release()
	for i := range heldLimit + 8 {
		id := fmt.Sprintf("extra-%d", i)
		if err := s.Create(newIncident(id, nil)); err != nil {
			t.Fatal(err)
		}
		if _, err := held.Header(id); err != nil {
			t.Fatal(err)
		}
	}
	if opened := len(held.held.dbs); opened != heldLimit {
		t.Fatalf("%d connections open, want %d", opened, heldLimit)
	}
	if _, err := held.Header("extra-0"); err != nil {
		t.Fatalf("a closed connection should reopen: %v", err)
	}
}

func TestAcceptDueAcceptsLikeAcceptWithADeadlineAndKeepsWhatIsHeld(t *testing.T) {
	s := created(t, nil)
	deadline := storeNow.Add(time.Hour)
	err := s.Edit("i1", func(tx *Tx) error {
		plain, err := tx.Accept("plain", "send", "t", "a", storeNow)
		if err != nil {
			return err
		}
		due, fresh, err := tx.AcceptDue("due", "send", "t", "a", storeNow, deadline)
		if err != nil || !fresh {
			return fmt.Errorf("first accept: fresh=%v: %w", fresh, err)
		}
		if due.Status != plain.Status || due.AcceptedAt != plain.AcceptedAt || due.AuthorityRef != plain.AuthorityRef || due.OwnerGeneration != plain.OwnerGeneration {
			return fmt.Errorf("%+v is not accepted as %+v is", due, plain)
		}
		if string(due.Deadline) != store.Stamp(deadline) {
			return fmt.Errorf("deadline %q", due.Deadline)
		}
		undated, fresh, err := tx.AcceptDue("undated", "send", "t", "a", storeNow, time.Time{})
		if err != nil || !fresh || undated.Deadline != "" {
			return fmt.Errorf("undated accept: %+v fresh=%v: %w", undated, fresh, err)
		}
		held, fresh, err := tx.AcceptDue("due", "other", "other", "b", storeNow.Add(time.Minute), time.Time{})
		if err != nil || fresh {
			return fmt.Errorf("second accept: fresh=%v: %w", fresh, err)
		}
		if held.Target != "t" || held.Kind != "send" || string(held.Deadline) != store.Stamp(deadline) {
			return fmt.Errorf("the held action changed: %+v", held)
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
}

func TestStartByMovesAnActionAsStartDoesAndRefusesWithTheSameText(t *testing.T) {
	s := created(t, nil)
	deadline := storeNow.Add(time.Minute)
	err := s.Edit("i1", func(tx *Tx) error {
		for _, key := range []string{"start", "by"} {
			if _, err := tx.Accept(key, "send", "t", "", storeNow); err != nil {
				return err
			}
		}
		started, err := tx.Start("start", storeNow, time.Minute)
		if err != nil {
			return err
		}
		by, err := tx.StartBy("by", storeNow, deadline, "ctx_1")
		if err != nil {
			return err
		}
		if by.Status != started.Status || by.StartedAt != started.StartedAt || by.Deadline != started.Deadline || by.DispatchID != "ctx_1" {
			return fmt.Errorf("%+v did not start as %+v did", by, started)
		}
		_, startErr := tx.Start("by", storeNow, 0)
		_, byErr := tx.StartBy("by", storeNow, time.Time{}, "")
		var refused *TransitionError
		if !errors.As(byErr, &refused) || startErr == nil || byErr.Error() != startErr.Error() {
			return fmt.Errorf("StartBy said %w, Start said %w", byErr, startErr)
		}
		if byErr.Error() != "by is started, not accepted" {
			return byErr
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
}

func TestRespondWritesTheResponseAndMovesNothing(t *testing.T) {
	s := created(t, nil)
	err := s.Edit("i1", func(tx *Tx) error {
		if _, err := tx.Accept("a", "send", "t", "", storeNow); err != nil {
			return err
		}
		_, err := tx.Respond("a", map[string]any{"text": "kept"})
		return err
	})
	if err != nil {
		t.Fatal(err)
	}
	action, err := s.Action("i1", "a")
	if err != nil {
		t.Fatal(err)
	}
	if action.Status != ActionAccepted || action.Response["text"] != "kept" {
		t.Fatalf("%+v", action)
	}
	missing := s.Edit("i1", func(tx *Tx) error {
		_, err := tx.Respond("missing", nil)
		return err
	})
	if !errors.Is(missing, ErrUnknownAction) {
		t.Fatal(missing)
	}
}

func TestTakeOverNeedsTheOfferAndMovesTheGeneration(t *testing.T) {
	s := created(t, nil)
	before, err := s.Header("i1")
	if err != nil {
		t.Fatal(err)
	}
	refused := s.Edit("i1", func(tx *Tx) error {
		tx.Offer("ctx_1")
		return tx.TakeOver("ctx_2")
	})
	if refused == nil || refused.Error() != "i1 has no pending transfer to ctx_2 (pending: ctx_1)" {
		t.Fatal(refused)
	}
	err = s.Edit("i1", func(tx *Tx) error {
		tx.Offer("ctx_1")
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	offered, err := s.Header("i1")
	if err != nil {
		t.Fatal(err)
	}
	if offered.PendingOwner != "ctx_1" || offered.Owner != before.Owner || offered.OwnerGeneration != before.OwnerGeneration {
		t.Fatalf("%+v", offered)
	}
	err = s.Edit("i1", func(tx *Tx) error { return tx.TakeOver("ctx_1") })
	if err != nil {
		t.Fatal(err)
	}
	taken, err := s.Header("i1")
	if err != nil {
		t.Fatal(err)
	}
	if taken.Owner != "ctx_1" || taken.PendingOwner != "" || taken.OwnerGeneration != before.OwnerGeneration+1 {
		t.Fatalf("%+v", taken)
	}
	stale := s.Owned("i1", before.OwnerGeneration, func(*Tx) error { return nil })
	if !errors.Is(stale, ErrStaleGeneration) {
		t.Fatal(stale)
	}
}
