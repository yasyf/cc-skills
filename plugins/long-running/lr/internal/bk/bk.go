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
	"syscall"
	"time"

	"github.com/kballard/go-shellquote"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/askdesk"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	Lane  = "bk-desk"
	Duty  = "answers lanes' Buildkite reads over cci"
	Reads = "bk build <pipeline> <number>, bk jobs <pipeline> <number> [<N/M>] [--state <state>] [--step <regex>], bk failures <pipeline> <number>, bk builds <pipeline> [--branch <glob>] [--since <date>], bk annotations <pipeline> <number> [<context>], bk log <pipeline> <number> <job id or step key> [<N/M>] [--grep '<regex>'] [--context <n>], bk grep <pipeline> <number> <job id or step key> <regex>, bk api <path> [--fields <names>], or one of the three writes, bk retry <pipeline> <number> <job id>, bk create test <commit> <branch> [FORGE_CI_<FLAG>=<value>...], which starts a whole test build, and tools/ci bk rerun <pipeline> <number|dev> --unit <unit> [--count <n>], which runs as tools/ci bk parses it"

	fresh          = time.Minute
	readTimeout    = 5 * time.Minute
	tokenWait      = time.Minute
	replyLimit     = 16 << 10
	bodiesDir      = "bk-desk"
	perPage        = 100
	buildsUsage    = "builds takes <pipeline> [--branch <glob>] [--since <date or RFC3339 time, UTC>]"
	jobsUsage      = "jobs takes <pipeline> <number> [<window N/M>] [--state <job state>] [--step <regex on the job name or step key>]"
	logUsage       = "log takes <pipeline> <number> <job id, or step key and its N/M for a parallel step> [--grep '<regex>'] [--context <lines, 0 to 50>]"
	jobFailed      = "failed"
	failureMark    = `--- FAIL|panic:|FAIL\s`
	maxContext     = 50
	createPipeline = "test"
	createUsage    = "create takes test <full 40-character commit> <branch> [FORGE_CI_<FLAG>=<value>...]; it starts only test builds, and its env takes only FORGE_CI_ flags"
	apiUsage       = "api reads one path with GET and answers its raw JSON, or with --fields <name,parent.child,...> one line per element; it takes no other flag such as -X, -d or --jq; to start a test build, ask bk create test <commit> <branch> [FORGE_CI_<FLAG>=<value>...]"
	usage          = "name one read: " + Reads
)

var (
	request       = regexp.MustCompile(`\bbk\s+(\S+)(.*)`)
	logEscapes    = regexp.MustCompile(`\x1b\[[0-9;]*[A-Za-z]|\x1b_bk;t=\d+\x07`)
	htmlTags      = regexp.MustCompile(`<[^>]*>`)
	parallelShard = regexp.MustCompile(`^[1-9][0-9]*/[1-9][0-9]*$`)
	fixedArity    = map[string]int{"build": 2, "failures": 2, "retry": 3}
	failureLine   = regexp.MustCompile(failureMark + `|error TS\d+|✖|panicked`)
	ciCommandEnd  = regexp.MustCompile("[\n`;]")
	commitSHA     = regexp.MustCompile(`^[0-9a-f]{40}$`)
	ciFlag        = regexp.MustCompile(`^FORGE_CI_[A-Z0-9_]+=\S+$`)
	envPair       = regexp.MustCompile(`^[A-Z][A-Z0-9_]*=`)
	jobID         = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)
	unlistedJob   = []string{"passed", "skipped", "broken", "not_run"}
	unfinishedJob = []string{"scheduled", "assigned", "accepted", "running", "waiting", "limited", "canceling", "timing_out"}
)

type Settings struct {
	CLI               string   `json:"cli"`
	Checkout          string   `json:"checkout"`
	TokenCommand      []string `json:"token_command"`
	RetryTokenCommand []string `json:"retry_token_command"`
}

func LoadSettings(path string) (Settings, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return Settings{}, err
	}
	var s Settings
	if err := json.Unmarshal(raw, &s); err != nil || strings.TrimSpace(s.CLI) == "" || s.Checkout == "" || len(s.TokenCommand) == 0 || len(s.RetryTokenCommand) == 0 {
		return Settings{}, fmt.Errorf("%s needs cli, checkout, token_command, the command that prints this host's read-only Buildkite token, and retry_token_command, the one that prints the token bk retry writes with", path)
	}
	return s, nil
}

type reply struct {
	head, body, url string
	refused         bool
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

	token, retryToken string
	cache             map[string]reply
}

func NewDesk(ctx context.Context, runner shell.Runner, settings Settings, drive, state string, now func() time.Time, log io.Writer) (*Desk, error) {
	token, err := printToken(ctx, runner, settings.TokenCommand)
	if err != nil {
		return nil, err
	}
	retryToken, err := printToken(ctx, runner, settings.RetryTokenCommand)
	if err != nil {
		return nil, err
	}
	return &Desk{
		Runner: runner, CLI: strings.Fields(settings.CLI), Checkout: settings.Checkout, Drive: drive,
		Bodies: filepath.Join(state, bodiesDir), Now: now, Log: log, token: token, retryToken: retryToken, cache: map[string]reply{},
	}, nil
}

func printToken(ctx context.Context, runner shell.Runner, argv []string) (string, error) {
	printed, err := runner.Run(ctx, shell.Command{Argv: argv, Timeout: tokenWait})
	if err != nil {
		return "", fmt.Errorf("the Buildkite token command %s failed: %w", argv[0], err)
	}
	token := strings.TrimSpace(printed.Stdout)
	if token == "" {
		return "", fmt.Errorf("the Buildkite token command %s printed nothing", argv[0])
	}
	return token, nil
}

func (d *Desk) Serve(ctx context.Context, claude string) error {
	return askdesk.ServeInstalled(ctx, d.loop(), claude, syscall.Exec)
}

func (d *Desk) loop() askdesk.Desk {
	return askdesk.Desk{Lane: Lane, Drive: d.Drive, Duty: Duty, Bodies: d.Bodies, Now: d.Now, Log: d.Log, Read: func(ctx context.Context, text string) askdesk.Reply {
		read := d.read(ctx, text)
		return askdesk.Reply{Head: read.head, Body: read.body, URL: read.url, Refused: read.refused}
	}}
}

func (d *Desk) read(ctx context.Context, text string) reply {
	args, refusal := parse(text)
	if refusal != "" {
		return reply{head: "bk read refused", body: refusal, refused: true}
	}
	key := shellquote.Join(args...)
	now := d.Now()
	if hit, found := d.cache[key]; found && now.Sub(hit.at) < fresh {
		return hit
	}
	maps.DeleteFunc(d.cache, func(_ string, kept reply) bool { return now.Sub(kept.at) >= fresh })
	read := d.fetch(ctx, args)
	read.head = "bk " + strings.Join(args, " ") + ": " + read.head
	read.at = now
	d.cache[key] = read
	return read
}

func parse(text string) ([]string, string) {
	match := request.FindStringSubmatch(text)
	if match == nil {
		return nil, usage
	}
	if match[1] == "rerun" {
		command, _, _ := strings.Cut(match[2], ciCommandEnd.FindString(match[2]+"\n"))
		rest, err := shellquote.Split(command)
		if err != nil {
			return nil, fmt.Sprintf("the command does not split into shell words: %v", err)
		}
		return append([]string{match[1]}, rest...), ""
	}
	if match[1] == "create" {
		return parseCreate(strings.Fields(match[2]))
	}
	if match[1] == "jobs" {
		return parseJobs(strings.Fields(match[2]))
	}
	if arity, fixed := fixedArity[match[1]]; fixed {
		words := strings.Fields(match[2])
		match[2] = strings.Join(words[:min(arity, len(words))], " ")
	}
	rest, err := shellquote.Split(match[2])
	if err != nil {
		return nil, fmt.Sprintf("the read does not split into shell words: %v", err)
	}
	switch match[1] {
	case "build":
		if len(rest) == 2 {
			if _, err := strconv.Atoi(rest[1]); err == nil {
				return append([]string{"build"}, rest...), ""
			}
		}
		return nil, "build takes <pipeline> <number>"
	case "failures":
		if len(rest) == 2 {
			if _, err := strconv.Atoi(rest[1]); err == nil {
				return append([]string{"failures"}, rest...), ""
			}
		}
		return nil, "failures takes <pipeline> <number>"
	case "builds":
		return parseBuilds(rest)
	case "retry":
		if len(rest) == 3 && jobID.MatchString(rest[2]) {
			if _, err := strconv.Atoi(rest[1]); err == nil {
				return append([]string{"retry"}, rest...), ""
			}
		}
		return nil, "retry takes <pipeline> <number> <job id>"
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

func (d *Desk) create(ctx context.Context, pipeline, commit, branch string, flags []string) reply {
	env := map[string]string{}
	for _, pair := range flags {
		key, value, _ := strings.Cut(pair, "=")
		env[key] = value
	}
	body, err := json.Marshal(map[string]any{"commit": commit, "branch": branch, "env": env, "ignore_pipeline_branch_filters": true, "message": "requested through bk-desk: " + strings.Join(flags, " ")})
	if err != nil {
		return reply{head: "the build request does not encode", body: err.Error()}
	}
	said, failure := d.runAs(ctx, d.retryToken, "api", "-no-wait", "-X", "POST", "-d", string(body), "/pipelines/"+pipeline+"/builds")
	if failure != nil {
		return *failure
	}
	var created struct {
		Number int    `json:"number"`
		State  string `json:"state"`
		WebURL string `json:"web_url"`
	}
	if err := json.Unmarshal([]byte(said), &created); err != nil {
		return reply{head: "the build's reply does not parse", body: err.Error()}
	}
	return reply{head: fmt.Sprintf("started %s #%d, %s", pipeline, created.Number, created.State), url: created.WebURL}
}

func parseCreate(rest []string) ([]string, string) {
	if len(rest) < 3 || rest[0] != createPipeline || !commitSHA.MatchString(rest[1]) || strings.HasPrefix(rest[2], "-") {
		return nil, createUsage
	}
	flags := rest[3:]
	if cut := slices.IndexFunc(flags, func(word string) bool { return !ciFlag.MatchString(word) }); cut >= 0 {
		if envPair.MatchString(flags[cut]) {
			return nil, createUsage
		}
		flags = flags[:cut]
	}
	return append([]string{"create", rest[0], rest[1], rest[2]}, flags...), ""
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

func parseJobs(words []string) ([]string, string) {
	if len(words) < 2 || strings.HasPrefix(words[0], "-") {
		return nil, jobsUsage
	}
	if _, err := strconv.Atoi(words[1]); err != nil {
		return nil, jobsUsage
	}
	window, rest := "1", words[2:]
	if len(rest) > 0 && parallelShard.MatchString(rest[0]) {
		window, _, _ = strings.Cut(rest[0], "/")
		rest = rest[1:]
	}
	filters := map[string]string{"--state": "", "--step": ""}
	for len(rest) > 1 {
		if _, known := filters[rest[0]]; !known {
			break
		}
		filters[rest[0]], rest = rest[1], rest[2:]
	}
	if _, err := regexp.Compile(filters["--step"]); err != nil {
		return nil, fmt.Sprintf("jobs' --step regex does not compile: %v", err)
	}
	return []string{"jobs", words[0], words[1], window, filters["--state"], filters["--step"]}, ""
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
	case "rerun":
		said, failure := d.runAs(ctx, d.retryToken, args...)
		if failure != nil {
			return *failure
		}
		return reply{head: "ran", body: askdesk.LastBytes(strings.TrimSpace(said), replyLimit)}
	case "retry":
		said, failure := d.runAs(ctx, d.retryToken, "api", "-no-wait", "-X", "PUT", "/pipelines/"+args[1]+"/builds/"+args[2]+"/jobs/"+args[3]+"/retry")
		if failure != nil {
			return *failure
		}
		var retried struct {
			ID     string `json:"id"`
			State  string `json:"state"`
			WebURL string `json:"web_url"`
		}
		if err := json.Unmarshal([]byte(said), &retried); err != nil {
			return reply{head: "the retry's reply does not parse", body: err.Error()}
		}
		return reply{head: fmt.Sprintf("retried as job %s, %s", retried.ID, retried.State), url: retried.WebURL}
	case "create":
		return d.create(ctx, args[1], args[2], args[3], args[4:])
	case "jobs":
		read, failure := d.build(ctx, args[1], args[2])
		if failure != nil {
			return *failure
		}
		window, _ := strconv.Atoi(args[3])
		return everyJob(read, args[1], window, args[4], regexp.MustCompile(args[5]))
	case "failures":
		return d.failures(ctx, args[1], args[2])
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
			found := around(log, regexp.MustCompile(pattern), width)
			if found.body == "" && args[4] != "" {
				if state == "" {
					state = d.jobState(ctx, args[1], args[2], args[3])
				}
				return unfinished(found, state)
			}
			if found.body != "" || args[4] != "" {
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
	return d.runAs(ctx, d.token, args...)
}

func (d *Desk) runAs(ctx context.Context, token string, args ...string) (string, *reply) {
	result, err := d.Runner.Run(ctx, shell.Command{
		Argv: slices.Concat(d.CLI, args), Dir: d.Checkout,
		Env: []string{"BUILDKITE_TOKEN=" + token}, Timeout: readTimeout, Idempotent: true,
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

func everyJob(read build, pipeline string, window int, state string, step *regexp.Regexp) reply {
	windows := []strings.Builder{{}}
	scripts, listed := 0, 0
	for _, each := range read.Jobs {
		if each.Type != "script" {
			continue
		}
		scripts++
		if state != "" && each.State != state || !step.MatchString(each.Name) && !step.MatchString(each.StepKey) {
			continue
		}
		listed++
		line := fmt.Sprintf("%s job=%s %s%s\n", each.State, each.ID, each.Name, each.label())
		if windows[len(windows)-1].Len()+len(line) > replyLimit {
			windows = append(windows, strings.Builder{})
		}
		windows[len(windows)-1].WriteString(line)
	}
	head := fmt.Sprintf("%s, %d jobs", read.State, scripts)
	if listed < scripts {
		head += fmt.Sprintf(", %d matching", listed)
	}
	if window > len(windows) {
		return reply{head: fmt.Sprintf("%s in %d windows, so there is no window %d", head, len(windows), window), url: read.WebURL}
	}
	if len(windows) > 1 {
		head += fmt.Sprintf(", window %d of %d", window, len(windows))
	}
	if window < len(windows) {
		next := fmt.Sprintf("bk jobs %s %d %d/%d", pipeline, read.Number, window+1, len(windows))
		if state != "" {
			next += " --state " + state
		}
		if step.String() != "" {
			next += " --step " + step.String()
		}
		head += "; " + next + " reads the next"
	}
	return reply{head: head, body: windows[window-1].String(), url: read.WebURL}
}

func unfinished(r reply, state string) reply {
	if slices.Contains(unfinishedJob, state) {
		r.head = fmt.Sprintf("the job is %s, so this reads its log so far; ask again once it finishes: %s", state, r.head)
	}
	return r
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

func (d *Desk) failures(ctx context.Context, pipeline, number string) reply {
	read, failure := d.build(ctx, pipeline, number)
	if failure != nil {
		return *failure
	}
	var body strings.Builder
	failed := 0
	for _, step := range read.Jobs {
		if step.Type != "script" || step.State != jobFailed || step.Retried {
			continue
		}
		failed++
		fmt.Fprintf(&body, "failed job=%s %s%s\n", step.ID, step.Name, step.label())
		log, failure := d.run(ctx, "log", pipeline, number, step.ID)
		if failure != nil {
			fmt.Fprintf(&body, "  %s\n", failure.head)
			continue
		}
		for line := range strings.Lines(logEscapes.ReplaceAllString(log, "")) {
			if failureLine.MatchString(line) {
				body.WriteString("  " + strings.TrimLeft(line, " \t"))
			}
		}
	}
	return reply{head: fmt.Sprintf("%s, %d failed jobs", read.State, failed), body: askdesk.FirstBytes(body.String(), replyLimit), url: read.WebURL}
}
