package bus

import (
	"cmp"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
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

	TapRefusedKind = "tap.refused"
	TapRefusedFact = "tap-refused"
	refusalAttr    = "reason"
	refusedHead    = "head"
	keyedReason    = 8
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

// PostCardTap records his tap on a card as an owner record, which WriteOrders turns into the land order on the landing host. An owner machine's relay never carries a dotted kind, so a typed order written here would stay here.
func (b Bus) PostCardTap(ctx context.Context, repo string, tap Tap) error {
	return b.postOwnerTap(ctx, repo, tap, fmt.Sprintf("card-tap/%d/%s", tap.PR, tap.Head), "on a card")
}

func (b Bus) PostWordTap(ctx context.Context, repo string, tap Tap, word client.Record) error {
	return b.postOwnerTap(ctx, repo, tap, "word-tap/"+cmp.Or(word.ID, word.Name()), "in "+word.Name())
}

func (b Bus) postOwnerTap(ctx context.Context, repo string, tap Tap, id, where string) error {
	_, err := client.Post(ctx, client.Draft{
		Drive: b.Drive, Lane: b.Lane, Kind: ownerKind,
		ID:    id,
		PRs:   []string{fmt.Sprintf("%s#%d", repo, tap.PR)},
		Attrs: map[string]string{"row": strconv.Itoa(tap.PR), "verdict": "approved", "head": tap.Head},
		Text:  fmt.Sprintf("Owner %s: #%d approved at %s", where, tap.PR, tap.Head[:min(12, len(tap.Head))]),
	})
	return err
}

func (b Bus) PostRefusal(ctx context.Context, repo string, tap Tap, reason string) error {
	sum := sha256.Sum256([]byte(reason))
	_, err := client.Post(ctx, client.Draft{
		Drive: b.Drive, Lane: b.Lane, Kind: TapRefusedKind,
		ID:    fmt.Sprintf("%s/%d/%s/%s", TapRefusedKind, tap.PR, tap.Head, hex.EncodeToString(sum[:keyedReason])),
		PRs:   []string{fmt.Sprintf("%s#%d", repo, tap.PR)},
		Attrs: map[string]string{refusedHead: tap.Head, refusalAttr: reason},
		Text:  fmt.Sprintf("tap on #%d at %s refused: %s", tap.PR, tap.Head[:min(12, len(tap.Head))], reason),
	})
	return err
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

// UnreadPick says a pick was recorded with option ids and no label because its board could not be read, so no one can tell a land pick from any other.
func UnreadPick(record client.Record) bool {
	attrs := record.Fields.Attrs
	return string(record.Kind) == ownerKind && attrs["event"] == "choice.selected" && attrs["picked"] != "" && !pickedLabel.MatchString(record.Text)
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
