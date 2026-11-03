package owner

import (
	"fmt"
	"regexp"
	"slices"
	"strings"

	"github.com/yasyf/cc-inbox/client"
)

const (
	boardTap  = "Owner on board "
	lineChars = 160
)

var (
	ledgerRow  = regexp.MustCompile(`(?m)^\| (m\d+) \| ([^|]*) \| (.*) \| [^|]* \| (open|responded|addressed|no-order) \|$`)
	orderToken = regexp.MustCompile(`\bowner:(m\d+)\b`)
	sentence   = regexp.MustCompile(`[^.!?\n]+`)
	imperative = regexp.MustCompile(`(?i)^\W*(?:(?:also|and|but|ok|so|now|then|please)\W+)*(?:add|build|bring|change|check|close|consider|cut|delete|design|dig|drop|explain|figure|find|fix|get|give|keep|land|let|make|merge|move|mute|put|raise|redesign|release|remove|restore|run|send|ship|show|spin|start|stop|switch|tell|turn|use|write)\b`)
	obligation = regexp.MustCompile(`(?i)\b(?:need to|should(?:n['’]t)?|must|make sure|let['’]s|don['’]t|do not|never|always)\b`)
	plainCell  = strings.NewReplacer(`\|`, "|", "<br>", " ")
)

type Row struct {
	ID    string
	At    string
	Text  string
	State string
}

type Lane struct {
	Name    string
	Working bool
	Orders  []string
}

func Parse(ledger []byte) []Row {
	var rows []Row
	for _, found := range ledgerRow.FindAllSubmatch(ledger, -1) {
		rows = append(rows, Row{ID: string(found[1]), At: strings.TrimSpace(string(found[2])), Text: plainCell.Replace(string(found[3])), State: string(found[4])})
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

func (r Row) Order() bool {
	return r.State != noOrder && !strings.HasPrefix(r.Text, boardTap) && !question(r.Text)
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
	return fmt.Sprintf("%s %s: %s", r.ID, r.At, text)
}

func Undelivered(rows []Row, lanes []Lane, proofs []client.Record) []Row {
	covered := map[string]bool{}
	carried := map[string][]string{}
	for _, lane := range lanes {
		carried[lane.Name] = lane.Orders
		for _, id := range lane.Orders {
			covered[id] = covered[id] || lane.Working
		}
	}
	for _, proof := range proofs {
		for _, id := range Orders(proof.Text) {
			covered[id] = true
		}
		for _, lane := range append([]string{proof.Lane}, proof.Refs.Lanes...) {
			for _, id := range carried[lane] {
				covered[id] = true
			}
		}
	}
	return slices.DeleteFunc(slices.Clone(rows), func(row Row) bool { return covered[row.ID] || !row.Order() })
}

func Tokens(rows []Row) string {
	ids := make([]string, len(rows))
	for i, row := range rows {
		ids[i] = "owner:" + row.ID
	}
	return strings.Join(ids, ", ")
}
