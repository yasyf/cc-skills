package landing

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	trunkTopic      = "dev-red"
	deskRunnerLane  = "desk-runner"
	trunkRedEvery   = 30 * time.Minute
	flakeDue        = 24 * time.Hour
	trunkRedFact    = "trunk-red"
	trunkJobsFact   = "trunk-compared"
	trunkJobsHead   = "compared"
	flakyCursor     = "flaky-notices"
	agentStop       = "agent_stop"
	stateSuccess    = "SUCCESS"
	scriptJob       = "script"
	landingBuilds   = "?per_page=30"
	trunkReadLimit  = 5 * time.Minute
	flakyNoticeRead = "20"
	trunkQuery      = `query($owner: String!, $name: String!, $tip: String!) { repository(owner: $owner, name: $name) { object(expression: $tip) { ... on Commit { history(first: 50) { nodes { oid committedDate message status { contexts { context state targetUrl } } } } } } } }`
)

var (
	squashedPR  = regexp.MustCompile(`\(#(\d+)\)$`)
	buildURL    = regexp.MustCompile(`/([\w.-]+)/builds/(\d+)/?$`)
	trunkRed    = regexp.MustCompile(`^(?:DEV-RED|POST-MERGE-RED) (?:for|since) `)
	failedTest  = regexp.MustCompile(`(?m)--- FAIL: ([\w.-]+)|^test (\S+) \.\.\. FAILED|^\s*FAIL \[\s*[\d.]+s\] \S+ (\S+)|^\s*● .+ › (.+?)\s*$`)
	logControl  = regexp.MustCompile("\x1b(?:_[^\x07]*\x07|\\[[0-9;]*[A-Za-z])|\r")
	flakyNotice = regexp.MustCompile("> failed (?:`([^`]+)`.*? )?on <(https://buildkite\\.com/[^|>]+)\\|[^>]*> at <[^|>]*/commit/([0-9a-f]+)\\|.*?(?:it looks flaky and|touches what it reads, so) nobody was told")
	testName    = regexp.MustCompile(`^(?:Test\w+|\w+(?:::\w+)+)$`)
	dataFile    = []string{".json", ".md", ".txt", ".yaml", ".yml"}
	finished    = []string{stateSuccess, "FAILURE", "ERROR"}
)

type TrunkConfig struct {
	Check     string
	CIBuild   string
	FlakeLane string
	Channel   string
}

type TrunkWatch struct {
	Runner   shell.Runner
	CCX      ccx.Client
	Store    *store.Store
	Drive    string
	Repo     string
	Trunk    string
	Checkout string
	Config   TrunkConfig
	Now      func() time.Time

	builds  map[string]*trunkBuild
	redSaid string
	calm    bool
}

type flakyMessage struct {
	TS        string `json:"ts"`
	Text      string `json:"text"`
	Permalink string `json:"permalink"`
}

type trunkContext struct {
	Context   string `json:"context"`
	State     string `json:"state"`
	TargetURL string `json:"targetUrl"`
}

type trunkCommit struct {
	OID           string    `json:"oid"`
	CommittedDate time.Time `json:"committedDate"`
	Message       string    `json:"message"`
	Status        *struct {
		Contexts []trunkContext `json:"contexts"`
	} `json:"status"`
}

type trunkJob struct {
	ID           string `json:"id"`
	Type         string `json:"type"`
	Name         string `json:"name"`
	StepKey      string `json:"step_key"`
	State        string `json:"state"`
	SoftFailed   bool   `json:"soft_failed"`
	SignalReason string `json:"signal_reason"`
	RetrySource  *struct {
		JobID string `json:"job_id"`
	} `json:"retry_source"`
}

type trunkBuild struct {
	Branch string     `json:"branch"`
	State  string     `json:"state"`
	Jobs   []trunkJob `json:"jobs"`
}

func (c trunkCommit) check(name string) (trunkContext, bool) {
	if c.Status == nil {
		return trunkContext{}, false
	}
	for _, status := range c.Status.Contexts {
		if status.Context == name && slices.Contains(finished, status.State) {
			return status, true
		}
	}
	return trunkContext{}, false
}

func (j trunkJob) step() string {
	if j.StepKey != "" {
		return j.StepKey
	}
	return j.Name
}

func (j trunkJob) failed() bool {
	return j.Type == scriptJob && j.State == "failed"
}

func (j trunkJob) retriedGreen() bool {
	return j.Type == scriptJob && j.State == "passed" && j.RetrySource != nil
}

func (w *TrunkWatch) Pass(ctx context.Context) error {
	w.builds = map[string]*trunkBuild{}
	history, err := w.history(ctx)
	if err != nil {
		return err
	}
	return errors.Join(w.compareBuilds(ctx, history), w.watchFlakes(ctx, history), w.watchNotices(ctx), w.reportRed(ctx, history))
}

func (w *TrunkWatch) history(ctx context.Context) ([]trunkCommit, error) {
	owner, name, found := strings.Cut(w.Repo, "/")
	if !found {
		return nil, fmt.Errorf("repo %q is not owner/name", w.Repo)
	}
	out, err := w.CCX.GH(ctx, ccx.MaxAgeFresh, "api", "graphql", "-f", "owner="+owner, "-f", "name="+name, "-f", "tip="+w.Trunk, "-f", "query="+trunkQuery)
	if err != nil {
		return nil, err
	}
	var answer struct {
		Data struct {
			Repository struct {
				Object struct {
					History struct {
						Nodes []trunkCommit `json:"nodes"`
					} `json:"history"`
				} `json:"object"`
			} `json:"repository"`
		} `json:"data"`
	}
	if err := json.Unmarshal([]byte(out), &answer); err != nil {
		return nil, fmt.Errorf("trunk history: %w", err)
	}
	return answer.Data.Repository.Object.History.Nodes, nil
}

func (w *TrunkWatch) bk(ctx context.Context, url, suffix string) (string, error) {
	found := buildURL.FindStringSubmatch(url)
	if found == nil {
		return "", fmt.Errorf("%q is no Buildkite build URL", url)
	}
	words := strings.Fields(strings.NewReplacer("{pipeline}", found[1], "{number}", found[2]).Replace(w.Config.CIBuild))
	words[0] = filepath.Join(w.Checkout, words[0])
	words[len(words)-1] += suffix
	result, err := w.Runner.Run(ctx, shell.Command{Argv: words, Dir: w.Checkout, Timeout: trunkReadLimit})
	if err != nil {
		return "", err
	}
	return result.Stdout, nil
}

func (w *TrunkWatch) build(ctx context.Context, url string) (*trunkBuild, error) {
	if kept, known := w.builds[url]; known {
		return kept, nil
	}
	out, err := w.bk(ctx, url, "")
	if err != nil {
		return nil, err
	}
	var build trunkBuild
	if err := json.Unmarshal([]byte(out), &build); err != nil {
		return nil, fmt.Errorf("build %s: %w", url, err)
	}
	w.builds[url] = &build
	return &build, nil
}

func (w *TrunkWatch) compareBuilds(ctx context.Context, history []trunkCommit) error {
	type finishedBuild struct {
		index int
		url   string
	}
	var builds []finishedBuild
	for index, commit := range history {
		if status, found := commit.check(w.Config.Check); found && status.TargetURL != "" {
			builds = append(builds, finishedBuild{index, status.TargetURL})
		}
	}
	if len(builds) < 2 {
		return nil
	}
	stored, err := w.Store.Facts(w.Repo, 0, trunkJobsHead)
	if err != nil {
		return err
	}
	until := 1
	if at := slices.IndexFunc(builds, func(b finishedBuild) bool { return b.url == stored[trunkJobsFact] }); at >= 0 {
		until = at
	}
	for position := min(until, len(builds)-1) - 1; position >= 0; position-- {
		newer, older := builds[position], builds[position+1]
		if err := w.compare(ctx, history, newer.index, older.index, newer.url, older.url); err != nil {
			return err
		}
		if _, err := w.Store.PutFact(w.Repo, 0, trunkJobsHead, trunkJobsFact, newer.url, w.Now()); err != nil {
			return err
		}
	}
	return nil
}

func (w *TrunkWatch) compare(ctx context.Context, history []trunkCommit, since, until int, newest, previous string) error {
	build, err := w.build(ctx, newest)
	if err != nil {
		return err
	}
	before, err := w.build(ctx, previous)
	if err != nil {
		return err
	}
	if slices.ContainsFunc(build.Jobs, trunkJob.retriedGreen) {
		if err := w.flake(ctx, history[since].OID, newest, newest); err != nil {
			return err
		}
	}
	fresh := newFailures(*build, *before)
	if len(fresh) == 0 {
		return nil
	}
	var landed []string
	for _, commit := range history[since:until] {
		if found := squashedPR.FindStringSubmatch(firstLine(commit.Message)); found != nil {
			landed = append(landed, "#"+found[1])
		}
	}
	between := "no PR a squash subject names"
	if len(landed) > 0 {
		between = strings.Join(landed, ", ")
	}
	number := buildURL.FindStringSubmatch(newest)[2]
	text := fmt.Sprintf("POST-MERGE-RED trunk-jobs:%s %s build %s: %s has jobs failing that %s did not: %s. Landed between the two: %s. A soft-failed job leaves the build green, so nothing else reads it; a fix is owed.", number, w.Trunk, number, newest, previous, strings.Join(fresh, ", "), between)
	_, err = client.Post(ctx, client.Draft{Drive: w.Drive, Lane: self, Kind: "report", To: []string{rootLane}, ID: "trunk-jobs:" + number, Text: text})
	return err
}

func newFailures(newest, previous trunkBuild) []string {
	count := func(build trunkBuild) map[string]int {
		counted := map[string]int{}
		for _, job := range build.Jobs {
			if job.failed() {
				word := "failed"
				if job.SoftFailed {
					word = "soft-failed"
				}
				name := job.Name
				if name == "" {
					name = job.StepKey
				}
				counted[name+" "+word]++
			}
		}
		return counted
	}
	before := count(previous)
	var fresh []string
	for name, times := range count(newest) {
		if extra := times - before[name]; extra > 1 {
			fresh = append(fresh, fmt.Sprintf("%s in %d jobs", name, extra))
		} else if extra == 1 {
			fresh = append(fresh, name)
		}
	}
	slices.Sort(fresh)
	return fresh
}

func (w *TrunkWatch) watchFlakes(ctx context.Context, history []trunkCommit) error {
	reds, err := w.Store.TrunkFacts(w.Repo, trunkRedFact)
	if err != nil {
		return err
	}
	inHistory := map[string]bool{}
	var failed []error
	for _, commit := range history {
		inHistory[commit.OID] = true
		status, found := commit.check(w.Config.Check)
		if !found {
			continue
		}
		red, known := reds[commit.OID]
		switch {
		case status.State != stateSuccess && !known:
			_, err := w.Store.PutFact(w.Repo, 0, commit.OID, trunkRedFact, status.TargetURL, w.Now())
			failed = append(failed, err)
		case status.State == stateSuccess && known:
			if err := w.flake(ctx, commit.OID, red, status.TargetURL); err != nil {
				failed = append(failed, err)
				continue
			}
			failed = append(failed, w.Store.DeleteFact(w.Repo, 0, commit.OID, trunkRedFact))
		}
	}
	for oid := range reds {
		if !inHistory[oid] {
			failed = append(failed, w.Store.DeleteFact(w.Repo, 0, oid, trunkRedFact))
		}
	}
	return errors.Join(failed...)
}

func (w *TrunkWatch) flake(ctx context.Context, oid, red, green string) error {
	var jobs []string
	if red == green {
		build, err := w.build(ctx, green)
		if err != nil {
			return err
		}
		for _, job := range build.Jobs {
			if !job.retriedGreen() {
				continue
			}
			stopped, err := w.stopped(ctx, green, job.RetrySource.JobID)
			if err != nil {
				return err
			}
			if !stopped {
				jobs = append(jobs, job.RetrySource.JobID)
			}
		}
	} else {
		build, err := w.build(ctx, red)
		if err != nil {
			return err
		}
		for _, job := range build.Jobs {
			if job.failed() && !job.SoftFailed && job.SignalReason != agentStop {
				jobs = append(jobs, job.ID)
			}
		}
	}
	if len(jobs) == 0 {
		return nil
	}
	test := ""
	for _, job := range jobs {
		if test = w.failedTest(ctx, red, job); test != "" {
			break
		}
	}
	return w.postFlake(ctx, test, fmt.Sprintf("failed on %s at %s and passed on retry", red, oid[:min(10, len(oid))]), "flake:"+oid)
}

func (w *TrunkWatch) stopped(ctx context.Context, build, job string) (bool, error) {
	out, err := w.bk(ctx, build, "/jobs/"+job)
	if err != nil {
		return false, err
	}
	var read trunkJob
	if err := json.Unmarshal([]byte(out), &read); err != nil {
		return false, fmt.Errorf("job %s: %w", job, err)
	}
	return read.SignalReason == agentStop, nil
}

func (w *TrunkWatch) failedTest(ctx context.Context, build, job string) string {
	out, err := w.bk(ctx, build, "/jobs/"+job+"/log")
	if err != nil {
		return ""
	}
	var log struct {
		Content string `json:"content"`
	}
	if json.Unmarshal([]byte(out), &log) != nil {
		return ""
	}
	found := failedTest.FindStringSubmatch(logControl.ReplaceAllString(log.Content, ""))
	for _, group := range found[min(1, len(found)):] {
		if group != "" {
			return group
		}
	}
	return ""
}

func (w *TrunkWatch) watchNotices(ctx context.Context) error {
	if w.Config.Channel == "" {
		return nil
	}
	result, err := w.Runner.Run(ctx, shell.Command{Argv: []string{"cc-slack", "history", "--channel", w.Config.Channel, "--json", "--limit", flakyNoticeRead}, Timeout: trunkReadLimit})
	if err != nil {
		return err
	}
	var read struct {
		Messages []flakyMessage `json:"messages"`
	}
	if err := json.Unmarshal([]byte(result.Stdout), &read); err != nil {
		return fmt.Errorf("cc-slack history: %w", err)
	}
	after, err := w.Store.Cursor(flakyCursor)
	if err != nil {
		return err
	}
	if after == 0 {
		after = w.Now().Add(-flakeDue).UnixMicro()
	}
	slices.SortFunc(read.Messages, func(a, b flakyMessage) int { return strings.Compare(a.TS, b.TS) })
	for _, message := range read.Messages {
		seconds, err := strconv.ParseFloat(message.TS, 64)
		if err != nil {
			return fmt.Errorf("slack ts %q: %w", message.TS, err)
		}
		at := int64(seconds * 1e6)
		if at <= after {
			continue
		}
		if found := flakyNotice.FindStringSubmatch(message.Text); found != nil {
			test := ""
			if words := strings.Fields(found[1]); len(words) > 0 && testName.MatchString(words[len(words)-1]) {
				test = words[len(words)-1]
			}
			failed := fmt.Sprintf("failed on %s at %s while nothing it reads changed, per %s", found[2], found[3][:min(10, len(found[3]))], message.Permalink)
			if err := w.postFlake(ctx, test, failed, "flake-notice:"+message.TS); err != nil {
				return err
			}
		}
		if err := w.Store.Advance(flakyCursor, at); err != nil {
			return err
		}
	}
	return nil
}

func (w *TrunkWatch) postFlake(ctx context.Context, test, failed, key string) error {
	path, pr := w.lastChange(ctx, test)
	owner, err := w.flakeOwner(ctx, pr)
	if err != nil {
		return err
	}
	changed := ""
	if pr != "" {
		changed = "; #" + pr + " last changed it"
	}
	shown, named := "-", "a test"
	if path != "" {
		shown = path
	}
	if test != "" {
		named = test
	}
	due := w.Now().Add(flakeDue).In(pacificZone()).Format("Jan 2 3:04 PM")
	text := fmt.Sprintf("[finding:%s] - %s flake owner %s: FLAKE %s %s%s. Fix due %s; name this record in the fix's HEAD. It blocks nothing.", self, shown, owner, named, failed, changed, due)
	to := []string{owner}
	if owner != rootLane {
		to = append(to, rootLane)
	}
	_, err = client.Post(ctx, client.Draft{Drive: w.Drive, Lane: self, Kind: "defect", To: to, Topic: trunkTopic, ID: key, Text: text})
	return err
}

func (w *TrunkWatch) lastChange(ctx context.Context, test string) (path, pr string) {
	if test == "" {
		return "", ""
	}
	name := test
	if at := strings.LastIndex(test, "::"); at >= 0 {
		name = test[at+2:]
	}
	listed, err := w.Runner.Run(ctx, shell.Command{Argv: []string{"git", "-C", w.Checkout, "grep", "-l", "-w", "-F", "-e", name}, Timeout: trunkReadLimit})
	if err != nil {
		return "", ""
	}
	for _, file := range strings.Fields(listed.Stdout) {
		if !slices.Contains(dataFile, filepath.Ext(file)) {
			path = file
			break
		}
	}
	if path == "" {
		return "", ""
	}
	subject, err := w.Runner.Run(ctx, shell.Command{Argv: []string{"git", "-C", w.Checkout, "log", "-1", "--format=%s", "--", path}, Timeout: trunkReadLimit})
	if err != nil {
		return path, ""
	}
	if found := squashedPR.FindStringSubmatch(strings.TrimSpace(subject.Stdout)); found != nil {
		return path, found[1]
	}
	return path, ""
}

func (w *TrunkWatch) flakeOwner(ctx context.Context, pr string) (string, error) {
	var candidates []string
	if pr != "" {
		rows, err := w.Store.PRs(w.Repo)
		if err != nil {
			return "", err
		}
		for _, row := range rows {
			if strconv.Itoa(row.Number) == pr && row.Lane != "" {
				candidates = append(candidates, row.Lane)
			}
		}
	}
	if w.Config.FlakeLane != "" {
		candidates = append(candidates, w.Config.FlakeLane)
	}
	if len(candidates) == 0 {
		return rootLane, nil
	}
	peers, err := client.Who(ctx, w.Drive)
	if err != nil {
		return "", err
	}
	for _, candidate := range candidates {
		if slices.ContainsFunc(peers, func(peer client.Peer) bool { return peer.Lane == candidate && peer.Live }) {
			return candidate, nil
		}
	}
	return rootLane, nil
}

func (w *TrunkWatch) reportRed(ctx context.Context, history []trunkCommit) error {
	var run []trunkCommit
	var newest trunkContext
	seen := false
	for _, commit := range history {
		status, found := commit.check(w.Config.Check)
		if !found {
			continue
		}
		if !seen {
			newest, seen = status, true
		}
		if status.State == stateSuccess {
			break
		}
		run = append(run, commit)
	}
	if !seen {
		return nil
	}
	if len(run) == 0 {
		return w.reportGreen(ctx)
	}
	w.calm = false
	first := run[len(run)-1]
	bucket := int(w.Now().Sub(first.CommittedDate) / trunkRedEvery)
	id := fmt.Sprintf("%s:%s:%d", trunkTopic, first.OID, bucket)
	if id == w.redSaid {
		return nil
	}
	minutes := int(w.Now().Sub(first.CommittedDate).Minutes())
	jobs, blocking := w.blocking(ctx, newest.TargetURL)
	failing := "is red on " + newest.TargetURL
	if len(jobs) > 0 {
		failing = "fails " + strings.Join(jobs, ", ") + " on " + newest.TargetURL
	}
	origin := "first red at " + first.OID[:min(10, len(first.OID))]
	if found := squashedPR.FindStringSubmatch(firstLine(first.Message)); found != nil {
		origin += ", landed by #" + found[1]
	}
	fix, err := w.namedFix(ctx, first.CommittedDate)
	if err != nil {
		return err
	}
	if blocking {
		owed := "nothing lands until a fix lands; name it with `cci post --topic dev-red --pr <n>`"
		if fix != "" {
			owed = "nothing lands until #" + fix + " fixes it"
		}
		text := fmt.Sprintf("DEV-RED for %d min: %s's %s %s, %s. No PR can land until this is fixed; %s.", minutes, w.Trunk, w.Config.Check, failing, origin, owed)
		return w.postRed(ctx, "incident", id, text)
	}
	owed := "name the fix with `cci post --topic dev-red --pr <n>`"
	if fix != "" {
		owed = "#" + fix + " fixes it"
	}
	text := fmt.Sprintf("POST-MERGE-RED for %d min: %s's %s %s, %s; no PR build runs these jobs. Landing continues; a fix is owed: %s.", minutes, w.Trunk, w.Config.Check, failing, origin, owed)
	return w.postRed(ctx, "report", "post-merge-red:"+id, text)
}

func (w *TrunkWatch) postRed(ctx context.Context, kind, id, text string) error {
	if _, err := client.Post(ctx, client.Draft{Drive: w.Drive, Lane: self, Kind: kind, To: []string{rootLane}, Topic: trunkTopic, ID: id, Text: text}); err != nil {
		return err
	}
	w.redSaid = strings.TrimPrefix(id, "post-merge-red:")
	return nil
}

func (w *TrunkWatch) blocking(ctx context.Context, url string) ([]string, bool) {
	build, err := w.build(ctx, url)
	if err != nil {
		return nil, true
	}
	var jobs []string
	for _, job := range build.Jobs {
		if job.failed() && !job.SoftFailed {
			jobs = append(jobs, job.step())
		}
	}
	slices.Sort(jobs)
	jobs = slices.Compact(jobs)
	if len(jobs) == 0 {
		return nil, true
	}
	found := buildURL.FindStringSubmatch(url)
	out, err := w.Runner.Run(ctx, shell.Command{Argv: w.landingBuildsArgv(found[1]), Dir: w.Checkout, Timeout: trunkReadLimit})
	if err != nil {
		return jobs, true
	}
	var builds []trunkBuild
	if err := json.Unmarshal([]byte(out.Stdout), &builds); err != nil {
		return jobs, true
	}
	steps := map[string]bool{}
	for _, read := range builds {
		if read.Branch == w.Trunk || (read.State != "passed" && read.State != "failed") {
			continue
		}
		for _, job := range read.Jobs {
			if job.Type == scriptJob {
				steps[job.step()] = true
			}
		}
	}
	if len(steps) == 0 {
		return jobs, true
	}
	return jobs, slices.ContainsFunc(jobs, func(job string) bool { return steps[job] })
}

func (w *TrunkWatch) landingBuildsArgv(pipeline string) []string {
	words := strings.Fields(strings.NewReplacer("{pipeline}", pipeline, "/{number}", "").Replace(w.Config.CIBuild))
	words[0] = filepath.Join(w.Checkout, words[0])
	words[len(words)-1] += landingBuilds
	return words
}

func (w *TrunkWatch) namedFix(ctx context.Context, since time.Time) (string, error) {
	records, err := client.Tail(ctx, client.Query{Drive: w.Drive, Since: since})
	if err != nil {
		return "", err
	}
	fix := ""
	for _, record := range records {
		if record.Topic == trunkTopic && record.Lane != self && record.Lane != deskRunnerLane && string(record.Kind) != "incident" && len(record.Refs.PRs) > 0 {
			fix = strconv.Itoa(record.Refs.PRs[0].Number)
		}
	}
	return fix, nil
}

func (w *TrunkWatch) reportGreen(ctx context.Context) error {
	if w.calm {
		return nil
	}
	records, err := client.Tail(ctx, client.Query{Drive: w.Drive, Since: w.Now().Add(-7 * 24 * time.Hour), Lanes: []string{self, deskRunnerLane}, Kinds: []string{"incident", "report"}})
	if err != nil {
		return err
	}
	var red *client.Record
	for i, record := range records {
		if record.Topic != trunkTopic {
			continue
		}
		switch {
		case strings.HasPrefix(record.Text, "DEV-GREEN"):
			red = nil
		case red == nil && trunkRed.MatchString(record.Text):
			red = &records[i]
		}
	}
	if red == nil {
		w.calm = true
		return nil
	}
	text := fmt.Sprintf("DEV-GREEN %s's %s passes again; it was red from #%d.", w.Trunk, w.Config.Check, red.Seq)
	_, err = client.Post(ctx, client.Draft{Drive: w.Drive, Lane: self, Kind: "report", To: []string{rootLane}, Topic: trunkTopic, ID: "dev-green:" + w.Now().UTC().Format("200601021504"), Text: text})
	if err == nil {
		w.calm = true
	}
	return err
}

func firstLine(message string) string {
	line, _, _ := strings.Cut(message, "\n")
	return line
}

func pacificZone() *time.Location {
	zone, err := time.LoadLocation("America/Los_Angeles")
	if err != nil {
		panic(err)
	}
	return zone
}
