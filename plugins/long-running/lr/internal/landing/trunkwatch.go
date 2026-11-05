package landing

import (
	"cmp"
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
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/asks"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bkread"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	trunkTopic      = "dev-red"
	deskRunnerLane  = "desk-runner"
	trunkRedEvery   = 30 * time.Minute
	flakeDue        = 2 * time.Hour
	flakeAttr       = "flake"
	flakeCloser     = "flake-closers"
	flakeAsked      = "flake-asked"
	flakeLookback   = 24 * time.Hour
	orcaDesk        = "orca-desk"
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
	failedJobReads  = 3
	trunkQuery      = `query($owner: String!, $name: String!, $tip: String!) { repository(owner: $owner, name: $name) { object(expression: $tip) { ... on Commit { history(first: 50) { nodes { oid committedDate message status { contexts { context state targetUrl } } } } } } } }`
)

//go:embed flake_brief.md
var flakeBrief string

var (
	squashedPR  = regexp.MustCompile(`\(#(\d+)\)$`)
	buildURL    = regexp.MustCompile(`/([\w.-]+)/builds/(\d+)/?$`)
	trunkRed    = regexp.MustCompile(`^(?:DEV-RED|POST-MERGE-RED) (?:for|since) `)
	failedTest  = regexp.MustCompile(`(?m)--- FAIL: ([\w.-]+)|^test (\S+) \.\.\. FAILED|^\s*FAIL \[\s*[\d.]+s\] \S+ (\S+)|^\s*● .+ › (.+?)\s*$`)
	logControl  = regexp.MustCompile("\x1b(?:_[^\x07]*\x07|\\[[0-9;]*[A-Za-z])|\r")
	flakyNotice = regexp.MustCompile("> failed (?:`([^`]+)`.*? )?on <(https://buildkite\\.com/[^|>]+)\\|[^>]*> at <[^|>]*/commit/([0-9a-f]+)\\|.*?(?:it looks flaky and|touches what it reads, so) nobody was told")
	testName    = regexp.MustCompile(`^(?:Test\w+|\w+(?:::\w+)+)$`)
	infraLine   = regexp.MustCompile(`(?im)^.*(?:API rate limit exceeded|secondary rate limit|quota exceeded|\b403\b.*\b(?:rate limit|quota|Forbidden)\b|\b429 Too Many Requests|no space left on device).*$`)
	dataFile    = []string{".json", ".md", ".txt", ".yaml", ".yml"}
	finished    = []string{stateSuccess, "FAILURE", "ERROR"}
)

type TrunkConfig struct {
	Check        string
	CIBuild      string
	TokenCommand []string
	FlakeLane    string
	Channel      string
}

type TrunkWatch struct {
	Runner   shell.Runner
	CCX      ccx.Client
	Store    *store.Store
	Drive    string
	Repo     string
	Trunk    string
	Checkout string
	State    string
	Asks     *asks.Mirror
	Config   TrunkConfig
	Now      func() time.Time

	builds  map[string]*trunkBuild
	token   bkread.Token
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
	w.builds, w.token = map[string]*trunkBuild{}, bkread.Token{Command: w.Config.TokenCommand}
	history, err := w.history(ctx)
	if err != nil {
		return err
	}
	return errors.Join(w.compareBuilds(ctx, history), w.watchFlakes(ctx, history), w.watchNotices(ctx), w.reopenUnfixed(ctx), w.superviseOpen(ctx), w.reportRed(ctx, history))
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
	return w.readBuildkite(ctx, words)
}

func (w *TrunkWatch) readBuildkite(ctx context.Context, argv []string) (string, error) {
	result, err := w.token.Run(ctx, w.Runner, shell.Command{Argv: argv, Dir: w.Checkout, Timeout: trunkReadLimit})
	if errors.Is(err, bkread.ErrRefused) {
		return "", fmt.Errorf("not a red build: DEV-RED still posts from GitHub's statuses; job comparisons and flake reports wait for a pass that can read the build: %w", err)
	}
	return result.Stdout, err
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
	var jobs []trunkJob
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
				job.ID = job.RetrySource.JobID
				jobs = append(jobs, job)
			}
		}
	} else {
		build, err := w.build(ctx, red)
		if err != nil {
			return err
		}
		for _, job := range build.Jobs {
			if job.failed() && !job.SoftFailed && job.SignalReason != agentStop {
				jobs = append(jobs, job)
			}
		}
	}
	if len(jobs) == 0 {
		return nil
	}
	found := flakeFound{job: jobs[0].step()}
	for _, job := range jobs {
		test, infra := w.failure(ctx, red, job.ID)
		if test != "" {
			found = flakeFound{test: test, job: job.step()}
			break
		}
		if infra != "" && found.infra == "" {
			found = flakeFound{job: job.step(), infra: infra}
		}
	}
	found.failed = fmt.Sprintf("failed on %s at %s and passed on retry", red, oid[:min(10, len(oid))])
	return w.postFlake(ctx, found, "flake:"+oid)
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

func (w *TrunkWatch) failure(ctx context.Context, build, job string) (test, infra string) {
	out, err := w.bk(ctx, build, "/jobs/"+job+"/log")
	if err != nil {
		return "", ""
	}
	var log struct {
		Content string `json:"content"`
	}
	if json.Unmarshal([]byte(out), &log) != nil {
		return "", ""
	}
	content := logControl.ReplaceAllString(log.Content, "")
	found := failedTest.FindStringSubmatch(content)
	for _, group := range found[min(1, len(found)):] {
		if group != "" {
			return group, ""
		}
	}
	line := strings.TrimSpace(infraLine.FindString(content))
	return "", line[:min(200, len(line))]
}

func (w TrunkWatch) FailedJob(ctx context.Context, url string) (job, test, infra string) {
	w.builds, w.token = map[string]*trunkBuild{}, bkread.Token{Command: w.Config.TokenCommand}
	build, err := w.build(ctx, url)
	if err != nil {
		return "", "", ""
	}
	read := 0
	for _, failed := range build.Jobs {
		if !failed.failed() || failed.SoftFailed || failed.SignalReason == agentStop {
			continue
		}
		if job == "" {
			job = failed.step()
		}
		if read++; read > failedJobReads {
			break
		}
		named, line := w.failure(ctx, url, failed.ID)
		if named != "" {
			return failed.step(), named, ""
		}
		if line != "" && infra == "" {
			job, infra = failed.step(), line
		}
	}
	return job, "", infra
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
			if err := w.postFlake(ctx, flakeFound{test: test, job: found[1], failed: failed}, "flake-notice:"+message.TS); err != nil {
				return err
			}
		}
		if err := w.Store.Advance(flakyCursor, at); err != nil {
			return err
		}
	}
	return nil
}

type flakeFound struct {
	test, job, infra, failed string
}

func (f flakeFound) subject() string {
	if f.test != "" {
		return f.test
	}
	return "job " + f.job
}

func (f flakeFound) named() string {
	switch {
	case f.test != "":
		return "FLAKE " + f.test
	case f.infra != "":
		return fmt.Sprintf("INFRA job %s failed on infrastructure, not a test (%s)", f.job, f.infra)
	}
	return fmt.Sprintf("FLAKE job %s, no failing test named in its log", f.job)
}

func (w *TrunkWatch) postFlake(ctx context.Context, found flakeFound, key string) error {
	open, err := w.openFlakes(ctx)
	if err != nil {
		return err
	}
	if at := slices.IndexFunc(open, func(r client.Record) bool { return r.Fields.Attrs[flakeAttr] == found.subject() }); at >= 0 {
		_, err := client.Post(ctx, client.Draft{Drive: w.Drive, Lane: self, Kind: "note", To: open[at].To, Topic: trunkTopic, ID: key, Re: open[at].Seq, Force: true, Text: fmt.Sprintf("%s again: %s. It stays open on %s until a merged PR fixes it.", found.named(), found.failed, open[at].Name())})
		return err
	}
	path, pr := w.lastChange(ctx, found.test)
	owner, err := w.flakeOwner(ctx, pr)
	if err != nil {
		return err
	}
	changed := ""
	if pr != "" {
		changed = "; #" + pr + " last changed it"
	}
	shown := "-"
	if path != "" {
		shown = path
	}
	due := w.Now().Add(flakeDue).In(pacificZone()).Format("Jan 2 3:04 PM")
	text := fmt.Sprintf("[finding:%s] - %s flake owner %s: %s %s%s. Find the cause and fix it by %s; a retry, skip, quarantine or longer timeout is no fix. It stays open until a merged PR fixes it: resolve it with that PR. It blocks nothing.", self, shown, owner, found.named(), found.failed, changed, due)
	stored, err := client.Post(ctx, client.Draft{Drive: w.Drive, Lane: self, Kind: "defect", To: []string{owner}, Topic: trunkTopic, ID: key, Force: true, Attrs: map[string]string{flakeAttr: found.subject()}, Text: text})
	if err != nil || owner == rootLane {
		return err
	}
	_, err = client.Post(ctx, client.Draft{Drive: w.Drive, Lane: self, Kind: "note", To: []string{rootLane}, Topic: trunkTopic, ID: key + ":root", Re: stored.Seq, Text: fmt.Sprintf("%s: %s owns the fix in %s; nothing for root to do.", found.named(), owner, stored.Name())})
	return err
}

func (w *TrunkWatch) openFlakes(ctx context.Context) ([]client.Record, error) {
	return openFlakes(ctx, w.Drive)
}

func openFlakes(ctx context.Context, drive string) ([]client.Record, error) {
	open, err := client.Open(ctx, drive, "defect")
	return slices.DeleteFunc(open, func(r client.Record) bool { return r.Lane != self || r.Fields.Attrs[flakeAttr] == "" }), err
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
		if at := slices.IndexFunc(peers, func(peer client.Peer) bool { return peer.Live && client.Stem(peer.Lane) == client.Stem(candidate) }); at >= 0 {
			return peers[at].Lane, nil
		}
	}
	if w.Config.FlakeLane == "" {
		return rootLane, nil
	}
	return w.launchFlakeLane(ctx)
}

func (w *TrunkWatch) launchFlakeLane(ctx context.Context) (string, error) {
	stem := client.Stem(w.Config.FlakeLane)
	taken, err := client.Generations(ctx, w.Drive, stem)
	if err != nil {
		return "", err
	}
	highest := 0
	for _, name := range taken {
		if n, err := strconv.Atoi(strings.TrimPrefix(name, stem+"-")); err == nil {
			highest = max(highest, n)
		}
	}
	lane := fmt.Sprintf("%s-%d", stem, highest+1)
	brief := filepath.Join(w.State, stem, "brief.md")
	if err := os.MkdirAll(filepath.Dir(brief), 0o750); err != nil {
		return "", err
	}
	if err := os.WriteFile(brief, []byte(strings.NewReplacer("<stem>", stem, "<drive>", w.Drive, "<self>", self).Replace(flakeBrief)), 0o600); err != nil {
		return "", err
	}
	_, err = client.Post(ctx, client.Draft{Drive: w.Drive, Lane: self, Kind: "go", To: []string{orcaDesk}, Topic: stem, ID: "flake-launch:" + lane, Text: fmt.Sprintf("%s: launch %s opus high role=fix brief=%s", orcaDesk, lane, brief)})
	return lane, err
}

func (w *TrunkWatch) reopenUnfixed(ctx context.Context) error {
	after, err := w.Store.Cursor(flakeCloser)
	if err != nil {
		return err
	}
	query := client.Query{Drive: w.Drive, After: after}
	if after == 0 {
		query = client.Query{Drive: w.Drive, Since: w.Now().Add(-flakeLookback)}
	}
	records, err := client.Tail(ctx, query)
	if err != nil || len(records) == 0 {
		return err
	}
	closers := map[int64][]client.Record{}
	var seqs []int64
	for _, record := range records {
		for _, seq := range append([]int64{record.Resolves}, record.AlsoResolves...) {
			if seq != 0 {
				closers[seq] = append(closers[seq], record)
				seqs = append(seqs, seq)
			}
		}
	}
	closed, err := client.Among(ctx, w.Drive, seqs)
	if err != nil {
		return err
	}
	for _, flake := range closed {
		if flake.Lane != self || flake.Fields.Attrs[flakeAttr] == "" {
			continue
		}
		for _, closer := range closers[flake.Seq] {
			merged, err := w.mergedPR(ctx, closer)
			if err != nil {
				return err
			}
			if merged != 0 {
				continue
			}
			text := fmt.Sprintf("REOPENED %s: %s closed it with no merged PR, and a flake closes only when a merged PR fixes it. %s", flake.Name(), closer.Name(), flake.Text)
			if _, err := client.Post(ctx, client.Draft{Drive: w.Drive, Lane: self, Kind: "defect", To: flake.To, Topic: trunkTopic, ID: fmt.Sprintf("flake-reopen:%d", closer.Seq), Re: flake.Seq, Force: true, Attrs: map[string]string{flakeAttr: flake.Fields.Attrs[flakeAttr]}, Text: text}); err != nil {
				return err
			}
		}
	}
	return w.Store.Advance(flakeCloser, records[len(records)-1].Seq)
}

func (w *TrunkWatch) mergedPR(ctx context.Context, closer client.Record) (int, error) {
	for _, pr := range closer.Refs.PRs {
		out, err := w.CCX.GH(ctx, ccx.MaxAgeCached, "pr", "view", strconv.Itoa(pr.Number), "--repo", cmp.Or(pr.Repo, w.Repo), "--json", "mergedAt", "--jq", ".mergedAt")
		if err != nil {
			return 0, err
		}
		if strings.TrimSpace(out) != "" {
			return pr.Number, nil
		}
	}
	return 0, nil
}

func (w *TrunkWatch) superviseOpen(ctx context.Context) error {
	open, err := w.openFlakes(ctx)
	if err != nil || len(open) == 0 {
		return err
	}
	peers, err := client.Who(ctx, w.Drive)
	if err != nil {
		return err
	}
	asked, err := w.Store.Facts(w.Repo, 0, flakeAsked)
	if err != nil {
		return err
	}
	for _, flake := range open {
		held := slices.ContainsFunc(peers, func(peer client.Peer) bool {
			return peer.Live && (client.Stem(peer.Lane) == client.Stem(flake.To[0]) || client.Stem(peer.Lane) == client.Stem(w.Config.FlakeLane))
		})
		due := flake.At.Add(flakeDue)
		if held && w.Now().Before(due) {
			continue
		}
		if !held && w.Config.FlakeLane != "" {
			lane, err := w.launchFlakeLane(ctx)
			if err != nil {
				return err
			}
			if lane != flake.To[0] {
				if _, err := client.Post(ctx, client.Draft{Drive: w.Drive, Lane: self, Kind: "note", To: []string{lane}, Topic: trunkTopic, ID: fmt.Sprintf("flake-handover:%d:%s", flake.Seq, lane), Re: flake.Seq, Force: true, Text: fmt.Sprintf("%s owns %s now, since no lane on it is live: %s", lane, flake.Name(), flake.Text)}); err != nil {
					return err
				}
			}
		}
		if w.Asks == nil || asked[flake.Name()] != "" {
			continue
		}
		line, holder, err := w.Asks.Add(ctx, asks.NewAsk{Text: fmt.Sprintf("Fix the flake %s names: %s", flake.Name(), flake.Fields.Attrs[flakeAttr]), Lane: flake.To[0], Accept: "a merged PR fixes its cause and resolves " + flake.Name(), Due: due.UTC().Format(time.RFC3339)}, func(context.Context, string) (string, error) { return "", nil })
		if err != nil {
			return err
		}
		if _, err := w.Store.PutFact(w.Repo, 0, flakeAsked, flake.Name(), cmp.Or(holder, line), w.Now()); err != nil {
			return err
		}
	}
	return nil
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
	if fix == "" {
		line := fmt.Sprintf("orca-desk: incident %s-%s %s :: %s's %s %s, %s; fix it", trunkTopic, first.OID[:min(10, len(first.OID))], newest.TargetURL, w.Trunk, w.Config.Check, failing, origin)
		if _, err := client.Post(ctx, client.Draft{Drive: w.Drive, Lane: self, Kind: "go", To: []string{orcaDesk}, Topic: trunkTopic, ID: "fix-lane:" + id, Text: line}); err != nil {
			return err
		}
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
	out, err := w.readBuildkite(ctx, w.landingBuildsArgv(found[1]))
	if err != nil {
		return jobs, true
	}
	var builds []trunkBuild
	if err := json.Unmarshal([]byte(out), &builds); err != nil {
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
