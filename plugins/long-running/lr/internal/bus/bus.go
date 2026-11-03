package bus

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
)

type Body interface {
	Kind() string
}

type Head struct {
	PR   int    `json:"pr"`
	Head string `json:"head"`
	Lane string `json:"lane"`
}

type Grade struct {
	PR      int    `json:"pr"`
	Head    string `json:"head"`
	Grader  string `json:"grader"`
	Verdict string `json:"verdict"`
}

type Tap struct {
	PR        int    `json:"pr"`
	Head      string `json:"head"`
	By        string `json:"by"`
	Value     string `json:"value"`
	Rule      string `json:"rule,omitempty"`
	Condition string `json:"condition,omitempty"`
}

type Verdict struct {
	PR      int    `json:"pr"`
	Head    string `json:"head"`
	Verdict string `json:"verdict"`
}

type Item struct {
	Item     int64  `json:"item"`
	Action   string `json:"action"`
	PR       int    `json:"pr"`
	Head     string `json:"head"`
	Mover    string `json:"mover"`
	Deadline string `json:"deadline"`
}

type Done struct {
	Item int64 `json:"item"`
}

type Escalation struct {
	Subject string `json:"subject"`
	Cause   string `json:"cause"`
}

type Decision struct {
	Text string `json:"text"`
}

type Ask struct {
	Text string `json:"text"`
}

type Answer struct {
	Re   int64  `json:"re"`
	Text string `json:"text"`
}

type Note struct {
	Text string `json:"text"`
}

type State struct {
	Lane string `json:"lane"`
	PR   int    `json:"pr,omitempty"`
	Head string `json:"head,omitempty"`
	Text string `json:"text,omitempty"`
}

func (Head) Kind() string       { return "head" }
func (Grade) Kind() string      { return "grade" }
func (Tap) Kind() string        { return "tap" }
func (Verdict) Kind() string    { return "verdict" }
func (Item) Kind() string       { return "item" }
func (Done) Kind() string       { return "done" }
func (Escalation) Kind() string { return "escalation" }
func (Decision) Kind() string   { return "decision" }
func (Ask) Kind() string        { return "ask" }
func (Answer) Kind() string     { return "answer" }
func (Note) Kind() string       { return "note" }
func (State) Kind() string      { return "state" }

func decodeBody(kind string, raw json.RawMessage) (Body, error) {
	switch kind {
	case "head":
		return decodeAs[Head](raw)
	case "grade":
		return decodeAs[Grade](raw)
	case "tap":
		return decodeAs[Tap](raw)
	case "verdict":
		return decodeAs[Verdict](raw)
	case "item":
		return decodeAs[Item](raw)
	case "done":
		return decodeAs[Done](raw)
	case "escalation":
		return decodeAs[Escalation](raw)
	case "decision":
		return decodeAs[Decision](raw)
	case "ask":
		return decodeAs[Ask](raw)
	case "answer":
		return decodeAs[Answer](raw)
	case "note":
		return decodeAs[Note](raw)
	case "state":
		return decodeAs[State](raw)
	}
	return nil, fmt.Errorf("no body type for kind %s", kind)
}

func decodeAs[B Body](raw json.RawMessage) (Body, error) {
	var body B
	if err := json.Unmarshal(raw, &body); err != nil {
		return nil, err
	}
	return body, nil
}

type Record struct {
	Seq  int64
	At   time.Time
	Lane string
	To   []string
	Body Body
}

func (r Record) Kind() string {
	return r.Body.Kind()
}

type Bus struct {
	Drive string
	Lane  string
}

func (b Bus) Post(ctx context.Context, to []string, body Body) (Record, error) {
	raw, err := json.Marshal(body)
	if err != nil {
		return Record{}, err
	}
	fields, err := decodeFields(raw)
	if err != nil {
		return Record{}, err
	}
	return b.PostFields(ctx, body.Kind(), fields, to)
}

func (b Bus) PostFields(ctx context.Context, kind string, fields map[string]any, to []string) (Record, error) {
	if err := Check(kind, fields, to); err != nil {
		return Record{}, err
	}
	s, _ := lookup(kind)
	raw, err := json.Marshal(fields)
	if err != nil {
		return Record{}, err
	}
	draft := client.Draft{Drive: b.Drive, Lane: b.Lane, Kind: namespace + "." + kind, Text: summary(s, fields), To: to, Body: raw}
	if len(s.identity) > 0 {
		draft.ID = identity(s, fields)
	}
	if pr, ok := fields["pr"]; ok {
		draft.PRs = []string{plain(pr)}
	}
	if re, ok := fields["re"].(json.Number); ok {
		if draft.Re, err = re.Int64(); err != nil {
			return Record{}, err
		}
	}
	stored, err := client.Post(ctx, draft)
	if err != nil {
		return Record{}, err
	}
	return parse(stored)
}

func decodeFields(raw json.RawMessage) (map[string]any, error) {
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	var fields map[string]any
	if err := dec.Decode(&fields); err != nil {
		return nil, err
	}
	return fields, nil
}

func parse(raw client.Record) (Record, error) {
	kind := strings.TrimPrefix(string(raw.Kind), namespace+".")
	fields, err := decodeFields(raw.Body)
	if err != nil {
		return Record{}, err
	}
	if err := Check(kind, fields, raw.To); err != nil {
		return Record{}, err
	}
	body, err := decodeBody(kind, raw.Body)
	if err != nil {
		return Record{}, err
	}
	return Record{raw.Seq, raw.At.UTC(), raw.Lane, raw.To, body}, nil
}

func (b Bus) Read(ctx context.Context, since int64, kinds ...string) (records []Record, rejected []client.Record, err error) {
	if len(kinds) == 0 {
		for _, s := range table {
			kinds = append(kinds, s.kind)
		}
	}
	selected := make([]string, 0, len(kinds))
	for _, kind := range kinds {
		selected = append(selected, namespace+"."+kind)
	}
	rows, err := client.Tail(ctx, client.Query{Drive: b.Drive, After: since, Kinds: selected})
	if err != nil {
		return nil, nil, err
	}
	for _, row := range rows {
		record, err := parse(row)
		if err != nil {
			rejected = append(rejected, row)
			continue
		}
		records = append(records, record)
	}
	return records, rejected, nil
}
