package lanes

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func TestAnOverrideRootWroteThatDoesNotParseComesBackOnce(t *testing.T) {
	fake := &fakeBus{tail: []client.Record{{Seq: 9, Lane: "root", Kind: "decide", At: t0, Text: "fidelity-override #47755 at its current head: plan 44"}}}
	check := &OverrideRefusals{Env: Env{Bus: fake, Config: config.Config{Drive: testDrive}}, Store: memory(t)}

	if err := check.Check(context.Background(), t0); err != nil {
		t.Fatal(err)
	}
	check.read = t0.Add(-overrideEvery)
	if err := check.Check(context.Background(), t0); err != nil {
		t.Fatal(err)
	}

	if len(fake.posted) != 1 || fake.posted[0].To[0] != "root" || !strings.HasPrefix(fake.posted[0].Text, "OVERRIDE-REFUSED cci #9 reads `fidelity-override #47755 at its current head: plan 44`") {
		t.Fatalf("posted = %+v", fake.posted)
	}
}

func TestAnOverrideWhoseQuoteDoesNotHoldWarnsRootOnceAndNeverRefuses(t *testing.T) {
	const override = "finder-override #47755 at abc1234: 174488. "
	for name, tc := range map[string]struct {
		text string
		stub func(*shelltest.Fake)
		want string
	}{
		"words he never said": {override + `answer 0000000, "anything"`, func(runner *shelltest.Fake) { runner.Reply("Go look into it.\n", "ccn") }, "does not say"},
		"a cut quote":         {override + `answer 0000000, "Go look into it"`, func(runner *shelltest.Fake) { runner.Reply("Go look into it now.\n", "ccn") }, "goes on after"},
		"ccn is down":         {override + `answer 0000000, "Go look into it"`, func(runner *shelltest.Fake) { runner.Fail(1, "ccn is down", "ccn") }, "could not read"},
		"no row id":           {override + `The grade says "plan names no field"`, func(*shelltest.Fake) {}, "with no row or answer key"},
	} {
		t.Run(name, func(t *testing.T) {
			fake := &fakeBus{tail: []client.Record{{Seq: 9, Lane: "root", Kind: "decide", At: t0, Text: tc.text}}}
			runner := &shelltest.Fake{}
			tc.stub(runner)
			check := &OverrideRefusals{Env: Env{Bus: fake, Runner: runner, Config: config.Config{Drive: testDrive}}, Store: memory(t)}
			registerDrive(t)

			if err := check.Check(context.Background(), t0); err != nil {
				t.Fatal(err)
			}
			check.read = t0.Add(-overrideEvery)
			if err := check.Check(context.Background(), t0); err != nil {
				t.Fatal(err)
			}

			if len(fake.posted) != 1 || fake.posted[0].Kind != "note" || !strings.HasPrefix(fake.posted[0].Text, "OVERRIDE-WARNING cci #9 still counts") || !strings.Contains(fake.posted[0].Text, tc.want) {
				t.Fatalf("posted = %+v", fake.posted)
			}
			if escalations, err := check.Store.Escalations(""); err != nil || len(escalations) != 0 {
				t.Fatalf("escalations = %+v, %v", escalations, err)
			}
		})
	}
}

func registerDrive(t *testing.T) {
	t.Helper()
	configDir := t.TempDir()
	t.Setenv("CLAUDE_CONFIG_DIR", configDir)
	if err := os.MkdirAll(filepath.Join(configDir, "long-running", "drives"), 0o750); err != nil {
		t.Fatal(err)
	}
	entry := fmt.Sprintf(`{"drive":%q,"checkout":"/repo","program":"p","sessions":["s"]}`, testDrive)
	if err := os.WriteFile(filepath.Join(configDir, "long-running", "drives", "d.json"), []byte(entry), 0o600); err != nil {
		t.Fatal(err)
	}
}

func TestAnOverrideThatParsesOrIsOnlyQuotedDrawsNoRefusal(t *testing.T) {
	for name, text := range map[string]string{
		"parses":         "finder-override #47755 at abc1234: 174488",
		"in a span":      "the lane asked for `fidelity-override #47755 at its head` and I refuse",
		"in double":      "it said \"fidelity-override #47755 at its head\" here",
		"in a quote":     "> fidelity-override #47755 at its head",
		"mid sentence":   "no fidelity-override #47755 at its head is owed",
		"names no grade": "the override stands",
	} {
		t.Run(name, func(t *testing.T) {
			fake := &fakeBus{tail: []client.Record{{Seq: 9, Lane: "root", Kind: "decide", At: t0, Text: text}}}
			check := &OverrideRefusals{Env: Env{Bus: fake, Config: config.Config{Drive: testDrive}}, Store: memory(t)}
			if err := check.Check(context.Background(), t0); err != nil {
				t.Fatal(err)
			}
			if len(fake.posted) != 0 {
				t.Fatalf("posted = %+v", fake.posted)
			}
		})
	}
}

func TestAnOverrideReachesThePRsOwningLaneOnce(t *testing.T) {
	fake := &fakeBus{tail: []client.Record{{Seq: 9, Lane: "root", Kind: "decide", At: t0, Text: "finder-override #47755 at abc1234: 174488"}}}
	s := memory(t)
	if _, err := s.WantLane(store.Lane{Name: "lane-1", State: store.LaneDesired}, t0); err != nil {
		t.Fatal(err)
	}
	if err := s.Own("lane-1", []int{47755}); err != nil {
		t.Fatal(err)
	}
	check := &OverrideRefusals{Env: Env{Bus: fake, Runner: &shelltest.Fake{}, Config: config.Config{Drive: testDrive}}, Store: s}
	registerDrive(t)

	for range 2 {
		check.read, check.quoted = time.Time{}, 0
		if err := check.Check(context.Background(), t0); err != nil {
			t.Fatal(err)
		}
	}

	var told []client.Draft
	for _, post := range fake.posted {
		if post.To[0] == "lane-1" {
			told = append(told, post)
		}
	}
	if len(told) != 1 || !strings.Contains(told[0].Text, "#47755 at abc1234 in cci #9") {
		t.Fatalf("told = %+v, want one note naming the PR, head and record", told)
	}
}

func TestAnOverrideOnAStoppedLanesPRReachesRootAsUnowned(t *testing.T) {
	fake := &fakeBus{tail: []client.Record{{Seq: 9, Lane: "root", Kind: "decide", At: t0, Text: "finder-override #47755 at abc1234: 174488"}}}
	s := memory(t)
	if err := s.Own("lane-1", []int{47755}); err != nil {
		t.Fatal(err)
	}
	check := &OverrideRefusals{Env: Env{Bus: fake, Runner: &shelltest.Fake{}, Config: config.Config{Drive: testDrive}}, Store: s}
	registerDrive(t)

	if err := check.Check(context.Background(), t0); err != nil {
		t.Fatal(err)
	}

	for _, post := range fake.posted {
		if strings.HasPrefix(post.Text, "UNOWNED, lane-1 is not running: Root overrode a grade on your #47755") && post.To[0] == Root {
			return
		}
	}
	t.Fatalf("posted = %+v", fake.posted)
}

func TestAnAppealCitingNoRowThatExistsGoesBackToItsLane(t *testing.T) {
	const override = "finder-override #47755 at abc1234: 174488. "
	for name, tc := range map[string]struct {
		text string
		stub func(*shelltest.Fake)
	}{
		"no such answer": {override + "Answer 1111111 grounds it", func(runner *shelltest.Fake) { runner.Fail(3, "not-found", "ccn") }},
		"no row at all":  {override + "The finding is wrong", func(*shelltest.Fake) {}},
	} {
		t.Run(name, func(t *testing.T) {
			fake := &fakeBus{tail: []client.Record{{Seq: 9, Lane: "grader-appeals-1", Kind: "decide", At: t0, Text: tc.text}}}
			runner := &shelltest.Fake{}
			tc.stub(runner)
			check := &OverrideRefusals{Env: Env{Bus: fake, Runner: runner, Config: config.Config{Drive: testDrive}}, Store: memory(t)}
			registerDrive(t)

			if err := check.Check(context.Background(), t0); err != nil {
				t.Fatal(err)
			}

			if len(fake.posted) != 1 || fake.posted[0].To[0] != "grader-appeals-1" || !strings.HasPrefix(fake.posted[0].Text, "OVERRIDE-REFUSED cci #9 cites no owner row that exists") {
				t.Fatalf("posted = %+v", fake.posted)
			}
		})
	}
}

func TestAnotherLanesDecideNamingAnOverrideDrawsNothing(t *testing.T) {
	fake := &fakeBus{tail: []client.Record{{Seq: 9, Lane: "lane-7", Kind: "decide", At: t0, Text: "finder-override #47755 at abc1234: 174488"}}}
	check := &OverrideRefusals{Env: Env{Bus: fake, Runner: &shelltest.Fake{}, Config: config.Config{Drive: testDrive}}, Store: memory(t)}

	if err := check.Check(context.Background(), t0); err != nil {
		t.Fatal(err)
	}

	if len(fake.posted) != 0 {
		t.Fatalf("posted = %+v", fake.posted)
	}
}

func TestAnAppealCitingARealRowIsCountedOnceByTheLanesMachine(t *testing.T) {
	fake := &fakeBus{tail: []client.Record{{Seq: 9, Lane: "grader-appeals-1", Kind: "decide", At: t0, Text: "finder-override #47755 at abc1234: 174488. Answer 7f54b1d grounds it"}}}
	runner := &shelltest.Fake{}
	runner.Reply("the owner's words\n", "ccn")
	check := &OverrideRefusals{Env: Env{Bus: fake, Runner: runner, Config: config.Config{Drive: testDrive}}, Store: memory(t)}
	registerDrive(t)

	if err := check.Check(context.Background(), t0); err != nil {
		t.Fatal(err)
	}
	check.read, check.quoted = t0.Add(-overrideEvery), 0
	if err := check.Check(context.Background(), t0); err != nil {
		t.Fatal(err)
	}

	if len(fake.posted) != 1 {
		t.Fatalf("posted = %+v, want one counted record", fake.posted)
	}
	counted := fake.posted[0]
	if counted.Lane != Self || counted.Kind != "decide" || !slices.Equal(counted.PRs, []string{"47755"}) || !strings.HasPrefix(counted.Text, "finder-override #47755 at abc1234: 174488. Counted from grader-appeals-1's cci #9") {
		t.Fatalf("counted = %+v", counted)
	}
}

func TestAnAppealRepostingAWithdrawnCiteIsRefusedAndNeverCounted(t *testing.T) {
	const repo = "org/repo"
	fake := &fakeBus{tail: []client.Record{{Seq: 9, Lane: "grader-appeals-3", Kind: "decide", At: t0, Text: "finder-override #47755 at abc1234: 174488. Answer 7f54b1d grounds it"}}}
	runner := &shelltest.Fake{}
	runner.Reply("the owner's words\n", "ccn")
	s := memory(t)
	if _, err := s.PutFact(repo, 47755, "abc1234", machine.WithdrawnOverrideFact("finder"), "174488", t0.Add(-time.Hour)); err != nil {
		t.Fatal(err)
	}
	check := &OverrideRefusals{Env: Env{Bus: fake, Runner: runner, Config: config.Config{Drive: testDrive, Repo: repo}}, Store: s}
	registerDrive(t)

	if err := check.Check(context.Background(), t0); err != nil {
		t.Fatal(err)
	}

	if len(fake.posted) != 1 || fake.posted[0].Kind != blockerKind || fake.posted[0].To[0] != "grader-appeals-3" || !strings.HasPrefix(fake.posted[0].Text, "OVERRIDE-REFUSED cci #9 reads `finder-override #47755 at abc1234: 174488`, which a withdraw took back") {
		t.Fatalf("posted = %+v, want one refusal and no counted copy", fake.posted)
	}
}

func TestACountedAppealToldToItsOwnerRaisesNoEscalation(t *testing.T) {
	fake := &fakeBus{tail: []client.Record{{Seq: 9, Lane: "grader-appeals-1", Kind: "decide", At: t0, Text: "finder-override #47755 at abc1234: 174488. Answer 7f54b1d grounds it"}}}
	runner := &shelltest.Fake{}
	runner.Reply("the owner's words\n", "ccn")
	s := memory(t)
	if _, err := s.WantLane(store.Lane{Name: "lane-1", State: store.LaneDesired}, t0); err != nil {
		t.Fatal(err)
	}
	if err := s.Own("lane-1", []int{47755}); err != nil {
		t.Fatal(err)
	}
	check := &OverrideRefusals{Env: Env{Bus: fake, Runner: runner, Config: config.Config{Drive: testDrive}}, Store: s}
	registerDrive(t)

	if err := check.Check(context.Background(), t0); err != nil {
		t.Fatal(err)
	}

	escalations, err := s.Escalations("")
	if err != nil {
		t.Fatal(err)
	}
	if len(fake.posted) != 2 || len(escalations) != 0 {
		t.Fatalf("posted = %+v, escalations = %+v, want the counted record and the owner's note and no escalation", fake.posted, escalations)
	}
}
