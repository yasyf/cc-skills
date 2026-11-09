package lanes

import (
	"context"
	"fmt"
	"regexp"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	classOwnedWithin = 10 * time.Minute
	classHorizon     = time.Hour
	classEvery       = time.Minute
)

var (
	classLine   = regexp.MustCompile(`^(?:NEW )?CLASS(?:[^\w-]|$)`)
	classTag    = regexp.MustCompile(`^\[[^\]\n]+\]`)
	incidentTag = regexp.MustCompile(`\b([A-Z]{2,})(?:-([0-9A-Z]*\d[0-9A-Z]*)|(\d[0-9A-Z]*))\b`)
	fixOrder    = regexp.MustCompile(`(?i)\bfix\b`)
	orderKinds  = []string{"go", "decide", "decision"}
)

type ClassUnowned struct {
	Env   Env
	Store *store.Store

	read time.Time
}

func (c *ClassUnowned) Check(ctx context.Context, now time.Time) error {
	if now.Sub(c.read) < classEvery {
		return nil
	}
	c.read = now
	records, err := c.Env.Bus.Tail(ctx, client.Query{Drive: c.Env.Config.Drive, Since: now.Add(-classHorizon)})
	if err != nil {
		return fmt.Errorf("%w: cci tail: %w", ErrUnread, err)
	}
	closed := map[int64]bool{}
	for _, record := range records {
		closed[record.Resolves] = true
		for _, seq := range record.AlsoResolves {
			closed[seq] = true
		}
	}
	for _, record := range records {
		if Stem(record.Lane) == Root || closed[record.Seq] || (string(record.Kind) != "incident" && !classLine.MatchString(record.Text)) {
			continue
		}
		keys := classKeys(record)
		if len(keys) == 0 || now.Sub(record.At) < classOwnedWithin || fixOrdered(records, record, keys) {
			continue
		}
		if err := c.raise(ctx, record, keys, now); err != nil {
			return err
		}
	}
	return nil
}

func (c *ClassUnowned) raise(ctx context.Context, record client.Record, keys []string, now time.Time) error {
	first, err := c.Store.Escalate("class-unowned:"+keys[0], "class-unowned", Root, now)
	if err != nil || !first {
		return err
	}
	text := fmt.Sprintf("CLASS-UNOWNED %s %s: %s, %d min with no fix-lane GO naming %s | coordinator: GO a fix lane for this class", keys[0], record.Lane, record.Name(), int(now.Sub(record.At).Minutes()), strings.Join(keys, ", "))
	if _, err := c.Env.Bus.Post(ctx, client.Draft{Drive: c.Env.Config.Drive, Lane: Self, Kind: blockerKind, To: []string{Root}, ID: "class-unowned:" + keys[0], Text: text}); err != nil {
		return fmt.Errorf("%w: cci post: %w", ErrUnread, err)
	}
	return nil
}

func fixOrdered(records []client.Record, class client.Record, keys []string) bool {
	if fixLaneOf(class.Lane, keys) {
		return true
	}
	return slices.ContainsFunc(records, func(record client.Record) bool {
		if spawned, found := strings.CutPrefix(record.Text, "SPAWNED "); found && record.Lane == Self && string(record.Kind) == "state" {
			lane, _, _ := strings.Cut(spawned, " ")
			return fixLaneOf(lane, keys)
		}
		return (Stem(record.Lane) == Root || record.Lane == Self) && record.Seq > class.Seq && slices.Contains(orderKinds, string(record.Kind)) && fixOrder.MatchString(record.Text) &&
			slices.ContainsFunc(incidentKeys(record.Topic, record.Text), func(key string) bool { return slices.Contains(keys, key) })
	})
}

func fixLaneOf(lane string, keys []string) bool {
	slug, found := strings.CutSuffix(lane, "-fix")
	return found && slices.Contains(keys, "topic:"+slug)
}

func classKeys(record client.Record) []string {
	first, _, _ := strings.Cut(record.Text, "\n")
	header := classTag.FindString(record.Text)
	if classLine.MatchString(first) {
		header = first
	}
	return incidentKeys(record.Topic, header)
}

func incidentKeys(topic, text string) []string {
	var keys []string
	if topic != "" {
		keys = append(keys, "topic:"+topic)
	}
	for _, found := range incidentTag.FindAllStringSubmatch(topic+" "+text, -1) {
		keys = append(keys, "tag:"+found[1]+found[2]+found[3])
	}
	slices.Sort(keys)
	return slices.Compact(keys)
}
