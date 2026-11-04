package lanes

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
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
