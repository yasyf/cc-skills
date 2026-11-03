package lanes

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
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

func TestAnOverrideQuotingAWordTheAnswerDoesNotHoldComesBackOnce(t *testing.T) {
	fake := &fakeBus{tail: []client.Record{{Seq: 9, Lane: "root", Kind: "decide", At: t0, Text: `finder-override #47755 at abc1234: 174488. answer 0000000, "anything"`}}}
	runner := &shelltest.Fake{}
	runner.Reply("Go look into it.\n", "ccn")
	check := &OverrideRefusals{Env: Env{Bus: fake, Runner: runner, Config: config.Config{Drive: testDrive}}, Store: memory(t)}
	registerDrive(t)

	if err := check.Check(context.Background(), t0); err != nil {
		t.Fatal(err)
	}
	check.read = t0.Add(-overrideEvery)
	if err := check.Check(context.Background(), t0); err != nil {
		t.Fatal(err)
	}

	if len(fake.posted) != 1 || fake.posted[0].To[0] != "root" || !strings.HasPrefix(fake.posted[0].Text, "OVERRIDE-REFUSED cci #9 settles no grade: answer 0000000 does not say") {
		t.Fatalf("posted = %+v", fake.posted)
	}
}

func TestAnOverrideWhoseQuoteStopsMidClauseOrCannotBeReadWarnsRoot(t *testing.T) {
	for name, tc := range map[string]struct {
		stub func(*shelltest.Fake)
		want string
	}{
		"a cut quote": {func(runner *shelltest.Fake) { runner.Reply("Go look into it now.\n", "ccn") }, "goes on after"},
		"ccn is down": {func(runner *shelltest.Fake) { runner.Fail(1, "ccn is down", "ccn") }, "could not read"},
	} {
		t.Run(name, func(t *testing.T) {
			fake := &fakeBus{tail: []client.Record{{Seq: 9, Lane: "root", Kind: "decide", At: t0, Text: `finder-override #47755 at abc1234: 174488. answer 0000000, "Go look into it"`}}}
			runner := &shelltest.Fake{}
			tc.stub(runner)
			check := &OverrideRefusals{Env: Env{Bus: fake, Runner: runner, Config: config.Config{Drive: testDrive}}, Store: memory(t)}
			registerDrive(t)

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
