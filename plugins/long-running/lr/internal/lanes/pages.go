package lanes

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bkread"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	pageTail        = 30 * time.Minute
	pagePoll        = 15 * time.Second
	pageRead        = 20
	releaseLookback = 12 * time.Hour
	releaseKind     = "release"
	incidentKind    = "incident"
	reportKind      = "report"
	legacyRunner    = "desk-runner"
)

var (
	livePage     = regexp.MustCompile(`^:(?:rotating_light|mag|eyes|hammer_and_wrench|hourglass_flowing_sand|chart_with_upwards_trend): \*`)
	slackLink    = regexp.MustCompile(`<[^|>]+\|([^>]+)>`)
	laneBuildURL = regexp.MustCompile(`https://buildkite\.com/[\w.-]+/[\w.-]+/builds/\d+`)
	buildAddress = regexp.MustCompile(`/([\w.-]+)/builds/(\d+)/?$`)
	releaseID    = regexp.MustCompile(`\breleases/(\d{4}-\d{2}-\d{2}/\d+)\b`)
)

type PageWatch struct {
	Env      Env
	Store    *store.Store
	Channels []string
	BuildCLI string
	Token    bkread.Token
	Log      *slog.Logger
	Refused  []config.Refusal

	after  int64
	polled time.Time
	builds map[string]*watchedBuild
}

type watchedBuild struct {
	lane     string
	release  string
	at       time.Time
	done     bool
	finished time.Time
}

type pageMessage struct {
	slackMessage
	UserName string `json:"user_name"`
}

func (w *PageWatch) Check(ctx context.Context, now time.Time) error {
	if err := w.readReleases(ctx, now); err != nil {
		return err
	}
	if now.Sub(w.polled) < pagePoll {
		return nil
	}
	w.polled = now
	if err := w.readFinished(ctx); err != nil {
		return err
	}
	return w.readPages(ctx, now)
}

func (w *PageWatch) readReleases(ctx context.Context, now time.Time) error {
	query := client.Query{Drive: w.Env.Config.Drive, Kinds: []string{releaseKind}, After: w.after}
	if w.builds == nil {
		w.builds = map[string]*watchedBuild{}
		query.Since = now.Add(-releaseLookback)
	}
	records, err := w.Env.Bus.Tail(ctx, query)
	if err != nil {
		return fmt.Errorf("%w: cci tail release: %w", ErrUnread, err)
	}
	for _, record := range records {
		w.after = max(w.after, record.Seq)
		if slices.Contains([]string{legacyRunner, Root, Self}, record.Lane) {
			continue
		}
		release := ""
		if found := releaseID.FindStringSubmatch(record.Text); found != nil {
			release = found[1]
		}
		for _, build := range slices.Concat(record.Refs.Builds, laneBuildURL.FindAllString(record.Text, -1)) {
			if _, known := w.builds[build]; known {
				continue
			}
			w.builds[build] = &watchedBuild{lane: record.Lane, release: release, at: record.At}
			if err := w.claim(ctx, build, w.builds[build], now); err != nil {
				return err
			}
		}
	}
	return nil
}

func (w *PageWatch) claim(ctx context.Context, build string, watched *watchedBuild, now time.Time) error {
	first, err := w.Store.Notice("release-claim:"+build, now)
	if err != nil || !first {
		return err
	}
	peers, err := w.Env.Bus.Who(ctx, w.Env.Config.Drive)
	if err != nil {
		return fmt.Errorf("%w: cci who: %w", ErrUnread, err)
	}
	if slices.ContainsFunc(peers, func(peer client.Peer) bool { return peer.Lane == watched.lane && peer.StoodDown }) {
		return nil
	}
	owner := watched.lane + "@" + w.Env.Config.Drive
	registry := []string{filepath.Join(w.Env.Claude, "long-running", "current", "bin", "drive.py"), "release", "--cci-drive", w.Env.Config.Drive, "--owner", owner, "--build", build}
	if watched.release != "" {
		registry = append(registry, "--release", watched.release)
	}
	var failures []string
	for _, argv := range [][]string{registry, {"cci", "claim", "--for", owner, "--build", build, "--rel", "started"}} {
		if _, err := w.Env.Run(ctx, argv...); err != nil {
			failures = append(failures, err.Error())
		}
	}
	if len(failures) == 0 {
		return nil
	}
	return w.post(ctx, blockerKind, "release-claim:"+build, fmt.Sprintf("RELEASE-CLAIM-FAILED build %s started by %s: %s", build, owner, strings.Join(failures, "; ")))
}

func (w *PageWatch) readFinished(ctx context.Context) error {
	for build, watched := range w.builds {
		if watched.done {
			continue
		}
		found := buildAddress.FindStringSubmatch(build)
		if found == nil {
			watched.done = true
			if err := w.post(ctx, reportKind, "applying-read:"+build, fmt.Sprintf("APPLYING-UNREAD build %s of lane %s is no Buildkite build URL, so it opens no page watch", build, watched.lane)); err != nil {
				return err
			}
			continue
		}
		words := strings.Fields(strings.NewReplacer("{pipeline}", found[1], "{number}", found[2]).Replace(w.BuildCLI))
		words[0] = filepath.Join(w.Env.Config.Checkout, words[0])
		done, err := w.Token.Run(ctx, w.Env.Runner, shell.Command{Argv: words, Dir: w.Env.Config.Checkout, Timeout: readTimeout})
		if err != nil {
			w.Log.Warn("a release build did not read", "build", build, "err", err)
			continue
		}
		var shown struct {
			FinishedAt *time.Time `json:"finished_at"`
		}
		if err := json.Unmarshal([]byte(done.Stdout), &shown); err != nil {
			w.Log.Warn("a release build did not parse", "build", build, "err", err)
			continue
		}
		if shown.FinishedAt != nil {
			watched.done, watched.finished = true, *shown.FinishedAt
		}
	}
	return nil
}

func (w *PageWatch) readPages(ctx context.Context, now time.Time) error {
	var since time.Time
	for _, watched := range w.builds {
		if (!watched.done || (!watched.finished.IsZero() && now.Sub(watched.finished) < pageTail)) && (since.IsZero() || watched.at.Before(since)) {
			since = watched.at
		}
	}
	if since.IsZero() {
		return nil
	}
	for _, channel := range w.Channels {
		stdout, err := w.Env.Run(ctx, "cc-slack", "history", "--channel", channel, "--json", "--limit", strconv.Itoa(pageRead))
		if err != nil {
			if err := w.post(ctx, blockerKind, "pages-unread:"+channel+":"+now.UTC().Format("2006010215"), fmt.Sprintf("PAGES-UNREAD %s did not read, so no page in it reaches root until it does: %v", channel, err)); err != nil {
				return err
			}
			continue
		}
		var history struct {
			Messages []pageMessage `json:"messages"`
		}
		if err := json.Unmarshal([]byte(stdout), &history); err != nil {
			w.Log.Warn("a page channel did not parse", "channel", channel, "err", err)
			continue
		}
		if err := w.raise(ctx, channel, history.Messages, since); err != nil {
			return err
		}
	}
	return nil
}

func (w *PageWatch) raise(ctx context.Context, channel string, messages []pageMessage, since time.Time) error {
	seen, err := w.Store.Cursor("pages:" + channel)
	if err != nil {
		return err
	}
	after := max(seen, since.UnixMicro())
	type dated struct {
		pageMessage
		at time.Time
	}
	var pages []dated
	for _, message := range messages {
		at, err := firedAt(message.TS)
		if err != nil {
			return err
		}
		if refusal, _, refused := config.RefusedLine(w.Refused, message.Text); refused {
			w.Log.Debug("page on a refused topic dropped", "channel", channel, "why", refusal.Why)
			continue
		}
		if at.UnixMicro() > after && (message.User != "" || livePage.MatchString(message.Text)) {
			pages = append(pages, dated{message, at})
		}
	}
	slices.SortFunc(pages, func(a, b dated) int { return a.at.Compare(b.at) })
	for _, page := range pages {
		headline, _, _ := strings.Cut(page.Text, "\n")
		headline = truncated(strings.Join(strings.Fields(slackLink.ReplaceAllString(headline, "$1")), " "), 160)
		who := page.UserName
		if who == "" {
			who = "someone"
		}
		key := "page:" + channel + ":" + page.TS
		text := fmt.Sprintf("PAGE %s | a drive release is live: open the incident, launch its fix lane and reply in the page thread now, no owner round-trip | %s: %s", page.Permalink, who, headline)
		if _, err := w.Env.Bus.Post(ctx, client.Draft{Drive: w.Env.Config.Drive, Lane: Self, Kind: incidentKind, To: []string{Root}, ID: key, Topic: key, URL: page.Permalink, Text: text}); err != nil {
			return fmt.Errorf("%w: cci post: %w", ErrUnread, err)
		}
		if err := w.Store.Advance("pages:"+channel, page.at.UnixMicro()); err != nil {
			return err
		}
	}
	return nil
}

func (w *PageWatch) post(ctx context.Context, kind, id, text string) error {
	if _, err := w.Env.Bus.Post(ctx, client.Draft{Drive: w.Env.Config.Drive, Lane: Self, Kind: kind, To: []string{Root}, ID: id, Text: text}); err != nil {
		return fmt.Errorf("%w: cci post: %w", ErrUnread, err)
	}
	return nil
}
