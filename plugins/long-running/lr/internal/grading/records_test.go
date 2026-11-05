package grading

import (
	"context"
	"reflect"
	"strconv"
	"testing"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/grading/grader"
)

const pr = "45489"

func TestANumberedSuccessorOfADeskIsThatDeskAndAnEarlierOneIsNot(t *testing.T) {
	for _, tt := range []struct {
		lane, delegate string
		want           bool
	}{
		{"finder-desk", "finder-desk", true},
		{"finder-desk-2", "finder-desk", true},
		{"finder-desk-1", "finder-desk", false},
		{"finder-desk-3", "finder-desk-2", true},
		{"finder-desk-2", "finder-desk-3", false},
		{"finder-desks", "finder-desk", false},
	} {
		if got := delegated(tt.lane, tt.delegate); got != tt.want {
			t.Errorf("delegated(%q, %q) = %v", tt.lane, tt.delegate, got)
		}
	}
}

func TestARecordIsAboutThePRItsRefsNameOrWithNoRefsThePRItsTextNames(t *testing.T) {
	refs := func(text string, names ...string) client.Record {
		t.Helper()
		t.Setenv("CCI_HOME", t.TempDir())
		seq := post(t, client.Draft{Lane: "lane-7", Kind: kindNote, Text: text, PRs: names})
		found, err := client.Among(context.Background(), testDrive, []int64{seq})
		if err != nil || len(found) != 1 {
			t.Fatalf("found = %v, %v", found, err)
		}
		return found[0]
	}
	for name, tt := range map[string]struct {
		record client.Record
		want   bool
	}{
		"a ref in the repo":          {refs("a note", pull), true},
		"a bare ref":                 {refs("a note", pr), true},
		"a ref in another repo":      {refs("about #45489", "other/repo#45489"), false},
		"another PR's ref":           {refs("about #45489", repo+"#45490"), false},
		"no ref, a hash in its text": {refs("about #45489 today"), true},
		"no ref, a bracket":          {refs("finder [45489] ok"), true},
		"no ref, a longer number":    {refs("about #454890"), false},
	} {
		if got := aboutPR(tt.record, repo, pr); got != tt.want {
			t.Errorf("%s: aboutPR = %v", name, got)
		}
	}
}

func TestAMultiHeadRecordNamesEachPRsHeadAndWhatItReplaced(t *testing.T) {
	r := client.Record{Text: "restack of " + older}
	r.Fields.Attrs = map[string]string{"head:45489": older + ".." + head + " rebased", "head:" + repo + "#45490": newer, "head:other/repo#45491": head}

	if got := headOf(r, repo, pr); got != head {
		t.Errorf("head = %q", got)
	}
	if got := headOf(r, repo, "45490"); got != newer {
		t.Errorf("second head = %q", got)
	}
	if got := headOf(r, repo, "45491"); got != "" {
		t.Errorf("another repo's head = %q", got)
	}
	r.Kind = kindHead
	if got := previousHead([]client.Record{withPR(r)}, repo, pr, head); got != older {
		t.Errorf("previous = %q", got)
	}
}

func withPR(r client.Record) client.Record {
	r.Text += " #45489"
	return r
}

func TestARecordsWasShaIsNeverTheHeadItAnnounces(t *testing.T) {
	r := client.Record{Kind: kindHead, Text: "#45489 (was " + older + ") " + head}

	if announces(r, repo, pr, older) {
		t.Error("announced the head it replaced")
	}
	if !announces(r, repo, pr, head) {
		t.Error("did not announce its head")
	}
	if got := previousHead([]client.Record{r}, repo, pr, head); got != older {
		t.Errorf("previous = %q", got)
	}
}

func TestAReviewNamingNoShaGradesTheHeadPostedBeforeIt(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	post(t, client.Draft{Lane: "lane-7", Kind: kindHead, Text: "#45489 " + older, PRs: []string{pull}})
	early := post(t, client.Draft{Lane: grader.FinderLane, Kind: kindReview, Text: "finder: ok, no majors", PRs: []string{pull}})
	post(t, client.Draft{Lane: "lane-7", Kind: kindHead, Text: "#45489 " + head, PRs: []string{pull}})
	post(t, client.Draft{Lane: "lane-7", Kind: kindReview, Text: "fidelity: the finder agrees", PRs: []string{pull}})
	late := post(t, client.Draft{Lane: "lane-7", Kind: kindReview, Text: "the finder missed this", PRs: []string{pull}})
	found, err := client.Tail(context.Background(), client.Query{Drive: testDrive, Kinds: []string{kindReview, kindHead}, PR: pull})
	if err != nil {
		t.Fatal(err)
	}

	reviews := finderReviews(found, repo, pr)

	if len(reviews) != 2 || reviews[0].Seq != early || reviews[1].Seq != late {
		t.Fatalf("reviews = %+v", reviews)
	}
	if reviews[0].grade != "ok" || reviews[0].head != older {
		t.Errorf("early = %+v", reviews[0])
	}
	if reviews[1].grade != "" || reviews[1].head != head {
		t.Errorf("a lane's reply reads as a grade: %+v", reviews[1])
	}
	if graded := gradedReviews(reviews); len(graded) != 1 || graded[0].Seq != early {
		t.Errorf("graded = %+v", graded)
	}
}

func TestOnlyAnOverrideOpeningAClauseOverrides(t *testing.T) {
	sha := head[:7]
	for text, want := range map[string]bool{
		"finder-override 45489 " + sha:                      true,
		"finder-override #45489 at " + sha + " for now":     true,
		"root: finder-override 45489 " + sha:                true,
		"Looked again. finder-override 45489 " + sha:        true,
		"8:03 PM root: finder-override 45489 " + sha:        true,
		"we said finder-override 45489 " + sha + " earlier": false,
		"finder-override 45490 " + sha:                      false,
		"finder-override 45489 " + older[:7]:                false,
		"fidelity-override 45489 " + sha:                    false,
	} {
		if got := overridden(text, overrideFinder, pr, head); got != want {
			t.Errorf("overridden(%q) = %v", text, got)
		}
	}
}

func TestAClearUnlessNegatedOrARebuttalAcceptedClearsAFinder(t *testing.T) {
	for text, want := range map[string]bool{
		"cleared":              true,
		"clears the major":     true,
		"rebuttal accepted":    true,
		"land it":              true,
		"not cleared":          false,
		"never clear":          false,
		"not clear, but clear": true,
		"unclear":              false,
		"nothing to say":       false,
	} {
		if got := finderCleared(text); got != want {
			t.Errorf("finderCleared(%q) = %v", text, got)
		}
	}
}

func TestEachRootRulingSettlesTheRecordsItNames(t *testing.T) {
	sha := head[:7]
	finderMajor := "finder: major " + sha + " 1 open"
	fidelityGap := "fidelity: missing " + sha + " plan:12"
	for name, tt := range map[string]struct {
		graded  string
		decide  client.Draft
		settled bool
	}{
		"a reply":                   {finderMajor, client.Draft{Text: "no"}, true},
		"said settled on its PR":    {finderMajor, client.Draft{Text: "this is settled now", PRs: []string{pull}}, true},
		"said settled elsewhere":    {finderMajor, client.Draft{Text: "this is settled now", PRs: []string{repo + "#1"}}, false},
		"a clear naming the head":   {finderMajor, client.Draft{Text: "cleared the finder on #45489 at " + sha}, true},
		"a finder override":         {finderMajor, client.Draft{Text: "finder-override 45489 " + sha}, true},
		"an override of a gap":      {fidelityGap, client.Draft{Text: "fidelity-override 45489 " + sha}, true},
		"a finder override of gap":  {fidelityGap, client.Draft{Text: "finder-override 45489 " + sha}, false},
		"a ruling on nothing":       {finderMajor, client.Draft{Text: "unrelated ruling"}, false},
		"a clear of another head":   {finderMajor, client.Draft{Text: "cleared the finder on #45489 at " + older[:7]}, false},
		"a clear in another clause": {finderMajor, client.Draft{Text: "we have cleared nothing about the finder. #45489 " + sha}, false},
	} {
		t.Run(name, func(t *testing.T) {
			t.Setenv("CCI_HOME", t.TempDir())
			graded := post(t, client.Draft{Lane: grader.FinderLane, Kind: kindReview, Text: tt.graded, PRs: []string{pull}})
			draft := tt.decide
			draft.Lane, draft.Kind = rootLane, kindDecide
			if name == "a reply" {
				draft.Re = graded
			}
			decide := post(t, draft)
			named, err := client.Among(context.Background(), testDrive, []int64{graded})
			if err != nil {
				t.Fatal(err)
			}

			settled, err := rootSettled(context.Background(), testDrive, repo, pr, named)
			if err != nil {
				t.Fatal(err)
			}

			want := map[string]int64{}
			if tt.settled {
				want[strconv.FormatInt(graded, 10)] = decide
			}
			if !reflect.DeepEqual(settled, want) {
				t.Errorf("settled = %v, want %v", settled, want)
			}
		})
	}
}

func TestAnAcceptAnswerIsSettledByTheFirstRootRulingAfterIt(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	graded := post(t, client.Draft{Lane: grader.FinderLane, Kind: kindReview, Text: "finder: major " + head[:7] + " 1 open", PRs: []string{pull}})
	post(t, client.Draft{Lane: rootLane, Kind: kindNote, Text: "before the answer", PRs: []string{pull}})
	post(t, client.Draft{Lane: "lane-7", Kind: kindAnswer, Text: "accepted, the risk stands", Re: graded, PRs: []string{pull}})
	ruling := post(t, client.Draft{Lane: rootLane, Kind: kindDecide, Text: "go on", PRs: []string{pull}})
	post(t, client.Draft{Lane: rootLane, Kind: kindDecide, Text: "go on again", PRs: []string{pull}})
	named, err := client.Among(context.Background(), testDrive, []int64{graded})
	if err != nil {
		t.Fatal(err)
	}

	settled, err := rootSettled(context.Background(), testDrive, repo, pr, named)
	if err != nil {
		t.Fatal(err)
	}

	if want := map[string]int64{strconv.FormatInt(graded, 10): ruling}; !reflect.DeepEqual(settled, want) {
		t.Errorf("settled = %v, want %v", settled, want)
	}
}

func TestARebuttalCitingARootRulingCarriesThatRuling(t *testing.T) {
	const graded, ruling = int64(300001), int64(300500)
	settled := map[string]int64{}
	rebuttals := []client.Record{
		{Seq: 300600, Re: graded, Text: "see #299999 and #300500, both on this"},
		{Seq: 300601, Re: 300002, Text: "nothing cited that rules"},
	}

	applyRebuttals(rebuttals, map[int64]bool{ruling: true}, settled)

	if want := map[string]int64{"300001": ruling}; !reflect.DeepEqual(settled, want) {
		t.Errorf("settled = %v, want %v", settled, want)
	}
}

func TestACitedSeqIsSixDigitsOrMore(t *testing.T) {
	if got, want := citedSeqs("#12345 #123456 #1234567 and #5"), []int64{123456, 1234567}; !reflect.DeepEqual(got, want) {
		t.Errorf("cited = %v, want %v", got, want)
	}
}

func TestANewestContractAboutThePRWinsThenTheLanesNewest(t *testing.T) {
	about := client.Record{Seq: 1, Text: "contract for #45489"}
	later := client.Record{Seq: 2, Text: "contract for #45490"}

	if got := newestContract([]client.Record{about, later}, repo, pr); got != about.Text {
		t.Errorf("got %q", got)
	}
	if got := newestContract([]client.Record{later}, repo, pr); got != later.Text {
		t.Errorf("got %q", got)
	}
	if got := newestContract(nil, repo, pr); got != "" {
		t.Errorf("got %q", got)
	}
}

func TestLaneSourcesTakeEveryPatternTheLaneMatchesOnce(t *testing.T) {
	table := map[string][]string{"*": {"all", "class"}, "s*-class-*": {"class"}, "other": {"unread"}}

	if got, want := laneSources(table, "s8-class-2"), []string{"all", "class"}; !reflect.DeepEqual(got, want) {
		t.Errorf("sources = %v, want %v", got, want)
	}
}

func TestACarriedOverrideSettlesTheGradeAtItsHead(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	graded := post(t, client.Draft{Lane: grader.FinderLane, Kind: kindReview, Text: "finder: major " + head[:7] + " 1 open", PRs: []string{pull}})
	decide := post(t, client.Draft{Lane: bus.SweepLane, Kind: kindDecide, Text: "finder-override #" + pr + " at " + head[:7] + ": 9. Carried from aaaaaaa, whose own diff is the same.", PRs: []string{pull}})
	named, err := client.Among(context.Background(), testDrive, []int64{graded})
	if err != nil {
		t.Fatal(err)
	}

	settled, err := rootSettled(context.Background(), testDrive, repo, pr, named)
	if err != nil {
		t.Fatal(err)
	}

	if want := map[string]int64{strconv.FormatInt(graded, 10): decide}; !reflect.DeepEqual(settled, want) {
		t.Errorf("settled = %v, want %v", settled, want)
	}
}

func TestAnAppealAloneSettlesNoGrade(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	graded := post(t, client.Draft{Lane: grader.FidelityLane, Kind: kindReview, Text: fidelityMissing + " " + head + " owner/deletion deletes a thing", PRs: []string{pull}})
	post(t, client.Draft{Lane: "grader-appeals-1", Kind: kindDecide, Text: "fidelity-override #" + pr + " at " + head[:7] + ": " + strconv.FormatInt(graded, 10) + ". Row rule/4236611 grounds it", PRs: []string{pull}})
	named, err := client.Among(context.Background(), testDrive, []int64{graded})
	if err != nil {
		t.Fatal(err)
	}

	settled, err := rootSettled(context.Background(), testDrive, repo, pr, named)
	if err != nil {
		t.Fatal(err)
	}

	if len(settled) != 0 {
		t.Errorf("settled = %v, want none", settled)
	}
}
