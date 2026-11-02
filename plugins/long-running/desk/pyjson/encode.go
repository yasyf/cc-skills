package pyjson

import (
	"fmt"
	"math"
	"math/big"
	"strconv"
	"strings"
	"unicode/utf8"
)

const maxIntDigits = 4300

// TypeError is the TypeError json.dumps raises for a value it cannot serialize.
type TypeError struct{ Message string }

func (e *TypeError) Error() string { return e.Message }

// ValueError is a ValueError the json module raises outside JSONDecodeError: a circular reference, or an integer
// past Python's 4300-digit string conversion limit.
type ValueError struct{ Message string }

func (e *ValueError) Error() string { return e.Message }

// Dumps is json.dumps(value): ", " and ": " separators, every character outside printable ASCII escaped, object
// members in insertion order, and floats as Python's repr writes them. Values are nil, bool, int, int64, *big.Int,
// float64, string, []any and *Object; a lone surrogate travels in a string as its three-byte WTF-8 form.
func Dumps(value any) (string, error) {
	return dumps(value, ", ", ": ")
}

// DumpsCompact is json.dumps(value, separators=(",", ":")).
func DumpsCompact(value any) (string, error) {
	return dumps(value, ",", ":")
}

// FloatRepr is Python's repr(value): the shortest digits that read back as value, positional with at least one
// fractional digit when the decimal exponent lies in [-4, 16), in exponent form otherwise.
func FloatRepr(value float64) string {
	switch {
	case math.IsNaN(value):
		return "nan"
	case math.IsInf(value, 1):
		return "inf"
	case math.IsInf(value, -1):
		return "-inf"
	}
	exponential := strconv.FormatFloat(value, 'e', -1, 64)
	power, err := strconv.Atoi(exponential[strings.IndexByte(exponential, 'e')+1:])
	if err != nil {
		panic(err)
	}
	if power < -4 || power >= 16 {
		return exponential
	}
	positional := strconv.FormatFloat(value, 'f', -1, 64)
	if !strings.Contains(positional, ".") {
		positional += ".0"
	}
	return positional
}

// DecodeRune decodes the first character of text as a Python string holds it: a UTF-8 character, or a lone
// surrogate in its three-byte WTF-8 form. Its size is 0 when text starts with neither.
func DecodeRune(text string) (rune, int) {
	r, size := utf8.DecodeRuneInString(text)
	if r != utf8.RuneError || size > 1 {
		return r, size
	}
	if len(text) >= 3 && text[0] == 0xed && text[1] >= 0xa0 && text[1] <= 0xbf && text[2] >= 0x80 && text[2] <= 0xbf {
		return 0xd000 | rune(text[1]&0x3f)<<6 | rune(text[2]&0x3f), 3
	}
	return utf8.RuneError, 0
}

type encoder struct {
	out  strings.Builder
	item string
	key  string
	open map[*Object]bool
}

func dumps(value any, item, key string) (string, error) {
	e := encoder{item: item, key: key, open: map[*Object]bool{}}
	if err := e.value(value); err != nil {
		return "", err
	}
	return e.out.String(), nil
}

func (e *encoder) value(value any) error {
	switch value := value.(type) {
	case nil:
		e.out.WriteString("null")
	case bool:
		e.out.WriteString(strconv.FormatBool(value))
	case int:
		e.out.WriteString(strconv.Itoa(value))
	case int64:
		e.out.WriteString(strconv.FormatInt(value, 10))
	case *big.Int:
		digits := value.String()
		if len(strings.TrimPrefix(digits, "-")) > maxIntDigits {
			return &ValueError{Message: "Exceeds the limit (4300 digits) for integer string conversion; use sys.set_int_max_str_digits() to increase the limit"}
		}
		e.out.WriteString(digits)
	case float64:
		e.out.WriteString(floatLiteral(value))
	case string:
		return e.text(value)
	case []any:
		return e.list(value)
	case *Object:
		return e.object(value)
	default:
		return &TypeError{Message: fmt.Sprintf("Object of type %T is not JSON serializable", value)}
	}
	return nil
}

func floatLiteral(value float64) string {
	switch {
	case math.IsNaN(value):
		return "NaN"
	case math.IsInf(value, 1):
		return "Infinity"
	case math.IsInf(value, -1):
		return "-Infinity"
	}
	return FloatRepr(value)
}

func (e *encoder) list(items []any) error {
	if len(items) == 0 {
		e.out.WriteString("[]")
		return nil
	}
	e.out.WriteByte('[')
	for index, item := range items {
		if index > 0 {
			e.out.WriteString(e.item)
		}
		if err := e.value(item); err != nil {
			return err
		}
	}
	e.out.WriteByte(']')
	return nil
}

func (e *encoder) object(object *Object) error {
	if object.Len() == 0 {
		e.out.WriteString("{}")
		return nil
	}
	if e.open[object] {
		return &ValueError{Message: "Circular reference detected"}
	}
	e.open[object] = true
	e.out.WriteByte('{')
	for index, key := range object.keys {
		if index > 0 {
			e.out.WriteString(e.item)
		}
		if err := e.text(key); err != nil {
			return err
		}
		e.out.WriteString(e.key)
		if err := e.value(object.values[key]); err != nil {
			return err
		}
	}
	e.out.WriteByte('}')
	delete(e.open, object)
	return nil
}

const hexDigits = "0123456789abcdef"

func (e *encoder) text(text string) error {
	e.out.WriteByte('"')
	for index := 0; index < len(text); {
		if c := text[index]; c >= ' ' && c <= '~' && c != '\\' && c != '"' {
			e.out.WriteByte(c)
			index++
			continue
		}
		r, size := DecodeRune(text[index:])
		if size == 0 {
			return &TypeError{Message: fmt.Sprintf("string %q is not text a Python str can hold", text)}
		}
		index += size
		switch r {
		case '\\', '"':
			e.out.WriteByte('\\')
			e.out.WriteByte(byte(r))
		case '\b':
			e.out.WriteString(`\b`)
		case '\f':
			e.out.WriteString(`\f`)
		case '\n':
			e.out.WriteString(`\n`)
		case '\r':
			e.out.WriteString(`\r`)
		case '\t':
			e.out.WriteString(`\t`)
		default:
			if r >= 0x10000 {
				r -= 0x10000
				e.escape(0xd800 | r>>10&0x3ff)
				e.escape(0xdc00 | r&0x3ff)
			} else {
				e.escape(r)
			}
		}
	}
	e.out.WriteByte('"')
	return nil
}

func (e *encoder) escape(r rune) {
	e.out.WriteString(`\u`)
	e.out.WriteByte(hexDigits[r>>12&0xf])
	e.out.WriteByte(hexDigits[r>>8&0xf])
	e.out.WriteByte(hexDigits[r>>4&0xf])
	e.out.WriteByte(hexDigits[r&0xf])
}
