package alert

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/incident"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	mutedKind   = "hold"
	monitorAttr = "monitor"
	muteAttr    = "mute"
	muteTimeout = time.Minute
)

func MutedQuery() client.Query {
	return client.Query{Lanes: []string{lanes.Root}, Kinds: []string{mutedKind}}
}

func Muted(records []client.Record, monitors []string) (client.Record, bool) {
	for _, record := range slices.Backward(records) {
		attrs := record.Fields.Attrs
		if record.Lane == lanes.Root && string(record.Kind) == mutedKind && attrs[muteAttr] != "" && slices.Contains(monitors, attrs[monitorAttr]) {
			return record, true
		}
	}
	return client.Record{}, false
}

func MutedLine(record client.Record) string {
	attrs := record.Fields.Attrs
	return fmt.Sprintf("the owner muted monitor %s (Datadog downtime %s, cci #%d)", attrs[monitorAttr], attrs[muteAttr], record.Seq)
}

type Mute struct {
	Bus     incident.Bus
	Runner  shell.Runner
	Drive   string
	Monitor string
	Why     string
	Live    []string
	Out     io.Writer
}

func (m Mute) Run(ctx context.Context) error {
	id, err := m.downtime(ctx)
	if err != nil {
		return fmt.Errorf("monitor %s is not muted, and nothing else changed: %w", m.Monitor, err)
	}
	stem := "dd-" + m.Monitor + "-"
	ended := slices.DeleteFunc(slices.Clone(m.Live), func(lane string) bool { return !strings.HasPrefix(lane, stem) })
	muted, err := m.Bus.Post(ctx, client.Draft{
		Drive: m.Drive, Lane: lanes.Root, Kind: mutedKind, To: []string{lanes.OwnerRelay, lanes.Self}, ID: "owner-muted:" + m.Monitor + ":" + id,
		URL: "https://app.datadoghq.com/monitors/" + m.Monitor, LaneRefs: ended, Attrs: map[string]string{monitorAttr: m.Monitor, muteAttr: id},
		Text: fmt.Sprintf("OWNER-MUTED %s: Datadog downtime %s mutes it indefinitely (%s); no lane launches on it again; %s", m.Monitor, id, m.Why, standing(ended)),
	})
	if err != nil {
		return fmt.Errorf("monitor %s is muted by Datadog downtime %s, but its owner-muted record did not post: %w", m.Monitor, id, err)
	}
	for _, lane := range ended {
		if _, err := m.Bus.Post(ctx, client.Draft{Drive: m.Drive, Lane: lanes.Root, Kind: "stand-down", To: []string{lane}, Re: muted.Seq, Force: true, Text: fmt.Sprintf("stand-down %s: the owner muted monitor %s (#%d)", lane, m.Monitor, muted.Seq)}); err != nil {
			return fmt.Errorf("monitor %s is muted (#%d), but the stand-down of %s did not post: %w", m.Monitor, muted.Seq, lane, err)
		}
	}
	_, err = fmt.Fprintf(m.Out, "%s %s\n", muted.Name(), id)
	return err
}

func (m Mute) downtime(ctx context.Context) (string, error) {
	monitor, err := json.Number(m.Monitor).Int64()
	if err != nil {
		return "", fmt.Errorf("monitor %q is not a Datadog monitor id", m.Monitor)
	}
	body, err := json.Marshal(map[string]any{"data": map[string]any{"type": "downtime", "attributes": map[string]any{
		"monitor_identifier": map[string]int64{"monitor_id": monitor}, "scope": "*", "message": m.Why,
	}}})
	if err != nil {
		return "", err
	}
	result, err := m.Runner.Run(ctx, shell.Command{Argv: []string{"pup", "--no-agent", "downtime", "create", "--file", "/dev/stdin", "--output", "json"}, Stdin: string(body), Timeout: muteTimeout})
	if err != nil {
		return "", err
	}
	var created struct {
		Data struct {
			ID string `json:"id"`
		} `json:"data"`
	}
	if err := json.Unmarshal([]byte(result.Stdout), &created); err != nil || created.Data.ID == "" {
		return "", fmt.Errorf("pup downtime create printed no downtime id: %s", strings.TrimSpace(result.Stdout+" "+result.Stderr))
	}
	return created.Data.ID, nil
}

func standing(ended []string) string {
	if len(ended) == 0 {
		return "no live lane works it"
	}
	return "stood down: " + strings.Join(ended, ", ")
}
