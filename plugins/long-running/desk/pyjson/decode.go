package pyjson

import (
	"errors"
	"fmt"
	"math"
	"math/big"
	"strconv"
	"strings"
)

// DecodeError is a json.JSONDecodeError, a ValueError: the message and the position Python reports, counted in
// characters.
type DecodeError struct {
	Message string
	Pos     int
	Lineno  int
	Colno   int
}

func (e *DecodeError) Error() string {
	return fmt.Sprintf("%s: line %d column %d (char %d)", e.Message, e.Lineno, e.Colno, e.Pos)
}

// Loads is json.loads(text): an object decodes as an *Object in member order, a repeated key keeping its first
// position and its last value; an integer as int64, or *big.Int past int64; any other number as float64, NaN and
// Infinity included; and a lone surrogate escape as its three-byte WTF-8 form.
func Loads(text string) (any, error) {
	for index := 0; index < len(text); {
		_, size := DecodeRune(text[index:])
		if size == 0 {
			return nil, &TypeError{Message: fmt.Sprintf("JSON text %q is not text a Python str can hold", text)}
		}
		index += size
	}
	d := decoder{text: text}
	if strings.HasPrefix(text, "\ufeff") {
		return nil, d.fail("Unexpected UTF-8 BOM (decode using utf-8-sig)", 0)
	}
	value, end, err := d.scan(d.skip(0))
	if err != nil {
		return nil, err
	}
	if end = d.skip(end); end != len(text) {
		return nil, d.fail("Extra data", end)
	}
	return value, nil
}

type decoder struct{ text string }

func characters(text string) int {
	count := 0
	for index := 0; index < len(text); count++ {
		_, size := DecodeRune(text[index:])
		index += size
	}
	return count
}

func (d *decoder) fail(message string, at int) error {
	prefix := d.text[:at]
	pos := characters(prefix)
	colno := pos + 1
	if newline := strings.LastIndexByte(prefix, '\n'); newline >= 0 {
		colno = pos - characters(prefix[:newline])
	}
	return &DecodeError{Message: message, Pos: pos, Lineno: strings.Count(prefix, "\n") + 1, Colno: colno}
}

func (d *decoder) skip(at int) int {
	for at < len(d.text) && strings.IndexByte(" \t\n\r", d.text[at]) >= 0 {
		at++
	}
	return at
}

func (d *decoder) scan(at int) (any, int, error) {
	text := d.text
	if at >= len(text) {
		return nil, 0, d.fail("Expecting value", at)
	}
	rest := text[at:]
	switch text[at] {
	case '"':
		return d.string(at + 1)
	case '{':
		return d.object(at + 1)
	case '[':
		return d.array(at + 1)
	case 'n':
		if strings.HasPrefix(rest, "null") {
			return nil, at + 4, nil
		}
	case 't':
		if strings.HasPrefix(rest, "true") {
			return true, at + 4, nil
		}
	case 'f':
		if strings.HasPrefix(rest, "false") {
			return false, at + 5, nil
		}
	case 'N':
		if strings.HasPrefix(rest, "NaN") {
			return math.NaN(), at + 3, nil
		}
	case 'I':
		if strings.HasPrefix(rest, "Infinity") {
			return math.Inf(1), at + 8, nil
		}
	case '-':
		if strings.HasPrefix(rest, "-Infinity") {
			return math.Inf(-1), at + 9, nil
		}
	}
	return d.number(at)
}

func digit(c byte) bool {
	return c >= '0' && c <= '9'
}

func (d *decoder) number(start int) (any, int, error) {
	text, at, last := d.text, start, len(d.text)-1
	if text[at] == '-' {
		at++
		if at > last {
			return nil, 0, d.fail("Expecting value", start)
		}
	}
	switch {
	case text[at] >= '1' && text[at] <= '9':
		at++
		for at <= last && digit(text[at]) {
			at++
		}
	case text[at] == '0':
		at++
	default:
		return nil, 0, d.fail("Expecting value", start)
	}
	fractional := false
	if at < last && text[at] == '.' && digit(text[at+1]) {
		fractional = true
		at += 2
		for at <= last && digit(text[at]) {
			at++
		}
	}
	if at < last && (text[at] == 'e' || text[at] == 'E') {
		mark := at
		at++
		if at < last && (text[at] == '-' || text[at] == '+') {
			at++
		}
		for at <= last && digit(text[at]) {
			at++
		}
		if digit(text[at-1]) {
			fractional = true
		} else {
			at = mark
		}
	}
	literal := text[start:at]
	if fractional {
		value, err := strconv.ParseFloat(literal, 64)
		if err != nil && !errors.Is(err, strconv.ErrRange) {
			return nil, 0, err
		}
		return value, at, nil
	}
	if value, err := strconv.ParseInt(literal, 10, 64); err == nil {
		return value, at, nil
	}
	if digits := len(strings.TrimPrefix(literal, "-")); digits > maxIntDigits {
		return nil, 0, &ValueError{Message: fmt.Sprintf("Exceeds the limit (4300 digits) for integer string conversion: value has %d digits; use sys.set_int_max_str_digits() to increase the limit", digits)}
	}
	value, ok := new(big.Int).SetString(literal, 10)
	if !ok {
		return nil, 0, fmt.Errorf("pyjson: integer literal %q", literal)
	}
	return value, at, nil
}

func hex4(text string) (rune, bool) {
	if len(text) != 4 {
		return 0, false
	}
	value, err := strconv.ParseUint(text, 16, 32)
	if err != nil {
		return 0, false
	}
	return rune(value), true
}

func atLeast(text string, count int) bool {
	for index := 0; count > 0; count-- {
		if index >= len(text) {
			return false
		}
		_, size := DecodeRune(text[index:])
		index += size
	}
	return true
}

func appendCharacter(out *strings.Builder, r rune) {
	if r >= 0xd800 && r <= 0xdfff {
		out.WriteByte(byte(0xe0 | r>>12))
		out.WriteByte(byte(0x80 | r>>6&0x3f))
		out.WriteByte(byte(0x80 | r&0x3f))
		return
	}
	out.WriteRune(r)
}

func (d *decoder) string(end int) (string, int, error) {
	text := d.text
	begin := end - 1
	var out strings.Builder
	for {
		next := end
		for next < len(text) && text[next] != '"' && text[next] != '\\' {
			if text[next] <= 0x1f {
				return "", 0, d.fail("Invalid control character at", next)
			}
			next++
		}
		if next >= len(text) {
			return "", 0, d.fail("Unterminated string starting at", begin)
		}
		out.WriteString(text[end:next])
		if text[next] == '"' {
			return out.String(), next + 1, nil
		}
		next++
		if next == len(text) {
			return "", 0, d.fail("Unterminated string starting at", begin)
		}
		if c := text[next]; c != 'u' {
			end = next + 1
			switch c {
			case '"', '\\', '/':
				out.WriteByte(c)
			case 'b':
				out.WriteByte('\b')
			case 'f':
				out.WriteByte('\f')
			case 'n':
				out.WriteByte('\n')
			case 'r':
				out.WriteByte('\r')
			case 't':
				out.WriteByte('\t')
			default:
				return "", 0, d.fail("Invalid \\escape", end-2)
			}
			continue
		}
		next++
		if !atLeast(text[next:], 5) {
			return "", 0, d.fail("Invalid \\uXXXX escape", next-1)
		}
		r, ok := hex4(text[next : next+4])
		if !ok {
			return "", 0, d.fail("Invalid \\uXXXX escape", next-1)
		}
		end = next + 4
		if r >= 0xd800 && r <= 0xdbff && atLeast(text[end:], 7) && text[end] == '\\' && text[end+1] == 'u' {
			low, ok := hex4(text[end+2 : end+6])
			if !ok {
				return "", 0, d.fail("Invalid \\uXXXX escape", end+1)
			}
			if low >= 0xdc00 && low <= 0xdfff {
				r = 0x10000 + (r-0xd800)<<10 + (low - 0xdc00)
				end += 6
			}
		}
		appendCharacter(&out, r)
	}
}

func (d *decoder) object(at int) (any, int, error) {
	text, last := d.text, len(d.text)-1
	object := NewObject()
	at = d.skip(at)
	if at > last || text[at] != '}' {
		for {
			if at > last || text[at] != '"' {
				return nil, 0, d.fail("Expecting property name enclosed in double quotes", at)
			}
			key, next, err := d.string(at + 1)
			if err != nil {
				return nil, 0, err
			}
			at = d.skip(next)
			if at > last || text[at] != ':' {
				return nil, 0, d.fail("Expecting ':' delimiter", at)
			}
			value, next, err := d.scan(d.skip(at + 1))
			if err != nil {
				return nil, 0, err
			}
			object.Set(key, value)
			at = d.skip(next)
			if at <= last && text[at] == '}' {
				break
			}
			if at > last || text[at] != ',' {
				return nil, 0, d.fail("Expecting ',' delimiter", at)
			}
			comma := at
			if at = d.skip(at + 1); at <= last && text[at] == '}' {
				return nil, 0, d.fail("Illegal trailing comma before end of object", comma)
			}
		}
	}
	return object, at + 1, nil
}

func (d *decoder) array(at int) (any, int, error) {
	text, last := d.text, len(d.text)-1
	items := []any{}
	at = d.skip(at)
	if at > last || text[at] != ']' {
		for {
			value, next, err := d.scan(at)
			if err != nil {
				return nil, 0, err
			}
			items = append(items, value)
			at = d.skip(next)
			if at <= last && text[at] == ']' {
				break
			}
			if at > last || text[at] != ',' {
				return nil, 0, d.fail("Expecting ',' delimiter", at)
			}
			comma := at
			if at = d.skip(at + 1); at <= last && text[at] == ']' {
				return nil, 0, d.fail("Illegal trailing comma before end of array", comma)
			}
		}
	}
	return items, at + 1, nil
}
