package pyjson

import (
	"errors"
	"math"
	"math/big"
	"strings"
	"testing"
)

func TestDumpsWritesWhatPythonWrites(t *testing.T) {
	big70, _ := new(big.Int).SetString("1180591620717411303424", 10)
	cases := []struct {
		value   any
		dumps   string
		compact string
	}{
		{ObjectOf("b", 1, "a", []any{1.5, nil, true, "x", NewObject(), []any{}}), `{"b": 1, "a": [1.5, null, true, "x", {}, []]}`, `{"b":1,"a":[1.5,null,true,"x",{},[]]}`},
		{"ü🚀<>&\u2028\"\\/\t\x01\x7f\b\f", `"\u00fc\ud83d\ude80<>&\u2028\"\\/\t\u0001\u007f\b\f"`, ""},
		{"\xed\xa0\xbd", `"\ud83d"`, ""},
		{[]any{math.NaN(), math.Inf(1), math.Inf(-1)}, `[NaN, Infinity, -Infinity]`, `[NaN,Infinity,-Infinity]`},
		{big70, "1180591620717411303424", ""},
		{int64(-12), "-12", ""},
		{ObjectOf("k", ObjectOf("deep", []any{ObjectOf()})), `{"k": {"deep": [{}]}}`, `{"k":{"deep":[{}]}}`},
	}
	for _, c := range cases {
		if c.compact == "" {
			c.compact = c.dumps
		}
		if got, err := Dumps(c.value); err != nil || got != c.dumps {
			t.Errorf("Dumps(%#v) = %q, %v; want %q", c.value, got, err, c.dumps)
		}
		if got, err := DumpsCompact(c.value); err != nil || got != c.compact {
			t.Errorf("DumpsCompact(%#v) = %q, %v; want %q", c.value, got, err, c.compact)
		}
	}
}

func TestFloatReprIsPythonRepr(t *testing.T) {
	cases := []struct {
		value float64
		want  string
	}{
		{1e16, "1e+16"},
		{1e15, "1000000000000000.0"},
		{0.0001, "0.0001"},
		{0.00001, "1e-05"},
		{1.5e-7, "1.5e-07"},
		{123456789.125, "123456789.125"},
		{0.1, "0.1"},
		{math.Copysign(0, -1), "-0.0"},
		{0, "0.0"},
		{1e300, "1e+300"},
		{5e-324, "5e-324"},
		{2.5, "2.5"},
		{1e22, "1e+22"},
		{math.MaxFloat64, "1.7976931348623157e+308"},
		{math.Inf(1), "inf"},
		{math.Inf(-1), "-inf"},
		{100000, "100000.0"},
		{-1234.5, "-1234.5"},
		{0.30000000000000004, "0.30000000000000004"},
		{9007199254740993, "9007199254740992.0"},
		{1.0000000000000002e+16, "1.0000000000000002e+16"},
		{0.000123, "0.000123"},
	}
	for _, c := range cases {
		if got := FloatRepr(c.value); got != c.want {
			t.Errorf("FloatRepr(%v) = %q, want %q", c.value, got, c.want)
		}
	}
	if got := FloatRepr(math.NaN()); got != "nan" {
		t.Errorf("FloatRepr(NaN) = %q", got)
	}
}

func TestLoadsReadsWhatPythonReads(t *testing.T) {
	cases := []struct{ text, compact string }{
		{`{"b":1,"a":2,"b":3}`, `{"b":3,"a":2}`},
		{`-0`, `0`},
		{`1E400`, `Infinity`},
		{`1e-400`, `0.0`},
		{`[1.0, 1e5, -0.0, 12345678901234567890123]`, `[1.0,100000.0,-0.0,12345678901234567890123]`},
		{`"\ud83d\ude80"`, `"\ud83d\ude80"`},
		{`"\ud83d"`, `"\ud83d"`},
		{`"\ud83d\u0041"`, `"\ud83dA"`},
		{`"\ude80\ud83d"`, `"\ude80\ud83d"`},
		{" [ 1 ,\n\t2 ] \r\n", `[1,2]`},
		{`"\/\b\f\n\r\t\"\\"`, `"/\b\f\n\r\t\"\\"`},
		{`[NaN, -Infinity, Infinity, true, false, null]`, `[NaN,-Infinity,Infinity,true,false,null]`},
		{`"é\u00E9"`, `"\u00e9\u00e9"`},
	}
	for _, c := range cases {
		value, err := Loads(c.text)
		if err != nil {
			t.Errorf("Loads(%q): %v", c.text, err)
			continue
		}
		if got, err := DumpsCompact(value); err != nil || got != c.compact {
			t.Errorf("Loads(%q) dumps as %q, %v; want %q", c.text, got, err, c.compact)
		}
	}
	rocket, err := Loads(`"\ud83d\ude80"`)
	if err != nil || rocket != "🚀" {
		t.Errorf("a surrogate pair decodes as %q, %v", rocket, err)
	}
	lone, err := Loads(`"\ud83dx"`)
	if err != nil || lone != "\xed\xa0\xbdx" {
		t.Errorf("a lone surrogate decodes as %q, %v", lone, err)
	}
	for text, want := range map[string]any{`7`: int64(7), `9223372036854775807`: int64(math.MaxInt64), `1.0`: 1.0} {
		if got, err := Loads(text); err != nil || got != want {
			t.Errorf("Loads(%q) = %#v, %v; want %#v", text, got, err, want)
		}
	}
	huge, err := Loads(`9223372036854775808`)
	if number, ok := huge.(*big.Int); err != nil || !ok || number.String() != "9223372036854775808" {
		t.Errorf("an integer past int64 decodes as %#v, %v", huge, err)
	}
}

func TestLoadsFailsAsPythonFails(t *testing.T) {
	cases := map[string]string{
		"":               "Expecting value: line 1 column 1 (char 0)",
		`{"a" 1}`:        "Expecting ':' delimiter: line 1 column 6 (char 5)",
		`[1,]`:           "Illegal trailing comma before end of array: line 1 column 3 (char 2)",
		`{"a":1,}`:       "Illegal trailing comma before end of object: line 1 column 7 (char 6)",
		`"a`:             "Unterminated string starting at: line 1 column 1 (char 0)",
		`1 2`:            "Extra data: line 1 column 3 (char 2)",
		"\"\x01\"":       "Invalid control character at: line 1 column 2 (char 1)",
		`"\x"`:           `Invalid \escape: line 1 column 2 (char 1)`,
		`"\u12"`:         `Invalid \uXXXX escape: line 1 column 3 (char 2)`,
		`[1 2]`:          "Expecting ',' delimiter: line 1 column 4 (char 3)",
		`{1:2}`:          "Expecting property name enclosed in double quotes: line 1 column 2 (char 1)",
		`-`:              "Expecting value: line 1 column 1 (char 0)",
		"\ufeff1":        "Unexpected UTF-8 BOM (decode using utf-8-sig): line 1 column 1 (char 0)",
		`[`:              "Expecting value: line 1 column 2 (char 1)",
		`{`:              "Expecting property name enclosed in double quotes: line 1 column 2 (char 1)",
		`nul`:            "Expecting value: line 1 column 1 (char 0)",
		`"\ud83d\u12zz"`: `Invalid \uXXXX escape: line 1 column 9 (char 8)`,
		`01`:             "Extra data: line 1 column 2 (char 1)",
		"\n\n  x":        "Expecting value: line 3 column 3 (char 4)",
		`["ü", x]`:       "Expecting value: line 1 column 7 (char 6)",
	}
	for text, want := range cases {
		_, err := Loads(text)
		var decode *DecodeError
		if !errors.As(err, &decode) || err.Error() != want {
			t.Errorf("Loads(%q) = %v; want the JSONDecodeError %q", text, err, want)
		}
	}
	var limit *ValueError
	if _, err := Loads(strings.Repeat("7", 4301)); !errors.As(err, &limit) {
		t.Errorf("a 4301-digit integer decodes with %v, not Python's ValueError", err)
	}
	digits, _ := new(big.Int).SetString(strings.Repeat("7", 4301), 10)
	if _, err := Dumps(digits); !errors.As(err, &limit) {
		t.Errorf("a 4301-digit integer encodes with %v, not Python's ValueError", err)
	}
	var unserializable *TypeError
	if _, err := Dumps(map[string]any{}); !errors.As(err, &unserializable) {
		t.Errorf("a Go map encodes with %v, not a TypeError", err)
	}
}

func TestObjectKeepsDictOrder(t *testing.T) {
	object := ObjectOf("a", 1, "b", 2, "c", 3)
	object.Set("a", 4)
	object.Delete("b")
	object.Set("b", 5)
	if got, _ := DumpsCompact(object); got != `{"a":4,"c":3,"b":5}` {
		t.Errorf("object dumps as %s", got)
	}
	cyclic := NewObject()
	cyclic.Set("self", cyclic)
	var circular *ValueError
	if _, err := Dumps(cyclic); !errors.As(err, &circular) || err.Error() != "Circular reference detected" {
		t.Errorf("a cyclic object encodes with %v", err)
	}
}
