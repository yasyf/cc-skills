package store

import (
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/desk/pyjson"
)

// Stamp is actions.stamp: the instant in UTC to the second, as 2006-01-02T15:04:05Z.
func Stamp(at time.Time) string {
	return at.UTC().Format("2006-01-02T15:04:05Z")
}

// ParseStamp is actions.parse_stamp on Python 3.14 for the extended form stamps take: YYYY-MM-DD, one separator,
// HH:MM:SS with an optional fraction, and Z or a ±HH:MM[:SS] offset. Python's basic, reduced and week-date forms are
// refused, and so is a stamp with no offset, which Python reads as a naive datetime.
func ParseStamp(value string) (time.Time, error) {
	text := strings.ReplaceAll(value, "Z", "+00:00")
	invalid := &ValueError{Message: "Invalid isoformat string: " + pyRepr(text)}
	if len(text) < 10 || text[4] != '-' || text[7] != '-' {
		return time.Time{}, invalid
	}
	year, okYear := digits(text[0:4])
	month, okMonth := digits(text[5:7])
	day, okDay := digits(text[8:10])
	if !okYear || !okMonth || !okDay {
		return time.Time{}, invalid
	}
	_, separator := pyjson.DecodeRune(text[10:])
	if separator == 0 {
		return time.Time{}, &ValueError{Message: fmt.Sprintf("%s has no time and no offset, which Python reads as a naive datetime", pyRepr(value))}
	}
	clock := text[10+separator:]
	if len(clock) < 8 || clock[2] != ':' || clock[5] != ':' {
		return time.Time{}, invalid
	}
	hour, okHour := digits(clock[0:2])
	minute, okMinute := digits(clock[3:5])
	second, okSecond := digits(clock[6:8])
	if !okHour || !okMinute || !okSecond {
		return time.Time{}, invalid
	}
	rest := clock[8:]
	microsecond := 0
	if rest != "" && (rest[0] == '.' || rest[0] == ',') {
		end := 1
		for end < len(rest) && rest[end] >= '0' && rest[end] <= '9' {
			end++
		}
		if end == 1 {
			return time.Time{}, invalid
		}
		fraction := (rest[1:end] + "000000")[:6]
		microsecond, _ = digits(fraction)
		rest = rest[end:]
	}
	if rest == "" {
		return time.Time{}, &ValueError{Message: fmt.Sprintf("%s has no offset, which Python reads as a naive datetime", pyRepr(value))}
	}
	offset, ok := zoneOffset(rest)
	if !ok {
		return time.Time{}, invalid
	}
	if hour == 24 && (minute != 0 || second != 0 || microsecond != 0) {
		return time.Time{}, &ValueError{Message: "minute, second, and microsecond must be 0 when hour is 24"}
	}
	if offset <= -86400 || offset >= 86400 {
		return time.Time{}, &ValueError{Message: "offset must be a timedelta strictly between -timedelta(hours=24) and timedelta(hours=24), not " + timedeltaRepr(offset)}
	}
	switch {
	case year < 1 || year > 9999:
		return time.Time{}, &ValueError{Message: fmt.Sprintf("year must be in 1..9999, not %d", year)}
	case month < 1 || month > 12:
		return time.Time{}, &ValueError{Message: fmt.Sprintf("month must be in 1..12, not %d", month)}
	case day < 1 || day > daysIn(year, month):
		return time.Time{}, &ValueError{Message: fmt.Sprintf("day %d must be in range 1..%d for month %d in year %d", day, daysIn(year, month), month, year)}
	case hour > 24:
		return time.Time{}, &ValueError{Message: fmt.Sprintf("hour must be in 0..23, not %d", hour)}
	case minute > 59:
		return time.Time{}, &ValueError{Message: fmt.Sprintf("minute must be in 0..59, not %d", minute)}
	case second > 59:
		return time.Time{}, &ValueError{Message: fmt.Sprintf("second must be in 0..59, not %d", second)}
	}
	if hour == 24 {
		next := time.Date(year, time.Month(month), day+1, 0, 0, 0, 0, time.UTC)
		if next.Year() > 9999 {
			return time.Time{}, &ValueError{Message: fmt.Sprintf("year must be in 1..9999, not %d", next.Year())}
		}
		year, month, day, hour = next.Year(), int(next.Month()), next.Day(), 0
	}
	location := time.UTC
	if offset != 0 {
		location = time.FixedZone("", offset)
	}
	return time.Date(year, time.Month(month), day, hour, minute, second, microsecond*1000, location), nil
}

func digits(text string) (int, bool) {
	for index := 0; index < len(text); index++ {
		if text[index] < '0' || text[index] > '9' {
			return 0, false
		}
	}
	value, err := strconv.Atoi(text)
	return value, err == nil
}

func zoneOffset(text string) (int, bool) {
	if len(text) != 6 && len(text) != 9 || text[0] != '+' && text[0] != '-' || text[3] != ':' || len(text) == 9 && text[6] != ':' {
		return 0, false
	}
	hours, okHours := digits(text[1:3])
	minutes, okMinutes := digits(text[4:6])
	seconds, okSeconds := 0, true
	if len(text) == 9 {
		seconds, okSeconds = digits(text[7:9])
	}
	if !okHours || !okMinutes || !okSeconds {
		return 0, false
	}
	offset := hours*3600 + minutes*60 + seconds
	if text[0] == '-' {
		offset = -offset
	}
	return offset, true
}

func timedeltaRepr(seconds int) string {
	days := seconds / 86400
	if seconds%86400 < 0 {
		days--
	}
	var fields []string
	if days != 0 {
		fields = append(fields, fmt.Sprintf("days=%d", days))
	}
	if rest := seconds - days*86400; rest != 0 {
		fields = append(fields, fmt.Sprintf("seconds=%d", rest))
	}
	return "datetime.timedelta(" + strings.Join(fields, ", ") + ")"
}

func daysIn(year, month int) int {
	return time.Date(year, time.Month(month)+1, 0, 0, 0, 0, 0, time.UTC).Day()
}
