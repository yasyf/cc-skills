package bus

import (
	"cmp"
	"context"
	"encoding/json"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
)

const (
	ownerKind   = "owner"
	OwnerBy     = "owner"
	tappedValue = "tapped"
	heldValue   = "withdrawn"
)

var (
	rowValues      = map[string]string{"approved": tappedValue, "rejected": heldValue, "cleared": heldValue}
	approvalValues = map[string]string{"approved": tappedValue, "rejected": heldValue}
	pickedLabel    = regexp.MustCompile(`picked “([^”]*)”`)
	landLabel      = regexp.MustCompile(`(?i)^\W*(?:yes\W+)?(?:land|merge|approve|ship)\b`)
	holdLabel      = regexp.MustCompile(`(?i)^\W*hold\b`)
	namedPR        = regexp.MustCompile(`([\w./-]*)#(\d+)\b`)
)

func (b Bus) ReadOwner(ctx context.Context, since int64, floor time.Time) ([]client.Record, error) {
	query := client.Query{Drive: b.Drive, After: since, Kinds: []string{ownerKind}}
	if since == 0 {
		query.Since = floor
	}
	return client.Tail(ctx, query)
}

// PostOrder keys the order by the owner record it carries out, so a tap undone and given again at one head is a new order, and a second machine reading the same record writes nothing.
func (b Bus) PostOrder(ctx context.Context, tap Tap, from client.Record) (Record, error) {
	raw, err := json.Marshal(tap)
	if err != nil {
		return Record{}, err
	}
	return b.postKeyed(ctx, tap.Kind(), raw, nil, "", namespace+"."+tap.Kind()+"/"+cmp.Or(from.ID, from.Name()))
}

// OrderOf reads the land order one owner record carries. An empty Head means the record names no head, so the order lands at the PR's head when it is written.
func OrderOf(record client.Record, repo string) (Tap, bool) {
	if string(record.Kind) != ownerKind {
		return Tap{}, false
	}
	attrs := record.Fields.Attrs
	if row, err := strconv.Atoi(attrs["row"]); err == nil {
		value, known := rowValues[attrs["verdict"]]
		return Tap{PR: row, Head: strings.ToLower(attrs["head"]), By: OwnerBy, Value: value}, known
	}
	value := cardValue(attrs, record.Text)
	if value == "" {
		return Tap{}, false
	}
	number, head, named := cardPR(attrs["heads"], record.Text, repo)
	return Tap{PR: number, Head: head, By: OwnerBy, Value: value}, named
}

func cardValue(attrs map[string]string, text string) string {
	switch attrs["event"] {
	case "decision.created":
		return approvalValues[attrs["verdict"]]
	case "choice.selected":
		said := pickedLabel.FindStringSubmatch(text)
		switch {
		case said == nil:
			return ""
		case landLabel.MatchString(said[1]):
			return tappedValue
		case holdLabel.MatchString(said[1]):
			return heldValue
		}
	}
	return ""
}

func cardPR(heads, text, repo string) (int, string, bool) {
	if heads != "" {
		pr, head, _ := strings.Cut(heads, "=")
		owner, digits, _ := strings.Cut(pr, "#")
		number, err := strconv.Atoi(digits)
		return number, strings.ToLower(head), err == nil && !strings.Contains(heads, ",") && (repo == "" || strings.EqualFold(owner, repo))
	}
	var numbers []int
	for _, found := range namedPR.FindAllStringSubmatch(text, -1) {
		number, err := strconv.Atoi(found[2])
		if err != nil || found[1] != "" && !strings.EqualFold(found[1], repo) || slices.Contains(numbers, number) {
			continue
		}
		numbers = append(numbers, number)
	}
	if len(numbers) != 1 {
		return 0, "", false
	}
	return numbers[0], "", true
}
