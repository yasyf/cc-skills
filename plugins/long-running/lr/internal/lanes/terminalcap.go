package lanes

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/terminalcap"
)

const (
	terminalCapEvery   = 5 * time.Minute
	terminalCapSubject = "terminal-cap"
	worktreeRunChars   = 8

	terminalsLive         = "under lanes lr reads live"
	terminalsRetired      = "retired"
	terminalsHeld         = "held"
	terminalsUnregistered = "unregistered"
	terminalsOutside      = "outside the drive"
)

var terminalCategories = []string{terminalsLive, terminalsRetired, terminalsHeld, terminalsUnregistered, terminalsOutside}

type TerminalCap struct {
	Env    Env
	Store  *store.Store
	Home   string
	Prefix string
	read   time.Time
}

func newTerminalCap(e Env, s *store.Store) (*TerminalCap, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return nil, err
	}
	data, err := os.ReadFile(filepath.Join(e.State, "runner.json"))
	if errors.Is(err, fs.ErrNotExist) {
		return &TerminalCap{Env: e, Store: s, Home: home}, nil
	}
	if err != nil {
		return nil, err
	}
	var runner struct {
		Orca struct {
			LaunchEnv map[string]string `json:"launch_env"`
		} `json:"orca"`
	}
	if err := json.Unmarshal(data, &runner); err != nil {
		return nil, fmt.Errorf("runner.json: %w", err)
	}
	return &TerminalCap{Env: e, Store: s, Home: home, Prefix: runner.Orca.LaunchEnv["ORCA_LAUNCH_PREFIX"]}, nil
}

func (c *TerminalCap) Check(ctx context.Context, now time.Time) error {
	if !c.read.IsZero() && now.Sub(c.read) < terminalCapEvery {
		return nil
	}
	c.read = now
	reading, err := terminalcap.Read(ctx, c.Env.Runner, c.Home)
	if err != nil {
		return fmt.Errorf("%w: %w", ErrUnread, err)
	}
	if !reading.Near() {
		return c.Store.ClearEscalation(terminalCapSubject)
	}
	meta, err := MetaDesk(c.Store)
	if err != nil {
		return err
	}
	first, err := c.Store.Escalate(terminalCapSubject, terminalCapSubject, meta, now)
	if err != nil || !first {
		return err
	}
	registered, err := c.Store.AllLanes()
	if err != nil {
		return err
	}
	text := fmt.Sprintf("TERMINAL-CAP %d of %d Orca terminals on this host: %s. At %d a local launch fails at once with Orca's message, and a helper with no need of this Mac takes a Sprite. Closing a terminal needs the owner's word.", len(reading.Terminals), reading.Cap, c.counted(reading, registered), reading.Cap)
	if _, err := c.Env.Bus.Post(ctx, client.Draft{Drive: c.Env.Config.Drive, Lane: Self, Kind: blockerKind, To: []string{meta}, Topic: terminalCapSubject, Text: text}); err != nil {
		return fmt.Errorf("%w: cci post: %w", ErrUnread, err)
	}
	return nil
}

func (c *TerminalCap) counted(reading terminalcap.Reading, registered []store.Lane) string {
	states := make(map[string]store.LaneState, len(registered))
	for _, lane := range registered {
		states[lane.Name] = lane.State
	}
	run := strings.TrimPrefix(c.Env.Config.OrcaRun, "run_")
	run = run[:min(len(run), worktreeRunChars)]
	suffix := regexp.MustCompile("-" + regexp.QuoteMeta(run) + `-\d+$`)
	counts := make(map[string]int, len(terminalCategories))
	for _, terminal := range reading.Terminals {
		counts[terminalCategory(filepath.Base(terminal.Worktree), c.Prefix, suffix, states)]++
	}
	said := make([]string, 0, len(terminalCategories))
	for _, category := range terminalCategories {
		if counts[category] > 0 {
			said = append(said, fmt.Sprintf("%d %s", counts[category], category))
		}
	}
	return strings.Join(said, ", ")
}

func terminalCategory(worktree, prefix string, suffix *regexp.Regexp, states map[string]store.LaneState) string {
	if !strings.HasPrefix(worktree, prefix) || !suffix.MatchString(worktree) {
		return terminalsOutside
	}
	state, found := states[strings.TrimPrefix(suffix.ReplaceAllString(worktree, ""), prefix)]
	switch {
	case !found:
		return terminalsUnregistered
	case state == store.LaneDesired:
		return terminalsLive
	case state == store.LaneHeld:
		return terminalsHeld
	}
	return terminalsRetired
}
