package board

import (
	"context"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/cardtext"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx/ccxtest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

const (
	c211Title = "dashboard: 🐛 C211 and C220 wait for the other Mac's chat to reach the bucket before opening it"
	c211Head  = "b51dd199a0c4e2f1"
	c211Check = "a teammate's share, another Mac's chat and a cloud-started chat open read-only with no send box and no Fork, and an old-format chat's Fork starts a blank session naming it"
	c220Check = "another Mac's chat opened by link and a cloud-started chat open read-only behind the bar and tail live from the open with no reader requests between turns"
)

func TestTheC211MergeCardIsRefusedUntilItsTestsReadAsWhatTheyCheck(t *testing.T) {
	rows := []ReservedRow{{Key: "r7", Sentence: "no merge without his tap"}}
	before, err := ReservedCard("Forge-AI/monorepo", 52452, c211Head, rows, CardPR{Title: c211Title})
	if err != nil {
		t.Fatal(err)
	}
	if err := cardtext.PlainBlock([]byte(before)); err == nil || !strings.Contains(err.Error(), `"C211"`) {
		t.Fatalf("the merge card he could not read passed the bar: %v", err)
	}

	fake := &shelltest.Fake{}
	fake.Reply(`  "C211: `+c211Check+`",`+"\n"+`  "C220: `+c220Check+`",`+"\n", "git", "-C", "/co", "grep")
	read := &ccxtest.Fake{GHOut: map[string]string{"pr view 52452 --repo Forge-AI/monorepo --json files": `{"files":[{"path":"dashboard/e2e/irisServe.e2e.ts"}]}`}}
	plain := TestWords(context.Background(), fake, read, "/co", "Forge-AI/monorepo", 52452, c211Head, c211Title)
	after, err := ReservedCard("Forge-AI/monorepo", 52452, c211Head, rows, CardPR{Title: plain})
	if err != nil {
		t.Fatal(err)
	}
	if err := cardtext.PlainBlock([]byte(after)); err != nil {
		t.Fatalf("the rewritten merge card is still refused: %v\n%s", err, after)
	}
	if !strings.Contains(after, "the test that "+c211Check) || !strings.Contains(after, "the test that "+c220Check) {
		t.Fatalf("the merge card does not say what each test checks: %s", after)
	}
	calls := fake.Calls()
	if len(calls) == 0 || !strings.Contains(strings.Join(calls[0].Argv, " "), c211Head+" -- dashboard/e2e/irisServe.e2e.ts") {
		t.Fatalf("the first read was %v, want the test names at the PR's head in the files it changes", calls)
	}
}

func TestAnAskOwnerCardWithRecordIDsLaneNamesAndAHashIsRefused(t *testing.T) {
	good := Ask{
		Title: "Pair the smoke boot before tonight's release?", Background: "New boxes stop hanging at first boot.",
		Options: []string{"Pair :: New boxes boot on the first try.", "Skip :: Boots keep hanging about one time in five."}, NothingToBuild: "a process pick",
	}
	if _, err := askedCard(nil, good, "", "Forge-AI/monorepo", everyPR); err != nil {
		t.Fatalf("a plain ask was refused: %v", err)
	}
	for name, said := range map[string]string{
		"a record id": "Answers 581926 and m1746 on the smoke boot.",
		"a lane name": "dd-337919253-fix asks whether to pair.",
		"a hash":      "The boot image at 7c3e9a1 hangs.",
	} {
		ask := good
		ask.Background = said
		if _, err := askedCard(nil, ask, "", "Forge-AI/monorepo", everyPR); err == nil || !strings.Contains(err.Error(), "plain words") {
			t.Errorf("%s: err = %v, want the card refused for plain words", name, err)
		}
	}
}
