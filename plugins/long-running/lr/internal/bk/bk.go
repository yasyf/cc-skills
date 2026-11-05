package bk

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"maps"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	Lane = "bk-desk"

	askKind     = "ask"
	answerKind  = "answer"
	openStatus  = "open"
	catchUp     = time.Hour
	fresh       = time.Minute
	pollEvery   = 2 * time.Second
	readTimeout = 5 * time.Minute
	tokenWait   = time.Minute
	replyLimit  = 16 << 10
	bodiesDir   = "bk-desk"
	buildsPage  = 100
	buildsUsage = "builds takes <pipeline> [--branch <glob>] [--since <date or RFC3339 time, UTC>]"
	usage       = "name one read: bk build <pipeline> <number>, bk jobs <pipeline> <number>, bk builds <pipeline> [--branch <glob>] [--since <date>], bk log <pipeline> <number> <job>, bk grep <pipeline> <number> <job> <regex>, or bk api <path>"
)

var (
	request     = regexp.MustCompile(`\bbk\s+(\S+)(.*)`)
	logEscapes  = regexp.MustCompile(`\x1b\[[0-9;]*[A-Za-z]|\x1b_bk;t=\d+\x07`)
	unlistedJob = []string{"passed", "skipped", "broken", "not_run"}
)

type Settings struct {
	CLI          string   `json:"cli"`
	Checkout     string   `json:"checkout"`
	TokenCommand []string `json:"token_command"`
}

func LoadSettings(path string) (Settings, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return Settings{}, err
	}
	var s Settings
	if err := json.Unmarshal(raw, &s); err != nil || strings.TrimSpace(s.CLI) == "" || s.Checkout == "" || len(s.TokenCommand) == 0 {
		return Settings{}, fmt.Errorf("%s needs cli, checkout and token_command, the command that prints this host's read-only Buildkite token", path)
	}
	return s, nil
}

type reply struct {
	head, body, url string
	at              time.Time
}

type Desk struct {
	Runner   shell.Runner
	CLI      []string
	Checkout string
	Drive    string
	Bodies   string
	Now      func() time.Time
	Log      io.Writer

	token string
	cache map[string]reply
}

func NewDesk(ctx context.Context, runner shell.Runner, settings Settings, drive, state string, now func() time.Time, log io.Writer) (*Desk, error) {
	printed, err := runner.Run(ctx, shell.Command{Argv: settings.TokenCommand, Timeout: tokenWait})
	if err != nil {
		return nil, fmt.Errorf("the Buildkite token command failed: %w", err)
	}
	token := strings.TrimSpace(printed.Stdout)
	if token == "" {
		return nil, errors.New("the Buildkite token command printed nothing")
	}
	return &Desk{
		Runner: runner, CLI: strings.Fields(settings.CLI), Checkout: settings.Checkout, Drive: drive,
		Bodies: filepath.Join(state, bodiesDir), Now: now, Log: log, token: token, cache: map[string]reply{},
	}, nil
}

func (d *Desk) Serve(ctx context.Context) error {
	query := client.Query{Drive: d.Drive, Since: d.Now().Add(-catchUp), Kinds: []string{askKind}}
	for {
		next, err := d.pass(ctx, query)
		if err != nil {
			return err
		}
		query = next
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(pollEvery):
		}
	}
}

func (d *Desk) pass(ctx context.Context, query client.Query) (client.Query, error) {
	asks, err := client.Tail(ctx, query)
	if err != nil {
		return query, err
	}
	for _, ask := range asks {
		query = client.Query{Drive: query.Drive, After: ask.Seq, Kinds: query.Kinds}
		if ask.Status != openStatus || !slices.Contains(ask.To, Lane) {
			continue
		}
		if err := d.answer(ctx, ask); err != nil {
			_, _ = fmt.Fprintf(d.Log, "bk-desk: ask #%d from %s stays open: %v\n", ask.Seq, ask.Lane, err)
		}
	}
	return query, nil
}

func (d *Desk) answer(ctx context.Context, ask client.Record) error {
	read := d.read(ctx, ask.Text)
	sum := sha256.Sum256([]byte(read.body))
	path := filepath.Join(d.Bodies, hex.EncodeToString(sum[:])[:16]+".txt")
	if err := os.MkdirAll(d.Bodies, 0o750); err != nil {
		return err
	}
	if err := os.WriteFile(path, []byte(read.body), 0o600); err != nil {
		return err
	}
	_, err := client.Post(ctx, client.Draft{
		Drive: d.Drive, Lane: Lane, Kind: answerKind, Text: read.head, To: []string{ask.Lane},
		Resolves: ask.Seq, Path: path, URL: read.url, Force: true,
	})
	return err
}

func (d *Desk) read(ctx context.Context, text string) reply {
	args, refusal := parse(text)
	if refusal != "" {
		return reply{head: "bk read refused", body: refusal}
	}
	key := strings.Join(args, " ")
	now := d.Now()
	if hit, found := d.cache[key]; found && now.Sub(hit.at) < fresh {
		return hit
	}
	maps.DeleteFunc(d.cache, func(_ string, kept reply) bool { return now.Sub(kept.at) >= fresh })
	read := d.fetch(ctx, args)
	read.head = "bk " + key + ": " + read.head
	read.at = now
	d.cache[key] = read
	return read
}

func parse(text string) ([]string, string) {
	match := request.FindStringSubmatch(text)
	if match == nil {
		return nil, usage
	}
	rest := strings.Fields(match[2])
	switch match[1] {
	case "build":
		if len(rest) == 2 {
			if _, err := strconv.Atoi(rest[1]); err == nil {
				return append([]string{"build"}, rest...), ""
			}
		}
		return nil, "build takes <pipeline> <number>"
	case "jobs":
		if len(rest) == 2 {
			if _, err := strconv.Atoi(rest[1]); err == nil {
				return append([]string{"jobs"}, rest...), ""
			}
		}
		return nil, "jobs takes <pipeline> <number>"
	case "builds":
		return parseBuilds(rest)
	case "grep":
		if len(rest) < 4 {
			return nil, "grep takes <pipeline> <number> <job> <regex>"
		}
		pattern := strings.Join(rest[3:], " ")
		if _, err := regexp.Compile(pattern); err != nil {
			return nil, fmt.Sprintf("grep's regex does not compile: %v", err)
		}
		return []string{"grep", rest[0], rest[1], rest[2], pattern}, ""
	case "log":
		if len(rest) == 3 {
			return append([]string{"log"}, rest...), ""
		}
		return nil, "log takes <pipeline> <number> <job>"
	case "api":
		if len(rest) != 1 || strings.HasPrefix(rest[0], "-") {
			return nil, "api reads one path with GET, and takes no -X or -d"
		}
		if strings.HasPrefix("/"+strings.TrimPrefix(rest[0], "/"), "/organizations/") {
			return nil, "api reads only this desk's own organization; drop the /organizations/<org> prefix"
		}
		return []string{"api", rest[0]}, ""
	}
	return nil, fmt.Sprintf("bk %s is no read; %s", match[1], usage)
}

func parseBuilds(rest []string) ([]string, string) {
	if len(rest) == 0 || strings.HasPrefix(rest[0], "-") {
		return nil, buildsUsage
	}
	flags := flag.NewFlagSet("builds", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	branch := flags.String("branch", "", "")
	since := flags.String("since", "", "")
	if err := flags.Parse(rest[1:]); err != nil || flags.NArg() != 0 {
		return nil, buildsUsage
	}
	args := []string{"builds", rest[0]}
	if *branch != "" {
		args = append(args, "branch="+*branch)
	}
	if *since == "" {
		return args, ""
	}
	from, err := time.Parse(time.DateOnly, *since)
	if err != nil {
		from, err = time.Parse(time.RFC3339, *since)
	}
	if err != nil {
		return nil, buildsUsage
	}
	return append(args, "created_from="+from.UTC().Format(time.RFC3339)), ""
}

func (d *Desk) fetch(ctx context.Context, args []string) reply {
	switch args[0] {
	case "builds":
		return d.listBuilds(ctx, args[1], args[2:])
	case "build":
		said, failure := d.run(ctx, "api", "-no-wait", "/pipelines/"+args[1]+"/builds/"+args[2])
		if failure != nil {
			return *failure
		}
		return summary(said)
	case "jobs":
		said, failure := d.run(ctx, "api", "-no-wait", "/pipelines/"+args[1]+"/builds/"+args[2])
		if failure != nil {
			return *failure
		}
		return everyJob(said)
	case "grep":
		said, failure := d.run(ctx, "log", args[1], args[2], args[3])
		if failure != nil {
			return *failure
		}
		return matching(logEscapes.ReplaceAllString(said, ""), regexp.MustCompile(args[4]))
	case "log":
		said, failure := d.run(ctx, args...)
		if failure != nil {
			return *failure
		}
		cut := lastBytes(logEscapes.ReplaceAllString(said, ""), replyLimit)
		return reply{head: fmt.Sprintf("%d bytes of its log", len(cut)), body: cut}
	}
	said, failure := d.run(ctx, "api", "-no-wait", args[1])
	if failure != nil {
		return *failure
	}
	cut := firstBytes(said, replyLimit)
	return reply{head: fmt.Sprintf("%d of %d bytes", len(cut), len(said)), body: cut}
}

func (d *Desk) run(ctx context.Context, args ...string) (string, *reply) {
	result, err := d.Runner.Run(ctx, shell.Command{
		Argv: slices.Concat(d.CLI, args), Dir: d.Checkout,
		Env: []string{"BUILDKITE_TOKEN=" + d.token}, Timeout: readTimeout, Idempotent: true,
	})
	if err == nil {
		return result.Stdout, nil
	}
	said := lastBytes(strings.TrimSpace(result.Stderr), replyLimit)
	var exit *shell.ExitError
	if errors.As(err, &exit) {
		head := fmt.Sprintf("the read exited %d", exit.Code)
		if exit.Tries > 1 {
			head += fmt.Sprintf(" after %d tries", exit.Tries)
		}
		return "", &reply{head: head, body: said}
	}
	return "", &reply{head: "the read did not run", body: err.Error()}
}

type build struct {
	Number int    `json:"number"`
	State  string `json:"state"`
	Branch string `json:"branch"`
	Commit string `json:"commit"`
	WebURL string `json:"web_url"`
	Jobs   []job  `json:"jobs"`

	Message   string `json:"message"`
	CreatedAt string `json:"created_at"`
}

type job struct {
	ID         string `json:"id"`
	Type       string `json:"type"`
	Name       string `json:"name"`
	State      string `json:"state"`
	ExitStatus *int   `json:"exit_status"`
	SoftFailed bool   `json:"soft_failed"`
}

func (d *Desk) listBuilds(ctx context.Context, pipeline string, filters []string) reply {
	query := url.Values{"per_page": {strconv.Itoa(buildsPage)}, "exclude_jobs": {"true"}, "exclude_pipeline": {"true"}}
	for _, filter := range filters {
		name, value, _ := strings.Cut(filter, "=")
		query.Set(name, value)
	}
	var lines strings.Builder
	listed := 0
	for page := 1; ; page++ {
		query.Set("page", strconv.Itoa(page))
		said, failure := d.run(ctx, "api", "-no-wait", "/pipelines/"+pipeline+"/builds?"+query.Encode())
		if failure != nil {
			return *failure
		}
		var read []build
		if err := json.Unmarshal([]byte(said), &read); err != nil {
			return reply{head: "the builds did not parse", body: firstBytes(said, replyLimit)}
		}
		for _, found := range read {
			entry := fmt.Sprintf("%d %s %s %s\n  %s\n", found.Number, found.State, found.Branch, found.CreatedAt, strings.ReplaceAll(strings.TrimSpace(found.Message), "\n", "\n  "))
			if lines.Len()+len(entry) > replyLimit {
				return reply{head: fmt.Sprintf("the newest %d builds; older ones did not fit, so narrow with --branch or --since", listed), body: lines.String()}
			}
			lines.WriteString(entry)
			listed++
		}
		if len(read) < buildsPage {
			return reply{head: fmt.Sprintf("%d builds", listed), body: lines.String()}
		}
	}
}

func summary(said string) reply {
	var read build
	if err := json.Unmarshal([]byte(said), &read); err != nil {
		return reply{head: "the build did not parse", body: firstBytes(said, replyLimit)}
	}
	var scripts, unlisted int
	var lines strings.Builder
	fmt.Fprintf(&lines, "build %d %s on %s at %s\n%s\n", read.Number, read.State, read.Branch, read.Commit, read.WebURL)
	for _, step := range read.Jobs {
		if step.Type != "script" {
			continue
		}
		scripts++
		if slices.Contains(unlistedJob, step.State) {
			unlisted++
			continue
		}
		exit := "-"
		if step.ExitStatus != nil {
			exit = strconv.Itoa(*step.ExitStatus)
		}
		soft := ""
		if step.SoftFailed {
			soft = " soft"
		}
		fmt.Fprintf(&lines, "%s%s exit=%s job=%s %s\n", step.State, soft, exit, step.ID, step.Name)
	}
	return reply{
		head: fmt.Sprintf("%s, %d of %d jobs not passed", read.State, scripts-unlisted, scripts),
		body: firstBytes(lines.String(), replyLimit),
		url:  read.WebURL,
	}
}

func everyJob(said string) reply {
	var read build
	if err := json.Unmarshal([]byte(said), &read); err != nil {
		return reply{head: "the build did not parse", body: firstBytes(said, replyLimit)}
	}
	var lines strings.Builder
	scripts := 0
	for _, step := range read.Jobs {
		if step.Type == "script" {
			scripts++
			fmt.Fprintf(&lines, "%s job=%s %s\n", step.State, step.ID, step.Name)
		}
	}
	return reply{head: fmt.Sprintf("%s, %d jobs", read.State, scripts), body: firstBytes(lines.String(), replyLimit), url: read.WebURL}
}

func matching(log string, pattern *regexp.Regexp) reply {
	var lines strings.Builder
	found := 0
	for line := range strings.Lines(log) {
		if pattern.MatchString(line) {
			found++
			lines.WriteString(line)
		}
	}
	return reply{head: fmt.Sprintf("%d matching lines of a %d byte log", found, len(log)), body: firstBytes(lines.String(), replyLimit)}
}

func firstBytes(text string, limit int) string {
	if len(text) <= limit {
		return text
	}
	cut := strings.ToValidUTF8(text[:limit], "")
	return fmt.Sprintf("%s\n… cut: the first %d of %d bytes\n", cut, len(cut), len(text))
}

func lastBytes(text string, limit int) string {
	if len(text) <= limit {
		return text
	}
	cut := strings.ToValidUTF8(text[len(text)-limit:], "")
	return fmt.Sprintf("… cut: the last %d of %d bytes\n%s", len(cut), len(text), cut)
}
