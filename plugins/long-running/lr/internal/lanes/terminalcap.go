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
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/terminalcap"
)

const (
	terminalCapEvery   = 5 * time.Minute
	terminalCapSubject = "terminal-cap"
	terminalCapCard    = "terminal-cap"
	cardWait           = 4 * time.Minute
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
	data, err := drive.ReadMacOnly(filepath.Join(e.State, "runner.json"))
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
	meta, err := MetaDesk(c.Store)
	if err != nil {
		return err
	}
	if !reading.Near() {
		return c.withdraw(ctx, reading, meta)
	}
	first, err := c.Store.Escalate(terminalCapSubject, terminalCapSubject, meta, now)
	if err != nil || !first {
		return err
	}
	registered, err := c.Store.AllLanes()
	if err != nil {
		return err
	}
	counted := c.counted(reading, registered)
	card, err := c.askOwner(ctx, meta, "--id", terminalCapCard, "--until", "at:1d", "--nothing-to-build", "the change is a setting on his Mac, not code",
		"--title", "Orca is near its terminal limit on your Mac: how should it get room?",
		"--background", fmt.Sprintf("Orca runs %d of the %d terminals it allows on your Mac.\n\nThey are %s. At the limit Orca refuses every new terminal on the Mac, so lr starts each new lane on a Sprite instead, incident lanes with their fast models included, and a lane whose brief says it needs the Mac fails. Orca's limit is half the Mac's terminal-device limit, and this Mac has no saved Orca setting for lr to edit.", len(reading.Terminals), reading.Cap, counted),
		"--option", "Raise the limit in Orca's settings :: You set a higher terminal limit in Orca's settings window. Lanes start on the Mac again at once, and nothing restarts.",
		"--option", "Raise the Mac's terminal-device limit :: A cc-sudo run with your Touch ID doubles it, which lets Orca allow 400 terminals once Orca restarts; the restart waits for your word.",
		"--option", "Close finished lanes' terminals :: install-desk closes the terminals of lanes proven finished, one at a time, never root, a desk or a terminal whose owner is unknown, and posts the counts before and after.",
	)
	if err != nil {
		card = "no card drawn: " + err.Error()
	}
	text := fmt.Sprintf("TERMINAL-CAP %d of %d Orca terminals on this host: %s. At %d a local launch Orca refuses relaunches once on a Sprite, incident lanes included, and a brief naming its need of the Mac fails. Closing a terminal needs the owner's word. His pick, from `lr ask-owner --id %s`: %s", len(reading.Terminals), reading.Cap, counted, reading.Cap, terminalCapCard, card)
	if _, err := c.Env.Bus.Post(ctx, client.Draft{Drive: c.Env.Config.Drive, Lane: Self, Kind: blockerKind, To: []string{OwnerRelay, meta}, Topic: terminalCapSubject, Text: text}); err != nil {
		return fmt.Errorf("%w: cci post: %w", ErrUnread, err)
	}
	return nil
}

func (c *TerminalCap) withdraw(ctx context.Context, reading terminalcap.Reading, meta string) error {
	raised, err := c.Store.Escalations(terminalCapSubject)
	if err != nil || len(raised) == 0 {
		return err
	}
	if _, err := c.askOwner(ctx, meta, "--withdraw", terminalCapCard, "--reason", fmt.Sprintf("Orca has room again: %d of the %d terminals it allows", len(reading.Terminals), reading.Cap)); err != nil {
		return err
	}
	return c.Store.ClearEscalation(terminalCapSubject)
}

func (c *TerminalCap) askOwner(ctx context.Context, meta string, args ...string) (string, error) {
	result, err := c.Env.Runner.Run(ctx, shell.Command{Argv: append([]string{c.Env.tool("lr"), "ask-owner", "--drive", c.Env.Config.Drive, "--lane", meta}, args...), Timeout: cardWait})
	var exit *shell.ExitError
	if err != nil && !errors.As(err, &exit) {
		return "", err
	}
	return strings.TrimSpace(result.Stdout + result.Stderr), nil
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
