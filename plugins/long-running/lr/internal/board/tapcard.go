package board

import (
	"cmp"
	"fmt"
	"regexp"
	"slices"
	"strings"
	"unicode"
)

const (
	TapCardID  = "card-tap-waiting"
	TapBlockID = "merge-prs-waiting"

	tapGroupRows   = 20
	tapBlockGroups = 30
	tapBlockRows   = 150
	tapLineChars   = 400
	tapHeadChars   = 7
)

var (
	tapHeading = regexp.MustCompile(`(?m)^#{1,6}\s+(.+)$`)
	tapSlug    = regexp.MustCompile(`[^a-z0-9]+`)
	tapLink    = regexp.MustCompile(`(?m)^slack: (\S+) (https?://\S+)$`)
)

type Pull struct {
	Number int    `json:"number"`
	Title  string `json:"title"`
	Body   string `json:"body"`
	Head   string `json:"headRefOid"`
	Branch string `json:"headRefName"`
	Base   string `json:"baseRefName"`
}

type TapWait struct {
	Pull  Pull
	Head  string
	Below int
}

type TapRow struct {
	Number  int    `json:"number"`
	Head    string `json:"head"`
	Title   string `json:"title"`
	Change  string `json:"change"`
	Plan    string `json:"plan"`
	Risk    string `json:"risk"`
	Review  string `json:"review"`
	Blocker string `json:"blocker"`
}

type TapGroup struct {
	ID      string   `json:"id"`
	Title   string   `json:"title"`
	Summary string   `json:"summary"`
	PRs     []TapRow `json:"prs"`
}

type TapBlock struct {
	ID     string     `json:"id"`
	Type   string     `json:"type"`
	Repo   string     `json:"repo"`
	Prompt string     `json:"prompt"`
	Slack  string     `json:"slack"`
	Groups []TapGroup `json:"groups"`
}

type TapCard struct {
	ID       string     `json:"id"`
	Type     string     `json:"type"`
	Title    string     `json:"title"`
	Summary  string     `json:"summary"`
	Children []TapBlock `json:"children"`
}

type TapLink struct {
	PR        int    `json:"pr"`
	Head      string `json:"head"`
	Permalink string `json:"permalink"`
}

func BuildTapCard(repo, thread string, waits []TapWait) TapCard {
	return TapCard{
		ID:       TapCardID,
		Type:     "card",
		Title:    "Pull requests waiting on your tap",
		Summary:  "Each is graded clean, green and approved; a tap merges it at the commit shown.",
		Children: tapBlocks(repo, thread, tapGroups(waits)),
	}
}

func TapLinks(card TapCard, stdout string) []TapLink {
	permalinks := map[string]string{}
	for _, found := range tapLink.FindAllStringSubmatch(stdout, -1) {
		permalinks[found[1]] = found[2]
	}
	links := []TapLink{}
	for _, block := range card.Children {
		for _, group := range block.Groups {
			permalink, posted := permalinks[group.ID]
			if !posted {
				continue
			}
			for _, row := range group.PRs {
				links = append(links, TapLink{row.Number, row.Head, permalink})
			}
		}
	}
	return links
}

func tapBlocks(repo, thread string, groups []TapGroup) []TapBlock {
	blocks := make([]TapBlock, 0, 1)
	rows := 0
	for _, group := range groups {
		count := len(blocks)
		if count == 0 || len(blocks[count-1].Groups) == tapBlockGroups || rows+len(group.PRs) > tapBlockRows {
			id := TapBlockID
			if count > 0 {
				id = fmt.Sprintf("%s-%d", TapBlockID, count+1)
			}
			blocks = append(blocks, TapBlock{ID: id, Type: "pr-groups", Repo: repo, Prompt: "Approve each pull request to merge at the commit shown?", Slack: thread})
			rows = 0
		}
		last := &blocks[len(blocks)-1]
		last.Groups = append(last.Groups, group)
		rows += len(group.PRs)
	}
	return blocks
}

func tapGroups(waits []TapWait) []TapGroup {
	drawn := make(map[int]TapWait, len(waits))
	for _, wait := range waits {
		drawn[wait.Pull.Number] = wait
	}
	bottoms := make(map[int]int, len(waits))
	depths := make(map[int]int, len(waits))
	sizes := make(map[int]int, len(waits))
	for _, wait := range waits {
		bottom, depth := wait.Pull.Number, 0
		for below, stacked := drawn[wait.Below]; stacked; below, stacked = drawn[below.Below] {
			bottom, depth = below.Pull.Number, depth+1
		}
		bottoms[wait.Pull.Number], depths[wait.Pull.Number] = bottom, depth
		sizes[bottom]++
	}
	sorted := slices.Clone(waits)
	slices.SortFunc(sorted, func(one, other TapWait) int { return cmp.Compare(one.Pull.Number, other.Pull.Number) })
	keyed := make(map[string][]TapWait, len(waits))
	order := make([]string, 0, len(waits))
	for _, wait := range sorted {
		key := "scope-" + tapScopeSlug(wait.Pull.Title)
		if bottom := bottoms[wait.Pull.Number]; sizes[bottom] > 1 {
			key = fmt.Sprintf("stack-%d", bottom)
		}
		if _, seen := keyed[key]; !seen {
			order = append(order, key)
		}
		keyed[key] = append(keyed[key], wait)
	}
	groups := make([]TapGroup, 0, len(order))
	for _, key := range order {
		found := keyed[key]
		stack := strings.HasPrefix(key, "stack-")
		if stack {
			slices.SortStableFunc(found, func(one, other TapWait) int {
				return cmp.Compare(depths[one.Pull.Number], depths[other.Pull.Number])
			})
		}
		title, summary := tapGroupWords(found, stack)
		for i, chunk := range slices.Collect(slices.Chunk(found, tapGroupRows)) {
			group := TapGroup{ID: key, Title: title, Summary: summary, PRs: tapRows(chunk)}
			if i > 0 {
				group.ID, group.Title = fmt.Sprintf("%s-%d", key, i+1), fmt.Sprintf("%s (part %d)", title, i+1)
			}
			groups = append(groups, group)
		}
	}
	return groups
}

func tapGroupWords(waits []TapWait, stack bool) (string, string) {
	first := waits[0].Pull
	if stack {
		numbers := make([]string, len(waits))
		for i, wait := range waits {
			numbers[i] = fmt.Sprintf("#%d", wait.Pull.Number)
		}
		return tapPlainTitle(first.Title), "One stack that lands bottom first: " + strings.Join(numbers, ", ") + "."
	}
	if len(waits) == 1 {
		return tapPlainTitle(first.Title), tapRow(waits[0]).Change
	}
	scope, _, named := strings.Cut(first.Title, ": ")
	if !named {
		scope = "Other changes"
	}
	return fmt.Sprintf("%s: %d pull requests", scope, len(waits)), "Separate changes, each with its own tap."
}

func tapRows(waits []TapWait) []TapRow {
	rows := make([]TapRow, len(waits))
	for i, wait := range waits {
		rows[i] = tapRow(wait)
	}
	return rows
}

func tapRow(wait TapWait) TapRow {
	pull := wait.Pull
	blocker := "Only your tap."
	if wait.Below != 0 {
		blocker = fmt.Sprintf("Your tap, and #%d below it lands first.", wait.Below)
	}
	return TapRow{
		Number:  pull.Number,
		Head:    wait.Head,
		Title:   tapLine(pull.Title),
		Change:  tapLine(cmp.Or(tapParagraph(pull.Body, "summary"), tapParagraph(pull.Body, "context"), tapPlainTitle(pull.Title))),
		Plan:    tapLine(cmp.Or(tapParagraph(pull.Body, "plan"), "The PR names no difference from the plan.")),
		Risk:    tapLine(cmp.Or(tapSection(pull.Body, "risk"), "The PR names no risk.")),
		Review:  fmt.Sprintf("Fidelity ok, finder clear, CI green and approved at %s.", wait.Head[:min(tapHeadChars, len(wait.Head))]),
		Blocker: blocker,
	}
}

func tapScopeSlug(title string) string {
	scope, _, named := strings.Cut(title, ": ")
	if !named {
		return "other"
	}
	return cmp.Or(strings.Trim(tapSlug.ReplaceAllString(strings.ToLower(scope), "-"), "-"), "other")
}

func tapPlainTitle(title string) string {
	_, rest, named := strings.Cut(title, ": ")
	if !named {
		return title
	}
	plain := strings.TrimLeftFunc(rest, func(r rune) bool { return !unicode.IsLetter(r) && !unicode.IsDigit(r) })
	return cmp.Or(plain, title)
}

func tapParagraph(body, word string) string {
	first, _, _ := strings.Cut(tapSection(body, word), "\n\n")
	return first
}

func tapSection(body, word string) string {
	headings := tapHeading.FindAllStringSubmatchIndex(body, -1)
	for i, heading := range headings {
		named := strings.FieldsFunc(strings.ToLower(body[heading[2]:heading[3]]), func(r rune) bool { return !unicode.IsLetter(r) })
		if !slices.Contains(named, word) {
			continue
		}
		end := len(body)
		if i+1 < len(headings) {
			end = headings[i+1][0]
		}
		section, _, _ := strings.Cut(body[heading[1]:end], "<details")
		return strings.TrimSpace(strings.ReplaceAll(section, "\r\n", "\n"))
	}
	return ""
}

func tapLine(text string) string {
	runes := []rune(strings.Join(strings.Fields(text), " "))
	if len(runes) <= tapLineChars {
		return string(runes)
	}
	return string(runes[:tapLineChars-1]) + "…"
}
