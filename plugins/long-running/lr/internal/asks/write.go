package asks

import (
	"context"
	"encoding/json"
	"fmt"
	"maps"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/board"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	StandingDefault = 24
	stateReopen     = "open"
)

var (
	askKey         = regexp.MustCompile(`^(?:ask/)?(\d+)$`)
	dashboardWords = regexp.MustCompile(`(?i)\bdashboard\b|\b(?:overview|needs (?:you|me)|review|releases|workers|timeline|asks?|chat)\s+(?:tab|page)\b`)
	OverrideOf     = []string{"backlog", "live", "lost", stateReopen}
)

type NewAsk struct {
	Text     string
	Lane     string
	Accept   string
	Due      string
	Standing int
	Surface  string
}

func surfaceOf(ask NewAsk) string {
	if ask.Surface == "" && dashboardWords.MatchString(ask.Text) {
		return Dashboard
	}
	return ask.Surface
}

func Key(value string) (string, error) {
	found := askKey.FindStringSubmatch(value)
	if found == nil {
		return "", fmt.Errorf("%q is not an ask id; give one as lr asks add printed it, like ask/000104, or its number", value)
	}
	number, err := strconv.Atoi(found[1])
	if err != nil {
		return "", err
	}
	return fmt.Sprintf("%s%06d", Prefix, number), nil
}

func NextKey(rows map[string]map[string]string) string {
	highest := 0
	for key := range rows {
		if number, err := strconv.Atoi(strings.TrimPrefix(key, Prefix)); err == nil && strings.HasPrefix(key, Prefix) {
			highest = max(highest, number)
		}
	}
	return fmt.Sprintf("%s%06d", Prefix, highest+1)
}

func (m Mirror) set(ctx context.Context, key string, fields map[string]string) (map[string]string, error) {
	if err := m.Source.SetRow(ctx, m.Ledger, key, fields); err != nil {
		return nil, err
	}
	merged, _, err := m.Store.LedgerRow(m.Ledger, key)
	if err != nil {
		return nil, err
	}
	if merged == nil {
		merged = map[string]string{}
	}
	maps.Copy(merged, fields)
	return merged, m.Store.PutLedgerRow(m.Ledger, key, merged, m.Now())
}

func (m Mirror) existing(ctx context.Context, key string) (map[string]string, error) {
	fields, found, err := m.Store.LedgerRow(m.Ledger, key)
	if err != nil {
		return nil, err
	}
	if found {
		return fields, nil
	}
	if _, err := m.Sync(ctx); err != nil {
		return nil, err
	}
	fields, found, err = m.Store.LedgerRow(m.Ledger, key)
	if err != nil {
		return nil, err
	}
	if !found {
		return nil, fmt.Errorf("no ask %s in %s", key, m.Ledger)
	}
	return fields, nil
}

func normalized(text string) string {
	return strings.Join(strings.Fields(strings.ToLower(text)), " ")
}

func (m Mirror) Add(ctx context.Context, ask NewAsk, refuses func(ctx context.Context, lane string) (string, error)) (line string, heldBy string, err error) {
	if _, err := m.Sync(ctx); err != nil {
		return "", "", err
	}
	rows, err := m.Store.LedgerRows(m.Ledger)
	if err != nil {
		return "", "", err
	}
	for _, key := range slices.Sorted(maps.Keys(rows)) {
		fields := rows[key]
		if strings.HasPrefix(key, Prefix) && fields["dropped_at"] == "" && fields["answered_at"] == "" && normalized(fields["text"]) == normalized(ask.Text) {
			if _, err := m.Progress(ctx, key, "asked again for "+ask.Lane, nil); err != nil {
				return "", "", err
			}
			return "", key, nil
		}
	}
	why, err := refuses(ctx, ask.Lane)
	if err != nil {
		return "", "", err
	}
	if why != "" {
		return "", "", fmt.Errorf("ask refused: %s", why)
	}
	due, err := board.ParseEnd("at:"+strings.TrimSpace(ask.Due), m.Now())
	if err != nil {
		return "", "", err
	}
	key := NextKey(rows)
	fields := map[string]string{"text": ask.Text, "lane": ask.Lane, "accept": ask.Accept, "due": due.Value, "asked_at": store.Stamp(m.Now())}
	if ask.Standing > 0 {
		fields["standing_hours"] = strconv.Itoa(ask.Standing)
	}
	if surface := surfaceOf(ask); surface != "" {
		fields["surface"] = surface
	}
	if _, err := m.set(ctx, key, fields); err != nil {
		return "", "", err
	}
	return Ask{Key: key, Lane: ask.Lane, Text: ask.Text, StandingHours: ask.Standing}.Line(), "", nil
}

func (m Mirror) SetState(ctx context.Context, key, state, note string) error {
	if !slices.Contains(OverrideOf, state) {
		return fmt.Errorf("%q is not an ask state; give one of %s", state, strings.Join(OverrideOf, ", "))
	}
	if _, err := m.existing(ctx, key); err != nil {
		return err
	}
	stored := state
	if state == stateReopen {
		stored = ""
	}
	_, err := m.set(ctx, key, map[string]string{"state": stored, "state_note": note})
	return err
}

func (m Mirror) SetStanding(ctx context.Context, key string, hours int) error {
	if _, err := m.existing(ctx, key); err != nil {
		return err
	}
	_, err := m.set(ctx, key, map[string]string{"standing_hours": strconv.Itoa(hours)})
	return err
}

func (m Mirror) SetSurface(ctx context.Context, key, surface string) error {
	if _, err := m.existing(ctx, key); err != nil {
		return err
	}
	_, err := m.set(ctx, key, map[string]string{"surface": surface})
	return err
}

func (m Mirror) Progress(ctx context.Context, key, note string, artifacts []string) (string, error) {
	fields, err := m.existing(ctx, key)
	if err != nil {
		return "", err
	}
	sequence := 1
	for name := range fields {
		if number, err := strconv.Atoi(strings.TrimPrefix(name, progressPrefix)); err == nil && strings.HasPrefix(name, progressPrefix) {
			sequence = max(sequence, number+1)
		}
	}
	if artifacts == nil {
		artifacts = []string{}
	}
	encoded, err := json.Marshal(Progress{At: m.Now().UTC().Truncate(time.Second), Note: note, Artifacts: artifacts})
	if err != nil {
		return "", err
	}
	name := fmt.Sprintf("%s%06d", progressPrefix, sequence)
	if _, err := m.set(ctx, key, map[string]string{name: string(encoded)}); err != nil {
		return "", err
	}
	return strings.Join(append([]string{note}, artifacts...), " "), nil
}

func (m Mirror) Drop(ctx context.Context, key, reason string) error {
	if _, err := m.existing(ctx, key); err != nil {
		return err
	}
	_, err := m.set(ctx, key, map[string]string{"dropped_at": store.Stamp(m.Now()), "dropped_reason": reason})
	return err
}

func (m Mirror) Answer(ctx context.Context, key, text string) error {
	if _, err := m.existing(ctx, key); err != nil {
		return err
	}
	_, err := m.set(ctx, key, map[string]string{"answered_at": store.Stamp(m.Now()), "answer": text})
	return err
}

func (m Mirror) Live(ctx context.Context, at, text string) error {
	_, err := m.set(ctx, liveKey, map[string]string{"at": at, "text": text})
	return err
}
