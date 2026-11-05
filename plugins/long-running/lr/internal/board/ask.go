package board

import (
	"cmp"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/cardtext"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
)

const (
	partSplit     = "::"
	slugWords     = 6
	fewestOptions = 2
	mostOptions   = 4
	noPlanFrom    = "New, not in the plan."
	keyBytes      = 8
	linkWait      = 3 * time.Minute
	remoteImage   = "https://"
)

var (
	numberedChange  = regexp.MustCompile(`(^|[^\w&])(#(\d+))\b`)
	prNamed         = regexp.MustCompile(`github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)|([\w.-]+/[\w.-]+)#(\d+)|\bPR (\d+)\b`)
	kept            = regexp.MustCompile(cardtext.HiddenCite.String() + "|" + cardtext.Link.String())
	slugWord        = regexp.MustCompile(`[a-z0-9]+`)
	errLinkAnswered = errors.New("the board link answered")
)

type Ask struct {
	ID, Title, Background, From, Branch, NothingToBuild, Diagram string
	Options, Shots                                               []string
	PortOf                                                       int
}

type askOption struct {
	ID          string         `json:"id"`
	Label       string         `json:"label"`
	Md          string         `json:"md"`
	Recommended bool           `json:"recommended,omitempty"`
	Build       map[string]any `json:"build,omitempty"`
}

func (a Ask) Block(repo string, existing func([]SlackPR) ([]SlackPR, error)) (json.RawMessage, error) {
	if !strings.HasSuffix(strings.TrimSpace(a.Title), "?") {
		return nil, fmt.Errorf("refused: --title %q is not a question; end it with what he decides and a question mark", a.Title)
	}
	if count := len(a.Options); count < fewestOptions || count > mostOptions {
		return nil, fmt.Errorf("refused: a card offers %d to %d options and this has %d; pass each as --option 'Keep it :: what picking it does', the recommended one first", fewestOptions, mostOptions, count)
	}
	build, err := a.build()
	if err != nil {
		return nil, err
	}
	slug := cmp.Or(a.ID, strings.Join(firstWords(a.Title), "-"))
	options := make([]askOption, len(a.Options))
	for i, option := range a.Options {
		label, does, _ := strings.Cut(option, partSplit)
		label, does = strings.TrimSpace(label), strings.TrimSpace(does)
		if label == "" || does == "" {
			return nil, fmt.Errorf("refused: --option %q does not say what picking it does; write it as 'Keep it :: what picking it does'", option)
		}
		options[i] = askOption{ID: fmt.Sprintf("%s-%d", slug, i+1), Label: label, Md: linkedNumbers(does, repo)}
	}
	options[0].Recommended, options[0].Build = true, build
	background := linkedNumbers(strings.TrimSpace(a.Background), repo)
	summary, more, _ := strings.Cut(background, "\n")
	said := []string{linkedNumbers(a.Title, repo), background}
	for _, option := range a.Options {
		said = append(said, linkedNumbers(option, repo))
	}
	prs, err := existing(slices.DeleteFunc(namedPRs(said, repo), func(pr SlackPR) bool { return pr == SlackPR{repo, a.PortOf} }))
	if err != nil {
		return nil, err
	}
	title, from := a.Title, cmp.Or(a.From, noPlanFrom)
	if a.PortOf != 0 {
		opening := fmt.Sprintf("port of #%d", a.PortOf)
		title, from = opening+": "+a.Title, cmp.Or(a.From, linkedNumbers(opening, repo))
	}
	children := []map[string]any{{"id": slug + "-from", "type": "markdown", "md": "**From:** " + from}}
	if more = strings.TrimSpace(more); more != "" {
		children = slices.Insert(children, 0, map[string]any{"id": slug + "-background", "type": "markdown", "md": more})
	}
	if a.Diagram != "" {
		diagram, err := diagramChild(slug, a.Diagram)
		if err != nil {
			return nil, err
		}
		children = append(children, diagram)
	}
	shots, err := a.shots(slug)
	if err != nil {
		return nil, err
	}
	children = append(children, shots...)
	children = append(children, map[string]any{"id": slug + "-choice", "type": "choice", "options": options})
	for _, pr := range prs {
		children = append(children, map[string]any{"id": fmt.Sprintf("%s-pr-%s-%d", slug, strings.ReplaceAll(pr.Repo, "/", "-"), pr.Number), "type": "pr", "repo": pr.Repo, "number": pr.Number})
	}
	return json.Marshal(map[string]any{"id": "card-" + slug, "type": "card", "title": title, "summary": strings.TrimSpace(summary), "children": children})
}

func (a Ask) build() (map[string]any, error) {
	switch {
	case (a.Branch == "") == (a.NothingToBuild == ""):
		return nil, errors.New("refused: pass --branch OWNER/NAME:BRANCH, where the recommended option is built so he can try it, or --nothing-to-build REASON, why a pick such as a process choice has nothing to build")
	case a.NothingToBuild != "":
		return map[string]any{"none": a.NothingToBuild}, nil
	}
	repo, branch, _ := strings.Cut(a.Branch, ":")
	if strings.Count(repo, "/") != 1 || branch == "" {
		return nil, fmt.Errorf("refused: --branch %q is not OWNER/NAME:BRANCH", a.Branch)
	}
	return map[string]any{"repo": repo, "branch": branch}, nil
}

func (a Ask) shots(slug string) ([]map[string]any, error) {
	shots := make([]map[string]any, len(a.Shots))
	for i, shot := range a.Shots {
		src, shows, _ := strings.Cut(shot, partSplit)
		src, shows = strings.TrimSpace(src), strings.TrimSpace(shows)
		if src == "" || shows == "" {
			return nil, fmt.Errorf("refused: --shot %q does not say what it shows; write it as 'PATH or https URL :: what it shows'", shot)
		}
		if !strings.HasPrefix(src, remoteImage) {
			absolute, err := filepath.Abs(src)
			if err != nil {
				return nil, err
			}
			src = absolute
		}
		shots[i] = map[string]any{"id": fmt.Sprintf("%s-shot-%d", slug, i+1), "type": "image", "src": src, "alt": shows}
	}
	return shots, nil
}

func firstWords(title string) []string {
	words := slugWord.FindAllString(strings.ToLower(title), -1)
	return words[:min(len(words), slugWords)]
}

func linkedNumbers(text, repo string) string {
	var linked strings.Builder
	at := 0
	for _, span := range append(kept.FindAllStringIndex(text, -1), []int{len(text), len(text)}) {
		linked.WriteString(numberedChange.ReplaceAllString(text[at:span[0]], "${1}[${2}](https://github.com/"+repo+"/pull/${3})"))
		linked.WriteString(text[span[0]:span[1]])
		at = span[1]
	}
	return linked.String()
}

func namedPRs(said []string, repo string) []SlackPR {
	var prs []SlackPR
	for _, text := range said {
		for _, hit := range prNamed.FindAllStringSubmatch(cardtext.HiddenCite.ReplaceAllString(text, ""), -1) {
			number, err := strconv.Atoi(cmp.Or(hit[2], hit[4], hit[5]))
			if err != nil {
				continue
			}
			if pr := (SlackPR{cmp.Or(hit[1], hit[3], repo), number}); !slices.Contains(prs, pr) {
				prs = append(prs, pr)
			}
		}
	}
	return prs
}

func realPRs(ctx context.Context, read ccx.Client, warn io.Writer) func([]SlackPR) ([]SlackPR, error) {
	return func(named []SlackPR) ([]SlackPR, error) {
		numbers := map[string][]int{}
		for _, pr := range named {
			numbers[pr.Repo] = append(numbers[pr.Repo], pr.Number)
		}
		unread := map[SlackPR]string{}
		for repo, wanted := range numbers {
			rows, err := read.PRStatus(ctx, repo, wanted)
			if err != nil {
				return nil, err
			}
			for _, row := range rows {
				if row.State == "" {
					unread[SlackPR{repo, row.Number}] = row.Unread
				}
			}
		}
		var prs []SlackPR
		for _, pr := range named {
			if why, missing := unread[pr]; missing {
				fmt.Fprintf(warn, "WARNING %s, so the card carries no PR block for #%d\n", why, pr.Number)
				continue
			}
			prs = append(prs, pr)
		}
		return prs, nil
	}
}

func lacksBackground(card json.RawMessage) bool {
	var block struct {
		Summary  string `json:"summary"`
		Children []struct {
			Type string `json:"type"`
			Md   string `json:"md"`
		} `json:"children"`
	}
	if _, _, asks := asksTitle(card); !asks || json.Unmarshal(card, &block) != nil {
		return false
	}
	told := block.Summary
	for _, child := range block.Children {
		if child.Type == "diagram" || strings.HasPrefix(child.Type, "display.") {
			return false
		}
		if child.Type == "markdown" && !strings.HasPrefix(strings.TrimSpace(child.Md), "**From:**") {
			told += child.Md
		}
	}
	return strings.TrimSpace(told) == ""
}

func changeKey(parts ...[]byte) string {
	sum := sha256.New()
	for _, part := range parts {
		sum.Write(part)
		sum.Write([]byte{0})
	}
	return hex.EncodeToString(sum.Sum(nil))[:keyBytes*2]
}

func awaitLink(ctx context.Context, bus, lane, change string, after int64) (string, error) {
	wait, stop := context.WithTimeout(ctx, linkWait)
	defer stop()
	var said string
	var answered bool
	var refused error
	err := client.Follow(wait, client.Watch{Drive: bus, Reader: lane, After: after, Kinds: []string{linkAnswerKind, linkRefusalKind}}, func(record client.Record) error {
		if record.Lane != LinkLane || record.Fields.Attrs[changeAttr] != change {
			return nil
		}
		var told linkSaid
		if err := json.Unmarshal(record.Body, &told); err != nil {
			return err
		}
		said, answered = told.Said, true
		if string(record.Kind) == linkRefusalKind {
			refused = errors.New(said)
		}
		return errLinkAnswered
	})
	if err != nil && !errors.Is(err, errLinkAnswered) {
		return "", err
	}
	if !answered {
		return "", fmt.Errorf("no board link answered in %s: this stays owed and goes up when the link runs, and `lr board link install` on the board's Mac starts it", linkWait)
	}
	return said, refused
}

func AskCommand(a *app.App) *cobra.Command {
	var ask Ask
	var driveName, session, lane, until, by, file, withdraw, reason string
	var forAsk, handed int64
	var replaces []string
	cmd := &cobra.Command{
		Use:   "ask-owner",
		Short: "Put your question on the owner's main board and in his Slack DM with you as its owner, and wake you when he answers",
		Long: `Put one question card on the owner's main board and at the root of his Slack DM, owned by the calling lane, from any host. His answer wakes that lane with a decide record, and a lane on Orca also through the relay.

The card goes over the bus to the board link on the Mac, which checks it and answers on the bus. This command waits for that answer, prints it, and exits 1 on a refusal, which says what to change. Refused: no background; a link where the background should be; a question a card on his board already asks; a question he answered on this card or in his ccn answers; a request to grade a reading of his words; a failure offered only ways to live with it and no option fixing its cause; a loss with no plan beside it.

  lr ask-owner --title 'How do AIG runs recover?' \
    --background 'Nine runs were refused a model call today and stopped; the account has room for twice its limit.' \
    --option 'Wait durably :: A refused call waits and resumes, so no run stops on it. Built on the branch.' \
    --option 'Raise the limit :: Fewer refusals at the same cost, and a run still stops on the next one.' \
    --branch Forge-AI/monorepo:durable-wait --until pr:4521

--by TIME records the clock a card promises him something by, such as a report by 6:40 PM, on the card's row with its lane. The lane cannot retire until a record carries delivered=card:<id>, and when the clock passes first, the lane and owner-relay hear once with what he waits on. A card naming a clock in its words without --by draws with a warning.

--diagram FILE puts one architecture doc (arch/v1) on the card, drawn live on his board and dashboard and as its picture on the card in Slack. --port-of N opens a card about a forward port with "port of #N" and carries that PR. --card FILE|- takes a whole cc-present card as JSON in place of these flags, for one with diagrams or records, whose title opens with "port of #N" itself; --diagram beside it adds its doc to that card above its choice. --withdraw ID takes your own card down when its question has gone away. --take-over SEQ redraws a card another lane owns, when record SEQ, posted by another lane, is addressed to yours and names the card's id: your lane owns the card from then on, his answer wakes it, and the board records whom it was taken from.`,
		Args: cobra.NoArgs,
		RunE: func(c *cobra.Command, _ []string) error {
			if lane == "" {
				return errors.New("refused: no lane; pass --lane with your lane's name, since his answer wakes it")
			}
			entry, err := drive.Named(driveName)
			if err != nil {
				return err
			}
			block, remove := json.RawMessage(nil), withdraw
			if withdraw == "" {
				if block, err = askedCard(c.InOrStdin(), ask, file, entry.Repo, realPRs(c.Context(), a.CCX, c.ErrOrStderr())); err != nil {
					return err
				}
				if why := Refusal(until, a.Now()); why != "" {
					return fmt.Errorf("refused: --until: %s; name the event after which his answer no longer matters", why)
				}
				if by, err = promiseClock(by, a.Now()); err != nil {
					return err
				}
			}
			key := changeKey(block, []byte(remove), []byte(until), []byte(by), []byte(session), []byte(strconv.FormatInt(forAsk, 10)), []byte(strconv.FormatInt(handed, 10)), []byte(strings.Join(replaces, ",")))
			record, err := DrawCard(c.Context(), entry.Bus(), lane, key, cardBody{Session: session, Block: block, Remove: remove, Reason: reason, Until: until, By: by, Ask: forAsk, Handed: handed, Replaces: replaces})
			if err != nil {
				return err
			}
			said, err := awaitLink(c.Context(), entry.Bus(), lane, key, record.Seq-1)
			if err != nil {
				return err
			}
			fmt.Fprintln(c.OutOrStdout(), said)
			return nil
		},
	}
	flags := cmd.Flags()
	flags.StringVar(&ask.Title, "title", "", "the question, in plain words, ending in a question mark")
	flags.StringVar(&ask.Background, "background", "", "what he needs to decide it: what happened, what it costs, what each way forward rests on, in as many paragraphs as that takes; the first line is the summary his board lists, and the rest shows under it on the card")
	flags.StringArrayVar(&ask.Options, "option", nil, "'Keep it :: what picking it does', 2 to 4, the recommended one first; a failure offers the fix for its cause, and a loss names its fix")
	flags.StringVar(&ask.Branch, "branch", "", "OWNER/NAME:BRANCH, where the recommended option is built")
	flags.StringVar(&ask.NothingToBuild, "nothing-to-build", "", "why the recommended option has no branch")
	flags.StringVar(&ask.Diagram, "diagram", "", "FILE, one architecture doc (arch/v1), drawn live on his board and dashboard and as its picture on the card in Slack")
	flags.StringArrayVar(&ask.Shots, "shot", nil, "'PATH or https URL :: what it shows', a real screenshot, repeatable; a path is read on the board's Mac")
	flags.StringVar(&ask.From, "from", "", "the plan step and date, his ruling id, or whose PR it ports; default "+noPlanFrom)
	flags.IntVar(&ask.PortOf, "port-of", 0, "the old-tree PR this card's port copies: the card's title opens with port of #N")
	flags.StringVar(&ask.ID, "id", "", "the card's id after card-; default the title's first words")
	flags.StringVar(&until, "until", KindAnswered, "the event after which his answer no longer matters: pr:N, release:STACK@ENV:COMMIT, record:SEQ, or at:TIME or a span such as at:3d for a real expiry; the sweep never takes an unanswered question down on a time alone, so a question with no such event keeps the default, answered")
	flags.StringVar(&by, "by", "", "the clock the card promises him something by, an ISO 8601 time or a span such as 90m: the card's lane cannot retire until a record carries delivered=card:<id>, and when the clock passes first, the lane and owner-relay hear once")
	flags.StringVar(&file, "card", "", "a whole cc-present card as JSON, FILE or -, in place of the question flags")
	flags.StringArrayVar(&replaces, "replaces", nil, "ID of your card this one replaces, repeatable: once this card is drawn, that card leaves the board and its Slack card loses its buttons and says a new version replaced it, linking this card")
	flags.StringVar(&withdraw, "withdraw", "", "take this card of yours down instead of drawing one")
	flags.StringVar(&reason, "reason", "its question has gone away", "with --withdraw, why")
	flags.StringVar(&lane, "lane", os.Getenv("CLAUDE_LONG_RUNNING_LANE"), "the lane that owns the card and is woken by his answer")
	flags.StringVar(&driveName, "drive", os.Getenv("LR_DRIVE"), "the drive's bus name")
	flags.Int64Var(&forAsk, "for-ask", 0, "the seq of the lane's ask you are drawing this for: that lane owns the card, and the ask gets one card")
	flags.Int64Var(&handed, "take-over", 0, "SEQ, the record another lane addressed to yours that hands you this card: redraw it though another lane owns it, and own it from then on")
	flags.StringVar(&session, "session", "", "the board's session, for a scratch board; default the drive's coordinator")
	return cmd
}

func askedCard(stdin io.Reader, ask Ask, file, repo string, existing func([]SlackPR) ([]SlackPR, error)) (json.RawMessage, error) {
	var block json.RawMessage
	var err error
	switch file {
	case "":
		block, err = ask.Block(repo, existing)
	case "-":
		block, err = io.ReadAll(stdin)
	default:
		block, err = os.ReadFile(file)
	}
	if err != nil {
		return nil, err
	}
	if file != "" && ask.Diagram != "" {
		if block, err = withDiagram(block, ask.Diagram); err != nil {
			return nil, err
		}
	}
	if lacksBackground(block) {
		return nil, errors.New("refused: this card carries no background, and he decides from the card alone; pass --background with what happened, what it costs and what each way forward rests on, in plain words")
	}
	return block, nil
}

func diagramChild(slug, path string) (map[string]any, error) {
	doc, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	return map[string]any{"id": slug + "-diagram", "type": diagramType, "doc": json.RawMessage(doc)}, nil
}

func withDiagram(block json.RawMessage, path string) (json.RawMessage, error) {
	var card map[string]json.RawMessage
	if err := json.Unmarshal(block, &card); err != nil {
		return nil, err
	}
	var children []json.RawMessage
	if raw, found := card["children"]; found {
		if err := json.Unmarshal(raw, &children); err != nil {
			return nil, err
		}
	}
	var id string
	_ = json.Unmarshal(card["id"], &id)
	child, err := diagramChild(strings.TrimPrefix(id, "card-"), path)
	if err != nil {
		return nil, err
	}
	diagram, err := json.Marshal(child)
	if err != nil {
		return nil, err
	}
	at := slices.IndexFunc(children, func(raw json.RawMessage) bool {
		var kind struct {
			Type string `json:"type"`
		}
		return json.Unmarshal(raw, &kind) == nil && kind.Type == "choice"
	})
	if at < 0 {
		at = len(children)
	}
	if card["children"], err = json.Marshal(slices.Insert(children, at, json.RawMessage(diagram))); err != nil {
		return nil, err
	}
	return json.Marshal(card)
}

func promiseClock(by string, now time.Time) (string, error) {
	if by == "" {
		return "", nil
	}
	if why := Refusal(KindAt+":"+by, now, KindAt); why != "" {
		return "", fmt.Errorf("refused: --by: %s; give the clock the card promises him something by", why)
	}
	end, err := ParseEnd(KindAt+":"+by, now)
	return end.Value, err
}
