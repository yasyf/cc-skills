package incident

import (
	"context"
	"regexp"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
)

const (
	kindAsk      = "ask"
	kindAnswer   = "answer"
	kindDecision = "decision"
)

var postedTS = regexp.MustCompile(`\bts=(\d+\.\d+)`)

type Bus interface {
	Post(ctx context.Context, d client.Draft) (client.Record, error)
	Tail(ctx context.Context, q client.Query) ([]client.Record, error)
}

type CCI struct{}

func (CCI) Post(ctx context.Context, d client.Draft) (client.Record, error) {
	return client.Post(ctx, d)
}

func (CCI) Tail(ctx context.Context, q client.Query) ([]client.Record, error) {
	return client.Tail(ctx, q)
}

type CommsBus struct {
	Bus       Bus
	Drive     string
	Sender    string
	CommsLane string
	RootLane  string
	Topic     string
	OpenedAt  time.Time
}

func (c CommsBus) post(ctx context.Context, kind, text, topic string, to ...string) (int64, error) {
	stored, err := c.Bus.Post(ctx, client.Draft{Drive: c.Drive, Lane: c.Sender, Kind: kind, Text: text, Topic: topic, To: to})
	return stored.Seq, err
}

func eventPrefix(event string) (string, error) {
	encoded, err := encode(event)
	if err != nil {
		return "", err
	}
	return `{"event":` + encoded + `,`, nil
}

func eventText(event, grant string, facts map[string]any) (string, error) {
	prefix, err := eventPrefix(event)
	if err != nil {
		return "", err
	}
	rest, err := encode(facts)
	if err != nil {
		return "", err
	}
	encodedGrant, err := encode(grant)
	if err != nil {
		return "", err
	}
	if len(facts) == 0 {
		return prefix + `"grant":` + encodedGrant + `}`, nil
	}
	return prefix + `"grant":` + encodedGrant + `,` + rest[1:], nil
}

func (c CommsBus) Event(ctx context.Context, event string, facts map[string]any, grant string) (int64, error) {
	text, err := eventText(event, grant, facts)
	if err != nil {
		return 0, err
	}
	return c.post(ctx, kindAsk, text, c.Topic, c.CommsLane)
}

func (c CommsBus) Find(ctx context.Context, event string) (int64, error) {
	prefix, err := eventPrefix(event)
	if err != nil {
		return 0, err
	}
	records, err := c.Bus.Tail(ctx, client.Query{Drive: c.Drive, Since: c.OpenedAt, Kinds: []string{kindAsk}})
	if err != nil {
		return 0, err
	}
	for _, record := range records {
		if record.Lane == c.Sender && lanes.Addresses(record.To, c.CommsLane) && strings.HasPrefix(record.Text, prefix) {
			return record.Seq, nil
		}
	}
	return 0, nil
}

func (c CommsBus) Posted(ctx context.Context, seq int64) (string, error) {
	records, err := c.Bus.Tail(ctx, client.Query{Drive: c.Drive, After: seq, Kinds: []string{kindAnswer}})
	if err != nil {
		return "", err
	}
	for _, record := range records {
		if record.Re != seq {
			continue
		}
		if match := postedTS.FindStringSubmatch(record.Text); match != nil {
			return match[1], nil
		}
	}
	return "", nil
}

func (c CommsBus) AskRoot(ctx context.Context, text string) error {
	_, err := c.post(ctx, kindAsk, text, c.Topic, c.RootLane)
	return err
}

func (c CommsBus) Report(ctx context.Context, text string) error {
	_, err := c.post(ctx, kindDecision, text, c.Topic, c.RootLane)
	return err
}
