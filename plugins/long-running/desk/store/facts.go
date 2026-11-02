package store

import (
	"context"
	"fmt"
	"math/big"
	"slices"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/desk/pyjson"
)

func factPath(parent, key string) string {
	return parent + "/" + strings.ReplaceAll(strings.ReplaceAll(key, "~", "~0"), "/", "~1")
}

func parentOf(path string) string {
	return path[:strings.LastIndexByte(path, '/')]
}

type loadedFact struct {
	value    any
	record   string
	sequence int
}

type loadedRef struct {
	path     string
	sequence int
}

type factTree struct {
	ctx            context.Context
	conn           *conn
	loaded         map[string]*loadedFact
	order          []loadedRef
	sequence       int
	cachedChildren map[string]map[string]bool
	versions       map[string]int
	closed         bool
}

func newFactTree(ctx context.Context, c *conn) *factTree {
	return &factTree{ctx: ctx, conn: c, loaded: map[string]*loadedFact{}, cachedChildren: map[string]map[string]bool{}, versions: map[string]int{}}
}

func (t *factTree) active() (*conn, error) {
	if t.closed {
		return nil, &RuntimeError{Message: "fact state requires an active transaction; retain a detached value instead"}
	}
	return t.conn, nil
}

func (t *factTree) remember(path string, value any, record string) {
	if held, ok := t.loaded[path]; ok {
		held.value, held.record = value, record
	} else {
		t.sequence++
		t.loaded[path] = &loadedFact{value: value, record: record, sequence: t.sequence}
		t.order = append(t.order, loadedRef{path: path, sequence: t.sequence})
	}
	for path != "" {
		parent := parentOf(path)
		if t.cachedChildren[parent] == nil {
			t.cachedChildren[parent] = map[string]bool{}
		}
		t.cachedChildren[parent][path] = true
		path = parent
	}
}

func (t *factTree) forget(path string) {
	if children, ok := t.cachedChildren[parentOf(path)]; ok {
		delete(children, path)
	}
	pending := []string{path}
	for len(pending) > 0 {
		selected := pending[len(pending)-1]
		pending = pending[:len(pending)-1]
		for child := range t.cachedChildren[selected] {
			pending = append(pending, child)
		}
		delete(t.cachedChildren, selected)
		delete(t.loaded, selected)
	}
}

func (t *factTree) version(path string) []int {
	var versions []int
	for path != "" {
		versions = append(versions, t.versions[path])
		path = parentOf(path)
	}
	return versions
}

func (t *factTree) invalidate(path string) {
	t.versions[path]++
	t.forget(path)
}

func (t *factTree) remove(path string, descendantsOnly bool) error {
	c, err := t.active()
	if err != nil {
		return err
	}
	query := "SELECT path FROM fact WHERE path = ?"
	if descendantsOnly {
		query = "SELECT path FROM fact WHERE parent = ?"
	}
	if err := c.exec(t.ctx, "WITH RECURSIVE subtree(path) AS ("+query+" UNION ALL SELECT fact.path FROM fact JOIN subtree ON fact.parent = subtree.path) DELETE FROM fact WHERE path IN (SELECT path FROM subtree)", path); err != nil {
		return err
	}
	t.invalidate(path)
	return nil
}

func (t *factTree) replace(parent string, value *pyjson.Object) error {
	copied, err := detached(value)
	if err != nil {
		return err
	}
	value = copied.(*pyjson.Object)
	current, err := newFacts(t, parent)
	if err != nil {
		return err
	}
	held, err := current.Keys()
	if err != nil {
		return err
	}
	for _, key := range held {
		if !value.Has(key) {
			if err := t.remove(factPath(parent, key), false); err != nil {
				return err
			}
		}
	}
	keys := value.Keys()
	for _, key := range keys {
		item, _ := value.Get(key)
		if err := t.set(parent, key, item); err != nil {
			return err
		}
	}
	after, err := newFacts(t, parent)
	if err != nil {
		return err
	}
	ordered, err := after.Keys()
	if err != nil || slices.Equal(ordered, keys) {
		return err
	}
	c, err := t.active()
	if err != nil {
		return err
	}
	ordinal, err := c.integer(t.ctx, "SELECT MAX(ordinal) FROM fact")
	if err != nil {
		return err
	}
	for index, key := range keys {
		if err := c.exec(t.ctx, "UPDATE fact SET ordinal = ? WHERE path = ?", ordinal+int64(index)+1, factPath(parent, key)); err != nil {
			return err
		}
	}
	return nil
}

func (t *factTree) set(parent, key string, value any) error {
	path := factPath(parent, key)
	if mapping(value) {
		copied, err := detached(value)
		if err != nil {
			return err
		}
		value = copied
	}
	t.invalidate(path)
	c, err := t.active()
	if err != nil {
		return err
	}
	row, err := c.row(t.ctx, "SELECT kind, record FROM fact WHERE path = ?", path)
	if err != nil {
		return err
	}
	object, isDict := value.(*pyjson.Object)
	kind, record, encoded := "dict", any(nil), ""
	if !isDict {
		if encoded, err = pyjson.DumpsCompact(value); err != nil {
			return err
		}
		kind, record = "value", encoded
	}
	if row == nil {
		if err := c.exec(t.ctx, "INSERT INTO fact(path, parent, key, kind, record) VALUES (?, ?, ?, ?, ?)", path, parent, key, kind, record); err != nil {
			return err
		}
	} else if row[0] != kind || row[1] != record {
		if row[0] == "dict" {
			if err := t.remove(path, true); err != nil {
				return err
			}
		}
		if err := c.exec(t.ctx, "UPDATE fact SET kind = ?, record = ? WHERE path = ?", kind, record, path); err != nil {
			return err
		}
	}
	if isDict {
		return t.replace(path, object)
	}
	t.remember(path, value, encoded)
	return nil
}

func (t *factTree) flush() error {
	for _, ref := range t.order {
		held, ok := t.loaded[ref.path]
		if !ok || held.sequence != ref.sequence {
			continue
		}
		record, err := pyjson.DumpsCompact(held.value)
		if err != nil {
			return err
		}
		if record == held.record {
			continue
		}
		c, err := t.active()
		if err != nil {
			return err
		}
		if err := c.exec(t.ctx, "UPDATE fact SET record = ? WHERE path = ?", record, ref.path); err != nil {
			return err
		}
	}
	return nil
}

// Facts is a lazy view of one fact mapping inside a transaction, as _Facts is: a fact holding a mapping reads as a
// nested *Facts, any other fact as its decoded JSON, which commit writes back when it changed in place.
type Facts struct {
	tree     *factTree
	parent   string
	ordinal  int64
	versions []int
}

func newFacts(tree *factTree, parent string) (*Facts, error) {
	facts := &Facts{tree: tree, parent: parent}
	if parent == "" {
		return facts, nil
	}
	c, err := tree.active()
	if err != nil {
		return nil, err
	}
	row, err := c.row(tree.ctx, "SELECT ordinal FROM fact WHERE path = ?", parent)
	if err != nil {
		return nil, err
	}
	if row == nil {
		return nil, fmt.Errorf("no fact mapping at %s", pyRepr(parent))
	}
	if facts.ordinal, err = asInteger(row[0]); err != nil {
		return nil, err
	}
	facts.versions = tree.version(parent)
	return facts, nil
}

func (f *Facts) connection() (*conn, error) {
	c, err := f.tree.active()
	if err != nil || f.parent == "" {
		return c, err
	}
	row, err := c.row(f.tree.ctx, "SELECT ordinal, kind FROM fact WHERE path = ?", f.parent)
	if err != nil {
		return nil, err
	}
	if row == nil || row[0] != f.ordinal || !slices.Equal(f.tree.version(f.parent), f.versions) || row[1] != "dict" {
		return nil, &RuntimeError{Message: "fact mapping was replaced or removed during the transaction"}
	}
	return c, nil
}

// Get is facts[key]: a *Facts for a mapping, the decoded value otherwise, and a KeyError when there is no such key.
func (f *Facts) Get(key string) (any, error) {
	if _, err := f.connection(); err != nil {
		return nil, err
	}
	path := factPath(f.parent, key)
	if held, ok := f.tree.loaded[path]; ok {
		return held.value, nil
	}
	c, err := f.connection()
	if err != nil {
		return nil, err
	}
	row, err := c.row(f.tree.ctx, "SELECT kind, record FROM fact WHERE path = ?", path)
	if err != nil {
		return nil, err
	}
	if row == nil {
		return nil, &KeyError{Key: key}
	}
	if row[0] == "dict" {
		return newFacts(f.tree, path)
	}
	record, err := asText(row[1])
	if err != nil {
		return nil, err
	}
	value, err := pyjson.Loads(record)
	if err != nil {
		return nil, err
	}
	f.tree.remember(path, value, record)
	return f.tree.loaded[path].value, nil
}

// Contains is key in facts.
func (f *Facts) Contains(key string) (bool, error) {
	c, err := f.connection()
	if err != nil {
		return false, err
	}
	row, err := c.row(f.tree.ctx, "SELECT 1 FROM fact WHERE path = ?", factPath(f.parent, key))
	return row != nil, err
}

// Keys lists the mapping's keys in insertion order.
func (f *Facts) Keys() ([]string, error) {
	c, err := f.connection()
	if err != nil {
		return nil, err
	}
	rows, err := c.rows(f.tree.ctx, "SELECT key FROM fact WHERE parent = ? ORDER BY ordinal", f.parent)
	if err != nil {
		return nil, err
	}
	keys := make([]string, len(rows))
	for index, row := range rows {
		if keys[index], err = asText(row[0]); err != nil {
			return nil, err
		}
	}
	return keys, nil
}

// Len is len(facts).
func (f *Facts) Len() (int, error) {
	c, err := f.connection()
	if err != nil {
		return 0, err
	}
	var count int64
	if f.parent != "" {
		count, err = c.integer(f.tree.ctx, "SELECT child_count FROM fact WHERE path = ?", f.parent)
	} else {
		count, err = c.integer(f.tree.ctx, "SELECT fact_count FROM incident WHERE singleton = 1")
	}
	return int(count), err
}

// Set is facts[key] = value: a *pyjson.Object or *Facts stores as a mapping of facts, anything else as one JSON value.
func (f *Facts) Set(key string, value any) error {
	if _, err := f.connection(); err != nil {
		return err
	}
	return f.tree.set(f.parent, key, value)
}

// Delete is del facts[key].
func (f *Facts) Delete(key string) error {
	held, err := f.Contains(key)
	if err != nil {
		return err
	}
	if !held {
		return &KeyError{Key: key}
	}
	return f.tree.remove(factPath(f.parent, key), false)
}

// Pop is facts.pop(key, *def): the detached value it removes, def[0] when there is no such key, a KeyError without
// def.
func (f *Facts) Pop(key string, def ...any) (any, error) {
	held, err := f.Contains(key)
	if err != nil {
		return nil, err
	}
	if !held {
		if len(def) > 0 {
			return def[0], nil
		}
		return nil, &KeyError{Key: key}
	}
	item, err := f.Get(key)
	if err != nil {
		return nil, err
	}
	value, err := detached(item)
	if err != nil {
		return nil, err
	}
	return value, f.Delete(key)
}

// SetDefault is facts.setdefault(key, def): it stores def when key is missing, then returns facts[key].
func (f *Facts) SetDefault(key string, def any) (any, error) {
	held, err := f.Contains(key)
	if err != nil {
		return nil, err
	}
	if !held {
		if err := f.Set(key, def); err != nil {
			return nil, err
		}
	}
	return f.Get(key)
}

// Detach is detached(facts): the mapping copied out of the transaction as a *pyjson.Object.
func (f *Facts) Detach() (*pyjson.Object, error) {
	value, err := detached(f)
	if err != nil {
		return nil, err
	}
	return value.(*pyjson.Object), nil
}

func mapping(value any) bool {
	switch value.(type) {
	case *Facts, *pyjson.Object:
		return true
	}
	return false
}

func detached(value any) (any, error) {
	switch value := value.(type) {
	case *Facts:
		keys, err := value.Keys()
		if err != nil {
			return nil, err
		}
		object := pyjson.NewObject()
		for _, key := range keys {
			item, err := value.Get(key)
			if err != nil {
				return nil, err
			}
			copied, err := detached(item)
			if err != nil {
				return nil, err
			}
			object.Set(key, copied)
		}
		return object, nil
	case *pyjson.Object:
		object := pyjson.NewObject()
		for _, key := range value.Keys() {
			item, _ := value.Get(key)
			copied, err := detached(item)
			if err != nil {
				return nil, err
			}
			object.Set(key, copied)
		}
		return object, nil
	case []any:
		copied := make([]any, len(value))
		for index, item := range value {
			var err error
			if copied[index], err = detached(item); err != nil {
				return nil, err
			}
		}
		return copied, nil
	case *Action:
		return value.clone()
	case *big.Int:
		return new(big.Int).Set(value), nil
	}
	return value, nil
}
