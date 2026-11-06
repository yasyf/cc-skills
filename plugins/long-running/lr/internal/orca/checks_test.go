package orca

import (
	"slices"
	"testing"
)

func TestABriefThatRunsADevboxCommandIsRefusedUnlessItLaunchesOnTheMacOrIsAnAlertFixLane(t *testing.T) {
	const brief = "/b/item-84-journals-1.md"
	const slice = "Register slice: lane=item-84-journals-1 step=1 topics=journals\n" +
		"- prompt/1102-103205 repro it on a devbox\n" +
		"  > run `AWS_PROFILE=\"$DEVBOX_AWS_PROFILE\" tools/ci devbox claim --gh` and reproduce there\n" +
		"slice-hash: 90e843fdfe89\n" +
		"Scale: no N\n"
	const repro = "2. Claim a devbox with `AWS_PROFILE=\"$DEVBOX_AWS_PROFILE\" tools/ci devbox claim --gh` and run the failing workflow on it.\n"
	item84 := Order{Lane: "item-84-journals-1", Model: "opus", Effort: "high", Brief: brief}
	alertFix := Order{Lane: "sentry-7831318608-fix", Model: agentIncident, Effort: "xhigh", Brief: brief}
	for name, tt := range map[string]struct {
		order   Order
		text    string
		refuses string
	}{
		"item-84-journals-1, with no place":             {item84, slice + repro, "ci devbox claim"},
		"item-84-journals-1, placed remote":             {item84, slice + "ccx: role=build place=remote incident=none\n" + repro, "ci devbox claim"},
		"item-84-journals-1, placed local with no need": {item84, slice + "ccx: role=build place=local\n" + repro, "ci devbox claim"},
		"item-84-journals-1, placed local why=devbox":   {item84, slice + "ccx: role=build place=local why=devbox\n" + repro, ""},
		"an EC2 Mac box in a fenced block":              {item84, slice + "Build on a Mac box:\n\n```sh\nAWS_PROFILE=\"$DEVBOX_AWS_PROFILE\" tools/ci mac-box up build-1\n```\n", "ci mac-box up"},
		"a Namespace devbox":                            {item84, slice + "Make one with `lr devbox create --drive d1 --lane item-84-journals-1 -- --platform linux/amd64 --size s`.\n", "devbox create"},
		"an alert's fix lane": {alertFix, "ccx: lane=sentry-7831318608-fix role=incident effort=xhigh incident=sentry-7831318608\n" +
			"Run devbox repros through `sentry-7831318608-evidence`; never run `lr devbox create`, `tools/ci devbox claim` or `ci mac-box up`.\n", ""},
		"a command only the slice quotes":     {item84, slice + "Write the regression test from the code.\n", ""},
		"a command only an owner line quotes": {item84, slice + "Owner 1:20 AM, verbatim: \"repro it with `tools/ci devbox claim --gh`\"\n", ""},
		"a command named with no verb":        {item84, slice + "`ci devbox` cannot claim a box from this Mac without a person; read the claim door in go/ci devbox server code.\n", ""},
	} {
		t.Run(name, func(t *testing.T) {
			var want []string
			if tt.refuses != "" {
				want = []string{"brief " + brief + " runs `" + tt.refuses + "`, which needs this Mac's AWS identity and devbox CLI, but it launches on a Sprite, which has neither; add `place=local why=devbox` to its ccx line, or, for an alert's fix lane, replace the command with a `devbox` ask to its evidence lane"}
			}
			if faults := tt.order.launchFaults(tt.text, true); !slices.Equal(faults, want) {
				t.Fatalf("faults %q, want %q", faults, want)
			}
		})
	}
}
