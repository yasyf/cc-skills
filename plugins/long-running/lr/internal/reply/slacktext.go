package reply

import (
	"cmp"
	"context"
	"encoding/json"
	"fmt"
	"regexp"
	"slices"
	"strconv"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/board"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/cardtext"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

var (
	unfurled    = regexp.MustCompile(`^https://[\w-]+\.slack\.com/archives/[CG][A-Z0-9]+/p\d{16}(?:\?\S*)?$`)
	mention     = regexp.MustCompile(`<https?://[^\s|>]+(?:\|[^>\n]*)?>|\[([^\]\n]+)\]\((https?://[^\s)]+)\)|(https?://[^\s)>\]]*[^\s)>\].,;:!?])|\b(?:([\w.-]+)/)?([\w.-]+)#(\d+)\b|(?:^|[^\w/#&-])(?:PRs? )?#(\d+)\b`)
	numberLabel = regexp.MustCompile(`(?i)^(?:[\w.-]+/)?[\w.-]* ?(?:PR )?#?\d+$`)
	sentenceEnd = regexp.MustCompile(`[.!?] +[A-Z0-9"(\[*_]`)
	listItem    = regexp.MustCompile(`^(?:[-*•>|] |#{1,6} |\d+[.)] |<)`)
	slackHead   = regexp.MustCompile(`(?m)^#{1,6} +(.+?)\s*$`)
)

const postReach = 200

type prNames struct {
	repo   string
	titles map[string]string
	carded []string
	posts  map[string]string
}

// withPRTitles reads the title of every PR the reply names, the drive's own
// from its PR ledger and the rest in one GitHub query, so Slack names each by
// its title. A bare #N takes its title from the ledger alone, since it may
// number a step or a record. The second result is one warning line, or empty.
func (f Fanout) withPRTitles(ctx context.Context, r Reply) (Reply, string) {
	if cfg, err := board.DriveConfig(f.Entry); err == nil {
		r.names.repo = cfg.Repo
	}
	r.names.titles = map[string]string{}
	for _, card := range r.PRCards {
		if card.Title != "" {
			r.names.titles[strings.ToLower(card.URL)] = board.PlainTitle(card.Title)
		}
	}
	var own []int
	var asked []string
	for _, found := range mention.FindAllStringSubmatch(cardtext.HiddenCite.ReplaceAllString(r.MD, " "), -1) {
		url := r.mentionURL(found)
		if url == "" {
			continue
		}
		if repo, number := pullOf(url); strings.EqualFold(repo, r.names.repo) {
			own = append(own, number)
		}
		if found[7] == "" {
			asked = appendNew(asked, url)
		}
	}
	for _, pr := range r.PRs {
		asked = appendNew(asked, pr)
		if repo, number := pullOf(pr); strings.EqualFold(repo, r.names.repo) {
			own = append(own, number)
		}
	}
	if len(own) > 0 {
		if _, rows, err := board.Rows(f.Entry, own); err == nil {
			for _, row := range rows {
				url := strings.ToLower(fmt.Sprintf("https://github.com/%s/pull/%d", r.names.repo, row.Number))
				if row.Title != "" {
					r.names.titles[url] = board.PlainTitle(row.Title)
				}
				if board.OffersApproval(row) {
					r.names.carded = append(r.names.carded, url)
				}
			}
		}
	}
	unread := slices.DeleteFunc(asked, func(url string) bool { return r.names.titles[strings.ToLower(url)] != "" })
	if len(unread) == 0 {
		return r, ""
	}
	read, err := f.githubTitles(ctx, unread)
	if err != nil {
		return r, fmt.Sprintf("the PR titles did not read from GitHub, so Slack names these PRs by number: %v", err)
	}
	for url, title := range read {
		r.names.titles[strings.ToLower(url)] = board.PlainTitle(title)
	}
	return r, ""
}

func (f Fanout) githubTitles(ctx context.Context, urls []string) (map[string]string, error) {
	var query strings.Builder
	query.WriteString("query{")
	for n, url := range urls {
		repo, number := pullOf(url)
		owner, name, _ := strings.Cut(repo, "/")
		fmt.Fprintf(&query, "p%d:repository(owner:%q,name:%q){pullRequest(number:%d){title}}", n, owner, name, number)
	}
	query.WriteString("}")
	done, err := f.Runner.Run(ctx, shell.Command{Argv: []string{"gh", "api", "graphql", "-f", "query=" + query.String()}, Timeout: postTimeout})
	var read struct {
		Data map[string]*struct {
			PullRequest *struct{ Title string } `json:"pullRequest"`
		} `json:"data"`
	}
	if parse := json.Unmarshal([]byte(done.Stdout), &read); parse != nil {
		return nil, cmp.Or(err, parse)
	}
	titles := map[string]string{}
	for n, url := range urls {
		if repo := read.Data["p"+strconv.Itoa(n)]; repo != nil && repo.PullRequest != nil {
			titles[url] = repo.PullRequest.Title
		}
	}
	return titles, nil
}

func (r Reply) mentionURL(found []string) string {
	switch {
	case found[2] != "" || found[3] != "":
		if url := cmp.Or(found[2], found[3]); pullURL.MatchString(url) {
			return url
		}
		return ""
	case found[5] != "":
		repo := found[4] + "/" + found[5]
		if found[4] == "" {
			repo = r.repoNamed(found[5])
		}
		if repo == "" {
			return ""
		}
		return "https://github.com/" + repo + "/pull/" + found[6]
	case found[7] != "" && r.names.repo != "":
		return "https://github.com/" + r.names.repo + "/pull/" + found[7]
	}
	return ""
}

func (r Reply) repoNamed(name string) string {
	for _, repo := range slices.Concat(r.PRs, []string{"https://github.com/" + r.names.repo + "/pull/0"}) {
		if found := pullURL.FindStringSubmatch(repo); found != nil && strings.EqualFold(found[1][strings.Index(found[1], "/")+1:], name) {
			return found[1]
		}
	}
	return ""
}

func (f Fanout) withPostLines(ctx context.Context, r Reply) (Reply, string) {
	var links []string
	for _, found := range mention.FindAllStringSubmatch(cardtext.HiddenCite.ReplaceAllString(r.MD, " "), -1) {
		if unfurled.MatchString(found[3]) {
			links = appendNew(links, found[3])
		}
	}
	for _, link := range r.Links {
		if unfurled.MatchString(link.URL) {
			links = appendNew(links, link.URL)
		}
	}
	r.names.posts = map[string]string{}
	var unread []string
	for _, link := range links {
		line, err := f.postLine(ctx, link)
		if err != nil {
			unread = append(unread, link+": "+err.Error())
			continue
		}
		r.names.posts[link] = line
	}
	if len(unread) > 0 {
		return r, "these Slack posts did not read, so each shows as its bare link instead of its current first line: " + strings.Join(unread, "; ")
	}
	return r, ""
}

func (f Fanout) postLine(ctx context.Context, link string) (string, error) {
	done, err := f.Runner.Run(ctx, shell.Command{Argv: []string{"cc-slack", "thread", "--url", link, "--limit", strconv.Itoa(postReach)}, Timeout: postTimeout})
	if err != nil {
		return "", err
	}
	type message struct {
		TS   string `json:"ts"`
		Text string `json:"text"`
	}
	var thread struct {
		Messages []message `json:"messages"`
	}
	if err := json.Unmarshal([]byte(done.Stdout), &thread); err != nil {
		return "", err
	}
	at := slices.IndexFunc(thread.Messages, func(posted message) bool { return posted.TS == stampOf(link) })
	if at < 0 {
		return "", fmt.Errorf("its post is not among the last %d of its thread", postReach)
	}
	line, _, _ := strings.Cut(thread.Messages[at].Text, "\n")
	line, _, _ = strings.Cut(line, " · ")
	return strings.Trim(strings.NewReplacer("[", "", "]", "").Replace(line), "*_ \t"), nil
}

// slackText is the body Slack shows: each PR once, as its title linked; each
// link where its author wrote it, a bare channel post such as Platy's in
// #releases linked under its current first line; one sentence per line; and
// no first line that repeats the title.
func (r Reply) slackText(md string) string {
	named := map[string]bool{}
	var shared []string
	lines := strings.Split(strings.TrimSpace(md), "\n")
	if sameLine(lines[0], r.Title) {
		lines = lines[1:]
	}
	var out []string
	fenced := false
	for _, line := range lines {
		fence := strings.HasPrefix(strings.TrimSpace(line), "```")
		if fenced = fenced != fence; fenced || fence {
			out = append(out, line)
			continue
		}
		line = r.inSlack(r.namedOnce(line, named))
		if line == "" && len(out) > 0 && out[len(out)-1] == "" {
			continue
		}
		out = append(out, prose(line)...)
	}
	for _, link := range r.Links {
		if unfurled.MatchString(link.URL) {
			shared = appendNew(shared, r.postLink(link.URL))
		}
	}
	var footer []string
	for _, pr := range r.PRs {
		key := strings.ToLower(pr)
		if named[key] || len(r.Picks) == 0 && slices.Contains(r.names.carded, key) {
			continue
		}
		named[key] = true
		footer = append(footer, "- "+r.titled(pr))
	}
	body := strings.TrimSpace(strings.Join(out, "\n"))
	for _, tail := range [][]string{footer, shared} {
		if len(tail) > 0 {
			body += "\n\n" + strings.Join(tail, "\n")
		}
	}
	return strings.TrimSpace(body)
}

// namedOnce names a PR's first mention in the line by its title linked and a
// later one by its title alone; inline code stays as written.
func (r Reply) namedOnce(line string, named map[string]bool) string {
	parts := strings.Split(line, "`")
	for n := 0; n < len(parts); n += 2 {
		parts[n] = mention.ReplaceAllStringFunc(parts[n], func(said string) string {
			if strings.HasPrefix(said, "<") {
				return said
			}
			found := mention.FindStringSubmatch(said)
			lead := ""
			if found[7] != "" && !strings.HasPrefix(said, "#") && !strings.HasPrefix(said, "PR") {
				lead, said = said[:1], said[1:]
			}
			if r.names.posts[found[3]] != "" {
				return lead + r.postLink(found[3])
			}
			url := r.mentionURL(found)
			if url == "" {
				return lead + said
			}
			key := strings.ToLower(url)
			title := r.names.titles[key]
			wrote := found[1] != "" && !numberLabel.MatchString(found[1]) && !strings.EqualFold(found[1], url)
			seen := named[key]
			named[key] = true
			switch {
			case wrote && seen:
				return lead + found[1]
			case wrote, title == "":
				return lead + said
			case seen:
				return lead + title
			}
			return lead + "[" + title + "](" + url + ")"
		})
	}
	return strings.Join(parts, "`")
}

func (r Reply) postLink(url string) string {
	if line := r.names.posts[url]; line != "" {
		return "[" + line + "](" + url + ")"
	}
	return url
}

func (r Reply) titled(pr string) string {
	if title := r.names.titles[strings.ToLower(pr)]; title != "" {
		return "[" + title + "](" + pr + ")"
	}
	return pr
}

func prose(line string) []string {
	if listItem.MatchString(strings.TrimSpace(line)) {
		return []string{line}
	}
	var lines []string
	for at := sentenceEnd.FindStringIndex(line); at != nil; at = sentenceEnd.FindStringIndex(line) {
		lines = append(lines, line[:at[0]+1])
		line = line[at[1]-1:]
	}
	return append(lines, line)
}

func sameLine(line, title string) bool {
	plain := func(s string) string {
		return strings.ToLower(strings.TrimRight(strings.Trim(strings.TrimLeft(strings.TrimSpace(s), "# "), "*_"), ".:!"))
	}
	return title != "" && plain(line) == plain(title)
}

func appendNew(list []string, item string) []string {
	if slices.ContainsFunc(list, func(had string) bool { return strings.EqualFold(had, item) }) {
		return list
	}
	return append(list, item)
}

func pullOf(url string) (string, int) {
	found := pullURL.FindStringSubmatch(url)
	number, _ := strconv.Atoi(found[2])
	return found[1], number
}
