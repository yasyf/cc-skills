package reply

import (
	"cmp"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"log/slog"
	"maps"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"sync"
	"time"
	"unicode"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/arch"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/board"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/cardtext"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/owner"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/talkboard"
)

const (
	communicator   = "owner-relay"
	storeDir       = "replies"
	postTimeout    = 30 * time.Second
	nearestRulings = 3
	tapTimeout     = 90 * time.Second
	maxTitle       = 140
	maxPick        = 200
	maxPicks       = 4
	pickSplit      = " :: "
	maxLinks       = 5
	maxPRs         = 10
	maxBlocks      = 8
	cardPrefix     = "card-"
	withdrawn      = " withdrawn"
	alreadyGone    = "no block "
	pickLink       = "Pick on the card"
	needsYou       = "needs_you"
	progress       = "progress"
	info           = "info"
	incident       = "incident"
	done           = "done"

	typeExplanation = "explanation"
	typeUpdate      = "update"
	typeForYou      = "for_you"
	typeQuestion    = "question"

	threadIndex = "thread index"
	linePrefix  = "line:"

	untiedPick = "Picking this approves no PR by itself: this card shows no single PR at one head, so a lane carries your yes by hand."

	namesBugKind    = "names-bug"
	cardCopySurface = "card copy"
	movedRoot       = "slack root"
	resendOnce      = "; nothing went out on this surface, so once the tool is back send this reply again with its id, which posts it once"
)

var (
	statuses   = []string{info, progress, needsYou, incident, done}
	types      = []string{typeExplanation, typeUpdate, typeForYou, typeQuestion, incident, done}
	orderRow   = regexp.MustCompile(`^m\d+(?:\.\d+)?$`)
	replyID    = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,47}$`)
	pullURL    = regexp.MustCompile(`^https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)$`)
	slackSent  = regexp.MustCompile(`(?m)^sent: \S+ ts=\d{10}\.\d{6} (\S+)`)
	mdLink     = regexp.MustCompile(`\[([^\]\n]+)\]\((https?://[^\s)]+)\)`)
	cardLink   = regexp.MustCompile(`\[([^\]\n]*)\]\(card:([^\s)]+)\)`)
	notebook   = regexp.MustCompile(`https://[\w.-]*datadoghq\.com/notebook/\d+`)
	slackStamp = regexp.MustCompile(`/p(\d{10})(\d{6})(?:\?|$)`)
	laneRole   = regexp.MustCompile(`-(?:fix|evidence|comms)$`)
	sentence   = regexp.MustCompile(`[.!?](?:\s+|$)|\n`)
	tapOrder   = regexp.MustCompile(`(?i)^\s*(?:tap|approve)\b`)
	asksTap    = regexp.MustCompile(`(?i)\b(?:waits?|waiting|ready|needs?|asks?)\b[^.\n]{0,60}\b(?:your (?:tap|approval|answer|yes)|on you\b)|\bapprove or hold\b|\b(?:approve|tap) (?:to land|it\b|here\b|#\d)`)
	describes  = regexp.MustCompile(`(?i)\b(?:refus\w*|buttons?)\b`)
	notLanding = regexp.MustCompile(`(?i)\b(?:is|was|got|been|now|stays?|PR|it) closed\b|#\d+,? (?:is |was )?closed\b|\bclos(?:e|es|ed|ing) (?:#\d+|it\b|this\b|unlanded|unmerged|without)|\bunlanded\b|\bwithdr(?:aw|awn|ew|awing)\b|\b(?:not|never|won't|will not|does not|doesn't) land(?:s|ing)?\b|\babandon(?:ed|ing)?\b`)
	namedPR    = regexp.MustCompile(`(?i)github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)|([\w.-]+)#(\d+)\b|(?:^|[^\w/#-])#(\d+)\b|\bPRs? #?(\d+)\b`)
	repoName   = regexp.MustCompile(`^[\w.-]+/[\w.-]+$`)
	namedBlock = regexp.MustCompile(`block "([^"]+)"`)
	strayTag   = regexp.MustCompile(`</?[a-zA-Z][\w-]*(?:[\s/>]|$)`)
	notBranch  = regexp.MustCompile(`^$|^[-/.]|[/.]$|^@$|[\x00-\x20\x7f~^:?*\[\\]|\.\.|@\{|//|/\.|\.lock(?:/|$)`)
)

type Link struct {
	Label string `json:"label"`
	URL   string `json:"url"`
}

type Build struct {
	Repo   string `json:"repo,omitempty"`
	Branch string `json:"branch,omitempty"`
	None   string `json:"none,omitempty"`
}

// Lines are a PR's four plain lines, read from its row in the PR ledger.
type Lines struct {
	Change string `json:"change,omitempty"`
	Plan   string `json:"plan,omitempty"`
	Risk   string `json:"risk,omitempty"`
	Blocks string `json:"blocks,omitempty"`
}

// prCard is one PR a pick card draws, with its lines read from the PR ledger.
type prCard struct {
	URL    string `json:"url"`
	Head   string `json:"head,omitempty"`
	Title  string `json:"title,omitempty"`
	Review string `json:"review,omitempty"`
	Lines
}

type Reply struct {
	ID          string           `json:"id"`
	Title       string           `json:"title"`
	MD          string           `json:"md"`
	Status      string           `json:"status,omitempty"`
	Type        string           `json:"type,omitempty"`
	Picks       []string         `json:"picks,omitempty"`
	Recommended string           `json:"recommended,omitempty"`
	Ask         int64            `json:"ask,omitempty"`
	Build       *Build           `json:"build,omitempty"`
	Message     *Message         `json:"message,omitempty"`
	Links       []Link           `json:"links,omitempty"`
	PRs         []string         `json:"prs,omitempty"`
	PRCards     []prCard         `json:"pr_cards,omitempty"`
	OpenCards   []board.OpenCard `json:"-"`
	Thread      string           `json:"thread,omitempty"`
	Order       string           `json:"order,omitempty"`
	Alert       string           `json:"alert,omitempty"`
	Unprompted  bool             `json:"unprompted,omitempty"`
	Action      string           `json:"action,omitempty"`
	By          string           `json:"by,omitempty"`
	Routed      int              `json:"routed,omitempty"`
	Artifacts   []Artifact       `json:"artifacts,omitempty"`

	Blocks  []json.RawMessage `json:"blocks,omitempty"`
	Diagram json.RawMessage   `json:"diagram,omitempty"`
	On      string            `json:"on,omitempty"`

	Words map[string]string `json:"words,omitempty"`

	diagram Artifact
	names   prNames
}

type Outcome struct {
	Surface string `json:"surface"`
	Card    string `json:"card,omitempty"`
	Ref     string `json:"ref,omitempty"`
	Error   string `json:"error,omitempty"`
	Warning string `json:"warning,omitempty"`
}

type delivery struct {
	Reply    Reply     `json:"reply"`
	At       time.Time `json:"at"`
	Re       string    `json:"re,omitempty"`
	Surfaces []Outcome `json:"surfaces"`
}

type Surface func(ctx context.Context, r Reply, sent []Outcome) []Outcome

type step struct {
	surface Surface
	reply   Reply
}

type Fanout struct {
	Runner  shell.Runner
	Entry   drive.Entry
	Now     func() time.Time
	Log     *slog.Logger
	Judge   board.Judge
	Shoot   func(ctx context.Context, dashboard string, diagram json.RawMessage, png string) error
	Cards   board.Cards
	Rulings string
}

func (f Fanout) Send(ctx context.Context, r Reply) []Outcome {
	r = f.aliased(r).typed()
	seen := r
	seen.MD = cardtext.HiddenCite.ReplaceAllString(r.MD, "")
	seen.Words = maps.Clone(r.Words)
	for pick, said := range seen.Words {
		seen.Words[pick] = cardtext.HiddenCite.ReplaceAllString(said, "")
	}
	told := seen.diagramNoted().artifactsLinked()
	steps := []step{{f.board, told}, {f.thread, told}, {f.slack, told}, {f.cardCopies, told}, {f.record, r}, {f.dashboard, seen}, {f.ledger, r}}
	switch {
	case r.quiet():
		steps = []step{{f.board, told}, {f.slack, told}, {f.cardCopies, told}, {f.record, r}, {f.dashboard, seen}, {f.ledger, r}}
	case !r.writesRow():
		steps = steps[:len(steps)-1]
	}
	sent := make([]Outcome, 0, 6)
	for _, s := range steps {
		sent = append(sent, s.surface(ctx, s.reply, sent)...)
	}
	return append(sent, f.indexThreads(r, sent)...)
}

func (f Fanout) indexThreads(r Reply, sent []Outcome) []Outcome {
	s, err := store.Open(f.Entry.Store())
	if err != nil {
		return []Outcome{{Surface: threadIndex, Error: err.Error()}}
	}
	defer func() { _ = s.Close() }()
	var missed []Outcome
	for _, o := range sent {
		if !strings.HasPrefix(o.Surface, "slack") || owner.ThreadRoot(o.Ref) == "" {
			continue
		}
		if err := s.PutSlackThread(owner.ReplyThread(o.Ref, r.ID, r.Order, r.Title, r.Picks, owner.PullsIn(f.Entry.Repo, r.PRs))); err != nil {
			missed = append(missed, Outcome{Surface: threadIndex, Error: err.Error()})
		}
		if r.Alert == "" {
			continue
		}
		if err := s.PutAlertThread(r.Alert, store.AlertThread{Reply: r.ID, TS: owner.ThreadRoot(o.Ref)}); err != nil {
			missed = append(missed, Outcome{Surface: threadIndex, Error: err.Error()})
		}
	}
	return missed
}

// withCardLinks links each open card the reply names as card:ID, longest id
// first so no card's name is taken for a shorter one's; each surface resolves
// the link when it draws, and inSlack resolves it to the Slack message.
// A reply that asks for his tap and links no card is refused by nothingToTap;
// any other posts whatever it finds: the second result is one warning line when
// the board did not read, or empty.
func (f Fanout) withCardLinks(ctx context.Context, r Reply) (Reply, string) {
	outcomes, err := board.ReadOutcomes(ctx, f.Runner, f.Entry.Root())
	if err != nil {
		return r, fmt.Sprintf("the board did not read, so no card this names carries its Slack link: %v", err)
	}
	cards := slices.DeleteFunc(outcomes.OpenCards(), func(card board.OpenCard) bool {
		return card.ID == cardPrefix+r.ID || linkedCard(r.MD, card) == r.MD && !strings.Contains(r.MD, card.URL) && !strings.Contains(r.MD, "(card:"+card.ID+")")
	})
	slices.SortFunc(cards, func(one, other board.OpenCard) int { return cmp.Compare(len(other.ID), len(one.ID)) })
	checking, stop := context.WithTimeout(ctx, postTimeout)
	defer stop()
	settled, checked := board.Cards{Runner: f.Runner, Checkout: f.Entry.Checkout}.SettledByHim(checking, f.Judge, cards)
	var warned []string
	if checked != nil {
		warned = append(warned, fmt.Sprintf("his answers did not read for every card this names, so one he settled may still be linked: %v", checked))
	}
	for _, card := range cards {
		if found := settled[card.ID]; found != nil {
			then := "run `lr board cards sweep` to take it down"
			if !found.Closes {
				then = "no tap or numbered reply of his closes it, so the next `lr board cards sweep` tells the lane that drew it to withdraw it or say why it stays"
			}
			warned = append(warned, fmt.Sprintf("%s is not linked because %s; take the words off this reply, and %s", card.ID, found.Line(), then))
			continue
		}
		r.MD = linkedCard(r.MD, card)
		r.OpenCards = append(r.OpenCards, card)
	}
	return r, strings.Join(warned, "; ")
}

func (r Reply) namedPRs() ([][]string, []int) {
	said := namedPR.FindAllStringSubmatch(r.Title+"\n"+r.MD+"\n"+strings.Join(r.PRs, "\n"), -1)
	numbers := make([]int, 0, len(said))
	for _, found := range said {
		number, _ := strconv.Atoi(cmp.Or(found[2], found[4], found[5], found[6]))
		numbers = append(numbers, number)
	}
	return said, numbers
}

func (r Reply) saidNotLanding() []int {
	var numbers []int
	for _, said := range sentence.Split(cardtext.HiddenCite.ReplaceAllString(r.Title+"\n"+r.MD, " "), -1) {
		if !notLanding.MatchString(said) {
			continue
		}
		for _, found := range namedPR.FindAllStringSubmatch(said, -1) {
			number, _ := strconv.Atoi(cmp.Or(found[2], found[4], found[5], found[6]))
			numbers = append(numbers, number)
		}
	}
	return numbers
}

func inRepo(repo string, found []string) bool {
	return strings.HasSuffix(strings.ToLower(repo), strings.ToLower(cmp.Or(found[1], found[3], repo)))
}

func (f Fanout) withPortOpening(r Reply) (Reply, string) {
	said, numbers := r.namedPRs()
	if len(slices.Compact(slices.Sorted(slices.Values(numbers)))) != 1 {
		return r, ""
	}
	repo, ports, err := board.Ports(f.Entry, numbers)
	if err != nil {
		return r, fmt.Sprintf("the PR ledger did not read, so no port this names opens the reply with %s#n: %v", cardtext.PortOpener, err)
	}
	var opening []string
	for n, found := range said {
		source, port := ports[numbers[n]]
		if !port || !inRepo(repo, found) {
			continue
		}
		line := fmt.Sprintf("%s[#%d](https://github.com/%s/pull/%d)", cardtext.PortOpener, source, repo, source)
		opens := regexp.MustCompile(fmt.Sprintf(`(?i)^\s*%s\[?#%d\b`, regexp.QuoteMeta(cardtext.PortOpener), source))
		if !slices.Contains(opening, line) && !opens.MatchString(r.Title) && !opens.MatchString(r.MD) {
			opening = append(opening, line)
		}
	}
	if len(opening) > 0 {
		r.MD = strings.Join(opening, ", ") + "\n\n" + r.MD
	}
	return r, ""
}

func (f Fanout) nothingToTap(ctx context.Context, r Reply) error {
	asks := slices.ContainsFunc(sentence.Split(cardtext.HiddenCite.ReplaceAllString(r.Title+"\n"+r.MD, " "), -1), func(said string) bool {
		return tapOrder.MatchString(said) || asksTap.MatchString(said) && !describes.MatchString(said)
	})
	if len(r.Picks) > 0 || len(r.OpenCards) > 0 || !asks {
		return nil
	}
	numbers := make([]int, len(r.PRs))
	for n, pr := range r.PRs {
		numbers[n], _ = strconv.Atoi(pullURL.FindStringSubmatch(pr)[2])
	}
	if len(numbers) > 0 {
		_, rows, err := board.Rows(f.Entry, numbers)
		if err != nil || slices.ContainsFunc(rows, func(row store.ReviewRow) bool {
			return slices.Contains(numbers, row.Number) && board.OffersApproval(row)
		}) {
			return nil
		}
	}
	const refused = "NOTHING TO TAP: this asks him to tap, approve or answer, and it carries no picks, no open PR he can approve and no open card, so he would get a message with nothing to tap and nothing went out; "
	outcomes, err := board.ReadOutcomes(ctx, f.Runner, f.Entry.Root())
	if err != nil {
		return errors.New(refused + "his board did not read, so no open card was found to copy into this message: " + err.Error())
	}
	cards := slices.DeleteFunc(outcomes.OpenCards(), func(card board.OpenCard) bool {
		return card.ID == cardPrefix+r.ID || linkedCard(r.MD, card) != r.MD
	})
	if len(cards) == 0 {
		return errors.New(refused + "no card on his board is open, so give him the picks he answers in this reply")
	}
	nearest := slices.MaxFunc(cards, func(one, other board.OpenCard) int {
		return cmp.Compare(sharedWords(one.Title, r.Title+" "+r.MD), sharedWords(other.Title, r.Title+" "+r.MD))
	})
	thread := cmp.Or(r.Thread, "<the ts this reply posts at>")
	return fmt.Errorf("%sthe nearest open card is %q: name it in md by that title or by %s, and this reply copies it into its own thread, or copy it with `cc-slack card-copy --url %s --thread %s`", refused, nearest.Title, strings.TrimPrefix(nearest.ID, cardPrefix), nearest.URL, thread)
}

func sharedWords(title, said string) int {
	split := func(text string) []string {
		return strings.FieldsFunc(strings.ToLower(text), func(r rune) bool { return !unicode.IsLetter(r) && !unicode.IsNumber(r) })
	}
	words := split(said)
	shared := 0
	for _, word := range split(title) {
		if len(word) > 3 && slices.Contains(words, word) {
			shared++
		}
	}
	return shared
}

func (f Fanout) approvesTapped(r Reply) error {
	if len(r.PRs) == 0 || !slices.ContainsFunc(r.Picks, bus.ReadsAsLand) {
		return nil
	}
	numbers := make([]int, len(r.PRs))
	for n, pr := range r.PRs {
		numbers[n], _ = strconv.Atoi(pullURL.FindStringSubmatch(pr)[2])
	}
	repo, rows, err := board.Rows(f.Entry, numbers)
	if errors.Is(err, fs.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	var tapped []string
	for _, row := range rows {
		if row.Tapped && slices.Contains(numbers, row.Number) {
			tapped = append(tapped, fmt.Sprintf("https://github.com/%s/pull/%d", repo, row.Number))
		}
	}
	if len(tapped) == 0 {
		return nil
	}
	return fmt.Errorf("ALREADY TAPPED: he tapped %s, and his tap counts once for a PR, so nothing went out; no card asks him to Approve it again unless a push its head record marks --attr delta=fundamental changes it, and the runner draws that card itself", strings.Join(tapped, ", "))
}

func (f Fanout) rulingsWarning(ctx context.Context, r Reply) string {
	if len(r.Picks) == 0 || f.Rulings == "" {
		return ""
	}
	done, err := f.Runner.Run(ctx, shell.Command{
		Argv:  []string{f.Rulings, "match", "--drive", f.Entry.Drive, "--repo", f.Entry.Checkout, "-k", strconv.Itoa(nearestRulings)},
		Stdin: r.Title + "\n" + strings.Join(r.Picks, "\n"), Timeout: tapTimeout,
	})
	if err != nil {
		return fmt.Sprintf("his register did not read, so no pick was checked against a ruling he already gave: %v", err)
	}
	var nearest []string
	for line := range strings.Lines(done.Stdout) {
		if row, found := strings.CutPrefix(strings.TrimSpace(line), "- "); found {
			nearest = append(nearest, row)
		}
	}
	if len(nearest) == 0 {
		return ""
	}
	return "his rulings nearest this question are " + strings.Join(nearest, "; ") + "; drop any pick one of them already settles"
}

func (f Fanout) tapWarning(r Reply) string {
	said, numbers := r.namedPRs()
	if len(numbers) == 0 {
		return ""
	}
	repo, rows, err := board.Rows(f.Entry, numbers)
	if err != nil {
		return fmt.Sprintf("the PR ledger did not read, so no PR this names was checked for a tap he owes: %v", err)
	}
	var missing, tapped, standing, exempt []string
	for n, found := range said {
		at := slices.IndexFunc(rows, func(row store.ReviewRow) bool { return row.Number == numbers[n] })
		if at < 0 || !inRepo(repo, found) {
			continue
		}
		url := fmt.Sprintf("https://github.com/%s/pull/%d", repo, numbers[n])
		listed := func(pr string) bool { return strings.EqualFold(pr, url) }
		switch {
		case rows[at].Tapped && len(r.Picks) > 0 && !slices.Contains(tapped, url):
			tapped = append(tapped, url)
		case prrow.LandsOnHisWord(rows[at]) && len(r.Picks) > 0 && !slices.Contains(standing, url):
			standing = append(standing, url)
		case rows[at].State == prrow.StateExempt && len(r.Picks) > 0 && !slices.Contains(exempt, url):
			exempt = append(exempt, url)
		case len(r.Picks) > 0 && board.OffersApproval(rows[at]) && !slices.ContainsFunc(r.PRs, listed) && !slices.Contains(missing, url):
			missing = append(missing, url)
		}
	}
	var warned []string
	if len(tapped) > 0 {
		warned = append(warned, "this asks him to pick on a PR he already tapped, so take it off the question: "+strings.Join(tapped, ", "))
	}
	if len(standing) > 0 {
		warned = append(warned, "this asks him to pick on a PR that lands on his standing word with no tap, so take it off the question: "+strings.Join(standing, ", "))
	}
	if len(exempt) > 0 {
		warned = append(warned, "this asks him to pick on a PR that lands exempt with no tap, so take it off the question: "+strings.Join(exempt, ", "))
	}
	if len(missing) > 0 {
		warned = append(warned, "this question names a PR he can approve and does not list it, so its card carries no Approve; edit the reply with it in prs: "+strings.Join(missing, ", "))
	}
	return strings.Join(warned, "; ")
}

func (f Fanout) withNamedPRs(r Reply) (Reply, string) {
	if len(r.Picks) > 0 {
		return r, ""
	}
	seen := r
	seen.MD = cardtext.HiddenCite.ReplaceAllString(r.MD, " ")
	said, numbers := seen.namedPRs()
	if len(numbers) == 0 {
		return r, ""
	}
	repo, rows, err := board.Rows(f.Entry, numbers)
	if err != nil {
		return r, fmt.Sprintf("the PR ledger did not read, so no PR this names drew its Approve: %v", err)
	}
	var over []string
	closed := r.saidNotLanding()
	for n, found := range said {
		at := slices.IndexFunc(rows, func(row store.ReviewRow) bool { return row.Number == numbers[n] })
		url := fmt.Sprintf("https://github.com/%s/pull/%d", repo, numbers[n])
		switch {
		case at < 0 || !inRepo(repo, found) || !board.OffersApproval(rows[at]) || slices.Contains(closed, numbers[n]):
		case slices.ContainsFunc(r.PRs, func(pr string) bool { return strings.EqualFold(pr, url) }):
		case len(r.PRs) >= maxPRs:
			if !slices.Contains(over, url) {
				over = append(over, url)
			}
		default:
			r.PRs = append(r.PRs, url)
		}
	}
	if len(over) > 0 {
		return r, fmt.Sprintf("this names more PRs than the %d a reply carries, so these draw no Approve: %s", maxPRs, strings.Join(over, ", "))
	}
	return r, ""
}

func (f Fanout) withBugFixes(ctx context.Context, r Reply) (Reply, string) {
	repo, fixes, err := board.BugFixes(f.Entry)
	if err != nil {
		return r, fmt.Sprintf("the PR ledger did not read, so no bug this names was checked for a fix PR he can tap: %v", err)
	}
	said := r.Title + "\n" + r.MD
	_, numbers := r.namedPRs()
	var named, asked []string
	for _, item := range slices.Sorted(maps.Keys(fixes)) {
		switch {
		case slices.ContainsFunc(fixes[item], func(row store.ReviewRow) bool { return slices.Contains(numbers, row.Number) }):
		case slices.Contains(prrow.Items(said), item):
			named = append(named, item)
		default:
			asked = append(asked, item)
		}
	}
	judging, stop := context.WithTimeout(ctx, postTimeout)
	defer stop()
	verdicts, failures := make([]*board.Judgment, len(asked)), make([]error, len(asked))
	var running sync.WaitGroup
	for n, item := range asked {
		evidence := []string{"Open pull requests in the drive fix one bug a person reported. Each is its title, then what it changes for a user:"}
		for _, row := range fixes[item] {
			evidence = append(evidence, "- "+row.Title+": "+row.ForUsers)
		}
		running.Go(func() { verdicts[n], failures[n] = f.Judge(judging, namesBugKind, strings.Join(evidence, "\n"), said) })
	}
	running.Wait()
	var unsure, unjudged []string
	for n, item := range asked {
		switch {
		case failures[n] != nil:
			unjudged = append(unjudged, failures[n].Error())
		case verdicts[n] == nil:
			unjudged = append(unjudged, "its model call failed for item "+item)
		case verdicts[n].Verdict == "yes":
			named = append(named, item)
		case verdicts[n].Verdict == "unsure":
			for _, row := range fixes[item] {
				unsure = append(unsure, fmt.Sprintf("https://github.com/%s/pull/%d", repo, row.Number))
			}
		}
	}
	approves := slices.ContainsFunc(r.Picks, bus.ReadsAsLand)
	var drawn []store.ReviewRow
	for _, item := range named {
		for _, row := range fixes[item] {
			if !slices.ContainsFunc(drawn, func(other store.ReviewRow) bool { return other.Number == row.Number }) {
				drawn = append(drawn, row)
			}
		}
	}
	var untying, unlisted []string
	for _, row := range drawn {
		url := fmt.Sprintf("https://github.com/%s/pull/%d", repo, row.Number)
		switch {
		case slices.ContainsFunc(r.PRs, func(pr string) bool { return strings.EqualFold(pr, url) }):
		case approves:
			untying = append(untying, url)
		default:
			unlisted = append(unlisted, url)
		}
	}
	var warned []string
	for _, warning := range []struct {
		lead  string
		found []string
	}{
		{"this names a bug whose fix PR waits on his tap, and its card beside an approving pick would tie that pick to no one PR, so send the fix in its own reply with it in prs: ", untying},
		{"this names a bug whose fix PRs are not on the card, and a PR joins a card only when you list it in prs with words that say what it does: ", unlisted},
		{"this may name a bug these PRs fix; if it does, edit the reply with them in prs: ", unsure},
		{"the judge did not read, so a bug this names in words may lack its fix PR's tap card: ", unjudged},
	} {
		if len(warning.found) > 0 {
			warned = append(warned, warning.lead+strings.Join(warning.found, ", "))
		}
	}
	return r, strings.Join(warned, "; ")
}

func (f Fanout) withApprovedPR(r Reply) (Reply, string) {
	if len(r.PRs) > 0 || !slices.ContainsFunc(r.Picks, bus.ReadsAsLand) {
		return r, ""
	}
	said, numbers := r.namedPRs()
	if len(numbers) == 0 {
		return r, ""
	}
	repo, rows, err := board.Rows(f.Entry, numbers)
	if err != nil {
		return r, fmt.Sprintf("the PR ledger did not read, so the PR an approving pick lands was not drawn: %v", err)
	}
	var named []int
	for n, found := range said {
		if inRepo(repo, found) && !slices.Contains(named, numbers[n]) {
			named = append(named, numbers[n])
		}
	}
	if len(named) == 1 && slices.ContainsFunc(rows, func(row store.ReviewRow) bool { return row.Number == named[0] }) {
		r.PRs = []string{fmt.Sprintf("https://github.com/%s/pull/%d", repo, named[0])}
	}
	return r, ""
}

// untiedApprovals marks each pick that reads as approving a PR when the card
// shows no one PR at one head, since only that head turns his pick into a tap.
func (r Reply) untiedApprovals() (Reply, string) {
	if len(r.PRCards) == 1 && r.PRCards[0].Head != "" {
		return r, ""
	}
	var untied []string
	for _, pick := range r.Picks {
		if !bus.ReadsAsLand(pick) {
			continue
		}
		untied = append(untied, fmt.Sprintf("%q", pick))
		r.Words[pick] = strings.TrimSpace(r.Words[pick] + " " + untiedPick)
	}
	if len(untied) == 0 {
		return r, ""
	}
	return r, fmt.Sprintf("APPROVES NOTHING: pick %s reads as approving a PR, but this card does not show one PR at one head, so picking it writes no tap and a lane must carry it by hand; list only the one PR it approves in prs, with a ledger row, or relabel the pick if it lands nothing", strings.Join(untied, ", "))
}

func linkedCard(md string, card board.OpenCard) string {
	typed := "(card:" + card.ID + ")"
	if strings.Contains(md, card.URL) || strings.Contains(md, typed) {
		return strings.ReplaceAll(md, "("+card.URL+")", typed)
	}
	linked := cardLink.FindAllStringIndex(md, -1)
	id := regexp.QuoteMeta(card.ID)
	if bare := strings.TrimPrefix(card.ID, cardPrefix); bare != card.ID && strings.ContainsAny(bare, "0123456789") {
		id = "(?:" + id + "|" + regexp.QuoteMeta(bare) + ")"
	}
	words := strings.ReplaceAll(regexp.QuoteMeta(strings.TrimPrefix(card.ID, cardPrefix)), "-", `[ -]`)
	for _, name := range []struct{ pattern, label string }{
		{"`?" + `\b` + id + `\b` + "`?", card.Title},
		{`(?i)\bcard ` + words + `\b|\b` + words + ` card\b`, ""},
		{`(?i)` + regexp.QuoteMeta(card.Title), ""},
	} {
		for _, found := range regexp.MustCompile(name.pattern).FindAllStringIndex(md, -1) {
			if !slices.ContainsFunc(linked, func(link []int) bool { return found[0] < link[1] && link[0] < found[1] }) {
				return md[:found[0]] + "[" + cmp.Or(name.label, md[found[0]:found[1]]) + "]" + typed + md[found[1]:]
			}
		}
	}
	return md
}

// inSlack draws each card:ID link as the Slack message of the card, or as its
// words alone when the reply links no open card by that id.
func (r Reply) inSlack(md string) string {
	return cardLink.ReplaceAllStringFunc(md, func(link string) string {
		found := cardLink.FindStringSubmatch(link)
		at := slices.IndexFunc(r.OpenCards, func(card board.OpenCard) bool { return card.ID == found[2] })
		if at < 0 {
			return found[1]
		}
		return "[" + cmp.Or(found[1], r.OpenCards[at].Title) + "](" + r.OpenCards[at].URL + ")"
	})
}

func (f Fanout) aliased(r Reply) Reply {
	s, err := store.Open(f.Entry.Store())
	if err != nil {
		return r
	}
	defer func() { _ = s.Close() }()
	names, err := s.Names()
	if err != nil {
		return r
	}
	aliases := lanes.AliasesOf(names)
	r.Title, r.MD, r.Action = aliases.In(r.Title), aliases.In(r.MD), aliases.In(r.Action)
	picks := aliases.AllIn(r.Picks)
	if len(slices.Compact(slices.Sorted(slices.Values(picks)))) == len(picks) {
		words := make(map[string]string, len(r.Words))
		for n, pick := range r.Picks {
			if said := r.Words[pick]; said != "" {
				words[picks[n]] = aliases.In(said)
			}
		}
		r.Picks, r.Words, r.Recommended = picks, words, aliases.In(r.Recommended)
	}
	r.PRCards = slices.Clone(r.PRCards)
	for n, card := range r.PRCards {
		r.PRCards[n].Lines = Lines{aliases.In(card.Change), aliases.In(card.Plan), aliases.In(card.Risk), aliases.In(card.Blocks)}
	}
	r.Blocks = slices.Clone(r.Blocks)
	for n, block := range r.Blocks {
		r.Blocks[n] = json.RawMessage(aliases.In(string(block)))
	}
	r.Artifacts = slices.Clone(r.Artifacts)
	for n, artifact := range r.Artifacts {
		r.Artifacts[n].Title = aliases.In(artifact.Title)
	}
	return r
}

func (f Fanout) underAlert(r Reply) (Reply, string) {
	if r.Alert == "" {
		if r.Status == incident || r.Type == incident {
			return r, "this incident line names no alert, so it posted on its own and no later line threads under it; pass alert, the incident id its lanes are named for, such as sentry-7830378865 for sentry-7830378865-fix, on every line of that incident"
		}
		return r, ""
	}
	s, err := store.Open(f.Entry.Store())
	if err != nil {
		return r, "the store did not open, so this did not look up its alert's first post and posted where it would without one: " + err.Error()
	}
	defer func() { _ = s.Close() }()
	first, found, err := s.AlertThreadOf(r.Alert)
	if err != nil {
		return r, "the alert's first post did not read, so this posted where it would without one: " + err.Error()
	}
	if found && first.Reply != r.ID {
		r.Thread, r.On = first.TS, cmp.Or(r.On, "reply:"+first.Reply)
	}
	return r, ""
}

func (f Fanout) placed(r Reply) (Reply, error) {
	if r.Thread == owner.AtRoot {
		r.Thread = ""
		return r, nil
	}
	if r.Thread != "" {
		if r.Order != "" || r.On != "" || r.Unprompted {
			return r, nil
		}
		order, err := owner.RowIn(f.Entry.StateDir, r.Thread)
		r.Order = order
		return r, err
	}
	line, _ := strings.CutPrefix(r.On, linePrefix)
	row := cmp.Or(r.Order, line)
	if row == "" || r.Order == "" && !strings.HasPrefix(r.On, linePrefix) {
		return r, nil
	}
	place, err := owner.PlaceOf(f.Entry.StateDir, row)
	if err != nil {
		return r, err
	}
	switch place {
	case owner.AtRoot:
		return r, nil
	case "":
		was, err := f.delivered(r.ID)
		if err != nil {
			return r, err
		}
		if was.ownMessage(r).Ref != "" || surface(was.Surfaces, "slack").Ref != "" {
			return r, nil
		}
		return r, fmt.Errorf(`%s has no recorded place, so this posted nothing, since he would not see it where he asked; pass thread with the ts of the Slack DM thread he asked in, or thread "root" if he asked anywhere else`, row)
	}
	r.Thread = place
	return r, nil
}

func (r Reply) typed() Reply {
	switch {
	case r.Type != "":
	case len(r.Picks) > 0:
		r.Type = typeQuestion
	case r.Status == needsYou:
		r.Type = typeForYou
	case r.Status == progress:
		r.Type = typeUpdate
	case r.Status == incident, r.Status == done:
		r.Type = r.Status
	default:
		r.Type = typeExplanation
	}
	return r
}

func (r Reply) diagramNoted() Reply {
	if r.diagram.URL != "" {
		r.MD += "\n\n[" + diagramLead + r.diagram.Title + "](" + r.diagram.URL + ")"
	}
	return r
}

func (r Reply) quiet() bool {
	return r.Order != "" && len(r.Picks) == 0 && r.Status == progress
}

func (r Reply) writesRow() bool {
	return r.Order != "" && len(r.Picks) == 0 && (r.Status == progress || r.Status == info)
}

func (r Reply) led() Reply {
	if r.Order == "" || strings.TrimSpace(r.Title) == "" || strings.TrimSpace(r.MD) == "" {
		return r
	}
	lead := strings.TrimRight(strings.TrimSpace(r.Action), ".")
	if lead != "" || len(r.Picks) > 0 {
		r.Status = cmp.Or(r.Status, needsYou)
	}
	if lead == "" {
		return r
	}
	if !strings.HasPrefix(r.Title, lead) {
		r.Title = shortened(lead+": "+r.Title, maxTitle)
	}
	if md := strings.TrimSpace(r.MD); !strings.HasPrefix(md, lead) {
		r.MD = lead + ".\n\n" + md
	}
	return r
}

func (r Reply) mended() (Reply, []string) {
	var warned []string
	warn := func(format string, args ...any) { warned = append(warned, fmt.Sprintf(format, args...)) }
	if r.Order != "" && !orderRow.MatchString(r.Order) {
		warn("order %q is not a ledger row such as m347, so it marks nothing", r.Order)
		r.Order = ""
	}
	r.Alert = laneRole.ReplaceAllString(strings.TrimSpace(r.Alert), "")
	if r.Action != "" && r.Order == "" {
		warn("action names what he must do about an order, and this reply names no order, so it was left off")
		r.Action = ""
	}
	r.Action = strings.Join(strings.Fields(r.Action), " ")
	if (r.By != "" || r.Routed != 0) && !r.writesRow() {
		warn("by and routed mark his ledger row only from a progress or info reply to an order with no picks, so they were left off")
		r.By, r.Routed = "", 0
	}
	if !replyID.MatchString(r.ID) {
		if r.ID != "" {
			warn("id %q is not one an earlier reply returned, so this is a new reply", r.ID)
		}
		r.ID = ""
	}
	r.MD = cmp.Or(strings.TrimSpace(r.MD), strings.TrimSpace(r.Title))
	r.Title = shortened(cmp.Or(strings.Join(strings.Fields(r.Title), " "), firstLine(r.MD)), maxTitle)
	if r.Status != "" && !slices.Contains(statuses, r.Status) {
		warn("status %q is not one of %s, so it was left off", r.Status, strings.Join(statuses, ", "))
		r.Status = ""
	}
	if r.Type != "" && !slices.Contains(types, r.Type) {
		warn("type %q is not one of %s, so it was left off", r.Type, strings.Join(types, ", "))
		r.Type = ""
	}
	r, warned = r.mendedPicks(warned)
	r.Links = kept(r.Links, maxLinks, "links", &warned, func(link Link) bool {
		return link.Label != "" && (strings.HasPrefix(link.URL, "https://") || strings.HasPrefix(link.URL, "http://"))
	})
	r.PRs = kept(r.PRs, maxPRs, "PRs", &warned, pullURL.MatchString)
	room := maxArtifacts
	if len(r.Diagram) > 0 {
		room--
	}
	r.Artifacts = kept(r.Artifacts, room, "artifacts", &warned, Artifact.wellFormed)
	r.Blocks = kept(r.Blocks, maxBlocks, "blocks", &warned, func(raw json.RawMessage) bool {
		var block struct {
			Type string `json:"type"`
		}
		return json.Unmarshal(raw, &block) == nil && block.Type != ""
	})
	if notebook.MatchString(mdLink.ReplaceAllString(r.MD, "$1")) {
		warn("a notebook link in md shows no title, and Slack draws none, so write it as [its title](url)")
	}
	return r, warned
}

func (r Reply) mendedPicks(warned []string) (Reply, []string) {
	var picks []string
	words := map[string]string{}
	for _, pick := range r.Picks {
		whole, said, _ := strings.Cut(pick, pickSplit)
		whole = strings.TrimSpace(whole)
		label := shortened(whole, maxPick)
		if label == "" || slices.Contains(picks, label) {
			continue
		}
		picks = append(picks, label)
		if label != whole {
			said = whole + " " + said
		}
		if said = strings.TrimSpace(said); said != "" {
			words[label] = said
		}
	}
	if len(picks) > maxPicks {
		warned = append(warned, fmt.Sprintf("a card offers at most %d picks, so the rest were left off", maxPicks))
		picks = picks[:maxPicks]
	}
	if len(picks) == 1 {
		warned = append(warned, fmt.Sprintf("one pick is no choice, so %q was left off", picks[0]))
		picks = nil
	}
	for _, pick := range picks {
		if words[pick] == "" {
			warned = append(warned, fmt.Sprintf("pick %q does not say what picking it does; write it as 'Keep it%swhat picking it does'", pick, pickSplit))
		}
	}
	r.Picks, r.Words = picks, words
	recommended, _, _ := strings.Cut(r.Recommended, pickSplit)
	r.Recommended = shortened(strings.TrimSpace(recommended), maxPick)
	if len(picks) > 0 && !slices.Contains(picks, r.Recommended) {
		warned = append(warned, "no pick is recommended")
		r.Recommended = ""
	}
	if len(picks) == 0 {
		r.Recommended = ""
	}
	if r.Build != nil && r.Recommended == "" {
		warned = append(warned, "build names what the recommended pick is built on, and nothing is recommended, so it was left off")
		r.Build = nil
	}
	if r.Build != nil {
		built, warning := r.Build.mended()
		if warning != "" {
			warned = append(warned, warning)
		}
		r.Build = &built
	}
	return r, warned
}

func (b Build) mended() (Build, string) {
	var why string
	switch {
	case b.None != "":
		none := Build{None: strings.Join(strings.Fields(b.None), " ")}
		if b.Repo != "" || b.Branch != "" {
			return none, "build names a branch and the reason nothing builds, so the card shows the reason alone"
		}
		return none, ""
	case !repoName.MatchString(b.Repo):
		why = fmt.Sprintf("repo %q is not OWNER/NAME, so no branch is linked", b.Repo)
	case notBranch.MatchString(b.Branch):
		why = fmt.Sprintf("branch %q is not a branch name, so no branch is linked", strings.Join(strings.Fields(b.Branch), " "))
	default:
		return b, ""
	}
	return Build{None: why}, "build " + why + "; the card says so under the recommended pick"
}

func kept[T any](items []T, limit int, name string, warned *[]string, valid func(T) bool) []T {
	good := slices.DeleteFunc(slices.Clone(items), func(item T) bool { return !valid(item) })
	if dropped := len(items) - len(good); dropped > 0 {
		*warned = append(*warned, fmt.Sprintf("%d of the %s are malformed, so they were left off", dropped, name))
	}
	if len(good) > limit {
		*warned = append(*warned, fmt.Sprintf("a reply carries at most %d %s, so the rest were left off", limit, name))
		good = good[:limit]
	}
	return good
}

func shortened(text string, limit int) string {
	runes := []rune(text)
	if len(runes) <= limit {
		return text
	}
	cut := string(runes[:limit-1])
	if space := strings.LastIndexByte(cut, ' '); space > 0 {
		cut = cut[:space]
	}
	return cut + "…"
}

func firstLine(md string) string {
	line, _, _ := strings.Cut(md, "\n")
	return strings.TrimSpace(line)
}

func (r Reply) unreadAsk() error {
	if len(r.Picks) == 0 {
		return nil
	}
	asked := slices.Concat([]string{r.Title, r.MD}, r.Picks)
	for _, link := range r.Links {
		asked = append(asked, link.URL)
	}
	shown := cardtext.HiddenCite.ReplaceAllString(strings.Join(asked, "\n"), " ")
	return cmp.Or(cardtext.UnshownDoc(shown), cardtext.UncitedCredential(shown))
}

func (r Reply) plain() error {
	options := make([]string, 0, len(r.Picks))
	for _, pick := range r.Picks {
		label, does, _ := strings.Cut(pick, pickSplit)
		options = append(options, strings.TrimSpace(label)+": "+strings.TrimSpace(does))
	}
	return cardtext.PlainCard(r.Title, r.MD, options)
}

func (r Reply) strayMarkup() error {
	if tag := strayTag.FindString(r.Title + "\n" + r.MD); tag != "" {
		return fmt.Errorf("STRAY MARKUP: %q is a tag fragment, and Slack would show it to him raw, so nothing went out; read the title and md once for stray markup and resend without it", strings.TrimSpace(tag))
	}
	return nil
}

func (r Reply) bareExplainMore() error {
	card, err := json.Marshal(map[string]any{"type": "card", "summary": strings.TrimPrefix(strings.TrimSpace(r.MD), strings.TrimRight(r.Action, ".")+"."), "children": r.Blocks})
	if err != nil {
		return err
	}
	if name := cardtext.Bare(card); name != "" {
		return cardtext.Refusal(name)
	}
	return nil
}

// withPRCards reads each PR the reply names from the drive's PR ledger, for
// its card to carry. The reply posts whatever it finds: the second result is
// one warning line naming what is missing, or empty. A PR he cannot approve,
// on a reply with no picks, is only a mention.
func (f Fanout) withPRCards(ctx context.Context, r Reply) (Reply, string, error) {
	if len(r.PRs) == 0 {
		return r, "", nil
	}
	said := board.NewAnswerWords(f.Runner, f.Entry.Checkout)
	numbers := make([]int, len(r.PRs))
	for n, pr := range r.PRs {
		numbers[n], _ = strconv.Atoi(pullURL.FindStringSubmatch(pr)[2])
	}
	repo, rows, read := board.Rows(f.Entry, numbers)
	var short, unrowed, blank []string
	cards := make([]prCard, 0, len(r.PRs))
	for n, pr := range r.PRs {
		at := slices.IndexFunc(rows, func(row store.ReviewRow) bool {
			return row.Number == numbers[n] && strings.EqualFold(repo, pullURL.FindStringSubmatch(pr)[1])
		})
		if at < 0 {
			if len(r.Picks) > 0 {
				short = append(short, pr+" has no row in the PR ledger")
				cards = append(cards, prCard{URL: pr})
				continue
			}
			unrowed = append(unrowed, pr)
			continue
		}
		row := rows[at]
		if len(r.Picks) == 0 && !board.OffersApproval(row) {
			continue
		}
		if row.ForUsers == "" || row.Risk == "" || board.PlanWords(row) == "" {
			row = f.fromGitHub(ctx, row, pr)
		}
		if row.ForUsers == "" && row.Risk == "" && board.PlanWords(row) == "" {
			blank = append(blank, pr)
		}
		card := prCard{URL: pr, Head: row.Head, Title: row.Title, Review: board.Standing(row), Lines: Lines{prrow.ChangeWords(row), said.Said(ctx, board.PlanWords(row)), prrow.RiskWords(row), prrow.BlocksWords(row)}}
		var missing []string
		for _, field := range []struct{ said, name string }{{row.ForUsers, "what it changes for a user"}, {board.PlanWords(row), "the plan row"}, {row.Risk, "the risk"}} {
			if field.said == "" {
				missing = append(missing, field.name)
			}
		}
		if len(missing) > 0 {
			short = append(short, fmt.Sprintf("%s's ledger row and PR body lack %s", pr, strings.Join(missing, ", ")))
		}
		cards = append(cards, card)
	}
	if len(r.Picks) > 0 && len(blank) > 0 {
		return r, "", fmt.Errorf("EMPTY PR CARD: %s has no Summary, plan line or Risk profile in its ledger row or its PR body, so its card would carry nothing and nothing went out; its lane writes them into the PR body, then this sends again", strings.Join(blank, ", "))
	}
	if len(r.Picks) > 0 {
		r.PRCards = cards
	}
	var warned []string
	if len(unrowed) > 0 {
		warned = append(warned, "no row in the PR ledger, so no Approve and he sees a link: "+strings.Join(unrowed, ", "))
	}
	if len(short) > 0 {
		warned = append(warned, strings.Join(short, "; ")+", so its lane fixes the PR body's Summary, plan reference or Risk profile, and this reply is edited once the row has them")
	}
	if len(warned) > 0 && read != nil {
		warned = append(warned, fmt.Sprintf("the PR ledger did not read: %v", read))
	}
	return r, strings.Join(warned, "; "), nil
}

func (f Fanout) fromGitHub(ctx context.Context, row store.ReviewRow, pr string) store.ReviewRow {
	done, err := f.Runner.Run(ctx, shell.Command{Argv: []string{"gh", "pr", "view", pr, "--json", "title,body"}, Timeout: postTimeout})
	var read struct{ Title, Body string }
	if err != nil || json.Unmarshal([]byte(done.Stdout), &read) != nil {
		return row
	}
	var body store.ReviewRow
	prrow.ApplyBody(&body, read.Body)
	row.Title, row.ForUsers, row.Risk, row.PlanLine = cmp.Or(row.Title, read.Title), cmp.Or(row.ForUsers, body.ForUsers), cmp.Or(row.Risk, body.Risk), cmp.Or(row.PlanLine, body.PlanLine)
	return row
}

func (r Reply) prChild(id, pr string) map[string]any {
	named := pullURL.FindStringSubmatch(pr)
	child := map[string]any{"id": id + "-pr-" + strings.ReplaceAll(named[1], "/", "-") + "-" + named[2], "type": "pr", "repo": named[1], "number": json.Number(named[2])}
	at := slices.IndexFunc(r.PRCards, func(card prCard) bool { return card.URL == pr })
	if at < 0 {
		return child
	}
	card := r.PRCards[at]
	return board.FillPRChild(child, board.SlackPRRow{Head: card.Head, Title: card.Title, Review: card.Review, Change: card.Change, Plan: card.Plan, Risk: card.Risk, Blocks: card.Blocks})
}

func (r Reply) markdown() string {
	lines := []string{strings.TrimSpace(r.MD)}
	if len(r.Picks) > 0 {
		lines = append(lines, "")
	}
	for _, pick := range r.Picks {
		said := r.Words[pick]
		switch {
		case pick != r.Recommended:
		case r.Build == nil:
			pick += " (recommended)"
		case r.Build.None != "":
			pick += " (recommended; nothing to build: " + r.Build.None + ")"
		default:
			pick += " (recommended; built on " + r.Build.Repo + " " + r.Build.Branch + ")"
		}
		if said != "" {
			pick += ": " + said
		}
		lines = append(lines, "- "+pick)
	}
	if len(r.Links)+len(r.PRs)+len(r.Artifacts) > 0 {
		lines = append(lines, "")
	}
	for _, link := range r.Links {
		lines = append(lines, "- ["+link.Label+"]("+link.URL+")")
	}
	for _, artifact := range r.Artifacts {
		lines = append(lines, "- ["+artifact.Title+"]("+artifact.URL+")")
	}
	for _, pr := range r.PRs {
		lines = append(lines, "- "+pr)
	}
	return strings.Join(lines, "\n")
}

func (f Fanout) board(ctx context.Context, r Reply, _ []Outcome) []Outcome {
	if len(r.Picks) == 0 {
		return f.withdraw(ctx, r)
	}
	id := "card-" + r.ID
	options := make([]map[string]any, len(r.Picks))
	for n, pick := range r.Picks {
		options[n] = map[string]any{"id": fmt.Sprintf("%s-%d", r.ID, n+1), "label": pick}
		if said := r.Words[pick]; said != "" {
			options[n]["md"] = said
		}
		if pick == r.Recommended {
			options[n]["recommended"] = true
			if r.Build != nil {
				options[n]["build"] = r.Build
			}
		}
	}
	lede, prose, _ := strings.Cut(strings.TrimSpace(r.MD), "\n\n")
	for _, link := range r.Links {
		prose += "\n- [" + link.Label + "](" + link.URL + ")"
	}
	children := []map[string]any{{"id": r.ID + "-choice", "type": "choice", "options": options}}
	if r.diagram.Image != "" {
		children = slices.Insert(children, 0, map[string]any{"id": r.ID + "-diagram", "type": arch.Block, "doc": r.Diagram, "picture": r.diagram.Image, "link": r.diagram.URL, "title": r.diagram.Title})
	}
	if prose = strings.TrimSpace(prose); prose != "" {
		children = slices.Insert(children, 0, map[string]any{"id": r.ID + "-md", "type": "markdown", "md": prose})
	}
	for _, pr := range r.PRs {
		children = append(children, r.prChild(r.ID, pr))
	}
	card := map[string]any{"id": id, "type": "card", "title": r.Title, "summary": strings.Join(strings.Fields(lede), " "), "children": children}
	if posted := f.postedRef(r.ID); posted != "" {
		card["replaces"] = posted
	}
	block, err := json.Marshal(card)
	if err != nil {
		return []Outcome{{Surface: "board", Error: err.Error()}}
	}
	done, err := f.Runner.Run(ctx, shell.Command{
		Argv:    []string{"cc-present", "update-block", "-", "--session", f.Entry.Root(), "--round", "current"},
		Stdin:   string(block),
		Dir:     f.Entry.Checkout,
		Timeout: postTimeout,
	})
	if err != nil {
		return []Outcome{failed("board", err), {Surface: "slack", Ref: f.postedRef(r.ID), Error: "the board card did not post, so cc-present sent no Slack card"}}
	}
	drawn := Outcome{Surface: "board", Ref: id}
	var warned []string
	if r.Ask != 0 {
		if err := f.Cards.RecordAsk(ctx, id, r.Ask); err != nil {
			warned = append(warned, fmt.Sprintf("the card's row does not record ask %d, so a correction answering it will not flag the card: %s", r.Ask, err))
		}
	}
	if r.Message != nil {
		if err := f.recordSend(ctx, id, r); err != nil {
			warned = append(warned, fmt.Sprintf("the card's row does not record its message, so his tap on %q reaches you as NEEDS-LANE, not SEND; send the message yourself the turn it arrives: %s", r.Message.Pick, err))
		}
	}
	drawn.Warning = strings.Join(warned, "; ")
	posted := slackSent.FindStringSubmatch(done.Stdout)
	if posted == nil {
		return []Outcome{drawn, f.leftOnTheBoard(r.ID, id)}
	}
	return []Outcome{drawn, {Surface: "slack", Ref: posted[1]}}
}

func (f Fanout) leftOnTheBoard(replyID, cardID string) Outcome {
	ref := f.postedRef(replyID)
	if ref == "" {
		return Outcome{Surface: "slack", Error: "cc-present put " + cardID + " on the board and sent no Slack card"}
	}
	return Outcome{Surface: "slack", Ref: ref, Error: "cc-present updated " + cardID + " on the board and left " + ref + " as posted: he already answered that card, or it already says this"}
}

func (f Fanout) withdraw(ctx context.Context, r Reply) []Outcome {
	id := "card-" + r.ID
	was, err := f.delivered(r.ID)
	if err != nil {
		return []Outcome{{Surface: "board", Error: err.Error()}, {Surface: "slack", Error: "the earlier reply did not read, so no Slack card was edited"}}
	}
	drawn := surface(was.Surfaces, "board")
	if drawn.Surface == "" {
		return nil
	}
	copied := Outcome{Surface: "slack", Ref: surface(was.Surfaces, "slack").Ref}
	if drawn.Ref != id+withdrawn {
		if _, err := f.Runner.Run(ctx, shell.Command{
			Argv:    []string{"cc-present", "remove-block", id, "--session", f.Entry.Root(), "--reason", "edited: " + r.Title},
			Dir:     f.Entry.Checkout,
			Timeout: postTimeout,
		}); err != nil && !leftTheBoard(err) {
			copied.Error = id + " is still on the board, so its Slack card keeps its buttons"
			return []Outcome{failed("board", err), copied}
		}
	}
	gone := Outcome{Surface: "board", Ref: id + withdrawn}
	if copied.Ref == "" {
		return []Outcome{gone}
	}
	return []Outcome{gone, f.edit(ctx, r, copied, r.MD)}
}

func leftTheBoard(err error) bool {
	var exit *shell.ExitError
	return errors.As(err, &exit) && strings.Contains(exit.Stderr, alreadyGone)
}

func (f Fanout) thread(ctx context.Context, r Reply, _ []Outcome) []Outcome {
	if !talkboard.Opened(f.Entry) {
		return nil
	}
	id := "reply-" + r.ID
	prose := strings.TrimSpace(r.MD)
	for _, link := range r.Links {
		prose += "\n- [" + link.Label + "](" + link.URL + ")"
	}
	if len(r.Picks) > 0 {
		prose += "\n\nThe question is under Needs me and in Slack."
	}
	children := make([]map[string]any, 0, 1+len(r.Blocks)+len(r.PRs))
	children = append(children, map[string]any{"id": id + "-md", "type": "markdown", "md": prose})
	for n, raw := range r.Blocks {
		var block map[string]any
		if err := json.Unmarshal(raw, &block); err != nil {
			return []Outcome{{Surface: "talk", Error: err.Error()}}
		}
		block["id"] = fmt.Sprintf("%s-b%d", id, n+1)
		if actions, ok := block["actions"].([]any); ok {
			for _, action := range actions {
				if named, ok := action.(map[string]any); ok {
					named["target"] = talkboard.Section
				}
			}
		}
		children = append(children, block)
	}
	for _, pr := range r.PRs {
		children = append(children, r.prChild(id, pr))
	}
	card := map[string]any{"id": id, "type": "card", "title": r.Title, "mirror": false, "children": children}
	if re := f.anchor(r.ID); re != "" {
		card["re"] = re
	}
	block, err := json.Marshal(card)
	if err != nil {
		return []Outcome{{Surface: "talk", Error: err.Error()}}
	}
	if err := talkboard.Fit(ctx, f.Runner, f.Entry, communicator, id, block); err != nil {
		return []Outcome{failed("talk", err)}
	}
	talked := Outcome{Surface: "talk", Ref: id}
	var left []string
	for {
		_, err := f.Runner.Run(ctx, shell.Command{
			Argv:    []string{"cc-present", "update-block", "-", "--session", talkboard.Session(f.Entry), "--round", "current", "--lane", communicator},
			Stdin:   string(block),
			Dir:     f.Entry.Checkout,
			Timeout: postTimeout,
		})
		if err == nil {
			return []Outcome{talked}
		}
		var exit *shell.ExitError
		if !errors.As(err, &exit) {
			return []Outcome{failed("talk", err)}
		}
		var refused []string
		for _, named := range namedBlock.FindAllStringSubmatch(exit.Stderr, -1) {
			if slices.ContainsFunc(children[1:], func(c map[string]any) bool { return c["id"] == named[1] }) {
				refused = append(refused, named[1])
			}
		}
		if len(refused) == 0 {
			return []Outcome{failed("talk", err)}
		}
		left = append(left, refused...)
		talked.Warning = fmt.Sprintf("Talk shows this reply without %s, which cc-present refused: %s", strings.Join(left, ", "), strings.TrimSpace(exit.Stderr))
		children = slices.DeleteFunc(children, func(c map[string]any) bool { return slices.Contains(refused, c["id"].(string)) })
		card["children"] = children
		if block, err = json.Marshal(card); err != nil {
			return []Outcome{{Surface: "talk", Error: err.Error()}}
		}
	}
}

func (f Fanout) anchor(id string) string {
	data, err := os.ReadFile(filepath.Join(f.Entry.StateDir, storeDir, id+".json"))
	if err != nil {
		return talkboard.LastNote(f.Entry)
	}
	var saved struct {
		Re string `json:"re"`
	}
	if err := json.Unmarshal(data, &saved); err != nil {
		return talkboard.LastNote(f.Entry)
	}
	return saved.Re
}

func (f Fanout) slack(ctx context.Context, r Reply, sent []Outcome) []Outcome {
	drawn := surface(sent, "slack")
	was, err := f.delivered(r.ID)
	if err != nil {
		return []Outcome{{Surface: "slack", Error: "the earlier reply did not read, so nothing was posted or edited: " + err.Error()}}
	}
	own := was.ownMessage(r)
	if own.Ref != "" && r.Thread != "" && r.Thread != stampOf(own.Ref) && was.Reply.Thread == "" && r.Order != "" {
		place, err := owner.PlaceOf(f.Entry.StateDir, r.Order)
		if err != nil {
			return []Outcome{{Surface: "slack", Error: "the order's place did not read, so nothing was posted or edited: " + err.Error()}}
		}
		if place == r.Thread {
			return f.movedToThread(ctx, r, own)
		}
	}
	if own.Ref != "" {
		return []Outcome{f.edit(ctx, r, own, r.withPickLink(r.MD, drawn))}
	}
	if surface(was.Surfaces, "slack").Ref != "" || len(r.Picks) > 0 && drawn.Ref == "" {
		return nil
	}
	if r.Thread != "" && drawn.Surface == "" {
		return f.inThread(ctx, r)
	}
	if drawn.Surface != "" || len(r.Links) == 0 && len(r.PRs) == 0 && len(r.Artifacts) == 0 && len(r.OpenCards) == 0 && r.Status == "" {
		return nil
	}
	card, err := f.statusCard(r, r.MD, "")
	if err != nil {
		return []Outcome{{Surface: "slack", Error: err.Error()}}
	}
	return f.dmStatus(ctx, append([]string{"--card", card}, r.tapFlags()...)...)
}

func (f Fanout) movedToThread(ctx context.Context, r Reply, root Outcome) []Outcome {
	moved := f.inThread(ctx, r)
	if moved[0].Error != "" {
		return moved
	}
	gone := f.edit(ctx, Reply{ID: r.ID, Title: r.Title, Status: done}, root, "Moved to [your thread]("+moved[0].Ref+").")
	gone.Surface = movedRoot
	return append(moved, gone)
}

func (d delivery) ownMessage(r Reply) Outcome {
	if beside := surface(d.Surfaces, "slack thread"); beside.Ref != "" {
		return beside
	}
	if surface(d.Surfaces, "board").Surface != "" || len(r.Picks) > 0 {
		return Outcome{}
	}
	return surface(d.Surfaces, "slack")
}

func (f Fanout) edit(ctx context.Context, r Reply, message Outcome, body string) Outcome {
	edited := Outcome{Surface: message.Surface, Ref: message.Ref}
	card, err := f.statusCard(r, body, message.Ref)
	if err != nil {
		edited.Error = editFailed(message.Ref, err.Error())
		return edited
	}
	done := f.dmStatus(ctx, append([]string{"--card", card}, r.tapFlags()...)...)[0]
	if done.Error != "" {
		edited.Error = editFailed(message.Ref, done.Error)
	}
	edited.Warning = done.Warning
	return edited
}

func editFailed(ref, why string) string {
	return "did not edit " + ref + " and posted nothing new: " + why
}

func (r Reply) withPickLink(body string, drawn Outcome) string {
	if drawn.Ref == "" || len(r.Picks) == 0 {
		return body
	}
	return body + "\n<" + drawn.Ref + "|" + pickLink + ">"
}

func (f Fanout) inThread(ctx context.Context, r Reply) []Outcome {
	body := strings.ReplaceAll(slackHead.ReplaceAllString(mdLink.ReplaceAllString(r.slackText(r.MD), "<$2|$1>"), "*$1*"), "**", "*")
	for _, link := range r.Links {
		if !unfurled.MatchString(link.URL) {
			body += "\n<" + link.URL + "|" + link.Label + ">"
		}
	}
	return f.dmStatus(ctx, slices.Concat([]string{"--thread", r.Thread}, r.tapFlags(), r.pictureFlags(), []string{"*" + r.Title + "*\n" + body})...)
}

func (r Reply) tapFlags() []string {
	if len(r.Picks) > 0 {
		return nil
	}
	flags := make([]string, 0, 2*len(r.PRs))
	for _, pr := range r.PRs {
		flags = append(flags, "--tap-pr", pr)
	}
	return flags
}

func (f Fanout) cardCopies(ctx context.Context, r Reply, sent []Outcome) []Outcome {
	drawn := surface(sent, "slack")
	threaded := len(r.Picks) > 0 && r.Thread != "" && drawn.Ref != ""
	was, err := f.delivered(r.ID)
	if err != nil && (threaded || len(r.OpenCards) > 0) {
		return []Outcome{{Surface: cardCopySurface, Error: "the earlier reply did not read, so no card was copied: " + err.Error()}}
	}
	type copied struct{ id, url string }
	var cards []copied
	if threaded && was.Reply.ID == "" {
		cards = append(cards, copied{cardPrefix + r.ID, drawn.Ref})
	}
	for _, card := range r.OpenCards {
		if !strings.Contains(was.Reply.MD, "(card:"+card.ID+")") {
			cards = append(cards, copied{card.ID, card.URL})
		}
	}
	thread := cmp.Or(r.Thread, stampOf(drawn.Ref))
	copies := make([]Outcome, 0, len(cards))
	for _, card := range cards {
		if thread == "" {
			copies = append(copies, Outcome{Surface: cardCopySurface, Card: card.id, Error: "this reply reached no Slack message, so the card has no thread to go in; copy it with `cc-slack card-copy --url " + card.url + " --thread <ts>` once the reply posts"})
			continue
		}
		done, err := f.Runner.Run(ctx, shell.Command{Argv: []string{"cc-slack", "card-copy", "--url", card.url, "--thread", thread}, Timeout: postTimeout})
		if err != nil {
			failure := failed(cardCopySurface, err)
			failure.Card = card.id
			copies = append(copies, failure)
			continue
		}
		var posted struct {
			Permalink string `json:"permalink"`
		}
		if err := json.Unmarshal([]byte(done.Stdout), &posted); err != nil || posted.Permalink == "" {
			copies = append(copies, Outcome{Surface: cardCopySurface, Card: card.id, Error: "cc-slack card-copy named no posted message: " + strings.TrimSpace(done.Stdout)})
			continue
		}
		copies = append(copies, Outcome{Surface: cardCopySurface, Card: card.id, Ref: posted.Permalink})
	}
	return copies
}

func (f Fanout) statusCard(r Reply, body, replaces string) (string, error) {
	body = r.slackText(body)
	buttons := make([]Link, 0, len(r.Links))
	for _, link := range r.Links {
		switch {
		case unfurled.MatchString(link.URL):
		case strings.HasPrefix(link.URL, "https://"):
			buttons = append(buttons, link)
		default:
			body += "\n<" + link.URL + "|" + link.Label + ">"
		}
	}
	key := r.ID
	if replaces != "" {
		key = ""
	}
	card, err := json.Marshal(struct {
		Key       string     `json:"key,omitempty"`
		Status    string     `json:"status"`
		Title     string     `json:"title"`
		Body      string     `json:"body"`
		Drive     string     `json:"drive"`
		Links     []Link     `json:"links,omitempty"`
		Artifacts []Artifact `json:"artifacts,omitempty"`
		Replaces  string     `json:"replaces,omitempty"`
	}{key, cmp.Or(r.Status, "info"), r.Title, body, f.Entry.Bus(), buttons, r.cardArtifacts(), replaces})
	return string(card), err
}

func (f Fanout) dmStatus(ctx context.Context, args ...string) []Outcome {
	timeout := postTimeout
	if slices.Contains(args, "--tap-pr") {
		timeout = tapTimeout
	}
	done, err := f.Runner.Run(ctx, shell.Command{Argv: append([]string{"cc-slack", "dm-status"}, args...), Timeout: timeout})
	if err != nil {
		return []Outcome{failed("slack", err)}
	}
	var posted struct {
		Permalink string `json:"permalink"`
		Warning   string `json:"warning"`
	}
	if err := json.Unmarshal([]byte(done.Stdout), &posted); err != nil || posted.Permalink == "" {
		return []Outcome{{Surface: "slack", Error: "cc-slack dm-status named no posted message: " + strings.TrimSpace(done.Stdout)}}
	}
	return []Outcome{{Surface: "slack", Ref: posted.Permalink, Warning: posted.Warning}}
}

func (f Fanout) record(ctx context.Context, r Reply, _ []Outcome) []Outcome {
	content, err := json.Marshal(r)
	if err != nil {
		return []Outcome{{Surface: "record", Error: err.Error()}}
	}
	sum := sha256.Sum256(content)
	argv := []string{
		"cci", "post", "--drive", f.Entry.Bus(), "--lane", communicator, "--kind", "note", "--topic", "reply:" + r.ID,
		"--to", "owner", "--id", "reply:" + r.ID + ":" + hex.EncodeToString(sum[:6]), "--path", "-", "--json",
	}
	for _, pr := range r.PRs {
		argv = append(argv, "--pr", pr)
	}
	done, err := f.Runner.Run(ctx, shell.Command{Argv: argv, Stdin: r.Title + "\n\n" + r.markdown(), Timeout: postTimeout})
	if err != nil {
		return []Outcome{failed("record", err)}
	}
	var posted struct {
		Num string `json:"num"`
	}
	if err := json.Unmarshal([]byte(done.Stdout), &posted); err != nil || posted.Num == "" {
		return []Outcome{{Surface: "record", Error: "cci post named no record: " + strings.TrimSpace(done.Stdout)}}
	}
	return []Outcome{{Surface: "record", Ref: posted.Num}}
}

func (f Fanout) ledger(ctx context.Context, r Reply, _ []Outcome) []Outcome {
	state, err := owner.Answer(f.Entry.StateDir, r.Order, r.By, r.ID, r.Routed, f.Now())
	if err != nil {
		return []Outcome{failed("ledger", err)}
	}
	marked := Outcome{Surface: "ledger", Ref: r.Order + " " + state}
	if r.By == "" {
		return []Outcome{marked}
	}
	warnings, err := owner.UnreadAnswers(ctx, f.Entry, func(row owner.Row) bool { return row.ID == r.Order })
	if err != nil {
		warnings = []string{"reading answers already waiting failed: " + err.Error()}
	}
	marked.Warning = strings.Join(warnings, "\n")
	return []Outcome{marked}
}

func (f Fanout) dashboard(_ context.Context, r Reply, sent []Outcome) []Outcome {
	path := f.stored(r.ID)
	data, err := json.MarshalIndent(delivery{r, f.Now().UTC(), f.anchor(r.ID), sent}, "", "  ")
	if err == nil {
		err = os.MkdirAll(filepath.Dir(path), 0o750)
	}
	if err == nil {
		err = os.WriteFile(path+".staged", append(data, '\n'), 0o600)
	}
	if err == nil {
		err = os.Rename(path+".staged", path)
	}
	if err != nil {
		return []Outcome{{Surface: "dashboard", Error: err.Error()}}
	}
	return []Outcome{{Surface: "dashboard", Ref: path}}
}

func (f Fanout) delivered(id string) (delivery, error) {
	var was delivery
	data, err := os.ReadFile(f.stored(id))
	if errors.Is(err, fs.ErrNotExist) {
		return was, nil
	}
	if err != nil {
		return was, err
	}
	return was, json.Unmarshal(data, &was)
}

func (f Fanout) postedRef(id string) string {
	was, err := f.delivered(id)
	if err != nil {
		return ""
	}
	return surface(was.Surfaces, "slack").Ref
}

func (f Fanout) stored(id string) string {
	return filepath.Join(f.Entry.StateDir, storeDir, id+".json")
}

func surface(sent []Outcome, name string) Outcome {
	for _, o := range sent {
		if o.Surface == name {
			return o
		}
	}
	return Outcome{}
}

func failed(surface string, err error) Outcome {
	var exit *shell.ExitError
	if errors.As(err, &exit) {
		return Outcome{Surface: surface, Error: fmt.Sprintf("%s %s exited %d: %s", exit.Argv[0], exit.Argv[1], exit.Code, strings.TrimSpace(exit.Stderr))}
	}
	if errors.Is(err, exec.ErrNotFound) && surface != cardCopySurface {
		return Outcome{Surface: surface, Error: err.Error() + resendOnce}
	}
	return Outcome{Surface: surface, Error: err.Error()}
}

func receipt(r Reply, sent []Outcome) string {
	lines := []string{r.markdown(), "", "Reply " + r.ID + "; pass this id to edit it on every surface."}
	for _, o := range sent {
		name := o.Surface
		if o.Card != "" {
			name += " of " + o.Card
		}
		if o.Error != "" {
			lines = append(lines, "- "+name+" FAILED: "+o.Error)
			continue
		}
		lines = append(lines, "- "+name+": "+o.Ref)
		if o.Warning != "" {
			lines = append(lines, "- "+name+" posted with a warning, so never resend it: "+o.Warning)
		}
	}
	return strings.Join(lines, "\n")
}

func stampOf(permalink string) string {
	found := slackStamp.FindStringSubmatch(permalink)
	if found == nil {
		return ""
	}
	return found[1] + "." + found[2]
}
