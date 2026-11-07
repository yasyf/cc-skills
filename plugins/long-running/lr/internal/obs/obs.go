package obs

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"maps"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"syscall"
	"time"
	"unicode/utf8"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/askdesk"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	Lane = "obs-desk"
	Duty = "answers lanes' Sentry, Datadog, Slack and ai-oncall reads over cci"

	bodiesDir     = "obs-desk"
	fresh         = time.Minute
	keyWait       = time.Minute
	readTimeout   = 2 * time.Minute
	replyLimit    = 16 << 10
	messageLimit  = 600
	defaultWindow = time.Hour
	listLimit     = 100
	frameLimit    = 15
	slackLimit    = 200
	Reads         = "sentry issue <id or short id>, sentry events <id>, sentry search [--period <14d>] <query>, sentry api <path> [--pick <dotted.path>,...] [--from <byte>], datadog monitor <id>, datadog logs [--from <age>] [--to <age>] <query>, datadog spans [--from <age>] [--to <age>] <query>, datadog metric [--from <age>] [--to <age>] <query>, datadog api <path> [--pick <dotted.path>,...] [--from <byte>]"
	OncallReads   = "incidents, page status <id>, ledger <id>, journal read <id> --to <seq>, history --monitor <id> --since <RFC3339>, monitors --key <key>, impact --from <RFC3339> --to <RFC3339>, current, responder or connections list <id>"
	usage         = "name one read: " + Reads + ", slack thread <permalink>, or oncall <read verb>"
	oncallUsage   = "oncall reads with " + OncallReads + "; connect, say and every write go through your evidence lane"
	apiUsage      = "reads one path under the API root with GET: api <path> [--pick <dotted.path>,...] [--from <byte>], where a step of a dotted path is a key, an array index, or field=value for the first array element whose field is that value"
)

var (
	request    = regexp.MustCompile(`\b(sentry|datadog|slack|oncall)\s+(\S+)(.*)`)
	unsplit    = regexp.MustCompile(`"[^"\n]*"|'[^'\n]*'|https?://\S*[^\s;]`)
	permalink  = regexp.MustCompile(`^https://[a-z0-9-]+\.slack\.com/archives/[A-Z0-9]+/p\d{16}(\?\S*)?$`)
	issueURL   = regexp.MustCompile(`/issues/(\d+)`)
	numericID  = regexp.MustCompile(`^\d+$`)
	shortID    = regexp.MustCompile(`^[A-Za-z0-9_-]+-[A-Za-z0-9]+$`)
	ageSuffix  = regexp.MustCompile(`^(\d+)([smhdw])$`)
	oncallRead = [][]string{{"incidents"}, {"page", "status"}, {"ledger"}, {"journal", "read"}, {"history"}, {"monitors"}, {"impact"}, {"current"}, {"responder"}, {"connections", "list"}}
)

type Settings struct {
	SSM struct {
		Profile string `json:"profile"`
		Region  string `json:"region"`
	} `json:"ssm"`
	Sentry struct {
		API   string `json:"api"`
		Org   string `json:"org"`
		Token string `json:"token"`
	} `json:"sentry"`
	Datadog struct {
		API    string `json:"api"`
		APIKey string `json:"api_key"`
		AppKey string `json:"app_key"`
	} `json:"datadog"`
	Oncall struct {
		CLI      string `json:"cli"`
		Checkout string `json:"checkout"`
	} `json:"oncall"`
}

func LoadSettings(path string) (Settings, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return Settings{}, err
	}
	var s Settings
	if err := json.Unmarshal(raw, &s); err != nil {
		return Settings{}, fmt.Errorf("%s: %w", path, err)
	}
	if s.SSM.Profile == "" || s.SSM.Region == "" || s.Sentry.API == "" || s.Sentry.Org == "" || s.Sentry.Token == "" ||
		s.Datadog.API == "" || s.Datadog.APIKey == "" || s.Datadog.AppKey == "" || s.Oncall.CLI == "" || s.Oncall.Checkout == "" {
		return Settings{}, fmt.Errorf("%s needs ssm.profile and ssm.region, sentry.api, sentry.org and sentry.token, datadog.api, datadog.api_key and datadog.app_key, each key an SSM parameter name, and oncall.cli and oncall.checkout", path)
	}
	return s, nil
}

type key struct {
	header, value string
}

type reply struct {
	askdesk.Reply
	at time.Time
}

type Desk struct {
	Runner   shell.Runner
	HTTP     *http.Client
	Settings Settings
	Drive    string
	Bodies   string
	Now      func() time.Time
	Log      io.Writer

	sentry  []key
	datadog []key
	cache   map[string]reply
}

func NewDesk(ctx context.Context, runner shell.Runner, settings Settings, drive, state string, now func() time.Time, log io.Writer) (*Desk, error) {
	read := func(name string) (string, error) {
		printed, err := runner.Run(ctx, shell.Command{Argv: []string{
			"aws", "ssm", "get-parameter", "--profile", settings.SSM.Profile, "--region", settings.SSM.Region,
			"--name", name, "--with-decryption", "--query", "Parameter.Value", "--output", "text",
		}, Timeout: keyWait})
		if err != nil {
			return "", fmt.Errorf("reading %s from SSM failed: %w", name, err)
		}
		value := strings.TrimSpace(printed.Stdout)
		if value == "" {
			return "", fmt.Errorf("SSM printed nothing for %s", name)
		}
		return value, nil
	}
	sentry, err := read(settings.Sentry.Token)
	if err != nil {
		return nil, err
	}
	apiKey, err := read(settings.Datadog.APIKey)
	if err != nil {
		return nil, err
	}
	appKey, err := read(settings.Datadog.AppKey)
	if err != nil {
		return nil, err
	}
	return &Desk{
		Runner: runner, HTTP: &http.Client{Timeout: readTimeout}, Settings: settings, Drive: drive,
		Bodies: filepath.Join(state, bodiesDir), Now: now, Log: log,
		sentry:  []key{{"Authorization", "Bearer " + sentry}},
		datadog: []key{{"DD-API-KEY", apiKey}, {"DD-APPLICATION-KEY", appKey}},
		cache:   map[string]reply{},
	}, nil
}

func (d *Desk) Serve(ctx context.Context, claude string) error {
	return askdesk.ServeInstalled(ctx, d.loop(), claude, syscall.Exec)
}

func (d *Desk) loop() askdesk.Desk {
	return askdesk.Desk{Lane: Lane, Drive: d.Drive, Duty: Duty, Bodies: d.Bodies, Now: d.Now, Log: d.Log, Read: d.read}
}

func (d *Desk) read(ctx context.Context, text string) askdesk.Reply {
	asked := reads(text)
	if len(asked) < 2 {
		return d.readOne(ctx, strings.Join(asked, ""))
	}
	heads := make([]string, len(asked))
	var body strings.Builder
	for n, one := range asked {
		read := d.readOne(ctx, one)
		heads[n] = read.Head
		fmt.Fprintf(&body, "## read %d of %d: %s\n%s\n%s\n\n", n+1, len(asked), one, read.Head, strings.TrimRight(read.Body, "\n"))
	}
	return askdesk.Reply{Head: clip(fmt.Sprintf("%d reads: %s", len(asked), strings.Join(heads, "; "))), Body: body.String()}
}

func reads(text string) []string {
	var asked []string
	start, kept := 0, unsplit.FindAllStringIndex(text, -1)
	for at, c := range text {
		for len(kept) > 0 && kept[0][1] <= at {
			kept = kept[1:]
		}
		if (c != ';' && c != '\n') || (len(kept) > 0 && kept[0][0] <= at) {
			continue
		}
		asked = append(asked, strings.TrimSpace(text[start:at]))
		start = at + 1
	}
	asked = append(asked, strings.TrimSpace(text[start:]))
	return slices.DeleteFunc(asked, func(one string) bool { return one == "" })
}

func (d *Desk) readOne(ctx context.Context, text string) askdesk.Reply {
	match := request.FindStringSubmatch(text)
	if match == nil {
		return refused(usage)
	}
	source, verb, rest := match[1], match[2], strings.Fields(match[3])
	label := strings.TrimSpace(source + " " + verb + " " + strings.Join(rest, " "))
	now := d.Now()
	if hit, found := d.cache[label]; found && now.Sub(hit.at) < fresh {
		return hit.Reply
	}
	maps.DeleteFunc(d.cache, func(_ string, kept reply) bool { return now.Sub(kept.at) >= fresh })
	var read askdesk.Reply
	var refusal string
	switch source {
	case "sentry":
		read, refusal = d.sentryRead(ctx, verb, rest)
	case "datadog":
		read, refusal = d.datadogRead(ctx, verb, rest, now)
	case "slack":
		read, refusal = d.slackRead(ctx, verb, rest)
	default:
		read, refusal = d.oncallRead(ctx, append([]string{verb}, rest...))
	}
	if refusal != "" {
		return refused(refusal)
	}
	read.Head = label + ": " + read.Head
	d.cache[label] = reply{Reply: read, at: now}
	return read
}

func refused(why string) askdesk.Reply {
	return askdesk.Reply{Head: "obs read refused: " + clip(why), Body: why, Refused: true}
}

func (d *Desk) sentryRead(ctx context.Context, verb string, rest []string) (askdesk.Reply, string) {
	org := d.Settings.Sentry.API + "/organizations/" + url.PathEscape(d.Settings.Sentry.Org)
	switch verb {
	case "issue", "events":
		if len(rest) != 1 {
			return askdesk.Reply{}, "sentry " + verb + " takes one issue id, short id or issue URL"
		}
		id, failure := d.issueID(ctx, org, rest[0])
		if failure != nil {
			return *failure, ""
		}
		if verb == "events" {
			return d.sentryEvents(ctx, org, id), ""
		}
		return d.sentryIssue(ctx, org, id), ""
	case "search":
		period := "24h"
		if len(rest) >= 2 && rest[0] == "--period" {
			period, rest = rest[1], rest[2:]
		}
		if len(rest) == 0 {
			return askdesk.Reply{}, "sentry search takes [--period <14d>] <query>"
		}
		query := url.Values{"query": {unquoted(rest)}, "statsPeriod": {period}, "limit": {"25"}, "sort": {"date"}}
		return d.sentryIssues(ctx, org+"/issues/?"+query.Encode()), ""
	case "api":
		read, refusal := d.apiRead(ctx, d.Settings.Sentry.API, d.sentry, rest)
		if refusal != "" {
			return askdesk.Reply{}, "sentry api " + refusal
		}
		return read, ""
	}
	return askdesk.Reply{}, fmt.Sprintf("sentry %s is no read; %s", verb, usage)
}

func (d *Desk) apiRead(ctx context.Context, base string, keys []key, rest []string) (askdesk.Reply, string) {
	if len(rest) == 0 || strings.HasPrefix(rest[0], "-") || strings.Contains(rest[0], "://") {
		return askdesk.Reply{}, apiUsage
	}
	flags := flag.NewFlagSet("api", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	picks := flags.String("pick", "", "")
	from := flags.Int("from", 0, "")
	if err := flags.Parse(rest[1:]); err != nil || flags.NArg() != 0 || *from < 0 {
		return askdesk.Reply{}, apiUsage
	}
	said, failure := d.get(ctx, base+"/"+strings.TrimPrefix(rest[0], "/"), keys)
	if failure != nil {
		return *failure, ""
	}
	text, note := said, ""
	if paths := strings.FieldsFunc(*picks, func(r rune) bool { return r == ',' }); len(paths) > 0 {
		if !json.Valid([]byte(said)) {
			return askdesk.Reply{}, "--pick needs a JSON response and this one is not JSON, so read it without --pick"
		}
		text, note = picked(said, paths)
	}
	start := *from
	if start > len(text) {
		return askdesk.Reply{}, fmt.Sprintf("--from %d is past the end of %d bytes", start, len(text))
	}
	end := min(start+replyLimit, len(text))
	for back := 0; back < utf8.UTFMax-1 && end < len(text) && !utf8.RuneStart(text[end]); back++ {
		end--
	}
	head := fmt.Sprintf("%d of %d bytes", end-start, len(text))
	if start > 0 {
		head += fmt.Sprintf(" from byte %d", start)
	}
	if end < len(text) {
		head += fmt.Sprintf("; more with --from %d", end)
	}
	return askdesk.Reply{Head: head + note, Body: text[start:end]}, ""
}

func picked(said string, paths []string) (string, string) {
	var body strings.Builder
	var absent []string
	for _, path := range paths {
		found, present := pick(json.RawMessage(said), path)
		if !present {
			absent = append(absent, path)
			continue
		}
		fmt.Fprintf(&body, "%s: %s\n", path, found)
	}
	note := fmt.Sprintf("; picked %d of %d paths from a %d-byte response", len(paths)-len(absent), len(paths), len(said))
	if len(absent) > 0 {
		note += "; absent: " + strings.Join(absent, ", ")
	}
	return body.String(), note
}

func pick(at json.RawMessage, path string) (json.RawMessage, bool) {
	for step := range strings.SplitSeq(path, ".") {
		var object map[string]json.RawMessage
		var list []json.RawMessage
		found := false
		switch {
		case json.Unmarshal(at, &object) == nil:
			at, found = object[step]
		case json.Unmarshal(at, &list) == nil:
			at, found = element(list, step)
		}
		if !found {
			return nil, false
		}
	}
	return at, true
}

func element(list []json.RawMessage, step string) (json.RawMessage, bool) {
	if index, err := strconv.Atoi(step); err == nil {
		if index < 0 || index >= len(list) {
			return nil, false
		}
		return list[index], true
	}
	field, want, matched := strings.Cut(step, "=")
	if !matched {
		return nil, false
	}
	for _, one := range list {
		var object map[string]json.RawMessage
		if json.Unmarshal(one, &object) != nil {
			continue
		}
		if value, has := object[field]; has && scalar(value) == want {
			return one, true
		}
	}
	return nil, false
}

func scalar(raw json.RawMessage) string {
	var text string
	if raw[0] == '"' && json.Unmarshal(raw, &text) == nil {
		return text
	}
	return string(raw)
}

func (d *Desk) issueID(ctx context.Context, org, named string) (string, *askdesk.Reply) {
	if found := issueURL.FindStringSubmatch(named); found != nil {
		return found[1], nil
	}
	if numericID.MatchString(named) {
		return named, nil
	}
	if !shortID.MatchString(named) {
		return "", &askdesk.Reply{Head: "not an issue id", Body: "name a numeric issue id, a short id such as DASHBOARD-1A2, or an issue URL"}
	}
	said, failure := d.get(ctx, org+"/shortids/"+url.PathEscape(named)+"/", d.sentry)
	if failure != nil {
		return "", failure
	}
	var resolved struct {
		GroupID string `json:"groupId"`
	}
	if err := json.Unmarshal([]byte(said), &resolved); err != nil || resolved.GroupID == "" {
		return "", &askdesk.Reply{Head: "the short id did not resolve", Body: askdesk.FirstBytes(said, replyLimit)}
	}
	return resolved.GroupID, nil
}

type sentryIssue struct {
	ID        string `json:"id"`
	ShortID   string `json:"shortId"`
	Title     string `json:"title"`
	Culprit   string `json:"culprit"`
	Permalink string `json:"permalink"`
	Status    string `json:"status"`
	Substatus string `json:"substatus"`
	Level     string `json:"level"`
	Count     string `json:"count"`
	UserCount int    `json:"userCount"`
	FirstSeen string `json:"firstSeen"`
	LastSeen  string `json:"lastSeen"`
	Project   struct {
		Slug string `json:"slug"`
	} `json:"project"`
}

type sentryEvent struct {
	EventID     string `json:"eventID"`
	DateCreated string `json:"dateCreated"`
	Title       string `json:"title"`
	Message     string `json:"message"`
	Tags        []struct {
		Key   string `json:"key"`
		Value string `json:"value"`
	} `json:"tags"`
	Entries []struct {
		Type string          `json:"type"`
		Data json.RawMessage `json:"data"`
	} `json:"entries"`
}

func (d *Desk) sentryIssue(ctx context.Context, org, id string) askdesk.Reply {
	said, failure := d.get(ctx, org+"/issues/"+id+"/", d.sentry)
	if failure != nil {
		return *failure
	}
	var issue sentryIssue
	if err := json.Unmarshal([]byte(said), &issue); err != nil {
		return askdesk.Reply{Head: "the issue did not parse", Body: askdesk.FirstBytes(said, replyLimit)}
	}
	var lines strings.Builder
	fmt.Fprintf(&lines, "%s %s in %s: %s\n%s\nstatus=%s %s level=%s events=%s users=%d first=%s last=%s\n",
		issue.ShortID, issue.ID, issue.Project.Slug, issue.Title, issue.Culprit, issue.Status, issue.Substatus, issue.Level, issue.Count, issue.UserCount, issue.FirstSeen, issue.LastSeen)
	latest, failure := d.get(ctx, org+"/issues/"+id+"/events/latest/", d.sentry)
	if failure != nil {
		fmt.Fprintf(&lines, "\nits latest event did not read: %s\n%s\n", failure.Head, failure.Body)
	} else {
		lines.WriteString("\n" + eventText(latest))
	}
	return askdesk.Reply{
		Head: fmt.Sprintf("%s %s, %s events, last %s", issue.ShortID, issue.Status, issue.Count, issue.LastSeen),
		Body: askdesk.FirstBytes(lines.String(), replyLimit), URL: issue.Permalink,
	}
}

func eventText(said string) string {
	var event sentryEvent
	if err := json.Unmarshal([]byte(said), &event); err != nil {
		return "the latest event did not parse:\n" + askdesk.FirstBytes(said, replyLimit/2)
	}
	var lines strings.Builder
	fmt.Fprintf(&lines, "latest event %s at %s: %s\n", event.EventID, event.DateCreated, event.Title)
	if event.Message != "" {
		fmt.Fprintf(&lines, "message: %s\n", clip(event.Message))
	}
	for _, entry := range event.Entries {
		switch entry.Type {
		case "exception":
			var data struct {
				Values []struct {
					Type       string `json:"type"`
					Value      string `json:"value"`
					Stacktrace *struct {
						Frames []struct {
							Filename string `json:"filename"`
							Function string `json:"function"`
							LineNo   *int   `json:"lineNo"`
							InApp    bool   `json:"inApp"`
						} `json:"frames"`
					} `json:"stacktrace"`
				} `json:"values"`
			}
			if json.Unmarshal(entry.Data, &data) != nil {
				continue
			}
			for _, value := range data.Values {
				fmt.Fprintf(&lines, "exception %s: %s\n", value.Type, clip(value.Value))
				if value.Stacktrace == nil {
					continue
				}
				frames := value.Stacktrace.Frames
				frames = frames[max(0, len(frames)-frameLimit):]
				for i := len(frames) - 1; i >= 0; i-- {
					frame := frames[i]
					line := "?"
					if frame.LineNo != nil {
						line = strconv.Itoa(*frame.LineNo)
					}
					app := ""
					if frame.InApp {
						app = " in-app"
					}
					fmt.Fprintf(&lines, "  at %s %s:%s%s\n", frame.Function, frame.Filename, line, app)
				}
			}
		case "request":
			var data struct {
				Method string `json:"method"`
				URL    string `json:"url"`
			}
			if json.Unmarshal(entry.Data, &data) == nil && data.URL != "" {
				fmt.Fprintf(&lines, "request %s %s\n", data.Method, data.URL)
			}
		}
	}
	if len(event.Tags) > 0 {
		lines.WriteString("tags:")
		for _, tag := range event.Tags {
			fmt.Fprintf(&lines, " %s=%s", tag.Key, tag.Value)
		}
		lines.WriteString("\n")
	}
	return lines.String()
}

func (d *Desk) sentryEvents(ctx context.Context, org, id string) askdesk.Reply {
	said, failure := d.get(ctx, org+"/issues/"+id+"/events/?full=true", d.sentry)
	if failure != nil {
		return *failure
	}
	var events []sentryEvent
	if err := json.Unmarshal([]byte(said), &events); err != nil {
		return askdesk.Reply{Head: "the events did not parse", Body: askdesk.FirstBytes(said, replyLimit)}
	}
	var lines strings.Builder
	listed := 0
	for _, event := range events {
		var entry strings.Builder
		fmt.Fprintf(&entry, "%s %s %s\n ", event.DateCreated, event.EventID, clip(event.Title))
		for _, tag := range event.Tags {
			if slices.Contains([]string{"environment", "release", "url", "transaction", "server_name", "user", "team_id", "level"}, tag.Key) {
				fmt.Fprintf(&entry, " %s=%s", tag.Key, tag.Value)
			}
		}
		entry.WriteString("\n")
		if lines.Len()+entry.Len() > replyLimit {
			break
		}
		lines.WriteString(entry.String())
		listed++
	}
	return askdesk.Reply{Head: fmt.Sprintf("the newest %d events", listed), Body: lines.String()}
}

func (d *Desk) sentryIssues(ctx context.Context, address string) askdesk.Reply {
	said, failure := d.get(ctx, address, d.sentry)
	if failure != nil {
		return *failure
	}
	var issues []sentryIssue
	if err := json.Unmarshal([]byte(said), &issues); err != nil {
		return askdesk.Reply{Head: "the issues did not parse", Body: askdesk.FirstBytes(said, replyLimit)}
	}
	var lines strings.Builder
	for _, issue := range issues {
		fmt.Fprintf(&lines, "%s %s %s events=%s users=%d last=%s %s: %s\n  %s\n",
			issue.ID, issue.ShortID, issue.Status, issue.Count, issue.UserCount, issue.LastSeen, issue.Project.Slug, clip(issue.Title), issue.Permalink)
	}
	return askdesk.Reply{Head: fmt.Sprintf("%d issues", len(issues)), Body: askdesk.FirstBytes(lines.String(), replyLimit)}
}

func (d *Desk) datadogRead(ctx context.Context, verb string, rest []string, now time.Time) (askdesk.Reply, string) {
	switch verb {
	case "monitor":
		if len(rest) != 1 || !numericID.MatchString(rest[0]) {
			return askdesk.Reply{}, "datadog monitor takes one numeric monitor id"
		}
		return d.monitor(ctx, rest[0]), ""
	case "logs", "spans", "metric":
		from, to, query, refusal := window(rest, now)
		if refusal != "" {
			return askdesk.Reply{}, "datadog " + verb + " " + refusal
		}
		switch verb {
		case "logs":
			return d.logs(ctx, query, from, to), ""
		case "spans":
			return d.spans(ctx, query, from, to), ""
		}
		return d.metric(ctx, query, from, to), ""
	case "api":
		read, refusal := d.apiRead(ctx, d.Settings.Datadog.API, d.datadog, rest)
		if refusal != "" {
			return askdesk.Reply{}, "datadog api " + refusal
		}
		return read, ""
	}
	return askdesk.Reply{}, fmt.Sprintf("datadog %s is no read; %s", verb, usage)
}

func window(rest []string, now time.Time) (time.Time, time.Time, string, string) {
	from, to := now.Add(-defaultWindow), now
	for len(rest) >= 2 && (rest[0] == "--from" || rest[0] == "--to") {
		at, err := moment(rest[1], now)
		if err != nil {
			return from, to, "", "takes --from and --to as an age such as 90m, 6h or 2d, or an RFC3339 time"
		}
		if rest[0] == "--from" {
			from = at
		} else {
			to = at
		}
		rest = rest[2:]
	}
	if len(rest) == 0 {
		return from, to, "", "takes [--from <age>] [--to <age>] <query>"
	}
	return from, to, unquoted(rest), ""
}

func unquoted(words []string) string {
	text := strings.Join(words, " ")
	for _, quote := range []string{`"`, `'`} {
		inner, opened := strings.CutPrefix(text, quote)
		inner, closed := strings.CutSuffix(inner, quote)
		if opened && closed && !strings.Contains(inner, quote) {
			return inner
		}
	}
	return text
}

func moment(text string, now time.Time) (time.Time, error) {
	if at, err := time.Parse(time.RFC3339, text); err == nil {
		return at, nil
	}
	found := ageSuffix.FindStringSubmatch(text)
	if found == nil {
		return time.Time{}, errors.New("not an age or time")
	}
	count, _ := strconv.Atoi(found[1])
	unit := map[string]time.Duration{"s": time.Second, "m": time.Minute, "h": time.Hour, "d": 24 * time.Hour, "w": 7 * 24 * time.Hour}[found[2]]
	return now.Add(-time.Duration(count) * unit), nil
}

func (d *Desk) monitor(ctx context.Context, id string) askdesk.Reply {
	said, failure := d.get(ctx, d.Settings.Datadog.API+"/api/v1/monitor/"+id+"?group_states=alert,warn,no%20data", d.datadog)
	if failure != nil {
		return *failure
	}
	var monitor struct {
		Name         string   `json:"name"`
		Type         string   `json:"type"`
		Query        string   `json:"query"`
		Message      string   `json:"message"`
		OverallState string   `json:"overall_state"`
		Tags         []string `json:"tags"`
		State        struct {
			Groups map[string]struct {
				Status          string `json:"status"`
				LastTriggeredTS int64  `json:"last_triggered_ts"`
			} `json:"groups"`
		} `json:"state"`
	}
	if err := json.Unmarshal([]byte(said), &monitor); err != nil {
		return askdesk.Reply{Head: "the monitor did not parse", Body: askdesk.FirstBytes(said, replyLimit)}
	}
	var lines strings.Builder
	fmt.Fprintf(&lines, "%s (%s) %s\nquery: %s\ntags: %s\n", monitor.Name, monitor.Type, monitor.OverallState, monitor.Query, strings.Join(monitor.Tags, " "))
	for _, group := range slices.Sorted(maps.Keys(monitor.State.Groups)) {
		state := monitor.State.Groups[group]
		fmt.Fprintf(&lines, "group %s %s since %s\n", group, state.Status, time.Unix(state.LastTriggeredTS, 0).UTC().Format(time.RFC3339))
	}
	fmt.Fprintf(&lines, "message:\n%s\n", askdesk.FirstBytes(monitor.Message, 4<<10))
	return askdesk.Reply{
		Head: fmt.Sprintf("%s, %d groups not OK", monitor.OverallState, len(monitor.State.Groups)),
		Body: askdesk.FirstBytes(lines.String(), replyLimit),
		URL:  strings.Replace(d.Settings.Datadog.API, "://api.", "://app.", 1) + "/monitors/" + id,
	}
}

func (d *Desk) logs(ctx context.Context, query string, from, to time.Time) askdesk.Reply {
	said, failure := d.post(ctx, d.Settings.Datadog.API+"/api/v2/logs/events/search", map[string]any{
		"filter": map[string]string{"query": query, "from": from.UTC().Format(time.RFC3339), "to": to.UTC().Format(time.RFC3339)},
		"sort":   "-timestamp",
		"page":   map[string]int{"limit": listLimit},
	})
	if failure != nil {
		return *failure
	}
	var found struct {
		Data []struct {
			Attributes struct {
				Timestamp  string         `json:"timestamp"`
				Status     string         `json:"status"`
				Service    string         `json:"service"`
				Host       string         `json:"host"`
				Message    string         `json:"message"`
				Attributes map[string]any `json:"attributes"`
			} `json:"attributes"`
		} `json:"data"`
	}
	if err := json.Unmarshal([]byte(said), &found); err != nil {
		return askdesk.Reply{Head: "the logs did not parse", Body: askdesk.FirstBytes(said, replyLimit)}
	}
	var lines strings.Builder
	listed := 0
	for _, entry := range found.Data {
		log := entry.Attributes
		line := fmt.Sprintf("%s %s %s %s: %s", log.Timestamp, log.Status, log.Service, log.Host, clip(log.Message))
		if len(log.Attributes) > 0 {
			line += " " + attributes(log.Attributes)
		}
		if lines.Len()+len(line)+1 > replyLimit {
			break
		}
		lines.WriteString(line + "\n")
		listed++
	}
	return askdesk.Reply{Head: fmt.Sprintf("the newest %d of %d logs read, %s to %s", listed, len(found.Data), from.UTC().Format(time.RFC3339), to.UTC().Format(time.RFC3339)), Body: lines.String()}
}

func (d *Desk) spans(ctx context.Context, query string, from, to time.Time) askdesk.Reply {
	said, failure := d.post(ctx, d.Settings.Datadog.API+"/api/v2/spans/events/search", map[string]any{
		"data": map[string]any{"type": "search_request", "attributes": map[string]any{
			"filter": map[string]string{"query": query, "from": from.UTC().Format(time.RFC3339), "to": to.UTC().Format(time.RFC3339)},
			"sort":   "-timestamp",
			"page":   map[string]int{"limit": listLimit},
		}},
	})
	if failure != nil {
		return *failure
	}
	var found struct {
		Data []struct {
			Attributes struct {
				Service        string         `json:"service"`
				ResourceName   string         `json:"resource_name"`
				StartTimestamp string         `json:"start_timestamp"`
				EndTimestamp   string         `json:"end_timestamp"`
				TraceID        string         `json:"trace_id"`
				SpanID         string         `json:"span_id"`
				Custom         map[string]any `json:"custom"`
			} `json:"attributes"`
		} `json:"data"`
	}
	if err := json.Unmarshal([]byte(said), &found); err != nil {
		return askdesk.Reply{Head: "the spans did not parse", Body: askdesk.FirstBytes(said, replyLimit)}
	}
	var lines strings.Builder
	listed := 0
	for _, entry := range found.Data {
		span := entry.Attributes
		took := ""
		start, startErr := time.Parse(time.RFC3339Nano, span.StartTimestamp)
		end, endErr := time.Parse(time.RFC3339Nano, span.EndTimestamp)
		if startErr == nil && endErr == nil {
			took = end.Sub(start).String()
		}
		line := fmt.Sprintf("%s %s %s took=%s trace=%s span=%s", span.StartTimestamp, span.Service, clip(span.ResourceName), took, span.TraceID, span.SpanID)
		if len(span.Custom) > 0 {
			line += " " + attributes(span.Custom)
		}
		if lines.Len()+len(line)+1 > replyLimit {
			break
		}
		lines.WriteString(line + "\n")
		listed++
	}
	return askdesk.Reply{Head: fmt.Sprintf("the newest %d of %d spans read, %s to %s", listed, len(found.Data), from.UTC().Format(time.RFC3339), to.UTC().Format(time.RFC3339)), Body: lines.String()}
}

func (d *Desk) metric(ctx context.Context, query string, from, to time.Time) askdesk.Reply {
	values := url.Values{"query": {query}, "from": {strconv.FormatInt(from.Unix(), 10)}, "to": {strconv.FormatInt(to.Unix(), 10)}}
	said, failure := d.get(ctx, d.Settings.Datadog.API+"/api/v1/query?"+values.Encode(), d.datadog)
	if failure != nil {
		return *failure
	}
	var found struct {
		Status string `json:"status"`
		Error  string `json:"error"`
		Series []struct {
			Metric    string        `json:"metric"`
			Scope     string        `json:"scope"`
			Pointlist [][2]*float64 `json:"pointlist"`
		} `json:"series"`
	}
	if err := json.Unmarshal([]byte(said), &found); err != nil {
		return askdesk.Reply{Head: "the metric did not parse", Body: askdesk.FirstBytes(said, replyLimit)}
	}
	if found.Status != "ok" {
		return askdesk.Reply{Head: "the query failed", Body: found.Error}
	}
	var lines strings.Builder
	for _, series := range found.Series {
		fmt.Fprintf(&lines, "%s{%s}: %d points\n", series.Metric, series.Scope, len(series.Pointlist))
		for _, point := range series.Pointlist {
			if point[0] == nil || point[1] == nil {
				continue
			}
			fmt.Fprintf(&lines, "  %s %g\n", time.UnixMilli(int64(*point[0])).UTC().Format(time.RFC3339), *point[1])
		}
	}
	return askdesk.Reply{Head: fmt.Sprintf("%d series, %s to %s", len(found.Series), from.UTC().Format(time.RFC3339), to.UTC().Format(time.RFC3339)), Body: askdesk.FirstBytes(lines.String(), replyLimit)}
}

func (d *Desk) slackRead(ctx context.Context, verb string, rest []string) (askdesk.Reply, string) {
	if verb != "thread" || len(rest) != 1 || !permalink.MatchString(rest[0]) {
		return askdesk.Reply{}, "slack reads one thread with slack thread <permalink>, such as https://<workspace>.slack.com/archives/C0123ABCD/p1793903489566279, and writes nothing"
	}
	result, err := d.Runner.Run(ctx, shell.Command{
		Argv: []string{"cc-slack", "thread", "--url", rest[0], "--limit", strconv.Itoa(slackLimit)}, Timeout: readTimeout, Idempotent: true,
	})
	if err != nil {
		return askdesk.Reply{Head: "the read did not run", Body: askdesk.LastBytes(strings.TrimSpace(result.Stderr+"\n"+err.Error()), replyLimit)}, ""
	}
	var thread struct {
		Messages []struct {
			TS       string `json:"ts"`
			UserName string `json:"user_name"`
			User     string `json:"user"`
			Text     string `json:"text"`
		} `json:"messages"`
		Truncated bool `json:"truncated"`
	}
	if err := json.Unmarshal([]byte(result.Stdout), &thread); err != nil {
		return askdesk.Reply{Head: "the thread did not parse", Body: askdesk.FirstBytes(result.Stdout, replyLimit)}, ""
	}
	var lines strings.Builder
	for _, message := range thread.Messages {
		who := message.UserName
		if who == "" {
			who = message.User
		}
		at := message.TS
		if seconds, err := strconv.ParseFloat(message.TS, 64); err == nil {
			at = time.Unix(int64(seconds), 0).UTC().Format(time.RFC3339)
		}
		fmt.Fprintf(&lines, "%s %s:\n%s\n\n", at, who, strings.TrimSpace(message.Text))
	}
	head := fmt.Sprintf("%d messages", len(thread.Messages))
	if thread.Truncated {
		head = fmt.Sprintf("the newest %d messages", len(thread.Messages))
	}
	return askdesk.Reply{Head: head, Body: askdesk.FirstBytes(lines.String(), replyLimit), URL: rest[0]}, ""
}

func (d *Desk) oncallRead(ctx context.Context, args []string) (askdesk.Reply, string) {
	if !slices.ContainsFunc(oncallRead, func(verb []string) bool {
		return len(args) >= len(verb) && slices.Equal(args[:len(verb)], verb)
	}) {
		return askdesk.Reply{}, oncallUsage
	}
	result, err := d.Runner.Run(ctx, shell.Command{
		Argv: slices.Concat(strings.Fields(d.Settings.Oncall.CLI), args), Dir: d.Settings.Oncall.Checkout,
		Env: []string{"AWS_PROFILE=" + d.Settings.SSM.Profile}, Timeout: readTimeout, Idempotent: true,
	})
	if err != nil {
		head := "the read did not run"
		var exit *shell.ExitError
		if errors.As(err, &exit) {
			head = fmt.Sprintf("the read exited %d", exit.Code)
		}
		return askdesk.Reply{Head: head, Body: askdesk.LastBytes(strings.TrimSpace(result.Stderr+"\n"+result.Stdout), replyLimit)}, ""
	}
	cut := askdesk.FirstBytes(result.Stdout, replyLimit)
	return askdesk.Reply{Head: fmt.Sprintf("%d of %d bytes", len(cut), len(result.Stdout)), Body: cut}, ""
}

func (d *Desk) get(ctx context.Context, address string, keys []key) (string, *askdesk.Reply) {
	ask, err := http.NewRequestWithContext(ctx, http.MethodGet, address, nil)
	if err != nil {
		return "", &askdesk.Reply{Head: "the read did not run", Body: err.Error()}
	}
	return d.send(ask, keys)
}

func (d *Desk) post(ctx context.Context, address string, body any) (string, *askdesk.Reply) {
	raw, err := json.Marshal(body)
	if err != nil {
		return "", &askdesk.Reply{Head: "the read did not run", Body: err.Error()}
	}
	ask, err := http.NewRequestWithContext(ctx, http.MethodPost, address, bytes.NewReader(raw))
	if err != nil {
		return "", &askdesk.Reply{Head: "the read did not run", Body: err.Error()}
	}
	ask.Header.Set("Content-Type", "application/json")
	return d.send(ask, d.datadog)
}

func (d *Desk) send(ask *http.Request, keys []key) (string, *askdesk.Reply) {
	for _, k := range keys {
		ask.Header.Set(k.header, k.value)
	}
	answer, err := d.HTTP.Do(ask)
	if err != nil {
		return "", &askdesk.Reply{Head: "the read did not run", Body: err.Error()}
	}
	defer func() { _ = answer.Body.Close() }()
	said, err := io.ReadAll(answer.Body)
	if err != nil {
		return "", &askdesk.Reply{Head: "the read did not finish", Body: err.Error()}
	}
	if answer.StatusCode != http.StatusOK {
		return "", &askdesk.Reply{Head: fmt.Sprintf("the read answered HTTP %d", answer.StatusCode), Body: askdesk.FirstBytes(string(said), replyLimit)}
	}
	return string(said), nil
}

func attributes(found map[string]any) string {
	raw, err := json.Marshal(found)
	if err != nil {
		return ""
	}
	return clip(string(raw))
}

func clip(text string) string {
	text = strings.ReplaceAll(strings.TrimSpace(text), "\n", " ")
	if len(text) <= messageLimit {
		return text
	}
	return strings.ToValidUTF8(text[:messageLimit], "") + "…"
}
