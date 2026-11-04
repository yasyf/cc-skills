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
	} {
		if got := Uncited([]byte(source)); len(got) != 0 {
			t.Errorf("Uncited(%q) = %q, want none", source, got)
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
