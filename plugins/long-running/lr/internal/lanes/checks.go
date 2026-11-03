package lanes

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"log/slog"
	"maps"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
	"golang.org/x/mod/semver"
)

const (
	alertGrace   = 3 * time.Minute
	alertWindow  = time.Hour
	alertRead    = 20
	fixLiveIdle  = 30 * time.Minute
	fixLiveBack  = 6 * time.Hour
	gcEvery      = 5 * time.Minute
	gcTimeout    = 10 * time.Minute
	stopTimeout  = 5 * time.Minute
	stopRelease  = "v0.27.3"
	launchWait   = 5 * time.Minute
	launchMargin = 10 * time.Second
	incidentBar  = 20 * time.Minute
	notLiveOwed  = time.Hour
	notLiveSpan  = 24 * time.Hour
	notLiveEvery = 5 * time.Minute
	notLiveTopic = "landed-not-live"
	blockerKind  = "blocker"
)

var (
	alertURL       = regexp.MustCompile(`app\.datadoghq\.com/monitors/(\d+)|sentry\.io/\S*?issues/(\d+)`)
	recoveredAlert = regexp.MustCompile(`(?i)^\W*(?:Recovered|Resolved)\b`)
	titleScope     = regexp.MustCompile(`^\s*([\w./-]+):`)
)

type Check func(ctx context.Context, now time.Time) error

type slackMessage struct {
	TS        string `json:"ts"`
	Text      string `json:"text"`
	Permalink string `json:"permalink"`
}

type Alerts struct {
	Env      Env
	Store    *store.Store
	Channels []string
	Log      *slog.Logger
	Launch   func(ctx context.Context, fired Fired) (string, error)
}

type Fired struct {
	Slug string
	What string
	Link string
	At   time.Time
}

func (e Env) incidentBrief(lane, template string, fired Fired) (string, error) {
	text, err := os.ReadFile(filepath.Join(e.plugin(), "skills", "long-running", "reference", template))
	if errors.Is(err, fs.ErrNotExist) {
		text, err = os.ReadFile(filepath.Join(e.State, template))
	}
	if err != nil {
		return "", err
	}
	reported := e.Now().In(pacificZone)
	rendered := strings.NewReplacer(
		"{{", "{", "}}", "}", "{lane}", lane, "{incident}", fired.Slug, "{slug}", fired.Slug, "{link}", fired.Link, "{what}", fired.What,
		"{onset}", pacific(fired.At), "{reported}", reported.Format("15:04"), "{due}", reported.Add(incidentBar).Format("15:04"),
		"{repo}", e.Config.Checkout, "{log}", "incident-"+fired.Slug, "{facts}", "drive "+e.Config.Drive,
	).Replace(string(text))
	dir := filepath.Join(e.State, "lanes")
	if err := os.MkdirAll(dir, 0o750); err != nil {
		return "", err
	}
	path := filepath.Join(dir, lane+".brief.md")
	return path, os.WriteFile(path, []byte(rendered), 0o600)
}

func (e Env) incidentLaunch(ctx context.Context, fired Fired) (string, error) {
	fix, err := e.incidentBrief(fired.Slug+"-fix", "alert-fix-brief.md", fired)
	if err != nil {
		return "", err
	}
	argv := []string{e.tool("lr"), "incident", "run", fired.Slug, "--fix-brief", fix, "--timeout-seconds", strconv.Itoa(int((launchWait - launchMargin).Seconds()))}
	evidence, err := e.incidentBrief(fired.Slug+"-evidence", "alert-evidence-brief.md", fired)
	switch {
	case err == nil:
		argv = append(argv, "--evidence-brief", evidence)
	case !errors.Is(err, fs.ErrNotExist):
		return "", err
	}
	_, err = e.Runner.Run(ctx, shell.Command{Argv: append(argv, "--drive", e.Config.Drive, "--lane", Self), Timeout: launchWait})
	return fix, err
}

func (a Alerts) Check(ctx context.Context, now time.Time) error {
	for _, channel := range a.Channels {
		stdout, err := a.Env.Run(ctx, "cc-slack", "history", "--channel", channel, "--json", "--limit", strconv.Itoa(alertRead))
		if err != nil {
			a.Log.Warn("alert channel did not read", "channel", channel, "err", err)
			continue
		}
		var history struct {
			Messages []slackMessage `json:"messages"`
		}
		if err := json.Unmarshal([]byte(stdout), &history); err != nil {
			a.Log.Warn("alert channel did not parse", "channel", channel, "err", err)
			continue
		}
		for _, message := range history.Messages {
			if err := a.raise(ctx, channel, message, now); err != nil {
				return err
			}
		}
	}
	return nil
}

func alertOf(message slackMessage) (alert, slug string, ok bool) {
	found := alertURL.FindStringSubmatch(message.Text)
	if found == nil || recoveredAlert.MatchString(message.Text) {
		return "", "", false
	}
	if found[1] != "" {
		return found[1], "dd-" + found[1], true
	}
	return found[2], "sentry-" + found[2], true
}

func firedAt(ts string) (time.Time, error) {
	seconds, err := strconv.ParseFloat(ts, 64)
	if err != nil {
		return time.Time{}, fmt.Errorf("slack ts %q: %w", ts, err)
	}
	return time.Unix(0, int64(seconds*float64(time.Second))).UTC(), nil
}

func (a Alerts) raise(ctx context.Context, channel string, message slackMessage, now time.Time) error {
	alert, slug, ok := alertOf(message)
	if !ok {
		return nil
	}
	fired, err := firedAt(message.TS)
	if err != nil {
		return err
	}
	if age := now.Sub(fired); age < alertGrace || age >= alertWindow {
		return nil
	}
	incidents, err := a.Env.Bus.Tail(ctx, client.Query{Drive: a.Env.Config.Drive, Since: fired, Kinds: []string{"incident"}})
	if err != nil {
		return fmt.Errorf("%w: cci tail incident: %w", ErrUnread, err)
	}
	for _, incident := range incidents {
		if strings.Contains(incident.Text, alert) {
			return nil
		}
	}
	if _, found, err := a.Store.Lane(slug + "-fix"); err != nil || found {
		return err
	}
	first, err := a.Store.Escalate("alert:"+channel+":"+message.TS, "launch", Root, now)
	if err != nil || !first {
		return err
	}
	headline, _, _ := strings.Cut(message.Text, "\n")
	headline = truncated(strings.Join(strings.Fields(headline), " "), 160)
	minutes := int(now.Sub(fired).Minutes())
	draft := client.Draft{Drive: a.Env.Config.Drive, Lane: Self, To: []string{Root}, URL: message.Permalink, Stamp: true}
	brief, launchErr := a.Launch(ctx, Fired{slug, headline, message.Permalink, fired})
	if launchErr != nil {
		draft.Kind, draft.ID = blockerKind, "alert-unopened:"+channel+":"+message.TS
		draft.Text = fmt.Sprintf("ALERT-UNOPENED %s: no incident record names %s %d min after it fired, and the supervisor's launch of %s-fix failed: %s | root: open the incident on sight with `lr incident open --kind alert` and launch its fix lane this turn", headline, alert, minutes, slug, truncated(launchErr.Error(), 200))
	} else {
		draft.Kind, draft.ID = kindSpawn, "alert-launched:"+channel+":"+message.TS
		draft.LaneRefs = []string{slug + "-fix", slug + "-evidence"}
		draft.Attrs = map[string]string{"brief": brief, "role": "fix", "model": "incident", "effort": "xhigh"}
		draft.Text = fmt.Sprintf("ALERT-LAUNCHED %s: no incident record named %s %d min after it fired, so the supervisor launched %s-fix and %s-evidence", headline, alert, minutes, slug, slug)
	}
	if _, err := a.Env.Bus.Post(ctx, draft); err != nil {
		return fmt.Errorf("%w: cci post: %w", ErrUnread, err)
	}
	return nil
}

type FixLiveGC struct {
	Env       Env
	Log       *slog.Logger
	read      time.Time
	reclaimed map[string]bool
}

func (g *FixLiveGC) Check(ctx context.Context, now time.Time) error {
	if !g.read.IsZero() && now.Sub(g.read) < gcEvery {
		return nil
	}
	g.read = now
	rows, err := g.Env.Bus.Tail(ctx, client.Query{Drive: g.Env.Config.Drive, Since: now.Add(-fixLiveBack)})
	if err != nil {
		g.Log.Warn("fix-live gc did not read", "err", err)
		return nil
	}
	heard := map[string]time.Time{}
	for _, row := range rows {
		heard[row.Lane] = newer(row.At.UTC(), heard[row.Lane])
	}
	for _, row := range rows {
		if !strings.Contains(row.Text, "FIX-LIVE") || now.Sub(row.At) < fixLiveIdle {
			continue
		}
		seen := map[string]bool{}
		for _, lane := range append([]string{row.Lane}, row.To...) {
			if seen[lane] {
				continue
			}
			seen[lane] = true
			if err := g.reclaim(ctx, lane, row.Seq, heard[lane], now); err != nil {
				return err
			}
		}
	}
	return nil
}

func (g *FixLiveGC) reclaim(ctx context.Context, lane string, seq int64, heard, now time.Time) error {
	dispatch := dispatchOf(g.Env.receipts(), lane)
	if dispatch == "" || g.reclaimed[dispatch] || now.Sub(heard) < fixLiveIdle {
		return nil
	}
	if g.reclaimed == nil {
		g.reclaimed = map[string]bool{}
	}
	g.reclaimed[dispatch] = true
	gc := filepath.Join(g.Env.plugin(), "skills", "long-running", "scripts", "orca-gc")
	result, err := g.Env.Runner.Run(ctx, shell.Command{Argv: []string{gc, "--run", g.Env.Config.OrcaRun, "--done", dispatch}, Timeout: gcTimeout})
	var exit *shell.ExitError
	if err != nil && !errors.As(err, &exit) {
		g.Log.Warn("orca-gc did not run", "dispatch", dispatch, "err", err)
	}
	said := strings.TrimSpace(or(result.Stdout, result.Stderr))
	lines := strings.Split(said, "\n")
	lines = lines[max(0, len(lines)-3):]
	summary := or(strings.Join(lines, " | "), fmt.Sprintf("exit %d", result.Code))
	_, err = g.Env.Bus.Post(ctx, client.Draft{Drive: g.Env.Config.Drive, Lane: Self, Kind: "report", To: []string{Root}, ID: "fix-live-gc:" + dispatch, Text: fmt.Sprintf("GC %s: FIX-LIVE #%d is 30 minutes old and %s has posted nothing since, so `orca-gc --done %s` ended it: %s", lane, seq, lane, dispatch, summary)})
	if err != nil {
		return fmt.Errorf("%w: cci post: %w", ErrUnread, err)
	}
	return nil
}

type CcRemote struct {
	Binary string
	Config string
}

func CcRemoteOf(state string) (*CcRemote, error) {
	data, err := os.ReadFile(filepath.Join(state, "runner.json"))
	if errors.Is(err, fs.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var runner struct {
		Orca struct {
			Sprite *struct {
				Env map[string]string `json:"env"`
			} `json:"sprite"`
		} `json:"orca"`
	}
	if err := json.Unmarshal(data, &runner); err != nil {
		return nil, fmt.Errorf("runner.json: %w", err)
	}
	if runner.Orca.Sprite == nil {
		return nil, nil
	}
	env := runner.Orca.Sprite.Env
	tools := filepath.Join(env["WORKER_CHECKOUT"], "tools", "cc-remote")
	return &CcRemote{Binary: or(env["WORKER_CC_REMOTE"], filepath.Join(tools, "bin", "cc-remote")), Config: or(env["CC_REMOTE_CONFIG"], filepath.Join(tools, "config.yaml"))}, nil
}

func (r CcRemote) hasStop(ctx context.Context, runner shell.Runner) error {
	result, err := runner.Run(ctx, shell.Command{Argv: []string{r.Binary, "version"}, Timeout: stopTimeout})
	if err != nil {
		return err
	}
	printed := strings.TrimSpace(result.Stdout + result.Stderr)
	version := printed[strings.LastIndex(printed, "\n")+1:]
	if semver.Compare("v"+version, stopRelease) < 0 {
		return fmt.Errorf("%s version printed %s, and orca stop first shipped in cc-remote %s", r.Binary, or(version, "nothing"), stopRelease)
	}
	return nil
}

type SpriteStops struct {
	Env      Env
	Store    *store.Store
	CcRemote CcRemote
	Log      *slog.Logger
	read     time.Time
}

func (c *SpriteStops) Check(ctx context.Context, now time.Time) error {
	if !c.read.IsZero() && now.Sub(c.read) < gcEvery {
		return nil
	}
	c.read = now
	hosted, err := c.Store.UnstoppedSpriteLanes()
	if err != nil || len(hosted) == 0 {
		return err
	}
	statuses, err := c.Env.dispatchStatuses(ctx)
	if err != nil {
		c.Log.Warn("sprite stops did not read the Run", "err", err)
		return nil
	}
	why := map[string]string{}
	for _, lane := range hosted {
		if reason := endedBecause(lane, dispatchOf(c.Env.receipts(), lane.Name), statuses); reason != "" {
			why[lane.Name] = reason
		}
	}
	if len(why) == 0 {
		return nil
	}
	if err := c.CcRemote.hasStop(ctx, c.Env.Runner); err != nil {
		c.Log.Warn("sprite stops wait for a cc-remote that has orca stop", "lanes", slices.Sorted(maps.Keys(why)), "err", err)
		return nil
	}
	var ended []string
	for _, lane := range slices.Sorted(maps.Keys(why)) {
		first, err := c.Store.StopSprite(lane, now)
		if err != nil {
			return err
		}
		if first {
			ended = append(ended, lane)
		}
	}
	stopped := make([]error, len(ended))
	var wg sync.WaitGroup
	for i, lane := range ended {
		wg.Go(func() {
			_, stopped[i] = c.Env.Runner.Run(ctx, shell.Command{Argv: []string{c.CcRemote.Binary, "orca", "stop", lane, "--config", c.CcRemote.Config}, Timeout: stopTimeout})
		})
	}
	wg.Wait()
	for i, lane := range ended {
		if stopped[i] == nil {
			c.Log.Info("sprite stopped", "lane", lane, "why", why[lane])
			continue
		}
		text := fmt.Sprintf("SPRITE-STOP-FAILED %s: %s, so the supervisor ran `cc-remote orca stop %s` once, and it failed: %s | its Sprite still runs and the supervisor does not try again; stop it by hand with `%s orca stop %s --config %s`", lane, why[lane], lane, truncated(stopped[i].Error(), 300), c.CcRemote.Binary, lane, c.CcRemote.Config)
		if _, err := c.Env.Bus.Post(ctx, client.Draft{Drive: c.Env.Config.Drive, Lane: Self, Kind: blockerKind, To: []string{Root}, ID: "sprite-stop:" + lane, Stamp: true, Text: text}); err != nil {
			return fmt.Errorf("%w: cci post: %w", ErrUnread, err)
		}
	}
	return nil
}

func endedBecause(lane store.Lane, dispatch string, statuses map[string]dispatchRead) string {
	read, known := statuses[dispatch]
	switch {
	case known && slices.Contains(settledDispatch, read.Status):
		return fmt.Sprintf("its dispatch %s is %s", dispatch, read.Status)
	case !known && (lane.RetiredBy == "done" || lane.RetiredBy == "withdrawn"):
		return "it was retired by " + lane.RetiredBy
	}
	return ""
}

type ledgerRow struct {
	PR        json.Number `json:"pr"`
	State     string      `json:"state"`
	Title     string      `json:"title"`
	LandedSHA string      `json:"landed_sha"`
	LandedAt  string      `json:"landed_at"`
}

type landing struct {
	target string
	sha    string
	at     time.Time
}

type LandedNotLive struct {
	Env        Env
	ReleaseCLI []string
	Log        *slog.Logger
	read       time.Time
	holds      map[[2]string]bool
	said       string
	owed       map[string]int64
}

func ReleaseCLI(state string) ([]string, error) {
	data, err := os.ReadFile(filepath.Join(state, "runner.json"))
	if errors.Is(err, fs.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var runner struct {
		Landing struct {
			ReleaseCLI string `json:"release_cli"`
		} `json:"landing"`
	}
	if err := json.Unmarshal(data, &runner); err != nil {
		return nil, fmt.Errorf("runner.json: %w", err)
	}
	return strings.Fields(runner.Landing.ReleaseCLI), nil
}

func (n *LandedNotLive) landings(ctx context.Context, now time.Time) (map[int]landing, error) {
	stdout, err := n.Env.Run(ctx, n.Env.tool("ledger.py"), "-C", n.Env.Config.Checkout, "list", "--drive", n.Env.Config.Drive, "--json")
	if err != nil {
		return nil, err
	}
	var rows []ledgerRow
	if err := json.Unmarshal([]byte(stdout), &rows); err != nil {
		return nil, fmt.Errorf("%w: ledger list: %w", ErrUnread, err)
	}
	found := map[int]landing{}
	for _, row := range rows {
		if row.State != kindLanded || row.LandedSHA == "" || row.LandedAt == "" {
			continue
		}
		at, err := time.Parse(time.RFC3339, row.LandedAt)
		if err != nil {
			return nil, fmt.Errorf("ledger row %s landed_at: %w", row.PR, err)
		}
		scope := titleScope.FindStringSubmatch(row.Title)
		if age := now.Sub(at); scope == nil || age < notLiveOwed || age >= notLiveSpan {
			continue
		}
		number, err := row.PR.Int64()
		if err != nil {
			return nil, fmt.Errorf("ledger row pr %q: %w", row.PR, err)
		}
		found[int(number)] = landing{strings.ToLower(scope[1]), row.LandedSHA, at}
	}
	return found, nil
}

func (n *LandedNotLive) running(ctx context.Context) (map[string][]string, error) {
	argv := append(slices.Clone(n.ReleaseCLI), "live", "--json")
	result, err := n.Env.Runner.Run(ctx, shell.Command{Argv: argv, Dir: n.Env.Config.Checkout, Timeout: readTimeout})
	if err != nil {
		return nil, fmt.Errorf("%w: %w", ErrUnread, err)
	}
	var entries []struct {
		Target string `json:"target"`
		Commit string `json:"commit"`
	}
	if err := json.Unmarshal([]byte(result.Stdout), &entries); err != nil {
		return nil, fmt.Errorf("%w: release live: %w", ErrUnread, err)
	}
	running := map[string][]string{}
	for _, entry := range entries {
		running[entry.Target] = append(running[entry.Target], entry.Commit)
	}
	return running, nil
}

func (n *LandedNotLive) holdsSHA(ctx context.Context, commit, sha string) bool {
	key := [2]string{commit, sha}
	if held, known := n.holds[key]; known {
		return held
	}
	_, err := n.Env.Runner.Run(ctx, shell.Command{Argv: []string{"git", "-C", n.Env.Config.Checkout, "merge-base", "--is-ancestor", sha, commit}, Timeout: readTimeout})
	var exit *shell.ExitError
	notAncestor := errors.As(err, &exit) && exit.Code == 1
	held := !notAncestor
	if n.holds == nil {
		n.holds = map[[2]string]bool{}
	}
	n.holds[key] = held
	return held
}

func (n *LandedNotLive) Check(ctx context.Context, now time.Time) error {
	if !n.read.IsZero() && now.Sub(n.read) < notLiveEvery {
		return nil
	}
	n.read = now
	landed, err := n.landings(ctx, now)
	if err != nil {
		n.Log.Warn("landed-not-live did not read the ledger", "err", err)
		return nil
	}
	owed := map[string][]int{}
	if len(landed) > 0 {
		running, err := n.running(ctx)
		if err != nil {
			n.Log.Warn("landed-not-live did not read the release", "err", err)
			return nil
		}
		for _, number := range slices.Sorted(maps.Keys(landed)) {
			entry := landed[number]
			commits, known := running[entry.target]
			if !known || slices.ContainsFunc(commits, func(commit string) bool { return n.holdsSHA(ctx, commit, entry.sha) }) {
				continue
			}
			owed[entry.target] = append(owed[entry.target], number)
		}
	}
	return n.announce(ctx, landed, owed, now)
}

func (n *LandedNotLive) announce(ctx context.Context, landed map[int]landing, owed map[string][]int, now time.Time) error {
	parts := make([]string, 0, len(owed))
	keys := map[string]bool{}
	for _, target := range slices.Sorted(maps.Keys(owed)) {
		numbers := owed[target]
		words := make([]string, 0, len(numbers))
		for _, number := range numbers {
			words = append(words, fmt.Sprintf("#%d (%s)", number, pacific(landed[number].at)))
		}
		parts = append(parts, target+": "+strings.Join(words, ", "))
		keys[notLiveKey(target, numbers)] = true
	}
	said := strings.Join(parts, "; ")
	bus, drive := n.Env.Bus, n.Env.Config.Drive
	if said != n.said {
		digest := sha256.Sum256([]byte(said))
		if _, err := bus.Post(ctx, client.Draft{Drive: drive, Lane: Self, Kind: "state", Topic: notLiveTopic, ID: notLiveTopic + ":" + hex.EncodeToString(digest[:])[:12], Text: "LANDED-NOT-LIVE " + or(said, "none"), To: []string{Root}}); err != nil {
			return fmt.Errorf("%w: cci post: %w", ErrUnread, err)
		}
		n.said = said
	}
	if n.owed == nil {
		n.owed = map[string]int64{}
	}
	for key, seq := range n.owed {
		if keys[key] {
			continue
		}
		if _, err := bus.Post(ctx, client.Draft{Drive: drive, Lane: Self, Kind: "note", Text: "LANDED-NOT-LIVE outlived: " + key, Resolves: seq}); err != nil {
			return fmt.Errorf("%w: cci post: %w", ErrUnread, err)
		}
		delete(n.owed, key)
	}
	cli := strings.Join(n.ReleaseCLI, " ")
	for _, target := range slices.Sorted(maps.Keys(owed)) {
		numbers := owed[target]
		key := notLiveKey(target, numbers)
		if _, posted := n.owed[key]; posted {
			continue
		}
		oldest := now
		for _, number := range numbers {
			if landed[number].at.Before(oldest) {
				oldest = landed[number].at
			}
		}
		stored, err := bus.Post(ctx, client.Draft{Drive: drive, Lane: Self, Kind: blockerKind, To: []string{Root}, ID: key, PRs: []string{strconv.Itoa(numbers[0])}, Stamp: true, Text: fmt.Sprintf("LANDED-NOT-LIVE %s: %s landed, the oldest %d minutes ago, and %s runs none of them; start its release: `%s start --only %s --manual`", target, numberList(numbers), int(now.Sub(oldest).Minutes()), target, cli, target)})
		if err != nil {
			return fmt.Errorf("%w: cci post: %w", ErrUnread, err)
		}
		n.owed[key] = stored.Seq
	}
	return nil
}

func notLiveKey(target string, numbers []int) string {
	words := make([]string, 0, len(numbers))
	for _, number := range numbers {
		words = append(words, strconv.Itoa(number))
	}
	return notLiveTopic + ":" + target + ":" + strings.Join(words, ",")
}

func (e Env) Checks(s *store.Store, log *slog.Logger) ([]Check, error) {
	var checks []Check
	var channels []string
	for channel := range strings.SplitSeq(e.Config.AlertChannels, ",") {
		if channel = strings.TrimSpace(channel); channel != "" {
			channels = append(channels, channel)
		}
	}
	if len(channels) > 0 {
		checks = append(checks, Alerts{Env: e, Store: s, Channels: channels, Log: log, Launch: e.incidentLaunch}.Check)
	}
	if pipelines := e.Config.ReleasePipelineSlugs(); len(pipelines) > 0 {
		checks = append(checks, (&ReleaseBuilds{Env: e, Pipelines: pipelines, CLI: strings.Fields(e.Config.BuildkiteCLI), Log: log}).Check)
	}
	if e.Config.OrcaRun != "" {
		checks = append(checks, (&FixLiveGC{Env: e, Log: log}).Check)
	}
	remote, err := CcRemoteOf(e.State)
	if err != nil {
		return nil, err
	}
	if e.Config.OrcaRun != "" && remote != nil {
		checks = append(checks, (&SpriteStops{Env: e, Store: s, CcRemote: *remote, Log: log}).Check)
	}
	cli, err := ReleaseCLI(e.State)
	if err != nil {
		return nil, err
	}
	if len(cli) > 0 {
		checks = append(checks, (&LandedNotLive{Env: e, ReleaseCLI: cli, Log: log}).Check, (&ReleaseGates{Env: e, ReleaseCLI: cli, Log: log}).Check)
	}
	return checks, nil
}
