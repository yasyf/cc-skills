package owner

import (
	"cmp"
	"fmt"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
)

const (
	boardTap      = "Owner on board "
	lineChars     = 160
	deliveredAttr = "delivered"
	retractedAttr = "undelivered"
	Open          = "open"
	Late          = "late"
	InProgress    = "in progress"
	Parked        = "parked"
	Working       = "working"
	orderVerbs    = "add|build|bring|change|check|close|consider|cut|delete|design|dig|drop|explain|figure|find|fix|get|give|keep|land|let|make|merge|move|mute|put|raise|redesign|release|remove|restore|run|send|ship|show|spin|start|stop|switch|tell|turn|use|write"
)

var (
	ledgerRow  = regexp.MustCompile(`(?m)^\| (m\d+(?:\.\d+)?) \| ([^|]*) \| (.*) \| (\d*) \| (open|responded|addressed|no-order|due [^|]*) \|$`)
	orderToken = regexp.MustCompile(`\bowner:(m\d+(?:\.\d+)?)\b`)
	sentence   = regexp.MustCompile(`[^.!?\n]+`)
	imperative = regexp.MustCompile(`(?i)^\W*(?:(?:also|and|but|ok|so|now|then|please)\W+)*(?:` + orderVerbs + `)\b`)
	obligation = regexp.MustCompile(`(?i)\b(?:need to|should(?:n['’]t)?|must|make sure|let['’]s|don['’]t|do not|never|always)\b`)
	plainCell  = strings.NewReplacer(`\|`, "|", "<br>", " ")
	prInOrder  = regexp.MustCompile(`(?:#|/pull/)(\d{3,})|\b(\d{5})\b`)
	holdLine   = regexp.MustCompile(`(?i)\b(?:on hold|behind (?:a|the|its) hold|held (?:at|behind|by|for|on)|parked|blocked on|waiting on|left (?:\w+ ){0,3}open|leaves? (?:\w+ ){0,3}open|still open|not (?:yet )?built)\b`)
)

type Row struct {
	ID     string `json:"id"`
	At     string `json:"at"`
	Text   string `json:"text"`
	Routed int64  `json:"routed,omitempty"`
	State  string `json:"state"`
	Answer string `json:"answer,omitempty"`
	Status string `json:"status"`
	Lane   string `json:"lane"`
	Hold   string `json:"hold"`
}

type Lane struct {
	Name    string
	Working bool
	Orders  []string
}

func Parse(ledger []byte) []Row {
	matches := ledgerRow.FindAllSubmatch(ledger, -1)
	rows := make([]Row, 0, len(matches))
	for _, found := range matches {
		routed, _ := strconv.ParseInt(string(found[4]), 10, 64)
		rows = append(rows, Row{ID: string(found[1]), At: strings.TrimSpace(string(found[2])), Text: plainCell.Replace(string(found[3])), Routed: routed, State: string(found[5])})
	}
	return rows
}

func Orders(text string) []string {
	var ids []string
	for _, found := range orderToken.FindAllStringSubmatch(text, -1) {
		if !slices.Contains(ids, found[1]) {
			ids = append(ids, found[1])
		}
	}
	return ids
}

func PRsNamed(orders []Row) (map[int]bool, error) {
	named := map[int]bool{}
	for _, order := range orders {
		for _, found := range prInOrder.FindAllStringSubmatch(order.Text, -1) {
			number, err := strconv.Atoi(found[1] + found[2])
			if err != nil {
				return nil, err
			}
			named[number] = true
		}
	}
	return named, nil
}

func (r Row) Order() bool {
	return r.State != noOrder && !strings.HasPrefix(r.Text, boardTap) && !question(r.Text)
}

func (r Row) Owed() bool {
	return r.Order() || r.State == opened && question(r.Text)
}

func question(text string) bool {
	text = strings.TrimSpace(text)
	if !strings.HasSuffix(text, "?") || obligation.MatchString(text) {
		return false
	}
	return !slices.ContainsFunc(sentence.FindAllString(text, -1), imperative.MatchString)
}

func (r Row) Line() string {
	text := r.Text
	if runes := []rune(text); len(runes) > lineChars {
		text = strings.TrimSpace(string(runes[:lineChars])) + "…"
	}
	if r.Answer != "" {
		text += " [newest answer: " + r.Answer + "]"
	}
	switch r.Status {
	case Late:
		return fmt.Sprintf("%s %s: %s [PAST %s]", r.ID, r.At, text, strings.ToUpper(r.State))
	case Parked:
		return fmt.Sprintf("%s %s: %s [parked: %s; last lane %s]", r.ID, r.At, text, r.Hold, client.Stem(r.Lane))
	case InProgress:
		return fmt.Sprintf("%s %s: %s [in progress, last lane %s, no delivered proof]", r.ID, r.At, text, client.Stem(r.Lane))
	}
	return fmt.Sprintf("%s %s: %s", r.ID, r.At, text)
}

func DeliveredBy(record client.Record) (delivered, retracted []string) {
	return Orders(record.Fields.Attrs[deliveredAttr]), Orders(record.Fields.Attrs[retractedAttr])
}

func Undelivered(rows []Row, lanes []Lane, proofs []client.Record) []Row {
	working, delivered, claimed := map[string]bool{}, map[string]bool{}, map[string]bool{}
	carried, carriers := map[string][]string{}, map[string][]string{}
	for _, lane := range lanes {
		carried[lane.Name] = lane.Orders
		for _, id := range lane.Orders {
			working[id] = working[id] || lane.Working
			carriers[id] = append(carriers[id], lane.Name)
		}
	}
	latest, said := map[string]client.Record{}, map[string][]client.Record{}
	for _, proof := range proofs {
		if last, seen := latest[proof.Lane]; !seen || proof.Seq > last.Seq {
			latest[proof.Lane] = proof
		}
		credited, retracted := DeliveredBy(proof)
		for _, id := range credited {
			delivered[id] = true
		}
		for _, id := range retracted {
			delete(delivered, id)
		}
		for _, id := range Orders(proof.Text) {
			claimed[id] = true
			said[id] = append(said[id], proof)
		}
		if proof.Kind != "landed" && proof.Kind != "done" {
			continue
		}
		for _, lane := range append([]string{proof.Lane}, proof.Refs.Lanes...) {
			for _, id := range carried[lane] {
				claimed[id] = true
			}
		}
	}
	var open []Row
	for _, row := range rows {
		if delivered[row.ID] || working[row.ID] || !row.Owed() {
			continue
		}
		row.Status = Open
		if claimed[row.ID] {
			row.Status = InProgress
		}
		reports := slices.Clone(said[row.ID])
		for _, lane := range carriers[row.ID] {
			row.Lane = cmp.Or(row.Lane, lane)
			if report, seen := latest[lane]; seen {
				reports = append(reports, report)
			}
		}
		slices.SortStableFunc(reports, func(a, b client.Record) int { return cmp.Compare(a.Seq, b.Seq) })
		holder := ""
		for _, report := range reports {
			row.Lane = report.Lane
			if hold := holdOf(report.Text); hold != "" {
				holder, row.Hold = report.Lane, hold
			}
		}
		if holder != "" {
			row.Status, row.Lane = Parked, holder
		}
		open = append(open, row)
	}
	return open
}

// Carried is each owed order a live lane or open PR holds, which Undelivered
// leaves off because someone is on it.
func Carried(rows []Row, lanes []Lane) []Row {
	holder := map[string]string{}
	for _, lane := range lanes {
		if !lane.Working {
			continue
		}
		for _, id := range lane.Orders {
			holder[id] = cmp.Or(holder[id], lane.Name)
		}
	}
	var carried []Row
	for _, row := range rows {
		if lane := holder[row.ID]; lane != "" && row.Owed() {
			row.Status, row.Lane = Working, lane
			carried = append(carried, row)
		}
	}
	return carried
}

func holdOf(text string) string {
	for _, said := range sentence.FindAllString(text, -1) {
		if holdLine.MatchString(said) {
			if runes := []rune(strings.TrimSpace(said)); len(runes) > lineChars {
				return string(runes[:lineChars]) + "…"
			}
			return strings.TrimSpace(said)
		}
	}
	return ""
}

// PastDue returns the rows whose promised clock has passed, oldest promise first, whatever a lane or a record says about them.
func PastDue(rows []Row, now time.Time) []Row {
	var late []Row
	for _, row := range rows {
		if at, promised := row.dueAt(now); promised && !at.After(now) {
			row.Status = Late
			late = append(late, row)
		}
	}
	return late
}

func (r Row) dueAt(now time.Time) (time.Time, bool) {
	clock, promised := strings.CutPrefix(r.State, duePrefix)
	if !promised {
		return time.Time{}, false
	}
	at, err := time.ParseInLocation(rowTime, clock, pacific)
	if err != nil {
		return time.Time{}, false
	}
	return time.Date(now.In(pacific).Year(), at.Month(), at.Day(), at.Hour(), at.Minute(), 0, 0, pacific), true
}

func Answers(rows []Row, records []client.Record) ([]Row, error) {
	for i, row := range rows {
		if !row.Due() || row.Routed == 0 {
			continue
		}
		answer, _, err := row.answer(records)
		if err != nil {
			return nil, err
		}
		if answer.Seq != 0 {
			rows[i].Answer = fmt.Sprintf("%s from %s, `cci show %d --body`", answer.Kind, answer.Lane, answer.Seq)
		}
	}
	return rows, nil
}

func (r Row) Due() bool {
	return strings.HasPrefix(r.State, duePrefix)
}

func Unread(row Row, records []client.Record) (client.Record, bool, error) {
	answer, read, err := row.answer(records)
	return answer, answer.Seq != 0 && !read, err
}

func (r Row) answer(records []client.Record) (answer client.Record, read bool, err error) {
	prs, err := PRsNamed([]Row{r})
	if err != nil {
		return client.Record{}, false, err
	}
	for _, record := range records {
		if record.Seq <= r.Routed || !r.about(record, prs) {
			continue
		}
		if record.Lane == communicator {
			read = answer.Seq != 0
			continue
		}
		if slices.Contains(record.To, communicator) {
			answer, read = record, false
		}
	}
	return answer, read, nil
}

func (r Row) about(record client.Record, prs map[int]bool) bool {
	if r.Routed != 0 && record.Re == r.Routed || record.Fields.Attrs["row"] == r.ID || slices.Contains(Orders(record.Text), r.ID) {
		return true
	}
	for _, pr := range record.Refs.PRs {
		if prs[pr.Number] {
			return true
		}
	}
	return false
}

func FirstRouted(rows []Row) int64 {
	var first int64
	for _, row := range rows {
		if row.Routed != 0 && (first == 0 || row.Routed < first) {
			first = row.Routed
		}
	}
	return first
}

func Only(rows []Row, status string) []Row {
	return slices.DeleteFunc(slices.Clone(rows), func(row Row) bool { return row.Status != status })
}

func Tokens(rows []Row) string {
	ids := make([]string, len(rows))
	for i, row := range rows {
		ids[i] = "owner:" + row.ID
	}
	return strings.Join(ids, ", ")
}
