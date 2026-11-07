package lanes

import (
	"cmp"
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
	"os/exec"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bkread"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/terminalcap"
)

const (
	alertWindow  = time.Hour
	alertJoin    = 24 * time.Hour
	alertRead    = 20
	threadRead   = 200
	fixLiveIdle  = 30 * time.Minute
	fixLiveBack  = 6 * time.Hour
	gcEvery      = 5 * time.Minute
	gcTimeout    = 10 * time.Minute
	staleSilence = 2 * time.Hour
	endedQuiet   = StaleAfter
	ReapTopic    = "session-reap"
	closedTopic  = "terminal-closed"
	stopTimeout  = 5 * time.Minute
	stopTries    = 3
	launchWait   = 45 * time.Second
	launchMargin = 10 * time.Second
	incidentBar  = 20 * time.Minute
	NotLiveOwed  = time.Hour
	NotLiveSpan  = 24 * time.Hour
	NotLiveEvery = 5 * time.Minute
	NotLiveTopic = "landed-not-live"
	notLiveLead  = "LANDED-NOT-LIVE "
	notLiveNone  = "none"
	blockerKind  = "blocker"
)

var (
	stopTransient  = regexp.MustCompile(`sprites api answered 5\d\d|timed out|deadline exceeded`)
	alertURL       = regexp.MustCompile(`app\.datadoghq\.com/monitors/(\d+)|sentry\.io/\S*?issues/(\d+)`)
	alertLink      = regexp.MustCompile(`https?://[\w.-]*(?:datadoghq\.com/monitors|sentry\.io/\S*?issues)/\d+`)
	slackMessageID = regexp.MustCompile(`/p\d{16}\b`)
	alertMessage   = regexp.MustCompile(`https://[\w.-]+/archives/[A-Z0-9]+/p\d{16}(?:\?[^\s|>)]*)?`)
	recoveredAlert = regexp.MustCompile(`(?i)^\W*(?:Recovered|Resolved)\b`)
	redWatch       = regexp.MustCompile(`The Watch failed for (.+?) on (\S+?): ("[^"]+"(?:, "[^"]+")*) crossed`)
	redSmoke       = regexp.MustCompile(`(\S+) is live, but (.+?) on (\S+?) went red`)
	redTarget      = regexp.MustCompile(`:rotating_light: (.+?) went red(?: on (.+?))? after `)
	watchRelease   = regexp.MustCompile(`Nothing else in (\S+) waits on it`)
	releaseAuthor  = regexp.MustCompile(`<@(U[A-Z0-9]+)>`)
	jobLink        = regexp.MustCompile(`<(https://buildkite\.com/[^|>]+)`)
	notSlug        = regexp.MustCompile(`[^a-z0-9]+`)
	templateField  = regexp.MustCompile(`(\$?)(\{+)(lane|incident|slug|links?|what|onset|reported|due|repo|drive|facts|parent|case|parent_brief|prs|log)(\}+)`)
)

type Check func(ctx context.Context, now time.Time) error

type slackMessage struct {
	TS          string `json:"ts"`
	User        string `json:"user"`
	FromClaude  bool   `json:"from_claude"`
	Text        string `json:"text"`
	Permalink   string `json:"permalink"`
	ReplyCount  int    `json:"reply_count"`
	LatestReply string `json:"latest_reply"`
}

type Alerts struct {
	Env      Env
	Store    *store.Store
	Channels []string
	Log      *slog.Logger
	Refused  []config.Refusal
	Launch   func(ctx context.Context, fired Fired) (string, error)
	threads  map[string]string
	launched map[string]bool
	opened   []client.Record
	isOpened bool
}

type Fired struct {
	Slug  string
	What  string
	Link  string
	At    time.Time
	Facts []string
	Issue string
}

func (f Fired) links() string {
	var kept []string
	for _, link := range slices.Concat([]string{f.Link, f.Issue}, alertMessage.FindAllString(f.What, -1), alertLink.FindAllString(f.What, -1)) {
		if link != "" && !slices.Contains(kept, link) {
			kept = append(kept, link)
		}
	}
	return strings.Join(kept, " ")
}

type alert struct {
	name  string
	slug  string
	facts []string
	red   string
}

func HandFired(slug, what string, now time.Time) Fired {
	return Fired{Slug: slug, What: what, Link: cmp.Or(alertMessage.FindString(what), alertLink.FindString(what)), At: now}
}

func FillIncidentBrief(template, lane, repo, drive string, fired Fired, now time.Time) string {
	reported := now.In(pacificZone)
	return strings.NewReplacer(
		"{{", "{", "}}", "}", "{lane}", lane, "{incident}", fired.Slug, "{slug}", fired.Slug, "{link}", fired.Link, "{links}", fired.links(), "{what}", fired.What,
		"{onset}", pacific(fired.At), "{reported}", reported.Format("15:04"), "{due}", reported.Add(incidentBar).Format("15:04"),
		"{repo}", repo, "{drive}", drive, "{facts}", strings.Join(append([]string{"drive " + drive}, fired.Facts...), "\n"),
	).Replace(template)
}

func UnfilledFields(text string) []string {
	var fields []string
	for _, match := range templateField.FindAllStringSubmatch(text, -1) {
		field := "{" + match[3] + "}"
		if match[1] == "" && match[2] == "{" && match[4] == "}" && !slices.Contains(fields, field) {
			fields = append(fields, field)
		}
	}
	return fields
}

func (e Env) incidentBrief(lane, template string, fired Fired) (string, error) {
	text, err := os.ReadFile(filepath.Join(e.plugin(), "skills", "long-running", "reference", template))
	if errors.Is(err, fs.ErrNotExist) {
		text, err = os.ReadFile(filepath.Join(e.State, template))
	}
	if err != nil {
		return "", err
	}
	rendered := FillIncidentBrief(string(text), lane, e.Config.Checkout, e.Config.Drive, fired, e.Now())
	dir := filepath.Join(e.State, "lanes")
	if err := os.MkdirAll(dir, 0o750); err != nil {
		return "", err
	}
	path := filepath.Join(dir, lane+".brief.md")
	return path, os.WriteFile(path, []byte(rendered), 0o600)
}

func (e Env) IncidentLaunch(ctx context.Context, fired Fired) (string, error) {
	fix, err := e.incidentBrief(fired.Slug+"-fix", "alert-fix-brief.md", fired)
	if err != nil {
		return "", err
	}
	evidence, err := e.incidentBrief(fired.Slug+"-evidence", "alert-evidence-brief.md", fired)
	if err != nil {
		return "", err
	}
	comms, err := e.incidentBrief(fired.Slug+"-comms", "alert-comms-brief.md", fired)
	if err != nil {
		return "", err
	}
	argv := []string{e.tool("lr"), "incident", "run", fired.Slug, "--fix-brief", fix, "--evidence-brief", evidence, "--comms-brief", comms, "--timeout-seconds", strconv.Itoa(int((launchWait - launchMargin).Seconds())), "--drive", e.Config.Drive, "--lane", Self}
	_, err = e.Runner.Run(ctx, shell.Command{Argv: argv, Timeout: launchWait})
	return fix, err
}

func (a *Alerts) Check(ctx context.Context, now time.Time) error {
	start := time.Now()
	defer func() {
		if took := time.Since(start); took >= time.Minute {
			a.Log.Warn("alert pass slow", "took", took.Round(time.Second))
		}
	}()
	a.isOpened = false
	threads := map[string]string{}
	for _, channel := range a.Channels {
		var history struct {
			Messages []slackMessage `json:"messages"`
		}
		if !a.read(ctx, &history, "history", "--channel", channel, "--json", "--limit", strconv.Itoa(alertRead)) {
			continue
		}
		for _, card := range history.Messages {
			if card.FromClaude {
				continue
			}
			if err := a.raise(ctx, channel, card, card, now); err != nil {
				return err
			}
			if card.ReplyCount == 0 {
				continue
			}
			latest, err := firedAt(card.LatestReply)
			if err != nil {
				return err
			}
			if now.Sub(latest) >= alertWindow {
				continue
			}
			key, moved := channel+":"+card.TS, strconv.Itoa(card.ReplyCount)+" "+card.LatestReply
			if a.threads[key] == moved {
				threads[key] = moved
				continue
			}
			var thread struct {
				Messages []slackMessage `json:"messages"`
			}
			if !a.read(ctx, &thread, "thread", "--url", card.Permalink, "--limit", strconv.Itoa(threadRead)) {
				continue
			}
			threads[key] = moved
			for _, reply := range thread.Messages {
				if reply.TS == card.TS || reply.FromClaude || reply.User != card.User {
					continue
				}
				if err := a.raise(ctx, channel, card, reply, now); err != nil {
					return err
				}
			}
		}
	}
	a.threads = threads
	return nil
}

func (a *Alerts) read(ctx context.Context, into any, argv ...string) bool {
	stdout, err := a.Env.Run(ctx, append([]string{"cc-slack"}, argv...)...)
	if err == nil {
		err = json.Unmarshal([]byte(stdout), into)
	}
	if err != nil {
		a.Log.Warn("alert channel did not read", "read", argv[0], "err", err)
	}
	return err == nil
}

func alertOf(card, message slackMessage) (alert, bool) {
	if recoveredAlert.MatchString(message.Text) {
		return alert{}, false
	}
	if found := alertURL.FindStringSubmatch(message.Text); found != nil {
		if found[1] != "" {
			return alert{name: found[1], slug: "dd-" + found[1]}, true
		}
		return alert{name: found[2], slug: "sentry-" + found[2]}, true
	}
	author, job := submatch(releaseAuthor, message.Text+"\n"+card.Text), submatch(jobLink, message.Text)
	if found := redWatch.FindStringSubmatch(message.Text); found != nil {
		monitors := strings.ReplaceAll(strings.Trim(found[3], `"`), `", "`, ", ")
		monitor := sha256.Sum256([]byte(monitors))
		slug := slugOf("watch", found[1], found[2]) + "-" + hex.EncodeToString(monitor[:3])
		return alert{slug, slug, factsOf("component", found[1], "env", found[2], "monitor", monitors, "release", submatch(watchRelease, message.Text), "released by", author, "job", job), slugOf("red", found[1])}, true
	}
	if found := redSmoke.FindStringSubmatch(message.Text); found != nil {
		slug := slugOf("smoke", found[2], found[3])
		return alert{slug, slug, factsOf("component", found[2], "env", found[3], "monitor", found[2], "release", found[1], "released by", author, "job", job), slugOf("red", found[2])}, true
	}
	if found := redTarget.FindStringSubmatch(message.Text); found != nil {
		slug := slugOf("red", found[1])
		return alert{slug, slug, factsOf("component", found[1], "env", found[2], "released by", author, "job", job), slug}, true
	}
	return alert{}, false
}

func submatch(pattern *regexp.Regexp, text string) string {
	if found := pattern.FindStringSubmatch(text); found != nil {
		return found[1]
	}
	return ""
}

func slugOf(kind string, parts ...string) string {
	return kind + "-" + strings.Trim(notSlug.ReplaceAllString(strings.ToLower(strings.Join(parts, " ")), "-"), "-")
}

func factsOf(pairs ...string) []string {
	var facts []string
	for pair := range slices.Chunk(pairs, 2) {
		if pair[1] != "" {
			facts = append(facts, pair[0]+" "+pair[1])
		}
	}
	return facts
}

func namedFor(fired alert, message slackMessage) string {
	if link := alertLink.FindString(message.Text); link != "" {
		return link
	}
	return fired.name
}

func firedAt(ts string) (time.Time, error) {
	seconds, err := strconv.ParseFloat(ts, 64)
	if err != nil {
		return time.Time{}, fmt.Errorf("slack ts %q: %w", ts, err)
	}
	return time.Unix(0, int64(seconds*float64(time.Second))).UTC(), nil
}

func (a *Alerts) raise(ctx context.Context, channel string, card, message slackMessage, now time.Time) error {
	alert, ok := alertOf(card, message)
	if !ok {
		return nil
	}
	if refusal, _, refused := config.RefusedLine(a.Refused, card.Text+"\n"+message.Text); refused {
		a.Log.Debug("alert on a refused topic dropped", "alert", alert.slug, "why", refusal.Why)
		return nil
	}
	fired, err := firedAt(message.TS)
	if err != nil {
		return err
	}
	if now.Sub(fired) >= alertWindow {
		return nil
	}
	headline, _, _ := strings.Cut(message.Text, "\n")
	headline = truncated(strings.Join(strings.Fields(headline), " "), 160)
	minutes := int(now.Sub(fired).Minutes())
	draft := client.Draft{Drive: a.Env.Config.Drive, Lane: Self, To: []string{Root}, URL: card.Permalink}
	lane, found, err := a.Store.Lane(alert.slug + "-fix")
	if err == nil && !found {
		lane, found, err = a.Store.Lane(alert.red + "-fix")
	}
	if err != nil || found && (lane.State == store.LaneRetired || now.Sub(lane.Since) >= alertJoin) {
		return err
	}
	if found {
		first, err := a.Store.Notice("alert:"+channel+":"+message.TS, now)
		if err != nil || !first {
			return err
		}
		draft.Kind, draft.ID, draft.To = "note", "alert-joined:"+channel+":"+message.TS, []string{lane.Name}
		draft.Text = fmt.Sprintf("ALERT-AGAIN %s: %s fired again %d min ago, so the supervisor told %s and launched nothing new", headline, namedFor(alert, message), minutes, lane.Name)
		if _, err := a.Env.Bus.Post(ctx, draft); err != nil {
			return fmt.Errorf("%w: cci post: %w", ErrUnread, err)
		}
		return nil
	}
	if a.launched[alert.slug] || a.launched[alert.red] {
		return nil
	}
	if !a.isOpened {
		if a.opened, err = a.Env.Bus.Tail(ctx, client.Query{Drive: a.Env.Config.Drive, Since: now.Add(-alertWindow), Kinds: []string{"incident", kindSpawn}}); err != nil {
			return fmt.Errorf("%w: cci tail incident: %w", ErrUnread, err)
		}
		a.isOpened = true
	}
	thread := slackMessageID.FindString(card.Permalink)
	for _, incident := range a.opened {
		onThread := thread != "" && (strings.Contains(incident.Text, thread) || strings.Contains(incident.Refs.URL, thread))
		if onThread || !incident.At.Before(fired) && strings.Contains(incident.Text, alert.name) {
			return nil
		}
	}
	first, err := a.Store.Notice("alert:"+channel+":"+message.TS, now)
	if err != nil || !first {
		return err
	}
	ctx = context.WithoutCancel(ctx)
	holder, launchErr := a.Env.MonitorLane(ctx, alert.name, alert.slug+"-fix")
	if holder != "" {
		return nil
	}
	var brief string
	if launchErr == nil {
		brief, launchErr = a.Launch(ctx, Fired{alert.slug, headline, card.Permalink, fired, alert.facts, alertLink.FindString(message.Text)})
	}
	if launchErr != nil {
		draft.Kind, draft.ID = blockerKind, "alert-unopened:"+channel+":"+message.TS
		failure := []rune(launchErr.Error())
		draft.Text = fmt.Sprintf("ALERT-UNOPENED %s: no incident record names %s %d min after it fired, and the supervisor's launch of %s-fix failed: %s | coordinator: launch its fix lane this turn with `lr incident run %s --fix-brief <file>`; alert message %s", headline, namedFor(alert, message), minutes, alert.slug, string(failure[max(0, len(failure)-200):]), alert.slug, card.Permalink)
	} else {
		if a.launched == nil {
			a.launched = map[string]bool{}
		}
		a.launched[alert.slug] = true
		if alert.red != "" {
			a.launched[alert.red] = true
		}
		a.opened = append(a.opened, client.Record{Kind: kindSpawn, Text: card.Permalink})
		draft.Kind, draft.ID, draft.To = kindSpawn, "alert-launched:"+channel+":"+message.TS, []string{Root, OwnerRelay}
		draft.LaneRefs = []string{alert.slug + "-fix"}
		draft.Attrs = map[string]string{"brief": brief, "role": "fix", "model": "incident", "effort": "high"}
		draft.Text = fmt.Sprintf("ALERT-LAUNCHED %s: no incident record named %s %d min after it fired, so the supervisor launched %s-fix and %s-evidence; alert message %s", headline, namedFor(alert, message), minutes, alert.slug, alert.slug, card.Permalink)
	}
	if _, err := a.Env.Bus.Post(ctx, draft); err != nil {
		return fmt.Errorf("%w: cci post: %w", ErrUnread, err)
	}
	return nil
}

var fixLiveLine = regexp.MustCompile(`^(\d{1,2}:\d{2} [AP]M )?FIX-LIVE\b`)

type FixLiveGC struct {
	Env       Env
	Store     *store.Store
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
		if !announcesFixLive(row) || now.Sub(row.At) < fixLiveIdle {
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

func announcesFixLive(row client.Record) bool {
	return row.Kind == "fix-live" || fixLiveLine.MatchString(row.Text)
}

func (g *FixLiveGC) reclaim(ctx context.Context, lane string, seq int64, heard, now time.Time) error {
	dispatch := dispatchOf(g.Env.receipts(), lane)
	if dispatch == "" || g.reclaimed[dispatch] || now.Sub(heard) < fixLiveIdle {
		return nil
	}
	if owns, err := g.ownsWork(lane); owns || err != nil {
		return err
	}
	if g.reclaimed == nil {
		g.reclaimed = map[string]bool{}
	}
	g.reclaimed[dispatch] = true
	result, err := g.Env.gc(ctx, "--done", dispatch)
	var exit *shell.ExitError
	if err != nil && !errors.As(err, &exit) {
		g.Log.Warn("lr gc did not run", "dispatch", dispatch, "err", err)
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

func (g *FixLiveGC) ownsWork(name string) (bool, error) {
	lane, found, err := g.Store.Lane(name)
	if err != nil || !found {
		return false, err
	}
	brief, err := BriefOf(lane.Brief)
	return len(lane.PRs) > 0 || len(brief.Orders) > 0 || isStanding(lane), err
}

type CcRemote struct {
	Binary string
	Config string
}

func CcRemoteOf(state, plugin string) (*CcRemote, error) {
	data, err := drive.ReadMacOnly(filepath.Join(state, "runner.json"))
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
	config := or(env["CC_REMOTE_CONFIG"], filepath.Join(env["WORKER_CHECKOUT"], "tools", "cc-remote", "config.yaml"))
	return &CcRemote{Binary: filepath.Join(plugin, "bin", "cc-remote"), Config: config}, nil
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
	open, err := c.Env.openDispatches(ctx)
	if err != nil {
		c.Log.Warn("sprite stops did not read the Run", "err", err)
		return nil
	}
	why := map[string]string{}
	for _, lane := range hosted {
		if reason := endedBecause(lane, receiptOf(c.Env.receipts(), lane.Name), open); reason != "" {
			why[lane.Name] = reason
		}
	}
	if len(why) == 0 {
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
	var running map[string]bool
	var unlisted error
	if slices.ContainsFunc(stopped, func(err error) bool { return err != nil }) {
		running, unlisted = c.Env.sprites(ctx)
	}
	for i, lane := range ended {
		sprite := spriteOf(c.Env.receipts(), lane)
		switch {
		case stopped[i] == nil:
			c.Log.Info("sprite stopped", "lane", lane, "why", why[lane])
			err = c.closeBlocker(ctx, lane, fmt.Sprintf("SPRITE-STOPPED %s: the stop of its Sprite %s went through", lane, sprite))
		case unlisted == nil && !running[sprite]:
			c.Log.Info("sprite is already gone, so its failed stop closes", "lane", lane, "sprite", sprite, "err", stopped[i])
			err = c.closeBlocker(ctx, lane, fmt.Sprintf("SPRITE-STOPPED %s: `sprite list` no longer shows its Sprite %s", lane, sprite))
		default:
			err = c.failed(ctx, lane, sprite, why[lane], stopped[i], unlisted)
		}
		if err != nil {
			return err
		}
	}
	return nil
}

func (c *SpriteStops) failed(ctx context.Context, lane, sprite, why string, stop error, unlisted error) error {
	transient := errors.Is(stop, context.DeadlineExceeded) || stopTransient.MatchString(stop.Error())
	failure, err := c.Store.FailSpriteStop(lane, stopTries, transient)
	if err != nil || failure.Left {
		return err
	}
	if failure.Retries {
		c.Log.Warn("sprite stop failed, the next pass tries again", "lane", lane, "passes", failure.Passes, "err", stop)
		if !transient || failure.Blocker != 0 || failure.Passes < stopTries {
			return nil
		}
	}
	to, err := MetaDesk(c.Store)
	if err != nil {
		return err
	}
	state := fmt.Sprintf("`sprite list` shows its Sprite %s still there", sprite)
	if unlisted != nil {
		state = fmt.Sprintf("`sprite list` failed too, so its Sprite %s may still run: %s", sprite, truncated(unlisted.Error(), 300))
	}
	id := "sprite-stop:" + lane
	text := fmt.Sprintf("SPRITE-STOP-FAILED %s: %s, so the supervisor ran `cc-remote orca stop %s` %d times, and the last failed: %s | %s, and the supervisor does not try again; stop it by hand with `%s orca stop %s --config %s`", lane, why, lane, failure.Passes, truncated(stop.Error(), 300), state, c.CcRemote.Binary, lane, c.CcRemote.Config)
	if failure.Retries {
		id = "sprite-stop-retrying:" + lane
		text = fmt.Sprintf("SPRITE-STOP-FAILED %s: %s, so the supervisor ran `cc-remote orca stop %s` on %d passes in a row, and each failed, the last with: %s | %s. The supervisor tries again every pass until it stops or is gone, and posts one closing line; to leave it, run `lr stop-sprites --leave %s`", lane, why, lane, failure.Passes, truncated(stop.Error(), 300), state, lane)
	}
	stored, err := c.Env.Bus.Post(ctx, client.Draft{Drive: c.Env.Config.Drive, Lane: Self, Kind: blockerKind, To: []string{to}, ID: id, Resolves: failure.Blocker, Text: text})
	if err != nil {
		return fmt.Errorf("%w: cci post: %w", ErrUnread, err)
	}
	return c.Store.SpriteStopBlocked(lane, stored.Seq)
}

func (c *SpriteStops) closeBlocker(ctx context.Context, lane, text string) error {
	blocker, err := c.Store.SpriteStopBlocker(lane)
	if err != nil || blocker == 0 {
		return err
	}
	return c.postClose(ctx, lane, blocker, text)
}

func (c *SpriteStops) postClose(ctx context.Context, lane string, blocker int64, text string) error {
	to, err := MetaDesk(c.Store)
	if err != nil {
		return err
	}
	if _, err := c.Env.Bus.Post(ctx, client.Draft{Drive: c.Env.Config.Drive, Lane: Self, Kind: "note", To: []string{to}, ID: "sprite-stop-closed:" + lane, Resolves: blocker, Text: text}); err != nil {
		return fmt.Errorf("%w: cci post: %w", ErrUnread, err)
	}
	return nil
}

func (c *SpriteStops) Leave(ctx context.Context, lane string, now time.Time) error {
	blocker, err := c.Store.LeaveSpriteStop(lane, now)
	if err != nil || blocker == 0 {
		return err
	}
	return c.postClose(ctx, lane, blocker, fmt.Sprintf("SPRITE-STOP-LEFT %s: a person said to leave its Sprite, so the supervisor stops trying", lane))
}

func (e Env) sprites(ctx context.Context) (map[string]bool, error) {
	result, err := e.Runner.Run(ctx, shell.Command{Argv: []string{"sprite", "list"}, Timeout: readTimeout})
	if err != nil {
		return nil, err
	}
	running := map[string]bool{}
	for _, sprite := range strings.Fields(result.Stdout) {
		running[sprite] = true
	}
	return running, nil
}

type openDispatches struct {
	dispatches, terminals map[string]bool
}

func (e Env) openDispatches(ctx context.Context) (openDispatches, error) {
	stdout, err := e.Run(ctx, "orca", "orchestration", "task-list", "--run", e.Config.OrcaRun, "--status", "dispatched", "--brief", "--json")
	if err != nil {
		return openDispatches{}, err
	}
	var listed struct {
		OK     bool `json:"ok"`
		Error  any  `json:"error"`
		Result struct {
			Tasks []struct {
				Dispatch string `json:"dispatch_id"`
				Assignee string `json:"assignee_handle"`
			} `json:"tasks"`
		} `json:"result"`
	}
	if err := json.Unmarshal([]byte(stdout), &listed); err != nil {
		return openDispatches{}, fmt.Errorf("%w: orca task-list: %w", ErrUnread, err)
	}
	if !listed.OK {
		return openDispatches{}, fmt.Errorf("%w: orca task-list: %v", ErrUnread, listed.Error)
	}
	open := openDispatches{dispatches: map[string]bool{}, terminals: map[string]bool{}}
	for _, task := range listed.Result.Tasks {
		open.dispatches[task.Dispatch] = true
		if task.Assignee != "" {
			open.terminals[task.Assignee] = true
		}
	}
	return open, nil
}

func endedBecause(lane store.Lane, receipt launchReceipt, open openDispatches) string {
	dispatch := receipt.Result.DispatchID
	switch {
	case open.dispatches[dispatch] || open.terminals[receipt.agentTerminal()]:
		return ""
	case dispatch != "":
		return fmt.Sprintf("its dispatch %s is no longer open", dispatch)
	case lane.RetiredBy == "done" || lane.RetiredBy == "withdrawn":
		return "it was retired by " + lane.RetiredBy
	}
	return ""
}

type ledgerRow struct {
	PR        json.Number `json:"pr"`
	State     string      `json:"state"`
	LandedSHA string      `json:"landed_sha"`
	LandedAt  string      `json:"landed_at"`
}

type landing struct {
	targets []string
	sha     string
	at      time.Time
}

type LandedNotLive struct {
	Env        Env
	Store      *store.Store
	ReleaseCLI []string
	Token      bkread.Token
	Builds     *ReleaseBuilds
	Log        *slog.Logger
	read       time.Time
	holds      map[[2]string]bool
	touched    map[string][]string
	said       string
	blockers   map[string]int64
	rows       map[string]string
}

type ownerPick struct {
	Kind    string `json:"kind"`
	Block   string `json:"block"`
	URL     string `json:"url"`
	Release string `json:"release"`
}

type RunnerLanding struct {
	ReleaseCLI     string   `json:"release_cli"`
	Ledger         string   `json:"ledger"`
	TrunkCheck     string   `json:"trunk_check"`
	CIBuild        string   `json:"ci_build"`
	CITokenCommand []string `json:"ci_token_command"`
	FlakeLane      string   `json:"flake_lane"`
	FlakyChannel   string   `json:"flaky_channel"`
	MacQueue       MacQueue `json:"mac_queue"`
	Pages          struct {
		Channels []string `json:"channels"`
		Build    string   `json:"build"`
	} `json:"pages"`
}

func ReadRunnerLanding(state string) (RunnerLanding, error) {
	data, err := drive.ReadMacOnly(filepath.Join(state, "runner.json"))
	if errors.Is(err, fs.ErrNotExist) {
		return RunnerLanding{}, nil
	}
	if err != nil {
		return RunnerLanding{}, err
	}
	var runner struct {
		Landing RunnerLanding `json:"landing"`
	}
	if err := json.Unmarshal(data, &runner); err != nil {
		return RunnerLanding{}, fmt.Errorf("runner.json: %w", err)
	}
	return runner.Landing, nil
}

func (e Env) dropClasses(ctx context.Context, log *slog.Logger) []config.Refusal {
	listing, err := e.Run(ctx, "ccn", "-R", e.Config.Checkout, "answer", "list", "--label", config.DropClassLabel, "--limit", "0", "--json")
	var refusals []config.Refusal
	if err == nil {
		refusals, err = config.RefusalsOf(listing)
	}
	if err != nil {
		log.Warn("his drop classes did not read, so no alert or page is dropped", "err", err)
	}
	return refusals
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
	if n.touched == nil {
		if n.touched, err = n.Store.ReleaseTargets(); err != nil {
			return nil, err
		}
	}
	found := map[int]landing{}
	window := map[string]bool{}
	for _, row := range rows {
		if row.State != kindLanded || row.LandedSHA == "" || row.LandedAt == "" {
			continue
		}
		at, err := time.Parse(time.RFC3339, row.LandedAt)
		if err != nil {
			return nil, fmt.Errorf("ledger row %s landed_at: %w", row.PR, err)
		}
		if age := now.Sub(at); age < NotLiveOwed || age >= NotLiveSpan {
			continue
		}
		number, err := row.PR.Int64()
		if err != nil {
			return nil, fmt.Errorf("ledger row pr %q: %w", row.PR, err)
		}
		targets, err := n.targetsOf(ctx, row.LandedSHA, at)
		if err != nil {
			return nil, err
		}
		window[row.LandedSHA] = true
		found[int(number)] = landing{targets, row.LandedSHA, at}
	}
	maps.DeleteFunc(n.touched, func(sha string, _ []string) bool { return !window[sha] })
	return found, n.Store.DropReleaseTargets(now.Add(-NotLiveSpan))
}

func (n *LandedNotLive) targetsOf(ctx context.Context, sha string, landed time.Time) ([]string, error) {
	if targets, known := n.touched[sha]; known {
		return targets, nil
	}
	paths, err := changedPaths(ctx, n.Env.Runner, n.Env.Config.Checkout, sha)
	if err != nil {
		return nil, err
	}
	targets, err := touchedBy(ctx, n.Env.Runner, n.ReleaseCLI, n.Env.Config.Checkout, paths)
	if err != nil {
		return nil, err
	}
	n.touched[sha] = targets
	return targets, n.Store.PutReleaseTargets(sha, targets, landed)
}

func (n *LandedNotLive) running(ctx context.Context) (map[string][]string, error) {
	deploys, err := readDeploys(ctx, n.Env.Runner, &n.Token, n.ReleaseCLI, n.Env.Config.Checkout)
	if err != nil {
		return nil, err
	}
	running := map[string][]string{}
	for _, deploy := range deploys {
		running[deploy.Target] = append(running[deploy.Target], deploy.Commit)
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
	if !n.read.IsZero() && now.Sub(n.read) < NotLiveEvery {
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
		withheld, err := n.withheld(ctx, now)
		if err != nil {
			n.Log.Warn("landed-not-live did not read the holds", "err", err)
			return nil
		}
		releasing, err := n.releasing(ctx)
		if err != nil {
			n.Log.Warn("landed-not-live did not read the release builds", "err", err)
			return nil
		}
		carried, err := n.carried(ctx)
		if err != nil {
			n.Log.Warn("landed-not-live did not read the releases", "err", err)
			return nil
		}
		for _, number := range slices.Sorted(maps.Keys(landed)) {
			entry := landed[number]
			for _, target := range entry.targets {
				commits, known := running[target]
				if !known || withheld[target] || slices.ContainsFunc(slices.Concat(commits, releasing[target], carried[target]), func(commit string) bool { return n.holdsSHA(ctx, commit, entry.sha) }) {
					continue
				}
				owed[target] = append(owed[target], number)
			}
		}
	}
	gates := map[string]ownerPick{}
	if len(owed) > 0 {
		picks, err := n.picks(ctx)
		if err != nil {
			n.Log.Warn("landed-not-live did not read his open picks", "err", err)
			return nil
		}
		for target := range owed {
			if i := slices.IndexFunc(picks, func(pick ownerPick) bool { return pick.Release == target }); i >= 0 {
				gates[target] = picks[i]
			}
		}
	}
	return n.announce(ctx, landed, owed, gates, now)
}

func (n *LandedNotLive) picks(ctx context.Context) ([]ownerPick, error) {
	stdout, err := n.Env.Run(ctx, n.Env.tool("lr"), "board", "waits", "--drive", n.Env.Config.Drive, "--json")
	if err != nil {
		return nil, err
	}
	var waits []ownerPick
	if err := json.Unmarshal([]byte(stdout), &waits); err != nil {
		return nil, fmt.Errorf("%w: lr board waits: %w", ErrUnread, err)
	}
	return slices.DeleteFunc(waits, func(wait ownerPick) bool { return wait.Kind != "pick" }), nil
}

func (n *LandedNotLive) releasing(ctx context.Context) (map[string][]string, error) {
	releasing := map[string][]string{}
	if n.Builds == nil {
		return releasing, nil
	}
	builds, err := n.Builds.builds(ctx)
	if err != nil {
		return nil, fmt.Errorf("%w: %w", ErrUnread, err)
	}
	for _, build := range builds {
		if build.mode == dryRunMode || slices.Contains(buildFailed, build.State) || slices.Contains(buildClosed, build.State) {
			continue
		}
		for _, job := range build.Jobs {
			stack, applies := strings.CutPrefix(job.StepKey, "apply-")
			component, _, addressed := strings.Cut(stack, "--")
			if applies && addressed && !slices.Contains(jobStopped, job.State) {
				releasing[component] = append(releasing[component], build.Commit)
			}
		}
	}
	return releasing, nil
}

func (n *LandedNotLive) carried(ctx context.Context) (map[string][]string, error) {
	cuts, err := readCuts(ctx, n.Env.Runner, &n.Token, n.ReleaseCLI, n.Env.Config.Checkout)
	if err != nil {
		return nil, err
	}
	carried := map[string][]string{}
	for _, cut := range cuts {
		if cut.Mode == dryRunMode {
			continue
		}
		for _, stack := range cut.Unchanged {
			carried[stack.Component] = append(carried[stack.Component], cut.Commit)
		}
		if !slices.Contains([]string{releaseRunning, releaseQueued, releaseHeld}, cut.Status) {
			continue
		}
		for _, component := range cut.Components {
			carried[component] = append(carried[component], cut.Commit)
		}
	}
	return carried, nil
}

func (n *LandedNotLive) withheld(ctx context.Context, now time.Time) (map[string]bool, error) {
	records, err := n.Env.Bus.Open(ctx, n.Env.Config.Drive, "hold")
	if err != nil {
		return nil, fmt.Errorf("%w: cci open holds: %w", ErrUnread, err)
	}
	withheld := map[string]bool{}
	for _, open := range records {
		hold := recordOf(open)
		if hold.Lane != Root {
			continue
		}
		if until := hold.Attr("until"); until != "" {
			end, err := time.Parse(time.RFC3339, until)
			if err != nil {
				return nil, fmt.Errorf("hold %s until: %w", hold.Name(), err)
			}
			if !now.Before(end) {
				continue
			}
		}
		for _, target := range hold.Refs.Targets {
			withheld[target] = true
		}
	}
	return withheld, nil
}

func (n *LandedNotLive) announce(ctx context.Context, landed map[int]landing, owed map[string][]int, gates map[string]ownerPick, now time.Time) error {
	said, _, _ := notLiveParts(landed, owed, now)
	rows := map[ownerPick]map[string][]int{}
	routes := make([]string, 0, len(owed))
	for _, target := range slices.Sorted(maps.Keys(owed)) {
		pick := gates[target]
		if rows[pick] == nil {
			rows[pick] = map[string][]int{}
		}
		rows[pick][target] = owed[target]
		routes = append(routes, target+">"+pick.Block)
	}
	routed := strings.Join(append(routes, said), "\n")
	if routed == n.said {
		return nil
	}
	bus, drive := n.Env.Bus, n.Env.Config.Drive
	digest := sha256.Sum256([]byte(routed))
	key := NotLiveTopic + ":" + hex.EncodeToString(digest[:])[:12]
	if _, err := bus.Post(ctx, client.Draft{Drive: drive, Lane: Self, Kind: "state", Topic: NotLiveTopic, ID: key, Text: notLiveLead + or(said, notLiveNone), To: []string{Root}}); err != nil {
		return fmt.Errorf("%w: cci post: %w", ErrUnread, err)
	}
	kept, rowsSaid := map[string]int64{}, map[string]string{}
	for _, pick := range slices.SortedFunc(maps.Keys(rows), func(a, b ownerPick) int { return strings.Compare(a.Block, b.Block) }) {
		rowSaid, prs, oldest := notLiveParts(landed, rows[pick], now)
		rowsSaid[pick.Block] = rowSaid
		if rowSaid == n.rows[pick.Block] {
			kept[pick.Block] = n.blockers[pick.Block]
			continue
		}
		facts := sha256.Sum256([]byte(pick.Block + "\n" + rowSaid))
		id := NotLiveTopic + ":" + or(pick.Block, Root) + ":" + hex.EncodeToString(facts[:])[:12]
		minutes := int(now.Sub(oldest).Minutes())
		draft := client.Draft{Drive: drive, Lane: Self, Kind: blockerKind, To: []string{Root}, ID: id, PRs: prs, Resolves: n.blockers[pick.Block], Text: fmt.Sprintf("LANDED-NOT-LIVE %s. The oldest landed %d minutes ago, and no live release, running release build or release that found its target unchanged carries them; each ships with the next release that picks its target, and the owner starts that release", rowSaid, minutes)}
		if pick.Block != "" {
			draft.To, draft.Attrs = []string{OwnerRelay}, map[string]string{"card": pick.Block}
			draft.Text = fmt.Sprintf("LANDED-NOT-LIVE %s. The oldest landed %d minutes ago, and they ship only with the release his open pick on %s starts (%s); this row is that card's, so root has nothing to cut", rowSaid, minutes, pick.Block, pick.URL)
		}
		stored, err := bus.Post(ctx, draft)
		if err != nil {
			return fmt.Errorf("%w: cci post: %w", ErrUnread, err)
		}
		kept[pick.Block] = stored.Seq
	}
	for _, card := range slices.Sorted(maps.Keys(n.blockers)) {
		if _, still := kept[card]; still {
			continue
		}
		if _, err := bus.Post(ctx, client.Draft{Drive: drive, Lane: Self, Kind: "note", Text: "LANDED-NOT-LIVE outlived", Resolves: n.blockers[card]}); err != nil {
			return fmt.Errorf("%w: cci post: %w", ErrUnread, err)
		}
	}
	n.said, n.blockers, n.rows = routed, kept, rowsSaid
	return nil
}

func notLiveParts(landed map[int]landing, owed map[string][]int, now time.Time) (string, []string, time.Time) {
	parts := make([]string, 0, len(owed))
	var prs []string
	oldest := now
	for _, target := range slices.Sorted(maps.Keys(owed)) {
		numbers := owed[target]
		words := make([]string, 0, len(numbers))
		for _, number := range numbers {
			words = append(words, fmt.Sprintf("#%d", number))
			if pr := strconv.Itoa(number); !slices.Contains(prs, pr) {
				prs = append(prs, pr)
			}
			if landed[number].at.Before(oldest) {
				oldest = landed[number].at
			}
		}
		parts = append(parts, target+": "+strings.Join(words, ", "))
	}
	return strings.Join(parts, "; "), prs, oldest
}

func NotLiveOf(text string) (map[int][]string, bool) {
	said, found := strings.CutPrefix(text, notLiveLead)
	if !found {
		return nil, false
	}
	owed := map[int][]string{}
	if said == notLiveNone {
		return owed, true
	}
	for part := range strings.SplitSeq(said, "; ") {
		target, numbers, _ := strings.Cut(part, ": ")
		for word := range strings.SplitSeq(numbers, ", ") {
			if number, err := strconv.Atoi(strings.TrimPrefix(word, "#")); err == nil {
				owed[number] = append(owed[number], target)
			}
		}
	}
	return owed, true
}

func (e Env) gc(ctx context.Context, args ...string) (shell.Result, error) {
	remote, err := CcRemoteOf(e.State, e.plugin())
	if err != nil {
		return shell.Result{}, err
	}
	command := shell.Command{Argv: append([]string{e.tool("lr"), "gc", "--run", e.Config.OrcaRun}, args...), Dir: e.Config.Checkout, Timeout: gcTimeout}
	if remote != nil {
		command.Env = []string{"WORKER_CC_REMOTE=" + remote.Binary, "CC_REMOTE_CONFIG=" + remote.Config}
	}
	return e.Runner.Run(ctx, command)
}

func (e Env) keepWanted(s *store.Store) ([]string, error) {
	registered, err := s.AllLanes()
	if err != nil {
		return nil, err
	}
	names := make(map[string]bool, len(registered))
	succeeded := map[string]bool{}
	for _, lane := range registered {
		names[lane.Name] = true
		succeeded[lane.Predecessor] = true
	}
	var keep []string
	for _, lane := range registered {
		wanted := isLive(lane.State) || lane.State == store.LaneHeld
		if lane.State == store.LaneRetired && !succeeded[lane.Name] && (lane.RetiredBy == Root || !names[lane.RetiredBy]) {
			wanted = slices.Contains(standingRoles, lane.Role) || lane.Standing != ""
			if !wanted {
				brief, err := BriefOf(lane.Brief)
				wanted = err != nil || brief.Standing != ""
			}
		}
		if !wanted {
			continue
		}
		if dispatch := dispatchOf(e.receipts(), lane.Name); dispatch != "" {
			keep = append(keep, "--keep", dispatch)
		}
	}
	return keep, nil
}

type staleLane struct {
	name, dispatch, terminal, run, last, retiredBy, brief string
	heard                                                 time.Time
	ended                                                 bool
}

func (e Env) staleRetired(ctx context.Context, s *store.Store, now time.Time) ([]staleLane, []string, error) {
	registered, err := s.AllLanes()
	if err != nil {
		return nil, nil, err
	}
	var candidates []staleLane
	for _, lane := range registered {
		if lane.State != store.LaneRetired || lane.Name == Root || lane.Name == OwnerRelay || slices.Contains(standingRoles, lane.Role) || lane.Standing != "" || lane.Heard.IsZero() || now.Sub(lane.Heard) < endedQuiet {
			continue
		}
		if receipt := receiptOf(e.receipts(), lane.Name); receipt.Result.DispatchID != "" {
			candidates = append(candidates, staleLane{name: lane.Name, dispatch: receipt.Result.DispatchID, terminal: receipt.agentTerminal(), run: receipt.Result.RunID, last: lane.Last, retiredBy: lane.RetiredBy, brief: lane.Brief, heard: lane.Heard})
		}
	}
	if len(candidates) == 0 {
		return nil, nil, nil
	}
	// TODO: once resting-lanes-gone's relaunch wakes a closed lane on his answer, a lane with an open ask or card may close at endedQuiet too.
	open, err := e.Bus.Open(ctx, e.Config.Drive, "ask", "blocker", "decide")
	if err != nil {
		return nil, nil, fmt.Errorf("%w: cci open: %w", ErrUnread, err)
	}
	asking := map[string]bool{}
	for _, r := range open {
		asking[r.Lane] = true
	}
	registry := make(map[string]bool, len(registered))
	for _, lane := range registered {
		registry[lane.Name] = true
	}
	for i, lane := range candidates {
		endedItself := slices.Contains(laneEnds, lane.last) || slices.Contains(settled, lane.last) || lane.last == kindLanded
		retiredForGood := slices.Contains([]string{"done", retiredStopped, retiredWorkerDone, Root}, lane.retiredBy) || registry[lane.retiredBy]
		candidates[i].ended = !asking[lane.name] && (endedItself || retiredForGood)
	}
	candidates = slices.DeleteFunc(candidates, func(lane staleLane) bool { return !lane.ended && now.Sub(lane.heard) < staleSilence })
	if len(candidates) == 0 {
		return nil, nil, nil
	}
	terminals, err := terminalcap.List(ctx, e.Runner)
	if err != nil {
		return nil, nil, err
	}
	openTerminals := make(map[string]bool, len(terminals))
	for _, terminal := range terminals {
		openTerminals[terminal.Handle] = true
	}
	var due []staleLane
	var misread []string
	for _, lane := range candidates {
		if !openTerminals[lane.terminal] {
			continue
		}
		if lane.run != e.Config.OrcaRun {
			misread = append(misread, fmt.Sprintf("%s: its launch receipt names Orca Run %q, not the drive's %s, so its session stays open", lane.name, lane.run, e.Config.OrcaRun))
			continue
		}
		if _, err := os.Stat(lane.brief); err != nil {
			continue
		}
		if brief, err := BriefOf(lane.brief); err == nil && brief.Standing == "" {
			due = append(due, lane)
		}
	}
	return due, misread, nil
}

type headcount struct {
	claude, codex, worktrees, terminals int
}

func (e Env) headcount(ctx context.Context) (headcount, error) {
	processes, err := agentProcesses(ctx, e.Runner)
	if err != nil {
		return headcount{}, err
	}
	worktrees, err := orcaTotal(ctx, e.Runner, "worktree")
	if err != nil {
		return headcount{}, err
	}
	terminals, err := orcaTotal(ctx, e.Runner, "terminal")
	if err != nil {
		return headcount{}, err
	}
	return headcount{processes["claude"], processes["codex"], worktrees, terminals}, nil
}

func reapText(before, after headcount, tally string) string {
	return fmt.Sprintf("sessions %d to %d, worktrees %d to %d, terminals %d to %d; this pass: %s",
		before.claude+before.codex, after.claude+after.codex, before.worktrees, after.worktrees, before.terminals, after.terminals, tally)
}

func reclaimedAny(tally string) bool {
	for word := range strings.FieldsSeq(tally) {
		if verb, _, counted := strings.Cut(word, "="); counted && verb != "keep" && verb != "skip" {
			return true
		}
	}
	return false
}

const (
	gcStartedCursor = "gc-pass-started"
	gcEndedCursor   = "gc-pass-ended"
)

var gcPassRunning atomic.Bool

func GCPassRunning() bool {
	return gcPassRunning.Load()
}

var gcYielding atomic.Bool

func YieldGC(yield bool) {
	gcYielding.Store(yield)
}

func GCPassSince(s *store.Store) (time.Time, error) {
	started, err := s.Cursor(gcStartedCursor)
	if err != nil {
		return time.Time{}, err
	}
	ended, err := s.Cursor(gcEndedCursor)
	if err != nil || started <= ended {
		return time.Time{}, err
	}
	return time.UnixMicro(started), nil
}

type SettledGC struct {
	Env   Env
	Store *store.Store
	Log   *slog.Logger
	read  time.Time
}

func (g *SettledGC) Check(ctx context.Context, now time.Time) error {
	gcPassRunning.Store(true)
	defer gcPassRunning.Store(false)
	if gcYielding.Load() || (!g.read.IsZero() && now.Sub(g.read) < gcEvery) {
		return nil
	}
	g.read = now
	if err := g.Store.Advance(gcStartedCursor, now.UnixMicro()); err != nil {
		return err
	}
	defer func() {
		if err := g.Store.Advance(gcEndedCursor, now.UnixMicro()); err != nil {
			g.Log.Warn("a finished lr gc pass stays marked running until the next one ends", "err", err)
		}
	}()
	keep, err := g.Env.keepWanted(g.Store)
	if err != nil {
		g.Log.Warn("lr gc --new did not read the registry", "err", err)
		return nil
	}
	stale, misread, err := g.Env.staleRetired(ctx, g.Store, now)
	if err != nil {
		g.Log.Warn("lr gc --new did not read which retired lanes are stale, so this pass ends and the next one reads them again", "err", err)
		return nil
	}
	for _, why := range misread {
		g.Log.Warn("lr gc --new leaves a retired lane out", "why", why)
	}
	var staleArgs []string
	for _, lane := range stale {
		if lane.ended {
			staleArgs = append(staleArgs, "--finished", lane.dispatch)
		}
		staleArgs = append(staleArgs, "--stale", lane.dispatch)
	}
	before, uncounted := g.Env.headcount(ctx)
	result, err := g.Env.gc(ctx, slices.Concat(keep, staleArgs, []string{"--new", "--shells", "--other-runs"})...)
	said := strings.TrimSpace(result.Stdout)
	if named := g.nameClosed(ctx, stale, said, now); named != nil {
		return named
	}
	if err != nil {
		g.Log.Warn("lr gc --new did not finish", "err", err, "said", strings.TrimSpace(result.Stderr))
		return nil
	}
	g.Log.Info("lr gc --new", "said", said)
	if uncounted != nil {
		g.Log.Warn("lr gc --new did not count before its pass", "err", uncounted)
		return nil
	}
	after, err := g.Env.headcount(ctx)
	if err != nil {
		g.Log.Warn("lr gc --new did not count after its pass", "err", err)
		return nil
	}
	tally := said[strings.LastIndex(said, "\n")+1:]
	if before == after && !reclaimedAny(tally) {
		return nil
	}
	_, err = g.Env.Bus.Post(ctx, client.Draft{Drive: g.Env.Config.Drive, Lane: Self, Kind: "state", Topic: ReapTopic, ID: fmt.Sprintf("%s:%d", ReapTopic, now.Unix()), Text: reapText(before, after, tally)})
	if err != nil {
		return fmt.Errorf("%w: cci post: %w", ErrUnread, err)
	}
	return nil
}

func (g *SettledGC) nameClosed(ctx context.Context, stale []staleLane, said string, now time.Time) error {
	closedBy := map[string]bool{}
	for line := range strings.SplitSeq(said, "\n") {
		fields := strings.Fields(line)
		switch {
		case len(fields) == 3 && fields[0] == "stopped":
			closedBy[fields[1]] = true
		case len(fields) >= 4 && fields[0] == "closed" && fields[2] == "dispatch":
			closedBy[fields[3]] = true
		}
	}
	closed := slices.DeleteFunc(slices.Clone(stale), func(lane staleLane) bool { return !closedBy[lane.dispatch] })
	if len(closed) == 0 {
		return nil
	}
	year, month, day := now.In(pacificZone).Date()
	today, err := g.Env.Bus.Tail(ctx, client.Query{Drive: g.Env.Config.Drive, Since: time.Date(year, month, day, 0, 0, 0, 0, pacificZone), Kinds: []string{"state"}, Lanes: []string{Self}})
	if err != nil {
		return fmt.Errorf("%w: cci tail: %w", ErrUnread, err)
	}
	count := 0
	for _, r := range today {
		if r.Topic == closedTopic {
			count++
		}
	}
	for _, lane := range closed {
		count++
		text := fmt.Sprintf("%s's terminal closed: its last record was %s and the registry retired it (%s); lr gc found it at an empty prompt under the agent process it launched. Dispatch %s. Lane terminals closed today: %d.", lane.name, lane.last, lane.retiredBy, lane.dispatch, count)
		if _, err := g.Env.Bus.Post(ctx, client.Draft{Drive: g.Env.Config.Drive, Lane: Self, Kind: "state", Topic: closedTopic, ID: closedTopic + ":" + lane.dispatch, Text: text}); err != nil {
			return fmt.Errorf("%w: cci post: %w", ErrUnread, err)
		}
	}
	return nil
}

func (e Env) Checks(ctx context.Context, s *store.Store, log *slog.Logger) ([]Check, error) {
	checks := []Check{(&ClassUnowned{Env: e, Store: s}).Check}
	var channels []string
	for channel := range strings.SplitSeq(e.Config.AlertChannels, ",") {
		if channel = strings.TrimSpace(channel); channel != "" {
			channels = append(channels, channel)
		}
	}
	refused := e.dropClasses(ctx, log)
	if len(channels) > 0 {
		checks = append(checks, (&Alerts{Env: e, Store: s, Channels: channels, Log: log, Refused: refused, Launch: e.IncidentLaunch}).Check)
	}
	checks = append(checks, (&OverrideRefusals{Env: e, Store: s}).Check)
	landing, err := ReadRunnerLanding(e.State)
	if err != nil {
		return nil, err
	}
	var builds *ReleaseBuilds
	if pipelines := e.Config.ReleasePipelineSlugs(); len(pipelines) > 0 {
		builds = &ReleaseBuilds{Env: e, Store: s, Pipelines: pipelines, CLI: strings.Fields(e.Config.BuildkiteCLI), Token: bkread.Token{Command: landing.CITokenCommand}, Log: log}
		checks = append(checks, builds.Check)
	}
	if e.Config.OrcaRun != "" {
		terminals, err := newTerminalCap(e, s)
		if err != nil {
			return nil, err
		}
		checks = append(checks, (&FixLiveGC{Env: e, Store: s, Log: log}).Check, (&SettledGC{Env: e, Store: s, Log: log}).Check, terminals.Check)
	}
	if len(landing.Pages.Channels) > 0 {
		checks = append(checks, (&PageWatch{Env: e, Store: s, Channels: landing.Pages.Channels, BuildCLI: landing.Pages.Build, Token: bkread.Token{Command: landing.CITokenCommand}, Log: log, Refused: refused}).Check)
	}
	remote, err := CcRemoteOf(e.State, e.plugin())
	if err != nil {
		return nil, err
	}
	if e.Config.OrcaRun != "" && remote != nil {
		checks = append(checks, (&SpriteStops{Env: e, Store: s, CcRemote: *remote, Log: log}).Check)
	}
	if _, err := exec.LookPath("nsc"); err == nil {
		checks = append(checks, (&NscDeadlines{Env: e, Store: s, Log: log}).Check)
		if cli := strings.Fields(e.Config.BuildkiteCLI); landing.MacQueue.Queue != "" && len(cli) > 0 {
			checks = append(checks, (&MacWaits{Env: e, Queue: landing.MacQueue, CLI: cli, Token: bkread.Token{Command: landing.CITokenCommand}, Log: log}).Check)
		}
	}
	if cli := strings.Fields(landing.ReleaseCLI); len(cli) > 0 {
		checks = append(checks, (&LandedNotLive{Env: e, Store: s, ReleaseCLI: cli, Token: bkread.Token{Command: landing.CITokenCommand}, Builds: builds, Log: log}).Check, (&ReleaseGates{Env: e, ReleaseCLI: cli, Token: bkread.Token{Command: landing.CITokenCommand}, Log: log}).Check)
	}
	return checks, nil
}
