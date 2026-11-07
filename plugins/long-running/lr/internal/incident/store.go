package incident

import (
	"bytes"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"net/url"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
	_ "modernc.org/sqlite"
)

const (
	schemaVersion = 2
	fileSuffix    = ".sqlite3"
	readSource    = "mode=ro&_pragma=busy_timeout(5000)"
	writeSource   = "mode=rw&_pragma=busy_timeout(5000)&_pragma=synchronous(FULL)&_txlock=immediate"
	headerColumns = "incident_id, owner, owner_generation, opened_at, input_revision, status, pending_owner"
	metaDecisions = "decisions"
	metaMilestone = "milestones"
	heldLimit     = 64
)

type Store struct {
	Root string
	held *handles
}

type handles struct {
	mu      sync.Mutex
	dbs     map[string]*heldDB
	uses    int64
	passing bool
	reads   map[string]*heldReads
}

type heldDB struct {
	db    *sql.DB
	used  int64
	users int
}

type heldReads struct {
	version storeVersion
	values  map[string]any
}

type fileVersion struct {
	modified int64
	size     int64
}

type storeVersion struct {
	db  fileVersion
	wal fileVersion
}

func versionOf(path string) (storeVersion, error) {
	db, err := os.Stat(path)
	if err != nil {
		return storeVersion{}, err
	}
	version := storeVersion{db: fileVersion{db.ModTime().UnixNano(), db.Size()}}
	wal, err := os.Stat(path + "-wal")
	switch {
	case err == nil:
		version.wal = fileVersion{wal.ModTime().UnixNano(), wal.Size()}
	case !errors.Is(err, fs.ErrNotExist):
		return storeVersion{}, err
	}
	return version, nil
}

func (h *handles) get(path, query string) (*sql.DB, func(), error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	key := query + "\x00" + path
	held, found := h.dbs[key]
	if !found {
		db, err := openDB(path, query)
		if err != nil {
			return nil, nil, err
		}
		held = &heldDB{db: db}
		h.dbs[key] = held
	}
	h.uses++
	held.used = h.uses
	held.users++
	h.evict()
	return held.db, func() {
		h.mu.Lock()
		defer h.mu.Unlock()
		held.users--
	}, nil
}

func (h *handles) evict() {
	for len(h.dbs) > heldLimit {
		oldest := ""
		for key, held := range h.dbs {
			if held.users == 0 && (oldest == "" || held.used < h.dbs[oldest].used) {
				oldest = key
			}
		}
		if oldest == "" {
			return
		}
		_ = h.dbs[oldest].db.Close()
		delete(h.dbs, oldest)
	}
}

func (h *handles) close() {
	h.mu.Lock()
	defer h.mu.Unlock()
	for _, held := range h.dbs {
		_ = held.db.Close()
	}
}

func (h *handles) startPass() {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.passing = true
	for path, kept := range h.reads {
		if version, err := versionOf(path); err != nil || version != kept.version {
			delete(h.reads, path)
		}
	}
}

func (h *handles) kept(path, key string) (any, bool) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if kept := h.reads[path]; kept != nil {
		value, found := kept.values[key]
		return value, found
	}
	return nil, false
}

func (h *handles) keep(path, key string, version storeVersion, value any) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if !h.passing {
		return
	}
	kept := h.reads[path]
	if kept == nil || kept.version != version {
		kept = &heldReads{version: version, values: map[string]any{}}
		h.reads[path] = kept
	}
	kept.values[key] = value
}

func (h *handles) known(path string) bool {
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.reads[path] != nil
}

func (h *handles) forget(path string) {
	h.mu.Lock()
	defer h.mu.Unlock()
	delete(h.reads, path)
}

func heldRead[T any](s Store, id, key string, read func() (T, error)) (T, error) {
	if s.held == nil {
		return read()
	}
	path, err := s.Path(id)
	if err != nil {
		return read()
	}
	if value, found := s.held.kept(path, key); found {
		return value.(T), nil
	}
	version, err := versionOf(path)
	if err != nil {
		return read()
	}
	value, err := read()
	if err == nil {
		s.held.keep(path, key, version, value)
	}
	return value, err
}

func (s Store) Held() (Store, func()) {
	held := &handles{dbs: map[string]*heldDB{}, reads: map[string]*heldReads{}}
	s.held = held
	return s, held.close
}

func (s Store) StartPass() {
	if s.held != nil {
		s.held.startPass()
	}
}

func (s Store) connect(path, query string) (*sql.DB, func(), error) {
	if s.held != nil {
		return s.held.get(path, query)
	}
	db, err := openDB(path, query)
	if err != nil {
		return nil, nil, err
	}
	return db, func() { _ = db.Close() }, nil
}

func DefaultRoot() (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, ".claude", "long-running", "incidents"), nil
}

func (s Store) IDs() ([]string, error) {
	paths, err := filepath.Glob(filepath.Join(s.Root, "*"+fileSuffix))
	if err != nil {
		return nil, err
	}
	ids := make([]string, 0, len(paths))
	for _, path := range paths {
		ids = append(ids, strings.TrimSuffix(filepath.Base(path), fileSuffix))
	}
	return ids, nil
}

func (s Store) Path(id string) (string, error) {
	if id == "" || strings.ContainsAny(id, "/\x00") {
		return "", fmt.Errorf("incident ID %q must be a nonempty filename component", id)
	}
	return filepath.Join(s.Root, id+fileSuffix), nil
}

func (s Store) locked(id string, fn func(path string) error) error {
	path, err := s.Path(id)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(s.Root, 0o750); err != nil {
		return err
	}
	lock, err := os.OpenFile(filepath.Join(s.Root, "."+id+".lock"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	defer lock.Close()
	if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_EX); err != nil {
		return err
	}
	return fn(path)
}

func openDB(path, query string) (*sql.DB, error) {
	source := url.URL{Scheme: "file", Path: path, RawQuery: query}
	db, err := sql.Open("sqlite", source.String())
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	return db, nil
}

func checkVersion(tx *sql.Tx, path string) error {
	var version int
	if err := tx.QueryRow("PRAGMA user_version").Scan(&version); err != nil {
		return err
	}
	if version != schemaVersion {
		return fmt.Errorf("unsupported action store version %d: %s", version, path)
	}
	return nil
}

func (s Store) exists(path, id string) error {
	if _, err := os.Stat(path); errors.Is(err, fs.ErrNotExist) {
		return fmt.Errorf("no incident %s in %s", id, s.Root)
	} else if err != nil {
		return err
	}
	return nil
}

func (s Store) read(id string, fn func(*sql.Tx) error) error {
	path, err := s.Path(id)
	if err != nil {
		return err
	}
	if err := s.exists(path, id); err != nil {
		return err
	}
	db, release, err := s.connect(path, readSource)
	if err != nil {
		return err
	}
	defer release()
	tx, err := db.Begin()
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback() }()
	if err := checkVersion(tx, path); err != nil {
		return err
	}
	return fn(tx)
}

type Written struct {
	Header
	Facts WrittenFacts `json:"facts"`
}

type WrittenFacts struct {
	Target   string `json:"target"`
	Alert    string `json:"alert"`
	RepairPR int    `json:"repair_pr"`
	NotOurs  string `json:"not_ours"`
}

func (s Store) WrittenSince(since time.Time) ([]Written, error) {
	ids, err := s.IDs()
	if err != nil {
		return nil, err
	}
	written := []Written{}
	for _, id := range ids {
		path, err := s.Path(id)
		if err != nil {
			return nil, err
		}
		info, err := os.Stat(path)
		if err != nil {
			return nil, err
		}
		if info.ModTime().Before(since) {
			continue
		}
		var incident Written
		err = s.read(id, func(tx *sql.Tx) error {
			header, err := readHeader(tx)
			if err != nil {
				return err
			}
			facts, err := readFacts(tx, []string{"target", "alert", "repair_pr", "not_ours"})
			incident = Written{Header: header, Facts: WrittenFacts{Target: facts.Target, Alert: facts.Alert, RepairPR: facts.RepairPR, NotOurs: facts.NotOurs}}
			return err
		})
		if err != nil {
			return nil, err
		}
		written = append(written, incident)
	}
	return written, nil
}

func (s Store) Exists(id string) (bool, error) {
	path, err := s.Path(id)
	if err != nil {
		return false, err
	}
	if s.held != nil && s.held.known(path) {
		return true, nil
	}
	_, err = os.Stat(path)
	if errors.Is(err, fs.ErrNotExist) {
		return false, nil
	}
	return err == nil, err
}

func encode(value any) (string, error) {
	var out bytes.Buffer
	encoder := json.NewEncoder(&out)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(value); err != nil {
		return "", err
	}
	return strings.TrimSuffix(out.String(), "\n"), nil
}

func (s Store) Create(incident Incident) error {
	return s.locked(incident.ID, func(path string) error {
		switch _, err := os.Stat(path); {
		case err == nil:
			return nil
		case !errors.Is(err, fs.ErrNotExist):
			return err
		}
		return s.stage(incident, path)
	})
}

func (s Store) stage(incident Incident, path string) error {
	staged, err := os.CreateTemp(s.Root, "."+filepath.Base(path)+".")
	if err != nil {
		return err
	}
	stagedPath := staged.Name()
	defer os.Remove(stagedPath)
	if err := staged.Close(); err != nil {
		return err
	}
	if err := writeInitial(stagedPath, incident); err != nil {
		return err
	}
	if err := syncPath(stagedPath); err != nil {
		return err
	}
	if err := os.Rename(stagedPath, path); err != nil {
		return err
	}
	return syncPath(s.Root)
}

func syncPath(path string) error {
	handle, err := os.Open(path)
	if err != nil {
		return err
	}
	defer handle.Close()
	return handle.Sync()
}

func writeInitial(path string, incident Incident) error {
	db, err := openDB(path, "_pragma=busy_timeout(5000)")
	if err != nil {
		return err
	}
	defer db.Close()
	for _, statement := range []string{"PRAGMA journal_mode = WAL", "PRAGMA synchronous = FULL", schema} {
		if _, err := db.Exec(statement); err != nil {
			return err
		}
	}
	tx, err := db.Begin()
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback() }()
	if _, err := tx.Exec(
		"INSERT INTO incident(singleton, "+headerColumns+") VALUES (1, ?, ?, ?, ?, ?, ?, ?)",
		incident.ID, incident.Owner, incident.OwnerGeneration, incident.OpenedAt, incident.InputRevision, incident.Status, nullText(string(incident.PendingOwner)),
	); err != nil {
		return err
	}
	for _, name := range []string{metaDecisions, metaMilestone} {
		if _, err := tx.Exec("INSERT INTO metadata(name, value) VALUES (?, '[]')", name); err != nil {
			return err
		}
	}
	keys := make([]string, 0, len(incident.Facts))
	for key := range incident.Facts {
		keys = append(keys, key)
	}
	slices.Sort(keys)
	for _, key := range keys {
		normal, err := normalize(incident.Facts[key])
		if err != nil {
			return err
		}
		if err := setFact(tx, "", key, normal); err != nil {
			return err
		}
	}
	if err := tx.Commit(); err != nil {
		return err
	}
	_, err = db.Exec("PRAGMA wal_checkpoint(TRUNCATE)")
	return err
}

func nullText(text string) sql.NullString {
	return sql.NullString{String: text, Valid: text != ""}
}

func (s Store) Header(id string) (Header, error) {
	var header Header
	err := s.read(id, func(tx *sql.Tx) (err error) {
		header, err = readHeader(tx)
		return err
	})
	return header, err
}

func (s Store) PassHeader(id string) (Header, error) {
	return heldRead(s, id, "header", func() (Header, error) { return s.Header(id) })
}

func readHeader(tx *sql.Tx) (Header, error) {
	var header Header
	var pending sql.NullString
	err := tx.QueryRow("SELECT "+headerColumns+" FROM incident WHERE singleton = 1").Scan(
		&header.ID, &header.Owner, &header.OwnerGeneration, &header.OpenedAt, &header.InputRevision, &header.Status, &pending,
	)
	header.PendingOwner = nullable(pending.String)
	return header, err
}

func (s Store) Facts(id string, keys ...string) (Facts, error) {
	var facts Facts
	err := s.read(id, func(tx *sql.Tx) (err error) {
		facts, err = readFacts(tx, keys)
		return err
	})
	return facts, err
}

func (s Store) Milestones(id string) ([]Milestone, error) {
	var milestones []Milestone
	err := s.read(id, func(tx *sql.Tx) error { return readMetadata(tx, metaMilestone, &milestones) })
	return milestones, err
}

func (s Store) Decisions(id string) ([]Decision, error) {
	var decisions []Decision
	err := s.read(id, func(tx *sql.Tx) error { return readMetadata(tx, metaDecisions, &decisions) })
	return decisions, err
}

func readMetadata(tx *sql.Tx, name string, into any) error {
	var value string
	if err := tx.QueryRow("SELECT value FROM metadata WHERE name = ?", name).Scan(&value); err != nil {
		return err
	}
	return json.Unmarshal([]byte(value), into)
}

func (s Store) Action(id, key string) (Action, error) {
	var action Action
	err := s.read(id, func(tx *sql.Tx) (err error) {
		action, err = readAction(tx, key)
		return err
	})
	return action, err
}

func readAction(tx *sql.Tx, key string) (Action, error) {
	var record string
	err := tx.QueryRow("SELECT record FROM action WHERE action_key = ?", key).Scan(&record)
	if errors.Is(err, sql.ErrNoRows) {
		return Action{}, fmt.Errorf("%w: %s", ErrUnknownAction, key)
	}
	if err != nil {
		return Action{}, err
	}
	var action Action
	return action, json.Unmarshal([]byte(record), &action)
}

func (s Store) ActionFamily(id, base string) ([]Action, error) {
	return s.queryActions(id, "SELECT record FROM action WHERE action_key = ? OR (action_key >= ? AND action_key < ?) ORDER BY ordinal", base, base+"#", base+"$")
}

func (s Store) Actions(id, kind string, statuses ...ActionStatus) ([]Action, error) {
	var terms []string
	var values []any
	if kind != "" {
		terms = append(terms, "kind = ?")
		values = append(values, kind)
	}
	if len(statuses) > 0 {
		terms = append(terms, "status IN (?"+strings.Repeat(", ?", len(statuses)-1)+")")
		for _, status := range statuses {
			values = append(values, status)
		}
	}
	where := ""
	if len(terms) > 0 {
		where = " WHERE " + strings.Join(terms, " AND ")
	}
	return s.queryActions(id, "SELECT record FROM action"+where+" ORDER BY ordinal", values...)
}

func (s Store) queryActions(id, query string, values ...any) ([]Action, error) {
	found, err := heldRead(s, id, fmt.Sprint(query, values), func() ([]Action, error) {
		var found []Action
		err := s.read(id, func(tx *sql.Tx) (err error) {
			found, err = selectActions(tx, query, values...)
			return err
		})
		return found, err
	})
	return slices.Clone(found), err
}

func selectActions(tx *sql.Tx, query string, values ...any) ([]Action, error) {
	rows, err := tx.Query(query, values...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var found []Action
	for rows.Next() {
		var record string
		if err := rows.Scan(&record); err != nil {
			return nil, err
		}
		var action Action
		if err := json.Unmarshal([]byte(record), &action); err != nil {
			return nil, err
		}
		found = append(found, action)
	}
	return found, rows.Err()
}

func (s Store) Load(id string) (Incident, error) {
	var incident Incident
	err := s.read(id, func(tx *sql.Tx) (err error) {
		if incident.Header, err = readHeader(tx); err != nil {
			return err
		}
		if err := readMetadata(tx, metaDecisions, &incident.Decisions); err != nil {
			return err
		}
		if err := readMetadata(tx, metaMilestone, &incident.Milestones); err != nil {
			return err
		}
		if incident.Facts, err = readAllFacts(tx); err != nil {
			return err
		}
		actions, err := selectActions(tx, "SELECT record FROM action ORDER BY ordinal")
		if err != nil {
			return err
		}
		incident.Actions = make(map[string]Action, len(actions))
		for _, action := range actions {
			incident.Actions[action.ActionID] = action
		}
		return nil
	})
	return incident, err
}

func (s Store) Owned(id string, generation int64, fn func(*Tx) error) error {
	return s.transact(id, func(tx *Tx) error {
		if tx.OwnerGeneration != generation {
			return fmt.Errorf("%w: %s is at owner generation %d (%s), not %d", ErrStaleGeneration, id, tx.OwnerGeneration, tx.Owner, generation)
		}
		return fn(tx)
	})
}

func (s Store) Input(id string, fn func(*Tx) error) error {
	return s.transact(id, func(tx *Tx) error {
		if err := fn(tx); err != nil {
			return err
		}
		tx.InputRevision++
		return nil
	})
}

func (s Store) Edit(id string, fn func(*Tx) error) error {
	return s.transact(id, fn)
}

func (s Store) Fact(id, key string) (any, bool, error) {
	var value any
	var found bool
	err := s.read(id, func(tx *sql.Tx) (err error) {
		value, found, err = readFact(tx, "", key)
		return err
	})
	return value, found, err
}

func (s Store) transact(id string, fn func(*Tx) error) error {
	return s.locked(id, func(path string) error {
		if err := s.exists(path, id); err != nil {
			return err
		}
		db, release, err := s.connect(path, writeSource)
		if err != nil {
			return err
		}
		defer release()
		sqlTx, err := db.Begin()
		if err != nil {
			return err
		}
		defer func() { _ = sqlTx.Rollback() }()
		if err := checkVersion(sqlTx, path); err != nil {
			return err
		}
		header, err := readHeader(sqlTx)
		if err != nil {
			return err
		}
		tx := &Tx{Header: header, sql: sqlTx, original: header}
		if err := fn(tx); err != nil {
			return err
		}
		if err := tx.flush(); err != nil {
			return err
		}
		if err := sqlTx.Commit(); err != nil {
			return err
		}
		if s.held != nil {
			s.held.forget(path)
		}
		return nil
	})
}

type Tx struct {
	Header
	sql        *sql.Tx
	original   Header
	decisions  []Decision
	milestones []Milestone
	loaded     map[string]bool
}

func (t *Tx) flush() error {
	if t.Header != t.original {
		if _, err := t.sql.Exec(
			"UPDATE incident SET incident_id = ?, owner = ?, owner_generation = ?, opened_at = ?, input_revision = ?, status = ?, pending_owner = ? WHERE singleton = 1",
			t.ID, t.Owner, t.OwnerGeneration, t.OpenedAt, t.InputRevision, t.Status, nullText(string(t.PendingOwner)),
		); err != nil {
			return err
		}
	}
	if t.loaded[metaDecisions] {
		if err := t.writeMetadata(metaDecisions, t.decisions); err != nil {
			return err
		}
	}
	if t.loaded[metaMilestone] {
		return t.writeMetadata(metaMilestone, t.milestones)
	}
	return nil
}

func (t *Tx) writeMetadata(name string, value any) error {
	record, err := encode(value)
	if err != nil {
		return err
	}
	_, err = t.sql.Exec("UPDATE metadata SET value = ? WHERE name = ?", record, name)
	return err
}

func (t *Tx) loadDecisions() error {
	if t.loaded[metaDecisions] {
		return nil
	}
	return t.load(metaDecisions, &t.decisions)
}

func (t *Tx) loadMilestones() error {
	if t.loaded[metaMilestone] {
		return nil
	}
	return t.load(metaMilestone, &t.milestones)
}

func (t *Tx) load(name string, into any) error {
	if err := readMetadata(t.sql, name, into); err != nil {
		return err
	}
	if t.loaded == nil {
		t.loaded = map[string]bool{}
	}
	t.loaded[name] = true
	return nil
}

func (t *Tx) Decide(key, text string, at time.Time) (bool, error) {
	if err := t.loadDecisions(); err != nil {
		return false, err
	}
	if slices.ContainsFunc(t.decisions, func(decision Decision) bool { return decision.Key == key }) {
		return false, nil
	}
	t.decisions = append(t.decisions, Decision{Key: key, Text: text, At: store.Stamp(at)})
	return true, nil
}

func (t *Tx) Resolve(key string, at time.Time) error {
	if err := t.loadDecisions(); err != nil {
		return err
	}
	for i := range t.decisions {
		if t.decisions[i].Key == key && t.decisions[i].ResolvedAt == "" {
			t.decisions[i].ResolvedAt = nullable(store.Stamp(at))
		}
	}
	return nil
}

func (t *Tx) Milestone(name, text string, at time.Time) (bool, error) {
	if err := t.loadMilestones(); err != nil {
		return false, err
	}
	if _, found := reached(t.milestones, name); found {
		return false, nil
	}
	t.milestones = append(t.milestones, Milestone{Name: name, Text: text, At: store.Stamp(at)})
	return true, nil
}

func (t *Tx) Accept(id, kind, target, authority string, at time.Time) (Action, error) {
	action, err := readAction(t.sql, id)
	if err == nil || !errors.Is(err, ErrUnknownAction) {
		return action, err
	}
	action = Action{
		IncidentID:      t.ID,
		ActionID:        id,
		Kind:            kind,
		Target:          target,
		AuthorityRef:    nullable(authority),
		InputRevision:   t.InputRevision,
		OwnerGeneration: t.OwnerGeneration,
		AcceptedAt:      store.Stamp(at),
		Status:          ActionAccepted,
	}
	record, err := encode(action)
	if err != nil {
		return Action{}, err
	}
	_, err = t.sql.Exec("INSERT INTO action(action_key, kind, status, record) VALUES (?, ?, ?, ?)", id, kind, action.Status, record)
	return action, err
}

func (t *Tx) change(id string, edit func(*Action) error) (Action, error) {
	action, err := readAction(t.sql, id)
	if err != nil {
		return Action{}, err
	}
	if err := edit(&action); err != nil {
		return Action{}, err
	}
	record, err := encode(action)
	if err != nil {
		return Action{}, err
	}
	_, err = t.sql.Exec("UPDATE action SET kind = ?, status = ?, record = ? WHERE action_key = ?", action.Kind, action.Status, record, id)
	return action, err
}

func (t *Tx) AcceptDue(id, kind, target, authority string, at, deadline time.Time) (Action, bool, error) {
	held, err := readAction(t.sql, id)
	if err == nil {
		return held, false, nil
	}
	if !errors.Is(err, ErrUnknownAction) {
		return Action{}, false, err
	}
	if _, err := t.Accept(id, kind, target, authority, at); err != nil {
		return Action{}, false, err
	}
	action, err := t.change(id, func(action *Action) error {
		if !deadline.IsZero() {
			action.Deadline = nullable(store.Stamp(deadline))
		}
		return nil
	})
	return action, true, err
}

func (t *Tx) StartBy(id string, at, deadline time.Time, dispatch string) (Action, error) {
	return t.change(id, func(action *Action) error {
		if action.Status != ActionAccepted {
			return &TransitionError{fmt.Sprintf("%s is %s, not accepted", id, action.Status)}
		}
		action.Status, action.StartedAt, action.Deadline = ActionStarted, nullable(store.Stamp(at)), ""
		if !deadline.IsZero() {
			action.Deadline = nullable(store.Stamp(deadline))
		}
		if dispatch != "" {
			action.DispatchID = nullable(dispatch)
		}
		return nil
	})
}

func (t *Tx) Respond(id string, response map[string]any) (Action, error) {
	return t.change(id, func(action *Action) error {
		action.Response = response
		return nil
	})
}

func (t *Tx) Start(id string, at time.Time, deadline time.Duration) (Action, error) {
	return t.change(id, func(action *Action) error {
		if action.Status != ActionAccepted {
			return &TransitionError{fmt.Sprintf("%s is %s, not accepted", id, action.Status)}
		}
		action.Status, action.StartedAt, action.Deadline = ActionStarted, nullable(store.Stamp(at)), ""
		if deadline > 0 {
			action.Deadline = nullable(store.Stamp(at.Add(deadline)))
		}
		return nil
	})
}

func (t *Tx) Complete(id string, response map[string]any, dispatch string) (Action, error) {
	return t.change(id, func(action *Action) error {
		if action.Status != ActionStarted && action.Status != ActionUnverifiable {
			return &TransitionError{fmt.Sprintf("%s is %s, not started", id, action.Status)}
		}
		action.Status, action.Response = ActionCompleted, response
		if dispatch != "" {
			action.DispatchID = nullable(dispatch)
		}
		return nil
	})
}

func (t *Tx) Verify(id string, receipt map[string]any) (Action, error) {
	return t.change(id, func(action *Action) error {
		if action.terminal() {
			return &TransitionError{fmt.Sprintf("%s is already %s", id, action.Status)}
		}
		action.Status, action.VerificationReceipt = ActionVerified, receipt
		return nil
	})
}

func (t *Tx) Fail(id, reason string) (Action, error) {
	return t.change(id, func(action *Action) error {
		if action.terminal() {
			return &TransitionError{fmt.Sprintf("%s is already %s", id, action.Status)}
		}
		action.Status, action.Reason = ActionFailed, nullable(reason)
		return nil
	})
}

func (t *Tx) Lose(id string) (Action, error) {
	return t.change(id, func(action *Action) error {
		if action.Status != ActionStarted {
			return &TransitionError{fmt.Sprintf("%s is %s, not started", id, action.Status)}
		}
		action.Status, action.Reason = ActionUnverifiable, "response lost; settle from external state"
		return nil
	})
}

func (t *Tx) LoseStarted() error {
	rows, err := t.sql.Query("SELECT action_key FROM action WHERE status = ? ORDER BY ordinal", ActionStarted)
	if err != nil {
		return err
	}
	var keys []string
	for rows.Next() {
		var key string
		if err := rows.Scan(&key); err != nil {
			rows.Close()
			return err
		}
		keys = append(keys, key)
	}
	if err := rows.Err(); err != nil {
		rows.Close()
		return err
	}
	if err := rows.Close(); err != nil {
		return err
	}
	for _, key := range keys {
		if _, err := t.Lose(key); err != nil {
			return err
		}
	}
	return nil
}

func (t *Tx) ActionFamilySize(base string) (int, error) {
	var count int
	err := t.sql.QueryRow("SELECT COUNT(*) FROM action WHERE action_key = ? OR (action_key >= ? AND action_key < ?)", base, base+"#", base+"$").Scan(&count)
	return count, err
}

func (t *Tx) Action(id string) (Action, error) {
	return readAction(t.sql, id)
}

func (t *Tx) Facts(keys ...string) (Facts, error) {
	return readFacts(t.sql, keys)
}

func (t *Tx) SetFact(key string, value any) error {
	normal, err := normalize(value)
	if err != nil {
		return err
	}
	return setFact(t.sql, "", key, normal)
}

func (t *Tx) Fact(key string) (any, bool, error) {
	return readFact(t.sql, "", key)
}

func (t *Tx) DropFact(key string) error {
	return dropFact(t.sql, "", key)
}

func (t *Tx) Offer(owner string) {
	t.PendingOwner = nullable(owner)
}

func (t *Tx) TakeOver(owner string) error {
	if string(t.PendingOwner) != owner {
		return fmt.Errorf("%s has no pending transfer to %s (pending: %s)", t.ID, owner, t.PendingOwner)
	}
	t.Owner, t.PendingOwner = owner, ""
	t.OwnerGeneration++
	return nil
}

func (t *Tx) SetChild(parent, key string, value any) error {
	normal, err := normalize(value)
	if err != nil {
		return err
	}
	return setFact(t.sql, factPath("", parent), key, normal)
}
