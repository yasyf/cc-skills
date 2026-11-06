package quotes

import (
	"context"
	"encoding/json"
	"reflect"
	"strings"
	"testing"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/scaletest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

const oneLevel = "that 32 database Seems like a regression. Go look into it. I told you to pick just one level to put the At, and it seems like maybe we picked the wrong level."

func reader(t *testing.T) *Reader {
	t.Helper()
	repeats, err := json.Marshal([]map[string]string{
		{"key": "prompt/1026-184540", "at": "Oct 26", "words": "yea just keep the fucking patches that are still useful jesus"},
		{"key": "prompt/1026-190000", "at": "Oct 26", "words": "second line\nthird line"},
	})
	if err != nil {
		t.Fatal(err)
	}
	implications, err := json.Marshal([]map[string]any{{"n": 1, "state": "proposed", "text": "the register's own reading"}})
	if err != nil {
		t.Fatal(err)
	}
	rows, err := json.Marshal(map[string]any{"rows": []map[string]any{
		{"key": "rule/784f1ab", "fields": map[string]string{"at": "Oct 20", "words": "every thjrn hiinchanhe jeedsa regresion test and one that actuwlly woild habe failed not a trivial one. just fic the rmeaining wctuwl jssues"}},
		{"key": "prompt/1026-113025", "fields": map[string]string{"at": "Oct 26", "words": "keep the patches of we need them for perf", "repeats": string(repeats), "implications": string(implications)}},
		{"key": "prompt/1023-094200-d2261fba", "fields": map[string]string{"at": "Oct 23", "words": "for the cloid box mint some credentials locally using machine profile or something"}},
	}})
	if err != nil {
		t.Fatal(err)
	}
	fake := &shelltest.Fake{}
	fake.Reply(`[{"id":"register-1","updated_at":"2026-11-03"}]`, "ccn", "-R", "/repo", "ledger", "list")
	fake.Reply(string(rows), "ccn", "-R", "/repo", "ledger", "show", "register-1")
	fake.Reply("title: the look\n\nVerbatim: \""+oneLevel+"\" Bridge reading: the lane re-reads.\n", "ccn", "-R", "/repo", "answer", "show", "79e14c4")
	fake.Reply("title: the ci tool's AWS sign-in\n\nVerbatim: \"only our computer has rhe madhine orofiel so dint brewk the co tool for eveyone else\"\n", "ccn", "-R", "/repo", "answer", "show", "54c47b4")
	fake.Fail(1, "no such answer", "ccn", "-R", "/repo", "answer", "show", "0000000")
	fake.Fail(noSuchNote, "not-found", "ccn", "-R", "/repo", "answer", "show", "1111111")
	return &Reader{Runner: fake, Entry: func() (drive.Entry, error) { return drive.Entry{Checkout: "/repo", Program: "p"}, nil }}
}

func TestCitedPairsEachQuoteWithTheKeyItFollows(t *testing.T) {
	for name, tc := range map[string]struct {
		text string
		want []Quote
	}{
		"beside its key":  {`rule/86efabc, "reader ships first", is the order`, []Quote{{"rule/86efabc", "reader ships first"}}},
		"answer id":       {`Answer 79e14c4, "pick just one level", orders it`, []Quote{{"Answer 79e14c4", "pick just one level"}}},
		"a chain of them": {`Answer 68c82f0: "A plus B", "one open connection", and "stacked"`, []Quote{{"Answer 68c82f0", "A plus B"}, {"Answer 68c82f0", "one open connection"}, {"Answer 68c82f0", "stacked"}}},
		"curly quotes":    {`prompt/1101-103245 “seek ways”`, []Quote{{"prompt/1101-103245", "seek ways"}}},
		"a later clause":  {`prompt/1101-214503, "2 bugs", and your 356750, "The named invariant"`, []Quote{{"prompt/1101-214503", "2 bugs"}}},
		"root's words":    {`rule/86efabc is the ground, per your 356750, "The named invariant"`, nil},
		"next sentence":   {`rule/da49cb1 is why it exists. The body says "cannot leak"`, nil},
		"the next key":    {`prompt/1026-113025 and rule/91150e7, "rolls back"`, []Quote{{"rule/91150e7", "rolls back"}}},
		"a dotted file":   {`rule/86efabc in remote.rs, "reader ships"`, []Quote{{"rule/86efabc", "reader ships"}}},
		"no key at all":   {`The grade says "plan names no field"`, nil},
	} {
		t.Run(name, func(t *testing.T) {
			if got := Cited(tc.text); !reflect.DeepEqual(got, tc.want) {
				t.Fatalf("Cited = %+v, want %+v", got, tc.want)
			}
		})
	}
}

func TestCheck(t *testing.T) {
	for name, tc := range map[string]struct {
		text string
		want string
	}{
		"a clause whole":                   {text: `finder-override #1 at abc1234: 9. Answer 79e14c4, "I told you to pick just one level to put the At", grounds it`},
		"a full sentence":                  {text: `Answer 79e14c4, "Go look into it.", is the order`},
		"after a joining word":             {text: `Answer 79e14c4, "it seems like maybe we picked the wrong level."`},
		"a marked cut":                     {text: `Answer 79e14c4, "that 32 database ... Go look into it", is the order`},
		"a repeat under its parent":        {text: `prompt/1026-113025, "yea just keep the fucking patches that are still useful jesus"`},
		"a repeat under its own key":       {text: `prompt/1026-184540, "yea just keep the fucking patches that are still useful jesus"`},
		"a line of a repeat":               {text: `prompt/1026-190000, "second line"`},
		"the row's own words":              {text: `prompt/1026-113025, "keep the patches of we need them for perf"`},
		"no quote at all":                  {text: `finder-override #1 at abc1234: 9`},
		"words he never said":              {`Answer 79e14c4, "orders this fix"`, `does not say "orders this fix"`},
		"a parent's words, not a repeat's": {`prompt/1026-184540, "keep the patches of we need them for perf"`, "does not say"},
		"the register's reading":           {`prompt/1026-113025, "the register's own reading"`, "does not say"},
		"an unknown row":                   {`rule/0000000, "anything"`, ErrUnknownKey.Error()},
		"stopping mid clause":              {`rule/784f1ab, "every thjrn hiinchanhe jeedsa regresion test", asks for it`, `goes on after "every thjrn hiinchanhe jeedsa regresion test": "and one that actuwlly`},
		"opening mid clause":               {`Answer 79e14c4, "pick just one level to put the At"`, "has more of that clause before"},
		"an answer it cannot read":         {`answer 0000000, "anything"`, "could not read answer 0000000"},
		"a quote with no row id":           {`finder-override #1 at abc1234: 9. The grade says "plan names no field"`, "with no row or answer key"},
	} {
		t.Run(name, func(t *testing.T) {
			got := reader(t).Check(context.Background(), tc.text)
			if tc.want == "" && len(got) != 0 {
				t.Fatalf("warned %q", got)
			}
			if tc.want != "" && (len(got) != 1 || !strings.Contains(got[0], tc.want)) {
				t.Fatalf("got %q, want one warning saying %q", got, tc.want)
			}
		})
	}
}

func TestARegisterItCannotReadWarnsOncePerRead(t *testing.T) {
	for name, stub := range map[string]func(*shelltest.Fake){
		"ccn fails":   func(fake *shelltest.Fake) { fake.Fail(1, "ccn is down", "ccn") },
		"no register": func(fake *shelltest.Fake) { fake.Reply(`[]`, "ccn", "-R", "/repo", "ledger", "list") },
	} {
		t.Run(name, func(t *testing.T) {
			fake := &shelltest.Fake{}
			stub(fake)
			r := &Reader{Runner: fake, Entry: func() (drive.Entry, error) { return drive.Entry{Checkout: "/repo", Program: "p"}, nil }}

			got := r.Check(context.Background(), `rule/784f1ab, "anything" and rule/86efabc, "anything else"`)

			if len(got) != 2 || !strings.Contains(got[0], "could not read rule/784f1ab") {
				t.Fatalf("got %q", got)
			}
			if calls := len(fake.Calls()); calls != 1 {
				t.Fatalf("read the register %d times", calls)
			}
		})
	}
}

func TestOverrideReadsRootsAndAppealsOverrides(t *testing.T) {
	bad := `finder-override #1 at abc1234: 9. rule/784f1ab, "words he never said", asks for it`
	for name, tc := range map[string]struct {
		record client.Record
		want   int
	}{
		"root's override":       {client.Record{Lane: "root", Kind: "decide", Text: bad}, 1},
		"an appeal":             {client.Record{Lane: "grader-appeals-1", Kind: "decide", Text: bad}, 1},
		"a lane's proposal":     {client.Record{Lane: "lane-7", Kind: "decide", Text: bad}, 0},
		"root with no override": {client.Record{Lane: "root", Kind: "decide", Text: `rule/784f1ab, "words he never said" came up`}, 0},
	} {
		t.Run(name, func(t *testing.T) {
			if got := reader(t).Override(context.Background(), tc.record); len(got) != tc.want {
				t.Fatalf("got %q, want %d", got, tc.want)
			}
		})
	}
}

func TestWholeCarriesEveryRepeatAndImplication(t *testing.T) {
	whole, err := reader(t).Whole(context.Background(), "prompt/1026-184540")
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"prompt/1026-113025 (Oct 26)", "keep the patches of we need them for perf", "repeat prompt/1026-184540", "useful jesus", "third line", "implication 1 [proposed]: the register's own reading"} {
		if !strings.Contains(whole, want) {
			t.Fatalf("Whole lacks %q:\n%s", want, whole)
		}
	}
}

func TestReadingTheRegisterOnceServesEveryQuote(t *testing.T) {
	r := reader(t)
	text := `rule/784f1ab, "just fic the rmeaining wctuwl jssues" and prompt/1026-113025, "keep the patches of we need them for perf"`
	for range scaletest.N() {
		if got := r.Check(context.Background(), text); len(got) != 0 {
			t.Fatalf("got %q", got)
		}
	}
	shows := 0
	for _, call := range r.Runner.(*shelltest.Fake).Calls() {
		if strings.Contains(strings.Join(call.Argv, " "), "ledger show") {
			shows++
		}
	}
	if shows != 1 {
		t.Fatalf("read the register %d times", shows)
	}
}

func TestAnswersReadsEachCitedIDAndHisTopRankedOnTheScopeAndSkipsAnIDNoAnswerHolds(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply(`{"id":"0531fdd00","title":"which routes","body":"every route","updated_at":"2026-11-04","tags":["topic:llm-gateway"]}`, "ccn", "-R", "/repo", "answer", "show", "0531fdd", "--json")
	fake.Fail(3, "not-found: entity not found", "ccn", "-R", "/repo", "answer", "show", "7777777", "--json")
	fake.Reply(`[{"id":"c47347100","title":"a night","body":"what changed","updated_at":"2026-11-02"}]`, "ccn", "-R", "/repo", "answer", "search", "dream", "--label", "scope:durable")
	found, err := (&Reader{Runner: fake, Entry: func() (drive.Entry, error) { return drive.Entry{Checkout: "/repo"}, nil }}).Answers(context.Background(), []string{"0531fdd", "7777777"}, "dream")
	if err != nil {
		t.Fatal(err)
	}
	want := []Answer{{ID: "0531fdd00", Title: "which routes", Body: "every route", UpdatedAt: "2026-11-04"}, {ID: "c47347100", Title: "a night", Body: "what changed", UpdatedAt: "2026-11-02"}}
	if !reflect.DeepEqual(found, want) {
		t.Fatalf("Answers = %+v, want %+v", found, want)
	}
}

func TestForPRReadsCitedTopicAndScopeAnswersOnceEachNewestFirstAndNeverAURLFragment(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply(`{"id":"8ae07a000","title":"which path","body":"one path","updated_at":"2026-11-04"}`, "ccn", "-R", "/repo", "answer", "show", "8ae07a0", "--json")
	fake.Reply(`{"id":"33d842800","title":"the settings","body":"recommended","updated_at":"2026-11-01"}`, "ccn", "-R", "/repo", "answer", "show", "33d8428", "--json")
	fake.Fail(3, "not-found: entity not found", "ccn", "-R", "/repo", "answer", "show", "7777777", "--json")
	fake.Reply(`[{"id":"c47347100","title":"a night","body":"what changed","updated_at":"2026-11-02"}]`, "ccn", "-R", "/repo", "answer", "search", "sand-next", "--label", "scope:durable")
	fake.Reply(`[{"id":"aaaaaaa00","updated_at":"2026-10-20"}]`, "ccn", "-R", "/repo", "ledger", "list", "--label", "answer-topics", "--label", "program:brook", "--json")
	fake.Reply(`{"rows":[{"key":"old-chats","fields":{"words":"\\bold chats?\\b","dirs":"[]"}},{"key":"kernel","fields":{"words":"nothing here","dirs":"[\"go/kernel\"]"}},{"key":"elsewhere","fields":{"words":"nothing here","dirs":"[\"api\"]"}}]}`, "ccn", "-R", "/repo", "ledger", "show", "aaaaaaa00", "--json")
	fake.Reply(`[{"id":"b0b0b0b00","title":"old","body":"chats open","updated_at":"2026-10-20"},{"id":"8ae07a000","title":"which path","body":"one path","updated_at":"2026-11-04"}]`, "ccn", "-R", "/repo", "answer", "search", "old-chats", "--limit", "0", "--json")
	fake.Reply(`[{"id":"d0d0d0d00","title":"kernel","body":"keep it","updated_at":"2026-10-21"}]`, "ccn", "-R", "/repo", "answer", "search", "kernel", "--limit", "0", "--json")
	pr := PR{Title: "sand-next: compaction forks", Body: "Under answer 8ae07a0 at commit 7777777, on OLD chats. Doc: https://docs.poetic.design/design-docs/fork/abcdef1 and rule/ffacf77.", Author: author{Login: "lane"}}
	pr.Comments = append(pr.Comments, struct {
		Author author `json:"author"`
		Body   string `json:"body"`
	}{author{"lane"}, "Also answer/33d8428."}, struct {
		Author author `json:"author"`
		Body   string `json:"body"`
	}{author{"bot"}, "Breaks 9999999."})
	pr.Files = append(pr.Files, struct {
		Path string `json:"path"`
	}{"go/kernel/run.go"})
	found, err := (&Reader{Runner: fake, Entry: func() (drive.Entry, error) { return drive.Entry{Checkout: "/repo"}, nil }}).ForPR(context.Background(), "brook", pr, "")
	if err != nil {
		t.Fatal(err)
	}
	ids := make([]string, 0, len(found))
	for _, answer := range found {
		ids = append(ids, answer.ID)
	}
	if want := []string{"8ae07a000", "c47347100", "33d842800", "d0d0d0d00", "b0b0b0b00"}; !reflect.DeepEqual(ids, want) {
		t.Fatalf("ids = %v, want %v", ids, want)
	}
}

func TestForPRReadsAnAnswerOnlyACoordinatorRulingCites(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply(`{"id":"6e7a34600","title":"writers","body":"goroutines","updated_at":"2026-11-03"}`, "ccn", "-R", "/repo", "answer", "show", "6e7a346", "--json")
	fake.Reply(`[]`, "ccn", "-R", "/repo", "ledger", "list", "--label", "answer-topics", "--label", "program:brook", "--json")
	found, err := (&Reader{Runner: fake, Entry: func() (drive.Entry, error) { return drive.Entry{Checkout: "/repo"}, nil }}).ForPR(context.Background(), "brook", PR{Title: "no scope here"}, "finder-override: Answer 6e7a346 settles it.")
	if err != nil {
		t.Fatal(err)
	}
	if len(found) != 1 || found[0].ID != "6e7a34600" {
		t.Fatalf("found = %+v", found)
	}
}

func TestAnswersFailsWhenTheStoreCannotBeRead(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Fail(1, "open git repository", "ccn", "-R", "/repo", "answer", "show", "0531fdd", "--json")
	if _, err := (&Reader{Runner: fake, Entry: func() (drive.Entry, error) { return drive.Entry{Checkout: "/repo"}, nil }}).Answers(context.Background(), []string{"0531fdd"}, ""); err == nil {
		t.Fatal("a store that cannot be read must not read as an answer that does not exist")
	}
}

func TestAnswersWithNoIDAndNoScopeRunsNoCommandAndPrintsAnEmptyList(t *testing.T) {
	fake := &shelltest.Fake{}
	found, err := (&Reader{Runner: fake, Entry: func() (drive.Entry, error) { return drive.Entry{Checkout: "/repo"}, nil }}).Answers(context.Background(), nil, "")
	if err != nil {
		t.Fatal(err)
	}
	encoded, err := json.Marshal(found)
	if err != nil {
		t.Fatal(err)
	}
	if string(encoded) != "[]" || len(fake.Calls()) != 0 {
		t.Fatalf("encoded %s after %d calls", encoded, len(fake.Calls()))
	}
}

func TestAnswersFollowsASupersededAnswerToItsLiveHead(t *testing.T) {
	fake := &shelltest.Fake{}
	fake.Reply(`{"id":"9134c5400","title":"how long","body":"his first tap","updated_at":"2026-11-04","superseded_by":["bbadca200"],"live_head":["bbadca200"]}`, "ccn", "-R", "/repo", "answer", "show", "9134c54", "--json")
	fake.Reply(`{"id":"bbadca200","title":"how long, in full","body":"his full answer","updated_at":"2026-11-04"}`, "ccn", "-R", "/repo", "answer", "show", "bbadca200", "--json")
	found, err := (&Reader{Runner: fake, Entry: func() (drive.Entry, error) { return drive.Entry{Checkout: "/repo"}, nil }}).Answers(context.Background(), []string{"9134c54"}, "")
	if err != nil {
		t.Fatal(err)
	}
	if len(found) != 2 || found[0].Body != "his first tap" || found[1].Body != "his full answer" {
		t.Fatalf("Answers = %+v", found)
	}
}

func TestGroundedNeedsEveryCitedRowToExist(t *testing.T) {
	for name, tc := range map[string]struct {
		text string
		want bool
	}{
		"a row":                {"finder-override #1 at abc1234: 9. Row rule/784f1ab grounds it", true},
		"a repeat's key":       {"finder-override #1 at abc1234: 9. Row prompt/1026-184540 grounds it", true},
		"an answer":            {"fidelity-override #1 at abc1234: 9. Answer 79e14c4 grounds it", true},
		"a row that is no row": {"finder-override #1 at abc1234: 9. Row rule/0000000 grounds it", false},
		"no such answer":       {"fidelity-override #1 at abc1234: 9. Answer 1111111 grounds it", false},
		"one real and one not": {"finder-override #1 at abc1234: 9. Rows rule/784f1ab and rule/0000000 ground it", false},
		"no row cited at all":  {"finder-override #1 at abc1234: 9. The finding is wrong", false},
		"a prompt key that carries its digest, as cci 515309 cites one beside an answer": {
			"finder-override #51540 at 7aba477: 514875 (carried from 508862 and 506965, patch-identical). His later ruling the same day on the ci tool's AWS sign-in, answer 54c47b4: \"only our computer has rhe madhine orofiel so dint brewk the co tool for eveyone else\". Row prompt/1023-094200-d2261fba is his word on minting the cloud box's credentials from the machine profile, not a ban on the shared tool's SSO default.",
			true,
		},
		"a digest-keyed prompt row cited without its digest": {"finder-override #1 at abc1234: 9. Row prompt/1023-094200 grounds it", false},
	} {
		t.Run(name, func(t *testing.T) {
			got, err := reader(t).Grounded(context.Background(), client.Record{Lane: "grader-appeals-1", Kind: "decide", Text: tc.text})
			if err != nil || got != tc.want {
				t.Fatalf("Grounded = %v, %v, want %v", got, err, tc.want)
			}
		})
	}
}

func TestGroundedSaysSoWhenCcnCannotAnswer(t *testing.T) {
	got, err := reader(t).Grounded(context.Background(), client.Record{Lane: "grader-appeals-1", Kind: "decide", Text: "fidelity-override #1 at abc1234: 9. Answer 0000000 grounds it"})
	if err == nil || got {
		t.Fatalf("Grounded = %v, %v, want an error so the record is read again", got, err)
	}
}
