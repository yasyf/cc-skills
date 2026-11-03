package bkread_test

import (
	"context"
	"errors"
	"slices"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bkread"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

const (
	tokenTool = "print-read-token"
	ciTool    = "tools/ci"
	refusal   = `ci bk: GET /organizations/org/pipelines/release/builds failed: 401 {"message":"Authentication required."}`
)

var read = shell.Command{Argv: []string{ciTool, "bk", "api", "/organizations/org/pipelines/release/builds"}, Dir: "/co"}

func printing(token string) *shelltest.Fake {
	fake := &shelltest.Fake{}
	fake.Reply(token+"\n", tokenTool)
	fake.Reply("[]", ciTool)
	return fake
}

func reads(t *testing.T, fake *shelltest.Fake) (printed int, carried []string) {
	t.Helper()
	for _, call := range fake.Calls() {
		switch call.Argv[0] {
		case tokenTool:
			printed++
		case ciTool:
			carried = append(carried, strings.Join(call.Env, " "))
		}
	}
	return printed, carried
}

func TestEveryReadCarriesTheTokenOneCommandPrinted(t *testing.T) {
	token := bkread.Token{Command: []string{tokenTool}}
	fake := printing("first")

	for range 3 {
		if _, err := token.Run(context.Background(), fake, read); err != nil {
			t.Fatal(err)
		}
	}

	printed, carried := reads(t, fake)
	if printed != 1 || !slices.Equal(carried, []string{"BUILDKITE_TOKEN=first", "BUILDKITE_TOKEN=first", "BUILDKITE_TOKEN=first"}) {
		t.Fatalf("token printed %d times, reads carried %v", printed, carried)
	}
}

func TestARefusedTokenIsNamedAndTheNextReadPrintsAFreshOne(t *testing.T) {
	token := bkread.Token{Command: []string{tokenTool}}
	refused := &shelltest.Fake{}
	refused.Reply("stale\n", tokenTool)
	refused.Fail(1, refusal, ciTool)

	_, err := token.Run(context.Background(), refused, read)

	if !errors.Is(err, bkread.ErrRefused) || !strings.Contains(err.Error(), "failed: 401") {
		t.Fatalf("err = %v, want the auth failure named", err)
	}
	if strings.Contains(err.Error(), "stale") {
		t.Fatalf("the error carries the token: %v", err)
	}

	fake := printing("rotated")
	if _, err := token.Run(context.Background(), fake, read); err != nil {
		t.Fatal(err)
	}
	if printed, carried := reads(t, fake); printed != 1 || !slices.Equal(carried, []string{"BUILDKITE_TOKEN=rotated"}) {
		t.Fatalf("token printed %d times, reads carried %v", printed, carried)
	}
}

func TestAFailedReadThatIsNoRefusalKeepsTheToken(t *testing.T) {
	token := bkread.Token{Command: []string{tokenTool}}
	failing := &shelltest.Fake{}
	failing.Reply("kept\n", tokenTool)
	failing.Fail(1, "ci bk: GET failed: 502 bad gateway", ciTool)

	if _, err := token.Run(context.Background(), failing, read); err == nil || errors.Is(err, bkread.ErrRefused) {
		t.Fatalf("err = %v, want a plain read failure", err)
	}

	fake := printing("unused")
	if _, err := token.Run(context.Background(), fake, read); err != nil {
		t.Fatal(err)
	}
	if printed, carried := reads(t, fake); printed != 0 || !slices.Equal(carried, []string{"BUILDKITE_TOKEN=kept"}) {
		t.Fatalf("token printed %d times, reads carried %v", printed, carried)
	}
}

func TestNoUsableTokenRunsNoReadAndNamesTheSetting(t *testing.T) {
	for name, set := range map[string]struct {
		command []string
		reply   func(*shelltest.Fake)
		want    string
	}{
		"unset":   {nil, func(*shelltest.Fake) {}, "reading Buildkite needs landing.ci_token_command"},
		"failing": {[]string{tokenTool}, func(fake *shelltest.Fake) { fake.Fail(254, "the SSO session expired", tokenTool) }, "landing.ci_token_command failed: "},
		"empty":   {[]string{tokenTool}, func(fake *shelltest.Fake) { fake.Reply("\n", tokenTool) }, "landing.ci_token_command printed nothing"},
	} {
		t.Run(name, func(t *testing.T) {
			fake := &shelltest.Fake{}
			set.reply(fake)
			fake.Reply("[]", ciTool)
			token := bkread.Token{Command: set.command}

			_, err := token.Run(context.Background(), fake, read)

			if err == nil || !strings.Contains(err.Error(), set.want) {
				t.Fatalf("err = %v, want it to name %q", err, set.want)
			}
			if _, carried := reads(t, fake); len(carried) != 0 {
				t.Fatalf("read Buildkite with no token: %v", carried)
			}
		})
	}
}
