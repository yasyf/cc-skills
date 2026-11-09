package landing_test

import (
	"context"
	"fmt"
	"io"
	"log/slog"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bkread"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/landing"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	owedSHA    = "abc1234567890abc1234567890abc1234567890a"
	owedTool   = "/co/tools/ci"
	owedBuild  = "https://buildkite.com/forge/release/builds/12305"
	fastLabel  = `{"name":"merge-fast"}`
	oneLanding = monorepo + "#7"
)

type owedFixture struct {
	owed   *landing.ReleaseOwed
	store  *store.Store
	fake   *shelltest.Fake
	merged time.Time
	now    time.Time
	clocks []string
}

func newOwed(t *testing.T, lane, labels, body string, files ...string) *owedFixture {
	t.Helper()
	t.Setenv("CCI_HOME", t.TempDir())
	s, err := store.Memory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	merged := time.Now().Add(-time.Minute).UTC().Truncate(time.Second)
	if _, err := s.WantLane(store.Lane{Name: "release-5", State: store.LaneDesired}, merged); err != nil {
		t.Fatal(err)
	}
	if err := s.Advance("release-owed", merged.Add(-time.Hour).Unix()); err != nil {
		t.Fatal(err)
	}
	if err := s.PutPR(store.PR{Repo: monorepo, Number: 7, Head: owedSHA, Base: "dev", Lane: lane, State: machine.StateLanded, ReadAt: merged}); err != nil {
		t.Fatal(err)
	}
	fake := &shelltest.Fake{}
	paths := make([]string, 0, len(files))
	for _, file := range files {
		paths = append(paths, fmt.Sprintf(`{"path":%q}`, file))
	}
	fake.Reply(fmt.Sprintf(`{"body":%q,"files":[%s],"labels":[%s],"mergedAt":%q,"mergeCommit":{"oid":%q}}`, body, strings.Join(paths, ","), labels, merged.Format(time.RFC3339), owedSHA), "ccx", "vcs", "gh", "--max-age", "0s", "--", "pr", "view")
	f := &owedFixture{fake: fake, store: s, merged: merged, now: merged}
	f.owed = f.instance(t, merged.Add(-time.Minute))
	return f
}

func (f *owedFixture) instance(t *testing.T, started time.Time) *landing.ReleaseOwed {
	t.Helper()
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	clock := func() time.Time { return f.now }
	f.fake.Reply("read-token\n", tokenTool)
	return &landing.ReleaseOwed{
		Runner: f.fake, CCX: ccx.CLI{Runner: f.fake}, Store: f.store, Drive: "d", Repo: monorepo, Owner: "release-5", CLI: []string{owedTool, "release"}, Token: bkread.Token{Command: []string{tokenTool}}, Checkout: "/co",
		Every: 10 * time.Minute, Urgent: 3 * time.Minute, Cut: 4 * time.Minute, Started: started, Now: clock, Log: logger,
		LiveClocks: func() ([]string, error) { return f.clocks, nil },
		Squash:     &landing.Squash{Runner: f.fake, CCX: ccx.CLI{Runner: f.fake}, Store: f.store, Drive: "d", Trunk: "dev", FixLane: func(string) (bool, error) { return false, nil }, Now: clock, Log: logger},
	}
}

func (f *owedFixture) at(t *testing.T, after time.Duration) {
	t.Helper()
	f.now = f.merged.Add(after)
	if err := f.owed.Pass(context.Background()); err != nil {
		t.Fatal(err)
	}
}

func owedPosted(t *testing.T, kinds ...string) []client.Record {
	t.Helper()
	records, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: kinds, Lanes: []string{"supervisor"}})
	if err != nil {
		t.Fatal(err)
	}
	return records
}

func owedBlockers(t *testing.T, opening string) []client.Record {
	t.Helper()
	return slices.DeleteFunc(owedPosted(t, "blocker"), func(record client.Record) bool { return !strings.Contains(record.Text, opening) })
}

func owedPost(t *testing.T, draft client.Draft) client.Record {
	t.Helper()
	draft.Drive = "d"
	posted, err := client.Post(context.Background(), draft)
	if err != nil {
		t.Fatal(err)
	}
	return posted
}

func owedAlert(t *testing.T) {
	t.Helper()
	owedPost(t, client.Draft{Lane: "root", Kind: "incident", To: []string{"incident-api-fix"}, Text: "api is down"})
}

func (f *owedFixture) register(t *testing.T, name string, state store.LaneState, retiredBy string) {
	t.Helper()
	if _, err := f.store.WantLane(store.Lane{Name: name, State: store.LaneDesired}, f.merged); err != nil {
		t.Fatal(err)
	}
	if err := f.store.SetLaneState(name, state, f.merged, retiredBy, ""); err != nil {
		t.Fatal(err)
	}
}

func TestAMergeFastLandingPressesItsOwnerUntilARecordNamesIt(t *testing.T) {
	f := newOwed(t, "lane-1", fastLabel, "text\nRelease-With: api datadog\n")

	f.at(t, 0)
	f.at(t, time.Minute)
	first := owedPosted(t, "release")
	if len(first) != 1 || !slices.Equal(first[0].To, []string{"release-5", "root"}) || !strings.Contains(first[0].Text, "Start the api datadog release now") {
		t.Fatalf("release records = %+v", first)
	}
	if got := owedPosted(t, "blocker"); len(got) != 0 {
		t.Fatalf("blocked before its step: %+v", got)
	}

	f.at(t, 11*time.Minute)
	pressed := owedBlockers(t, "landed 11 minutes ago")
	if len(pressed) != 1 || !strings.Contains(pressed[0].Text, "repeats every 10 minutes") {
		t.Fatalf("blockers = %+v", pressed)
	}

	owedPost(t, client.Draft{Lane: "release-5", Kind: "release", PRs: []string{oneLanding}, Text: "started api"})
	before := len(owedPosted(t, "blocker"))
	f.at(t, 21*time.Minute)
	if got := owedPosted(t, "blocker"); len(got) != before {
		t.Fatalf("pressed past a release record: %+v", got)
	}
}

func TestALandingThatOwesNoReleaseDrawsNothing(t *testing.T) {
	for name, c := range map[string]struct{ labels, body string }{
		"release-with alone":     {"", "Release-With: api"},
		"merge-fast naming none": {fastLabel, "no line here"},
		"another label":          {`{"name":"other"}`, ""},
	} {
		t.Run(name, func(t *testing.T) {
			f := newOwed(t, "lane-1", c.labels, c.body)
			f.at(t, 0)
			if got := owedPosted(t, "release", "blocker"); len(got) != 0 {
				t.Fatalf("records = %+v", got)
			}
		})
	}
}

func TestAnIncidentFixOwesItsReleaseAndOnlyABuildOrFixLiveSettlesIt(t *testing.T) {
	f := newOwed(t, "incident-api-fix", "", "no release line")
	owedAlert(t)

	f.at(t, 0)
	if got := owedPosted(t, "release"); len(got) != 1 || !strings.Contains(got[0].Text, "as an incident fix") || !strings.Contains(got[0].Text, "--build <url>") {
		t.Fatalf("release records = %+v", got)
	}

	owedPost(t, client.Draft{Lane: "landing-desk-3", Kind: "release", PRs: []string{oneLanding}, Text: "looking at it"})
	f.at(t, 11*time.Minute)
	if got := owedPosted(t, "blocker"); len(got) != 1 {
		t.Fatalf("a release record with no build settled an incident landing: %+v", got)
	}

	owedPost(t, client.Draft{Lane: "release-5", Kind: "fix-live", PRs: []string{oneLanding}, Text: "live"})
	f.at(t, 21*time.Minute)
	if got := owedPosted(t, "blocker"); len(got) != 1 {
		t.Fatalf("pressed past fix-live: %+v", got)
	}
}

func TestAnIncidentFixThatChangesOnlyTestsOwesNoRelease(t *testing.T) {
	f := newOwed(t, "incident-api-fix", "", "no release line", "go/sand/runner/upload_test.go")
	owedAlert(t)

	f.at(t, 0)
	f.at(t, 11*time.Minute)
	if got := owedPosted(t, "release", "blocker"); len(got) != 0 {
		t.Fatalf("a test-only incident landing was pressed for a release: %+v", got)
	}
}

func TestALiveIncidentClockPressesEveryLandingOnTheShortStep(t *testing.T) {
	f := newOwed(t, "lane-1", fastLabel, "Release-With: api")
	f.clocks = []string{"incident-other-fix"}

	f.at(t, 0)
	f.at(t, 4*time.Minute)

	if got := owedBlockers(t, "repeats every 3 minutes"); len(got) != 1 {
		t.Fatalf("blockers = %+v", owedPosted(t, "blocker"))
	}
}

func TestACoordinatorReplySettlesAMergeFastLandingButNotAnIncidentOne(t *testing.T) {
	for name, c := range map[string]struct {
		lane, labels string
		stops        bool
	}{
		"merge-fast": {"lane-1", fastLabel, true},
		"incident":   {"incident-api-fix", "", false},
	} {
		t.Run(name, func(t *testing.T) {
			f := newOwed(t, c.lane, c.labels, "Release-With: api")
			owedAlert(t)
			f.at(t, 0)
			first := owedPosted(t, "release")[0]
			owedPost(t, client.Draft{Lane: "root", Kind: "answer", To: []string{"supervisor"}, Re: first.Seq, Text: "noted"})
			f.at(t, 11*time.Minute)
			if got := len(owedPosted(t, "blocker")) == 0; got != c.stops {
				t.Fatalf("stopped = %v, want %v", got, c.stops)
			}
		})
	}
}

func TestAnOwnerAddressedRecordNamingTheCommitSettlesTheLanding(t *testing.T) {
	f := newOwed(t, "lane-1", fastLabel, "Release-With: api")
	f.at(t, 0)

	owedPost(t, client.Draft{Lane: "landing-desk-3", Kind: "note", To: []string{"release-5"}, Text: "abc1234 is in your next cut"})
	f.at(t, 11*time.Minute)

	if got := owedPosted(t, "blocker"); len(got) != 0 {
		t.Fatalf("blockers = %+v", got)
	}
}

func TestALandingFromBeforeThePortWentLiveOwesNothing(t *testing.T) {
	f := newOwed(t, "lane-1", fastLabel, "Release-With: api")
	if err := f.store.Advance("release-owed", f.merged.Add(time.Hour).Unix()); err != nil {
		t.Fatal(err)
	}

	f.at(t, 11*time.Minute)

	if got := owedPosted(t, "release", "blocker"); len(got) != 0 {
		t.Fatalf("records = %+v", got)
	}
}

func TestTheFirstPassOnlySetsTheStart(t *testing.T) {
	f := newOwed(t, "lane-1", fastLabel, "Release-With: api")
	fresh, err := store.Memory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = fresh.Close() })
	f.owed.Store = fresh

	f.at(t, 0)

	position, err := fresh.Cursor("release-owed")
	if err != nil {
		t.Fatal(err)
	}
	if position != f.merged.Unix() {
		t.Fatalf("cursor = %d, want %d", position, f.merged.Unix())
	}
}

func TestARestartNeitherRereadsTheLandingNorPostsItsStepAgain(t *testing.T) {
	f := newOwed(t, "lane-1", fastLabel, "Release-With: api")
	f.at(t, 0)

	f.owed = f.instance(t, f.merged.Add(-time.Minute))
	f.at(t, time.Minute)

	views := 0
	for _, call := range f.fake.Calls() {
		if slices.Contains(call.Argv, "view") {
			views++
		}
	}
	if views != 1 {
		t.Fatalf("pr views = %d", views)
	}
	if got := owedPosted(t, "release", "blocker"); len(got) != 1 {
		t.Fatalf("records = %+v", got)
	}
}

func TestALandingBeforeTheStartThatAlreadyShippedOwesNothing(t *testing.T) {
	f := newOwed(t, "lane-1", fastLabel, "Release-With: api")
	f.owed.Started = f.merged.Add(time.Minute)
	f.fake.Reply(`[{"target":"web","commit":"other"},{"target":"api","commit":"later"}]`, owedTool, "release", "live", "--json")
	f.fake.Reply("", "git", "-C", "/co", "merge-base", "--is-ancestor", owedSHA, "later")

	f.at(t, 0)

	if got := owedPosted(t, "release", "blocker"); len(got) != 0 {
		t.Fatalf("records = %+v", got)
	}
}

func TestAnOpenRootHoldAfterAnIncidentLandingDrawsOneLineThatARootReplyCloses(t *testing.T) {
	f := newOwed(t, "incident-api-fix", "", "Release-With: api")
	owedAlert(t)
	f.at(t, 0)
	owedPost(t, client.Draft{Lane: "root", Kind: "hold", To: []string{"release-5"}, Text: "hold the api cut"})

	f.at(t, time.Minute)
	f.at(t, 2*time.Minute)

	held := owedBlockers(t, "RELEASE-HELD-AFTER-LANDING #7")
	if len(held) != 1 {
		t.Fatalf("held lines = %+v", owedPosted(t, "blocker"))
	}

	owedPost(t, client.Draft{Lane: "root", Kind: "answer", To: []string{"supervisor"}, Resolves: held[0].Seq, Text: "cut it"})
	before := len(owedPosted(t, "blocker"))
	f.at(t, 21*time.Minute)
	if got := owedPosted(t, "blocker"); len(got) != before {
		t.Fatalf("pressed past root's reply: %+v", got)
	}
}

func TestASilentCutDrawsOneLineWithItsHandPathUnlessRefused(t *testing.T) {
	for name, refuses := range map[string]bool{"silent": false, "refused": true} {
		t.Run(name, func(t *testing.T) {
			f := newOwed(t, "lane-1", fastLabel, "Release-With: api")
			f.at(t, 0)
			if refuses {
				owedPost(t, client.Draft{Lane: "release-5", Kind: "refuse", Text: "api is mid-cut"})
			}
			f.at(t, 5*time.Minute)
			f.at(t, 6*time.Minute)

			silent := owedBlockers(t, "CUT-SILENT")
			if refuses && len(silent) != 0 {
				t.Fatalf("a refusal still drew %+v", silent)
			}
			if !refuses && (len(silent) != 1 || !strings.Contains(silent[0].Text, "CUT-SILENT release-5 api") || !strings.Contains(silent[0].Text, owedTool+" release start --manual api --env <env> --commit "+owedSHA+" --emergency")) {
				t.Fatalf("silent = %+v", silent)
			}
		})
	}
}

func TestASettledBuildPostsReleasedOnceEveryComponentIsLive(t *testing.T) {
	f := newOwed(t, "lane-1", fastLabel, "Release-With: api")
	value := fmt.Sprintf(`{"sha":%q,"components":["api"],"landed":%q,"posted":{"0":1},"settled":9,"builds":[%q]}`, owedSHA, f.merged.Format(time.RFC3339), owedBuild)
	if _, err := f.store.PutFact(monorepo, 7, owedSHA, "release-owed", value, f.merged); err != nil {
		t.Fatal(err)
	}
	f.fake.Reply(`{"api":{"live":"not live"}}`, owedTool, "release", "live", "12305", "--json")
	f.at(t, time.Minute)
	for _, call := range f.fake.Calls() {
		if slices.Contains(call.Argv, "post") {
			t.Fatalf("released before it was live: %v", call.Argv)
		}
	}

	f.fake = &shelltest.Fake{}
	f.owed = f.instance(t, f.merged.Add(-time.Minute))
	f.fake.Reply(`{"api":{"live":"v42"}}`, owedTool, "release", "live", "12305", "--json")
	f.fake.Reply(`[{"target":"api","env":"core-usw2-auto","commit":"later","build":"`+owedBuild+`"}]`, owedTool, "release", "live", "--json")
	f.fake.Reply("", "cci", "post")
	f.at(t, 2*time.Minute)
	f.at(t, 3*time.Minute)

	posts := cciPosts(f.fake)
	if len(posts) != 1 || !strings.Contains(posts[0], "--kind fix-live --to root --id release:7:live") || !strings.Contains(posts[0], "--target api") || !strings.Contains(posts[0], "--build "+owedBuild) || !strings.HasSuffix(posts[0], "--text RELEASED api v42 ("+owedBuild+")") {
		t.Fatalf("posts = %v", posts)
	}
}

func TestAFixLiveNamesTheBuildThatDeployedTheHeadNotACancelledOneThatCarriedIt(t *testing.T) {
	const cancelled, deployed, staging = "https://buildkite.com/forge/release/builds/13320", "https://buildkite.com/forge/release/builds/13332", "https://buildkite.com/forge/release/builds/13322"
	f := newOwed(t, "lane-1", fastLabel, "Release-With: rsw-go datadog")
	value := fmt.Sprintf(`{"sha":%q,"components":["rsw-go","datadog"],"landed":%q,"posted":{"0":1},"settled":9,"builds":[%q]}`, owedSHA, f.merged.Format(time.RFC3339), cancelled)
	if _, err := f.store.PutFact(monorepo, 7, owedSHA, "release-owed", value, f.merged); err != nil {
		t.Fatal(err)
	}
	f.fake.Reply(`{
		"rsw-go":{"live":"8603b7cd1760","clusters":[{"env":"plat-usw2-prod","commit":"7f33c8d2252e"},{"env":"tnt-usw2-0ddq7rb","commit":"7f33c8d2252e"}]},
		"datadog":{"live":"8603b7cd1760","clusters":[{"env":"plat-usw2-prod","commit":"7f33c8d2252e"}]},
		"identity":{"live":"not live","clusters":[{"env":"plat-usw2-prod","commit":"37a3e2866bfe","behind":true}]}
	}`, owedTool, "release", "live", "13320", "--json")
	f.fake.Reply(`[
		{"target":"rsw-go","env":"plat-usw2-prod","commit":"7f33c8d2252e4056","build":"`+deployed+`"},
		{"target":"rsw-go","env":"plat-usw2-staging","commit":"3bf223518eae","build":"`+staging+`"},
		{"target":"rsw-go","env":"tnt-usw2-0ddq7rb","commit":"7f33c8d2252e4056","build":"`+deployed+`"},
		{"target":"datadog","env":"plat-usw2-prod","commit":"7f33c8d2252e4056","build":"`+deployed+`"},
		{"target":"identity","env":"plat-usw2-prod","commit":"37a3e2866bfe","build":"https://buildkite.com/forge/release/builds/13290"}
	]`, owedTool, "release", "live", "--json")
	f.fake.Reply("", "cci", "post")

	f.at(t, 2*time.Minute)

	posts := cciPosts(f.fake)
	if len(posts) != 1 {
		t.Fatalf("posts = %v", posts)
	}
	if strings.Contains(posts[0], cancelled) || strings.Contains(posts[0], staging) || !strings.Contains(posts[0], "--build "+deployed+" --target rsw-go --target datadog") || !strings.HasSuffix(posts[0], "--text RELEASED rsw-go 8603b7cd1760, datadog 8603b7cd1760 ("+deployed+")") {
		t.Fatalf("the fix-live names another build than the one that deployed the head: %s", posts[0])
	}
}

func cciPosts(fake *shelltest.Fake) []string {
	var posts []string
	for _, call := range fake.Calls() {
		if len(call.Argv) > 1 && call.Argv[0] == "cci" {
			posts = append(posts, strings.Join(call.Argv, " "))
		}
	}
	return posts
}

func TestANagToARetiredOwnerReachesTheRoleHolder(t *testing.T) {
	for name, retiredBy := range map[string]string{"stood down for its successor": "release-6", "stood down with no successor": ""} {
		t.Run(name, func(t *testing.T) {
			f := newOwed(t, "incident-api-fix", "", "no release line")
			f.clocks = []string{"incident-api-fix"}
			owedAlert(t)
			f.register(t, "release-5", store.LaneRetired, retiredBy)
			f.register(t, "release-6", store.LaneDesired, "")

			f.at(t, 0)
			f.at(t, 4*time.Minute)

			nags := owedPosted(t, "release", "blocker")
			if len(nags) != 2 || !slices.ContainsFunc(nags, func(nag client.Record) bool { return nag.Kind == "blocker" }) {
				t.Fatalf("nags = %+v", nags)
			}
			for _, nag := range nags {
				if !slices.Equal(nag.To, []string{"release-6", "root"}) {
					t.Fatalf("nag %d went to %v", nag.Seq, nag.To)
				}
			}
		})
	}
}

func TestANagWithNoRunningHolderReachesRootAsUnowned(t *testing.T) {
	f := newOwed(t, "incident-api-fix", "", "no release line")
	f.clocks = []string{"incident-api-fix"}
	owedAlert(t)
	f.register(t, "release-5", store.LaneRetired, "")

	f.at(t, 0)

	nags := owedPosted(t, "release", "blocker")
	if len(nags) != 1 || !slices.Equal(nags[0].To, []string{"root"}) || !strings.HasPrefix(nags[0].Text, "UNOWNED, release-5 is not running: ") {
		t.Fatalf("nags = %+v", nags)
	}
}

func TestTheHoldersAnswerStopsTheNagAcrossARestart(t *testing.T) {
	f := newOwed(t, "incident-api-fix", "", "no release line")
	f.clocks = []string{"incident-api-fix"}
	owedAlert(t)
	f.register(t, "release-5", store.LaneRetired, "release-6")
	f.register(t, "release-6", store.LaneDesired, "")
	f.at(t, 0)
	f.at(t, 4*time.Minute)
	nag := owedBlockers(t, "landed 4 minutes ago")
	if len(nag) != 1 {
		t.Fatalf("blockers = %+v", owedPosted(t, "blocker"))
	}

	owedPost(t, client.Draft{Lane: "d-release-6", Kind: "answer", To: []string{"supervisor"}, Re: nag[0].Seq, Text: "no alert backs it; it rides the next cut"})
	f.at(t, 7*time.Minute)
	f.owed = f.instance(t, f.merged.Add(-time.Minute))
	f.at(t, 10*time.Minute)
	f.at(t, 13*time.Minute)

	if got := owedPosted(t, "blocker"); len(got) != 1 {
		t.Fatalf("nagged past the holder's answer: %+v", got)
	}
}

func TestAnIncidentLaneLandingWithNoLiveAlertIsOrdinaryLandingWork(t *testing.T) {
	f := newOwed(t, "incident-api-fix", "", "no release line")
	f.clocks = []string{"incident-api-fix"}

	f.at(t, 0)
	f.at(t, 4*time.Minute)

	if got := owedPosted(t, "release", "blocker"); len(got) != 0 {
		t.Fatalf("a landing no live alert backs drew the incident nag: %+v", got)
	}
}
