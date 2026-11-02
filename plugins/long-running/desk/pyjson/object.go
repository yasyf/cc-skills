// Package pyjson reads and writes JSON byte for byte as Python's json module does, keeping object members in the
// insertion order a Python dict keeps.
package pyjson

import "slices"

// Object is a JSON object whose members keep their insertion order, as a Python dict's do: setting an existing key
// keeps its position, and a deleted key set again moves to the end.
type Object struct {
	keys   []string
	values map[string]any
}

// NewObject returns an empty Object.
func NewObject() *Object {
	return &Object{values: map[string]any{}}
}

// ObjectOf returns an Object of the alternating keys and values in pairs, in order.
func ObjectOf(pairs ...any) *Object {
	if len(pairs)%2 != 0 {
		panic("pyjson.ObjectOf takes key, value pairs")
	}
	object := NewObject()
	for index := 0; index < len(pairs); index += 2 {
		object.Set(pairs[index].(string), pairs[index+1])
	}
	return object
}

// Len is the number of members.
func (o *Object) Len() int {
	return len(o.keys)
}

// Keys returns the member keys in order.
func (o *Object) Keys() []string {
	return slices.Clone(o.keys)
}

// Get returns the member under key and whether there is one.
func (o *Object) Get(key string) (any, bool) {
	value, ok := o.values[key]
	return value, ok
}

// Has reports whether key is a member.
func (o *Object) Has(key string) bool {
	_, ok := o.values[key]
	return ok
}

// Set stores value under key, appending key when it is new.
func (o *Object) Set(key string, value any) {
	if o.values == nil {
		o.values = map[string]any{}
	}
	if _, ok := o.values[key]; !ok {
		o.keys = append(o.keys, key)
	}
	o.values[key] = value
}

// Delete removes key and reports whether it was a member.
func (o *Object) Delete(key string) bool {
	if _, ok := o.values[key]; !ok {
		return false
	}
	delete(o.values, key)
	o.keys = slices.DeleteFunc(o.keys, func(candidate string) bool { return candidate == key })
	return true
}
