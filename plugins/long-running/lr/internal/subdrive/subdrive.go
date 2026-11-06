package subdrive

import (
	"bytes"
	"context"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"text/template"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	separator  = "--"
	laneEnv    = "CLAUDE_LONG_RUNNING_LANE"
	driveEnv   = "CLAUDE_LONG_RUNNING_DRIVE"
	endedBy    = "parent-coordinator"
	configFile = "lr.json"
)

type fact struct {
	flag, label, usage string
}

var facts = []fact{
	{"no-shared-prs", "PRs", "how you know no PR of this project is also one of this drive's"},
	{"no-shared-files", "Files", "how you know this project and this drive touch no file in common"},
	{"no-shared-steps", "Plan steps", "how you know no step of this drive's plan belongs to this project"},
	{"no-shared-targets", "Release targets", "how you know this project and this drive release no target in common"},
	{"own-lanes", "Its own lanes", "the lanes this project needs, which make it big enough for its own coordinator"},
}

var (
	validName = regexp.MustCompile(`^[a-z0-9]+(-[a-z0-9]+)*$`)
	openKinds = []string{"ask", "decide", "blocker", "blocked", "defect", "hold", "incident"}

	//go:embed brief.md.tmpl
	briefText string
	brief     = template.Must(template.New("brief").Parse(briefText))
)

type Request struct {
	Name      string
	Objective string
	Reason    string
	Repo      string
	Checkout  string
	Facts     map[string]string
}

func (r Request) missing() []string {
	var named []string
	for flag, value := range map[string]string{"objective": r.Objective, "reason": r.Reason} {
		if strings.TrimSpace(value) == "" {
			named = append(named, "--"+flag)
		}
	}
	for _, f := range facts {
		if strings.TrimSpace(r.Facts[f.flag]) == "" {
			named = append(named, "--"+f.flag)
		}
	}
	slices.Sort(named)
	return named
}

type Manager struct {
	Env         lanes.Env
	Supervise   func(config string) error
	Unsupervise func(config string) error
}

func (m Manager) refusal(r Request) error {
	parent := m.Env.Config
	if parent.Parent != "" {
		return fmt.Errorf("%s is itself a sub-drive of %s, and only the coordinator of a top-level drive starts one: report the project to root@%s", parent.Drive, parent.Parent, parent.Parent)
	}
	if lane := os.Getenv(laneEnv); lane != "" && lane != lanes.Root {
		return fmt.Errorf("this window is bound %s, and the coordinator alone decides on a sub-drive: ask the coordinator", lane)
	}
	if missing := r.missing(); len(missing) > 0 {
		return fmt.Errorf("a sub-drive is never the default, only a big project plainly unrelated to this drive; name %s, or give the work a lane here", strings.Join(missing, ", "))
	}
	if !validName.MatchString(r.Name) {
		return fmt.Errorf("sub-drive name %q must be lowercase words joined by single hyphens", r.Name)
	}
	_, found, err := m.entry(r.Name)
	if err != nil {
		return err
	}
	if found {
		return fmt.Errorf("sub-drive %s is already open; end it first with `lr drive sub end %s`", r.Name, r.Name)
	}
	return nil
}

func (m Manager) entry(name string) (drive.Entry, bool, error) {
	open, err := drive.Children(m.Env.Config.Drive)
	if err != nil {
		return drive.Entry{}, false, err
	}
	child := m.Env.Config.Drive + separator + name
	for _, entry := range open {
		if entry.Drive == child {
			return entry, true, nil
		}
	}
	return drive.Entry{}, false, nil
}

func (m Manager) child(cfg config.Config, state string) lanes.Env {
	env := m.Env
	env.Config, env.State = cfg, state
	return env
}

func (m Manager) createRun(ctx context.Context, objective string) (string, error) {
	out, err := m.Env.Run(ctx, "orca", "orchestration", "run-create", "--objective", objective, "--full", "--json")
	if err != nil {
		return "", err
	}
	var created struct {
		Result struct {
			Run struct {
				ID string `json:"id"`
			} `json:"run"`
		} `json:"result"`
	}
	if err := json.Unmarshal([]byte(out), &created); err != nil {
		return "", fmt.Errorf("orca orchestration run-create printed %q: %w", out, err)
	}
	if created.Result.Run.ID == "" {
		return "", fmt.Errorf("orca orchestration run-create printed no Run id: %q", out)
	}
	return created.Result.Run.ID, nil
}

func (m Manager) Start(ctx context.Context, r Request) (string, error) {
	if err := m.refusal(r); err != nil {
		return "", err
	}
	parent := m.Env.Config
	name := parent.Drive + separator + r.Name
	state := filepath.Join(m.Env.State, "sub", r.Name)
	cfg := config.Config{Repo: r.Repo, Drive: name, Checkout: r.Checkout, Author: parent.Author, Label: name, Movers: []string{"lanes"}, Parent: parent.Drive}
	if cfg.Repo == "" {
		cfg.Repo = parent.Repo
	}
	if cfg.Checkout == "" {
		cfg.Checkout = parent.Checkout
	}
	env := m.child(cfg, state)
	ledger, err := env.Ledger(ctx, "init", "--title", "desk: "+name)
	if err != nil {
		return "", err
	}
	common, err := env.Run(ctx, "git", "-C", cfg.Checkout, "rev-parse", "--path-format=absolute", "--git-common-dir")
	if err != nil {
		return "", err
	}
	if cfg.OrcaRun, err = m.createRun(ctx, r.Objective); err != nil {
		return "", err
	}
	env.Config = cfg
	path, briefPath := filepath.Join(state, configFile), filepath.Join(state, lanes.Root, "brief.md")
	if err := os.MkdirAll(filepath.Dir(briefPath), 0o750); err != nil {
		return "", err
	}
	if err := writeConfig(path, cfg); err != nil {
		return "", err
	}
	ledger = strings.TrimSpace(ledger)
	if err := writeBrief(briefPath, r, cfg, state, ledger); err != nil {
		return "", err
	}
	stamp := env.Now().UTC().Format(time.RFC3339)
	if err := drive.Save(drive.Entry{
		Drive: name, CCIDrive: name, Program: name, Ledger: ledger, Repo: cfg.Repo, GitCommonDir: strings.TrimSpace(common), Checkout: cfg.Checkout,
		OrcaRun: cfg.OrcaRun, StateDir: state, Sessions: []string{}, StartedAt: stamp, UpdatedAt: stamp,
		Parent: parent.Drive, Objective: r.Objective, Reason: r.Reason, Facts: r.Facts,
	}); err != nil {
		return "", err
	}
	if err := m.Supervise(path); err != nil {
		return "", fmt.Errorf("sub-drive %s is registered but its supervisor did not install: %w; run `lr drive sub end %s` and start again", r.Name, err, r.Name)
	}
	launched, err := env.LaunchCoordinator(ctx, briefPath, driveEnv+"="+name, "ORCA_LAUNCH_RUN="+cfg.OrcaRun, "ORCA_LAUNCH_PARENT="+cfg.Checkout)
	if err != nil {
		return "", fmt.Errorf("sub-drive %s is registered but its coordinator did not launch: %w; run `lr drive sub end %s` and start again", r.Name, err, r.Name)
	}
	if _, err := m.Env.Bus.Post(ctx, client.Draft{
		Drive: parent.Drive, Lane: lanes.Root, Kind: "report", To: []string{lanes.OwnerRelay}, ID: "sub-drive-start:" + name,
		Text: fmt.Sprintf("Started sub-drive %s: %s. Why it is separate: %s. Its small coordinator reports only to the coordinator, on cci drive %s.", r.Name, r.Objective, r.Reason, name),
	}); err != nil {
		return "", err
	}
	return fmt.Sprintf("sub-drive %s up: cci drive %s, Orca Run %s, state %s, ledger %s\n%s", r.Name, name, cfg.OrcaRun, state, ledger, launched), nil
}

func writeConfig(path string, cfg config.Config) error {
	data, err := json.MarshalIndent(map[string]any{
		"repo": cfg.Repo, "drive": cfg.Drive, "checkout": cfg.Checkout, "author": cfg.Author, "label": cfg.Label,
		"movers": cfg.Movers, "orca_run": cfg.OrcaRun, "parent": cfg.Parent,
	}, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, append(data, '\n'), 0o600)
}

func writeBrief(path string, r Request, cfg config.Config, state, ledger string) error {
	type line struct{ Label, Value string }
	stated := make([]line, 0, len(facts))
	for _, f := range facts {
		stated = append(stated, line{f.label, r.Facts[f.flag]})
	}
	var out bytes.Buffer
	if err := brief.Execute(&out, map[string]any{
		"Name": r.Name, "Parent": cfg.Parent, "Child": cfg.Drive, "Objective": r.Objective, "Reason": r.Reason, "Facts": stated,
		"State": state, "Ledger": ledger, "Run": cfg.OrcaRun,
	}); err != nil {
		return err
	}
	return os.WriteFile(path, out.Bytes(), 0o600)
}

type item struct {
	label, detail string
}

func prList(numbers []int) string {
	words := make([]string, 0, len(numbers))
	for _, number := range numbers {
		words = append(words, "#"+strconv.Itoa(number))
	}
	return strings.Join(words, " ")
}

func openItems(ctx context.Context, env lanes.Env, s *store.Store) (items []item, live []string, err error) {
	rows, err := s.Lanes(store.LaneLaunching, store.LaneDesired, store.LaneHeld)
	if err != nil {
		return nil, nil, err
	}
	for _, lane := range rows {
		live = append(live, lane.Name)
		if lane.Name != lanes.Root {
			items = append(items, item{
				label:  strings.TrimSpace(fmt.Sprintf("lane %s (%s) %s", lane.Name, lane.State, prList(lane.PRs))),
				detail: fmt.Sprintf("brief %s, handoff %s", lane.Brief, lane.Handoff),
			})
		}
	}
	records, err := env.Bus.Open(ctx, env.Config.Drive, openKinds...)
	if err != nil {
		return nil, nil, err
	}
	for _, record := range records {
		items = append(items, item{label: fmt.Sprintf("%s %s from %s", record.Name(), record.Kind, record.Lane), detail: record.Text})
	}
	out, err := env.Ledger(ctx, "list", "--drive", env.Config.Drive, "--open", "--json")
	if err != nil {
		return nil, nil, err
	}
	var prs []struct {
		PR   json.Number `json:"pr"`
		Head string      `json:"head"`
		Lane string      `json:"lane"`
	}
	if err := json.Unmarshal([]byte(out), &prs); err != nil {
		return nil, nil, fmt.Errorf("ledger list: %w", err)
	}
	for _, pr := range prs {
		items = append(items, item{label: fmt.Sprintf("PR #%s of %s", pr.PR, pr.Lane), detail: "head " + pr.Head})
	}
	return items, live, nil
}

func (m Manager) loaded(ctx context.Context, entry drive.Entry) (items []item, live []string, coordinator string, err error) {
	cfg, err := config.Load(filepath.Join(entry.StateDir, configFile), os.Stderr)
	if err != nil {
		return nil, nil, "", err
	}
	s, err := store.Open(entry.Store())
	if err != nil {
		return nil, nil, "", err
	}
	defer func() { err = errors.Join(err, s.Close()) }()
	if items, live, err = openItems(ctx, m.child(cfg, entry.StateDir), s); err != nil {
		return nil, nil, "", err
	}
	row, found, err := s.Lane(lanes.Root)
	if err != nil {
		return nil, nil, "", err
	}
	coordinator = "not heard yet"
	if found {
		coordinator = string(row.State)
	}
	return items, live, coordinator, nil
}

func (m Manager) List(ctx context.Context) ([]string, error) {
	if err := drive.MacOnly(filepath.Join(m.Env.State, lanes.StoreFile)); err != nil {
		return nil, err
	}
	open, err := drive.Children(m.Env.Config.Drive)
	if err != nil {
		return nil, err
	}
	lines := make([]string, 0, len(open))
	for _, entry := range open {
		items, _, coordinator, err := m.loaded(ctx, entry)
		if err != nil {
			return nil, err
		}
		name := strings.TrimPrefix(entry.Drive, entry.Parent+separator)
		lines = append(lines, fmt.Sprintf("%s: %s. Coordinator %s, %d open items, Orca Run %s, cci drive %s. Why separate: %s", name, entry.Objective, coordinator, len(items), entry.OrcaRun, entry.Drive, entry.Reason))
	}
	return lines, nil
}

func (m Manager) End(ctx context.Context, name string) (string, error) {
	entry, found, err := m.entry(name)
	if err != nil {
		return "", err
	}
	if !found {
		return "", fmt.Errorf("%s has no open sub-drive %s", m.Env.Config.Drive, name)
	}
	items, live, _, err := m.loaded(ctx, entry)
	if err != nil {
		return "", err
	}
	parent := m.Env.Config.Drive
	gc := fmt.Sprintf("Run `lr gc --run %s` once its lanes stop.", entry.OrcaRun)
	back := client.Draft{Drive: parent, Lane: "sub-drive-" + name, Kind: "report", To: []string{lanes.Root}, ID: "sub-drive-end:" + entry.Drive, Text: fmt.Sprintf("Sub-drive %s ended with nothing open. %s", name, gc)}
	if len(items) > 0 {
		path, err := writeHandback(entry, name, items)
		if err != nil {
			return "", err
		}
		labels := make([]string, 0, len(items))
		for _, one := range items {
			labels = append(labels, one.label)
		}
		back.Kind = "ask"
		back.Text = fmt.Sprintf("Sub-drive %s ended and hands back %d open items, now yours: %s. Details: %s. %s", name, len(items), strings.Join(labels, "; "), path, gc)
	}
	if _, err := m.Env.Bus.Post(ctx, back); err != nil {
		return "", err
	}
	if len(live) > 0 {
		if _, err := m.Env.Bus.Post(ctx, client.Draft{
			Drive: entry.Drive, Lane: endedBy, Kind: "stand-down", To: live,
			Text: fmt.Sprintf("Sub-drive %s is over: its open work went to root@%s. Stop your desks and end your turn.", name, parent),
		}); err != nil {
			return "", err
		}
	}
	if err := m.Unsupervise(filepath.Join(entry.StateDir, configFile)); err != nil {
		return "", err
	}
	if err := retire(entry, live, m.Env.Now()); err != nil {
		return "", err
	}
	if err := drive.Remove(entry.Drive); err != nil {
		return "", err
	}
	return back.Text, nil
}

func retire(entry drive.Entry, live []string, at time.Time) (err error) {
	s, err := store.Open(entry.Store())
	if err != nil {
		return err
	}
	defer func() { err = errors.Join(err, s.Close()) }()
	for _, lane := range live {
		if err := s.SetLaneState(lane, store.LaneRetired, at, endedBy, "the sub-drive ended"); err != nil {
			return err
		}
	}
	return nil
}

func writeHandback(entry drive.Entry, name string, items []item) (string, error) {
	var out strings.Builder
	fmt.Fprintf(&out, "# Open items of sub-drive %s\n\n", name)
	for _, one := range items {
		fmt.Fprintf(&out, "- %s: %s\n", one.label, one.detail)
	}
	path := filepath.Join(entry.StateDir, "handback.md")
	return path, os.WriteFile(path, []byte(out.String()), 0o600)
}
