package actions

import (
	"database/sql"
	"errors"
	"io/fs"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"
)

const incidentSchema = `
CREATE TABLE incident (singleton INTEGER PRIMARY KEY CHECK(singleton = 1), incident_id TEXT NOT NULL, owner TEXT NOT NULL, owner_generation INTEGER NOT NULL, opened_at TEXT NOT NULL, input_revision INTEGER NOT NULL, status TEXT NOT NULL, pending_owner TEXT, action_count INTEGER NOT NULL DEFAULT 0, fact_count INTEGER NOT NULL DEFAULT 0);
CREATE TABLE metadata (name TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE fact (ordinal INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL UNIQUE, parent TEXT NOT NULL, key TEXT NOT NULL, kind TEXT NOT NULL, record TEXT, child_count INTEGER NOT NULL DEFAULT 0);
CREATE TABLE action (ordinal INTEGER PRIMARY KEY AUTOINCREMENT, action_key TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, status TEXT NOT NULL, record TEXT NOT NULL);
`

func write(t *testing.T, root, id string, statements ...string) {
	t.Helper()
	db, err := sql.Open("sqlite", filepath.Join(root, id+fileSuffix)+"?_pragma=journal_mode(WAL)")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	for _, statement := range append([]string{incidentSchema}, statements...) {
		if _, err := db.Exec(statement); err != nil {
			t.Fatalf("%s: %v", statement, err)
		}
	}
}

func TestAnIncidentReadsWhatThePythonStoreWrote(t *testing.T) {
	root := t.TempDir()
	write(t, root, "inc-1",
		`INSERT INTO incident (singleton, incident_id, owner, owner_generation, opened_at, input_revision, status, pending_owner) VALUES (1, 'inc-1', 'landing-desk', 3, '2026-10-30T21:00:00Z', 2, 'open', 'landing-desk-2')`,
		`INSERT INTO metadata VALUES ('decisions', '[{"key":"d1","text":"which"}]'), ('milestones', '[]')`,
		`INSERT INTO fact (path, parent, key, kind, record) VALUES
			('/landing~1queue', '', 'landing/queue', 'dict', NULL),
			('/landing~1queue/depth', '/landing~1queue', 'depth', 'value', '3'),
			('/name', '', 'name', 'value', '"plat"')`,
		`INSERT INTO action (action_key, kind, status, record) VALUES
			('second', 'bus', 'started', '{"incident_id":"inc-1","action_id":"a2","kind":"bus","target":"t2","authority_ref":null,"input_revision":2,"owner_generation":3,"accepted_at":"x","dispatch_id":null,"started_at":"y","deadline":null,"status":"started","response":null,"verification_receipt":null,"reason":null}'),
			('first', 'merge', 'verified', '{"incident_id":"inc-1","action_id":"a1","kind":"merge","target":"t1","authority_ref":"root","input_revision":1,"owner_generation":3,"accepted_at":"x","dispatch_id":"d","started_at":"y","deadline":"z","status":"verified","response":{"ok":true},"verification_receipt":{"seen":1},"reason":"done"}')`,
	)
	got, err := Files{Root: root}.Load("inc-1")
	if err != nil {
		t.Fatal(err)
	}
	want := Incident{
		ID: "inc-1", Owner: "landing-desk", OwnerGeneration: 3, OpenedAt: "2026-10-30T21:00:00Z", InputRevision: 2, Status: "open", PendingOwner: "landing-desk-2",
		Facts:      map[string]any{"landing/queue": map[string]any{"depth": 3.0}, "name": "plat"},
		Decisions:  []map[string]any{{"key": "d1", "text": "which"}},
		Milestones: []map[string]any{},
		Actions: []Action{
			{Key: "second", IncidentID: "inc-1", ActionID: "a2", Kind: "bus", Target: "t2", InputRevision: 2, OwnerGeneration: 3, AcceptedAt: "x", StartedAt: "y", Status: "started"},
			{Key: "first", IncidentID: "inc-1", ActionID: "a1", Kind: "merge", Target: "t1", AuthorityRef: "root", InputRevision: 1, OwnerGeneration: 3, AcceptedAt: "x", DispatchID: "d", StartedAt: "y", Deadline: "z", Status: "verified", Response: map[string]any{"ok": true}, VerificationReceipt: map[string]any{"seen": 1.0}, Reason: "done"},
		},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got  %+v\nwant %+v", got, want)
	}
}

func TestIDsListEveryIncidentFileInOrder(t *testing.T) {
	root := t.TempDir()
	write(t, root, "b")
	write(t, root, "a")
	got, err := Files{Root: root}.IDs()
	if err != nil {
		t.Fatal(err)
	}
	if !slices.Equal(got, []string{"a", "b"}) {
		t.Fatalf("ids %v", got)
	}
}

func TestLoadRefusesWhatCannotBeAnIncident(t *testing.T) {
	root := t.TempDir()
	if _, err := (Files{Root: root}).Load("missing"); !errors.Is(err, fs.ErrNotExist) {
		t.Fatalf("a missing incident: %v", err)
	}
	for _, id := range []string{"", "../escape", "a\x00b"} {
		if _, err := (Files{Root: root}).Load(id); err == nil || !strings.Contains(err.Error(), "filename component") {
			t.Errorf("%q: %v", id, err)
		}
	}
}
