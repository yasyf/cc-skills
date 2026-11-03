package store

const schema = `
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
CREATE TABLE IF NOT EXISTS heard (lane TEXT PRIMARY KEY, at TEXT NOT NULL, kind TEXT, seq INTEGER);
CREATE TABLE IF NOT EXISTS owners (pr INTEGER PRIMARY KEY, lane TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS probes (lane TEXT PRIMARY KEY, asked TEXT NOT NULL, dead TEXT);
CREATE TABLE IF NOT EXISTS orders (seq INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS sprite_stops (lane TEXT PRIMARY KEY, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sprite_stop_failures (lane TEXT PRIMARY KEY, failures INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS ledger_rows (
  ledger TEXT NOT NULL, key TEXT NOT NULL, fields TEXT NOT NULL, synced TEXT NOT NULL,
  PRIMARY KEY (ledger, key)
);
CREATE TABLE IF NOT EXISTS card_deps (
  dep TEXT NOT NULL, session TEXT NOT NULL, card TEXT NOT NULL, lane TEXT NOT NULL,
  PRIMARY KEY (dep, session, card)
);
CREATE INDEX IF NOT EXISTS card_deps_card ON card_deps (session, card);
`

var addedColumns = []struct{ table, column, kind string }{
	{"lanes", "waiting", "INTEGER"},
	{"lanes", "answered", "TEXT"},
	{"lanes", "page", "TEXT"},
	{"lanes", "awaits", "TEXT"},
	{"heard", "kind", "TEXT"},
	{"heard", "seq", "INTEGER"},
	{"lanes", "placement", "TEXT"},
	{"lanes", "stopped_on", "INTEGER"},
	{"lanes", "launcher", "TEXT"},
}
