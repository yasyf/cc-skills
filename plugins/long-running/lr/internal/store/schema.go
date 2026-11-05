package store

const schema = `
CREATE TABLE IF NOT EXISTS release_targets (
  sha TEXT PRIMARY KEY, targets TEXT NOT NULL, landed TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS prs (
  repo TEXT NOT NULL, number INTEGER NOT NULL, head TEXT NOT NULL, base TEXT NOT NULL,
  lane TEXT NOT NULL, state TEXT NOT NULL, read_at TEXT NOT NULL,
  PRIMARY KEY (repo, number)
);
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL, repo TEXT NOT NULL, number INTEGER NOT NULL, head TEXT NOT NULL,
  mover TEXT NOT NULL, worker TEXT,
  created TEXT NOT NULL, deadline TEXT NOT NULL, closed TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS items_open ON items (repo, number, head, kind) WHERE closed IS NULL;
CREATE TABLE IF NOT EXISTS facts (
  repo TEXT NOT NULL, number INTEGER NOT NULL, head TEXT NOT NULL, kind TEXT NOT NULL,
  value TEXT NOT NULL, posted TEXT NOT NULL,
  PRIMARY KEY (repo, number, head, kind)
);
CREATE TABLE IF NOT EXISTS escalations (
  subject TEXT NOT NULL, cause TEXT NOT NULL, to_mover TEXT NOT NULL, raised TEXT NOT NULL,
  PRIMARY KEY (subject, cause)
);
CREATE TABLE IF NOT EXISTS notices (subject TEXT PRIMARY KEY, at TEXT NOT NULL);
INSERT OR IGNORE INTO notices (subject, at) SELECT subject, raised FROM escalations WHERE cause IN ('appeal-counted', 'override-owner', 'claim', 'launch') OR subject LIKE 'override-warning:%';
DELETE FROM escalations WHERE cause IN ('appeal-counted', 'override-owner', 'claim', 'launch') OR subject LIKE 'override-warning:%';
CREATE TABLE IF NOT EXISTS workers (
  name TEXT PRIMARY KEY, generation INTEGER NOT NULL, heartbeat TEXT NOT NULL,
  item INTEGER, context_left INTEGER
);
CREATE TABLE IF NOT EXISTS lease (
  drive TEXT PRIMARY KEY, generation INTEGER NOT NULL, version TEXT NOT NULL,
  pid INTEGER NOT NULL, renewed TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rules (
  id TEXT PRIMARY KEY, words TEXT NOT NULL, said TEXT NOT NULL,
  check_kind TEXT CHECK (check_kind IN ('predicate', 'judgment')), check_ref TEXT,
  CHECK ((check_kind IS NULL) = (check_ref IS NULL))
);
CREATE TABLE IF NOT EXISTS cursors (source TEXT PRIMARY KEY, position INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS lanes (
  name TEXT PRIMARY KEY, brief TEXT NOT NULL, model TEXT NOT NULL, effort TEXT NOT NULL, role TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('launching', 'desired', 'held', 'retired')),
  since TEXT NOT NULL, handoff TEXT,
  predecessor TEXT, reason TEXT, strikes INTEGER NOT NULL DEFAULT 0, asked TEXT, flushed TEXT, retired_by TEXT, waiting INTEGER, answered TEXT, page TEXT, awaits TEXT, placement TEXT
);
CREATE TABLE IF NOT EXISTS heard (lane TEXT PRIMARY KEY, at TEXT NOT NULL, kind TEXT, seq INTEGER, node TEXT, late_seconds INTEGER);
CREATE TABLE IF NOT EXISTS owners (pr INTEGER PRIMARY KEY, lane TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS owners_lane ON owners (lane);
CREATE TABLE IF NOT EXISTS probes (lane TEXT PRIMARY KEY, asked TEXT NOT NULL, dead TEXT);
CREATE TABLE IF NOT EXISTS orders (seq INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS unread (
  lane TEXT NOT NULL, seq INTEGER NOT NULL, at TEXT NOT NULL, author TEXT NOT NULL, kind TEXT NOT NULL,
  woke INTEGER NOT NULL DEFAULT 0, raised INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (lane, seq)
);
CREATE TABLE IF NOT EXISTS sprite_stops (lane TEXT PRIMARY KEY, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sprite_stop_failures (lane TEXT PRIMARY KEY, failures INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS sprite_tools (sprite TEXT NOT NULL, name TEXT NOT NULL, version TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY (sprite, name));
CREATE TABLE IF NOT EXISTS bad_tools (name TEXT NOT NULL, version TEXT NOT NULL, why TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY (name, version));
CREATE TABLE IF NOT EXISTS ledger_rows (
  ledger TEXT NOT NULL, key TEXT NOT NULL, fields TEXT NOT NULL, synced TEXT NOT NULL,
  PRIMARY KEY (ledger, key)
);
CREATE TABLE IF NOT EXISTS branch_cards (
  lane TEXT NOT NULL, card TEXT NOT NULL, answered TEXT, ordered TEXT, held TEXT,
  PRIMARY KEY (lane, card)
);
CREATE TABLE IF NOT EXISTS card_takers (card TEXT PRIMARY KEY, lane TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS card_deps (
  dep TEXT NOT NULL, session TEXT NOT NULL, card TEXT NOT NULL, lane TEXT NOT NULL,
  PRIMARY KEY (dep, session, card)
);
CREATE INDEX IF NOT EXISTS card_deps_card ON card_deps (session, card);
CREATE TABLE IF NOT EXISTS slack_threads (
  ts TEXT PRIMARY KEY, reply TEXT NOT NULL, card TEXT NOT NULL, lane TEXT NOT NULL,
  ledger_row TEXT NOT NULL, question TEXT NOT NULL, options TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alert_threads (alert TEXT PRIMARY KEY, reply TEXT NOT NULL, ts TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS review_rows (
  repo TEXT NOT NULL, number INTEGER NOT NULL, title TEXT NOT NULL, lane TEXT NOT NULL, branch TEXT NOT NULL,
  base TEXT NOT NULL, parent TEXT NOT NULL, parent_state TEXT NOT NULL, head TEXT NOT NULL,
  head_at TEXT NOT NULL, read_head TEXT NOT NULL, for_users TEXT NOT NULL, plan_line TEXT NOT NULL,
  plan_row TEXT NOT NULL, risk TEXT NOT NULL, description TEXT NOT NULL, unsafe_wait TEXT NOT NULL,
  ci TEXT NOT NULL, approvers TEXT NOT NULL, review TEXT NOT NULL, grade TEXT NOT NULL,
  grade_findings TEXT NOT NULL, mergeable TEXT NOT NULL, queue TEXT NOT NULL, hold TEXT NOT NULL,
  superseded_by TEXT NOT NULL, waits_on TEXT NOT NULL, ready_for_tap INTEGER NOT NULL, state TEXT NOT NULL,
  tapped INTEGER NOT NULL, tapped_at TEXT NOT NULL, tapped_by TEXT NOT NULL, landed_at TEXT NOT NULL,
  last_record INTEGER NOT NULL, written_at TEXT NOT NULL,
  PRIMARY KEY (repo, number)
);
DROP TABLE IF EXISTS pr_states;
DROP TABLE IF EXISTS asks;
DROP TABLE IF EXISTS carries;
DROP TABLE IF EXISTS grades;
DROP TABLE IF EXISTS stacks;
DROP TABLE IF EXISTS owed;
DROP TABLE IF EXISTS due;
DROP TABLE IF EXISTS pr_facts;
DROP TABLE IF EXISTS checks;
DROP TABLE IF EXISTS reviews;
DROP TABLE IF EXISTS taps;
DROP TABLE IF EXISTS holds;
DROP TABLE IF EXISTS changes;
DROP TABLE IF EXISTS behaviors;
DROP TABLE IF EXISTS queue;
`

var addedColumns = []struct{ table, column, kind string }{
	{"unread", "declined", "INTEGER NOT NULL DEFAULT 0"},
	{"sprite_stop_failures", "blocker", "INTEGER"},
	{"sprite_stop_failures", "left_at", "TEXT"},
	{"lanes", "waiting", "INTEGER"},
	{"lanes", "answered", "TEXT"},
	{"lanes", "page", "TEXT"},
	{"lanes", "awaits", "TEXT"},
	{"heard", "kind", "TEXT"},
	{"heard", "seq", "INTEGER"},
	{"heard", "node", "TEXT"},
	{"heard", "late_seconds", "INTEGER"},
	{"lanes", "placement", "TEXT"},
	{"lanes", "stopped_on", "INTEGER"},
	{"lanes", "launcher", "TEXT"},
	{"lanes", "standing", "TEXT"},
	{"review_rows", "tap_refused", "TEXT NOT NULL DEFAULT ''"},
	{"review_rows", "awaits_tap", "TEXT NOT NULL DEFAULT ''"},
	{"review_rows", "port", "TEXT NOT NULL DEFAULT ''"},
	{"review_rows", "moved_at", "TEXT NOT NULL DEFAULT ''"},
}
