package explain

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

const skewPage = "#384714 shows fleet users run 0.162.3396-3423, so 0.162.3410 (the FDE's chat writer) is a FLEET build, not next. chat-version-land-1's 'next build wrote it' is false. The evidence now says: a normal fleet sand on an FDE Mac writes chats at sessions migration 6 (dc94738, release 12280) and the old-tree cloud runner on plat reads only 0, so any FDE run that passes a chat to a deployed workflow crashes. That is a release skew between Mac sand and cloud runner, likely ours. No provenance pod-exec needed: the version is in the API row."

func TestUncitedNamesEachInferredMechanismSentenceAndPassesTheCitedOne(t *testing.T) {
	got := Uncited([]byte(skewPage))
	if len(got) != 2 || !strings.Contains(got[0], "writes chats at sessions migration 6") || !strings.HasPrefix(got[1], "That is a release skew") {
		t.Fatalf("Uncited = %q, want the migration 6 sentence and the release skew sentence", got)
	}
}

func TestUncitedPassesAMechanismThatCitesItsProofOrIsLabelledUnproven(t *testing.T) {
	for _, source := range []string{
		"The reader stops at migration 0 because #49304 has not shipped.",
		"The reader stops at migration 0 because build 12280 carries the old tree.",
		"Build 12280 carries the old tree, https://buildkite.com/forge/sand/builds/12280.\n\nSo every run on it fails at the first chat.",
		"The page flaps because the check reads `internal/vm/chat.go:88` before the write lands.",
		"- **Unproven:** a release skew between Mac sand and the cloud runner, likely ours.",
		"He said \"it broke because of the skew\" and asked for the record.",
		"```\nfails because of skew\n```",
		"The cloud runner reads chats at migration 0.",
		"The run crashed because the cloud runner reads only migration 0. <!-- cci #384714 -->",
		"The run crashed because the cloud runner reads only migration 0 <!-- cci #384714 -->.",
	} {
		if got := Uncited([]byte(source)); len(got) != 0 {
			t.Errorf("Uncited(%q) = %q, want none", source, got)
		}
	}
}

func TestUncitedSkipsALanesTextQuotedInBlockquotesAndLintsTheSameTextAsThePagesOwn(t *testing.T) {
	design := "Option 1 is recommended because it drops the copy. The fallback fails because the server copies the whole run."
	quoted := "The browser-run lane's design reads:\n\n> " + strings.ReplaceAll(design, ". ", ".\n> ") + "\n\nOption 1 is the pick."
	if got := Uncited([]byte(quoted)); len(got) != 0 {
		t.Errorf("Uncited(%q) = %q, want none", quoted, got)
	}
	if got := Uncited([]byte(design)); len(got) != 2 {
		t.Errorf("Uncited(%q) = %q, want both sentences", design, got)
	}
	if !strings.Contains(refuse([]string{design}).Error(), "Text quoted from another lane goes in `>` lines") {
		t.Errorf("the refusal never says how to quote another lane")
	}
}

func TestUncitedSkipsHeadingsAndTableRulesAndNeverReadsTheBareWordCauseAsAWhy(t *testing.T) {
	census := "## Causes, worst first\n\n" +
		"| # | Cause | What the tests do | Failures seen | Live tests hit | Fix (one PR each) |\n|---|---|---|---|---|---|\n" +
		"| C1 | Hidden retry | The shard reruns a failed test once and reports green | 17 | 8 | Fail on the first try |\n\n" +
		"| PR | Cause | Owner | Order |\n| --- | :--- | ---: | :---: |\n| #51700 | C1 | flake-fix-class | 1 |\n\n" +
		"The tests share five causes, and each fix cured one test while its siblings kept the same cause."
	if got := Uncited([]byte(census)); len(got) != 0 {
		t.Fatalf("Uncited(census) = %q, want none: a heading, a table's header and rule, and the noun cause say no why", got)
	}
	for _, why := range []string{
		"The shard goes green because it reruns a failed test once.",
		"The red was caused by a shared server.",
		"Each test names its own team so that no two tests write the same rows.",
	} {
		if got := Uncited([]byte(census + "\n\n" + why)); len(got) != 1 || got[0] != why {
			t.Errorf("Uncited(census + %q) = %q, want that sentence alone", why, got)
		}
	}
}

func TestUncitedNamesAPRBlamedForARedUntilARecordProvesIt(t *testing.T) {
	blamed := "#49475 broke dev's sand-next vet job."
	if got := Uncited([]byte(blamed)); len(got) != 1 || got[0] != blamed {
		t.Fatalf("Uncited(%q) = %q, want that sentence", blamed, got)
	}
	if got := Uncited([]byte("#49475 broke dev's sand-next vet job: build 12290 logs its missing import.")); len(got) != 0 {
		t.Fatalf("Uncited = %q, want none for a blame that cites its build", got)
	}
}

func TestExplainRefusesAnUncitedMechanismBeforeItWritesHostsOrAnnouncesAPage(t *testing.T) {
	runner := &shelltest.Fake{}
	h, owner := &host{}, &told{}
	_, err := Explain(context.Background(), Checkout{Runner: runner, Dir: t.TempDir()}, h, owner, "chat skew", []byte("# Chat skew\n\n"+skewPage), time.Now())
	if err == nil || !strings.HasPrefix(err.Error(), "lr explain refuses this page") || !strings.Contains(err.Error(), "- That is a release skew") || !strings.Contains(err.Error(), "example: "+cited) {
		t.Fatalf("Explain error = %v, want the refusal naming the skew sentence with its example", err)
	}
	if len(runner.Calls()) != 0 || len(h.dirs) != 0 || len(owner.notices) != 0 || len(owner.failures) != 0 {
		t.Fatalf("a refused page ran %v, served %v, announced %v and filed %v", runner.Calls(), h.dirs, owner.notices, owner.failures)
	}
	if got := Uncited([]byte(cited)); len(got) != 0 {
		t.Fatalf("the refusal's example %q does not pass", cited)
	}
}

func TestAHiddenCiteProvesTheSentenceAndNeverReachesHim(t *testing.T) {
	source := "The run crashed because the runner reads only migration 0. <!-- cci #384714 -->\n\nIt is fixed."
	if got := string(Visible([]byte(source))); got != "The run crashed because the runner reads only migration 0.\n\nIt is fixed." {
		t.Fatalf("Visible = %q", got)
	}
}
