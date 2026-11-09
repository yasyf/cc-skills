package install

import (
	"context"
	"encoding/base64"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

func carryingChecks(t *testing.T, tags string, compare map[string]string) (Checks, *time.Time) {
	t.Helper()
	checks := cachedChecks(t, compare)
	fake := &shelltest.Fake{}
	fake.Reply(tags, "git", "ls-remote")
	now := time.Date(2026, 11, 9, 0, 0, 0, 0, time.UTC)
	checks.Runner, checks.Now = fake, func() time.Time { return now }
	return checks, &now
}

const pinPath = "api repos/o/r/contents/plugins/long-running/bin/lr.binrun?ref=long-running--v0.7.100"

var pinned = fmt.Sprintf(`{"content":%q}`, base64.StdEncoding.EncodeToString([]byte("#!/usr/bin/env binrun\n{\"version\":{\"static\":\"0.1394.1\"}}\n")))

func TestCarryingPicksTheNewestTagWhenTheLrItPinsHoldsTheCommit(t *testing.T) {
	tags := "a\trefs/tags/long-running--v0.7.99\nb\trefs/tags/long-running--v0.7.100\nc\trefs/tags/long-running--v0.7.98\n"
	checks, _ := carryingChecks(t, tags, map[string]string{pinPath: pinned, "api repos/o/r/compare/abc123...lr--v0.1394.1": `{"status":"ahead"}`})

	got, err := checks.Carrying(context.Background(), "abc123", func(time.Duration) { t.Fatal("slept with a carrying tag in hand") })

	if err != nil || got != "0.7.100" {
		t.Fatalf("Carrying = %q, %v; want 0.7.100", got, err)
	}
}

func TestCarryingWaitsAnHourForATagWhoseLrHoldsTheCommitAndThenRefuses(t *testing.T) {
	checks, now := carryingChecks(t, "a\trefs/tags/long-running--v0.7.100\n", map[string]string{pinPath: pinned, "api repos/o/r/compare/abc123...lr--v0.1394.1": `{"status":"behind"}`})
	slept := 0

	_, err := checks.Carrying(context.Background(), "abc123", func(d time.Duration) { slept++; *now = now.Add(d) })

	if err == nil || !strings.Contains(err.Error(), "no long-running tag carried abc123 within 1h0m0s") || slept != 60 {
		t.Fatalf("err = %v after %d sleeps; want a refusal after 60", err, slept)
	}
}
