package bus

import (
	"cmp"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"maps"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/cardtext"
)

const (
	ownerKind   = "owner"
	OwnerBy     = "owner"
	tappedValue = "tapped"
	heldValue   = "withdrawn"

	WriterAttr     = "writer"
	BuiltAttr      = "built"
	TapRefusedKind = "tap.refused"
	TapRefusedFact = "tap-refused"
	TapPendingKind = "tap.pending"
	GoRefusedKind  = "go.refused"
	GoRefusedFact  = "standing-refused"
	StuckKind      = "landing.stuck"
	StuckFact      = "landing-stuck"
	refusalAttr    = "reason"
	refusedHead    = "head"
	keyedReason    = 8

	holdEndedKind = "note"
	holdUntilAttr = "until"
	holdUntilTap  = "tap"
)

var (
	rowValues      = map[string]string{"approved": tappedValue, "rejected": heldValue, "cleared": heldValue}
	approvalValues = map[string]string{"approved": tappedValue, "rejected": heldValue}
	pickedLabel    = regexp.MustCompile(`picked “([^”]*)”`)
	typedLabel     = regexp.MustCompile(`^Owner: "([^"]*)"`)
	untilHisWord   = regexp.MustCompile(`(?i)\buntil he (?:answers|taps|approves|lands)\b`)
	landLabel      = regexp.MustCompile(`(?i)^\W*(?:yes\W+)?(?:land|merge|approve|ship)\b`)
	holdLabel      = regexp.MustCompile(`(?i)^\W*hold\b`)
	namedPR        = regexp.MustCompile(`([\w./-]*)#(\d+)\b`)
	portOpening    = regexp.MustCompile(`(?i)\b(?:port of |` + regexp.QuoteMeta(cardtext.PortOpener) + `)\[?[\w./-]*#\d+`)
	slackAsk       = regexp.MustCompile(`^slack[-:]`)
	threadCard     = regexp.MustCompile(`^merge:(\d+):([0-9a-fA-F]{7,40})$`)
	threadWords    = regexp.MustCompile(`^Owner's words in the Slack thread of [^:]+:[^:]+:[^:]+: (.*)$`)
	holdWords      = regexp.MustCompile(`(?i)\b(?:hold|close|reject(?:ed)?|not approved|don'?t (?:land|merge|ship)|do not (?:land|merge|ship))\b`)
	tapWords       = regexp.MustCompile(`(?i)\b(?:approved?|land it|merge it|ship it)\b`)
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
	return b.postKeyed(ctx, tap.Kind(), raw, nil, "", namespace+"."+tap.Kind()+"/"+cmp.Or(from.ID, from.Name())+"/"+strconv.Itoa(tap.PR))
}

// PostCardTap records his tap on a card as an owner record, which WriteOrders turns into the land order on the landing host. An owner machine's relay never carries a dotted kind, so a typed order written here would stay here.
func (b Bus) PostCardTap(ctx context.Context, repo string, tap Tap) error {
	return b.postOwnerTap(ctx, repo, tap, fmt.Sprintf("card-tap/%d/%s", tap.PR, tap.Head), "on a card", nil)
}

func (b Bus) PostWordTap(ctx context.Context, repo string, tap Tap, word client.Record, writer string) error {
	return b.postOwnerTap(ctx, repo, tap, fmt.Sprintf("word-tap/%s/%d/%s", cmp.Or(word.ID, word.Name()), tap.PR, tap.Head), fmt.Sprintf("in %s, carried by %s", word.Name(), writer), map[string]string{WriterAttr: writer})
}

func (b Bus) postOwnerTap(ctx context.Context, repo string, tap Tap, id, where string, extra map[string]string) error {
	attrs := map[string]string{"row": strconv.Itoa(tap.PR), "verdict": "approved", "head": tap.Head}
	maps.Copy(attrs, extra)
	_, err := client.Post(ctx, client.Draft{
		Drive: b.Drive, Lane: b.Lane, Kind: ownerKind,
		ID:    id,
		PRs:   []string{fmt.Sprintf("%s#%d", repo, tap.PR)},
		Attrs: attrs,
		Text:  fmt.Sprintf("Owner %s: #%d approved%s", where, tap.PR, At(tap.Head)),
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
		Text:  fmt.Sprintf("tap on #%d%s refused: %s", tap.PR, At(tap.Head), reason),
	})
	return err
}

// PostPending says his tap waits for a record naming the PR's head. It is keyed by the PR, the head his card named and his record, so each pass that reads the tap again writes nothing.
func (b Bus) PostPending(ctx context.Context, repo string, tap Tap, from client.Record) error {
	_, err := client.Post(ctx, client.Draft{
		Drive: b.Drive, Lane: b.Lane, Kind: TapPendingKind,
		ID:    fmt.Sprintf("%s/%d/%s/%s", TapPendingKind, tap.PR, tap.Head, cmp.Or(from.ID, from.Name())),
		PRs:   []string{fmt.Sprintf("%s#%d", repo, tap.PR)},
		Attrs: map[string]string{refusedHead: tap.Head},
		Text:  fmt.Sprintf("tap on #%d%s waits for a record naming its head", tap.PR, At(tap.Head)),
	})
	return err
}

// At names a head as " at <sha>" for a record's text, and nothing when no head is known.
func At(head string) string {
	if head == "" {
		return ""
	}
	return " at " + head[:min(12, len(head))]
}

func RefusedHead(record client.Record) string {
	return record.Fields.Attrs[refusedHead]
}

func (b Bus) PostGoRefused(ctx context.Context, repo string, pr int, head string, to []string, reason string) error {
	sum := sha256.Sum256([]byte(reason))
	_, err := client.Post(ctx, client.Draft{
		Drive: b.Drive, Lane: b.Lane, Kind: GoRefusedKind, To: to,
		ID:    fmt.Sprintf("%s/%d/%s/%s", GoRefusedKind, pr, head, hex.EncodeToString(sum[:keyedReason])),
		PRs:   []string{fmt.Sprintf("%s#%d", repo, pr)},
		Attrs: map[string]string{refusedHead: head, refusalAttr: reason},
		Text:  fmt.Sprintf("no go for #%d at %s: %s", pr, head[:min(12, len(head))], reason),
	})
	return err
}

func (b Bus) PostStuck(ctx context.Context, repo string, pr int, head string, to []string, why string) error {
	attrs, text := map[string]string{refusedHead: head}, fmt.Sprintf("#%d at %s can move again", pr, head[:min(12, len(head))])
	if why != "" {
		attrs[refusalAttr], text = why, fmt.Sprintf("#%d at %s cannot move: %s", pr, head[:min(12, len(head))], why)
	}
	_, err := client.Post(ctx, client.Draft{Drive: b.Drive, Lane: b.Lane, Kind: StuckKind, To: to, PRs: []string{fmt.Sprintf("%s#%d", repo, pr)}, Attrs: attrs, Text: text})
	return err
}

func (b Bus) PostHoldEnded(ctx context.Context, repo string, pr int, held client.Record) error {
	_, err := client.Post(ctx, client.Draft{
		Drive: b.Drive, Lane: b.Lane, Kind: holdEndedKind,
		ID:       fmt.Sprintf("hold-ended/%s", cmp.Or(held.ID, held.Name())),
		PRs:      []string{fmt.Sprintf("%s#%d", repo, pr)},
		Resolves: held.Seq,
		To:       []string{held.Lane},
		Text:     fmt.Sprintf("his tap on #%d ends hold %s", pr, held.Name()),
	})
	return err
}

// OrdersOf reads the land orders one owner record carries: one for a row record, and one per PR its card showed at the head it showed for a pick. An empty Head means the record names no head, so the order lands at the PR's head when it is written.
func OrdersOf(record client.Record, repo string) []Tap {
	if string(record.Kind) != ownerKind {
		return nil
	}
	attrs := record.Fields.Attrs
	if row, err := strconv.Atoi(attrs["row"]); err == nil {
		value, known := rowValues[attrs["verdict"]]
		if !known {
			return nil
		}
		return []Tap{{PR: row, Head: strings.ToLower(attrs["head"]), By: OwnerBy, Value: value}}
	}
	if tap, said := threadTap(attrs["card"], record.Text); said {
		return []Tap{tap}
	}
	value := cardValue(attrs, record.Text)
	if value == "" {
		return nil
	}
	var taps []Tap
	for _, shown := range cardPRs(attrs["heads"], record.Text, repo) {
		taps = append(taps, Tap{PR: shown.PR, Head: shown.Head, By: OwnerBy, Value: value})
	}
	return taps
}

// SaidInCardThread says the record carries his words from a PR card's Slack thread, which withdraw the card's tap when they hold and tap it when they approve.
func SaidInCardThread(record client.Record) bool {
	_, said := threadTap(record.Fields.Attrs["card"], record.Text)
	return string(record.Kind) == ownerKind && said
}

func threadTap(card, text string) (Tap, bool) {
	shown, words := threadCard.FindStringSubmatch(card), threadWords.FindStringSubmatch(text)
	if shown == nil || words == nil {
		return Tap{}, false
	}
	number, _ := strconv.Atoi(shown[1])
	tap := Tap{PR: number, Head: strings.ToLower(shown[2]), By: OwnerBy}
	switch {
	case holdWords.MatchString(words[1]):
		tap.Value = heldValue
	case tapWords.MatchString(words[1]):
		tap.Value = tappedValue
	default:
		return Tap{}, false
	}
	return tap, true
}

// PostWordHold holds the PR at the head his card showed until his next tap, keyed by his words so a second pass writes nothing.
func (b Bus) PostWordHold(ctx context.Context, repo string, tap Tap, from client.Record) error {
	_, err := client.Post(ctx, client.Draft{
		Drive: b.Drive, Lane: b.Lane, Kind: "hold",
		ID:    fmt.Sprintf("word-hold/%s/%d", cmp.Or(from.ID, from.Name()), tap.PR),
		PRs:   []string{fmt.Sprintf("%s#%d", repo, tap.PR)},
		Attrs: map[string]string{holdUntilAttr: holdUntilTap, refusedHead: tap.Head},
		Text:  fmt.Sprintf("hold #%d%s until he taps again: his words in its card's thread (%s) withdraw his tap", tap.PR, At(tap.Head), from.Name()),
	})
	return err
}

// UnreadPick says a pick was recorded with option ids and no label, so no one can tell a land pick from any other. A Slack permission card asks whether to post, so its pick never lands a PR.
func UnreadPick(record client.Record) bool {
	attrs := record.Fields.Attrs
	return string(record.Kind) == ownerKind && attrs["event"] == "choice.selected" && attrs["picked"] != "" && !pickedLabel.MatchString(record.Text) && !slackAsk.MatchString(attrs["block"])
}

// UnnamedLand says a pick reads as a land or hold order and its card names PRs, but not one, so no order can come from it. A card naming no PR asks a question, never for a land.
func UnnamedLand(record client.Record, repo string) bool {
	attrs := record.Fields.Attrs
	if string(record.Kind) != ownerKind || attrs["event"] != "choice.selected" || cardValue(attrs, record.Text) == "" {
		return false
	}
	if len(OrdersOf(record, repo)) > 0 {
		return false
	}
	return attrs["heads"] != "" || len(namedPRs(record.Text, repo)) > 0
}

// CarriedEach says a later record orders every PR of repo a pick names, with the order the pick gives, as when the relay carries a pick on several PRs one PR at a time.
func CarriedEach(pick client.Record, later []client.Record, repo string) bool {
	value, named := cardValue(pick.Fields.Attrs, pick.Text), pickedPRs(pick, repo)
	return len(named) > 0 && !slices.ContainsFunc(named, func(number int) bool {
		return !slices.ContainsFunc(later, func(record client.Record) bool {
			return slices.ContainsFunc(OrdersOf(record, repo), func(tap Tap) bool { return tap.PR == number && tap.Value == value })
		})
	})
}

func pickedPRs(pick client.Record, repo string) []int {
	heads := pick.Fields.Attrs["heads"]
	if heads == "" {
		return namedPRs(pick.Text, repo)
	}
	var numbers []int
	for entry := range strings.SplitSeq(heads, ",") {
		pr, _, _ := strings.Cut(entry, "=")
		owner, digits, _ := strings.Cut(pr, "#")
		if number, err := strconv.Atoi(digits); err == nil && (repo == "" || strings.EqualFold(owner, repo)) {
			numbers = append(numbers, number)
		}
	}
	return numbers
}

func Built(record client.Record) (repo, branch string, built bool) {
	attrs := record.Fields.Attrs
	if string(record.Kind) != ownerKind || attrs["event"] != "choice.selected" || cardValue(attrs, record.Text) != "" {
		return "", "", false
	}
	repo, branch, built = strings.Cut(attrs[BuiltAttr], ":")
	return repo, branch, built && repo != "" && branch != ""
}

func PickedHead(record client.Record, repo string, number int) string {
	for entry := range strings.SplitSeq(record.Fields.Attrs["heads"], ",") {
		pr, head, _ := strings.Cut(entry, "=")
		owner, digits, _ := strings.Cut(pr, "#")
		if strings.EqualFold(owner, repo) && digits == strconv.Itoa(number) {
			return strings.ToLower(head)
		}
	}
	return ""
}

func ReadsAsLand(label string) bool {
	return landLabel.MatchString(label)
}

func cardValue(attrs map[string]string, text string) string {
	switch attrs["event"] {
	case "":
		if said := typedLabel.FindStringSubmatch(text); said != nil && landLabel.MatchString(said[1]) {
			return tappedValue
		}
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

func cardPRs(heads, text, repo string) []Tap {
	if heads == "" {
		if numbers := namedPRs(text, repo); len(numbers) == 1 {
			return []Tap{{PR: numbers[0]}}
		}
		return nil
	}
	var shown []Tap
	for entry := range strings.SplitSeq(heads, ",") {
		pr, head, _ := strings.Cut(entry, "=")
		owner, digits, _ := strings.Cut(pr, "#")
		number, err := strconv.Atoi(digits)
		if err != nil || repo != "" && !strings.EqualFold(owner, repo) {
			return nil
		}
		shown = append(shown, Tap{PR: number, Head: strings.ToLower(head)})
	}
	return shown
}

func namedPRs(text, repo string) []int {
	var numbers []int
	for _, found := range namedPR.FindAllStringSubmatch(portOpening.ReplaceAllString(text, ""), -1) {
		number, err := strconv.Atoi(found[2])
		if err != nil || found[1] != "" && !strings.EqualFold(found[1], repo) || slices.Contains(numbers, number) {
			continue
		}
		numbers = append(numbers, number)
	}
	return numbers
}
