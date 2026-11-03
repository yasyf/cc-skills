package lanes

import (
	"context"
	"strings"
	"testing"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
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
