package findings

import (
	"cmp"
	"context"
	"errors"
	"fmt"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
)

const (
	StillUnmetAttr = "still-unmet"
	pickPass       = 15 * time.Minute
	sendWindow     = 14 * 24 * time.Hour
	sendTopic      = "send:"
	sendToAttr     = "send-to"
	ownerKind      = "owner"
	askKind        = "ask"
	answerKind     = "answer"
	quotedChars    = 160
	clockAndDay    = "3:04 PM Jan 2"
)

func StillUnmet(record client.Record) int64 {
	seq, err := strconv.ParseInt(strings.TrimPrefix(record.Fields.Attrs[StillUnmetAttr], "#"), 10, 64)
	if err != nil {
		return 0
	}
	return seq
}

func UnmetPost(lane, seq string) string {
	return fmt.Sprintf(`cci post --lane %s --kind %s --re %s --attr %s=%s "<what it asks for that is still undone, and that its text or change is still right>"`, lane, answerKind, seq, StillUnmetAttr, seq)
}

func (b Book) Unmet(ctx context.Context, seq int64) (string, error) {
	now := b.Now()
	named, err := b.Bus.Among(ctx, b.Entry.Bus(), []int64{seq})
	if err != nil {
		return "", err
	}
	if len(named) == 0 {
		return "", fmt.Errorf("findings: no record #%d", seq)
	}
	word := named[0]
	if string(word.Kind) != ownerKind {
		return "", fmt.Errorf("findings: #%d is a %s record, not an owner record holding his word", seq, word.Kind)
	}
	if now.Sub(word.At) < pickPass {
		return "", nil
	}
	row, err := b.rowOf(ctx, seq)
	if err != nil {
		return "", err
	}
	owner := cmp.Or(row["owner"], routedTo(word), coordinator)
	answers, err := b.Bus.Tail(ctx, client.Query{Drive: b.Entry.Bus(), After: seq, Since: now.Add(-pickPass)})
	if err != nil {
		return "", err
	}
	if slices.ContainsFunc(answers, func(record client.Record) bool { return StillUnmet(record) == seq && speaksFor(owner, record.Lane) }) {
		return "", nil
	}
	return refusal(word, row, owner)
}

func (b Book) Clears(ctx context.Context, seq int64) error {
	refused, err := b.Unmet(ctx, seq)
	if err != nil || refused == "" {
		return err
	}
	return errors.New(refused)
}

func (b Book) SendUnmet(ctx context.Context, to string) (string, error) {
	asks, err := b.Bus.Tail(ctx, client.Query{Drive: b.Entry.Bus(), Since: b.Now().Add(-sendWindow), Kinds: []string{askKind}, Lanes: []string{boardWatcher}})
	if err != nil {
		return "", err
	}
	for _, ask := range slices.Backward(asks) {
		if strings.HasPrefix(ask.Topic, sendTopic) && ask.Fields.Attrs[sendToAttr] == to {
			return b.Unmet(ctx, ask.Re)
		}
	}
	return "", nil
}

func (b Book) rowOf(ctx context.Context, seq int64) (Fields, error) {
	state, err := b.load()
	if err != nil || state.Ledger == "" {
		return nil, err
	}
	rows, err := b.rows(ctx, state.Ledger)
	if err != nil {
		return nil, err
	}
	for _, row := range rows {
		if seqOf(row) == seq {
			return row, nil
		}
	}
	return nil, nil
}

func refusal(word client.Record, row Fields, owner string) (string, error) {
	pacific, err := time.LoadLocation("America/Los_Angeles")
	if err != nil {
		return "", err
	}
	said := strings.Join(strings.Fields(word.Text), " ")
	if runes := []rune(said); len(runes) > quotedChars {
		said = string(runes[:quotedChars-1]) + "…"
	}
	ledger, closing := "", ""
	if row != nil {
		closing = fmt.Sprintf("; if it is done or moot, close it with `lr findings close --why <reason> %d`", word.Seq)
		if !live(row) {
			ledger = fmt.Sprintf(" The findings ledger has it %s by %s.", row["state"], orNone(row["by"]))
		}
	}
	return fmt.Sprintf("REFUSED #%d: his word in it, %q, is from %s, past the pass that read it, so nothing acts under it now.%s If what it asks for is still undone and its text or change is still right, %s posts `%s` and this runs again%s.",
		word.Seq, said, word.At.In(pacific).Format(clockAndDay), ledger, owner, UnmetPost(owner, strconv.FormatInt(word.Seq, 10)), closing), nil
}

func orNone(word string) string {
	return cmp.Or(word, "none")
}
