package bus

import (
	"bytes"
	_ "embed"
	"encoding/json"
	"fmt"
	"regexp"
	"slices"
	"strings"
)

const namespace = "lr"

//go:embed kinds.json
var kindsJSON []byte

var (
	table = loadTable(kindsJSON)
	sha   = regexp.MustCompile(`^[0-9a-f]{40}$`)
	stamp = regexp.MustCompile(`^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$`)
	roles = []string{"supervisor", "lane", "pool", "owner", "root"}
)

type Refused struct {
	Message string
}

func (r *Refused) Error() string {
	return r.Message
}

func refused(format string, args ...any) *Refused {
	return &Refused{Message: fmt.Sprintf(format, args...)}
}

type shape struct {
	base  string
	oneOf []string
}

type field struct {
	name  string
	shape shape
}

type spec struct {
	kind     string
	fields   []field
	optional []field
	identity []string
	audience []string
}

type member struct {
	key   string
	value json.RawMessage
}

func members(object json.RawMessage) []member {
	dec := json.NewDecoder(bytes.NewReader(object))
	if _, err := dec.Token(); err != nil {
		panic(err)
	}
	var out []member
	for dec.More() {
		key, err := dec.Token()
		if err != nil {
			panic(err)
		}
		var value json.RawMessage
		if err := dec.Decode(&value); err != nil {
			panic(err)
		}
		out = append(out, member{key.(string), value})
	}
	return out
}

func fieldsOf(object json.RawMessage) []field {
	if object == nil {
		return nil
	}
	var out []field
	for _, m := range members(object) {
		var oneOf []string
		if json.Unmarshal(m.value, &oneOf) == nil {
			out = append(out, field{m.key, shape{oneOf: oneOf}})
			continue
		}
		var base string
		if err := json.Unmarshal(m.value, &base); err != nil {
			panic(err)
		}
		out = append(out, field{m.key, shape{base: base}})
	}
	return out
}

func loadTable(raw []byte) []spec {
	var out []spec
	for _, m := range members(raw) {
		var parsed struct {
			Fields   json.RawMessage `json:"fields"`
			Optional json.RawMessage `json:"optional"`
			Identity []string        `json:"identity"`
			Audience json.RawMessage `json:"audience"`
		}
		if err := json.Unmarshal(m.value, &parsed); err != nil {
			panic(err)
		}
		var audience []string
		if !bytes.Equal(parsed.Audience, []byte(`"any"`)) {
			if err := json.Unmarshal(parsed.Audience, &audience); err != nil {
				panic(err)
			}
		}
		out = append(out, spec{m.key, fieldsOf(parsed.Fields), fieldsOf(parsed.Optional), parsed.Identity, audience})
	}
	return out
}

func lookup(kind string) (spec, bool) {
	i := slices.IndexFunc(table, func(s spec) bool { return s.kind == kind })
	if i < 0 {
		return spec{}, false
	}
	return table[i], true
}

func names(fields []field) []string {
	out := make([]string, 0, len(fields))
	for _, f := range fields {
		out = append(out, f.name)
	}
	return out
}

func (s spec) all() []field {
	return slices.Concat(s.fields, s.optional)
}

func (s spec) takes(given map[string]any) bool {
	for _, f := range s.fields {
		if _, ok := given[f.name]; !ok {
			return false
		}
	}
	for name := range given {
		if !slices.Contains(names(s.all()), name) {
			return false
		}
	}
	return true
}

func Check(kind string, given map[string]any, to []string) error {
	s, ok := lookup(kind)
	if !ok {
		kinds := make([]string, 0, len(table))
		for _, known := range table {
			kinds = append(kinds, known.kind)
		}
		return refused("unknown kind '%s'; the kinds are %s", kind, strings.Join(kinds, ", "))
	}
	if !s.takes(given) {
		return wrongFields(s, given, to)
	}
	for _, f := range s.all() {
		value, ok := given[f.name]
		if !ok {
			continue
		}
		if list, isList := value.([]any); isList && f.name == "pr" {
			return onePR(s, given, list, to)
		}
		if !f.shape.fits(value) {
			return refused("%s.%s must be %s, got %s", kind, f.name, f.shape.describe(), repr(value))
		}
	}
	return s.reaches(to)
}

func wrongFields(s spec, given map[string]any, to []string) error {
	for _, other := range table {
		if other.kind != s.kind && other.takes(given) {
			return refused("these fields are a %s record, not %s; run: %s", other.kind, s.kind, command(other, given, to))
		}
	}
	var missing, unexpected []string
	for _, f := range s.fields {
		if _, ok := given[f.name]; !ok {
			missing = append(missing, f.name)
		}
	}
	for name := range given {
		if !slices.Contains(names(s.all()), name) {
			unexpected = append(unexpected, name)
		}
	}
	slices.Sort(missing)
	slices.Sort(unexpected)
	message := fmt.Sprintf("%s takes %s", s.kind, strings.Join(names(s.fields), ", "))
	if missing != nil {
		message += "; missing " + strings.Join(missing, ", ")
	}
	if unexpected != nil {
		message += "; unexpected " + strings.Join(unexpected, ", ")
	}
	return &Refused{Message: message}
}

func onePR(s spec, given map[string]any, prs []any, to []string) error {
	commands := make([]string, 0, len(prs))
	for _, pr := range prs {
		one := make(map[string]any, len(given))
		for name, value := range given {
			one[name] = value
		}
		one["pr"] = pr
		commands = append(commands, command(s, one, to))
	}
	return refused("a %s names one PR; post one record per PR: %s", s.kind, strings.Join(commands, " && "))
}

func (s spec) reaches(to []string) error {
	if s.audience == nil {
		return nil
	}
	for _, lane := range to {
		if slices.Contains(s.audience, lane) || slices.Contains(s.audience, "lane") && !slices.Contains(roles, lane) {
			continue
		}
		message := fmt.Sprintf("a %s record never reaches %s; it goes to %s", s.kind, lane, strings.Join(s.audience, ", "))
		if lane == "root" {
			message += "; to tell root, post an escalation, an ask or a decision"
		}
		return &Refused{Message: message}
	}
	return nil
}

func (sh shape) fits(value any) bool {
	text, isText := value.(string)
	switch {
	case sh.oneOf != nil:
		return isText && slices.Contains(sh.oneOf, text)
	case sh.base == "int":
		number, isNumber := value.(json.Number)
		if !isNumber {
			return false
		}
		_, err := number.Int64()
		return err == nil
	case sh.base == "str":
		return isText && strings.TrimSpace(text) != ""
	case sh.base == "sha":
		return isText && sha.MatchString(text)
	case sh.base == "time":
		return isText && stamp.MatchString(text)
	}
	panic("unknown shape " + sh.base)
}

func (sh shape) describe() string {
	switch {
	case sh.oneOf != nil:
		return "one of " + strings.Join(sh.oneOf, ", ")
	case sh.base == "int":
		return "an integer"
	case sh.base == "str":
		return "non-empty text"
	case sh.base == "sha":
		return "a full 40-character sha"
	case sh.base == "time":
		return "a UTC time like 2026-10-31T06:00:00Z"
	}
	panic("unknown shape " + sh.base)
}

func repr(value any) string {
	if text, isText := value.(string); isText {
		return "'" + text + "'"
	}
	return plain(value)
}

func plain(value any) string {
	if text, isText := value.(string); isText {
		return text
	}
	encoded, err := json.Marshal(value)
	if err != nil {
		panic(err)
	}
	return string(encoded)
}

func command(s spec, given map[string]any, to []string) string {
	words := []string{"lr post", s.kind}
	for _, f := range s.all() {
		if value, ok := given[f.name]; ok {
			words = append(words, "--"+f.name, plain(value))
		}
	}
	for _, lane := range to {
		words = append(words, "--to", lane)
	}
	return strings.Join(words, " ")
}

func identity(s spec, given map[string]any) string {
	parts := []string{namespace + "." + s.kind}
	for _, name := range s.identity {
		parts = append(parts, plain(given[name]))
	}
	return strings.Join(parts, "/")
}

func summary(s spec, given map[string]any) string {
	parts := []string{s.kind}
	for _, f := range s.all() {
		value, ok := given[f.name]
		switch {
		case !ok || f.name == "text":
		case f.name == "pr":
			parts = append(parts, "#"+plain(value))
		case f.name == "head":
			parts = append(parts, plain(value)[:12])
		default:
			parts = append(parts, plain(value))
		}
	}
	line := strings.Join(parts, " ")
	if text, ok := given["text"]; ok {
		line += ": " + plain(text)
	}
	return line
}
