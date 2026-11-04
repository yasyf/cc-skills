package landing

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"path/filepath"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/board"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	gateInputsTopic  = "landing-gate-inputs"
	gateInputsAttr   = "inputs"
	gateInputsReader = "desk-runner"
	InputsEvery      = time.Minute
	inputsRepost     = 5 * time.Minute
	inputsStale      = 15 * time.Minute
)

type gateInputs struct {
	Cards    []gates.Card        `json:"cards"`
	Taps     []gates.TapRow      `json:"taps"`
	TapRules map[string][]string `json:"tap_rules"`
	Exempt   []string            `json:"exempt"`
	GoLanes  []string            `json:"go_lanes"`
}

type InputsPublisher struct {
	Runner  shell.Runner
	Session string
	Config  string
	Now     func() time.Time

	posted string
	at     time.Time
}

func (p *InputsPublisher) Pass(ctx context.Context) error {
	outcomes, err := board.ReadOutcomes(ctx, p.Runner, p.Session)
	if err != nil {
		return err
	}
	live, err := board.LiveOf(outcomes, nil)
	if err != nil {
		return err
	}
	cfg, err := config.Load(p.Config, io.Discard)
	if err != nil {
		return err
	}
	landing, err := landingSection(filepath.Join(filepath.Dir(p.Config), runnerFile))
	if err != nil {
		return err
	}
	cards, taps := ownerWaits(live)
	encoded, err := json.Marshal(gateInputs{Cards: cards, Taps: taps, TapRules: landing.TapRules, Exempt: landing.ExemptGrades, GoLanes: cfg.GoLaneNames()})
	if err != nil {
		return err
	}
	now := p.Now()
	if string(encoded) == p.posted && now.Sub(p.at) < inputsRepost {
		return nil
	}
	text := fmt.Sprintf("What lr landing go reads off this board: %d open cards, %d open tap rows", len(cards), len(taps))
	if _, err := client.Post(ctx, client.Draft{Drive: cfg.Drive, Lane: self, Kind: "note", Topic: gateInputsTopic, To: []string{gateInputsReader}, Text: text, Attrs: map[string]string{gateInputsAttr: string(encoded)}}); err != nil {
		return err
	}
	p.posted, p.at = string(encoded), now
	return nil
}

func readGateInputs(ctx context.Context, drive string, now time.Time) (gateInputs, error) {
	records, err := client.Tail(ctx, client.Query{Drive: drive, Since: now.Add(-inputsStale), Kinds: []string{"note"}, Lanes: []string{self}})
	if err != nil {
		return gateInputs{}, err
	}
	for i := len(records) - 1; i >= 0; i-- {
		if records[i].Topic != gateInputsTopic {
			continue
		}
		var inputs gateInputs
		if err := json.Unmarshal([]byte(records[i].Fields.Attrs[gateInputsAttr]), &inputs); err != nil {
			return gateInputs{}, fmt.Errorf("gate inputs #%d: %w", records[i].Seq, err)
		}
		return inputs, nil
	}
	return gateInputs{}, fmt.Errorf("the supervisor has posted no %s record in %s, so the board's open cards and tap rows are unread and nothing is gated", gateInputsTopic, inputsStale)
}
