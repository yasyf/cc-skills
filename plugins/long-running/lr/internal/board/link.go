package board

import (
	"cmp"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/arch"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/cardtext"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/launchd"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	LinkLane        = "board-link"
	cardKind        = "board.card"
	cursorFile      = "board-link.cursor"
	firstRead       = 1
	saidLimit       = 300
	changeAttr      = "change"
	linkAnswerKind  = "answer"
	linkRefusalKind = "defect"
	linkLog         = "board-link.log"
	askField        = "ask"
)

var slackLine = regexp.MustCompile(`(?m)^slack: (\S+)`)

type cardBody struct {
	Session string          `json:"session,omitempty"`
	Block   json.RawMessage `json:"block,omitempty"`
	Remove  string          `json:"remove,omitempty"`
	Reason  string          `json:"reason,omitempty"`
	Until   string          `json:"until,omitempty"`
	By      string          `json:"by,omitempty"`
	Ask     int64           `json:"ask,omitempty"`
	TapOf   string          `json:"tap_of,omitempty"`
	TapHead string          `json:"tap_head,omitempty"`
}

type linkSaid struct {
	Said string `json:"said"`
}

type linkRefusal struct{ said string }

func (r linkRefusal) Error() string { return r.said }

func DrawCard(ctx context.Context, driveName, lane, key string, change cardBody) (client.Record, error) {
	if (len(change.Block) == 0) == (change.Remove == "") {
		return client.Record{}, errors.New("a card change is one block to draw or one block id to remove")
	}
	body, err := json.Marshal(change)
	if err != nil {
		return client.Record{}, err
	}
	return client.Post(ctx, client.Draft{
		Drive: driveName, Lane: lane, Kind: cardKind, ID: cardKind + "/" + key, Text: "card change " + key,
		To: []string{LinkLane}, Body: body, Force: true, Attrs: map[string]string{changeAttr: key},
	})
}

type Link struct {
	Drive string
	State string
	Cards func(session string) (Cards, func(), error)
	Judge Judge
	Draw  func(ctx context.Context, dashboard string, doc json.RawMessage, png string) error
}

func (l Link) Serve(ctx context.Context) error {
	after, err := l.cursor()
	if err != nil {
		return err
	}
	return client.Follow(ctx, client.Watch{Drive: l.Drive, Reader: LinkLane, After: after}, func(record client.Record) error {
		if err := l.apply(ctx, record); err != nil {
			return err
		}
		return os.WriteFile(filepath.Join(l.State, cursorFile), []byte(strconv.FormatInt(record.Seq, 10)), 0o600)
	})
}

func (l Link) cursor() (int64, error) {
	raw, err := os.ReadFile(filepath.Join(l.State, cursorFile))
	if errors.Is(err, os.ErrNotExist) {
		return firstRead, nil
	}
	if err != nil {
		return 0, err
	}
	return strconv.ParseInt(string(raw), 10, 64)
}

func SeedCursor(ctx context.Context, bus, state string) error {
	path := filepath.Join(state, cursorFile)
	if _, err := os.Stat(path); !errors.Is(err, os.ErrNotExist) {
		return err
	}
	past, err := client.Tail(ctx, client.Query{Drive: bus, Kinds: []string{cardKind}})
	if err != nil || len(past) == 0 {
		return err
	}
	return os.WriteFile(path, []byte(strconv.FormatInt(past[len(past)-1].Seq, 10)), 0o600)
}

func (l Link) apply(ctx context.Context, record client.Record) error {
	if string(record.Kind) != cardKind {
		return nil
	}
	var card cardBody
	if err := json.Unmarshal(record.Body, &card); err != nil {
		return l.tell(ctx, record, linkRefusalKind, err.Error())
	}
	cards, done, err := l.Cards(card.Session)
	if err != nil {
		return err
	}
	defer done()
	said, err := l.change(ctx, cards, record.Lane, card)
	var refused linkRefusal
	var failed *shell.ExitError
	switch {
	case errors.As(err, &refused):
		return l.refuse(ctx, cards, record, card, refused.said)
	case errors.As(err, &failed):
		return l.refuse(ctx, cards, record, card, "it did not reach the board: "+strings.TrimSpace(failed.Stderr))
	case err != nil:
		return err
	}
	return l.tell(ctx, record, linkAnswerKind, said)
}

func (l Link) change(ctx context.Context, cards Cards, lane string, card cardBody) (string, error) {
	if card.Remove != "" {
		var out strings.Builder
		if err := cards.Retire(ctx, lane, card.Reason, []string{card.Remove}, &out); err != nil {
			return "", err
		}
		return strings.TrimSpace(out.String()), nil
	}
	return l.draw(ctx, cards, lane, card)
}

func (l Link) draw(ctx context.Context, cards Cards, lane string, card cardBody) (string, error) {
	var top struct {
		ID    string `json:"id"`
		Title string `json:"title"`
	}
	if err := json.Unmarshal(card.Block, &top); err != nil || top.ID == "" {
		return "", linkRefusal{"this block has no id: send a cc-present block with an id"}
	}
	if card.Until != "" {
		if why := Refusal(card.Until, cards.Now()); why != "" {
			return "", linkRefusal{"refused: --until: " + why}
		}
	}
	lane, err := askingLane(ctx, cards, lane, card.Ask, top.ID)
	if err != nil {
		return "", err
	}
	answered, err := cards.answeredBlocks(ctx, top.ID)
	if err != nil {
		return "", err
	}
	if len(answered) > 0 {
		return "", linkRefusal{fmt.Sprintf("refused: he already answered %s; act on his answer, which `cc-present block show %s --session %s` prints, and give a new question a new --id", top.ID, top.ID, cards.Session)}
	}
	if card.Block, err = l.pictured(ctx, card.Block); err != nil {
		return "", err
	}
	var unchecked error
	if card.TapOf == "" {
		var refused error
		if refused, unchecked = cards.Check(ctx, l.Judge, card.Block); refused != nil {
			return "", linkRefusal{refused.Error()}
		}
	}
	_, _, asks := asksTitle(card.Block)
	drawn, err := cards.Runner.Run(ctx, presentCommand(card.Block, cards, lane, asks))
	if err != nil {
		return "", err
	}
	said := top.ID + " is on his board"
	if url := slackLine.FindStringSubmatch(drawn.Stdout); url != nil {
		said += " and in his DM, " + url[1]
	}
	if card.Until != "" {
		if _, err := cards.Until(ctx, top.ID, card.Until, lane); err != nil {
			return "", err
		}
		if card.TapOf == "" {
			said += fmt.Sprintf("; his answer wakes %s", lane)
			if _, err := cards.Depend(ctx, top.ID, lane, nil); err != nil {
				said += fmt.Sprintf("; what it rests on was not read, so a later ruling will not redraw it: %s", err)
			}
		}
	}
	if card.Ask != 0 {
		if err := cards.record(ctx, top.ID, map[string]string{askField: strconv.FormatInt(card.Ask, 10)}); err != nil {
			return "", err
		}
	}
	switch named := cardtext.NamesAClock(card.Block); {
	case card.By != "":
		if err := cards.mirrored(ctx, top.ID, map[string]string{byField: card.By, laneField: lane, questionField: top.Title}); err != nil {
			return "", err
		}
		said += fmt.Sprintf("; %s cannot retire until a record carries delivered=card:%s", lane, top.ID)
	case named != "":
		said += fmt.Sprintf("; WARNING it names the clock %q and no --by, so nothing holds %s to it: redraw it with --by", named, lane)
	}
	if card.TapOf == "" {
		if lacks := cards.Lacks(ctx, l.Judge, card.Block); lacks != "" {
			said += "; WARNING " + lacks
		}
	}
	if unchecked != nil {
		said += "; WARNING it drew unchecked: " + strings.ReplaceAll(unchecked.Error(), "\n", "; ")
	}
	var warned strings.Builder
	warnUnnamedProcess(&warned, card.Block)
	warnUntiedLand(&warned, card.Block)
	return said + strings.TrimRight("; "+warned.String(), "; \n"), nil
}

func askingLane(ctx context.Context, cards Cards, lane string, ask int64, card string) (string, error) {
	if ask == 0 {
		return lane, nil
	}
	asked, err := client.Among(ctx, cards.Bus, []int64{ask})
	if err != nil {
		return "", err
	}
	if len(asked) == 0 {
		return "", linkRefusal{fmt.Sprintf("refused: --for-ask %d names no record on this drive; pass the seq of the lane's ask", ask)}
	}
	_, rows, err := cards.rows(ctx)
	if err != nil {
		return "", err
	}
	seq := strconv.FormatInt(ask, 10)
	for drawn, fields := range rows {
		if fields[askField] == seq && fields["withdrawn"] == "" && drawn != card {
			return "", linkRefusal{fmt.Sprintf("refused: ask %d already has %s on his board, and one ask gets one card; pass --id %s to change that card", ask, drawn, strings.TrimPrefix(drawn, "card-"))}
		}
	}
	return asked[0].Lane, nil
}

func presentCommand(block json.RawMessage, cards Cards, lane string, asks bool) shell.Command {
	verb := "update-block"
	if asks {
		verb = "ask"
	}
	return shell.Command{Argv: []string{"cc-present", verb, "-", "--session", cards.Session, "--lane", lane}, Stdin: string(block), Dir: cards.Checkout}
}

func (l Link) refuse(ctx context.Context, cards Cards, record client.Record, card cardBody, said string) error {
	if card.TapOf == "" {
		return l.tell(ctx, record, linkRefusalKind, said)
	}
	repo, digits, _ := strings.Cut(card.TapOf, "#")
	number, err := strconv.Atoi(digits)
	if err != nil {
		return err
	}
	if _, err := cards.Store.PutFact(repo, number, card.TapHead, FactTapCard, tapCardRefused, cards.Now()); err != nil {
		return err
	}
	text := fmt.Sprintf("The tap card for %s did not draw, so nothing asks him for it; draw it by hand: %s", card.TapOf, said)
	_, err = client.Post(ctx, client.Draft{
		Drive: l.Drive, Lane: LinkLane, Kind: linkRefusalKind, ID: "tap-card-alarm/" + record.Fields.Attrs[changeAttr],
		Text: text[:min(len(text), saidLimit)], To: []string{communicator}, PRs: []string{card.TapOf}, Re: record.Seq, Force: true,
	})
	return err
}

func (l Link) tell(ctx context.Context, record client.Record, kind, said string) error {
	body, err := json.Marshal(linkSaid{said})
	if err != nil {
		return err
	}
	change := record.Fields.Attrs[changeAttr]
	_, err = client.Post(ctx, client.Draft{
		Drive: l.Drive, Lane: LinkLane, Kind: kind, ID: fmt.Sprintf("%s-%s/%s", cardKind, kind, change),
		Text: said[:min(len(said), saidLimit)], To: []string{record.Lane}, Re: record.Seq, Body: body, Force: true, Attrs: map[string]string{changeAttr: change},
	})
	return err
}

func LinkLabel(driveName string) string {
	return "com.long-running." + driveName + ".board-link"
}

func LinkJob(driveName, binary, path, state string) launchd.Job {
	return launchd.Job{
		Label:     LinkLabel(driveName),
		Env:       [][2]string{{"PATH", path}, {"CLAUDE_LONG_RUNNING_LANE", LinkLane}},
		Arguments: []string{binary, "board", "--drive", driveName, "link", "serve"},
		Directory: state,
		Log:       filepath.Join(state, linkLog),
	}
}

func linkCommand(a *app.App, opened func() (drive.Entry, string, error), judge Judge) *cobra.Command {
	cmd := &cobra.Command{Use: "link", Short: "On the board's Mac, check and apply each card change a lane posts with lr ask-owner"}
	serve := &cobra.Command{
		Use:   "serve",
		Short: "Apply each card change addressed to " + LinkLane + " in order, resuming from its cursor, and answer its lane on the bus",
		Args:  cobra.NoArgs,
		RunE: func(c *cobra.Command, _ []string) error {
			entry, _, err := opened()
			if err != nil {
				return err
			}
			return Link{Drive: entry.Bus(), State: entry.StateDir, Judge: judge, Draw: arch.Draw, Cards: func(named string) (Cards, func(), error) {
				current, session, err := opened()
				if err != nil {
					return Cards{}, nil, err
				}
				s, err := store.Open(current.Store())
				if err != nil {
					return Cards{}, nil, err
				}
				return CardsOf(a, current, cmp.Or(named, session), s), func() { _ = s.Close() }, nil
			}}.Serve(c.Context())
		},
	}
	install := &cobra.Command{
		Use:   "install",
		Short: "Keep the link running under launchd on the pinned lr, starting after the card changes already on the bus",
		Args:  cobra.NoArgs,
		RunE: func(c *cobra.Command, _ []string) error {
			entry, _, err := opened()
			if err != nil {
				return err
			}
			if err := SeedCursor(c.Context(), entry.Bus(), entry.StateDir); err != nil {
				return err
			}
			agents, err := launchd.New(a.Runner, c.OutOrStdout())
			if err != nil {
				return err
			}
			home, err := os.UserHomeDir()
			if err != nil {
				return err
			}
			if err := agents.Install(c.Context(), LinkJob(entry.Drive, filepath.Join(home, ".claude", "long-running", "current", "bin", "lr"), os.Getenv("PATH"), entry.StateDir)); err != nil {
				return err
			}
			return agents.Restart(c.Context(), LinkLabel(entry.Drive))
		},
	}
	cmd.AddCommand(serve, install)
	return cmd
}
