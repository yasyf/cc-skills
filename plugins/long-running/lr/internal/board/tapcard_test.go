package board

import (
	"fmt"
	"reflect"
	"strings"
	"testing"
)

const tapThreadTS = "1792842193.708029"

const tapBody = "## Context\n\nThe setting.\n\n## Summary\n\nA chat's name is one saved value.\nIt shows at once.\n\nA second paragraph.\n\n## Differences from the plan\n\nDrops the rename handle.\n\n## Risk profile\n\n- **Threat model:** own Mac only\n- **Availability:** none\n\n<details>\n<summary>Design</summary>\n\nLong.\n</details>\n"

func tapWait(number int, title, body string, below int) TapWait {
	return TapWait{Pull: Pull{Number: number, Title: title, Body: body, Head: head, Branch: fmt.Sprintf("b/%d", number)}, Head: head, Below: below}
}

func TestARowIsGeneratedFromThePRsTitleAndBody(t *testing.T) {
	card := BuildTapCard(repo, tapThreadTS, []TapWait{tapWait(7, "sand-next: ♻️ a chat has one name", tapBody, 0)})
	row := card.Children[0].Groups[0].PRs[0]
	want := TapRow{
		Number:  7,
		Head:    head,
		Title:   "sand-next: ♻️ a chat has one name",
		Change:  "A chat's name is one saved value. It shows at once.",
		Plan:    "Drops the rename handle.",
		Risk:    "- **Threat model:** own Mac only - **Availability:** none",
		Review:  "Fidelity ok, finder clear, CI green and approved at e718a74.",
		Blocker: "Only your tap.",
	}
	if !reflect.DeepEqual(row, want) {
		t.Fatalf("row = %+v\nwant  %+v", row, want)
	}
}

func TestARowFromABareBodyStillFillsEveryFactOnOneLine(t *testing.T) {
	row := tapRow(tapWait(7, "api: ✨ reads one credential\n", "", 0))
	if row.Change != "reads one credential" {
		t.Fatalf("change = %q", row.Change)
	}
	for name, value := range map[string]string{"plan": row.Plan, "risk": row.Risk, "title": row.Title} {
		if value == "" || strings.Contains(value, "\n") {
			t.Fatalf("%s = %q", name, value)
		}
	}
}

func TestASectionIsFoundByAWholeWordOfItsHeading(t *testing.T) {
	row := tapRow(tapWait(7, "api: x", "## Explanation\n\nNot the plan.\n\n## Against the plan:\n\nAdds a cap.", 0))
	if row.Plan != "Adds a cap." {
		t.Fatalf("plan = %q", row.Plan)
	}
}

func TestALongFactIsClippedToOneBoundedLine(t *testing.T) {
	body := "## Summary\n\n" + strings.Repeat("word ", 400)
	change := tapRow(tapWait(7, "api: x", body, 0)).Change
	if got := len([]rune(change)); got != tapLineChars {
		t.Fatalf("len = %d", got)
	}
}

func TestSeparatePRsOfOneScopeShareACardAndALonePRTakesItsOwnWords(t *testing.T) {
	groups := BuildTapCard(repo, tapThreadTS, []TapWait{
		tapWait(1, "api: ✨ one", "", 0),
		tapWait(2, "api: 🐛 two", "", 0),
		tapWait(3, "sand-next: ♻️ three", "## Summary\n\nThe third change.", 0),
	}).Children[0].Groups
	if len(groups) != 2 {
		t.Fatalf("groups = %+v", groups)
	}
	if groups[0].ID != "scope-api" || groups[0].Title != "api: 2 pull requests" || len(groups[0].PRs) != 2 {
		t.Fatalf("group = %+v", groups[0])
	}
	if groups[1].ID != "scope-sand-next" || groups[1].Title != "three" || groups[1].Summary != "The third change." {
		t.Fatalf("group = %+v", groups[1])
	}
}

func TestAStackIsOneGroupBottomFirstWithNoNumberOrder(t *testing.T) {
	groups := BuildTapCard(repo, tapThreadTS, []TapWait{
		tapWait(5, "api: ✨ top", "", 9),
		tapWait(9, "api: ✨ bottom", "", 0),
		tapWait(6, "api: 🐛 alone", "", 0),
	}).Children[0].Groups
	var stack TapGroup
	for _, group := range groups {
		if strings.HasPrefix(group.ID, "stack-") {
			stack = group
		}
	}
	if stack.ID != "stack-9" || stack.PRs[0].Number != 9 || stack.PRs[1].Number != 5 {
		t.Fatalf("stack = %+v", stack)
	}
	if stack.Title != "bottom" || !strings.Contains(stack.Summary, "#9, #5") {
		t.Fatalf("stack = %+v", stack)
	}
	if stack.PRs[1].Blocker != "Your tap, and #9 below it lands first." {
		t.Fatalf("blocker = %q", stack.PRs[1].Blocker)
	}
}

func TestACardKeepsToTheBoardsAndSlacksLimitsAtThreeHundredPRs(t *testing.T) {
	waits := make([]TapWait, 0, 300)
	for number := 1; number <= 300; number++ {
		waits = append(waits, tapWait(number, "api: ✨ a change", "", 0))
	}
	for number := 301; number <= 340; number++ {
		waits = append(waits, tapWait(number, fmt.Sprintf("scope%d: ✨ a change", number), "", 0))
	}
	card := BuildTapCard(repo, tapThreadTS, waits)
	rows := 0
	groupIDs, blockIDs := map[string]bool{}, map[string]bool{}
	for _, block := range card.Children {
		if blockIDs[block.ID] {
			t.Fatalf("duplicate block %s", block.ID)
		}
		blockIDs[block.ID] = true
		if len(block.Groups) > tapBlockGroups {
			t.Fatalf("%s holds %d groups", block.ID, len(block.Groups))
		}
		inBlock := 0
		for _, group := range block.Groups {
			if groupIDs[group.ID] {
				t.Fatalf("duplicate group %s", group.ID)
			}
			groupIDs[group.ID] = true
			if len(group.PRs) > tapGroupRows {
				t.Fatalf("%s holds %d rows, more than one Slack card", group.ID, len(group.PRs))
			}
			inBlock += len(group.PRs)
		}
		if inBlock > tapBlockRows {
			t.Fatalf("%s holds %d rows", block.ID, inBlock)
		}
		rows += inBlock
	}
	if rows != 340 {
		t.Fatalf("rows = %d", rows)
	}
}

func TestTapLinksReadOnlyWhatCCPresentSaysItPosted(t *testing.T) {
	card := BuildTapCard(repo, tapThreadTS, []TapWait{tapWait(1, "api: ✨ one", "", 0), tapWait(2, "sand: ✨ two", "", 0)})
	got := TapLinks(card, "url: http://x\nslack: scope-sand https://slack/p2\nslack: scope-api nothing open, never posted\n")
	if want := []TapLink{{2, head, "https://slack/p2"}}; !reflect.DeepEqual(got, want) {
		t.Fatalf("links = %+v", got)
	}
}
