package store

import (
	"errors"
	"fmt"
	"strings"
	"unicode"

	"github.com/yasyf/cc-skills/plugins/long-running/desk/pyjson"
)

// ValueError is a ValueError the store raises: a refused action transition, an invalid incident ID, an unsupported
// store version, or text SQLite cannot hold as UTF-8.
type ValueError struct{ Message string }

func (e *ValueError) Error() string { return e.Message }

// KeyError is a missing fact or action key; its message is the key's repr, as Python's is.
type KeyError struct{ Key string }

func (e *KeyError) Error() string { return pyRepr(e.Key) }

// UnknownAction is the KeyError for an action ID the incident does not hold.
type UnknownAction struct{ ActionID string }

func (e *UnknownAction) Error() string { return pyRepr(e.ActionID) }

// Unwrap makes an UnknownAction match KeyError, its Python base class.
func (e *UnknownAction) Unwrap() error { return &KeyError{Key: e.ActionID} }

// RuntimeError is transaction state used after its transaction closed, or a fact mapping used after the
// transaction replaced or removed it.
type RuntimeError struct{ Message string }

func (e *RuntimeError) Error() string { return e.Message }

// StaleGeneration means the stored owner generation moved past the writer's, so the writer no longer owns the
// incident.
type StaleGeneration struct{ Message string }

func (e *StaleGeneration) Error() string { return e.Message }

// Unwrap makes a StaleGeneration match RuntimeError, its Python base class.
func (e *StaleGeneration) Unwrap() error { return &RuntimeError{Message: e.Message} }

// NoIncident is the SystemExit the store raises for an incident with no database.
type NoIncident struct {
	IncidentID string
	Root       string
}

func (e *NoIncident) Error() string { return fmt.Sprintf("no incident %s in %s", e.IncidentID, e.Root) }

func refusesAttempt(err error) bool {
	var value *ValueError
	var unknown *UnknownAction
	var decode *pyjson.DecodeError
	var limit *pyjson.ValueError
	return errors.As(err, &value) || errors.As(err, &unknown) || errors.As(err, &decode) || errors.As(err, &limit)
}

func pyRepr(text string) string {
	quote := '\''
	if strings.ContainsRune(text, '\'') && !strings.ContainsRune(text, '"') {
		quote = '"'
	}
	var out strings.Builder
	out.WriteRune(quote)
	for index := 0; index < len(text); {
		r, size := pyjson.DecodeRune(text[index:])
		if size == 0 {
			r, size = rune(text[index]), 1
		}
		index += size
		switch {
		case r == quote || r == '\\':
			out.WriteByte('\\')
			out.WriteRune(r)
		case r == '\t':
			out.WriteString(`\t`)
		case r == '\n':
			out.WriteString(`\n`)
		case r == '\r':
			out.WriteString(`\r`)
		case r < ' ' || r == 0x7f:
			fmt.Fprintf(&out, `\x%02x`, r)
		case r < 0x7f || unicode.IsPrint(r):
			out.WriteRune(r)
		case r <= 0xff:
			fmt.Fprintf(&out, `\x%02x`, r)
		case r <= 0xffff:
			fmt.Fprintf(&out, `\u%04x`, r)
		default:
			fmt.Fprintf(&out, `\U%08x`, r)
		}
	}
	out.WriteRune(quote)
	return out.String()
}
