package orca

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"slices"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/incident"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	lanePrefix      = "desk-lane-"
	landingBook     = "desk-landing"
	supervisorLane  = "supervisor"
	watchdogLane    = "drive-watchdog"
	unlaunched      = "unlaunched"
	kindLaunch      = "launch"
	kindRelay       = "relay"
	kindReply       = "reply"
	kindSend        = "send"
	kindHold        = "hold"
	kindEscalation  = "escalation"
	kindBlocker     = "blocker"
	kindNote        = "note"
	kindReport      = "report"
	kindDefect      = "defect"
	textLimit       = 400
	refusalsAllowed = 10
	escalationPref  = "escalation:"
	deferredFact    = "deferred-escalations"
	clockFact       = "incident-clock"
)

var (
	escalationKinds = map[string]string{
		"PREFIX": kindBlocker, "CARRIES": kindBlocker, "DECIDE": "decide", "INCIDENT": "incident", "INCIDENT-OWNED": kindState, "UNBOUND": kindBlocker,
		"UNOWNED": kindBlocker, "CLASS-UNOWNED": kindBlocker, "UNACKED": kindBlocker, "NOT-DELIVERED": kindBlocker, "INCIDENT-CLOCK": kindBlocker, "LOAD-HIGH": kindBlocker, "CUT-SILENT": kindBlocker,
		"RELEASE-HELD-AFTER-LANDING": kindBlocker, "LAUNCH-STALLED": kindBlocker, "LAUNCH-DUPLICATE": kindBlocker, "UNGRADED-30": kindBlocker, "SLICE-UNREAD": kindBlocker,
		"GITHUB-QUOTA-LOW": "urgent", "FINDER-DESK-STALLED": kindBlocker, "GO-TAP-MISSING": kindBlocker, "HOOK-DRIFT": kindBlocker, "BASE-DRIFT": kindBlocker, "OVERRIDE-REFUSED": kindBlocker, "REFUSED-STOP": "incident", "MUTE-PUSHBACK": "incident",
	}
	deferrable = []string{"DEADLINE", "NOT-DELIVERED", "UNACKED", "STALE-MAIL", "RECLAIM"}
	noAction   = []string{"RELAYED", "ORCA-BACK", "PARKED"}
	poster     = regexp.MustCompile("\\bcci post\\b[^`\\n]*?--lane[= ]['\"]?([\\w.-]+)")
)

type DeskConfig struct {
	Source, Store, Drive, DeskInbox, Run, Receipts, BriefsRepo, BriefsLog, GC, AlertFacts, LandingRepo, LandingCheckout string
	Launches                                                                                                            string
	LaunchEnv                                                                                                           map[string]string
	RefusedBriefs                                                                                                       []config.Refusal
	IncidentMechanismMinutes, IncidentPRMinutes, IncidentReadyMinutes                                                   int
	StartMinutes, LaunchMinutes, HoldMinutes, CaseLanes, LoadLimit                                                      int
}

type runnerFile struct {
	Store string `json:"store"`
	Drive string `json:"drive"`
	Orca  struct {
		DeskInbox string `json:"desk_inbox"`
		Run       string `json:"run"`
		Launches  string `json:"launches"`
		LoadLimit *int   `json:"load_limit"`
		Briefs    struct {
			Repo string `json:"repo"`
			Log  string `json:"log"`
		} `json:"briefs"`
		LaunchEnv     map[string]string `json:"launch_env"`
		GC            string            `json:"gc"`
		Sprite        *spriteRoute      `json:"sprite"`
		RefusedBriefs []config.Refusal  `json:"refused_briefs"`
	} `json:"orca"`
	Deadlines struct {
		Start  *int `json:"start_minutes"`
		Launch *int `json:"launch_minutes"`
		Hold   *int `json:"hold_minutes"`

		IncidentMechanism *int `json:"incident_mechanism_minutes"`
		IncidentPR        *int `json:"incident_pr_minutes"`
		IncidentReady     *int `json:"incident_ready_minutes"`
	} `json:"deadlines"`
	Incident struct {
		CaseLanes *int `json:"case_lanes"`
	} `json:"incident"`
	Alert struct {
		Facts string `json:"facts"`
	} `json:"alert"`
	Landing struct {
		Repo     string `json:"repo"`
		Checkout string `json:"checkout"`
	} `json:"landing"`
}

type spriteRoute struct {
	Launcher string            `json:"launcher"`
	Env      map[string]string `json:"env"`
	Tools    []spriteTool      `json:"tools"`
}

func readRunnerFile(path string) (runnerFile, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return runnerFile{}, err
	}
	var file runnerFile
	return file, json.Unmarshal(raw, &file)
}

func orDefault(value *int, fallback int) int {
	if value == nil {
		return fallback
	}
	return *value
}

func expandHome(path, home string) string {
	if path == "~" {
		return home
	}
	if rest, found := strings.CutPrefix(path, "~/"); found {
		return filepath.Join(home, rest)
	}
	return path
}

func LoadDeskConfig(path string, env func(string) string) (DeskConfig, error) {
	file, err := readRunnerFile(path)
	if err != nil {
		return DeskConfig{}, err
	}
	source, err := filepath.Abs(path)
	if err != nil {
		return DeskConfig{}, err
	}
	home := env("HOME")
	state := env("XDG_STATE_HOME")
	if state == "" {
		state = filepath.Join(home, ".local", "state")
	}
	if !slices.Contains([]string{"", launchesMac, launchesSprite}, file.Orca.Launches) {
		return DeskConfig{}, fmt.Errorf("%s: orca.launches is %q; it names %s, %s, or nothing for a runner that takes every launch", path, file.Orca.Launches, launchesMac, launchesSprite)
	}
	root := expandHome(file.Store, home)
	if root == "" {
		root = filepath.Join(home, ".claude", "long-running", "incidents")
	}
	return DeskConfig{
		Source: resolved(source), Store: root, Drive: file.Drive, DeskInbox: expandHome(file.Orca.DeskInbox, home), Run: file.Orca.Run, Launches: file.Orca.Launches,
		Receipts:   filepath.Join(state, "long-running", "orca-launch", file.Orca.Run),
		BriefsRepo: expandHome(file.Orca.Briefs.Repo, home), BriefsLog: file.Orca.Briefs.Log, GC: file.Orca.GC, AlertFacts: expandHome(file.Alert.Facts, home),
		LandingRepo: file.Landing.Repo, LandingCheckout: file.Landing.Checkout, LaunchEnv: file.Orca.LaunchEnv, RefusedBriefs: file.Orca.RefusedBriefs,
		StartMinutes: orDefault(file.Deadlines.Start, 10), LaunchMinutes: orDefault(file.Deadlines.Launch, 15), HoldMinutes: orDefault(file.Deadlines.Hold, 15), CaseLanes: orDefault(file.Incident.CaseLanes, 2), LoadLimit: orDefault(file.Orca.LoadLimit, runtime.NumCPU()),
		IncidentMechanismMinutes: orDefault(file.Deadlines.IncidentMechanism, 10), IncidentPRMinutes: orDefault(file.Deadlines.IncidentPR, 15), IncidentReadyMinutes: orDefault(file.Deadlines.IncidentReady, 20),
	}, nil
}

type DeskBus interface {
	Bus
	Among(ctx context.Context, drive string, seqs []int64) ([]client.Record, error)
}

type Process interface {
	Exited() bool
}

type Desk struct {
	Config       DeskConfig
	Bus          DeskBus
	Runner       shell.Runner
	Spawn        func(argv []string, log string, env []string) (Process, error)
	Env          func(string) string
	Now          func() time.Time
	Sleep        func(time.Duration)
	Release      func() string
	AliveTicks   <-chan time.Time
	Plugin       string
	Stderr       io.Writer
	FireIncident func(ctx context.Context, fired lanes.Fired) error
	Registry     *store.Store

	background  sync.WaitGroup
	store       incident.Store
	reads       passReads
	sender      []string
	launching   map[string]Process
	sprites     map[string]bool
	binding     *bindingState
	passTook    []time.Duration
	passSince   time.Time
	unreachable int
	refused     []time.Time
	swept       time.Time
	pass        atomic.Pointer[passMark]
	stuckPass   int
	stepTook    map[string]time.Duration
	passes      int
}

func (d *Desk) open() {
	d.store = incident.Store{Root: d.Config.Store}
	d.launching = map[string]Process{}
	d.sprites = map[string]bool{}
	d.stepTook = map[string]time.Duration{}
}

func (d *Desk) tool(name string) string {
	return filepath.Join(d.Plugin, "bin", name)
}

func (d *Desk) stamp() string {
	return store.Stamp(d.Now())
}

func laneBook(lane string) string {
	return lanePrefix + lane
}

func pacificClock(at time.Time) string {
	return at.In(pacificTime).Format("15:04")
}

func compactJSON(value any) (string, error) {
	var out bytes.Buffer
	encoder := json.NewEncoder(&out)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(value); err != nil {
		return "", err
	}
	return strings.TrimSuffix(out.String(), "\n"), nil
}

func (d *Desk) ensure(container, owner string) error {
	facts := map[string]any{}
	if container == deskRunner {
		facts[clockFacts] = map[string]any{}
		facts[clockWork] = map[string]any{}
	}
	return d.store.Create(incident.Incident{
		Header: incident.Header{ID: container, Owner: owner, OpenedAt: d.stamp(), Status: incident.StatusOpen},
		Facts:  facts,
	})
}

func (d *Desk) containers(prefix string) ([]string, error) {
	entries, err := os.ReadDir(d.Config.Store)
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var names []string
	for _, entry := range entries {
		name, isStore := strings.CutSuffix(entry.Name(), ".sqlite3")
		if isStore && !strings.HasPrefix(name, ".") && strings.HasPrefix(name, prefix) {
			names = append(names, name)
		}
	}
	slices.Sort(names)
	return names, nil
}

func (d *Desk) known(container, key string) (*incident.Action, error) {
	exists, err := d.store.Exists(container)
	if err != nil || !exists {
		return nil, err
	}
	action, err := d.store.Action(container, key)
	if errors.Is(err, incident.ErrUnknownAction) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &action, nil
}

func (d *Desk) laneActions(container, kind string, statuses ...incident.ActionStatus) ([]incident.Action, error) {
	exists, err := d.store.Exists(container)
	if err != nil || !exists {
		return nil, err
	}
	return d.store.Actions(container, kind, statuses...)
}

func (d *Desk) accept(container, key, kind, target, authority string, deadline time.Time) (incident.Action, bool, error) {
	var action incident.Action
	var created bool
	err := d.store.Edit(container, func(tx *incident.Tx) (err error) {
		action, created, err = tx.AcceptDue(key, kind, target, authority, d.Now(), deadline)
		return err
	})
	return action, created, err
}

func refusedChange(err error) (bool, error) {
	var refused *incident.TransitionError
	if err == nil {
		return true, nil
	}
	if errors.As(err, &refused) || errors.Is(err, incident.ErrUnknownAction) || errors.Is(err, incident.ErrStaleGeneration) {
		return false, nil
	}
	return false, err
}

func (d *Desk) attempt(container string, change func(*incident.Tx) error) (bool, error) {
	return refusedChange(d.store.Edit(container, change))
}

func (d *Desk) asOwner(container, owner string, change func(*incident.Tx) error) (bool, error) {
	header, err := d.store.Header(container)
	if err != nil {
		return false, err
	}
	if header.Owner != owner {
		return false, nil
	}
	return refusedChange(d.store.Owned(container, header.OwnerGeneration, change))
}

func (d *Desk) factOf(container, key string) (any, bool, error) {
	exists, err := d.store.Exists(container)
	if err != nil || !exists {
		return nil, false, err
	}
	return d.store.Fact(container, key)
}

func (d *Desk) setFact(container, key string, value any) error {
	return d.store.Edit(container, func(tx *incident.Tx) error { return tx.SetFact(key, value) })
}

func (d *Desk) record(key, line string) error {
	_, _, err := d.accept(deskRunner, key, kindEscalation, line, deskRunner, time.Time{})
	return err
}

func collapsed(text string) string {
	return strings.Join(strings.Fields(text), " ")
}

func escalationLine(kind, subject, about, text string) string {
	return kind + " " + subject + " " + about + ": " + collapsed(text)
}

func (d *Desk) escalate(key, kind, about, text string) error {
	return d.record(escalationPref+key, escalationLine(kind, key, about, text))
}

func (d *Desk) escalateTo(ctx context.Context, key, kind, about, text, to string) error {
	return d.recordTo(ctx, escalationPref+key, escalationLine(kind, key, about, text), to)
}

func (d *Desk) recordTo(ctx context.Context, action, line, to string) error {
	if to == "" {
		return d.record(action, line)
	}
	held, err := d.known(deskRunner, action)
	if err != nil || held != nil {
		return err
	}
	posted, err := d.postEscalation(ctx, line, to, action)
	if err != nil {
		fmt.Fprintf(d.Stderr, "escalation %s to %s goes to its notice lane on the flush; cci post: %v\n", strings.TrimPrefix(action, escalationPref), to, err)
		if err := d.countRefusal(to, err); err != nil {
			return err
		}
		return d.record(action, line)
	}
	if err := d.record(action, line); err != nil {
		return err
	}
	_, err = d.attempt(deskRunner, func(tx *incident.Tx) error {
		if _, err := tx.StartBy(action, d.Now(), time.Time{}, ""); err != nil {
			return err
		}
		_, err := tx.Complete(action, map[string]any{"at": d.stamp(), "posted": seqReceipt(posted.Seq), "to": to}, "")
		return err
	})
	return err
}

func (d *Desk) countRefusal(to string, cause error) error {
	now := d.Now()
	d.refused = append(slices.DeleteFunc(d.refused, func(at time.Time) bool { return now.Sub(at) >= time.Minute }), now)
	if len(d.refused) <= refusalsAllowed {
		return nil
	}
	return d.escalate("refused:"+to, "POSTS-REFUSED", deskRunner, fmt.Sprintf("%d cci posts refused in the last minute, the newest to %s: %.200s", len(d.refused), to, cause.Error()))
}

func seqReceipt(seq int64) string {
	return fmt.Sprintf("#%d", seq)
}

func (d *Desk) messageFile(id string) string {
	return filepath.Join(d.Config.Receipts, "cci", "messages", id+".md")
}

func escalationKind(label string) string {
	if kind, found := escalationKinds[label]; found {
		return kind
	}
	if strings.HasSuffix(label, "-FAILED") {
		return kindDefect
	}
	return kindReport
}

func (d *Desk) postEscalation(ctx context.Context, line, to, key string) (client.Record, error) {
	words := strings.Fields(line)
	label, topic := words[0], strings.TrimSuffix(words[1], ":")
	message, err := os.ReadFile(d.messageFile(topic))
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return client.Record{}, err
	}
	return d.post(ctx, deskPost{Kind: escalationKind(label), Text: line, To: to, Topic: topic, Key: key, Message: string(message), ID: key})
}

const (
	clockRefused      = "names something that has not happened yet"
	withdrawalRefused = "a withdrawn go is one withdraw record"
)

var (
	quotedClock      = regexp.MustCompile(`\b\d{1,2}:\d{2}(?::\d{2})? ?[AaPp][Mm]\b`)
	quotedWithdrawal = regexp.MustCompile(`(?i)\bwithdraw(?:s|n|ing)?:?\s+(?:the\s+|root\s+|my\s+)?go(?:s|\s+records?)?\b`)
)

type deskPost struct {
	Kind, Text, To, Topic, Key, Message, PR, ID, Lane string
	Attrs                                             map[string]string
}

func clipped(text string, limit int) string {
	runes := []rune(text)
	if len(runes) <= limit {
		return text
	}
	return string(runes[:limit-1]) + "…"
}

func (d *Desk) post(ctx context.Context, p deskPost) (client.Record, error) {
	posted, err := d.postOnce(ctx, p)
	if err == nil || (!strings.Contains(err.Error(), clockRefused) && !strings.Contains(err.Error(), withdrawalRefused)) {
		return posted, err
	}
	p.Message = strings.TrimSpace(p.Text + "\n\n" + p.Message)
	p.Text = quotedWithdrawal.ReplaceAllString(quotedClock.ReplaceAllString(p.Text, "[clock]"), "[withdrawal]")
	return d.postOnce(ctx, p)
}

func (d *Desk) postOnce(ctx context.Context, p deskPost) (client.Record, error) {
	draft := client.Draft{Drive: d.Config.Drive, Lane: p.Lane, Kind: p.Kind, Topic: p.Topic, To: []string{p.To}, ID: p.ID, Attrs: p.Attrs, Text: clipped(p.Text, textLimit)}
	if draft.Lane == "" {
		draft.Lane = deskRunner
	}
	if p.PR != "" {
		draft.PRs = []string{p.PR}
	}
	if len([]rune(p.Text)) > textLimit || p.Message != "" {
		sum := sha256.Sum256([]byte(p.Key))
		body := filepath.Join(d.Config.Receipts, "cci", hex.EncodeToString(sum[:])[:16]+".txt")
		if err := os.MkdirAll(filepath.Dir(body), 0o750); err != nil {
			return client.Record{}, err
		}
		parts := []string{p.Text}
		if message := strings.TrimSpace(p.Message); message != "" {
			parts = append(parts, message)
		}
		if err := os.WriteFile(body, []byte(strings.Join(parts, "\n\n")+"\n"), 0o600); err != nil {
			return client.Record{}, err
		}
		draft.Path = body
	}
	return d.Bus.Post(ctx, draft)
}

func (d *Desk) liveClocks() ([]string, error) {
	exists, err := d.store.Exists(deskRunner)
	if err != nil || !exists {
		return nil, err
	}
	return d.store.MatchingFactEntries(deskRunner, clockFacts, "ended", "")
}

func (d *Desk) flush(ctx context.Context) error {
	clocks, err := d.liveClocks()
	if err != nil {
		return err
	}
	onIncident := map[string]bool{}
	for _, lane := range clocks {
		family, err := d.siblings(lane)
		if err != nil {
			return err
		}
		for _, name := range append(family, lane) {
			names, err := d.names(name)
			if err != nil {
				return err
			}
			for _, alias := range names {
				onIncident[alias] = true
			}
		}
	}
	if len(clocks) == 0 {
		if err := d.releaseDeferred(); err != nil {
			return err
		}
	}
	held, err := d.deferredIDs()
	if err != nil {
		return err
	}
	pending, err := d.store.Actions(deskRunner, kindEscalation, incident.ActionAccepted)
	if err != nil {
		return err
	}
	var failures []string
	for _, action := range pending {
		words := strings.Fields(action.Target)
		to, err := lanes.NoticeTo(d.Registry, strings.TrimSuffix(words[2], ":"))
		if words[0] == "HOOK-DRIFT" || words[0] == "BASE-DRIFT" || words[0] == "REPLAYED" {
			to, err = lanes.MetaDesk(d.Registry)
		}
		if err != nil {
			return err
		}
		if to == coordinatorLane && len(clocks) > 0 && slices.Contains(deferrable, words[0]) && !onIncident[strings.TrimSuffix(words[2], ":")] {
			if !slices.Contains(held, action.ActionID) {
				if err := d.deferEscalation(action.ActionID, clocks); err != nil {
					return err
				}
				held = append(held, action.ActionID)
			}
			continue
		}
		if slices.Contains(noAction, words[0]) {
			to = watchdogLane + "@" + d.Config.Drive
		}
		posted, err := d.postEscalation(ctx, action.Target, to, action.ActionID)
		if err != nil {
			failures = append(failures, err.Error())
			continue
		}
		if _, err := d.attempt(deskRunner, func(tx *incident.Tx) error {
			if _, err := tx.StartBy(action.ActionID, d.Now(), time.Time{}, ""); err != nil {
				return err
			}
			_, err := tx.Complete(action.ActionID, map[string]any{"at": d.stamp(), "posted": seqReceipt(posted.Seq)}, "")
			return err
		}); err != nil {
			return err
		}
	}
	if len(failures) > 0 {
		fmt.Fprintf(d.Stderr, "%d escalation(s) left for the next pass; %.300s\n", len(failures), failures[0])
	}
	return nil
}

func (d *Desk) deferredIDs() ([]string, error) {
	value, found, err := d.factOf(deskRunner, deferredFact)
	if err != nil || !found {
		return nil, err
	}
	return stringList(value.(map[string]any)["ids"]), nil
}

func stringList(value any) []string {
	items, _ := value.([]any)
	found := make([]string, 0, len(items))
	for _, item := range items {
		if text, isText := item.(string); isText {
			found = append(found, text)
		}
	}
	return found
}

func (d *Desk) deferEscalation(id string, clocks []string) error {
	return d.store.Edit(deskRunner, func(tx *incident.Tx) error {
		value, found, err := tx.Fact(deferredFact)
		if err != nil {
			return err
		}
		sorted := slices.Sorted(slices.Values(clocks))
		held := map[string]any{"incident": strings.Join(sorted, ", "), "ids": []any{}}
		if found {
			held = value.(map[string]any)
		}
		ids := stringList(held["ids"])
		if !slices.Contains(ids, id) {
			ids = append(ids, id)
		}
		held["ids"] = ids
		return tx.SetFact(deferredFact, held)
	})
}

func (d *Desk) releaseDeferred() error {
	value, found, err := d.factOf(deskRunner, deferredFact)
	if err != nil || !found {
		return err
	}
	held := value.(map[string]any)
	ids := stringList(held["ids"])
	if len(ids) == 0 {
		return nil
	}
	lines := make([]string, 0, len(ids))
	err = d.store.Edit(deskRunner, func(tx *incident.Tx) error {
		for _, id := range ids {
			action, err := tx.Action(id)
			if err != nil {
				return err
			}
			lines = append(lines, action.Target)
			if action.Status != incident.ActionAccepted {
				continue
			}
			if _, err := tx.StartBy(id, d.Now(), time.Time{}, ""); err != nil {
				return err
			}
			if _, err := tx.Complete(id, map[string]any{"at": d.stamp(), "deferred": true}, ""); err != nil {
				return err
			}
		}
		return tx.DropFact(deferredFact)
	})
	if err != nil {
		return err
	}
	return d.record(escalationPref+"deferred:"+ids[0], fmt.Sprintf("DEFERRED %d escalations held during %v: %s", len(lines), held["incident"], strings.Join(lines, " | ")))
}
