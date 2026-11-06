package findings

import (
	"fmt"
	"maps"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/owner"
)

const (
	stateOpen    = "open"
	stateRouted  = "routed"
	stateFixed   = "fixed"
	stateClosed  = "closed"
	kindFinding  = "finding"
	kindPick     = "pick"
	coordinator  = "root"
	boardWatcher = "board-watch"
)

var (
	findingWords   = regexp.MustCompile(`(?s)\[finding:([\w.-]+)\]\s*(.*)`)
	canonicalWords = regexp.MustCompile(`(?s)^(?:#(\d+)|-)\s+(\S+)\s+(\S+)\s+owner\s+([\w.-]+):\s*(.*)$`)
	leadingPR      = regexp.MustCompile(`^#(\d+)\b|\bpr#(\d+)\b`)
	ownerWord      = regexp.MustCompile(`\bowner\s+([a-z][\w.-]*)`)
	pickWords      = regexp.MustCompile(`(?s)^Owner on board \w+, (.*?) \(([\w.-]+)\): (?:picked|wrote) “([^”]*)”?`)
	nameWord       = regexp.MustCompile(`\w[\w.-]*`)
	seqWord        = regexp.MustCompile(`#\d+\b`)
	notOwners      = []string{coordinator, "main", "all"}
)

type Fields = map[string]string

type Rows = map[string]Fields

// Names resolves what a record can call a pick: Cards maps a box id to its
// card, and Boxes maps an owner ledger row to the box its pick came from.
type Names struct {
	Cards map[string]string
	Boxes map[string]string
}

func BoxOfOrder(row owner.Row) string {
	if found := pickWords.FindStringSubmatch(row.Text); found != nil {
		return found[2]
	}
	return ""
}

type settler struct {
	rows    Rows
	names   Names
	named   map[string][]string
	boxes   map[string][]string
	held    map[string]string
	changed map[string]bool
}

// settle folds records, oldest first, into rows in one pass. A row is looked up
// by the names a record carries, so the cost is the records' length and never
// records times rows. Only a record past cursor opens a row, so reading an older
// window settles without reopening.
func settle(rows Rows, records []client.Record, cursor int64, names Names) settler {
	s := indexed(rows, names)
	for _, record := range records {
		if record.Seq > cursor {
			s.open(record)
		}
		s.move(record)
	}
	return s
}

func (s settler) keys() []string {
	return slices.Sorted(maps.Keys(s.changed))
}

func (s settler) build(prs []PR) {
	for _, pr := range prs {
		text := pr.Branch + " " + pr.Body
		keys := s.words(text)
		for _, word := range nameWord.FindAllString(text, -1) {
			keys = append(keys, s.named["#"+strings.TrimRight(word, ".-")]...)
		}
		for _, key := range keys {
			if row := s.rows[key]; row["kind"] == kindPick && live(row) && pr.MergedAt.After(atOf(row)) {
				s.set(key, row, with(row, stateFixed, fmt.Sprintf("%s#%d", pr.Repo, pr.Number)))
			}
		}
	}
}

func (s settler) words(text string) []string {
	var keys []string
	for _, word := range nameWord.FindAllString(text, -1) {
		keys = append(keys, s.named[strings.TrimRight(word, ".-")]...)
	}
	for _, word := range seqWord.FindAllString(text, -1) {
		keys = append(keys, s.named[word]...)
	}
	return keys
}

func indexed(rows Rows, names Names) settler {
	s := settler{rows, names, map[string][]string{}, map[string][]string{}, map[string]string{}, map[string]bool{}}
	for key, row := range rows {
		s.index(key, row)
	}
	return s
}

// cited returns the picks some record names after them, of any kind and from
// anyone but the lane that watches the board. picks maps each seq to its box.
func cited(picks map[int64]string, records []client.Record, names Names) []int64 {
	rows := make(Rows, len(picks))
	for seq, box := range picks {
		rows[strconv.FormatInt(seq, 10)] = Fields{"seq": strconv.FormatInt(seq, 10), "card": box, "state": stateOpen}
	}
	s := indexed(rows, names)
	for key := range rows {
		s.named[key] = append(s.named[key], key)
	}
	found := map[int64]bool{}
	for _, record := range records {
		if client.Stem(record.Lane) == boardWatcher {
			continue
		}
		for _, key := range s.touched(record) {
			if seq := seqOf(rows[key]); record.Seq > seq {
				found[seq] = true
			}
		}
	}
	return slices.Sorted(maps.Keys(found))
}

func (s settler) index(key string, row Fields) {
	s.named["#"+row["seq"]] = append(s.named["#"+row["seq"]], key)
	if row["pr"] != "" {
		s.named["#"+row["pr"]] = append(s.named["#"+row["pr"]], key)
	}
	box := row["card"]
	if box == "" {
		return
	}
	s.named[box] = append(s.named[box], key)
	if card := s.names.Cards[box]; card != "" && card != box {
		s.named[card] = append(s.named[card], key)
	}
	s.boxes[box] = append(s.boxes[box], key)
	if live(row) {
		s.held[box] = key
	}
}

func (s settler) open(record client.Record) {
	key, row, ok := rowOf(record)
	if !ok {
		return
	}
	if _, known := s.rows[key]; known {
		return
	}
	if held, ok := s.held[row["card"]]; ok && row["kind"] == kindPick && live(s.rows[held]) {
		key = held
	}
	s.rows[key] = row
	s.changed[key] = true
	s.index(key, row)
}

func (s settler) move(record client.Record) {
	for _, key := range s.touched(record) {
		row := s.rows[key]
		if !live(row) || record.Seq <= seqOf(row) {
			continue
		}
		s.set(key, row, step(row, record))
	}
	delivered, retracted := owner.DeliveredBy(record)
	for _, id := range delivered {
		for _, key := range s.boxes[s.names.Boxes[id]] {
			if row := s.rows[key]; live(row) && record.Seq > seqOf(row) {
				s.set(key, row, with(row, stateFixed, strconv.FormatInt(record.Seq, 10)))
			}
		}
	}
	for _, id := range retracted {
		for _, key := range s.boxes[s.names.Boxes[id]] {
			if row := s.rows[key]; row["state"] == stateFixed {
				s.set(key, row, with(row, stateOpen, ""))
			}
		}
	}
}

func (s settler) set(key string, was, row Fields) {
	if maps.Equal(was, row) {
		return
	}
	s.rows[key] = row
	s.changed[key] = true
}

func (s settler) touched(record client.Record) []string {
	keys := slices.Concat(s.words(record.Text), s.words(record.Topic))
	for _, pr := range record.Refs.PRs {
		keys = append(keys, s.named["#"+strconv.Itoa(pr.Number)]...)
	}
	for _, seq := range append(record.Closes(), record.Re) {
		keys = append(keys, s.named["#"+strconv.FormatInt(seq, 10)]...)
	}
	return keys
}

func step(row Fields, record client.Record) Fields {
	by := strconv.FormatInt(record.Seq, 10)
	seq := seqOf(row)
	switch {
	case fixes(row["kind"], record):
		return with(row, stateFixed, by)
	case string(record.Kind) == "opened":
		moved := with(row, stateRouted, by)
		moved["owner"] = record.Lane
		return moved
	case row["state"] == stateOpen && (record.Re == seq || slices.Contains(record.Closes(), seq)):
		return with(row, stateRouted, by)
	}
	return row
}

// fixes says whether a record naming a row ends it. A pick also ends on a
// release, which ships it, on a claim, which names who holds it, and on the
// coordinator's decide, which assigns it; another lane's decide only relays or
// asks about the pick.
func fixes(kind string, record client.Record) bool {
	switch string(record.Kind) {
	case "head", "landed":
		return true
	case "release", "claim":
		return kind == kindPick
	case "decide":
		return kind == kindPick && client.Stem(record.Lane) == coordinator
	}
	return false
}

func with(row Fields, state, by string) Fields {
	moved := maps.Clone(row)
	moved["state"] = state
	moved["by"] = by
	return moved
}

func live(row Fields) bool {
	return row["state"] == stateOpen || row["state"] == stateRouted
}

func atOf(row Fields) time.Time {
	at, err := time.Parse(time.RFC3339Nano, row["at"])
	if err != nil {
		panic("findings: row at " + row["at"])
	}
	return at
}

func seqOf(row Fields) int64 {
	seq, err := strconv.ParseInt(row["seq"], 10, 64)
	if err != nil {
		panic("findings: row seq " + row["seq"])
	}
	return seq
}

func rowOf(record client.Record) (string, Fields, bool) {
	base := Fields{"seq": strconv.FormatInt(record.Seq, 10), "at": record.At.UTC().Format(time.RFC3339Nano), "state": stateOpen, "by": ""}
	switch string(record.Kind) {
	case "defect":
		found := findingWords.FindStringSubmatch(record.Text)
		if found == nil {
			return "", nil, false
		}
		maps.Copy(base, Fields{"kind": kindFinding, "sweep": found[1], "card": ""})
		maps.Copy(base, findingSaid(record, strings.TrimSpace(found[2])))
		return "finding/" + base["seq"], base, true
	case "owner":
		found := pickWords.FindStringSubmatch(record.Text)
		if found == nil {
			return "", nil, false
		}
		maps.Copy(base, Fields{"kind": kindPick, "sweep": "owner", "pr": "", "line": "", "verdict": "pick", "owner": coordinator, "text": found[1] + ": " + found[3], "card": found[2]})
		return "pick/" + base["seq"], base, true
	}
	return "", nil, false
}

func findingSaid(record client.Record, rest string) Fields {
	if found := canonicalWords.FindStringSubmatch(rest); found != nil {
		line := found[2]
		if line == "-" {
			line = ""
		}
		return Fields{"pr": found[1], "line": line, "verdict": found[3], "owner": found[4], "text": strings.TrimSpace(found[5])}
	}
	said := Fields{"pr": "", "line": "", "verdict": "", "owner": coordinator, "text": rest}
	if len(record.Refs.PRs) > 0 {
		said["pr"] = strconv.Itoa(record.Refs.PRs[0].Number)
	} else if found := leadingPR.FindStringSubmatch(rest); found != nil {
		said["pr"] = found[1] + found[2]
	}
	if found := ownerWord.FindStringSubmatch(rest); found != nil {
		said["owner"] = found[1]
	} else if i := slices.IndexFunc(record.To, func(lane string) bool { return !slices.Contains(notOwners, lane) }); i >= 0 {
		said["owner"] = record.To[i]
	}
	return said
}
