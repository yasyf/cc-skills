package bulk

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/orca"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/wait"
)

const (
	testDrive    = "drive-1"
	testReader   = "struggles-1"
	testTriage   = "struggles-triage-1"
	testLauncher = "meta-desk-2"
	testLink     = "https://mac.tailnet.example:8643/one-pagers/struggles/"
	fixBrief     = "Scale: no N\nRegister slice: lane=x step=all\nccx: role=build incident=none place=local\n"
)

var (
	testNow = time.Date(2026, 11, 3, 12, 0, 0, 0, time.UTC)
	dueSoon = testNow.Add(2 * time.Hour).Format(time.RFC3339)
)

type fakeBus struct {
	posts   []client.Draft
	verdict string
}

func (b *fakeBus) Post(_ context.Context, d client.Draft) (client.Record, error) {
	b.posts = append(b.posts, d)
	seq := int64(len(b.posts) * 10)
	return client.Record{Seq: seq, Num: fmt.Sprintf("#%d", seq), Lane: d.Lane, Text: d.Text}, nil
}

func (b *fakeBus) Tail(_ context.Context, q client.Query) ([]client.Record, error) {
	if len(q.Kinds) > 0 {
		return []client.Record{{Seq: 1, Lane: "desk-runner", Kind: "state", Topic: "orca", At: testNow, Text: "orca alive pass 3"}}, nil
	}
	_, after, _ := strings.Cut(b.posts[len(b.posts)-1].Text, " launch ")
	lane, _, _ := strings.Cut(after, " ")
	seq := int64(len(b.posts) * 10)
	return []client.Record{{Seq: seq + 1, Lane: "desk-runner", Text: fmt.Sprintf("%s R%d %s: dispatch ctx_1 terminal term_1", b.verdict, seq, lane)}}, nil
}

type harness struct {
	state      string
	transcript string
	log        string
	bus        *fakeBus
	send       orca.Poster
}

func writeFile(t *testing.T, path, text string) string {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func newHarness(t *testing.T) *harness {
	t.Helper()
	config := t.TempDir()
	state := filepath.Join(t.TempDir(), testDrive)
	entry, err := json.Marshal(map[string]string{"drive": testDrive, "cci_drive": testDrive, "state_dir": state, "checkout": state})
	if err != nil {
		t.Fatal(err)
	}
	writeFile(t, filepath.Join(config, "long-running", "drives", testDrive+".json"), string(entry))
	env := map[string]string{"CLAUDE_CONFIG_DIR": config, "HOME": config}
	bus := &fakeBus{verdict: "LAUNCHED"}
	corpus := t.TempDir()
	return &harness{
		state:      state,
		transcript: writeFile(t, filepath.Join(corpus, "68531111-afa1.jsonl"), "{}\n"),
		log:        writeFile(t, filepath.Join(corpus, "supervisor.log"), "line\n"),
		bus:        bus,
		send: orca.Poster{
			Bus: bus, Runner: &shelltest.Fake{}, Env: func(name string) string { return env[name] },
			Now: func() time.Time { return testNow }, Sleep: func(time.Duration) {}, Out: io.Discard, Drive: testDrive, Author: testLauncher,
		},
	}
}

func (h *harness) job() Job {
	return Job{
		Drive: testDrive, State: h.state, Binary: "/pack/bin/lr", Question: "where did it struggle?",
		Reader: testReader, Triage: testTriage, Launcher: testLauncher,
		Transcripts: []string{h.transcript}, Records: []string{"--lane root --since '2:13 AM'"}, Logs: []string{h.log},
	}
}

func (h *harness) postTexts() []string {
	texts := make([]string, len(h.bus.posts))
	for i, post := range h.bus.posts {
		texts[i] = post.Text
	}
	return texts
}

func TestTriageLaneKeepsTheReadersNumber(t *testing.T) {
	for reader, want := range map[string]string{
		"coordinator-struggles-1": "coordinator-struggles-triage-1",
		"logs-2-12":               "logs-2-triage-12",
		"bulk":                    "bulk-triage",
	} {
		if got := TriageLane(reader); got != want {
			t.Errorf("TriageLane(%q) = %q, want %q", reader, got, want)
		}
	}
}

func TestStartRefusesWhatCouldNotBeReadAndPostsNothing(t *testing.T) {
	cases := map[string]struct {
		change func(*Job)
		want   string
	}{
		"blank question":      {func(j *Job) { j.Question = "  " }, "question is blank"},
		"empty corpus":        {func(j *Job) { j.Transcripts, j.Records, j.Logs = nil, nil, nil }, "corpus is empty"},
		"no launcher":         {func(j *Job) { j.Launcher = "" }, "no launcher"},
		"bad lane name":       {func(j *Job) { j.Reader = "../root" }, "not lowercase"},
		"transcript dir":      {func(j *Job) { j.Transcripts = []string{filepath.Dir(j.Transcripts[0])} }, "not a file"},
		"transcript glob":     {func(j *Job) { j.Transcripts = []string{filepath.Dir(j.Transcripts[0]) + "/*.jsonl"} }, "no such file"},
		"transcript not json": {func(j *Job) { j.Transcripts = j.Logs }, "not a .jsonl"},
		"log dir":             {func(j *Job) { j.Logs = []string{filepath.Dir(j.Logs[0])} }, "not a file"},
		"missing contract":    {func(j *Job) { j.Contract = "/no/such/contract.md" }, "contract"},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			h := newHarness(t)
			job := h.job()
			tc.change(&job)
			err := job.Start(context.Background(), h.send)
			if err == nil || !strings.Contains(err.Error(), tc.want) {
				t.Fatalf("got %v, want a refusal naming %q", err, tc.want)
			}
			if len(h.bus.posts) != 0 {
				t.Fatalf("posted %v after a refusal", h.postTexts())
			}
		})
	}
}

func TestStartLaunchesOnlyTheSonnetReader(t *testing.T) {
	h := newHarness(t)
	job := h.job()
	job.Contract = writeFile(t, filepath.Join(t.TempDir(), "contract.md"), "the contract\n")
	if err := job.Start(context.Background(), h.send); err != nil {
		t.Fatal(err)
	}
	want := []string{"orca-desk: launch " + testReader + " sonnet xhigh brief=" + job.brief(testReader)}
	if got := h.postTexts(); !slices.Equal(got, want) {
		t.Fatalf("posted %q, want %q", got, want)
	}
	loaded, err := Load(job.JobPath())
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(loaded, job) {
		t.Fatalf("job read back as %+v, want %+v", loaded, job)
	}
}

func TestBriefsPassTheRunnersOwnChecks(t *testing.T) {
	h := newHarness(t)
	job := h.job()
	if err := job.Start(context.Background(), h.send); err != nil {
		t.Fatal(err)
	}
	for _, order := range []orca.Order{
		{Lane: job.Reader, Model: readerModel, Effort: readEffort, Brief: job.brief(job.Reader)},
		{Lane: job.Triage, Model: triageModel, Effort: readEffort, Brief: job.brief(job.Triage)},
	} {
		faults, err := h.send.Faults(context.Background(), order)
		if err != nil {
			t.Fatal(err)
		}
		if len(faults) > 0 {
			t.Errorf("the runner would refuse %s: %v", order.Lane, faults)
		}
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

func TestReaderBriefReadsEachCorpusKindThroughItsOwnTool(t *testing.T) {
	h := newHarness(t)
	job := h.job()
	if err := job.Start(context.Background(), h.send); err != nil {
		t.Fatal(err)
	}
	brief := readFile(t, job.brief(job.Reader))
	for _, want := range []string{
		"Transcript `" + h.transcript + "`",
		"cci tail --drive " + testDrive + " --lane root --since '2:13 AM' --oldest",
		"Log `" + h.log + "`",
		"Never search `~/.claude` recursively",
		job.Findings(),
		"/pack/bin/lr bulk triage " + job.JobPath(),
		"where did it struggle?",
		"cci watch --drive " + testDrive + " --lane desk-runner --kind state --topic orca",
	} {
		if !strings.Contains(brief, want) {
			t.Errorf("reader brief lacks %q:\n%s", want, brief)
		}
	}
}

func TestTriageBriefFilesToTheLauncherAndCarriesTheContract(t *testing.T) {
	h := newHarness(t)
	job := h.job()
	job.Contract = writeFile(t, filepath.Join(t.TempDir(), "contract.md"), "the contract\n")
	if err := job.Start(context.Background(), h.send); err != nil {
		t.Fatal(err)
	}
	brief := readFile(t, job.brief(job.Triage))
	for _, want := range []string{
		"You never launch a lane: " + testLauncher + " launches every one",
		"Read " + job.Contract + " in full first",
		"/pack/bin/lr explain \"<topic>\" --from " + job.OnePager() + " --config " + filepath.Join(h.state, "lr.json"),
		"/pack/bin/lr bulk file " + job.JobPath() + " --one-pager",
		job.FixList(),
	} {
		if !strings.Contains(brief, want) {
			t.Errorf("triage brief lacks %q:\n%s", want, brief)
		}
	}
}

func TestStartSurfacesARefusedLaunchAsExitOne(t *testing.T) {
	h := newHarness(t)
	h.bus.verdict = "LAUNCH-FAILED"
	job := h.job()
	err := job.Start(context.Background(), h.send)
	var exit *wait.ExitError
	if !errors.As(err, &exit) || exit.Code != 1 {
		t.Fatalf("got %v, want exit 1", err)
	}
}

func TestStartTriageRefusesFindingsThatCannotBeChecked(t *testing.T) {
	cases := map[string]struct {
		findings string
		want     string
	}{
		"uncited claim":      {"# Findings\n\n- The push failed three times.\n", "line 3 cites no record"},
		"cite in prose only": {"- The push failed, see 68531111-afa1.jsonl:40.\n", "line 1 cites no record"},
		"foreign file":       {"- The push failed. [other.jsonl:40]\n", "other.jsonl, which is not in the corpus"},
		"no claims":          {"# Findings\n\nNothing stood out.\n", "holds no claim"},
		"one bad of two":     {"- Fine. [#12]\n- Not fine.\n", "line 2 cites no record"},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			h := newHarness(t)
			job := h.job()
			writeFile(t, job.Findings(), tc.findings)
			err := job.StartTriage(context.Background(), h.send)
			if err == nil || !strings.Contains(err.Error(), tc.want) {
				t.Fatalf("got %v, want a refusal naming %q", err, tc.want)
			}
			if len(h.bus.posts) != 0 {
				t.Fatalf("posted %v after a refusal", h.postTexts())
			}
		})
	}
}

func TestStartTriageLaunchesOpusFromCitedFindings(t *testing.T) {
	h := newHarness(t)
	job := h.job()
	if err := job.Start(context.Background(), h.send); err != nil {
		t.Fatal(err)
	}
	h.bus.posts = nil
	writeFile(t, job.Findings(), strings.Join([]string{
		"# Findings",
		"## Refusals",
		"- A record. [#362766]",
		"- A replicated record and a second cite. [#2518@1cba2b34] [#12]",
		"- A transcript event. [68531111-afa1.jsonl:4120]",
		"- A transcript by whole path. [" + h.transcript + ":7]",
		"- A log line. [supervisor.log:88]",
		"",
	}, "\n"))
	if err := job.StartTriage(context.Background(), h.send); err != nil {
		t.Fatal(err)
	}
	want := []string{"orca-desk: launch " + testTriage + " opus xhigh brief=" + job.brief(testTriage)}
	if got := h.postTexts(); !slices.Equal(got, want) {
		t.Fatalf("posted %q, want %q", got, want)
	}
}

func (h *harness) fixLane(t *testing.T, lane string) FixLane {
	t.Helper()
	return FixLane{
		Lane: lane, Deliverable: "the hook reads the hold", Due: dueSoon, Model: "opus", Effort: "high",
		Brief: writeFile(t, filepath.Join(h.state, lane, briefFile), fixBrief),
	}
}

func writeList(t *testing.T, job Job, lanes []FixLane) {
	t.Helper()
	raw, err := json.Marshal(lanes)
	if err != nil {
		t.Fatal(err)
	}
	writeFile(t, job.FixList(), string(raw))
}

func TestFileRefusesAListTheLauncherCouldNotUse(t *testing.T) {
	cases := map[string]struct {
		change func(*testing.T, *Job, *FixLane) []FixLane
		want   string
	}{
		"no deliverable": {func(_ *testing.T, _ *Job, l *FixLane) []FixLane {
			l.Deliverable = " "
			return nil
		}, "has no deliverable"},
		"past due": {func(_ *testing.T, _ *Job, l *FixLane) []FixLane {
			l.Due = testNow.Add(-time.Minute).Format(time.RFC3339)
			return nil
		}, "has passed"},
		"due not a time": {func(_ *testing.T, _ *Job, l *FixLane) []FixLane {
			l.Due = "in two hours"
			return nil
		}, "not an RFC3339"},
		"relative brief": {func(_ *testing.T, _ *Job, l *FixLane) []FixLane {
			l.Brief = "fix-a/brief.md"
			return nil
		}, "not an absolute path"},
		"unknown model": {func(_ *testing.T, _ *Job, l *FixLane) []FixLane {
			l.Model = "gemini"
			return nil
		}, "not a model"},
		"brief the runner refuses": {func(t *testing.T, _ *Job, l *FixLane) []FixLane {
			writeFile(t, l.Brief, "Register slice: lane=fix-a step=all\nccx: place=local\n")
			return nil
		}, "`Scale:`"},
		"brief of another lane": {func(_ *testing.T, _ *Job, l *FixLane) []FixLane {
			l.Lane = "fix-b"
			return nil
		}, "is lane fix-a's"},
		"listed twice": {func(_ *testing.T, _ *Job, l *FixLane) []FixLane {
			return []FixLane{*l, *l}
		}, "listed twice"},
		"contract not named": {func(t *testing.T, j *Job, _ *FixLane) []FixLane {
			j.Contract = writeFile(t, filepath.Join(t.TempDir(), "contract.md"), "the contract\n")
			return nil
		}, "does not name the contract"},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			h := newHarness(t)
			job := h.job()
			lane := h.fixLane(t, "fix-a")
			lanes := tc.change(t, &job, &lane)
			if lanes == nil {
				lanes = []FixLane{lane}
			}
			writeList(t, job, lanes)
			err := job.File(context.Background(), h.send, testLink)
			if err == nil || !strings.Contains(err.Error(), tc.want) {
				t.Fatalf("got %v, want a refusal naming %q", err, tc.want)
			}
			if len(h.bus.posts) != 0 {
				t.Fatalf("posted %v after a refusal", h.postTexts())
			}
			if _, statErr := os.Stat(job.launchList()); !errors.Is(statErr, os.ErrNotExist) {
				t.Fatalf("wrote a launch list after a refusal: %v", statErr)
			}
		})
	}
}

func TestFileRefusesAOnePagerThatIsNotALink(t *testing.T) {
	for _, link := range []string{"", "one-pager.md", "/tmp/one-pager.md", "ftp://mac/one"} {
		h := newHarness(t)
		job := h.job()
		writeList(t, job, []FixLane{h.fixLane(t, "fix-a")})
		err := job.File(context.Background(), h.send, link)
		if err == nil || !strings.Contains(err.Error(), "is not a link") {
			t.Errorf("link %q: got %v, want a refusal", link, err)
		}
	}
}

func TestFileHandsTheListToTheLauncherAndLaunchesNothing(t *testing.T) {
	h := newHarness(t)
	job := h.job()
	lanes := []FixLane{h.fixLane(t, "fix-a"), h.fixLane(t, "fix-b")}
	lanes[1].Due = testNow.Add(5 * time.Hour).Format(time.RFC3339)
	writeList(t, job, lanes)
	if err := job.File(context.Background(), h.send, testLink); err != nil {
		t.Fatal(err)
	}
	if len(h.bus.posts) != 1 {
		t.Fatalf("posted %q, want one record", h.postTexts())
	}
	post := h.bus.posts[0]
	if post.Lane != testTriage || post.Kind != "done" || !slices.Equal(post.To, []string{testLauncher}) {
		t.Fatalf("posted %+v, want a done from %s to %s", post, testTriage, testLauncher)
	}
	if strings.Contains(post.Text, " launch fix-") || slices.Contains(post.To, "orca-desk") {
		t.Fatalf("the triage launched a lane itself: %+v", post)
	}
	if !slices.Equal(post.LaneRefs, []string{"fix-a", "fix-b"}) {
		t.Fatalf("lane refs %v", post.LaneRefs)
	}
	wantDues := map[string]string{"due:fix-a": lanes[0].Due, "due:fix-b": lanes[1].Due}
	if !reflect.DeepEqual(post.Attrs, wantDues) {
		t.Fatalf("attrs %v, want %v", post.Attrs, wantDues)
	}
	if clock := regexp.MustCompile(`\b\d{1,2}:\d{2}\b`).FindString(post.Text); clock != "" {
		t.Fatalf("record text types the time %q: %s", clock, post.Text)
	}
	for _, want := range []string{"fix-a, fix-b", job.Findings(), testLink, job.launchList()} {
		if !strings.Contains(post.Text, want) {
			t.Errorf("record text lacks %q: %s", want, post.Text)
		}
	}
	list := readFile(t, post.Path)
	command := "lr launch lane fix-a opus high " + lanes[0].Brief + " --drive " + testDrive + " --lane " + testLauncher
	if !strings.Contains(list, command) {
		t.Fatalf("launch list lacks %q:\n%s", command, list)
	}
	if strings.Contains(list, lanes[0].Due) {
		t.Fatalf("the record's body types a deadline, which only a typed attr may carry:\n%s", list)
	}
}

func TestFileAcceptsANamedContract(t *testing.T) {
	h := newHarness(t)
	job := h.job()
	job.Contract = writeFile(t, filepath.Join(t.TempDir(), "contract.md"), "the contract\n")
	lane := h.fixLane(t, "fix-a")
	writeFile(t, lane.Brief, "Read "+job.Contract+" in full first; it binds this lane.\n\n"+fixBrief)
	writeList(t, job, []FixLane{lane})
	if err := job.File(context.Background(), h.send, testLink); err != nil {
		t.Fatal(err)
	}
}

func TestFileWithNoLanesSaysSo(t *testing.T) {
	h := newHarness(t)
	job := h.job()
	writeList(t, job, []FixLane{})
	if err := job.File(context.Background(), h.send, testLink); err != nil {
		t.Fatal(err)
	}
	post := h.bus.posts[0]
	if !strings.Contains(post.Text, "no fix lanes") || len(post.LaneRefs) != 0 {
		t.Fatalf("posted %+v, want a done naming no lanes", post)
	}
}
