package store

import (
	"errors"
	"slices"
	"testing"
	"time"
)

func TestRandomChoosesAsPythonsRandomZero(t *testing.T) {
	want := []int{49, 53, 5, 33, 62, 51, 38, 61, 45, 27, 17, 36, 17, 12, 32, 18, 39, 12, 9, 42}
	random := newPyRandom()
	got := make([]int, len(want))
	for index := range got {
		got[index] = random.below(readerLimit)
	}
	if !slices.Equal(got, want) {
		t.Errorf("Random(0).choice(range(64)) draws %v, want %v", got, want)
	}
}

func TestStampIsUTCToTheSecond(t *testing.T) {
	cases := map[string]time.Time{
		"2026-11-02T08:00:01Z": time.Date(2026, 11, 2, 0, 0, 1, 0, time.FixedZone("", -8*3600)),
		"2026-11-02T08:00:02Z": time.Date(2026, 11, 2, 8, 0, 2, 999999999, time.UTC),
		"2025-12-31T23:30:00Z": time.Date(2026, 1, 1, 0, 30, 0, 0, time.FixedZone("", 3600)),
	}
	for want, at := range cases {
		if got := Stamp(at); got != want {
			t.Errorf("Stamp(%v) = %s, want %s", at, got, want)
		}
	}
}

func TestParseStampReadsWhatStampWrites(t *testing.T) {
	cases := map[string]time.Time{
		"2026-11-02T08:00:00Z":         time.Date(2026, 11, 2, 8, 0, 0, 0, time.UTC),
		"2026-11-02T08:00:00.5Z":       time.Date(2026, 11, 2, 8, 0, 0, 500000000, time.UTC),
		"2026-11-02T08:00:00.1234567Z": time.Date(2026, 11, 2, 8, 0, 0, 123456000, time.UTC),
		"2026-11-02T08:00:00+05:30":    time.Date(2026, 11, 2, 2, 30, 0, 0, time.UTC),
		"2026-11-02 08:00:00-07:00":    time.Date(2026, 11, 2, 15, 0, 0, 0, time.UTC),
		"2026-11-02T24:00:00Z":         time.Date(2026, 11, 3, 0, 0, 0, 0, time.UTC),
		"2026-11-02T08:00:00+05:30:15": time.Date(2026, 11, 2, 2, 29, 45, 0, time.UTC),
	}
	for text, want := range cases {
		got, err := ParseStamp(text)
		if err != nil || !got.Equal(want) {
			t.Errorf("ParseStamp(%q) = %v, %v; want %v", text, got, err, want)
		}
	}
	if parsed, _ := ParseStamp("2026-11-02T08:00:00Z"); parsed.Location() != time.UTC {
		t.Errorf("a Z stamp parses in %v, not UTC", parsed.Location())
	}
	refused := map[string]string{
		"2026-11-02T08:00:00.Z":     "Invalid isoformat string: '2026-11-02T08:00:00.+00:00'",
		"2026-11-02T08:00:00ZZ":     "Invalid isoformat string: '2026-11-02T08:00:00+00:00+00:00'",
		"2026-02-30T00:00:00Z":      "day 30 must be in range 1..28 for month 2 in year 2026",
		"2026-11-02T08:00:60Z":      "second must be in 0..59, not 60",
		"2026-13-02T08:00:00Z":      "month must be in 1..12, not 13",
		"2026-11-02T24:00:01Z":      "minute, second, and microsecond must be 0 when hour is 24",
		"9999-12-31T24:00:00Z":      "year must be in 1..9999, not 10000",
		"2026-11-02T08:00:00+24:00": "offset must be a timedelta strictly between -timedelta(hours=24) and timedelta(hours=24), not datetime.timedelta(days=1)",
		"2026-11-02T08:00:00-24:00": "offset must be a timedelta strictly between -timedelta(hours=24) and timedelta(hours=24), not datetime.timedelta(days=-1)",
	}
	for text, want := range refused {
		_, err := ParseStamp(text)
		var value *ValueError
		if !errors.As(err, &value) || err.Error() != want {
			t.Errorf("ParseStamp(%q) = %v; want the ValueError %q", text, err, want)
		}
	}
	var naive *ValueError
	if _, err := ParseStamp("2026-11-02T08:00:00"); !errors.As(err, &naive) {
		t.Errorf("a stamp with no offset parses with %v, not a refusal", err)
	}
}
