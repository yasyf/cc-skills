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
CREATE TABLE IF NOT EXISTS card_deps (
  dep TEXT NOT NULL, session TEXT NOT NULL, card TEXT NOT NULL, lane TEXT NOT NULL,
  PRIMARY KEY (dep, session, card)
);
CREATE INDEX IF NOT EXISTS card_deps_card ON card_deps (session, card);
CREATE TABLE IF NOT EXISTS pr_states (
  repo TEXT NOT NULL, number INTEGER NOT NULL, branch TEXT NOT NULL, base TEXT NOT NULL, head TEXT NOT NULL,
  change TEXT NOT NULL, stack TEXT NOT NULL, state TEXT NOT NULL, reason TEXT NOT NULL, mover TEXT NOT NULL, deadline TEXT,
  PRIMARY KEY (repo, number)
);
CREATE TABLE IF NOT EXISTS asks (
  repo TEXT NOT NULL, number INTEGER NOT NULL, change TEXT NOT NULL, head TEXT NOT NULL,
  opened TEXT NOT NULL, closed TEXT,
  PRIMARY KEY (repo, number, change)
);
CREATE TRIGGER IF NOT EXISTS pr_states_insert_needs_ask BEFORE INSERT ON pr_states
WHEN (NEW.state = 'awaiting-tap' OR (NEW.state = 'held' AND NEW.mover = 'owner'))
  AND NOT EXISTS (SELECT 1 FROM asks WHERE repo = NEW.repo AND number = NEW.number AND change = NEW.change AND closed IS NULL)
BEGIN SELECT RAISE(ABORT, 'a PR waits on the owner only with an open ask for its change'); END;
CREATE TRIGGER IF NOT EXISTS pr_states_update_needs_ask BEFORE UPDATE ON pr_states
WHEN (NEW.state = 'awaiting-tap' OR (NEW.state = 'held' AND NEW.mover = 'owner'))
  AND NOT EXISTS (SELECT 1 FROM asks WHERE repo = NEW.repo AND number = NEW.number AND change = NEW.change AND closed IS NULL)
BEGIN SELECT RAISE(ABORT, 'a PR waits on the owner only with an open ask for its change'); END;
CREATE TABLE IF NOT EXISTS carries (
  repo TEXT NOT NULL, number INTEGER NOT NULL, from_number INTEGER NOT NULL, from_head TEXT NOT NULL, to_head TEXT NOT NULL,
  proof TEXT NOT NULL CHECK (proof IN ('same-diff', 'fidelity')), grade TEXT, at TEXT NOT NULL,
  CHECK ((proof = 'fidelity') = (grade IS NOT NULL)),
  PRIMARY KEY (repo, number, to_head)
);
CREATE TABLE IF NOT EXISTS grades (
  repo TEXT NOT NULL, number INTEGER NOT NULL, head TEXT NOT NULL, grader TEXT NOT NULL,
  attempt INTEGER NOT NULL, verdict TEXT NOT NULL, record TEXT NOT NULL, posted TEXT NOT NULL,
  PRIMARY KEY (repo, number, head, grader)
);
CREATE TABLE IF NOT EXISTS stacks (repo TEXT NOT NULL, stack TEXT NOT NULL, revision INTEGER NOT NULL, PRIMARY KEY (repo, stack));
CREATE TABLE IF NOT EXISTS owed (
  key TEXT PRIMARY KEY, repo TEXT NOT NULL, stack TEXT NOT NULL, revision INTEGER NOT NULL,
  action TEXT NOT NULL, number INTEGER NOT NULL, head TEXT NOT NULL, created TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS owed_stack ON owed (repo, stack, revision);
CREATE TABLE IF NOT EXISTS due (
  key TEXT PRIMARY KEY, repo TEXT NOT NULL, number INTEGER NOT NULL, kind TEXT NOT NULL, at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS due_at ON due (at);
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
CREATE TABLE IF NOT EXISTS pr_facts (
  repo TEXT NOT NULL, number INTEGER NOT NULL, branch TEXT NOT NULL, base TEXT NOT NULL, head TEXT NOT NULL,
  open INTEGER NOT NULL, merged INTEGER NOT NULL, landed INTEGER NOT NULL DEFAULT 0, at TEXT NOT NULL,
  PRIMARY KEY (repo, number)
);
CREATE INDEX IF NOT EXISTS pr_facts_branch ON pr_facts (repo, branch);
CREATE INDEX IF NOT EXISTS pr_facts_base ON pr_facts (repo, base);
CREATE INDEX IF NOT EXISTS pr_facts_head ON pr_facts (repo, head);
CREATE TABLE IF NOT EXISTS checks (
  repo TEXT NOT NULL, sha TEXT NOT NULL, name TEXT NOT NULL, conclusion TEXT NOT NULL, at TEXT NOT NULL, id INTEGER NOT NULL,
  PRIMARY KEY (repo, sha, name)
);
CREATE TABLE IF NOT EXISTS reviews (
  repo TEXT NOT NULL, number INTEGER NOT NULL, reviewer TEXT NOT NULL, state TEXT NOT NULL, head TEXT NOT NULL, at TEXT NOT NULL,
  PRIMARY KEY (repo, number, reviewer)
);
CREATE TABLE IF NOT EXISTS taps (
  repo TEXT NOT NULL, change TEXT NOT NULL, verdict TEXT NOT NULL, record TEXT NOT NULL, at TEXT NOT NULL,
  PRIMARY KEY (repo, change)
);
CREATE TABLE IF NOT EXISTS holds (
  key TEXT PRIMARY KEY, repo TEXT NOT NULL, number INTEGER NOT NULL, lifter TEXT NOT NULL, reason TEXT NOT NULL,
  until TEXT NOT NULL, opened TEXT NOT NULL, lifted TEXT
);
CREATE INDEX IF NOT EXISTS holds_open ON holds (repo, number) WHERE lifted IS NULL;
CREATE INDEX IF NOT EXISTS holds_until ON holds (repo, until) WHERE lifted IS NULL;
CREATE TABLE IF NOT EXISTS changes (repo TEXT NOT NULL, head TEXT NOT NULL, change TEXT NOT NULL, PRIMARY KEY (repo, head));
CREATE INDEX IF NOT EXISTS grades_head ON grades (repo, head);
CREATE TABLE IF NOT EXISTS behaviors (
  repo TEXT NOT NULL, number INTEGER NOT NULL, head TEXT NOT NULL, since TEXT NOT NULL, behavior TEXT NOT NULL,
  what_changed TEXT NOT NULL, record TEXT NOT NULL, at TEXT NOT NULL,
  PRIMARY KEY (repo, number, head)
);
CREATE TABLE IF NOT EXISTS queue (
  repo TEXT NOT NULL, number INTEGER NOT NULL, head TEXT NOT NULL, first INTEGER NOT NULL, attempt INTEGER NOT NULL,
  outcome TEXT NOT NULL, reason TEXT NOT NULL,
  PRIMARY KEY (repo, number)
);
`

var addedColumns = []struct{ table, column, kind string }{
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
	{"taps", "head", "TEXT NOT NULL DEFAULT ''"},
	{"taps", "number", "INTEGER NOT NULL DEFAULT 0"},
	{"grades", "change", "TEXT NOT NULL DEFAULT ''"},
	{"pr_facts", "change", "TEXT NOT NULL DEFAULT ''"},
	{"queue", "entry", "TEXT NOT NULL DEFAULT ''"},
	{"review_rows", "tap_refused", "TEXT NOT NULL DEFAULT ''"},
}
