package landing_test

import (
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"slices"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bkread"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/landing"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/pass"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/scaletest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	gateTool  = "/plugin/bin/rulings.py"
	gateSHA   = "e718a7434fa01ee9835f992a9a647a825350732"
	gateBase  = "aa11bb22cc33dd44ee55ff6677889900aabbccdd"
	gateBuild = "https://buildkite.com/forge/test/builds/700"
	baseBuild = "https://buildkite.com/forge/test/builds/600"
	prView    = `{"title":"delete the old bucket","author":{"login":"lane"},"additions":3,"deletions":1,"reviewDecision":"APPROVED"}`
)

type rulingsTool struct {
	*shelltest.Fake
	refusals map[string]string
	tapped   string
	broken   bool
	reads    int
}

func (r *rulingsTool) Run(ctx context.Context, c shell.Command) (shell.Result, error) {
	if c.Argv[0] != gateTool {
		return r.Fake.Run(ctx, c)
	}
	if r.broken {
		return shell.Result{Code: 2}, &shell.ExitError{Argv: c.Argv, Code: 2}
	}
	if c.Argv[1] == "authorize" {
		if !slices.Contains(c.Argv, r.tapped) {
			return shell.Result{Code: 1}, &shell.ExitError{Argv: c.Argv, Code: 1}
		}
		clear(r.refusals)
		return shell.Result{}, nil
	}
	r.reads++
	line := r.refusals[c.Argv[slices.Index(c.Argv, "--pr")+1]]
	if line == "" {
		return shell.Result{}, nil
	}
	return shell.Result{Code: 1, Stdout: line + "\n"}, &shell.ExitError{Argv: c.Argv, Code: 1}
}

type gateFixture struct {
	gates *landing.Gates
	fake  *shelltest.Fake
	tool  *rulingsTool
	now   time.Time
}

func newGates(t *testing.T) *gateFixture {
	t.Helper()
	return newGatesOnDev(t, "FAILURE")
}

func newGatesOnDev(t *testing.T, devTest string) *gateFixture {
	t.Helper()
	t.Setenv("CCI_HOME", t.TempDir())
	s, err := store.Memory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	if _, err := s.WantLane(store.Lane{Name: "lane-7", State: store.LaneDesired}, t0); err != nil {
		t.Fatal(err)
	}
	fake := &shelltest.Fake{}
	fake.Reply(devTestBuild(devTest), "ccx", "vcs", "gh", "--max-age", "0s", "--", "api", "graphql")
	tool := &rulingsTool{Fake: fake, refusals: map[string]string{}}
	f := &gateFixture{fake: fake, tool: tool, now: t0}
	f.gates = &landing.Gates{
		Runner: tool, CCX: ccx.CLI{Runner: fake}, Store: s, Drive: "d", Repo: monorepo, Trunk: "dev", Check: "buildkite/test", Checkout: "/co", Rulings: gateTool,
		Session: func(context.Context) (string, error) { return "board-1", nil },
		Now:     func() time.Time { return f.now },
		Sleep:   func(d time.Duration) { f.now = f.now.Add(d) },
		Log:     slog.New(slog.NewTextHandler(io.Discard, nil)),
	}
	return f
}

func gateHead(number int) string {
	return gateSHA + strconv.Itoa(number)
}

func enqueueAct(number int, branch, base string) pass.Act {
	return pass.Act{
		Facts:   machine.Facts{Repo: monorepo, Number: number, Head: gateHead(number), Branch: branch, Base: base, Lane: "lane-7"},
		Verdict: machine.Verdict{Action: machine.ActionEnqueue},
	}
}

func (f *gateFixture) admit(t *testing.T, acts []pass.Act, want ...int) {
	t.Helper()
	snapshot := make([]machine.Facts, 0, len(acts))
	for _, act := range acts {
		snapshot = append(snapshot, act.Facts)
	}
	got := make([]int, 0, len(acts))
	for _, act := range f.gates.Admit(context.Background(), acts, snapshot) {
		got = append(got, act.Facts.Number)
	}
	if !slices.Equal(got, want) {
		t.Fatalf("admitted %v, want %v", got, want)
	}
}

func gateRecords(t *testing.T, kind string) []client.Record {
	t.Helper()
	records, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{kind}})
	if err != nil {
		t.Fatal(err)
	}
	return records
}

func TestAReservedRowHoldsItsStackOnOneCardAndMergeOnThatCardAdmitsIt(t *testing.T) {
	f := newGates(t)
	f.fake.Reply(prView, "ccx", "vcs", "gh", "--max-age", "5m", "--", "pr", "view")
	f.tool.refusals[monorepo+"#1"] = "RESERVED-NO-ANSWER reserved/cloud-delete\tNo cloud deletion."
	acts := []pass.Act{enqueueAct(2, "upper", "lower"), enqueueAct(1, "lower", "dev"), enqueueAct(3, "other", "dev")}

	f.admit(t, acts, 3)

	notes := gateRecords(t, "note")
	if len(notes) != 1 {
		t.Fatalf("notes = %+v", notes)
	}
	attrs := notes[0].Fields.Attrs
	if notes[0].Lane != "supervisor" || notes[0].Topic != "board-write" || attrs["op"] != "ask" || attrs["session"] != "board-1" || attrs["waits"] != "merge:1:e718a74" {
		t.Fatalf("board write = %+v", notes[0])
	}
	var card struct {
		ID       string `json:"id"`
		Title    string `json:"title"`
		Children []struct {
			ID      string `json:"id"`
			MD      string `json:"md"`
			Options []struct {
				ID string `json:"id"`
			} `json:"options"`
		} `json:"children"`
	}
	if err := json.Unmarshal([]byte(attrs["card"]), &card); err != nil {
		t.Fatal(err)
	}
	said := card.Children[0].MD
	if card.ID != "merge:1:e718a74" || card.Title != "Merge #1, delete the old bucket?" || !strings.Contains(said, "No cloud deletion.") || strings.Contains(said, "reserved/cloud-delete") || strings.Contains(said, "delete the old bucket") || !strings.Contains(said, "By lane, +3") {
		t.Fatalf("card = %+v", card)
	}
	answer := card.Children[2]
	if answer.ID != "merge:1:e718a74-answer" || answer.Options[0].ID != "merge" {
		t.Fatalf("answer block = %+v", answer)
	}
	reports := gateRecords(t, "report")
	if len(reports) != 1 || !slices.Equal(reports[0].To, []string{"lane-7", "root"}) || !strings.Contains(reports[0].Text, "reserved/cloud-delete") || !strings.Contains(reports[0].Text, "merge:1:e718a74") {
		t.Fatalf("reports = %+v", reports)
	}
	if got := gateRecords(t, "blocker"); len(got) != 0 {
		t.Fatalf("a reserved row raised a blocker: %+v", got)
	}

	f.now = t0.Add(time.Minute)
	reads := f.tool.reads
	f.admit(t, acts, 3)
	if f.tool.reads != reads {
		t.Fatalf("read again inside the re-read window: %d, was %d", f.tool.reads, reads)
	}

	f.now = t0.Add(6 * time.Minute)
	f.admit(t, acts, 3)
	if got := gateRecords(t, "note"); len(got) != 1 {
		t.Fatalf("the same card drew again: %+v", got)
	}

	f.tool.tapped = answer.ID
	f.now = t0.Add(12 * time.Minute)
	f.admit(t, acts, 2, 1, 3)
}

func TestACardThatCannotDrawTellsTheLaneAndRootAndStillHoldsTheHead(t *testing.T) {
	f := newGates(t)
	f.fake.Fail(1, "boom", "ccx", "vcs", "gh")
	f.tool.refusals[monorepo+"#1"] = "RESERVED-NO-ANSWER reserved/cloud-delete\tNo cloud deletion."

	f.admit(t, []pass.Act{enqueueAct(1, "one", "dev")})

	reports := gateRecords(t, "report")
	if len(reports) != 1 || !strings.Contains(reports[0].Text, "did not draw") {
		t.Fatalf("reports = %+v", reports)
	}
	if got := gateRecords(t, "note"); len(got) != 0 {
		t.Fatalf("notes = %+v", got)
	}
}

func TestARulingsCheckThatDoesNotReadForAMinuteHoldsTheHeadAndTellsRoot(t *testing.T) {
	f := newGates(t)
	acts := []pass.Act{enqueueAct(1, "one", "dev")}
	started := f.now

	f.tool.broken = true
	f.admit(t, acts)
	if waited := f.now.Sub(started); waited < time.Minute {
		t.Fatalf("waited %s before holding, want a minute of rereads", waited)
	}
	blockers := gateRecords(t, "blocker")
	if len(blockers) != 1 || !strings.HasPrefix(blockers[0].Text, "RULINGS-UNREAD #1 ") || !slices.Contains(blockers[0].To, "root") {
		t.Fatalf("blockers = %+v, want one RULINGS-UNREAD to root", blockers)
	}

	f.tool.broken = false
	f.admit(t, acts, 1)
	f.admit(t, acts, 1)
	if f.tool.reads != 1 {
		t.Fatalf("reads = %d, want one read once it reads and none once clear", f.tool.reads)
	}
}

func TestAHandEnqueueGateNamesARulingsCheckThatDoesNotRead(t *testing.T) {
	f := newGates(t)
	f.tool.broken = true

	got := f.gates.HandEnqueueLines(context.Background(), []machine.Facts{gateFacts(1, "one", "dev", nil)}, []string{"1"}, false, false)

	if len(got) != 1 || !strings.HasPrefix(got[0], "#1 e718a7434f RULINGS-UNREAD ") {
		t.Fatalf("lines = %q", got)
	}
}

func statusOf(url string) string {
	return `{"statuses":[{"state":"failure","target_url":"` + url + `"}]}`
}

func buildAnnotation(body string) string {
	return `[{"context":"go-build-failed-sand-next-tests","body_html":"<ul><li><code>` + body + `</code></li></ul>"},{"context":"other","body_html":"<ul><li>not this</li></ul>"}]`
}

func buildGates(t *testing.T, files, atHead, atBase string) *gateFixture {
	t.Helper()
	f := newGates(t)
	f.gates.Build = landing.BuildFailures{Tree: "go/sand/next/", Context: "go-build-failed-sand-next-tests", Annotations: "tools/ci bk api /pipelines/{pipeline}/builds/{number}/annotations"}
	f.gates.Token = bkread.Token{Command: []string{tokenTool}}
	f.fake.Reply("read-token\n", tokenTool)
	gh := []string{"ccx", "vcs", "gh", "--max-age", "5m", "--", "api"}
	f.fake.Reply(files, append(slices.Clone(gh), "--paginate")...)
	f.fake.Reply(statusOf(gateBuild), append(slices.Clone(gh), "repos/"+monorepo+"/commits/"+gateHead(1)+"/status")...)
	f.fake.Reply(statusOf(baseBuild), append(slices.Clone(gh), "repos/"+monorepo+"/commits/"+gateBase+"/status")...)
	f.fake.Reply(atHead, ciTool, "bk", "api", "/pipelines/test/builds/700/annotations")
	f.fake.Reply(atBase, ciTool, "bk", "api", "/pipelines/test/builds/600/annotations")
	f.fake.Reply("", "git", "-C", "/co", "cat-file")
	f.fake.Reply(gateBase+"\n", "git", "-C", "/co", "merge-base")
	return f
}

func TestABuildBreakInTheSoftFailedTreeStaysOutOfTheQueue(t *testing.T) {
	f := buildGates(t, "go/sand/next/run.go\n", buildAnnotation("go/sand/next/run undefined: Foo"), `[]`)

	f.admit(t, []pass.Act{enqueueAct(1, "one", "dev")})

	got := gateRecords(t, "blocker")
	if len(got) != 1 || !strings.HasPrefix(got[0].Text, "BUILD-FAILED #1: go/sand/next/run fails to build at e718a7434f on "+gateBuild+": undefined: Foo") || !slices.Equal(got[0].To, []string{"lane-7", "root"}) {
		t.Fatalf("blockers = %+v", got)
	}
}

func TestAHeadWhoseBodyWaitsToGoLiveUnsafeOnDevStaysOutOfTheQueueWithItsStack(t *testing.T) {
	f := newGates(t)
	unsafe := enqueueAct(1, "one", "dev")
	unsafe.Facts.UnsafeWait = "this lands only after #46014 is live on every cluster"

	f.admit(t, []pass.Act{unsafe, enqueueAct(2, "two", "one"), enqueueAct(3, "three", "dev")}, 3)

	got := gateRecords(t, "blocker")
	if len(got) != 1 || !strings.HasPrefix(got[0].Text, "UNSAFE-ON-DEV #1 at e718a7434f stays out of the queue: its body waits to go live") || !slices.Equal(got[0].To, []string{"lane-7", "root"}) {
		t.Fatalf("blockers = %+v", got)
	}
}

func TestTheBuildGateReadsAnnotationsWithTheReadToken(t *testing.T) {
	f := buildGates(t, "go/sand/next/run.go\n", buildAnnotation("go/sand/next/run undefined: Foo"), `[]`)

	f.admit(t, []pass.Act{enqueueAct(1, "one", "dev")})

	read := 0
	for _, call := range f.fake.Calls() {
		if call.Argv[0] == ciTool {
			read++
			if !slices.Equal(call.Env, []string{"BUILDKITE_TOKEN=read-token"}) {
				t.Fatalf("%v ran with env %v, want the read token", call.Argv, call.Env)
			}
		}
	}
	if read == 0 {
		t.Fatal("the gate read no annotations")
	}
}

func TestABuildBreakWhoseOwnerStoppedReachesRootAloneAsUnowned(t *testing.T) {
	f := buildGates(t, "go/sand/next/run.go\n", buildAnnotation("go/sand/next/run undefined: Foo"), `[]`)
	if err := f.gates.Store.SetLaneState("lane-7", store.LaneHeld, t0, "", "quiet 40 min"); err != nil {
		t.Fatal(err)
	}

	f.admit(t, []pass.Act{enqueueAct(1, "one", "dev")})

	got := gateRecords(t, "blocker")
	if len(got) != 1 || !strings.HasPrefix(got[0].Text, "UNOWNED, lane-7 is not running: BUILD-FAILED #1: ") || !slices.Equal(got[0].To, []string{"root"}) {
		t.Fatalf("blockers = %+v", got)
	}
}

func TestABuildBreakItsBaseAlsoHasIsInherited(t *testing.T) {
	broken := buildAnnotation("go/sand/next/run undefined: Foo")
	f := buildGates(t, "go/sand/next/run.go\n", broken, broken)

	f.admit(t, []pass.Act{enqueueAct(1, "one", "dev")}, 1)

	if got := gateRecords(t, "blocker"); len(got) != 0 {
		t.Fatalf("blockers = %+v", got)
	}
}

func TestAHeadOutsideTheTreeReadsNoBuild(t *testing.T) {
	f := buildGates(t, "api/src/index.ts\n", buildAnnotation("go/sand/next/run undefined: Foo"), `[]`)

	f.admit(t, []pass.Act{enqueueAct(1, "one", "dev")}, 1)

	for _, call := range f.fake.Calls() {
		if strings.Contains(strings.Join(call.Argv, " "), "annotations") {
			t.Fatalf("read a build for a head outside the tree: %v", call.Argv)
		}
	}
}

func gateFacts(number int, branch, base string, change func(*machine.Facts)) machine.Facts {
	return facts(number, func(f *machine.Facts) {
		f.Repo, f.Head, f.Branch, f.Base = monorepo, gateHead(number), branch, base
		if change != nil {
			change(f)
		}
	})
}

func TestAHandEnqueueGateReadsEveryHeadTheEnqueueTakes(t *testing.T) {
	red := func(f *machine.Facts) { f.CI = machine.CIFailure }
	snapshot := []machine.Facts{
		gateFacts(1, "low", "dev", red),
		gateFacts(2, "mid", "low", red),
		gateFacts(3, "top", "mid", red),
		gateFacts(4, "side", "low", red),
		gateFacts(5, "other", "dev", red),
	}
	line := func(number int) string { return "#" + strconv.Itoa(number) + " e718a7434f CI is not green" }
	for _, test := range []struct {
		name    string
		named   []string
		partial bool
		want    []string
	}{
		{"the whole stack above what it names", []string{"2"}, false, []string{line(1), line(2), line(3), line(4)}},
		{"partial stops at what it names", []string{"2"}, true, []string{line(1), line(2)}},
		{"a PR the drive does not hold", []string{"9"}, false, []string{"#9 is no open PR of this drive"}},
	} {
		t.Run(test.name, func(t *testing.T) {
			got := newGates(t).gates.HandEnqueueLines(context.Background(), snapshot, test.named, test.partial, false)
			if !slices.Equal(got, test.want) {
				t.Fatalf("lines = %q, want %q", got, test.want)
			}
		})
	}
}

func TestAHandEnqueueGateNamesAReservedRowAndABuildBreakAndChangesNothing(t *testing.T) {
	f := buildGates(t, "go/sand/next/run.go\n", buildAnnotation("go/sand/next/run undefined: Foo"), `[]`)
	f.tool.refusals[monorepo+"#1"] = "RESERVED-NO-ANSWER reserved/cloud-delete\tNo cloud deletion."

	got := f.gates.HandEnqueueLines(context.Background(), []machine.Facts{gateFacts(1, "one", "dev", nil)}, []string{"1"}, false, false)

	if len(got) != 2 || got[0] != "#1 e718a7434f RESERVED reserved/cloud-delete: No cloud deletion." || !strings.HasPrefix(got[1], "#1 e718a7434f BUILD-FAILED go/sand/next/run fails to build at e718a7434f on "+gateBuild) {
		t.Fatalf("lines = %q", got)
	}
	for _, kind := range []string{"note", "report", "blocker"} {
		if records := gateRecords(t, kind); len(records) != 0 {
			t.Fatalf("%s records = %+v", kind, records)
		}
	}
	kept, err := f.gates.Store.Facts(monorepo, 1, gateHead(1))
	if err != nil || len(kept) != 0 {
		t.Fatalf("facts = %v, err = %v", kept, err)
	}
}

func TestAHandEnqueueGateDoesNotReadAHeadTheRunnerCleared(t *testing.T) {
	f := newGates(t)
	f.admit(t, []pass.Act{enqueueAct(1, "one", "dev")}, 1)

	got := f.gates.HandEnqueueLines(context.Background(), []machine.Facts{gateFacts(1, "one", "dev", nil)}, []string{"1"}, false, false)

	if len(got) != 0 || f.tool.reads != 1 {
		t.Fatalf("lines = %q, reads = %d", got, f.tool.reads)
	}
}

func queueCheckArgv(number int) []string {
	return []string{"ccx", "vcs", "gh", "--max-age", "0s", "--", "api", "repos/" + monorepo + "/commits/" + gateHead(number) + "/check-runs?filter=latest&check_name=Trunk+Merge+Queue+%28dev%29"}
}

func TestADirectMergeGateNamesEachHeadTheQueueDidNotPass(t *testing.T) {
	untested := func(number int) string {
		return "#" + strconv.Itoa(number) + " e718a7434f QUEUE-UNTESTED Trunk Merge Queue (dev) has not passed at this head"
	}
	for _, test := range []struct {
		name       string
		conclusion string
		want       []string
	}{
		{"the queue passed it", "success\n", nil},
		{"the queue never ran on it", "null\n", []string{untested(1)}},
		{"the queue is still testing it", "\n", []string{untested(1)}},
		{"the queue failed it", "failure\n", []string{untested(1)}},
		{"the queue cancelled it", "cancelled\n", []string{untested(1)}},
	} {
		t.Run(test.name, func(t *testing.T) {
			f := newGates(t)
			f.fake.Reply(test.conclusion, queueCheckArgv(1)...)

			got := f.gates.HandEnqueueLines(context.Background(), []machine.Facts{gateFacts(1, "one", "dev", nil)}, []string{"1"}, true, true)

			if !slices.Equal(got, test.want) {
				t.Fatalf("lines = %q, want %q", got, test.want)
			}
		})
	}
}

func TestADirectMergeGateReadsEveryHeadBelowWhatItNames(t *testing.T) {
	f := newGates(t)
	f.fake.Reply("success\n", queueCheckArgv(1)...)
	f.fake.Reply("null\n", queueCheckArgv(2)...)
	snapshot := []machine.Facts{gateFacts(1, "low", "dev", nil), gateFacts(2, "top", "low", nil), gateFacts(3, "other", "dev", nil)}

	got := f.gates.HandEnqueueLines(context.Background(), snapshot, []string{"2"}, true, true)

	if len(got) != 1 || !strings.HasPrefix(got[0], "#2 ") {
		t.Fatalf("lines = %q", got)
	}
}

func TestADirectMergeGatePassesAHeadWhoseCheckDoesNotRead(t *testing.T) {
	f := newGates(t)
	f.fake.Fail(1, "rate limited", queueCheckArgv(1)...)

	got := f.gates.HandEnqueueLines(context.Background(), []machine.Facts{gateFacts(1, "one", "dev", nil)}, []string{"1"}, true, true)

	if len(got) != 0 {
		t.Fatalf("lines = %q", got)
	}
}

func TestAnEnqueueGateNeverReadsTheQueuesCheck(t *testing.T) {
	f := newGates(t)

	f.gates.HandEnqueueLines(context.Background(), []machine.Facts{gateFacts(1, "one", "dev", nil)}, []string{"1"}, true, false)

	for _, call := range f.fake.Calls() {
		if strings.Contains(strings.Join(call.Argv, " "), "check-runs") {
			t.Fatalf("read the queue's check for a landing the queue tests itself: %v", call.Argv)
		}
	}
}

func TestAHandEnqueueAsksTheSweepOnlyAboutTheHeadsItTakesAtAnyDriveSize(t *testing.T) {
	asks := func(size int) int {
		f := newGates(t)
		count := 0
		f.gates.GoAnswer = func(context.Context, machine.Facts) ([]string, error) {
			count++
			return nil, nil
		}
		snapshot := make([]machine.Facts, size)
		for i := range snapshot {
			snapshot[i] = gateFacts(i+1, "branch-"+strconv.Itoa(i+1), "dev", func(f *machine.Facts) { f.Tap = "" })
		}
		f.gates.HandEnqueueLines(context.Background(), snapshot, []string{"1"}, true, false)
		return count
	}
	if one, many := asks(1), asks(scaletest.N()); one != 1 || many != one {
		t.Fatalf("asked the sweep %d times at N=1 and %d at N=%d, want 1 at both", one, many, scaletest.N())
	}
}

func openRed(t *testing.T, slug string) client.Record {
	t.Helper()
	opener, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: "supervisor", Kind: "incident", To: []string{"root"}, Topic: "dev-red", ID: "dev-red-open:" + slug, Force: true, Attrs: map[string]string{"dev-red": slug, "job:0": "go"}, Text: "INCIDENT OPEN " + slug + ": go fails."})
	if err != nil {
		t.Fatal(err)
	}
	return opener
}

func devTestBuild(state string) string {
	return `{"data":{"repository":{"object":{"history":{"nodes":[{"oid":"40f5676e","status":{"contexts":[{"context":"buildkite/test","state":"PENDING"}]}},{"oid":"0838da5","status":{"contexts":[{"context":"buildkite/test","state":"` + state + `","targetUrl":"https://buildkite.com/forge/test/builds/119479"}]}}]}}}}}`
}

func devRedHolds(t *testing.T) []client.Record {
	t.Helper()
	return slices.DeleteFunc(gateRecords(t, "report"), func(r client.Record) bool { return !strings.HasPrefix(r.Text, "DEV-RED-HOLD") })
}

func TestAnOpenDevRedHoldsEveryPRButItsFixWithOneRecordAndTheHeldPRsEnqueueOnceItCloses(t *testing.T) {
	f := newGates(t)
	slug := "dev-red-1ad58822aa"
	opener := openRed(t, slug)
	if _, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: slug + "-fix", Kind: "head", Topic: slug, PRs: []string{"52527"}, Text: "HEAD #52527 abc1234"}); err != nil {
		t.Fatal(err)
	}
	acts := []pass.Act{enqueueAct(51847, "held", "dev"), enqueueAct(52527, "fix", "dev")}

	for range 3 {
		f.admit(t, acts, 52527)
	}
	holds := devRedHolds(t)
	if len(holds) != 1 || holds[0].Topic != "51847" || !strings.Contains(holds[0].Text, slug) || !strings.Contains(holds[0].Text, "#52527") {
		t.Fatalf("holds = %+v, want one record on #51847's topic naming %s and its fix #52527", holds, slug)
	}

	if _, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: "supervisor", Kind: "done", To: []string{"root"}, Topic: "dev-red", Resolves: opener.Seq, Force: true, Text: "INCIDENT " + slug + " CLOSED"}); err != nil {
		t.Fatal(err)
	}
	f.admit(t, acts, 51847, 52527)
}

func TestADevRedHoldsEachOfManyQueuedPRsWithOneRecordAcrossPasses(t *testing.T) {
	f := newGates(t)
	openRed(t, "dev-red-e2d9d66aaa")
	acts := make([]pass.Act, scaletest.N())
	for i := range acts {
		acts[i] = enqueueAct(i+1, "branch-"+strconv.Itoa(i+1), "dev")
	}

	for range 3 {
		f.admit(t, acts)
	}
	holds := devRedHolds(t)
	topics := map[string]bool{}
	for _, hold := range holds {
		topics[hold.Topic] = true
	}
	if len(holds) != len(acts) || len(topics) != len(acts) {
		t.Fatalf("%d holds on %d topics for %d PRs over 3 passes, want one each", len(holds), len(topics), len(acts))
	}
}

func TestAnOpenDevRedHoldsNothingOnceDevsNewestTestBuildIsGreen(t *testing.T) {
	f := newGatesOnDev(t, "SUCCESS")
	openRed(t, "dev-red-6cc5146acc")
	acts := []pass.Act{enqueueAct(52236, "unrelated", "dev")}

	f.admit(t, acts, 52236)
	if holds := devRedHolds(t); len(holds) != 0 {
		t.Fatalf("holds = %+v, want none while dev's newest test build is green", holds)
	}
}
