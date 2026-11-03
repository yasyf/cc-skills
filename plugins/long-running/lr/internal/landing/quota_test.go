package landing_test

import (
	"context"
	"testing"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/landing"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

const quotaRead = `[{"reads":{"pools":[
	{"identity":"user","resource":"core","limit":5000,"remaining":900,"resetAt":"2026-11-03T12:33:26Z","callers":[{"caller":"zsh","requests":94}]},
	{"identity":"user","resource":"graphql","limit":5000,"remaining":4955,"resetAt":"2026-11-03T13:12:43Z","callers":[]},
	{"identity":"user","resource":"search","limit":30,"remaining":3,"resetAt":"2026-11-03T12:18:47Z","callers":[]}
]},"writes":{"pools":[
	{"identity":"user","resource":"core","limit":5000,"remaining":900,"resetAt":"2026-11-03T12:33:26Z","callers":[{"caller":"zsh","requests":94}]},
	{"identity":"app","resource":"core","limit":15000,"remaining":12,"resetAt":"2026-11-03T12:40:00Z","callers":[]}
]}}]`

func TestAQuotaNearEmptyWarnsRootOncePerReset(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	ctx := context.Background()
	fake := &shelltest.Fake{}
	fake.Reply(quotaRead, "ccx", "vcs", "auth", "status", monorepo, "--json")
	quota := landing.Quota{Runner: fake, Drive: "d", Repo: monorepo}

	for range 2 {
		if err := quota.Pass(ctx); err != nil {
			t.Fatal(err)
		}
	}

	posted, err := client.Tail(ctx, client.Query{Drive: "d", Kinds: []string{"urgent"}})
	if err != nil {
		t.Fatal(err)
	}
	if len(posted) != 2 || posted[0].To[0] != "root" {
		t.Fatalf("%+v", posted)
	}
	want := []string{
		"GITHUB-QUOTA-LOW the user core quota has 900 of 5000 calls left until 4:33 AM; top callers: zsh 94",
		"GITHUB-QUOTA-LOW the app core quota has 12 of 15000 calls left until 4:40 AM; top callers: none recorded",
	}
	for i, record := range posted {
		if record.Text != want[i] {
			t.Errorf("got %q, want %q", record.Text, want[i])
		}
	}
}
