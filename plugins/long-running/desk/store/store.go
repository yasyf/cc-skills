// Package store is the Go port of long_running's action store and Book: one SQLite database per incident, with
// the same file format, schema, locks, transactions and bytes as sqlite_store.Store, so a Python and a Go desk read
// and write one store concurrently.
package store

import (
	"context"
	"errors"
	"fmt"
	"os"
	"slices"
	"strings"
	"sync"

	"github.com/yasyf/cc-skills/plugins/long-running/desk/pyjson"
)

const readerLimit = 64

// Store is sqlite_store.Store: per-incident SQLite state under Root with detached reads and generation-checked edits.
type Store struct {
	root     string
	evicting sync.Mutex
	eviction *pyRandom
}

// New returns the store rooted at root, the path normalized as pathlib normalizes it.
func New(root string) *Store {
	return &Store{root: pyPath(root), eviction: newPyRandom()}
}

// DefaultRoot is the root Store() opens with none given: ~/.claude/long-running/incidents.
func DefaultRoot() (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return joinPath(joinPath(joinPath(pyPath(home), ".claude"), "long-running"), "incidents"), nil
}

// Root is the directory holding one database per incident.
func (s *Store) Root() string {
	return s.root
}

// Path is the incident's database file; an empty incident ID, or one holding "/" or NUL, is a ValueError.
func (s *Store) Path(incidentID string) (string, error) {
	if incidentID == "" || strings.ContainsAny(incidentID, "/\x00") {
		return "", &ValueError{Message: "incident ID must be a nonempty filename component"}
	}
	return joinPath(s.root, incidentID+".sqlite3"), nil
}

type readersKey struct{ store *Store }

type reader struct {
	identity fileIdentity
	conn     *conn
}

type readers struct {
	mu          sync.Mutex
	open        bool
	order       []string
	connections map[string]*reader
}

func (r *readers) take(path string) *reader {
	held, ok := r.connections[path]
	if !ok {
		return nil
	}
	delete(r.connections, path)
	r.order = slices.DeleteFunc(r.order, func(candidate string) bool { return candidate == path })
	return held
}

func (r *readers) put(path string, held *reader) {
	r.connections[path] = held
	r.order = append(r.order, path)
}

func (s *Store) scope(ctx context.Context) *readers {
	scope, _ := ctx.Value(readersKey{s}).(*readers)
	return scope
}

// Reading is reading(): read runs with a context under which this store's reads reuse at most 64 read-only
// connections, each read in a fresh transaction. A nested Reading reuses the open scope; the outermost closes them.
func (s *Store) Reading(ctx context.Context, read func(context.Context) error) (err error) {
	if scope := s.scope(ctx); scope != nil {
		scope.mu.Lock()
		open := scope.open
		scope.mu.Unlock()
		if open {
			return read(ctx)
		}
	}
	scope := &readers{open: true, connections: map[string]*reader{}}
	defer func() {
		scope.mu.Lock()
		defer scope.mu.Unlock()
		scope.open = false
		for _, path := range scope.order {
			err = errors.Join(err, scope.connections[path].conn.close())
		}
	}()
	return read(context.WithValue(ctx, readersKey{s}, scope))
}

func (s *Store) evict() int {
	s.evicting.Lock()
	defer s.evicting.Unlock()
	return s.eviction.below(readerLimit)
}

func (s *Store) reader(ctx context.Context, scope *readers, path string) (*conn, error) {
	if scope == nil {
		return dial(ctx, path, "ro")
	}
	identity, err := identify(path)
	if err != nil {
		return nil, err
	}
	if held := scope.take(path); held != nil {
		if held.identity == identity {
			scope.put(path, held)
			return held.conn, nil
		}
		if err := held.conn.close(); err != nil {
			return nil, err
		}
	}
	if len(scope.order) == readerLimit {
		if err := scope.take(scope.order[s.evict()]).conn.close(); err != nil {
			return nil, err
		}
	}
	c, err := dial(ctx, path, "ro")
	if err != nil {
		return nil, err
	}
	scope.put(path, &reader{identity: identity, conn: c})
	return c, nil
}

func snapshot(ctx context.Context, c *conn, path string, body func(*conn) error) (err error) {
	if err := c.exec(ctx, "BEGIN"); err != nil {
		return err
	}
	defer func() {
		if rollback := c.exec(context.WithoutCancel(ctx), "ROLLBACK"); rollback != nil {
			err = rollback
		}
	}()
	version, err := c.integer(ctx, "PRAGMA user_version")
	if err != nil {
		return err
	}
	if version != 2 {
		return &ValueError{Message: "unsupported action store version: " + path}
	}
	return body(c)
}

func (s *Store) read(ctx context.Context, incidentID string, body func(*conn) error) error {
	path, err := s.Path(incidentID)
	if err != nil {
		return err
	}
	var scope *readers
	if found := s.scope(ctx); found != nil {
		found.mu.Lock()
		defer found.mu.Unlock()
		if found.open {
			scope = found
		}
	}
	if !isFile(path) {
		if scope != nil {
			if held := scope.take(path); held != nil {
				if err := held.conn.close(); err != nil {
					return err
				}
			}
		}
		return &NoIncident{IncidentID: incidentID, Root: s.root}
	}
	c, err := s.reader(ctx, scope, path)
	if err != nil {
		return err
	}
	if err := snapshot(ctx, c, path, body); err != nil {
		if scope != nil {
			scope.take(path)
		}
		return errors.Join(err, c.close())
	}
	if scope == nil {
		return c.close()
	}
	return nil
}

func (s *Store) locked(incidentID string, body func(path string) error) (err error) {
	path, err := s.Path(incidentID)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(s.root, 0o777); err != nil {
		return err
	}
	lock, err := os.OpenFile(joinPath(s.root, "."+incidentID+".lock"), os.O_WRONLY|os.O_CREATE|os.O_APPEND, 0o666)
	if err != nil {
		return err
	}
	defer func() { err = errors.Join(err, lock.Close()) }()
	if err := flock(lock); err != nil {
		return err
	}
	return body(path)
}

// TransactionOption is one of transaction()'s keyword arguments.
type TransactionOption func(*transactionSettings)

type transactionSettings struct {
	generation *int64
	inputs     bool
}

// AtGeneration refuses the transaction with StaleGeneration unless the incident is at owner generation.
func AtGeneration(generation int64) TransactionOption {
	return func(settings *transactionSettings) { settings.generation = &generation }
}

// BumpInputs advances the incident's input revision when the transaction commits.
func BumpInputs() TransactionOption {
	return func(settings *transactionSettings) { settings.inputs = true }
}

// Transaction is transaction(): under the incident's lock file, change runs inside one BEGIN IMMEDIATE transaction
// on its database, and what it changed commits unless it returns an error.
func (s *Store) Transaction(ctx context.Context, incidentID string, change func(*Transaction) error, options ...TransactionOption) error {
	var settings transactionSettings
	for _, option := range options {
		option(&settings)
	}
	return s.locked(incidentID, func(path string) (err error) {
		if !isFile(path) {
			return &NoIncident{IncidentID: incidentID, Root: s.root}
		}
		c, err := dialWriter(ctx, path)
		if err != nil {
			return err
		}
		defer func() { err = errors.Join(err, c.close()) }()
		return transact(ctx, c, incidentID, settings, change)
	})
}

func transact(ctx context.Context, c *conn, incidentID string, settings transactionSettings, change func(*Transaction) error) error {
	if err := c.exec(ctx, "BEGIN IMMEDIATE"); err != nil {
		return err
	}
	tx, err := newTransaction(ctx, c)
	if err != nil {
		return err
	}
	defer tx.close()
	if settings.generation != nil && tx.OwnerGeneration != *settings.generation {
		return &StaleGeneration{Message: fmt.Sprintf("%s is at owner generation %d (%s), not %d", incidentID, tx.OwnerGeneration, tx.Owner, *settings.generation)}
	}
	if err := change(tx); err != nil {
		return err
	}
	if settings.inputs {
		tx.InputRevision++
	}
	if err := tx.flush(); err != nil {
		return err
	}
	return c.exec(ctx, "COMMIT")
}

// Ensure is ensure(): it creates the incident's database unless one exists.
func (s *Store) Ensure(ctx context.Context, incident *Incident) error {
	return s.locked(incident.IncidentID, func(path string) error {
		if exists(path) {
			return nil
		}
		return createDatabase(ctx, s.root, path, incident)
	})
}

func createDatabase(ctx context.Context, root, path string, incident *Incident) (err error) {
	staged, err := os.CreateTemp(root, "."+path[strings.LastIndexByte(path, '/')+1:]+".*")
	if err != nil {
		return err
	}
	name := staged.Name()
	defer func() { err = errors.Join(err, removeStaged(name)) }()
	if err := staged.Close(); err != nil {
		return err
	}
	c, err := dial(ctx, name, "rwc")
	if err != nil {
		return err
	}
	defer func() { err = errors.Join(err, c.close()) }()
	for _, statement := range []string{"PRAGMA journal_mode = WAL", "PRAGMA synchronous = FULL", schema, "BEGIN IMMEDIATE"} {
		if err := c.exec(ctx, statement); err != nil {
			return err
		}
	}
	if err := c.exec(ctx, "INSERT INTO incident(singleton, "+headerColumns+") VALUES (1, ?, ?, ?, ?, ?, ?, ?)",
		incident.IncidentID, incident.Owner, incident.OwnerGeneration, incident.OpenedAt, incident.InputRevision, incident.Status, nullable(incident.PendingOwner)); err != nil {
		return err
	}
	for _, entry := range []struct {
		name  string
		value []any
	}{{"decisions", incident.Decisions}, {"milestones", incident.Milestones}} {
		encoded, err := pyjson.DumpsCompact(entry.value)
		if err != nil {
			return err
		}
		if err := c.exec(ctx, "INSERT INTO metadata(name, value) VALUES (?, ?)", entry.name, encoded); err != nil {
			return err
		}
	}
	facts := &Facts{tree: newFactTree(ctx, c)}
	for _, key := range incident.Facts.Keys() {
		value, _ := incident.Facts.Get(key)
		if err := facts.Set(key, value); err != nil {
			return err
		}
	}
	for _, statement := range []string{"COMMIT", "PRAGMA wal_checkpoint(TRUNCATE)"} {
		if err := c.exec(ctx, statement); err != nil {
			return err
		}
	}
	if err := c.close(); err != nil {
		return err
	}
	if err := fsync(name); err != nil {
		return err
	}
	if err := os.Rename(name, path); err != nil {
		return err
	}
	return fsync(root)
}

// Exists is exists(): whether the incident has a database file.
func (s *Store) Exists(incidentID string) (bool, error) {
	path, err := s.Path(incidentID)
	if err != nil {
		return false, err
	}
	return isFile(path), nil
}

// Fact is fact(): the detached value of one top-level fact, def[0] when there is none, a KeyError without def.
func (s *Store) Fact(ctx context.Context, incidentID, key string, def ...any) (value any, err error) {
	err = s.read(ctx, incidentID, func(c *conn) error {
		item, err := (&Facts{tree: newFactTree(ctx, c)}).Get(key)
		if err == nil {
			value, err = detached(item)
		}
		var missing *KeyError
		if errors.As(err, &missing) && len(def) > 0 {
			value, err = def[0], nil
		}
		return err
	})
	return value, err
}

// Facts is facts(): the detached top-level facts named by keys that the incident holds, or every fact when keys is
// nil.
func (s *Store) Facts(ctx context.Context, incidentID string, keys []string) (*pyjson.Object, error) {
	selected := pyjson.NewObject()
	err := s.read(ctx, incidentID, func(c *conn) error {
		facts := &Facts{tree: newFactTree(ctx, c)}
		names := keys
		if names == nil {
			var err error
			if names, err = facts.Keys(); err != nil {
				return err
			}
		}
		for _, key := range names {
			held, err := facts.Contains(key)
			if err != nil {
				return err
			}
			if !held {
				continue
			}
			item, err := facts.Get(key)
			if err != nil {
				return err
			}
			value, err := detached(item)
			if err != nil {
				return err
			}
			selected.Set(key, value)
		}
		return nil
	})
	return selected, err
}

// FindAction is find_action(): the stored action under actionID, nil when there is none.
func (s *Store) FindAction(ctx context.Context, incidentID, actionID string) (action *Action, err error) {
	err = s.read(ctx, incidentID, func(c *conn) error {
		row, err := c.row(ctx, "SELECT record FROM action WHERE action_key = ?", actionID)
		if err != nil || row == nil {
			return err
		}
		record, err := asText(row[0])
		if err != nil {
			return err
		}
		action, err = loadAction(record)
		return err
	})
	return action, err
}

// Action is action(): the stored action under key, UnknownAction when there is none.
func (s *Store) Action(ctx context.Context, incidentID, key string) (*Action, error) {
	action, err := s.FindAction(ctx, incidentID, key)
	if err == nil && action == nil {
		err = &UnknownAction{ActionID: key}
	}
	return action, err
}

// Filter narrows Actions the way actions()'s kind, status and statuses arguments do.
type Filter func(*selection)

type selection struct {
	kind       *string
	status     *string
	statuses   []string
	byStatuses bool
}

// Kind keeps actions of kind.
func Kind(kind string) Filter {
	return func(s *selection) { s.kind = &kind }
}

// Status keeps actions in status.
func Status(status string) Filter {
	return func(s *selection) { s.status = &status }
}

// Statuses keeps actions in any of statuses, none when it is empty.
func Statuses(statuses ...string) Filter {
	return func(s *selection) { s.statuses, s.byStatuses = statuses, true }
}

func (s *selection) where() (string, []any) {
	var terms []string
	var values []any
	for _, term := range []struct {
		name  string
		value *string
	}{{"kind", s.kind}, {"status", s.status}} {
		if term.value != nil {
			terms = append(terms, term.name+" = ?")
			values = append(values, *term.value)
		}
	}
	if s.byStatuses {
		if len(s.statuses) == 0 {
			terms = append(terms, "0")
		} else {
			terms = append(terms, "status IN ("+strings.Repeat("?, ", len(s.statuses)-1)+"?)")
		}
		for _, status := range s.statuses {
			values = append(values, status)
		}
	}
	if len(terms) == 0 {
		return "", values
	}
	return " WHERE " + strings.Join(terms, " AND "), values
}

// ReadVersion is a read's PRAGMA data_version on the connection that served it.
type ReadVersion struct {
	conn  *conn
	value int64
}

// Actions is actions(): the stored actions the filters keep, in acceptance order.
func (s *Store) Actions(ctx context.Context, incidentID string, filters ...Filter) ([]*Action, error) {
	actions, _, err := s.selectActions(ctx, incidentID, false, filters)
	return actions, err
}

// ActionsWithVersion is actions_with_version(): Actions and the ReadVersion Unchanged checks.
func (s *Store) ActionsWithVersion(ctx context.Context, incidentID string, filters ...Filter) ([]*Action, ReadVersion, error) {
	return s.selectActions(ctx, incidentID, true, filters)
}

func (s *Store) selectActions(ctx context.Context, incidentID string, versioned bool, filters []Filter) (selected []*Action, version ReadVersion, err error) {
	var chosen selection
	for _, filter := range filters {
		filter(&chosen)
	}
	where, values := chosen.where()
	index := ""
	if chosen.kind != nil && chosen.byStatuses {
		index = " INDEXED BY action_kind_status"
	}
	err = s.read(ctx, incidentID, func(c *conn) error {
		if versioned {
			value, err := c.integer(ctx, "PRAGMA data_version")
			if err != nil {
				return err
			}
			version = ReadVersion{conn: c, value: value}
		}
		rows, err := c.rows(ctx, "SELECT record FROM action"+index+where+" ORDER BY ordinal", values...)
		if err != nil {
			return err
		}
		for _, row := range rows {
			record, err := asText(row[0])
			if err != nil {
				return err
			}
			action, err := loadAction(record)
			if err != nil {
				return err
			}
			selected = append(selected, action)
		}
		return nil
	})
	return selected, version, err
}

// Unchanged is unchanged(): true only when the same reading-scope connection serves this read and its data_version
// has not moved since version.
func (s *Store) Unchanged(ctx context.Context, incidentID string, version ReadVersion) (same bool, err error) {
	err = s.read(ctx, incidentID, func(c *conn) error {
		if c != version.conn {
			return nil
		}
		value, err := c.integer(ctx, "PRAGMA data_version")
		same = value == version.value
		return err
	})
	return same, err
}
