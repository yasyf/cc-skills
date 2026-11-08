package reply

import (
	"context"
	"fmt"
	"slices"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	platy   = "https://brook.slack.com/archives/C0B75AL4XEW/p1794015824488459"
	otherPR = "https://github.com/yasyf/cc-skills/pull/3779"
)

func card(link string) string {
	return "[\u200b](" + link + ")"
}

func titled(r Reply) Reply {
	r.names = prNames{repo: "yasyf/cc-skills", titles: map[string]string{landPR: "Relay fix", otherPR: "the supervisor serves a newer pack"}, carded: []string{landPR}}
	return r
}

func TestTheSlackTextNamesEachPROnceByTitleAndDrawsChannelLinksAsTheirCardAlone(t *testing.T) {
	for name, tc := range map[string]struct {
		reply Reply
		want  string
	}{
		"a bare Platy link leaves the sentence": {
			Reply{MD: "Release 13655 converged at 6:02 PM (" + platy + "). The stuck-restore alert recovered."},
			"Release 13655 converged at 6:02 PM.\nThe stuck-restore alert recovered." + card(platy),
		},
		"a lead-in left introducing nothing goes": {
			Reply{MD: "SandSQL boots from its saved index. Platy card: " + platy},
			"SandSQL boots from its saved index." + card(platy),
		},
		"a lead-in ending its sentence drops its colon": {
			Reply{MD: "Ria gets one reply with the release card: " + platy + "."},
			"Ria gets one reply with the release card." + card(platy),
		},
		"a link on its own line leaves no line": {
			Reply{MD: "It is live.\n" + platy + "\nMacs update tonight."},
			"It is live.\nMacs update tonight." + card(platy),
		},
		"a vague label goes with its link": {
			Reply{MD: "13666 went live about 6:45 PM ([card](" + platy + ")): run search finds runs."},
			"13666 went live about 6:45 PM: run search finds runs." + card(platy),
		},
		"a worded label stays in the sentence": {
			Reply{MD: "The fix is in [the alert thread](" + platy + ") now."},
			"The fix is in the alert thread now." + card(platy),
		},
		"a table keeps its last row": {
			Reply{MD: "Release card: " + platy + "\n\n| check | state |\n|---|---|\n| lr-go | green |"},
			"| check | state |\n|---|---|\n| lr-go | green |\n" + card(platy),
		},
		"a DM card link keeps its label": {
			Reply{MD: "Answer on [the card](" + permalink + ")."},
			"Answer on [the card](" + permalink + ").",
		},
		"a PR named twice by number": {
			Reply{MD: "#2196 is in CI. #2196 lands after it.", PRs: []string{landPR}},
			"[Relay fix](" + landPR + ") is in CI.\nRelay fix lands after it.",
		},
		"a PR by its URL, then by repo and number": {
			Reply{MD: "Merged " + landPR + " and cc-skills#2196 again.", PRs: []string{landPR}},
			"Merged [Relay fix](" + landPR + ") and Relay fix again.",
		},
		"a link labeled with its number": {
			Reply{MD: "[#2196](" + landPR + ") is in CI."},
			"[Relay fix](" + landPR + ") is in CI.",
		},
		"a PR the text never names and cc-slack draws no card for": {
			Reply{MD: "Two fixes are open.", PRs: []string{landPR, otherPR}},
			"Two fixes are open.\n\n- [the supervisor serves a newer pack](" + otherPR + ")",
		},
		"a first line repeating the title": {
			Reply{Title: "Mac Sand 13680 is live", MD: "**Mac Sand 13680 is live**\nThe release is live at b37f603. Macs update tonight."},
			"The release is live at b37f603.\nMacs update tonight.",
		},
		"list items and inline code": {
			Reply{MD: "- one. Two\n`#2196` stays"},
			"- one. Two\n`#2196` stays",
		},
	} {
		t.Run(name, func(t *testing.T) {
			r := titled(tc.reply)
			if got := r.slackText(r.MD); got != tc.want {
				t.Fatalf("slack text = %q, want %q", got, tc.want)
			}
		})
	}
}

func TestAChannelLinkIsItsCardAloneInsteadOfAButtonOrALinkLine(t *testing.T) {
	fake := scripted()
	notebook := Link{Label: "Notebook", URL: "https://app.datadoghq.com/notebook/1"}
	fanout(t, fake).Send(context.Background(), Reply{ID: "r1", Title: "Release 13687 is live", MD: "It is live.", Status: done, Links: []Link{{Label: "Platy", URL: platy}, notebook}})
	posted := slack(t, calls(fake, "cc-slack")[0])
	if posted.Body != "It is live."+card(platy) || !slices.Equal(posted.Links, []Link{notebook}) {
		t.Fatalf("card = %+v, want the Platy post as its card alone and only the notebook a button", posted)
	}
}

func TestAThreadAnswerNamesItsPRByTitleOnceInSlackMarkup(t *testing.T) {
	fake := scripted()
	r := titled(Reply{ID: "r1", Title: "t", MD: "## Done\n[#2196](" + landPR + ") is in CI.", PRs: []string{landPR}, Thread: "1793684147.131059"})
	fanout(t, fake).Send(context.Background(), r)
	posted := calls(fake, "cc-slack")
	want := "*t*\n*Done*\n<" + landPR + "|Relay fix> is in CI."
	if len(posted) != 1 || posted[0].Argv[len(posted[0].Argv)-1] != want {
		t.Fatalf("cc-slack calls = %+v, want %q", posted, want)
	}
}

func TestPRTitlesReadInOneLedgerReadAndOneGitHubQueryAtAnyCount(t *testing.T) {
	for _, n := range []int{1, 100} {
		fake := scripted()
		fake.Reply(`{"data":{"p0":{"pullRequest":{"title":"cc-slack: 🐛 a fix"}}}}`, "gh", "api", "graphql")
		f := fanout(t, fake)
		ledgerRow(t, f, inCI)
		named := make([]string, n)
		for k := range named {
			named[k] = fmt.Sprintf("yasyf/cc-slack#%d", k+1)
		}
		r, warning := f.withPRTitles(context.Background(), Reply{MD: "#2196 and " + strings.Join(named, ", ") + " are open."})
		queries := slices.DeleteFunc(fake.Calls(), func(c shell.Command) bool { return c.Argv[0] != "gh" })
		if len(queries) != 1 || warning != "" {
			t.Fatalf("n=%d: gh calls %d, warning %q; want one query and no warning", n, len(queries), warning)
		}
		if r.names.titles[landPR] != "Relay fix" || r.names.titles["https://github.com/yasyf/cc-slack/pull/1"] != "a fix" || !slices.Contains(r.names.carded, landPR) {
			t.Fatalf("n=%d: names = %+v, want the ledger title, the GitHub title without its scope, and the PR in CI carded", n, r.names)
		}
	}
}

func TestABareNumberTheLedgerLacksAsksGitHubNothing(t *testing.T) {
	fake := scripted()
	f := fanout(t, fake)
	ledgerRow(t, f, inCI)
	r, warning := f.withPRTitles(context.Background(), Reply{MD: "Step #7 is done."})
	if gh := slices.DeleteFunc(fake.Calls(), func(c shell.Command) bool { return c.Argv[0] != "gh" }); len(gh) != 0 || warning != "" || len(r.names.titles) != 0 {
		t.Fatalf("gh calls %+v, warning %q, titles %+v; want no query for a number that may not be a PR", gh, warning, r.names.titles)
	}
	if got := r.slackText(r.MD); got != "Step #7 is done." {
		t.Fatalf("slack text = %q, want the number as written", got)
	}
}
