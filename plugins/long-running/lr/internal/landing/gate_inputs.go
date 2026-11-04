package landing

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"path/filepath"
	"slices"
	"sync"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/board"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
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
	Lanes    []laneRow           `json:"lanes"`
	FixLanes []string            `json:"fix_lanes"`
	Clocks   []string            `json:"clocks"`
	Session  string              `json:"session"`
}

type laneRow struct {
	Name      string          `json:"name"`
	State     store.LaneState `json:"state"`
	RetiredBy string          `json:"retired_by,omitempty"`
	PRs       []int           `json:"prs,omitempty"`
}

type InputsPublisher struct {
	Runner     shell.Runner
	Store      *store.Store
	FixLane    func(lane string) (bool, error)
	LiveClocks func() ([]string, error)
	Session    string
	Config     string
	Now        func() time.Time

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
	all, err := p.Store.AllLanes()
	if err != nil {
		return err
	}
	now := p.Now()
	var fixes []string
	for _, lane := range all {
		if lane.State == store.LaneRetired && now.Sub(lane.Since) > incidentHorizon {
			continue
		}
		fix, err := p.FixLane(lane.Name)
		if err != nil {
			return err
		}
		if fix {
			fixes = append(fixes, lane.Name)
		}
	}
	clocks, err := p.LiveClocks()
	if err != nil {
		return err
	}
	cards, taps := ownerWaits(live)
	mirrored := mirroredLanes(all, fixes)
	encoded, err := json.Marshal(gateInputs{Cards: cards, Taps: taps, TapRules: landing.TapRules, Exempt: landing.ExemptGrades, GoLanes: cfg.GoLaneNames(), Lanes: mirrored, FixLanes: fixes, Clocks: clocks, Session: p.Session})
	if err != nil {
		return err
	}
	if string(encoded) == p.posted && now.Sub(p.at) < inputsRepost {
		return nil
	}
	text := fmt.Sprintf("What landing reads off the lanes machine: %d open cards, %d open tap rows, %d mirrored lanes, %d fix lanes, %d live incident clocks", len(cards), len(taps), len(mirrored), len(fixes), len(clocks))
	if _, err := client.Post(ctx, client.Draft{Drive: cfg.Drive, Lane: self, Kind: "note", Topic: gateInputsTopic, ID: fmt.Sprintf("%s:%d", gateInputsTopic, now.UnixMilli()), To: []string{gateInputsReader}, Text: text, Attrs: map[string]string{gateInputsAttr: string(encoded)}}); err != nil {
		return err
	}
	p.posted, p.at = string(encoded), now
	return nil
}

func mirroredLanes(all []store.Lane, fixes []string) []laneRow {
	named := make(map[string]store.Lane, len(all))
	for _, lane := range all {
		named[lane.Name] = lane
	}
	var rows []laneRow
	for _, lane := range all {
		last, seen := lane, map[string]bool{lane.Name: true}
		for last.State == store.LaneRetired && !seen[last.RetiredBy] {
			next, found := named[last.RetiredBy]
			if !found {
				break
			}
			last, seen[next.Name] = next, true
		}
		if last.State != store.LaneRetired || slices.Contains(fixes, lane.Name) {
			rows = append(rows, laneRow{Name: lane.Name, State: lane.State, RetiredBy: lane.RetiredBy, PRs: lane.PRs})
		}
	}
	return rows
}

type InputsMirror struct {
	Drive     string
	Store     *store.Store
	CopyLanes bool
	Now       func() time.Time

	mu     sync.Mutex
	inputs gateInputs
	err    error
}

func (m *InputsMirror) Pass(ctx context.Context) error {
	inputs, err := readGateInputs(ctx, m.Drive, m.Now())
	if err == nil && m.CopyLanes {
		rows := make([]store.Lane, len(inputs.Lanes))
		for i, lane := range inputs.Lanes {
			rows[i] = store.Lane{Name: lane.Name, State: lane.State, RetiredBy: lane.RetiredBy, PRs: lane.PRs}
		}
		err = m.Store.PutLanes(rows, m.Now())
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	m.inputs, m.err = inputs, err
	return err
}

func (m *InputsMirror) FixLane(lane string) (bool, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	return slices.Contains(m.inputs.FixLanes, lane), m.err
}

func (m *InputsMirror) LiveClocks() ([]string, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.inputs.Clocks, m.err
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
			slog.Warn("gate inputs record does not parse, so the gate reads the one before it", "record", records[i].Seq, "error", err)
			continue
		}
		return inputs, nil
	}
	return gateInputs{}, fmt.Errorf("the supervisor has posted no %s record in %s, so the board's open cards and tap rows are unread and nothing is gated", gateInputsTopic, inputsStale)
}
