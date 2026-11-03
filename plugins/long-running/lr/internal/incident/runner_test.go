package incident

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	reportedStamp = "2026-10-01T17:20:10Z"
	oldConfig     = "steps:\n  - commands:\n      - yarn workspace pr-reviewer build\n"
	newConfig     = "steps:\n  - commands:\n      - bun install --cwd infra\n      - bun infra/generate.ts\n"
	advancedPR    = 28945
	advancedSrc   = 72957
	advancedFrom  = "1c7fea72dfa62a07be8a167874c98d1d19467ff2"
	advancedTo    = "38cefac65c896f897cbe84037160857ff77c5930"
	lateSource    = 72943
	tick          = 30 * time.Second
	incidentID    = "pr-review-1001"
	repairStamp   = "2026-10-01T17:35:00Z"
	ruling        = "379b70a: deploy ordering calls the release pipeline's own resolver, never a second implementation"
	entryPoint    = "releaseDAG at go/ci/release.go:170"
)

type replayRow struct {
	PR           int    `json:"pr"`
	Source       int    `json:"source"`
	Rebuild      int    `json:"rebuild"`
	Outcome      string `json:"outcome"`
	FinalPRState string `json:"final_pr_state"`
	Commit       string `json:"commit"`
}

type replayData struct {
	Onset  string `json:"onset"`
	Intake string `json:"intake"`
	Repair struct {
		PR       int    `json:"pr"`
		SHA      string `json:"sha"`
		LandedAt string `json:"landed_at"`
	} `json:"repair"`
	CanaryJobs []struct {
		Name  string `json:"name"`
		State string `json:"state"`
	} `json:"canary_jobs"`
	Builds []replayRow `json:"builds"`
}

func loadReplay(t *testing.T) replayData {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("testdata", "pr-review-replay.json"))
	if err != nil {
		t.Fatal(err)
	}
	var data replayData
	if err := json.Unmarshal(raw, &data); err != nil {
		t.Fatal(err)
	}
	return data
}

func parsed(t *testing.T, stamp string) time.Time {
	t.Helper()
	at, err := store.Parse(stamp)
	if err != nil {
		t.Fatal(err)
	}
	return at
}

type sourceBuild struct {
	number  int
	commit  string
	pr      int
	created time.Time
	failsAt time.Time
}

type createdBuild struct {
	number  int
	source  int
	commit  string
	created time.Time
	outcome string
}

type post struct {
	event string
	grant string
	facts map[string]any
	at    time.Time
}

type launched struct {
	lane, model, effort string
}

type entry struct {
	log, text, attach string
}

type verdict struct {
	investigation, verb, text, commit string
}

type replay struct {
	t             *testing.T
	data          replayData
	at            time.Time
	configured    bool
	changesConfig bool
	applies       []string
	launches      []launched
	receipts      map[string]LaunchReceipt
	engagedLanes  map[string]bool
	rebuilds      []int
	loseRebuild   map[int]string
	neverRebuilds map[int]bool
	losePost      map[string]bool
	posts         []post
	postedNever   bool
	asks          []string
	reports       []string
	fences        []string
	entries       []entry
	verdicts      []verdict
	refuseRecords bool
	premise       string
	loseFixLaunch bool
	created       []createdBuild
	outcomes      map[int]replayRow
	prs           map[int]PRView
	sources       []sourceBuild
	advancedAt    time.Time
}

func newReplay(t *testing.T) *replay {
	t.Helper()
	data := loadReplay(t)
	onset := parsed(t, data.Onset)
	r := &replay{
		t:             t,
		data:          data,
		at:            parsed(t, data.Intake),
		changesConfig: true,
		receipts:      map[string]LaunchReceipt{},
		engagedLanes:  map[string]bool{},
		loseRebuild:   map[int]string{},
		neverRebuilds: map[int]bool{},
		losePost:      map[string]bool{},
		outcomes:      map[int]replayRow{},
		prs:           map[int]PRView{},
		advancedAt:    parsed(t, data.Repair.LandedAt),
	}
	for index, row := range data.Builds {
		r.outcomes[row.Source] = row
		state := "OPEN"
		if row.Outcome == "failed" && row.FinalPRState == "closed" {
			state = "CLOSED"
		}
		r.prs[row.PR] = PRView{State: state, Head: row.Commit, URL: fmt.Sprintf("https://github.com/Forge-AI/monorepo/pull/%d", row.PR)}
		created := onset.Add(time.Duration(index) * tick)
		r.sources = append(r.sources, sourceBuild{row.Source, row.Commit, row.PR, created, created.Add(2 * time.Minute)})
	}
	for i := range r.sources {
		if r.sources[i].number == lateSource {
			r.sources[i].created = r.advancedAt.Add(-tick)
			r.sources[i].failsAt = r.sources[i].created.Add(3 * time.Minute)
		}
	}
	r.sources = append(r.sources, sourceBuild{advancedSrc, advancedFrom, advancedPR, onset.Add(50 * time.Minute), onset.Add(52 * time.Minute)})
	view := r.prs[advancedPR]
	view.Head = advancedFrom
	r.prs[advancedPR] = view
	return r
}

func (r *replay) now() time.Time {
	return r.at
}

func (r *replay) world() World {
	return World{Orca: r, GitHub: r, Buildkite: r, Activation: r, Comms: r, Records: r}
}

func (r *replay) Launch(_ context.Context, lane, model, effort, brief string) (LaunchReceipt, error) {
	if _, err := os.Stat(brief); err != nil {
		r.t.Errorf("launch of %s has no brief: %v", lane, err)
	}
	r.launches = append(r.launches, launched{lane, model, effort})
	if r.loseFixLaunch && strings.HasSuffix(lane, "-fix") {
		r.loseFixLaunch = false
		return LaunchReceipt{}, fmt.Errorf("%w: worker-start timed out", ErrResponseLost)
	}
	receipt := LaunchReceipt{Lane: lane, State: "ready", Task: "task-" + lane, Dispatch: "dispatch-" + lane, Terminal: "t1", Worktree: "/w"}
	r.receipts[lane] = receipt
	return receipt, nil
}

func (r *replay) Receipt(lane string) (*LaunchReceipt, error) {
	if receipt, ok := r.receipts[lane]; ok {
		return &receipt, nil
	}
	return nil, nil
}

func (r *replay) Engaged(_ context.Context, lane string, _ time.Time) (bool, error) {
	return r.engagedLanes[lane], nil
}

func (r *replay) view(number int) PRView {
	view := r.prs[number]
	if number == advancedPR && !r.at.Before(r.advancedAt) {
		view.Head = advancedTo
	}
	return view
}

func (r *replay) PRs(_ context.Context, numbers []int) (map[int]PRView, error) {
	views := map[int]PRView{}
	for _, number := range numbers {
		if _, known := r.prs[number]; known {
			views[number] = r.view(number)
		}
	}
	return views, nil
}

func (r *replay) Landed(_ context.Context, pr int) (string, error) {
	if pr != r.data.Repair.PR {
		r.t.Errorf("landing read of #%d", pr)
	}
	if r.at.Before(parsed(r.t, r.data.Repair.LandedAt)) {
		return "", nil
	}
	return r.data.Repair.SHA, nil
}

func (r *replay) Configuration(context.Context) (string, error) {
	if r.configured {
		return newConfig, nil
	}
	return oldConfig, nil
}

func (r *replay) sourceState(source sourceBuild) string {
	if r.at.Before(source.failsAt) {
		return "running"
	}
	return "failed"
}

func (r *replay) inWindow(source sourceBuild, since, until string) bool {
	return !source.created.Before(parsed(r.t, since)) && !source.created.After(parsed(r.t, until))
}

func (r *replay) Failed(_ context.Context, since, until string) ([]Build, error) {
	var found []Build
	for _, source := range r.sources {
		if r.inWindow(source, since, until) && r.sourceState(source) == "failed" {
			found = append(found, Build{Number: source.number, Commit: source.commit, State: "failed", PullRequest: &BuildPR{ID: strconv.Itoa(source.pr)}})
		}
	}
	return found, nil
}

func (r *replay) Unfinished(_ context.Context, since, until string) ([]Build, error) {
	var found []Build
	for _, source := range r.sources {
		if r.inWindow(source, since, until) && r.sourceState(source) == "running" {
			found = append(found, Build{Number: source.number, State: "running"})
		}
	}
	return found, nil
}

func (r *replay) render(build createdBuild) Build {
	elapsed := r.at.Sub(build.created)
	state := "running"
	if elapsed >= 3*time.Minute {
		state = build.outcome
	}
	prepare := "running"
	if elapsed >= time.Minute {
		prepare = "passed"
	}
	jobs := make([]Job, 0, len(r.data.CanaryJobs))
	jobs = append(jobs, Job{ID: "j1", Name: ":robot_face: Prepare PR evidence", State: prepare})
	for i, job := range r.data.CanaryJobs[1:] {
		jobState := "waiting"
		if state != "running" {
			jobState = job.State
		}
		jobs = append(jobs, Job{ID: fmt.Sprintf("j%d", i+2), Name: job.Name, State: jobState})
	}
	return Build{
		Number:      build.number,
		State:       state,
		Commit:      build.commit,
		RebuiltFrom: &BuildSource{Number: build.source},
		WebURL:      fmt.Sprintf("https://buildkite.com/forge/pr-review/builds/%d", build.number),
		Jobs:        jobs,
	}
}

func (r *replay) Since(_ context.Context, since string) ([]Build, error) {
	low := parsed(r.t, since)
	var found []Build
	for _, build := range r.created {
		if !build.created.Before(low) {
			found = append(found, r.render(build))
		}
	}
	return found, nil
}

func (r *replay) Rebuild(_ context.Context, number int) (Build, error) {
	if !r.configured {
		r.t.Errorf("rebuild of %d fired while the stored configuration was old", number)
	}
	r.rebuilds = append(r.rebuilds, number)
	if r.neverRebuilds[number] {
		return Build{}, fmt.Errorf("%w: timed out", ErrResponseLost)
	}
	mode := r.loseRebuild[number]
	delete(r.loseRebuild, number)
	if mode == "before" {
		return Build{}, fmt.Errorf("%w: rebuild %d timed out before Buildkite took it", ErrResponseLost, number)
	}
	build := createdBuild{number: 80000 + len(r.created), source: number, created: r.at, outcome: "passed"}
	if row, known := r.outcomes[number]; known {
		build.number, build.outcome = row.Rebuild, row.Outcome
	}
	index := slices.IndexFunc(r.sources, func(source sourceBuild) bool { return source.number == number })
	build.commit = r.sources[index].commit
	r.created = append(r.created, build)
	if mode == "after" {
		return Build{}, fmt.Errorf("%w: rebuild %d response lost", ErrResponseLost, number)
	}
	return r.render(build), nil
}

func (r *replay) Prepare(_ context.Context, sha string) (string, error) {
	if sha != r.data.Repair.SHA {
		r.t.Errorf("prepared %s", sha)
	}
	return "/activation", nil
}

func (r *replay) Drifted(context.Context, string) (bool, error) {
	return !r.configured, nil
}

func (r *replay) Apply(context.Context, string) ([]string, error) {
	r.applies = append(r.applies, store.Stamp(r.at))
	r.configured = r.configured || r.changesConfig
	return []string{"applied patched pipeline pr-review"}, nil
}

func (r *replay) Event(_ context.Context, event string, facts map[string]any, grant string) (int64, error) {
	r.posts = append(r.posts, post{event, grant, facts, r.at})
	if r.losePost[event] {
		delete(r.losePost, event)
		return 0, fmt.Errorf("%w: bus post %s lost", ErrResponseLost, event)
	}
	return int64(len(r.posts)), nil
}

func (r *replay) Find(_ context.Context, event string) (int64, error) {
	index := slices.IndexFunc(r.posts, func(held post) bool { return held.event == event })
	return int64(index + 1), nil
}

func (r *replay) Posted(_ context.Context, seq int64) (string, error) {
	held := r.posts[seq-1]
	if r.postedNever || r.at.Sub(held.at) < 20*time.Second {
		return "", nil
	}
	return strconv.FormatInt(held.at.Unix()+20, 10) + ".000000", nil
}

func (r *replay) AskRoot(_ context.Context, text string) error {
	r.asks = append(r.asks, text)
	return nil
}

func (r *replay) Report(_ context.Context, text string) error {
	r.reports = append(r.reports, text)
	return nil
}

func (r *replay) Fence(_ context.Context, text string) error {
	r.fences = append(r.fences, text)
	return nil
}

func (r *replay) Open(_ context.Context, _, _, premise string) (RecordIDs, error) {
	if r.refuseRecords {
		return RecordIDs{}, fmt.Errorf("%w: ccn: malformed refspec", ErrResponseLost)
	}
	r.premise = premise
	return RecordIDs{Investigation: "inv1234", Log: "log1234"}, nil
}

func (r *replay) Entry(_ context.Context, log, text, attach string) error {
	r.entries = append(r.entries, entry{log, text, attach})
	return nil
}

func (r *replay) Verdict(_ context.Context, investigation, verb, text, commit string) error {
	r.verdicts = append(r.verdicts, verdict{investigation, verb, text, commit})
	return nil
}

func (r *replay) events() []string {
	events := make([]string, len(r.posts))
	for i, held := range r.posts {
		events[i] = held.event
	}
	return events
}

func (r *replay) counts() map[int]int {
	counts := map[int]int{}
	for _, number := range r.rebuilds {
		counts[number]++
	}
	return counts
}

func (r *replay) lanes() []string {
	lanes := make([]string, len(r.launches))
	for i, held := range r.launches {
		lanes[i] = held.lane
	}
	return lanes
}

func (r *replay) asksWith(part string) []string {
	var found []string
	for _, ask := range r.asks {
		if strings.Contains(ask, part) {
			found = append(found, ask)
		}
	}
	return found
}

type harness struct {
	t      *testing.T
	store  Store
	fake   *replay
	runner *Runner
	id     string
}

func allGrants() map[string]string {
	return map[string]string{"thread": "g-thread", "channel": "g-channel", "sync": "g-sync", "rebuild": "g-rebuild"}
}

func opening(fake *replay, kind string, grants map[string]string) Opening {
	o := Opening{
		ID:           incidentID,
		Kind:         kind,
		Thread:       "https://forge-ai.slack.com/archives/C0BQSADC7SS/p1790875497000000",
		Onset:        fake.data.Onset,
		Reported:     reportedStamp,
		Bus:          "bus1",
		CommsLane:    "incident-comms",
		RootLane:     "release-v3",
		Checkout:     "/monorepo",
		ExpectConfig: "infra/generate.ts",
		OrcaRun:      "run1",
		OrcaRepo:     "repo1",
		Repo:         "Forge-AI/monorepo",
		Trunk:        "dev",
		Grants:       grants,
	}
	if kind == KindAlert {
		o.Target = "api"
	}
	return o
}

func openWith(t *testing.T, o func(*replay) Opening) *harness {
	t.Helper()
	t.Parallel()
	fake := newReplay(t)
	s, release := Store{Root: filepath.Join(t.TempDir(), "incidents")}.Held()
	t.Cleanup(release)
	id, err := s.Open(o(fake), fake.at)
	if err != nil {
		t.Fatal(err)
	}
	h := &harness{t: t, store: s, fake: fake, id: id}
	h.restart()
	return h
}

func openedKind(t *testing.T, kind string) *harness {
	t.Helper()
	return openWith(t, func(fake *replay) Opening { return opening(fake, kind, allGrants()) })
}

func opened(t *testing.T) *harness {
	t.Helper()
	return openedKind(t, KindPRReview)
}

func (h *harness) restart() {
	h.t.Helper()
	runner, err := NewRunner(h.store, h.id, h.fake.world(), h.fake.now, slog.New(slog.NewTextHandler(io.Discard, nil)))
	if err != nil {
		h.t.Fatal(err)
	}
	h.runner = runner
}

func (h *harness) note(key string, value any) {
	h.t.Helper()
	if err := h.store.Input(h.id, func(tx *Tx) error { return tx.SetFact(key, value) }); err != nil {
		h.t.Fatal(err)
	}
}

func (h *harness) fixWorker() {
	h.t.Helper()
	facts, err := h.store.Facts(h.id, "mechanism", "repair_pr")
	if err != nil {
		h.t.Fatal(err)
	}
	if !h.fake.at.Before(parsed(h.t, h.fake.data.Intake).Add(4*time.Minute)) && facts.Mechanism == "" {
		h.note("mechanism", "#28934 stopped committing infra/ci/src/generated; prepare never generates it")
	}
	if !h.fake.at.Before(parsed(h.t, repairStamp)) && facts.RepairPR == 0 {
		h.note("repair_pr", h.fake.data.Repair.PR)
	}
}

func (h *harness) load() Incident {
	h.t.Helper()
	incident, err := h.store.Load(h.id)
	if err != nil {
		h.t.Fatal(err)
	}
	return incident
}

func (h *harness) drive(until time.Time, worker func()) Incident {
	h.t.Helper()
	incident := h.load()
	for !h.fake.at.After(until) && incident.Status != StatusClosed {
		if worker != nil {
			worker()
		}
		if err := h.runner.Advance(context.Background()); err != nil {
			h.t.Fatal(err)
		}
		incident = h.load()
		h.fake.at = h.fake.at.Add(tick)
	}
	return incident
}

func (h *harness) intake(after time.Duration) time.Time {
	h.t.Helper()
	return parsed(h.t, h.fake.data.Intake).Add(after)
}

func (h *harness) landed(after time.Duration) time.Time {
	h.t.Helper()
	return parsed(h.t, h.fake.data.Repair.LandedAt).Add(after)
}

func milestoneNames(incident Incident) []string {
	names := make([]string, len(incident.Milestones))
	for i, entry := range incident.Milestones {
		names[i] = entry.Name
	}
	return names
}

func TestReplayReachesTheFinalReplyWithNoRootTurn(t *testing.T) {
	h := opened(t)
	fake := h.fake
	incident := h.drive(h.intake(2*time.Hour), h.fixWorker)

	if incident.Status != StatusClosed {
		t.Fatalf("status %s", incident.Status)
	}
	if len(fake.asks) != 0 {
		t.Fatalf("the coordinator was asked: %v", fake.asks)
	}
	want := []launched{{"incident-" + h.id + "-fix", "incident", "xhigh"}, {"incident-" + h.id + "-evidence", "incident", "xhigh"}}
	if !reflect.DeepEqual(fake.launches, want) {
		t.Fatalf("launches %v", fake.launches)
	}
	if len(fake.applies) != 1 {
		t.Fatalf("applies %v", fake.applies)
	}
	for number, count := range fake.counts() {
		if count != 1 {
			t.Fatalf("build %d rebuilt %d times", number, count)
		}
	}
	if slices.Contains(fake.rebuilds, advancedSrc) || slices.Contains(fake.rebuilds, 72923) || !slices.Contains(fake.rebuilds, lateSource) {
		t.Fatalf("rebuilds %v", fake.rebuilds)
	}
	closed := map[int]bool{}
	for pr, view := range fake.prs {
		if view.State == "CLOSED" {
			closed[pr] = true
		}
	}
	for _, number := range fake.rebuilds {
		if row, known := fake.outcomes[number]; known && closed[row.PR] {
			t.Fatalf("rebuilt closed #%d", row.PR)
		}
	}
	if fake.rebuilds[0] != 72867 {
		t.Fatalf("canary %d", fake.rebuilds[0])
	}
	if events := fake.events(); !reflect.DeepEqual(events, []string{"ack", "pr", "review-request", "landed", "live", "recovered"}) {
		t.Fatalf("events %v", events)
	}
	review := fake.posts[slices.Index(fake.events(), "review-request")]
	if review.grant != "g-channel" || review.facts["surface"] != "channel" {
		t.Fatalf("review request %+v", review)
	}
	final := fake.posts[len(fake.posts)-1].facts
	accounting := incident.Facts["accounting"].(map[string]any)
	if asInt(final["rerun"]) != asInt(accounting["rerun"]) || asInt(final["rerun"]) != len(fake.rebuilds) || asInt(final["green"]) != asInt(accounting["green"]) {
		t.Fatalf("final %v accounting %v", final, accounting)
	}
	skipped := map[int]bool{}
	for _, held := range accounting["skipped"].([]any) {
		skipped[asInt(held.(map[string]any)["pr"])] = true
	}
	closed[advancedPR] = true
	for pr := range closed {
		if !skipped[pr] {
			t.Fatalf("#%d is not accounted as skipped: %v", pr, skipped)
		}
	}
	liveAt := textOf(final["live_at"])
	if (!strings.HasSuffix(liveAt, "am") && !strings.HasSuffix(liveAt, "pm")) || strings.Contains(liveAt, "Z") {
		t.Fatalf("live_at %q", liveAt)
	}
	if names := milestoneNames(incident); !reflect.DeepEqual(names, []string{"opened", "mechanism", "pr", "landed", "activated", "live", "recovered", "closed"}) {
		t.Fatalf("milestones %v", names)
	}
}

func TestAMergeWithoutASyncGrantStaysActivationPendingAndRekicksNothing(t *testing.T) {
	grants := allGrants()
	delete(grants, "sync")
	h := openWith(t, func(fake *replay) Opening { return opening(fake, KindPRReview, grants) })
	fake := h.fake
	incident := h.drive(h.landed(30*time.Minute), h.fixWorker)

	if incident.Status != StatusActivationPending {
		t.Fatalf("status %s", incident.Status)
	}
	if len(fake.applies) != 0 || len(fake.rebuilds) != 0 {
		t.Fatalf("applies %v rebuilds %v", fake.applies, fake.rebuilds)
	}
	if asks := fake.asksWith("sync grant"); len(asks) != 1 || len(fake.asks) != 1 {
		t.Fatalf("asks %v", fake.asks)
	}
	if !slices.Contains(fake.events(), "landed") {
		t.Fatalf("events %v", fake.events())
	}

	if err := h.store.Input(h.id, func(tx *Tx) error { return tx.SetChild("grants", "sync", "g-sync") }); err != nil {
		t.Fatal(err)
	}
	incident = h.drive(fake.at.Add(time.Hour), h.fixWorker)
	if incident.Status != StatusClosed {
		t.Fatalf("status %s", incident.Status)
	}
	if open := incident.OpenDecisions(); len(open) != 0 {
		t.Fatalf("open decisions %v", open)
	}
}

func TestOldConfigurationAfterApplyBlocksTheCanary(t *testing.T) {
	h := opened(t)
	h.fake.changesConfig = false
	incident := h.drive(h.landed(20*time.Minute), h.fixWorker)

	if incident.Status != StatusActivationPending || len(h.fake.rebuilds) != 0 || len(h.fake.applies) != 1 {
		t.Fatalf("status %s rebuilds %v applies %v", incident.Status, h.fake.rebuilds, h.fake.applies)
	}
	if len(h.fake.asksWith("read-back still drifts")) == 0 {
		t.Fatalf("asks %v", h.fake.asks)
	}
}

func TestARestartAndDuplicateNotesSendEachCommandOnce(t *testing.T) {
	h := opened(t)
	fake := h.fake
	h.drive(h.landed(2*time.Minute), h.fixWorker)
	h.note("repair_pr", fake.data.Repair.PR)
	h.restart()
	incident := h.drive(fake.at.Add(time.Hour), h.fixWorker)

	if incident.Status != StatusClosed || len(fake.applies) != 1 || len(fake.launches) != 2 {
		t.Fatalf("status %s applies %v launches %v", incident.Status, fake.applies, fake.launches)
	}
	for number, count := range fake.counts() {
		if count != 1 {
			t.Fatalf("build %d rebuilt %d times", number, count)
		}
	}
	prPosts := 0
	for _, event := range fake.events() {
		if event == "pr" {
			prPosts++
		}
	}
	if prPosts != 1 {
		t.Fatalf("pr posts %d", prPosts)
	}
}

func rebuildsOf(t *testing.T, s Store, id, prefix string) []Action {
	t.Helper()
	all, err := s.Actions(id, "rebuild")
	if err != nil {
		t.Fatal(err)
	}
	return slices.DeleteFunc(all, func(action Action) bool { return !strings.HasPrefix(action.ActionID, prefix) })
}

func TestALostRebuildResponseIsReconciledNotRetried(t *testing.T) {
	h := opened(t)
	h.fake.loseRebuild[72860] = "after"
	incident := h.drive(h.intake(2*time.Hour), h.fixWorker)

	if incident.Status != StatusClosed || h.fake.counts()[72860] != 1 {
		t.Fatalf("status %s counts %v", incident.Status, h.fake.counts())
	}
	actions := rebuildsOf(t, h.store, h.id, "rebuild:28349@")
	if len(actions) != 1 || actions[0].Status != ActionVerified || asInt(actions[0].Response["build"]) != 72978 {
		t.Fatalf("actions %+v", actions)
	}
}

func TestALostRebuildThatNeverReachedBuildkiteIsRetriedOnceAfterReconciling(t *testing.T) {
	h := opened(t)
	h.fake.loseRebuild[72860] = "before"
	incident := h.drive(h.intake(2*time.Hour), h.fixWorker)

	if incident.Status != StatusClosed || h.fake.counts()[72860] != 2 {
		t.Fatalf("status %s counts %v", incident.Status, h.fake.counts())
	}
	actions := rebuildsOf(t, h.store, h.id, "rebuild:28349@")
	if len(actions) != 2 || actions[0].Status != ActionFailed || actions[1].Status != ActionVerified || !strings.HasPrefix(string(actions[0].Reason), reconciled) {
		t.Fatalf("actions %+v", actions)
	}
}

func TestALostResponseStaysUnverifiableUntilReconciled(t *testing.T) {
	h := opened(t)
	h.fake.loseRebuild[72860] = "after"
	ctx := context.Background()
	for !slices.Contains(h.fake.rebuilds, 72860) {
		h.fixWorker()
		if err := h.runner.Advance(ctx); err != nil {
			t.Fatal(err)
		}
		h.fake.at = h.fake.at.Add(tick)
	}
	status := func() ActionStatus {
		actions := rebuildsOf(t, h.store, h.id, "rebuild:28349@")
		if len(actions) != 1 {
			t.Fatalf("actions %+v", actions)
		}
		return actions[0].Status
	}
	if got := status(); got != ActionStarted {
		t.Fatalf("status %s", got)
	}
	if err := h.runner.markLost(ctx); err != nil {
		t.Fatal(err)
	}
	if got := status(); got != ActionUnverifiable {
		t.Fatalf("status %s", got)
	}
	if err := h.runner.reconcileRebuilds(ctx); err != nil {
		t.Fatal(err)
	}
	if got := status(); got != ActionCompleted && got != ActionVerified {
		t.Fatalf("status %s", got)
	}
}

func TestALostCommsPostIsFoundOnTheBusNotReposted(t *testing.T) {
	h := opened(t)
	h.fake.losePost["ack"] = true
	incident := h.drive(h.intake(2*time.Hour), h.fixWorker)

	if incident.Status != StatusClosed {
		t.Fatalf("status %s", incident.Status)
	}
	acks := 0
	for _, event := range h.fake.events() {
		if event == "ack" {
			acks++
		}
	}
	if acks != 1 || incident.Actions["comms:ack"].Status != ActionVerified {
		t.Fatalf("acks %d action %+v", acks, incident.Actions["comms:ack"])
	}
}

func TestClaudeIsRefusedAsTheIncidentFixRoute(t *testing.T) {
	h := opened(t)
	h.note("routes", map[string][]string{RoleFix: {"opus", "xhigh"}})
	var refused *RouteRefused
	if err := h.runner.Advance(context.Background()); !errors.As(err, &refused) {
		t.Fatalf("got %v, want a refused route", err)
	}
	if len(h.fake.launches) != 0 {
		t.Fatalf("launches %v", h.fake.launches)
	}
}

func TestRouteAssertions(t *testing.T) {
	for _, c := range [][2]string{{"fix", "opus"}, {"fix", "sol"}, {"evidence", "claude-opus-5-5"}, {"backup", "fable"}, {"backup", "astra"}, {"backup", "codex"}} {
		var refused *RouteRefused
		if err := assertRoute(c[0], c[1]); !errors.As(err, &refused) {
			t.Errorf("%s on %s: %v", c[0], c[1], err)
		}
	}
	for _, c := range [][2]string{{"fix", "incident"}, {"evidence", "incident"}, {"backup", "incident-opus"}} {
		if err := assertRoute(c[0], c[1]); err != nil {
			t.Errorf("%s on %s: %v", c[0], c[1], err)
		}
	}
}

func TestBackupLaunchesOnIncidentOpusAfterFifteenQuietMinutes(t *testing.T) {
	h := opened(t)
	fake := h.fake
	h.drive(h.intake(16*time.Minute), nil)

	want := []launched{
		{laneName(h.id, "fix"), "incident", "xhigh"},
		{laneName(h.id, "evidence"), "incident", "xhigh"},
		{laneName(h.id, "backup"), "incident-opus", "xhigh"},
	}
	if !reflect.DeepEqual(fake.launches, want) {
		t.Fatalf("launches %v", fake.launches)
	}
}

func TestNoBackupLaunchesWhileTheDeskClocksOpusFallbackIsLaunchingOrLive(t *testing.T) {
	h := opened(t)
	fake := h.fake
	h.note("adopted", map[string]string{"fix": "api-1n85-fix"})
	fake.engagedLanes["api-1n85-fix-opus"] = true
	h.drive(h.intake(16*time.Minute), nil)

	if lanes := fake.lanes(); !reflect.DeepEqual(lanes, []string{laneName(h.id, "evidence")}) {
		t.Fatalf("lanes %v", lanes)
	}
	if _, held := h.load().Actions["dispatch:backup"]; held {
		t.Fatal("a backup action was recorded")
	}

	delete(fake.engagedLanes, "api-1n85-fix-opus")
	h.drive(h.intake(17*time.Minute), nil)
	if lanes := fake.lanes(); !reflect.DeepEqual(lanes[1:], []string{laneName(h.id, "backup")}) {
		t.Fatalf("lanes %v", lanes)
	}
}

func transfer(t *testing.T, s Store, id, to string) {
	t.Helper()
	err := s.transact(id, func(tx *Tx) error {
		tx.Owner = to
		tx.OwnerGeneration++
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
}

func TestATransferredIncidentRefusesTheFormerOwner(t *testing.T) {
	h := opened(t)
	ctx := context.Background()
	if err := h.runner.Advance(ctx); err != nil {
		t.Fatal(err)
	}
	transfer(t, h.store, h.id, "runner-b")
	posts := len(h.fake.posts)
	h.fake.at = h.fake.at.Add(tick)
	if err := h.runner.Advance(ctx); !errors.Is(err, ErrStaleGeneration) {
		t.Fatalf("got %v, want a stale generation", err)
	}
	if len(h.fake.posts) != posts {
		t.Fatalf("a former owner posted: %v", h.fake.events())
	}
	h.restart()
	if h.runner.generation != 2 {
		t.Fatalf("generation %d", h.runner.generation)
	}
}

func TestAMissingCommsAnswerEscalatesOnce(t *testing.T) {
	h := opened(t)
	h.fake.postedNever = true
	h.drive(h.intake(10*time.Minute), nil)

	overdue := 0
	for _, ask := range h.fake.asks {
		if strings.Contains(ask, "comms") && strings.Contains(ask, "is overdue") {
			overdue++
		}
	}
	if overdue != 1 {
		t.Fatalf("asks %v", h.fake.asks)
	}
}

func TestPacificTimesCarryNoZone(t *testing.T) {
	for stamp, want := range map[string]string{"2026-10-01T17:49:00Z": "10:49am", "2026-10-01T18:08:43Z": "11:08am"} {
		if got := pacific(stamp); got != want {
			t.Errorf("%s: %s, want %s", stamp, got, want)
		}
	}
}

func TestAnAlertWithNoPipelineClosesOnLiveEvidence(t *testing.T) {
	h := openedKind(t, KindAlert)
	fake := h.fake
	h.drive(h.intake(5*time.Minute), h.fixWorker)
	h.note("live", "API-1N82 error rate back to 0 since 11:20am")
	incident := h.drive(fake.at.Add(5*time.Minute), h.fixWorker)

	if incident.Status != StatusClosed || len(fake.asks) != 0 || len(fake.applies) != 0 || len(fake.rebuilds) != 0 {
		t.Fatalf("status %s asks %v applies %v rebuilds %v", incident.Status, fake.asks, fake.applies, fake.rebuilds)
	}
	if !reflect.DeepEqual(fake.fences, []string{"fence api from applies and deploys except " + laneName(h.id, "fix")}) {
		t.Fatalf("fences %v", fake.fences)
	}
	if events := fake.events(); !reflect.DeepEqual(events, []string{"ack", "live", "recovered"}) {
		t.Fatalf("events %v", events)
	}
}

func TestALostLaunchWithoutAReceiptAsksInsteadOfRelaunching(t *testing.T) {
	h := opened(t)
	h.fake.loseFixLaunch = true
	h.drive(h.intake(3*time.Minute), nil)

	fixes := 0
	for _, lane := range h.fake.lanes() {
		if lane == laneName(h.id, "fix") {
			fixes++
		}
	}
	if fixes != 1 || len(h.fake.asksWith("left no receipt")) == 0 {
		t.Fatalf("lanes %v asks %v", h.fake.lanes(), h.fake.asks)
	}
}

func TestACanaryWaitingOnItsGrantLaunchesOnceGranted(t *testing.T) {
	grants := allGrants()
	delete(grants, "rebuild")
	h := openWith(t, func(fake *replay) Opening { return opening(fake, KindPRReview, grants) })
	fake := h.fake
	incident := h.drive(h.landed(10*time.Minute), h.fixWorker)
	if _, held := incident.Facts["canary"]; held || len(fake.rebuilds) != 0 {
		t.Fatalf("canary %v rebuilds %v", incident.Facts["canary"], fake.rebuilds)
	}

	if err := h.store.Input(h.id, func(tx *Tx) error { return tx.SetChild("grants", "rebuild", "g-rebuild") }); err != nil {
		t.Fatal(err)
	}
	incident = h.drive(fake.at.Add(time.Hour), h.fixWorker)
	if incident.Status != StatusClosed || fake.rebuilds[0] != 72867 {
		t.Fatalf("status %s rebuilds %v", incident.Status, fake.rebuilds)
	}
}

func TestACrashAfterVerifyingTheSyncResumesWithoutASecondApply(t *testing.T) {
	h := opened(t)
	fake := h.fake
	h.drive(h.landed(40*time.Second), h.fixWorker)
	err := h.store.Owned(h.id, 1, func(tx *Tx) error {
		if err := tx.loadMilestones(); err != nil {
			return err
		}
		tx.milestones = slices.DeleteFunc(tx.milestones, func(entry Milestone) bool { return entry.Name == "activated" })
		tx.Status = StatusActivationPending
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	incident := h.drive(fake.at.Add(time.Hour), h.fixWorker)
	if incident.Status != StatusClosed || len(fake.applies) != 1 {
		t.Fatalf("status %s applies %v", incident.Status, fake.applies)
	}
}

func TestARebuildThatKeepsFailingToReachBuildkiteStopsAtTheAttemptCap(t *testing.T) {
	h := opened(t)
	fake := h.fake
	fake.neverRebuilds[72860] = true
	incident := h.drive(h.intake(2*time.Hour), h.fixWorker)

	if fake.counts()[72860] != maxAttempts || len(fake.asksWith("failed 3 times")) != 1 || incident.Status != StatusClosed {
		t.Fatalf("counts %v asks %v status %s", fake.counts(), fake.asks, incident.Status)
	}
	unlaunched := incident.Facts["accounting"].(map[string]any)["unlaunched"].([]any)
	last := unlaunched[len(unlaunched)-1].(map[string]any)
	if !strings.HasSuffix(textOf(last["key"]), "#3") {
		t.Fatalf("unlaunched %v", unlaunched)
	}
}

func TestNoFailedWorkStillReachesTheFinalReply(t *testing.T) {
	h := opened(t)
	fake := h.fake
	for pr, view := range fake.prs {
		view.State = "CLOSED"
		fake.prs[pr] = view
	}
	incident := h.drive(h.intake(2*time.Hour), h.fixWorker)
	if incident.Status != StatusClosed || len(fake.rebuilds) != 0 {
		t.Fatalf("status %s rebuilds %v", incident.Status, fake.rebuilds)
	}
	if asInt(incident.Facts["accounting"].(map[string]any)["rerun"]) != 0 {
		t.Fatalf("accounting %v", incident.Facts["accounting"])
	}
}

func TestAnAdoptedLaneIsRecordedNotLaunched(t *testing.T) {
	h := openedKind(t, KindAlert)
	fake := h.fake
	h.note("adopted", map[string]string{"fix": "api-1n85-fix"})
	h.drive(h.intake(time.Minute), nil)

	if lanes := fake.lanes(); !reflect.DeepEqual(lanes, []string{laneName(h.id, "evidence")}) {
		t.Fatalf("lanes %v", lanes)
	}
	if !reflect.DeepEqual(fake.fences, []string{"fence api from applies and deploys except api-1n85-fix"}) {
		t.Fatalf("fences %v", fake.fences)
	}
	action := h.load().Actions["dispatch:fix"]
	if !reflect.DeepEqual(action.VerificationReceipt, map[string]any{"lane": "api-1n85-fix", "adopted": true}) {
		t.Fatalf("receipt %v", action.VerificationReceipt)
	}
	brief := readFile(t, filepath.Join(h.store.Root, h.id, laneName(h.id, "evidence")+briefSuffix))
	if !strings.Contains(brief, "Feed api-1n85-fix and the executor") {
		t.Fatalf("evidence brief does not name the adopted lane:\n%s", brief)
	}
}

func readFile(t *testing.T, path string) string {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(raw)
}

func TestANotOursVerdictClosesWithAFinalReplyAndExonerates(t *testing.T) {
	h := openedKind(t, KindAlert)
	fake := h.fake
	h.drive(h.intake(3*time.Minute), h.fixWorker)
	h.note("not_ours", "one employee sand-cli event; no user traffic")
	incident := h.drive(fake.at.Add(20*time.Minute), nil)

	if incident.Status != StatusClosed {
		t.Fatalf("status %s", incident.Status)
	}
	if events := fake.events(); !reflect.DeepEqual(events, []string{"ack", "recovered"}) {
		t.Fatalf("events %v", events)
	}
	if !strings.HasPrefix(textOf(fake.posts[len(fake.posts)-1].facts["not_ours"]), "one employee") {
		t.Fatalf("final %v", fake.posts[len(fake.posts)-1].facts)
	}
	if slices.Contains(fake.lanes(), laneName(h.id, "backup")) {
		t.Fatalf("lanes %v", fake.lanes())
	}
	want := verdict{"inv1234", "exonerate", "one employee sand-cli event; no user traffic", ""}
	if !slices.Contains(fake.verdicts, want) {
		t.Fatalf("verdicts %v", fake.verdicts)
	}
}

func TestTheRecordLivesInCCNotesAndBriefsCarryItsIDs(t *testing.T) {
	h := opened(t)
	fake := h.fake
	incident := h.drive(h.intake(2*time.Hour), h.fixWorker)

	records := incident.Facts["records"].(map[string]any)
	if records["investigation"] != "inv1234" || records["log"] != "log1234" {
		t.Fatalf("records %v", records)
	}
	wantPremise := textOf(incident.Facts["alert"]) + " reports a pr-review outage on the pr-review pipeline since "
	if !strings.HasPrefix(fake.premise, wantPremise) {
		t.Fatalf("premise %q", fake.premise)
	}
	var attached, logged []string
	for _, held := range fake.entries {
		if held.attach != "" {
			attached = append(attached, filepath.Base(held.attach))
			continue
		}
		name, _, _ := strings.Cut(held.text, " at ")
		logged = append(logged, name)
	}
	if !reflect.DeepEqual(attached, []string{laneName(h.id, "fix") + briefSuffix, laneName(h.id, "evidence") + briefSuffix}) {
		t.Fatalf("attached %v", attached)
	}
	brief := readFile(t, filepath.Join(h.store.Root, h.id, laneName(h.id, "evidence")+briefSuffix))
	if !strings.Contains(brief, "log1234") || !strings.Contains(brief, "inv1234") {
		t.Fatalf("brief lacks the record ids:\n%s", brief)
	}
	if !reflect.DeepEqual(logged, milestoneNames(incident)) {
		t.Fatalf("logged %v, milestones %v", logged, milestoneNames(incident))
	}
	if len(fake.verdicts) != 2 || fake.verdicts[0].verb != "root-cause" || fake.verdicts[0].commit != "" || fake.verdicts[1].verb != "fix" || fake.verdicts[1].commit != fake.data.Repair.SHA {
		t.Fatalf("verdicts %v", fake.verdicts)
	}
}

func TestARefusedRecordAsksTheRootOnceAndStillLaunches(t *testing.T) {
	h := opened(t)
	fake := h.fake
	fake.refuseRecords = true
	h.drive(h.intake(2*time.Minute), nil)

	if !reflect.DeepEqual(fake.lanes(), []string{laneName(h.id, "fix"), laneName(h.id, "evidence")}) {
		t.Fatalf("lanes %v", fake.lanes())
	}
	if len(fake.asksWith("cc-notes refused")) != 1 || len(fake.entries) != 0 {
		t.Fatalf("asks %v entries %v", fake.asks, fake.entries)
	}
}

func alertWithRulings(t *testing.T) *harness {
	t.Helper()
	return openWith(t, func(fake *replay) Opening {
		o := opening(fake, KindAlert, allGrants())
		o.Rulings, o.EntryPoint = []string{ruling}, entryPoint
		return o
	})
}

func TestTheFixBriefQuotesTheRulingsAndEntryPoint(t *testing.T) {
	h := alertWithRulings(t)
	h.drive(h.intake(time.Minute), nil)

	brief := readFile(t, filepath.Join(h.store.Root, h.id, laneName(h.id, "fix")+briefSuffix))
	if !strings.Contains(brief, "  "+ruling+"\n  entry point: "+entryPoint+"\n") {
		t.Fatalf("brief:\n%s", brief)
	}
	if !strings.Contains(brief, `--design-check "<symbol at file:line>; <each ruling, met how>; leaves out: <none, or each piece>"`) {
		t.Fatalf("brief:\n%s", brief)
	}
}

func TestABriefWithNoRulingsSaysSoAndAsksNothing(t *testing.T) {
	h := openedKind(t, KindAlert)
	h.drive(h.intake(time.Minute), nil)

	brief := readFile(t, filepath.Join(h.store.Root, h.id, laneName(h.id, "fix")+briefSuffix))
	if !strings.Contains(brief, "  none recorded for this subsystem\n  entry point: none named\n") || len(h.fake.asks) != 0 {
		t.Fatalf("asks %v brief:\n%s", h.fake.asks, brief)
	}
}

func TestTheFixBriefNamesTheReportTimeAndItsDueTime(t *testing.T) {
	h := openedKind(t, KindAlert)
	h.drive(h.intake(time.Minute), nil)

	brief := readFile(t, filepath.Join(h.store.Root, h.id, laneName(h.id, "fix")+briefSuffix))
	for _, want := range []string{"Reported 10:20am PT.", "by 10:40am PT;"} {
		if !strings.Contains(brief, want) {
			t.Fatalf("brief lacks %q:\n%s", want, brief)
		}
	}
	for _, refused := range []string{"go build", "go vet"} {
		if strings.Contains(brief, refused) {
			t.Fatalf("brief carries %q", refused)
		}
	}
}

func TestADesignCheckReachesTheRootOnceAndItsConfirmationIsAMilestone(t *testing.T) {
	h := alertWithRulings(t)
	fake := h.fake
	h.drive(h.intake(time.Minute), nil)
	if len(fake.asks) != 0 {
		t.Fatalf("asks %v", fake.asks)
	}

	check := entryPoint + " called from deploy.planned; 379b70a met; leaves out: none"
	h.note("design_check", check)
	h.drive(fake.at.Add(2*time.Minute), nil)
	want := fmt.Sprintf("%s: design check before the PR: %s; confirm it calls %s and meets %s with `lr incident note --incident %s --design-ok`, or redirect the fix lane", h.id, check, entryPoint, ruling, h.id)
	if !reflect.DeepEqual(fake.asks, []string{want}) {
		t.Fatalf("asks %v", fake.asks)
	}

	h.note("design_ok", true)
	incident := h.drive(fake.at.Add(time.Minute), nil)
	entry, found := incident.Reached("design")
	if !found || entry.Text != "confirmed: "+check || len(incident.OpenDecisions()) != 0 || len(fake.asks) != 1 {
		t.Fatalf("milestone %+v open %v asks %v", entry, incident.OpenDecisions(), fake.asks)
	}
}

func TestAPROnARuledSubsystemWithNoDesignCheckAsksTheRootToHoldIt(t *testing.T) {
	h := alertWithRulings(t)
	fake := h.fake
	h.note("repair_pr", fake.data.Repair.PR)
	h.drive(h.intake(time.Minute), nil)

	want := fmt.Sprintf("%s: #%d opened with no design check; hold it, then confirm it calls %s and meets %s with `lr incident note --incident %s --design-ok`, or redirect the fix lane", h.id, fake.data.Repair.PR, entryPoint, ruling, h.id)
	if asks := fake.asksWith("design"); !reflect.DeepEqual(asks, []string{want}) {
		t.Fatalf("asks %v", asks)
	}
}

type flakyComms struct {
	Comms
	replay *replay
	reads  int
}

func (f *flakyComms) Posted(ctx context.Context, seq int64) (string, error) {
	f.reads++
	f.replay.at = f.replay.at.Add(tick)
	if f.reads == 1 {
		return "", fmt.Errorf("%w: cci read timed out", ErrResponseLost)
	}
	return f.Comms.Posted(ctx, seq)
}

func TestALostReadLeavesTheRunnerRunning(t *testing.T) {
	h := openedKind(t, KindAlert)
	h.runner.World.Comms = &flakyComms{Comms: h.fake, replay: h.fake}
	h.note("live", "error rate back to 0")
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
	defer cancel()
	header, err := h.runner.Run(ctx, time.Millisecond)
	if err != nil {
		t.Fatal(err)
	}
	if header.Status != StatusClosed {
		t.Fatalf("status %s", header.Status)
	}
}

func TestAnUnexpectedFailureStopsTheRunner(t *testing.T) {
	h := opened(t)
	h.note("routes", map[string][]string{RoleFix: {"opus", "xhigh"}})
	var refused *RouteRefused
	if _, err := h.runner.Run(context.Background(), time.Millisecond); !errors.As(err, &refused) {
		t.Fatalf("got %v, want a refused route", err)
	}
}

func TestRecoveryReadsStayFlatWithHistory(t *testing.T) {
	h := opened(t)
	const history = 500
	err := h.store.Owned(h.id, 1, func(tx *Tx) error {
		for i := range history {
			key := fmt.Sprintf("history:%d", i)
			if _, err := tx.Accept(key, "history", "old", "", h.fake.at); err != nil {
				return err
			}
			if _, err := tx.Verify(key, map[string]any{"done": true}); err != nil {
				return err
			}
		}
		if _, err := tx.Accept("active", "send", "new", "", h.fake.at); err != nil {
			return err
		}
		_, err := tx.Start("active", h.fake.at, 0)
		return err
	})
	if err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	for _, step := range []func(context.Context) error{h.runner.exhausted, h.runner.markLost, h.runner.deadlines} {
		if err := step(ctx); err != nil {
			t.Fatal(err)
		}
	}
	id, err := h.runner.nextID("dispatch:fix")
	if err != nil {
		t.Fatal(err)
	}
	if id != "dispatch:fix" {
		t.Fatalf("next id %s", id)
	}
	active, err := h.store.Action(h.id, "active")
	if err != nil {
		t.Fatal(err)
	}
	if active.Status != ActionUnverifiable {
		t.Fatalf("active %+v", active)
	}
	verified, err := h.store.Actions(h.id, "history", ActionVerified)
	if err != nil {
		t.Fatal(err)
	}
	if len(verified) != history {
		t.Fatalf("history %d", len(verified))
	}
}

func TestABackupLaunchesWhenAMechanismHasNoPRInFifteenMinutes(t *testing.T) {
	h := opened(t)
	h.note("mechanism", "#28934 stopped committing infra/ci/src/generated")
	h.drive(h.intake(14*time.Minute), nil)
	if h.launchedBackup() {
		t.Fatal("a backup launched before the budget")
	}

	h.drive(h.intake(16*time.Minute), nil)

	if !h.launchedBackup() {
		t.Fatalf("launches %v", h.fake.launches)
	}
}

func TestABackupLaunchesWhenTheRepairPRHasNotLandedInTwentyMinutesUnlessAHumanHoldsIt(t *testing.T) {
	cases := []struct {
		name   string
		review bool
		backup bool
	}{
		{"nobody holds it", false, true},
		{"a human reviews it", true, false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			h := opened(t)
			h.note("repair_pr", 48000)
			h.note("human_review", c.review)

			h.drive(h.intake(21*time.Minute), nil)

			if h.launchedBackup() != c.backup {
				t.Fatalf("launches %v", h.fake.launches)
			}
		})
	}
}

func TestAnExhaustedBackupTellsRootToStartItInProcess(t *testing.T) {
	h := opened(t)
	lane := laneName(h.id, "backup")
	id := fmt.Sprintf("dispatch:%s#%d", RoleBackup, maxAttempts)
	if err := h.store.Input(h.id, func(tx *Tx) error {
		if _, err := tx.Accept(id, "dispatch", lane, "", h.fake.now()); err != nil {
			return err
		}
		_, err := tx.Fail(id, "orca refused")
		return err
	}); err != nil {
		t.Fatal(err)
	}

	if err := h.runner.exhausted(context.Background()); err != nil {
		t.Fatal(err)
	}

	if !slices.ContainsFunc(h.fake.asks, func(text string) bool {
		return strings.Contains(text, "start "+lane+" in-process now")
	}) {
		t.Fatalf("asks %v", h.fake.asks)
	}
}

func (h *harness) launchedBackup() bool {
	return slices.Contains(h.fake.lanes(), laneName(h.id, "backup"))
}
