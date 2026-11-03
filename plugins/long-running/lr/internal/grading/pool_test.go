package grading

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

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/grading/grader"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	testDrive = "d"
	older     = "a269784e49009c5aa92b2341e08d6945f7ff8e95"
	pull      = repo + "#45489"
	jobDir    = "45489-e718a7434fa0"
)

type spawned struct {
	argv []string
	env  map[string]string
	log  string
}

type poolShell struct {
	running map[int]string
	view    string
	docs    map[string]string
	card    string
	calls   [][]string
	spawns  []spawned
}

func (f *poolShell) Run(_ context.Context, command shell.Command) (shell.Result, error) {
	f.calls = append(f.calls, command.Argv)
	switch command.Argv[0] {
	case "ps":
		var lines strings.Builder
		for pid, line := range f.running {
			fmt.Fprintf(&lines, "%6d %s\n", pid, line)
		}
		return shell.Result{Stdout: lines.String()}, nil
	case "ccx":
		return shell.Result{Stdout: f.view}, nil
	case "ccn":
		return shell.Result{Stdout: f.docs[command.Argv[5]]}, nil
	case "cc-present":
		return shell.Result{Stdout: f.card}, nil
	case "codex-ask":
		return shell.Result{}, nil
	}
	return shell.Result{}, fmt.Errorf("unexpected call %v", command.Argv)
}

func (f *poolShell) spawn(_ context.Context, argv []string, env map[string]string, log string) (int, error) {
	f.spawns = append(f.spawns, spawned{argv, env, log})
	pid := 4000 + len(f.spawns)
	f.running[pid] = strings.Join(argv, " ")
	return pid, nil
}

func (f *poolShell) exitAll() {
	f.running = map[int]string{}
}

func (f *poolShell) count(tool string) int {
	total := 0
	for _, call := range f.calls {
		if call[0] == tool {
			total++
		}
	}
	return total
}

type poolFixture struct {
	pool  *JobPool
	shell *poolShell
	store *store.Store
	now   time.Time
}

func newPool(t *testing.T, sections map[string]any) *poolFixture {
	t.Helper()
	t.Setenv("CCI_HOME", t.TempDir())
	return poolAt(t, t.TempDir(), sections)
}

func poolAt(t *testing.T, root string, sections map[string]any) *poolFixture {
	t.Helper()
	raw := make(map[string]json.RawMessage, len(sections))
	for name, value := range sections {
		data, err := json.Marshal(value)
		if err != nil {
			t.Fatal(err)
		}
		raw[name] = data
	}
	f := &poolFixture{
		shell: &poolShell{running: map[int]string{}, view: `{"title":"a change","body":"","headRefName":"lane-7"}`, docs: map[string]string{}},
		store: memory(t),
		now:   t0,
	}
	runner := config.Runner{Source: filepath.Join(root, "runner.json"), Drive: testDrive, Orca: config.RunnerOrca{Briefs: config.RunnerBriefs{Repo: "/briefs"}}, Sections: raw}
	pool, err := NewJobPool(runner, PoolDeps{
		Runner:  f.shell,
		Spawn:   f.shell.spawn,
		Workers: f.store.Workers,
		Cursors: f.store,
		Ledger:  f.store.LedgerRows,
		Picks:   f.store.CheckedRules,
		Now:     func() time.Time { return f.now },
		Log:     slog.New(slog.NewTextHandler(io.Discard, nil)),
		Self:    "/plugin/bin/lr",
		Scripts: "/plugin/scripts",
		Config:  t.TempDir(),
	})
	if err != nil {
		t.Fatal(err)
	}
	f.pool = pool
	return f
}

func (f *poolFixture) dispatch(t *testing.T, grading string) (string, error) {
	t.Helper()
	return f.pool.Dispatch(context.Background(), grading, facts(head))
}

func (f *poolFixture) started(t *testing.T, grading string) string {
	t.Helper()
	name, err := f.dispatch(t, grading)
	if err != nil {
		t.Fatal(err)
	}
	return name
}

func (f *poolFixture) hold(t *testing.T, names ...string) {
	t.Helper()
	for _, name := range names {
		if err := f.store.Heartbeat(store.Worker{Name: name, Generation: 1, Heartbeat: f.now}); err != nil {
			t.Fatal(err)
		}
	}
}

func (f *poolFixture) finderDir(base string) string {
	return filepath.Join(f.pool.root, grader.FinderLane, base)
}

func writeResult(t *testing.T, directory, result string, at time.Time) {
	t.Helper()
	path := filepath.Join(directory, grader.ResultFile)
	if err := os.WriteFile(path, []byte(result), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Chtimes(path, at, at); err != nil {
		t.Fatal(err)
	}
}

func post(t *testing.T, draft client.Draft) int64 {
	t.Helper()
	draft.Drive = testDrive
	stored, err := client.Post(context.Background(), draft)
	if err != nil {
		t.Fatal(err)
	}
	return stored.Seq
}

func readJob[T any](t *testing.T, directory string) T {
	t.Helper()
	data, err := os.ReadFile(filepath.Join(directory, grader.JobFile))
	if err != nil {
		t.Fatal(err)
	}
	var spec T
	if err := json.Unmarshal(data, &spec); err != nil {
		t.Fatal(err)
	}
	return spec
}

func sameStrings(t *testing.T, got, want []string) {
	t.Helper()
	if !slices.Equal(got, want) {
		t.Errorf("got %q\nwant %q", got, want)
	}
}

func TestAFinderJobCarriesTheHistoryRulingsAndHeadsAPassReads(t *testing.T) {
	f := newPool(t, map[string]any{"finder": map[string]any{"sprite": "dar-1", "to": []string{"board"}, "checkout": "/srv/checkout", "fast": false}})
	post(t, client.Draft{Lane: "lane-7", Kind: kindHead, Text: "#45489 " + older, PRs: []string{pull}})
	major := post(t, client.Draft{Lane: grader.FinderLane, Kind: kindReview, Text: "finder: major " + older[:7] + " 1 open", PRs: []string{pull}})
	reply := post(t, client.Draft{Lane: "lane-7", Kind: kindReview, Text: "the finder major is a false alarm", PRs: []string{pull}})
	decide := post(t, client.Draft{Lane: rootLane, Kind: kindDecide, Text: "that one is wrong, moving on", Re: major, PRs: []string{pull}})
	post(t, client.Draft{Lane: "lane-7", Kind: kindHead, Text: "#45489 " + head + " (was " + older + ")", PRs: []string{pull}})

	name := f.started(t, GraderFinder)

	if want := "dar-1/" + grader.FinderLane + "/" + jobDir; name != want {
		t.Fatalf("worker = %q, want %q", name, want)
	}
	spec := readJob[grader.FinderJob](t, f.finderDir(jobDir))
	if spec.Since != older || spec.Previous != older || spec.Prior == nil || spec.Prior.Seq != major || spec.Prior.Head != older[:7] {
		t.Errorf("since %q previous %q prior %+v", spec.Since, spec.Previous, spec.Prior)
	}
	if len(spec.History) != 2 || spec.History[0].Seq != major || spec.History[1].Seq != reply {
		t.Errorf("history = %+v", spec.History)
	}
	if len(spec.Ruled) != 1 || spec.Ruled[0].Seq != decide {
		t.Errorf("ruled = %+v", spec.Ruled)
	}
	if got := spec.Settled[strconv.FormatInt(major, 10)]; got != decide {
		t.Errorf("settled = %v, want %d by %d", spec.Settled, major, decide)
	}
	sameStrings(t, spec.To, []string{runnerLane, "lane-7", "board"})
	if spec.Lane != "lane-7" || spec.Checkout != "/srv/checkout" || spec.Model != defaultModel || spec.Fast || spec.Sprite != "dar-1" || spec.Full {
		t.Errorf("job = %+v", spec)
	}
}

func TestAHeadAFinderRecordAlreadyGradedStartsNoPass(t *testing.T) {
	f := newPool(t, map[string]any{"finder": map[string]any{}})
	post(t, client.Draft{Lane: grader.FinderLane + "-2", Kind: kindReview, Text: "finder: ok " + head[:7] + " sol finder", PRs: []string{pull}})

	if _, err := f.dispatch(t, GraderFinder); !errors.Is(err, ErrAnswered) {
		t.Fatalf("err = %v, want ErrAnswered", err)
	}
	if len(f.shell.spawns) != 0 || exists(f.finderDir(jobDir)) {
		t.Fatalf("spawns = %v", f.shell.spawns)
	}
}

func TestAFidelityJobCarriesGradesOldAppsRulingsContractAndSources(t *testing.T) {
	f := newPool(t, map[string]any{
		"landing":  map[string]any{"checkout": "/srv/main", "fidelity_lane": "fidelity-desk-4", "port_phrase": "plain port"},
		"fidelity": map[string]any{"baseline": "base-1", "sources": map[string]any{"docs": map[string]any{"lane-*": []string{"abc123456"}, "other": []string{"unread"}}, "records": map[string]any{"*": []int64{}}}},
	})
	f.shell.docs["abc123456"] = `{"id":"abc123456789","title":"Grounds","body":"  the grounds  "}`
	whole := filepath.Join(t.TempDir(), "grade.md")
	if err := os.WriteFile(whole, []byte("fidelity: ok "+older[:7]+" reads\nold-app: unchanged "+older[:7]+" go only\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	grade := post(t, client.Draft{Lane: "fidelity-desk-4", Kind: kindReview, Text: "fidelity: ok " + older[:7] + " reads", PRs: []string{pull}, Path: whole})
	wrong := post(t, client.Draft{Lane: rootLane, Kind: kindDecide, Text: "class wrong, this is a port", Re: grade, PRs: []string{pull}})
	post(t, client.Draft{Lane: "lane-7", Kind: kindContract, Text: "contract for #45489: builds the resubscribe", PRs: []string{pull}})

	f.started(t, GraderFidelity)

	directory := filepath.Join(f.pool.root, grader.FidelityLane, jobDir)
	spec := readJob[grader.FidelityJob](t, directory)
	sameStrings(t, spec.Lanes, []string{"fidelity-desk-4", grader.FidelityLane})
	sameStrings(t, spec.Readers, []string{"fidelity-desk-4"})
	if len(spec.Grades) != 1 || spec.Grades[0].Seq != grade || !strings.Contains(spec.Grades[0].Text, "old-app: unchanged") {
		t.Errorf("grades = %+v", spec.Grades)
	}
	if len(spec.OldApps) != 1 || spec.OldApps[0].Head != older[:7] || !strings.HasPrefix(spec.OldApps[0].Line, "old-app: unchanged") {
		t.Errorf("old apps = %+v", spec.OldApps)
	}
	if !slices.Equal(spec.Corrected, []int64{grade}) || len(spec.Rulings) != 1 || spec.Rulings[0].Seq != wrong {
		t.Errorf("corrected %v rulings %+v", spec.Corrected, spec.Rulings)
	}
	if spec.Settled[strconv.FormatInt(grade, 10)] != wrong {
		t.Errorf("settled = %v", spec.Settled)
	}
	if !strings.Contains(spec.Contract, "builds the resubscribe") {
		t.Errorf("contract = %q", spec.Contract)
	}
	if spec.Exempt["port"] != "plain port" || spec.Exempt["test-only"] != testOnlyPhrase || !slices.Equal(spec.ExemptGrades, []string{"plain port", testOnlyPhrase}) {
		t.Errorf("exempt %v %v", spec.Exempt, spec.ExemptGrades)
	}
	if spec.Baseline != "base-1" || spec.Checkout != "/srv/main" {
		t.Errorf("baseline %q checkout %q", spec.Baseline, spec.Checkout)
	}
	if spec.Sources != filepath.Join(directory, sourcesFile) {
		t.Errorf("sources = %q", spec.Sources)
	}
	sources, err := os.ReadFile(spec.Sources)
	if err != nil {
		t.Fatal(err)
	}
	if want := "## cc-notes doc abc1234: Grounds\n\nthe grounds"; string(sources) != want {
		t.Errorf("sources:\n%s\nwant:\n%s", sources, want)
	}
}

func TestAHeadAFidelityGradeOrAReaderHandoffAlreadyNamesStartsNoPass(t *testing.T) {
	for name, draft := range map[string]client.Draft{
		"grade":  {Lane: grader.FidelityLane, Kind: kindReview, Text: "fidelity: ok " + head[:7] + " reads", PRs: []string{pull}},
		"reader": {Lane: grader.FidelityLane, Kind: kindReview, Text: "fidelity: reader " + head[:7] + " goes to a desk", PRs: []string{pull}},
	} {
		t.Run(name, func(t *testing.T) {
			f := newPool(t, map[string]any{"fidelity": map[string]any{}})
			post(t, draft)

			if _, err := f.dispatch(t, GraderFidelity); !errors.Is(err, ErrAnswered) {
				t.Fatalf("err = %v, want ErrAnswered", err)
			}
		})
	}
}

func TestABranchOfABoardOptionGroundsItsGradeOnTheCardAndTheRootsSession(t *testing.T) {
	f := newPool(t, map[string]any{"fidelity": map[string]any{}})
	registry := filepath.Join(f.pool.Config, "long-running", "drives")
	if err := os.MkdirAll(registry, 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(registry, "one.json"), []byte(`{"cci_drive":"d","sessions":["first","newest"]}`), 0o600); err != nil {
		t.Fatal(err)
	}
	f.shell.view = `{"title":"an option","body":"","headRefName":"options/card-night-design-2"}`
	f.shell.card = "the card\n"

	f.started(t, GraderFidelity)

	if !slices.ContainsFunc(f.shell.calls, func(call []string) bool {
		return slices.Equal(call, []string{"cc-present", "block", "show", "card-night-design", "--session", "newest"})
	}) {
		t.Errorf("calls = %v", f.shell.calls)
	}
	sources, err := os.ReadFile(filepath.Join(f.pool.root, grader.FidelityLane, jobDir, sourcesFile))
	if err != nil {
		t.Fatal(err)
	}
	if want := "## Board card card-night-design, whose option 2 this branch builds\n\nthe card"; string(sources) != want {
		t.Errorf("sources = %q, want %q", sources, want)
	}
}

func TestAProofPRStartsNoPass(t *testing.T) {
	for name, view := range map[string]string{
		"title": `{"title":"proof: the red build","body":"","headRefName":"b"}`,
		"body":  `{"title":"a change","body":"context\nproof PR that never merges","headRefName":"b"}`,
		"names": `{"title":"a change","body":"Proof PR for #47110. Its build reads red.","headRefName":"b"}`,
	} {
		t.Run(name, func(t *testing.T) {
			f := newPool(t, map[string]any{"finder": map[string]any{}, "fidelity": map[string]any{}})
			f.shell.view = view

			for _, grading := range []string{GraderFinder, GraderFidelity} {
				if _, err := f.dispatch(t, grading); !errors.Is(err, ErrAnswered) {
					t.Fatalf("err = %v, want ErrAnswered", err)
				}
			}
			if len(f.shell.spawns) != 0 {
				t.Fatalf("spawns = %v", f.shell.spawns)
			}
			notes, err := client.Tail(context.Background(), client.Query{Drive: testDrive, Kinds: []string{kindNote}})
			if err != nil {
				t.Fatal(err)
			}
			if len(notes) != 1 || !strings.HasPrefix(notes[0].Text, "GRADER-SKIPPED #45489 ") || !slices.Contains(notes[0].To, "lane-7") {
				t.Fatalf("notes = %+v, want one telling the lane why", notes)
			}
		})
	}
}

func TestABodyLineCitingAPRsProofPRIsNoProofPR(t *testing.T) {
	f := newPool(t, map[string]any{"finder": map[string]any{}})
	f.shell.view = `{"title":"a real fix","body":"## Risk\n\nProof PR, red before this fix: linked in a comment once CI grades it.","headRefName":"b"}`

	f.started(t, GraderFinder)

	if len(f.shell.spawns) != 1 {
		t.Fatalf("spawns = %v", f.shell.spawns)
	}
}

func TestADeskThatListsNoSpriteRunsThePassHereThroughTheBinary(t *testing.T) {
	f := newPool(t, map[string]any{"finder": map[string]any{}})

	name := f.started(t, GraderFinder)

	if want := localBox + "/" + grader.FinderLane + "/" + jobDir; name != want {
		t.Fatalf("worker = %q, want %q", name, want)
	}
	directory := f.finderDir(jobDir)
	sameStrings(t, f.shell.spawns[0].argv, []string{"/plugin/bin/lr", "grade", GraderFinder, directory})
	if f.shell.spawns[0].log != filepath.Join(directory, logFile) {
		t.Errorf("log = %q", f.shell.spawns[0].log)
	}
	said, err := os.ReadFile(filepath.Join(directory, pidFile))
	if err != nil || string(said) != "4001" {
		t.Errorf("pid = %q, %v", said, err)
	}
}

func TestASpritePassRunsThroughSpriteJobWithTheDesksKeyCommands(t *testing.T) {
	f := newPool(t, map[string]any{"fidelity": map[string]any{"sprite": []string{"dar-1"}, "sprite_keys": map[string]any{"GH_TOKEN": []string{"gh", "auth", "token"}}}})

	f.started(t, GraderFidelity)

	directory := filepath.Join(f.pool.root, grader.FidelityLane, jobDir)
	sameStrings(t, f.shell.spawns[0].argv, []string{"/plugin/scripts/sprite-job.sh", "dar-1", "/plugin/scripts/fidelity.py", directory})
	if want := `{"GH_TOKEN":["gh","auth","token"]}`; f.shell.spawns[0].env["SPRITE_JOB_KEYS"] != want {
		t.Errorf("env = %v", f.shell.spawns[0].env)
	}
}

func TestEachPassGoesToTheSpriteRunningFewestPassesFirstListedOnATie(t *testing.T) {
	f := newPool(t, map[string]any{"finder": map[string]any{"sprite": []string{"dar-1", "dar-2", "dar-3"}}})
	if err := f.pool.Scale(24, f.now); err != nil {
		t.Fatal(err)
	}
	f.hold(t, "dar-1/fidelity-pipeline/1-aaaaaaaaaaaa", "dar-2/finder-desk/2-bbbbbbbbbbbb", "dar-3/finder-desk/3-cccccccccccc", "dar-3/finder-desk/4-dddddddddddd", "lane-without-a-box")
	sprite, err := f.pool.place(f.pool.desks[GraderFinder], mustWorkers(t, f), false)
	if err != nil || sprite != "dar-1" {
		t.Fatalf("sprite = %q, %v, want the first listed of the two tied", sprite, err)
	}
	f.hold(t, "dar-1/fidelity-pipeline/5-eeeeeeeeeeee")
	sprite, err = f.pool.place(f.pool.desks[GraderFinder], mustWorkers(t, f), false)
	if err != nil || sprite != "dar-2" {
		t.Fatalf("sprite = %q, %v, want dar-2", sprite, err)
	}
}

func mustWorkers(t *testing.T, f *poolFixture) []store.Worker {
	t.Helper()
	workers, err := f.store.Workers()
	if err != nil {
		t.Fatal(err)
	}
	return workers
}

func TestASpriteWhosePassLeftNoResultTakesNoPassWhileAnotherIsSound(t *testing.T) {
	f := newPool(t, map[string]any{"finder": map[string]any{"sprite": []string{"dar-1", "dar-2"}}})
	if err := f.pool.Scale(16, f.now); err != nil {
		t.Fatal(err)
	}
	name := f.started(t, GraderFinder)
	if !strings.HasPrefix(name, "dar-1/") {
		t.Fatalf("worker = %q", name)
	}
	f.shell.exitAll()

	if _, err := f.pool.Settle(context.Background(), []string{name}); err != nil {
		t.Fatal(err)
	}

	again := f.started(t, GraderFinder)
	if !strings.HasPrefix(again, "dar-2/") {
		t.Fatalf("retry = %q, want dar-2 while dar-1 fails", again)
	}
	f.now = f.now.Add(spriteFailingFor)
	sprite, err := f.pool.place(f.pool.desks[GraderFinder], nil, false)
	if err != nil || sprite != "dar-1" {
		t.Fatalf("sprite = %q, %v, want dar-1 once its failure ages out", sprite, err)
	}
}

func TestEverySpriteFailingStillTakesThePassOnTheFirstListed(t *testing.T) {
	f := newPool(t, map[string]any{"finder": map[string]any{"sprite": []string{"dar-1", "dar-2"}}})
	for _, sprite := range []string{"dar-1", "dar-2"} {
		if err := f.pool.stamp(failuresDir, sprite); err != nil {
			t.Fatal(err)
		}
	}

	sprite, err := f.pool.place(f.pool.desks[GraderFinder], nil, false)
	if err != nil || sprite != "dar-1" {
		t.Fatalf("sprite = %q, %v", sprite, err)
	}
}

func TestNoPassStartsOnASpriteAtItsCapOfFinderAndFidelityTogether(t *testing.T) {
	f := newPool(t, map[string]any{"sprite_cap": 1, "finder": map[string]any{"sprite": "dar-1"}, "fidelity": map[string]any{"sprite": "dar-1"}})
	f.started(t, GraderFinder)
	f.hold(t, "dar-1/"+grader.FinderLane+"/"+jobDir)

	if _, err := f.dispatch(t, GraderFidelity); !errors.Is(err, ErrNoCapacity) {
		t.Fatalf("err = %v, want ErrNoCapacity", err)
	}
}

func TestADeskAtItsConcurrencyStartsNoPassAndTheOtherDeskStillDoes(t *testing.T) {
	f := newPool(t, map[string]any{"finder": map[string]any{"concurrent": 1}, "fidelity": map[string]any{}})
	f.hold(t, localBox+"/"+grader.FinderLane+"/1-aaaaaaaaaaaa")

	if _, err := f.dispatch(t, GraderFinder); !errors.Is(err, ErrNoCapacity) {
		t.Fatalf("err = %v, want ErrNoCapacity", err)
	}
	f.started(t, GraderFidelity)
}

func TestADeskRunnerJsonLeavesOutStartsNoPass(t *testing.T) {
	f := newPool(t, map[string]any{"finder": map[string]any{}})

	if _, err := f.dispatch(t, GraderFidelity); !errors.Is(err, ErrNoCapacity) {
		t.Fatalf("err = %v, want ErrNoCapacity", err)
	}
}

func TestTheQueueDecidesHowManyListedSpritesTakePassesAcrossPoolRestarts(t *testing.T) {
	sections := map[string]any{"sprite_cap": 2, "finder": map[string]any{"sprite": []string{"dar-1", "dar-2", "dar-3"}}}
	f := newPool(t, sections)
	busy := []store.Worker{{Name: "dar-1/" + grader.FinderLane + "/1-aaaaaaaaaaaa"}}

	if err := f.pool.Scale(1, f.now); err != nil {
		t.Fatal(err)
	}
	if sprite, err := f.pool.place(f.pool.desks[GraderFinder], busy, false); err != nil || sprite != "dar-1" {
		t.Fatalf("small queue: sprite = %q, %v, want one Sprite taking passes", sprite, err)
	}

	if err := f.pool.Scale(4, f.now); err != nil {
		t.Fatal(err)
	}
	if sprite, err := f.pool.place(f.pool.desks[GraderFinder], busy, false); err != nil || sprite != "dar-2" {
		t.Fatalf("burst: sprite = %q, %v, want dar-2 in the same pass", sprite, err)
	}

	restarted := poolAt(t, f.pool.root, sections)
	if err := restarted.pool.Scale(0, f.now.Add(time.Minute)); err != nil {
		t.Fatal(err)
	}
	if sprite, err := restarted.pool.place(restarted.pool.desks[GraderFinder], busy, false); err != nil || sprite != "dar-2" {
		t.Fatalf("lull: sprite = %q, %v, want the size kept", sprite, err)
	}

	if err := restarted.pool.Scale(0, f.now.Add(time.Minute+time.Hour)); err != nil {
		t.Fatal(err)
	}
	if sprite, err := restarted.pool.place(restarted.pool.desks[GraderFinder], busy, false); err != nil || sprite != "dar-1" {
		t.Fatalf("after an idle hour: sprite = %q, %v, want one Sprite again", sprite, err)
	}
}

func TestARunningJobIsNeverStartedTwice(t *testing.T) {
	f := newPool(t, map[string]any{"finder": map[string]any{"sprite": "dar-1"}})

	first := f.started(t, GraderFinder)
	second := f.started(t, GraderFinder)

	if first != second || len(f.shell.spawns) != 1 {
		t.Fatalf("first %q second %q spawns %d", first, second, len(f.shell.spawns))
	}
}

func TestAJobThatEndedWithNoResultRetriesInAFreshDirectoryUpToItsLastAttempt(t *testing.T) {
	f := newPool(t, map[string]any{"finder": map[string]any{}})

	bases := make([]string, 0, maxAttempts)
	for range maxAttempts {
		bases = append(bases, refOf(f.started(t, GraderFinder)).base)
		f.shell.exitAll()
	}
	sameStrings(t, bases, []string{jobDir, jobDir + "-2", jobDir + "-3"})

	if _, err := f.dispatch(t, GraderFinder); !errors.Is(err, ErrAnswered) {
		t.Fatalf("err = %v, want ErrAnswered past the last attempt", err)
	}
	if len(f.shell.spawns) != maxAttempts {
		t.Fatalf("spawns = %d", len(f.shell.spawns))
	}
}

func TestAJobThatSettledStartsNoSecondPass(t *testing.T) {
	f := newPool(t, map[string]any{"finder": map[string]any{}})
	f.started(t, GraderFinder)
	f.shell.exitAll()
	writeResult(t, f.finderDir(jobDir), `{"verdict":"ok"}`, f.now)

	if _, err := f.dispatch(t, GraderFinder); !errors.Is(err, ErrAnswered) {
		t.Fatalf("err = %v, want ErrAnswered", err)
	}
}

func TestAToolingFailureWaitsOutItsHoldAndAWrapperThatDiedRetriesAtOnce(t *testing.T) {
	f := newPool(t, map[string]any{"finder": map[string]any{}})
	f.started(t, GraderFinder)
	f.shell.exitAll()
	writeResult(t, f.finderDir(jobDir), `{"error":"git failed"}`, f.now)

	if _, err := f.dispatch(t, GraderFinder); !errors.Is(err, ErrAnswered) {
		t.Fatalf("inside the hold: err = %v, want ErrAnswered", err)
	}
	f.now = f.now.Add(failedHold)
	if name := f.started(t, GraderFinder); refOf(name).base != jobDir+"-2" {
		t.Fatalf("past the hold: worker = %q", name)
	}

	f.shell.exitAll()
	writeResult(t, f.finderDir(jobDir+"-2"), `{"error":"the Sprite restarted","no_result":true}`, f.now)
	if name := f.started(t, GraderFinder); refOf(name).base != jobDir+"-3" {
		t.Fatalf("a lost job: worker = %q", name)
	}
}

func TestAPidTheSystemHandedToAnotherProcessReadsAsAJobThatEnded(t *testing.T) {
	f := newPool(t, map[string]any{"finder": map[string]any{}})
	f.started(t, GraderFinder)
	f.shell.running[4001] = "/usr/sbin/something-else"

	if name := f.started(t, GraderFinder); refOf(name).base != jobDir+"-2" {
		t.Fatalf("worker = %q", name)
	}
}

func TestSettleReadsOneProcessListAtOneWorkerAndAtAThousand(t *testing.T) {
	for _, workers := range []int{1, 1000} {
		f := newPool(t, map[string]any{"finder": map[string]any{}})
		names := make([]string, 0, workers)
		for index := range workers {
			names = append(names, fmt.Sprintf("%s/%s/%d-aaaaaaaaaaaa", localBox, grader.FinderLane, index))
		}

		if _, err := f.pool.Settle(context.Background(), names); err != nil {
			t.Fatal(err)
		}

		if got := f.shell.count("ps"); got != 1 {
			t.Fatalf("at %d workers ps ran %d times", workers, got)
		}
	}
}

func TestAJobWithNoResultPastItsTimeoutFailsAndFreesItsSlot(t *testing.T) {
	f := newPool(t, map[string]any{"finder": map[string]any{"timeout_minutes": 30}})
	name := f.started(t, GraderFinder)
	directory := f.finderDir(jobDir)
	startedAgo := func(ago time.Duration) map[string]bool {
		t.Helper()
		at := f.now.Add(-ago)
		if err := os.Chtimes(filepath.Join(directory, pidFile), at, at); err != nil {
			t.Fatal(err)
		}
		live, err := f.pool.Settle(context.Background(), []string{name})
		if err != nil {
			t.Fatal(err)
		}
		return live
	}

	if !startedAgo(29 * time.Minute)[name] {
		t.Fatal("a job inside its timeout was settled")
	}
	if startedAgo(31 * time.Minute)[name] {
		t.Fatal("a job past its timeout stayed live")
	}

	result, err := os.ReadFile(filepath.Join(directory, grader.ResultFile))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(result), "no result 31 min after it started") {
		t.Fatalf("result = %s", result)
	}
}

func TestSettleKeepsARunningJobAndPostsASettledJobsRecordsAgain(t *testing.T) {
	f := newPool(t, map[string]any{"finder": map[string]any{}})
	name := f.started(t, GraderFinder)

	live, err := f.pool.Settle(context.Background(), []string{name})
	if err != nil {
		t.Fatal(err)
	}
	if !live[name] {
		t.Fatalf("live = %v", live)
	}

	f.shell.exitAll()
	directory := f.finderDir(jobDir)
	writeResult(t, directory, `{"verdict":"ok"}`, f.now)
	argv, err := json.Marshal([]string{"cci", "post", "--drive", testDrive, "--lane", grader.FinderLane, "--kind", kindReview, "--pr", pull, "--id", "finder:one", "--to", "lane-7,desk-runner", "--text", "finder: ok " + head[:7], "--json"})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(directory, grader.PostsFile), append(argv, '\n'), 0o600); err != nil {
		t.Fatal(err)
	}

	for range 2 {
		live, err = f.pool.Settle(context.Background(), []string{name})
		if err != nil {
			t.Fatal(err)
		}
		if live[name] {
			t.Fatalf("live = %v", live)
		}
	}

	records, err := client.Tail(context.Background(), client.Query{Drive: testDrive, Kinds: []string{kindReview}, PR: pull})
	if err != nil {
		t.Fatal(err)
	}
	if len(records) != 1 || records[0].ID != "finder:one" || !slices.Contains(records[0].To, "lane-7") {
		t.Fatalf("records = %+v, want one by its id", records)
	}
}

func TestANewerHeadCancelsARunningFinderOfThatPRAndNoOther(t *testing.T) {
	f := newPool(t, map[string]any{"finder": map[string]any{}})
	stale := localBox + "/" + grader.FinderLane + "/45489-" + older[:headChars]
	otherPR := localBox + "/" + grader.FinderLane + "/45490-" + older[:headChars]
	fidelity := localBox + "/" + grader.FidelityLane + "/45489-" + older[:headChars]
	f.hold(t, stale, otherPR, fidelity)
	run := filepath.Join(f.finderDir("45489-"+older[:headChars]), "codex-1")
	for _, directory := range []string{run, f.finderDir("45490-" + older[:headChars]), filepath.Join(f.pool.root, grader.FidelityLane, "45489-"+older[:headChars])} {
		if err := os.MkdirAll(directory, 0o750); err != nil {
			t.Fatal(err)
		}
	}

	f.started(t, GraderFinder)

	said, err := os.ReadFile(filepath.Join(f.finderDir("45489-"+older[:headChars]), grader.CancelledFile))
	if err != nil || string(said) != head+"\n" {
		t.Fatalf("cancelled = %q, %v", said, err)
	}
	if !slices.ContainsFunc(f.shell.calls, func(call []string) bool { return slices.Equal(call, []string{"codex-ask", "--cancel", run}) }) {
		t.Fatalf("calls = %v", f.shell.calls)
	}
	if exists(filepath.Join(f.finderDir("45490-"+older[:headChars]), grader.CancelledFile)) || exists(filepath.Join(f.pool.root, grader.FidelityLane, "45489-"+older[:headChars], grader.CancelledFile)) {
		t.Fatal("cancelled a pass of another PR or another desk")
	}
}

func TestActDrivesTheJobPoolFromADispatchToARelease(t *testing.T) {
	f := newPool(t, map[string]any{"finder": map[string]any{"sprite": "dar-1"}, "fidelity": map[string]any{"sprite": "dar-1"}})
	graded := map[int]machine.Facts{number: facts(head)}
	if _, err := f.store.OpenItem(store.NewItem{Kind: ItemKind, Repo: repo, Number: number, Head: head, Mover: machine.MoverPool, Deadline: machine.GradeClock}, t0); err != nil {
		t.Fatal(err)
	}

	dispatched, err := Act(context.Background(), f.store, f.pool, items(t, f.store), graded, t0, 1)
	if err != nil {
		t.Fatal(err)
	}
	if got, want := graders(dispatched), []string{GraderFinder, GraderFidelity}; !reflect.DeepEqual(got, want) {
		t.Fatalf("dispatched = %v, want %v", got, want)
	}

	f.shell.exitAll()
	writeResult(t, f.finderDir(jobDir), `{"verdict":"ok"}`, t0)
	writeResult(t, filepath.Join(f.pool.root, grader.FidelityLane, jobDir), `{"verdict":"ok"}`, t0)

	again, err := Act(context.Background(), f.store, f.pool, items(t, f.store), graded, t0.Add(time.Minute), 1)
	if err != nil {
		t.Fatal(err)
	}
	if len(again) != 0 || len(workerNames(t, f.store)) != 0 {
		t.Fatalf("again = %v, workers = %v", again, workerNames(t, f.store))
	}
}
