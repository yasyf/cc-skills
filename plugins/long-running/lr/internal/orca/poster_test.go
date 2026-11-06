package orca

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/incident"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/wait"
)

const (
	briefLines = "Scale: no N\n\nRegister slice: lane=x step=1\n"
	noRunner   = "refused: no orca desk runner has posted an orca alive record on cci drive prog in the last five minutes ("
)

func aliveRecord(at time.Time) client.Record {
	return client.Record{Seq: 99, Lane: deskRunner, Kind: kindState, Topic: runnerTopic, At: at, Text: "orca alive 11:20 PM pass 3"}
}

type runnerBus struct {
	mu       sync.Mutex
	records  []client.Record
	verdicts map[string]string
	launched bool
	node     string
	posts    []client.Draft
	calls    []string
	stored   map[string]client.Record
}

func (b *runnerBus) Post(_ context.Context, d client.Draft) (client.Record, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.calls = append(b.calls, "post")
	b.posts = append(b.posts, d)
	if earlier, found := b.stored[d.Text+"\x00"+d.ID]; found {
		return earlier, nil
	}
	seq := b.records[len(b.records)-1].Seq + 1
	record := client.Record{Seq: seq, Num: fmt.Sprintf("#%d%s", seq, b.node), Lane: d.Lane, Text: d.Text}
	b.records = append(b.records, record)
	if b.stored != nil {
		b.stored[d.Text+"\x00"+d.ID] = record
	}
	key := "R" + strings.TrimPrefix(record.Num, "#")
	_, after, launch := strings.Cut(d.Text, " launch ")
	if !launch {
		return record, nil
	}
	lane, _, _ := strings.Cut(after, " ")
	verdict := b.verdicts[key]
	if verdict == "" && b.launched {
		verdict = "LAUNCHED " + key + " %[1]s: dispatch ctx_%[1]s terminal term_%[1]s"
	}
	if verdict != "" {
		b.records = append(b.records, client.Record{Seq: seq + 1, Lane: deskRunner, Kind: "report", Text: fmt.Sprintf(verdict, lane)})
	}
	return record, nil
}

func (b *runnerBus) Tail(_ context.Context, q client.Query) ([]client.Record, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.calls = append(b.calls, "tail")
	var found []client.Record
	for _, record := range b.records {
		if record.Seq <= q.After || (!q.Since.IsZero() && record.At.Before(q.Since)) {
			continue
		}
		if (len(q.Lanes) == 0 || slices.Contains(q.Lanes, record.Lane)) && (len(q.Kinds) == 0 || slices.Contains(q.Kinds, string(record.Kind))) {
			found = append(found, record)
		}
	}
	return found, nil
}

type posterFixture struct {
	bus    *runnerBus
	poster Poster
	out    *bytes.Buffer
	dir    string
	env    map[string]string
}

func newPosterFixture(t *testing.T) *posterFixture {
	t.Helper()
	dir := t.TempDir()
	home := filepath.Join(dir, "home")
	entries := filepath.Join(home, ".claude", "long-running", "drives")
	if err := os.MkdirAll(entries, 0o750); err != nil {
		t.Fatal(err)
	}
	raw, err := json.Marshal(map[string]any{"drive": "prog", "cci_drive": "prog", "sessions": []string{"s1"}, "state_dir": filepath.Join(dir, "prog"), "checkout": "/prog/checkout"})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(entries, "prog.json"), raw, 0o600); err != nil {
		t.Fatal(err)
	}
	f := &posterFixture{
		bus: &runnerBus{records: []client.Record{
			aliveRecord(time.Now()),
			{Seq: 100, Lane: coordinatorLane, Kind: kindGo, Text: "R2025 (11:50 PM) orca-desk: launch old-fix NOW incident xhigh brief=/b.md"},
			{Seq: 101, Lane: coordinatorLane, Kind: kindGo, Text: "orca-desk: unhold sandsql-5177"},
		}, verdicts: map[string]string{}},
		out: &bytes.Buffer{},
		dir: dir,
		env: map[string]string{"HOME": home, sessionEnv: "s1"},
	}
	f.poster = Poster{
		Bus: f.bus,
		Runner: &scripted{reply: func(argv []string) (string, string, int) {
			t.Errorf("ran %v", argv)
			return "", "", 1
		}},
		Env:    func(name string) string { return f.env[name] },
		Now:    time.Now,
		Sleep:  func(time.Duration) {},
		Out:    f.out,
		Author: coordinatorLane,
	}
	return f
}

func (f *posterFixture) brief(t *testing.T, rel, text string) string {
	t.Helper()
	path := filepath.Join(f.dir, rel)
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func (f *posterFixture) texts() []string {
	texts := make([]string, len(f.bus.posts))
	for i, post := range f.bus.posts {
		texts[i] = post.Text
	}
	return texts
}

func TestTwoPostersAtOnceNeverShareAKey(t *testing.T) {
	f := newPosterFixture(t)
	f.bus.launched = true
	brief := f.brief(t, "fix-brief.md", "fix\n"+briefLines)
	outs := []*bytes.Buffer{{}, {}}
	labels := make([]string, 2)
	var racing sync.WaitGroup
	for i, lane := range []string{"lane-a", "lane-b"} {
		racing.Go(func() {
			poster := f.poster
			poster.Out = outs[i]
			label, err := poster.Launch(context.Background(), Order{Lane: lane, Model: "sol", Effort: "high", Brief: brief}, time.Minute)
			if err != nil {
				t.Error(err)
			}
			labels[i] = label
		})
	}
	racing.Wait()
	if !slices.Equal(labels, []string{verdictLaunched, verdictLaunched}) {
		t.Fatal(labels, outs[0], outs[1])
	}
	keyOf := func(out *bytes.Buffer) string {
		for line := range strings.Lines(out.String()) {
			if strings.Contains(line, " LAUNCHED ") {
				return strings.Fields(line)[1]
			}
		}
		return ""
	}
	if a, b := keyOf(outs[0]), keyOf(outs[1]); a == "" || a == b {
		t.Fatalf("keys %q and %q", a, b)
	}
}

func TestASessionTheRegistryDoesNotListFindsItsDriveByTheDriveItCarries(t *testing.T) {
	f := newPosterFixture(t)
	f.env[sessionEnv] = "a-later-session"
	f.poster.Runner = cciMe("monorepo-8a lane lane=root busy now")
	if _, err := f.poster.Entry(context.Background()); err == nil {
		t.Fatal("a session carrying no drive found one")
	}
	f.env[driveEnv] = "prog"
	entry, err := f.poster.Entry(context.Background())
	if err != nil || entry.Drive != "prog" {
		t.Fatalf("entry %+v, %v", entry, err)
	}
}

func TestASessionTheRegistryDoesNotListFindsTheDriveCCIBindsItTo(t *testing.T) {
	f := newPosterFixture(t)
	f.env[sessionEnv] = "a-later-session"
	f.poster.Runner = cciMe("monorepo-8a lane drive=prog lane=root busy now")
	entry, err := f.poster.Entry(context.Background())
	if err != nil || entry.Drive != "prog" {
		t.Fatalf("entry %+v, %v", entry, err)
	}
}

func cciMe(line string) *scripted {
	return &scripted{reply: func(argv []string) (string, string, int) {
		if !slices.Equal(argv, []string{"cci", "me"}) {
			return "", "unexpected " + strings.Join(argv, " "), 1
		}
		return line + "\n", "", 0
	}}
}

func TestALaunchPostsAnAddressedLineWithNoClockAndPrintsItsVerdict(t *testing.T) {
	f := newPosterFixture(t)
	brief := f.brief(t, "brief-of-a.md", "work\n"+briefLines)
	f.bus.verdicts["R102"] = "LAUNCHED R102 %[1]s: dispatch ctx_l terminal term_l"
	label, err := f.poster.Launch(context.Background(), Order{Lane: "new-lane", Model: "opus", Effort: "high", Brief: brief, Now: true}, time.Minute)
	if err != nil || label != verdictLaunched {
		t.Fatal(label, err)
	}
	post := f.bus.posts[0]
	if post.Text != "orca-desk: launch new-lane NOW opus high brief="+brief || !slices.Equal(post.To, []string{deskLane}) || post.Kind != kindGo || post.Topic != "new-lane" || post.Drive != "prog" || post.Lane != coordinatorLane {
		t.Fatalf("%+v", post)
	}
	if !strings.Contains(f.out.String(), "new-lane R102 LAUNCHED R102 new-lane: dispatch ctx_l terminal term_l\n") {
		t.Fatal(f.out)
	}
}

func TestARelaunchUnderAFailedNameWithinTheBusDedupWindowPostsItsOwnLineAndReadsItsOwnVerdict(t *testing.T) {
	f := newPosterFixture(t)
	f.bus.stored = map[string]client.Record{}
	brief := f.brief(t, "brief-of-a.md", "work\n"+briefLines)
	order := Order{Lane: "new-lane", Model: "opus", Effort: "high", Brief: brief, Now: true}
	f.bus.verdicts["R102"] = "LAUNCH-FAILED desk-lane-%[1]s/R102 %[1]s: no terminal has appeared after orca terminal create"
	f.bus.verdicts["R104"] = "LAUNCHED R104 %[1]s: dispatch ctx_l terminal term_l"

	failed, err := f.poster.Launch(context.Background(), order, time.Minute)
	if err != nil || failed != "LAUNCH-FAILED" {
		t.Fatal(failed, err)
	}
	again, err := f.poster.Launch(context.Background(), order, time.Minute)
	if err != nil || again != verdictLaunched {
		t.Fatalf("the relaunch read %q, %v:\n%s", again, err, f.out)
	}
	if !strings.Contains(f.out.String(), "posted cci #104: ") {
		t.Fatalf("the relaunch posted no line of its own:\n%s", f.out)
	}
}

func TestTheRealBusReplaysARepeatedLineAndPostsOneWithItsOwnAttemptKey(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	ctx := context.Background()
	line := client.Draft{Drive: "d", Lane: coordinatorLane, Kind: kindGo, To: []string{deskLane}, Topic: "new-lane", Text: "orca-desk: launch new-lane NOW opus high brief=/b.md"}
	first, err := CCI{}.Post(ctx, line)
	if err != nil {
		t.Fatal(err)
	}
	replayed, err := CCI{}.Post(ctx, line)
	if err != nil || replayed.Seq != first.Seq {
		t.Fatalf("no key: #%d after #%d, %v", replayed.Seq, first.Seq, err)
	}
	line.ID = incident.LaunchAttempt("new-lane", time.Now())
	attempt, err := CCI{}.Post(ctx, line)
	if err != nil || attempt.Seq == first.Seq {
		t.Fatalf("attempt key: #%d after #%d, %v", attempt.Seq, first.Seq, err)
	}
}

func TestALaunchWarnsOnABriefWithASecondCCXLine(t *testing.T) {
	for _, tt := range []struct {
		name, ccx string
		warned    bool
	}{
		{"two lines", "ccx: role=build\nccx: incident=none\n", true},
		{"one line", "ccx: role=build\n", false},
		{"prose naming it", "ccx: role=build\nThe first ccx: line wins.\n", false},
	} {
		t.Run(tt.name, func(t *testing.T) {
			f := newPosterFixture(t)
			brief := f.brief(t, "brief-of-a.md", "Register slice: lane=new-lane step=all\nScale: no N\n"+tt.ccx)
			f.bus.verdicts["R102"] = "LAUNCHED R102 %[1]s: dispatch ctx_l terminal term_l"
			if _, err := f.poster.Launch(context.Background(), Order{Lane: "new-lane", Model: "opus", Effort: "high", Brief: brief, Now: true}, time.Minute); err != nil {
				t.Fatal(err)
			}
			if got := strings.Contains(f.out.String(), "WARN brief "+brief+" has 2 `ccx:` lines"); got != tt.warned {
				t.Fatalf("warned = %v, want %v:\n%s", got, tt.warned, f.out)
			}
		})
	}
}

func TestAClaudeLaunchWhoseBriefNeedsSentryWarnsToRunItOnSol(t *testing.T) {
	for _, tt := range []struct {
		name, model, ccx string
		warned           bool
	}{
		{"claude reading sentry", "opus", "ccx: role=read place=local why=sentry", true},
		{"sol reading sentry", "sol", "ccx: role=read place=local why=sentry", false},
		{"claude naming sentry in prose", "opus", "ccx: role=build place=local why=mac-build\nThe fix ends a Sentry issue's events.", false},
	} {
		t.Run(tt.name, func(t *testing.T) {
			f := newPosterFixture(t)
			brief := f.brief(t, "brief-of-a.md", "Register slice: lane=new-lane step=all\nScale: no N\n"+tt.ccx+"\n")
			f.bus.verdicts["R102"] = "LAUNCHED R102 %[1]s: dispatch ctx_l terminal term_l"
			if _, err := f.poster.Launch(context.Background(), Order{Lane: "new-lane", Model: tt.model, Effort: "high", Brief: brief, Now: true}, time.Minute); err != nil {
				t.Fatal(err)
			}
			if got := strings.Contains(f.out.String(), "WARN new-lane's brief says why=sentry, but a Claude lane has no Sentry tools; launch it with --model sol\n"); got != tt.warned || len(f.bus.posts) != 1 {
				t.Fatalf("warned = %v, want %v, posts %d:\n%s", got, tt.warned, len(f.bus.posts), f.out)
			}
		})
	}
}

func TestALaunchTheRunnerRefusesReturnsItsVerdict(t *testing.T) {
	f := newPosterFixture(t)
	brief := f.brief(t, "b.md", "work\n"+briefLines)
	f.bus.verdicts["R102"] = "LAUNCH-FAILED desk-lane-%[1]s/R102 %[1]s: lr launch start exited 1"
	label, err := f.poster.Launch(context.Background(), Order{Lane: "inc-x-fix", Model: "opus", Effort: "high", Brief: brief}, time.Minute)
	if err != nil || label != "LAUNCH-FAILED" {
		t.Fatal(label, err)
	}
	if !strings.Contains(f.out.String(), "inc-x-fix R102 LAUNCH-FAILED desk-lane-inc-x-fix/R102 inc-x-fix: lr launch start exited 1") {
		t.Fatal(f.out)
	}
}

func TestADuplicateLaunchIsAVerdictThatFails(t *testing.T) {
	f := newPosterFixture(t)
	brief := f.brief(t, "b.md", "work\n"+briefLines)
	f.bus.verdicts["R102"] = "LAUNCH-DUPLICATE R102 %[1]s: lane-b already holds this work; relay to it"
	err := f.poster.LaunchOrExit(context.Background(), Order{Lane: "dup-lane", Model: "opus", Effort: "high", Brief: brief}, time.Minute)
	var exit *wait.ExitError
	if !errors.As(err, &exit) || exit.Code != failedExit {
		t.Fatal(err)
	}
}

func TestAnElapsedWaitExitsZeroWhileTheRunnerIsAliveAndTwoOnceItIsStale(t *testing.T) {
	for _, tt := range []struct {
		name  string
		pause time.Duration
		code  int
		said  string
	}{
		{"alive", 30 * time.Second, 0, "slow-restore R102 posted with no verdict after 60s; the orca runner is alive, so its verdict arrives as a desk-runner record"},
		{"stale", 6 * time.Minute, unsettledExit, "slow-restore R102 unsettled after 60s: no orca desk runner has posted an orca alive record on cci drive prog in the last five minutes (last at "},
	} {
		t.Run(tt.name, func(t *testing.T) {
			f := newPosterFixture(t)
			brief := f.brief(t, "b.md", "work\n"+briefLines)
			now := time.Now()
			f.poster.Now = func() time.Time { return now }
			f.poster.Sleep = func(time.Duration) { now = now.Add(tt.pause) }
			err := f.poster.LaunchOrExit(context.Background(), Order{Lane: "slow-restore", Model: "sol", Effort: "high", Brief: brief}, time.Minute)
			code := 0
			var exit *wait.ExitError
			if errors.As(err, &exit) {
				code = exit.Code
			} else if err != nil {
				t.Fatal(err)
			}
			if code != tt.code {
				t.Fatal(code, err)
			}
			if !strings.Contains(f.out.String(), tt.said) {
				t.Fatal(f.out)
			}
		})
	}
}

func TestALaunchOnAnotherNodeWaitsOnTheKeyEveryNodeNamesItsRecordBy(t *testing.T) {
	f := newPosterFixture(t)
	brief := f.brief(t, "b.md", "work\n"+briefLines)
	f.bus.node = "@8d2775d4"
	f.bus.verdicts["R102@8d2775d4"] = "LAUNCHED desk-lane-%[1]s/R102@8d2775d4 %[1]s: dispatch ctx_r terminal term_r"
	label, err := f.poster.Launch(context.Background(), Order{Lane: "next-lane", Model: "opus", Effort: "high", Brief: brief}, time.Minute)
	if err != nil || label != verdictLaunched {
		t.Fatal(label, err)
	}
	if !strings.Contains(f.out.String(), "next-lane R102@8d2775d4 LAUNCHED desk-lane-next-lane/R102@8d2775d4 next-lane: dispatch ctx_r terminal term_r") {
		t.Fatal(f.out)
	}
}

func TestABriefWithNoSliceLinePostsWithAWarning(t *testing.T) {
	f := newPosterFixture(t)
	brief := f.brief(t, "plain-brief.md", "Do the thing.\nScale: no N\n")
	f.bus.verdicts["R102"] = "LAUNCHED R102 %[1]s: dispatch ctx_l terminal term_l"
	label, err := f.poster.Launch(context.Background(), Order{Lane: "new-lane", Model: "opus", Effort: "high", Brief: brief, Now: true}, time.Minute)
	if err != nil || label != verdictLaunched {
		t.Fatal(label, err)
	}
	if want := "WARN brief " + brief + " has no `Register slice: lane=<name> step=<n>` line; new-lane launches anyway"; !strings.Contains(f.out.String(), want) {
		t.Fatalf("output lacks %q:\n%s", want, f.out)
	}
}

func TestABriefWithNoScaleLineIsRefusedBeforeAnyCall(t *testing.T) {
	f := newPosterFixture(t)
	brief := f.brief(t, "plain-brief.md", "Do the thing.\n")
	_, err := f.poster.Launch(context.Background(), Order{Lane: "new-lane", Model: "opus", Effort: "high", Brief: brief}, time.Minute)
	if err == nil || !strings.Contains(err.Error(), "`Scale:`") || strings.Contains(err.Error(), "Register slice") {
		t.Fatal(err)
	}
	if len(f.bus.calls) != 0 {
		t.Fatal(f.bus.calls)
	}
}

func TestEveryFaultOfALaunchIsNamedAtOnceBeforeAnyCall(t *testing.T) {
	f := newPosterFixture(t)
	brief := f.brief(t, filepath.Join("other-lane", "brief.md"), "Do the thing.\n")
	_, err := f.poster.Launch(context.Background(), Order{Lane: "new-lane", Model: "gemini", Effort: "hard", Brief: brief}, time.Minute)
	if err == nil {
		t.Fatal("the launch passed")
	}
	for _, fault := range []string{"gemini is not a model", "hard is not an effort", "is lane other-lane's", "`Scale:`"} {
		if !strings.Contains(err.Error(), fault) {
			t.Errorf("%q lacks %q", err, fault)
		}
	}
	if len(f.bus.calls) != 0 {
		t.Fatal(f.bus.calls)
	}
}

func TestALaunchCarriesItsBriefsBodyOnItsGoRecord(t *testing.T) {
	f := newPosterFixture(t)
	brief := f.brief(t, filepath.Join("hook-fix-a", briefFile), briefLines+"Fix the hook.\n")
	f.bus.verdicts["R102"] = "LAUNCHED R102 %[1]s: dispatch ctx_l terminal term_l"
	if _, err := f.poster.Launch(context.Background(), Order{Lane: "hook-fix-a", Model: "opus", Effort: "high", Brief: brief}, time.Minute); err != nil {
		t.Fatal(err)
	}
	if len(f.bus.posts) == 0 || f.bus.posts[0].Path != brief || !strings.HasSuffix(f.bus.posts[0].Text, "brief="+brief) {
		t.Fatalf("%+v", f.bus.posts)
	}
}

func TestALaneThatMayLeaveTheMacIsWarnedOfARunOfTheMacsLedger(t *testing.T) {
	f := newPosterFixture(t)
	brief := f.brief(t, filepath.Join("pr-lane", briefFile), briefLines+"Right after the ship run `drive.py record --opened --pr <n>`.\n")
	f.bus.verdicts["R102"] = "LAUNCHED R102 %[1]s: dispatch ctx_l terminal term_l"
	if _, err := f.poster.Launch(context.Background(), Order{Lane: "pr-lane", Model: "sol", Effort: "high", Brief: brief}, time.Minute); err != nil {
		t.Fatal(err)
	}
	if want := "WARN brief " + brief + " runs drive.py record, which reads the Mac's drive ledger and no Sprite has"; !strings.Contains(f.out.String(), want) {
		t.Fatalf("output lacks %q:\n%s", want, f.out)
	}
}

func TestAShippedIncidentTemplateLaunchedUnrenderedIsRefusedNamingEachFieldBeforeAnyCall(t *testing.T) {
	f := newPosterFixture(t)
	template, err := os.ReadFile(filepath.Join("..", "..", "..", "skills", "long-running", "reference", "alert-fix-brief.md"))
	if err != nil {
		t.Fatal(err)
	}
	brief := f.brief(t, filepath.Join("sofi-team-runs-fix", briefFile), string(template))
	_, err = f.poster.Launch(context.Background(), Order{Lane: "sofi-team-runs-fix", Model: "incident", Effort: "high", Role: "fix", Brief: brief, Now: true}, time.Minute)
	if err == nil || !strings.Contains(err.Error(), "still holds the template fields {lane}, {incident}, {slug}, {onset}, {what}, {link}, {links}, {drive}, {facts}, {reported}, {due}, {repo}, which nothing filled") || len(f.bus.calls) != 0 {
		t.Fatal(err, f.bus.calls)
	}
}

func TestAMissingBriefIsRefusedBeforeAnyCall(t *testing.T) {
	f := newPosterFixture(t)
	_, err := f.poster.Launch(context.Background(), Order{Lane: "new-lane", Model: "opus", Effort: "high", Brief: filepath.Join(f.dir, "missing.md")}, time.Minute)
	if err == nil || !strings.Contains(err.Error(), "is not a file") || len(f.bus.calls) != 0 {
		t.Fatal(err, f.bus.calls)
	}
}

func TestADeskBriefWaitingOnItsOwnMailboxIsRefusedNamingItsCCIWait(t *testing.T) {
	f := newPosterFixture(t)
	stale := f.brief(t, filepath.Join("landing-desk-4", briefFile), briefLines+"`lr wait 50 cci:d:landing-desk mailbox:landing-desk=<cursor> -- ccx vcs pr watch`\n")
	_, err := f.poster.Launch(context.Background(), Order{Lane: "landing-desk-4", Model: "opus", Effort: "high", Brief: stale}, time.Minute)
	if err == nil || !strings.Contains(err.Error(), "waits on `mailbox:landing-desk`") || !strings.Contains(err.Error(), "wait on `cci:<drive>:landing-desk-4`") || len(f.bus.calls) != 0 {
		t.Fatal(err, f.bus.calls)
	}
	for lane, wait := range map[string]string{
		"landing-desk-5": "`lr wait 540 cci:d:landing-desk-5 -- ccx vcs pr watch`",
		"landing-desk-6": "Spawn shard-a with Agent; it waits with `lr wait 540 cci:d:shard-a mailbox:shard-a=<cursor>`.",
	} {
		brief := f.brief(t, filepath.Join(lane, briefFile), briefLines+wait+"\n")
		if _, err := f.poster.Launch(context.Background(), Order{Lane: lane, Model: "opus", Effort: "high", Brief: brief}, 0); err != nil {
			t.Fatal(lane, err)
		}
	}
}

func TestABriefWhoseRuleNamesACredentialLaunchesOnEveryRouteAndWarnsThatItAndAMissingPathStayBehind(t *testing.T) {
	f := newPosterFixture(t)
	iris := filepath.Join(f.env["HOME"], ".iris", "credentials.json")
	if err := os.MkdirAll(filepath.Dir(iris), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(iris, []byte("secret"), 0o600); err != nil {
		t.Fatal(err)
	}
	rule := "Never read ~/.iris/credentials.json.\n" + briefLines
	away := f.brief(t, "away.md", rule)
	local := f.brief(t, "local.md", "ccx: role=build place=local why=mac-build\n"+rule)
	picked := f.brief(t, "picked.md", "Owner pick: owner:m836\n"+rule)
	for _, order := range []Order{{Lane: "a", Model: "opus", Effort: "high", Brief: away}, {Lane: "a", Model: "opus", Effort: "high", Brief: local}, {Lane: "a", Model: "fable", Effort: "high", Brief: picked}, {Lane: "a", Model: agentIncident, Effort: "high", Brief: away}} {
		if _, err := f.poster.Launch(context.Background(), order, 0); err != nil {
			t.Fatal(order, err)
		}
	}
	if want := "WARN brief " + away + " names what stays behind on a Sprite: " + iris + " lies outside"; !strings.Contains(f.out.String(), want) {
		t.Fatal(f.out)
	}
	missing := filepath.Join(f.env["HOME"], ".claude", "scratch", "gone.md")
	warned := f.brief(t, "warned.md", "Read "+missing+".\n"+briefLines)
	if _, err := f.poster.Launch(context.Background(), Order{Lane: "w", Model: "opus", Effort: "high", Brief: warned}, 0); err != nil {
		t.Fatal(err)
	}
	if want := "WARN brief " + warned + " names what stays behind on a Sprite: " + missing + " does not exist on this Mac\n"; !strings.Contains(f.out.String(), want) {
		t.Fatal(f.out)
	}
}

func TestTheCoordinatorsLaunchWalksNoSpritePayloadWhateverItsBriefSays(t *testing.T) {
	f := newPosterFixture(t)
	gone := filepath.Join(f.env["HOME"], ".claude", "skills")
	brief := f.brief(t, filepath.Join(coordinatorLane, briefFile), "ccx: incident=none\nRead "+gone+".\n"+briefLines)
	if _, err := f.poster.Launch(context.Background(), Order{Lane: coordinatorLane, Model: "opus", Effort: "high", Brief: brief}, 0); err != nil {
		t.Fatal(err)
	}
	if len(f.bus.posts) != 1 {
		t.Fatal(f.bus.posts)
	}
}

func TestWhatALaunchOwesFollowsItsLaneItsBriefAndItsModel(t *testing.T) {
	oldBrief := scaleRule.Add(-time.Hour)
	newBrief := scaleRule.Add(time.Hour)
	cases := []struct {
		name         string
		order        Order
		text         string
		written      time.Time
		slice, scale bool
	}{
		{name: "a plain lane owes both", order: Order{Lane: "new-lane", Model: "opus"}, text: "work\n", written: newBrief, slice: true, scale: true},
		{name: "the incident route owes neither", order: Order{Lane: "inc-fix", Model: agentIncident}, text: "work\n", written: newBrief},
		{name: "the incident-opus route owes neither", order: Order{Lane: "inc-comms", Model: modelIncidentOpus, Role: "comms"}, text: "ccx: role=comms\n", written: newBrief},
		{name: "a brief naming an incident owes neither", order: Order{Lane: "inc-fix", Model: "opus"}, text: "ccx: incident=outage\n", written: newBrief},
		{name: "a brief naming no incident owes both", order: Order{Lane: "inc-fix", Model: "opus"}, text: "ccx: incident=none\n", written: newBrief, slice: true, scale: true},
		{name: "role=incident owes neither", order: Order{Lane: "inc-fix", Model: "opus"}, text: "ccx: role=incident\n", written: newBrief},
		{name: "an evidence lane is no fix lane", order: Order{Lane: "inc-evidence", Model: "opus"}, text: "ccx: incident=outage\n", written: newBrief, slice: true, scale: true},
		{name: "a desk role is no fix lane", order: Order{Lane: "inc", Model: "opus"}, text: "ccx: incident=outage role=desk\n", written: newBrief, slice: true, scale: true},
		{name: "a handoff owes no scale", order: Order{Lane: "a-handoff", Model: "opus"}, text: "work\n", written: newBrief, slice: true},
		{name: "a second lane owes no scale", order: Order{Lane: "a-2", Model: "opus"}, text: "work\n", written: newBrief, slice: true},
		{name: "a first numbered lane owes scale", order: Order{Lane: "a-1", Model: "opus"}, text: "work\n", written: newBrief, slice: true, scale: true},
		{name: "a replacement owes no scale", order: Order{Lane: "a", Model: "opus"}, text: "Replaces: `old`\n", written: newBrief, slice: true},
		{name: "a brief from before the rule owes no scale", order: Order{Lane: "a", Model: "opus"}, text: "work\n", written: oldBrief, slice: true},
		{name: "a quoted Scale line counts", order: Order{Lane: "a", Model: "opus"}, text: "> **Scale: no N\n", written: newBrief, slice: true},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			c.order.Effort, c.order.Brief = "high", "/briefs/b.md"
			slice := c.order.sliceWarning(c.text) != ""
			scale := c.order.scaleFault(c.text, c.written) != ""
			if slice != c.slice || scale != c.scale {
				t.Fatalf("slice %v, scale %v", slice, scale)
			}
		})
	}
}

func TestALaunchOfAStemTakesTheNumberPastEveryNameItsStemEverHad(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	f := newPosterFixture(t)
	for _, d := range []client.Draft{
		{Drive: "prog", Lane: "supervisor", Kind: "stand-down", Text: "desk-4 stood down", To: []string{"desk-4"}, Successor: "desk-5"},
		{Drive: "prog", Lane: "owner-relay", Kind: "state", Text: "owner-relay up"},
	} {
		if _, err := client.Post(context.Background(), d); err != nil {
			t.Fatal(err)
		}
	}

	for lane, want := range map[string]string{"desk": "desk-6", "desk-2": "desk-2", "owner-relay": "owner-relay", "fresh": "fresh-1"} {
		if got, err := f.poster.Numbered(context.Background(), lane); err != nil || got != want {
			t.Fatalf("Numbered(%s) = %q, %v; want %q", lane, got, err, want)
		}
	}
}

func TestALaunchFromAStoodDownLanesBriefRunsACopyRenamedForTheNewLane(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	f := newPosterFixture(t)
	for _, d := range []client.Draft{
		{Drive: "prog", Lane: "next-tests-servers-r-1", Kind: "state", Text: "next-tests-servers-r-1 running"},
		{Drive: "prog", Lane: "supervisor", Kind: "stand-down", Text: "next-tests-servers-r-1 stood down: the supervisor retired it (done).", To: []string{"next-tests-servers-r-1"}},
		{Drive: "prog", Lane: "live-lane-1", Kind: "state", Text: "live-lane-1 running"},
	} {
		if _, err := client.Post(context.Background(), d); err != nil {
			t.Fatal(err)
		}
	}
	retired := f.brief(t, "b/next-tests-servers-r-1/brief.md", "# next-tests-servers-r-1: shared test servers\nRegister slice: lane=next-tests-servers-r-1 step=all\nPost with `cci post --lane next-tests-servers-r-1`; next-tests-servers-r-10 is another lane.\n")
	brief, err := f.poster.Relaunched(context.Background(), "next-tests-servers-r-2", retired)
	if err != nil {
		t.Fatal(err)
	}
	if want := filepath.Join(f.dir, "prog", "next-tests-servers-r-2", briefFile); brief != want {
		t.Fatalf("brief %s, want %s", brief, want)
	}
	raw, err := os.ReadFile(brief)
	if err != nil {
		t.Fatal(err)
	}
	if want := "# next-tests-servers-r-2: shared test servers\nRegister slice: lane=next-tests-servers-r-2 step=all\nPost with `cci post --lane next-tests-servers-r-2`; next-tests-servers-r-10 is another lane.\n"; string(raw) != want {
		t.Fatalf("copied brief %q, want %q", raw, want)
	}
	if fault := (Order{Lane: "next-tests-servers-r-2", Brief: brief}).wrongLaneFault(); fault != "" {
		t.Fatal(fault)
	}

	live := f.brief(t, "b/live-lane-1/brief.md", "Register slice: lane=live-lane-1 step=all\n")
	kept, err := f.poster.Relaunched(context.Background(), "live-lane-2", live)
	if err != nil || kept != live {
		t.Fatalf("a live lane's brief moved to %s, %v", kept, err)
	}
	if fault := (Order{Lane: "live-lane-2", Brief: kept}).wrongLaneFault(); fault == "" {
		t.Fatal("live-lane-2 launched from live-lane-1's brief")
	}
}

func TestANumberedLaneLaunchesFromItsStemsBriefDirectory(t *testing.T) {
	if fault := (Order{Lane: "desk-6", Brief: "/s/desk/brief.md"}).wrongLaneFault(); fault != "" {
		t.Fatal(fault)
	}
	if fault := (Order{Lane: "desk-6", Brief: "/s/other/brief.md"}).wrongLaneFault(); fault == "" {
		t.Fatal("desk-6 launched from other's brief")
	}
}

func TestAnIncidentBriefInAnotherLanesDirectoryIsRefusedNamingItsLaunch(t *testing.T) {
	for _, name := range []string{"inc-x", "inc-x-fix"} {
		faults, err := wrongLaneFaults(incidentOrders(name, "/s/other/brief.md", "/s/inc-x-evidence/brief.md", "/s/inc-x-comms/brief.md"))
		if err != nil {
			t.Fatal(err)
		}
		if want := []string{"brief /s/other/brief.md is lane other's, and a lane keeps one name from launch on; post `orca-desk: launch other NOW incident high role=fix brief=/s/other/brief.md`"}; !slices.Equal(faults, want) {
			t.Fatal(faults)
		}
	}
}

func TestEachLinePostsWithNoKeyOfItsOwnAfterReadingTheRunnerAndPrintsItsRecord(t *testing.T) {
	cases := []struct {
		line func([]string) (string, string)
		args []string
		want string
	}{
		{relayLine, []string{"lane-a,lane-b", "restack onto dev"}, "orca-desk: relay to lane-a, lane-b: restack onto dev"},
		{holdLine, []string{"sandsql-5177", "root", "sand releases until #12 lands"}, "orca-desk: hold sandsql-5177 owner=root :: sand releases until #12 lands"},
		{unholdLine, []string{"sandsql-5177"}, "orca-desk: unhold sandsql-5177"},
		{withdrawLine, []string{"R2025"}, "orca-desk: withdraw R2025"},
	}
	for _, c := range cases {
		f := newPosterFixture(t)
		topic, text := c.line(c.args)
		if err := f.poster.Line(context.Background(), topic, text); err != nil {
			t.Fatal(err)
		}
		if !slices.Equal(f.texts(), []string{c.want}) || !slices.Equal(f.bus.calls, []string{"tail", "post"}) {
			t.Fatal(f.bus.posts, f.bus.calls)
		}
		if f.out.String() != "posted cci #102: "+c.want+"\n" {
			t.Fatal(f.out)
		}
		if topic != strings.Split(c.args[0], ",")[0] {
			t.Fatal(topic)
		}
	}
}

func TestADriveTheRegistryLacksPostsNothing(t *testing.T) {
	f := newPosterFixture(t)
	f.env[sessionEnv] = "s2"
	f.poster.Runner = cciMe("monorepo-8a lane lane=root busy now")
	err := f.poster.Line(context.Background(), "x", "unhold x")
	if err == nil || !strings.Contains(err.Error(), "no drive in the registry is bound to session s2 and cci me names none") || len(f.bus.calls) != 0 {
		t.Fatal(err)
	}
	f.poster.Drive = "elsewhere"
	err = f.poster.Line(context.Background(), "x", "unhold x")
	if err == nil || !strings.Contains(err.Error(), "no drive in the registry is cci drive elsewhere") || len(f.bus.calls) != 0 {
		t.Fatal(err)
	}
}

func TestADriveWhoseRunnerIsNotBeatingRefusesInOneLineNamingWhatToDoInstead(t *testing.T) {
	stale := aliveRecord(time.Now().Add(-6 * time.Minute))
	for _, last := range []*client.Record{nil, &stale} {
		f := newPosterFixture(t)
		f.bus.records = f.bus.records[1:]
		if last != nil {
			f.bus.records = slices.Insert(f.bus.records, 0, *last)
		}
		check := func(err error, instead string) {
			t.Helper()
			if err == nil || !strings.HasPrefix(err.Error(), noRunner) || !strings.HasSuffix(err.Error(), "nothing reads orca-desk lines; "+instead) || strings.Contains(err.Error(), "\n") {
				t.Fatal(err)
			}
			if strings.Contains(err.Error(), "(never)") != (last == nil) {
				t.Fatal(err)
			}
		}
		check(f.poster.Line(context.Background(), "lane-a", "relay to lane-a: go"), nothingPosted)
		brief := f.brief(t, "b.md", "work\n"+briefLines)
		_, err := f.poster.Launch(context.Background(), Order{Lane: "solo", Model: "opus", Effort: "high", Brief: brief}, 0)
		check(err, "spawn a background Agent (run_in_background) named `solo` instead")
		if len(f.bus.posts) != 0 {
			t.Fatal(f.bus.posts)
		}
	}
}

func TestALongPassPostsALineAndAStuckOneRefusesIt(t *testing.T) {
	beating := func(running time.Duration) client.Record {
		record := aliveRecord(time.Now())
		record.Text = "orca alive pass 3"
		record.Fields.Attrs = map[string]string{incident.PassSecondsAttr: fmt.Sprint(int(running.Seconds())), incident.PassStepAttr: "deliver"}
		return record
	}
	long := newPosterFixture(t)
	long.bus.records = slices.Insert(long.bus.records[1:], 0, beating(8*time.Minute))
	if err := long.poster.Line(context.Background(), "lane-a", "relay to lane-a: go"); err != nil {
		t.Fatal(err)
	}
	stuck := newPosterFixture(t)
	stuck.bus.records = slices.Insert(stuck.bus.records[1:], 0, beating(incident.StuckPass))
	err := stuck.poster.Line(context.Background(), "lane-a", "relay to lane-a: go")
	if err == nil || err.Error() != "refused: the orca desk runner on cci drive prog is alive but stuck: pass 3 has run 20 minutes, at step deliver, so nothing reads orca-desk lines; "+nothingPosted {
		t.Fatal(err)
	}
	if len(stuck.bus.posts) != 0 {
		t.Fatal(stuck.bus.posts)
	}
}

func TestARunnerThatIsAliveButGetsNoAnswerFromOrcaRefusesAtOnceAndNamesTheAgentFallback(t *testing.T) {
	f := newPosterFixture(t)
	beat := aliveRecord(time.Now())
	beat.Fields.Attrs = map[string]string{incident.OrcaDownAttr: "runtime_timeout"}
	f.bus.records = slices.Insert(f.bus.records[1:], 0, beat)
	brief := f.brief(t, "b.md", "work\n"+briefLines)
	_, err := f.poster.Launch(context.Background(), Order{Lane: "solo", Model: "opus", Effort: "high", Brief: brief}, 0)
	want := "refused: the orca desk runner on cci drive prog is alive but Orca does not answer it: runtime_timeout, so nothing reads orca-desk lines; spawn a background Agent (run_in_background) named `solo` instead"
	if err == nil || err.Error() != want {
		t.Fatalf("got %v, want %s", err, want)
	}
	if len(f.bus.posts) != 0 {
		t.Fatal(f.bus.posts)
	}
}

func TestALaunchWarnsOfALaneAlreadyClaimingItsWorkAndClaimsItOnceLaunched(t *testing.T) {
	f := newPosterFixture(t)
	brief := f.brief(t, "beside/brief.md", "Fix https://app.withforge.com/iris/sessions/sess_01m6efkn56e9krzhaa2fwtqmh0 from https://acme.slack.com/archives/C0909AD1458/p1793733828430639\n"+briefLines)
	runner := &scripted{reply: func(argv []string) (string, string, int) {
		if joined(argv) == "cci who --drive prog --ref thread:C0909AD1458/1793733828.430639 --json --fields lane" {
			return "{\"lane\":\"shared-open-fix\"}\n{\"lane\":\"beside\"}\n", "", 0
		}
		return "", "", 0
	}}
	f.poster.Runner = runner
	f.bus.verdicts["R102"] = "LAUNCHED R102 %[1]s: dispatch ctx_l terminal term_l"
	label, err := f.poster.Launch(context.Background(), Order{Lane: "beside", Model: "opus", Effort: "high", Brief: brief}, time.Minute)
	if err != nil || label != verdictLaunched {
		t.Fatal(label, err)
	}
	if !strings.Contains(f.out.String(), "WARN shared-open-fix already claims thread:C0909AD1458/1793733828.430639, so beside launches beside it;") || strings.Count(f.out.String(), "WARN ") != 1 {
		t.Fatal(f.out)
	}
	if last := joined(runner.calls[len(runner.calls)-1]); last != "cci claim --for beside@prog --ref session:sess_01m6efkn56e9krzhaa2fwtqmh0 --ref thread:C0909AD1458/1793733828.430639" {
		t.Fatal(last)
	}
}

func TestABriefClaimingNoSlackGrantWarnsUnlessItQuotesAnAnswerSearch(t *testing.T) {
	for text, warns := range map[string]bool{
		"Root cannot post and no grant covers the thread.\n":                                           true,
		"No Slack grant covers it. Run `ccn answer search <topic>` first.\n":                           true,
		"No Slack grant covers it: `ccn answer search slack-grant` returns nothing for C0909AD1458.\n": false,
		"Every Slack write needs the owner's permission.\n":                                            false,
	} {
		f := newPosterFixture(t)
		brief := f.brief(t, "grant/brief.md", text+briefLines)
		f.bus.verdicts["R102"] = "LAUNCHED R102 %[1]s: dispatch ctx_l terminal term_l"
		if _, err := f.poster.Launch(context.Background(), Order{Lane: "grant", Model: "opus", Effort: "high", Brief: brief}, time.Minute); err != nil {
			t.Fatal(err)
		}
		if got := strings.Contains(f.out.String(), "WARN brief "+brief+" says no Slack grant covers its work"); got != warns || len(f.bus.posts) != 1 {
			t.Fatalf("%q: warned %v, posts %d\n%s", text, got, len(f.bus.posts), f.out)
		}
	}
}

func TestAPosterWritesAsTheLaneBoundToItsSession(t *testing.T) {
	t.Setenv(lanes.LaneEnv, "meta-desk")
	cmd := &cobra.Command{}
	cmd.SetContext(context.Background())

	if got := (&posterFlags{}).author(cmd, &app.App{}); got != "meta-desk" {
		t.Fatalf("author = %q, want the bound lane", got)
	}
	if got := (&posterFlags{lane: "named"}).author(cmd, &app.App{}); got != "named" {
		t.Fatalf("author = %q, want the named lane", got)
	}
}
