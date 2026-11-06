package bk

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"html"
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

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/askdesk"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	Lane = "bk-desk"
	Duty = "answers lanes' Buildkite reads over cci"

	fresh       = time.Minute
	readTimeout = 5 * time.Minute
	tokenWait   = time.Minute
	replyLimit  = 16 << 10
	bodiesDir   = "bk-desk"
	perPage     = 100
	buildsUsage = "builds takes <pipeline> [--branch <glob>] [--since <date or RFC3339 time, UTC>]"
	logUsage    = "log takes <pipeline> <number> <job id, or step key and its N/M for a parallel step> [--grep <regex>] [--context <lines, 0 to 50>]"
	jobFailed   = "failed"
	failureMark = `--- FAIL|panic:|FAIL\s`
	maxContext  = 50
	apiUsage    = "api reads one path with GET and answers its raw JSON, or with --fields <name,parent.child,...> one line per element; it takes no other flag such as -X, -d or --jq"
	usage       = "name one read: bk build <pipeline> <number>, bk jobs <pipeline> <number>, bk builds <pipeline> [--branch <glob>] [--since <date>], bk annotations <pipeline> <number> [<context>], bk log <pipeline> <number> <job id or step key> [<N/M>] [--grep <regex>] [--context <n>], bk grep <pipeline> <number> <job id or step key> <regex>, or bk api <path> [--fields <names>]"
)

var (
	request       = regexp.MustCompile(`\bbk\s+(\S+)(.*)`)
	logEscapes    = regexp.MustCompile(`\x1b\[[0-9;]*[A-Za-z]|\x1b_bk;t=\d+\x07`)
	htmlTags      = regexp.MustCompile(`<[^>]*>`)
	parallelShard = regexp.MustCompile(`^[1-9][0-9]*/[1-9][0-9]*$`)
	jobID         = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)
	unlistedJob   = []string{"passed", "skipped", "broken", "not_run"}
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
	return d.loop().Serve(ctx)
}

func (d *Desk) loop() askdesk.Desk {
	return askdesk.Desk{Lane: Lane, Drive: d.Drive, Duty: Duty, Bodies: d.Bodies, Now: d.Now, Log: d.Log, Read: func(ctx context.Context, text string) askdesk.Reply {
		read := d.read(ctx, text)
		return askdesk.Reply{Head: read.head, Body: read.body, URL: read.url}
	}}
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
	case "annotations":
		if len(rest) == 2 || len(rest) == 3 {
			if _, err := strconv.Atoi(rest[1]); err == nil {
				return append([]string{"annotations"}, rest...), ""
			}
		}
		return nil, "annotations takes <pipeline> <number> [<context>]"
	case "grep":
		if len(rest) < 4 {
			return nil, "grep takes <pipeline> <number> <job id or step key> <regex>"
		}
		pattern := strings.Join(rest[3:], " ")
		if _, err := regexp.Compile(pattern); err != nil {
			return nil, fmt.Sprintf("grep's regex does not compile: %v", err)
		}
		return []string{"grep", rest[0], rest[1], rest[2], pattern}, ""
	case "log":
		return parseLog(rest)
	case "api":
		if len(rest) == 0 || strings.HasPrefix(rest[0], "-") {
			return nil, apiUsage
		}
		flags := flag.NewFlagSet("api", flag.ContinueOnError)
		flags.SetOutput(io.Discard)
		fields := flags.String("fields", "", "")
		if err := flags.Parse(rest[1:]); err != nil || flags.NArg() != 0 {
			return nil, apiUsage
		}
		path := "/" + strings.TrimPrefix(rest[0], "/")
		if inOrg, found := strings.CutPrefix(path, "/organizations/"); found {
			_, own, _ := strings.Cut(inOrg, "/")
			return nil, "api reads only this desk's own organization, so drop the /organizations/<org> prefix: bk api /" + own
		}
		if *fields == "" {
			return []string{"api", rest[0]}, ""
		}
		return []string{"api", rest[0], *fields}, ""
	}
	return nil, fmt.Sprintf("bk %s is no read; %s", match[1], usage)
}

func parseLog(rest []string) ([]string, string) {
	if len(rest) < 3 || slices.ContainsFunc(rest[:3], func(word string) bool { return strings.HasPrefix(word, "-") }) {
		return nil, logUsage
	}
	shard, flagged := "", rest[3:]
	if len(flagged) > 0 && parallelShard.MatchString(flagged[0]) && !jobID.MatchString(rest[2]) {
		shard, flagged = flagged[0], flagged[1:]
	}
	flags := flag.NewFlagSet("log", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	pattern := flags.String("grep", "", "")
	width := flags.Int("context", 3, "")
	if err := flags.Parse(flagged); err != nil || flags.NArg() != 0 || *width < 0 || *width > maxContext {
		return nil, logUsage
	}
	if _, err := regexp.Compile(*pattern); err != nil {
		return nil, fmt.Sprintf("log's --grep regex does not compile: %v", err)
	}
	return []string{"log", rest[0], rest[1], rest[2], *pattern, strconv.Itoa(*width), shard}, ""
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
	case "annotations":
		return d.annotations(ctx, args[1], args[2], args[3:])
	case "grep":
		said, _, failure := d.jobLog(ctx, args[1], args[2], args[3], "")
		if failure != nil {
			return *failure
		}
		return matching(logEscapes.ReplaceAllString(said, ""), regexp.MustCompile(args[4]))
	case "log":
		said, state, failure := d.jobLog(ctx, args[1], args[2], args[3], args[6])
		if failure != nil {
			return *failure
		}
		log := logEscapes.ReplaceAllString(said, "")
		pattern := args[4]
		if pattern == "" && state == "" {
			state = d.jobState(ctx, args[1], args[2], args[3])
		}
		if pattern == "" && state == jobFailed {
			pattern = failureMark
		}
		width, _ := strconv.Atoi(args[5])
		if pattern != "" {
			if found := around(log, regexp.MustCompile(pattern), width); found.body != "" || args[4] != "" {
				return found
			}
		}
		cut := askdesk.LastBytes(log, replyLimit)
		return reply{head: fmt.Sprintf("%d bytes of its log", len(cut)), body: cut}
	}
	said, failure := d.run(ctx, "api", "-no-wait", args[1])
	if failure != nil {
		return *failure
	}
	if len(args) == 3 {
		return picked(said, strings.Split(args[2], ","))
	}
	cut := askdesk.FirstBytes(said, replyLimit)
	return reply{head: fmt.Sprintf("%d of %d bytes", len(cut), len(said)), body: cut}
}

func picked(said string, fields []string) reply {
	var doc any
	if err := json.Unmarshal([]byte(said), &doc); err != nil {
		return reply{head: "the answer did not parse", body: askdesk.FirstBytes(said, replyLimit)}
	}
	rows, listed := doc.([]any)
	if !listed {
		rows = []any{doc}
	}
	var lines strings.Builder
	for shown, row := range rows {
		values := make([]string, len(fields))
		for i, field := range fields {
			values[i] = fieldText(row, field)
		}
		entry := strings.Join(values, " ") + "\n"
		if lines.Len()+len(entry) > replyLimit {
			return reply{head: fmt.Sprintf("the first %d of %d rows; the rest did not fit, so narrow the path's query", shown, len(rows)), body: lines.String()}
		}
		lines.WriteString(entry)
	}
	return reply{head: fmt.Sprintf("%d rows", len(rows)), body: lines.String()}
}

func fieldText(row any, path string) string {
	value := row
	for key := range strings.SplitSeq(path, ".") {
		object, isObject := value.(map[string]any)
		if !isObject {
			return "-"
		}
		value = object[key]
	}
	switch typed := value.(type) {
	case nil:
		return "-"
	case string:
		first, _, _ := strings.Cut(strings.TrimSpace(typed), "\n")
		return first
	}
	raw, _ := json.Marshal(value)
	return string(raw)
}

func (d *Desk) run(ctx context.Context, args ...string) (string, *reply) {
	result, err := d.Runner.Run(ctx, shell.Command{
		Argv: slices.Concat(d.CLI, args), Dir: d.Checkout,
		Env: []string{"BUILDKITE_TOKEN=" + d.token}, Timeout: readTimeout, Idempotent: true,
	})
	if err == nil {
		return result.Stdout, nil
	}
	said := askdesk.LastBytes(strings.TrimSpace(result.Stderr), replyLimit)
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

func (d *Desk) jobLog(ctx context.Context, pipeline, number, job, shard string) (string, string, *reply) {
	if jobID.MatchString(job) {
		log, failure := d.run(ctx, "log", pipeline, number, job)
		return log, "", failure
	}
	read, failure := d.build(ctx, pipeline, number)
	if failure != nil {
		return "", "", failure
	}
	var keyed strings.Builder
	var onlyID, onlyState string
	count := 0
	for _, step := range read.Jobs {
		if step.StepKey == job && !step.Retried && (shard == "" || step.shard() == shard) {
			count++
			onlyID, onlyState = step.ID, step.State
			fmt.Fprintf(&keyed, "%s job=%s %s%s\n", step.State, step.ID, step.Name, step.label())
		}
	}
	if count != 1 {
		named := strings.TrimSpace(job + " " + shard)
		return "", "", &reply{
			head: fmt.Sprintf("%d jobs of build %s carry step key %s, so name one job id, or the step key and its N/M; bk jobs %s %s lists them", count, number, named, pipeline, number),
			body: askdesk.FirstBytes(keyed.String(), replyLimit),
		}
	}
	log, failure := d.run(ctx, "log", pipeline, number, onlyID)
	return log, onlyState, failure
}

func (d *Desk) build(ctx context.Context, pipeline, number string) (build, *reply) {
	said, failure := d.run(ctx, "api", "-no-wait", "/pipelines/"+pipeline+"/builds/"+number)
	if failure != nil {
		return build{}, failure
	}
	var read build
	if err := json.Unmarshal([]byte(said), &read); err != nil {
		return build{}, &reply{head: "the build did not parse", body: askdesk.FirstBytes(said, replyLimit)}
	}
	return read, nil
}

func (d *Desk) jobState(ctx context.Context, pipeline, number, id string) string {
	read, failure := d.build(ctx, pipeline, number)
	if failure != nil {
		return ""
	}
	for _, step := range read.Jobs {
		if step.ID == id {
			return step.State
		}
	}
	return ""
}

type annotation struct {
	Context  string `json:"context"`
	Style    string `json:"style"`
	BodyHTML string `json:"body_html"`
}

func (a annotation) text() string {
	return strings.TrimSpace(html.UnescapeString(htmlTags.ReplaceAllString(a.BodyHTML, "")))
}

func (d *Desk) annotations(ctx context.Context, pipeline, number string, wanted []string) reply {
	var every []annotation
	for page := 1; ; page++ {
		said, failure := d.run(ctx, "api", "-no-wait", fmt.Sprintf("/pipelines/%s/builds/%s/annotations?page=%d&per_page=%d", pipeline, number, page, perPage))
		if failure != nil {
			return *failure
		}
		var read []annotation
		if err := json.Unmarshal([]byte(said), &read); err != nil {
			return reply{head: "the annotations did not parse", body: askdesk.FirstBytes(said, replyLimit)}
		}
		every = append(every, read...)
		if len(read) < perPage {
			break
		}
	}
	var lines strings.Builder
	if len(wanted) == 1 {
		for _, found := range every {
			if found.Context == wanted[0] {
				text := found.text()
				return reply{head: fmt.Sprintf("%s, %d bytes", found.Style, len(text)), body: askdesk.FirstBytes(text, replyLimit)}
			}
		}
		for _, found := range every {
			fmt.Fprintf(&lines, "%s %s\n", found.Style, found.Context)
		}
		return reply{head: fmt.Sprintf("none of its %d annotations has that context", len(every)), body: lines.String()}
	}
	pointed := 0
	for _, found := range every {
		text := found.text()
		entry := fmt.Sprintf("── %s %s\n%s\n\n", found.Style, found.Context, text)
		if lines.Len()+len(entry) > replyLimit {
			pointed++
			entry = fmt.Sprintf("── %s %s: %d bytes, so bk annotations %s %s %s reads it\n\n", found.Style, found.Context, len(text), pipeline, number, found.Context)
		}
		lines.WriteString(entry)
	}
	head := fmt.Sprintf("%d annotations", len(every))
	if pointed > 0 {
		head += fmt.Sprintf(", %d too long to fit and named by context", pointed)
	}
	return reply{head: head, body: askdesk.FirstBytes(lines.String(), replyLimit)}
}

type build struct {
	Number int    `json:"number"`
	State  string `json:"state"`
	Branch string `json:"branch"`
	Commit string `json:"commit"`
	WebURL string `json:"web_url"`
	Jobs   []job  `json:"jobs"`

	Message string `json:"message"`
}

type job struct {
	ID         string `json:"id"`
	Type       string `json:"type"`
	Name       string `json:"name"`
	State      string `json:"state"`
	ExitStatus *int   `json:"exit_status"`
	SoftFailed bool   `json:"soft_failed"`
	StepKey    string `json:"step_key"`
	Retried    bool   `json:"retried"`
	Index      *int   `json:"parallel_group_index"`
	Total      *int   `json:"parallel_group_total"`
}

func (j job) shard() string {
	if j.Index == nil || j.Total == nil {
		return ""
	}
	return fmt.Sprintf("%d/%d", *j.Index+1, *j.Total)
}

func (j job) label() string {
	if shard := j.shard(); shard != "" {
		return " " + shard
	}
	return ""
}

func (d *Desk) listBuilds(ctx context.Context, pipeline string, filters []string) reply {
	query := url.Values{"per_page": {strconv.Itoa(perPage)}, "exclude_jobs": {"true"}, "exclude_pipeline": {"true"}}
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
			return reply{head: "the builds did not parse", body: askdesk.FirstBytes(said, replyLimit)}
		}
		for _, found := range read {
			subject, _, _ := strings.Cut(strings.TrimSpace(found.Message), "\n")
			entry := fmt.Sprintf("%d %s %s %s %s\n", found.Number, found.State, found.Branch, found.Commit, subject)
			if lines.Len()+len(entry) > replyLimit {
				return reply{head: fmt.Sprintf("the newest %d builds; older ones did not fit, so narrow with --branch or --since", listed), body: lines.String()}
			}
			lines.WriteString(entry)
			listed++
		}
		if len(read) < perPage {
			return reply{head: fmt.Sprintf("%d builds", listed), body: lines.String()}
		}
	}
}

func summary(said string) reply {
	var read build
	if err := json.Unmarshal([]byte(said), &read); err != nil {
		return reply{head: "the build did not parse", body: askdesk.FirstBytes(said, replyLimit)}
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
		fmt.Fprintf(&lines, "%s%s exit=%s job=%s %s%s\n", step.State, soft, exit, step.ID, step.Name, step.label())
	}
	return reply{
		head: fmt.Sprintf("%s, %d of %d jobs not passed", read.State, scripts-unlisted, scripts),
		body: askdesk.FirstBytes(lines.String(), replyLimit),
		url:  read.WebURL,
	}
}

func everyJob(said string) reply {
	var read build
	if err := json.Unmarshal([]byte(said), &read); err != nil {
		return reply{head: "the build did not parse", body: askdesk.FirstBytes(said, replyLimit)}
	}
	var lines strings.Builder
	scripts := 0
	for _, step := range read.Jobs {
		if step.Type == "script" {
			scripts++
			fmt.Fprintf(&lines, "%s job=%s %s%s\n", step.State, step.ID, step.Name, step.label())
		}
	}
	return reply{head: fmt.Sprintf("%s, %d jobs", read.State, scripts), body: askdesk.FirstBytes(lines.String(), replyLimit), url: read.WebURL}
}

func around(log string, pattern *regexp.Regexp, width int) reply {
	lines := strings.SplitAfter(log, "\n")
	var matches []int
	for i, line := range lines {
		if pattern.MatchString(line) {
			matches = append(matches, i)
		}
	}
	var body strings.Builder
	shown, end := 0, 0
	for n, at := range matches {
		if at < end {
			shown++
			continue
		}
		from, to := max(at-width, end), min(at+width+1, len(lines))
		block := strings.Join(lines[from:to], "")
		if from > end && end > 0 {
			block = "--\n" + block
		}
		if body.Len()+len(block) > replyLimit {
			return reply{head: fmt.Sprintf("%d of %d lines matching %s in a %d byte log, %d more dropped to fit", shown, len(matches), pattern, len(log), len(matches)-n), body: body.String()}
		}
		body.WriteString(block)
		shown++
		end = to
	}
	return reply{head: fmt.Sprintf("%d lines matching %s in a %d byte log, %d lines of context each", len(matches), pattern, len(log), width), body: body.String()}
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
	return reply{head: fmt.Sprintf("%d matching lines of a %d byte log", found, len(log)), body: askdesk.FirstBytes(lines.String(), replyLimit)}
}
