package board

import (
	"context"
	"fmt"
	"io"
	"os"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-inbox/client"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	metaDesk       = "meta-desk"
	wakeKind       = "decide"
	askKind        = "ask"
	claimKind      = "claim"
	lateKind       = "report"
	wakeTopic      = "card-wake:"
	lateTopic      = "card-wake-late:"
	needsLaneTopic = "needs-lane:"
	laneField      = "lane"
	orcaPlacement  = "orca"
	ackAfter       = 10 * time.Minute
	wakeWindow     = 3 * time.Hour
	askWindow      = 14 * 24 * time.Hour
)

var DeskLane = regexp.MustCompile(`^(?:board-refresher|board-watch|card-mirror|owner-relay|desk-runner|supervisor|fidelity|finder|grader|judge|go-sweep|progress-desk|meta-desk|root$)`)

type CardAnswer struct {
	Card, Block, Said, Key string
	OwnerSeq               int64
	Unclaimed              bool
}

type Waker struct {
	Cards Cards
	Lane  string
	Tail  func(ctx context.Context, q client.Query) ([]client.Record, error)
	Post  func(ctx context.Context, d client.Draft) (client.Record, error)
	Relay func(ctx context.Context, lane, text string) error
	Warn  io.Writer
}

func (w Waker) Wake(ctx context.Context, a CardAnswer) error {
	asker, err := w.asker(ctx, a)
	if err != nil {
		return err
	}
	if asker == "" {
		if !a.Unclaimed {
			return nil
		}
		routed, err := w.routed(ctx, a)
		if err != nil || routed {
			return err
		}
		return w.needsLane(ctx, a, "", "No lane asked for this card, so launch one whose brief carries this answer")
	}
	lane, known, err := w.Cards.Store.Lane(asker)
	if err != nil {
		return err
	}
	if known && ended(lane) {
		heir, found, err := w.Cards.Store.Lane(lane.RetiredBy)
		if err != nil {
			return err
		}
		if !found || ended(heir) {
			return w.needsLane(ctx, a, asker, fmt.Sprintf("%s asked for it and has stopped, so launch its successor with this answer in its brief; the stopped lane's brief is %s", asker, lane.Brief))
		}
		asker, lane = heir.Name, heir
	}
	text := fmt.Sprintf("WAKE %s: the owner answered your card: %s. Act on it now; any record of yours naming %s acknowledges it.", a.Card, a.Said[:min(len(a.Said), saidLimit)], a.Card)
	if _, err := w.Post(ctx, client.Draft{
		Drive: w.Cards.Bus, Lane: w.Lane, Kind: wakeKind, ID: wakeTopic + a.Key, Topic: wakeTopic + a.Card, To: []string{asker}, Re: a.OwnerSeq, Text: text, Force: true,
		Attrs: map[string]string{"session": w.Cards.Session, "card": a.Card, "block": a.Block, laneField: asker},
	}); err != nil {
		return err
	}
	if lane.Placement != orcaPlacement {
		return nil
	}
	if err := w.Relay(ctx, asker, strings.Join(strings.Fields(text), " ")); err != nil {
		fmt.Fprintf(w.Warn, "WARNING the wake record for %s stands but its relay to %s did not post: %s\n", a.Card, asker, err)
	}
	return nil
}

func ended(lane store.Lane) bool {
	return lane.State == store.LaneRetired || lane.StoppedOn > 0
}

func (w Waker) asker(ctx context.Context, a CardAnswer) (string, error) {
	_, rows, err := w.Cards.rows(ctx)
	if err != nil {
		return "", err
	}
	if lane := rows[a.Card][laneField]; lane != "" && !DeskLane.MatchString(lane) {
		return lane, nil
	}
	asks, err := w.Tail(ctx, client.Query{Drive: w.Cards.Bus, Kinds: []string{askKind}, Since: w.Cards.Now().Add(-askWindow)})
	if err != nil {
		return "", err
	}
	naming := namesAny(a.Card, a.Block)
	for _, ask := range slices.Backward(asks) {
		if !DeskLane.MatchString(ask.Lane) && naming.MatchString(ask.Text) {
			return ask.Lane, nil
		}
	}
	return "", nil
}

func (w Waker) routed(ctx context.Context, a CardAnswer) (bool, error) {
	records, err := w.Tail(ctx, client.Query{Drive: w.Cards.Bus, Kinds: []string{wakeKind, claimKind, ownerKind}, After: a.OwnerSeq - 1, Since: w.Cards.Now().Add(-wakeWindow)})
	if err != nil {
		return false, err
	}
	naming := namesAny(a.Card, a.Block)
	return slices.ContainsFunc(records, func(record client.Record) bool {
		if record.Seq == a.OwnerSeq {
			_, ordered := bus.OrderOf(record, "")
			return ordered
		}
		return string(record.Kind) != ownerKind && record.Lane != w.Lane && naming.MatchString(record.Text)
	}), nil
}

func namesAny(names ...string) *regexp.Regexp {
	quoted := make([]string, 0, len(names))
	for _, name := range names {
		if name != "" {
			quoted = append(quoted, regexp.QuoteMeta(name))
		}
	}
	return regexp.MustCompile(`(?i)(?:^|[^\w-])#?(?:` + strings.Join(quoted, "|") + `)(?:[^\w-]|$)`)
}

func (w Waker) needsLane(ctx context.Context, a CardAnswer, lane, what string) error {
	_, err := w.Post(ctx, client.Draft{
		Drive: w.Cards.Bus, Lane: w.Lane, Kind: askKind, ID: needsLaneTopic + a.Key, Topic: needsLaneTopic + a.Card, To: []string{metaDesk}, Re: a.OwnerSeq, Force: true,
		Text:  fmt.Sprintf("NEEDS-LANE %s on board %s: the owner answered: %s. %s.", a.Card, w.Cards.Session, a.Said, what),
		Attrs: map[string]string{"session": w.Cards.Session, "card": a.Card, "block": a.Block, laneField: lane},
	})
	return err
}

func (w Waker) Late(ctx context.Context) error {
	now := w.Cards.Now()
	own, err := w.Tail(ctx, client.Query{Drive: w.Cards.Bus, Lanes: []string{w.Lane}, Kinds: []string{wakeKind, lateKind}, Since: now.Add(-wakeWindow)})
	if err != nil {
		return err
	}
	raised := map[string]bool{}
	for _, record := range own {
		raised[record.Topic] = true
	}
	var due []client.Record
	for _, record := range own {
		if strings.HasPrefix(record.Topic, wakeTopic) && record.Fields.Attrs["session"] == w.Cards.Session && now.Sub(record.At) >= ackAfter && !raised[lateTopic+strconv.FormatInt(record.Seq, 10)] {
			due = append(due, record)
		}
	}
	if len(due) == 0 {
		return nil
	}
	woken := make([]string, len(due))
	for i, record := range due {
		woken[i] = record.Fields.Attrs[laneField]
	}
	heard, err := w.Tail(ctx, client.Query{Drive: w.Cards.Bus, Lanes: woken, After: due[0].Seq})
	if err != nil {
		return err
	}
	for _, wake := range due {
		if acknowledged(wake, heard) {
			continue
		}
		attrs, topic := wake.Fields.Attrs, lateTopic+strconv.FormatInt(wake.Seq, 10)
		if _, err := w.Post(ctx, client.Draft{
			Drive: w.Cards.Bus, Lane: w.Lane, Kind: lateKind, ID: topic, Topic: topic, To: []string{metaDesk, communicator}, Re: wake.Seq, Force: true,
			Text: fmt.Sprintf("%s has not acknowledged the owner's answer on %s ten minutes after wake #%d. Nothing is blocked.", attrs[laneField], attrs["card"], wake.Seq),
		}); err != nil {
			return err
		}
	}
	return nil
}

func acknowledged(wake client.Record, heard []client.Record) bool {
	attrs := wake.Fields.Attrs
	names := []string{attrs["card"], attrs["block"], strconv.FormatInt(wake.Seq, 10)}
	if wake.Re != 0 {
		names = append(names, strconv.FormatInt(wake.Re, 10))
	}
	naming := namesAny(names...)
	return slices.ContainsFunc(heard, func(record client.Record) bool {
		return record.Lane == attrs[laneField] && record.Seq > wake.Seq &&
			(record.Re == wake.Seq || (wake.Re != 0 && record.Re == wake.Re) || record.Topic == attrs["card"] || naming.MatchString(record.Text))
	})
}

func wakeCommand(a *app.App, cards func() (Cards, func(), error)) *cobra.Command {
	var answer CardAnswer
	var lane string
	waker := func(c *cobra.Command, board Cards) Waker {
		relay := func(ctx context.Context, to, text string) error {
			lr, err := os.Executable()
			if err != nil {
				return err
			}
			_, err = a.Runner.Run(ctx, shell.Command{Argv: []string{lr, "relay", to, text, "--drive", board.Bus, "--lane", lane}})
			return err
		}
		return Waker{Cards: board, Lane: lane, Tail: client.Tail, Post: client.Post, Relay: relay, Warn: c.ErrOrStderr()}
	}
	cmd := &cobra.Command{
		Use:   "wake CARD",
		Short: "Wake the lane whose ask drew a card with the owner's answer, or ask meta-desk for a lane when it has stopped",
		Args:  cobra.ExactArgs(1),
		RunE: func(c *cobra.Command, args []string) error {
			if answer.Key == "" {
				return fmt.Errorf("--key is required")
			}
			board, done, err := cards()
			if err != nil {
				return err
			}
			defer done()
			answer.Card = args[0]
			return waker(c, board).Wake(c.Context(), answer)
		},
	}
	cmd.PersistentFlags().StringVar(&lane, "lane", "", "the lane the records post as")
	cmd.Flags().StringVar(&answer.Block, "block", "", "the block he answered")
	cmd.Flags().StringVar(&answer.Said, "said", "", "what he picked or wrote")
	cmd.Flags().StringVar(&answer.Key, "key", "", "what makes a repeat of this wake harmless, the owner record's key")
	cmd.Flags().Int64Var(&answer.OwnerSeq, "owner-seq", 0, "the owner record carrying his answer")
	cmd.Flags().BoolVar(&answer.Unclaimed, "unclaimed", false, "ask meta-desk for a lane when no lane asked for the card")
	if err := cmd.MarkPersistentFlagRequired("lane"); err != nil {
		panic(err)
	}
	cmd.AddCommand(&cobra.Command{
		Use:   "late",
		Short: "Tell meta-desk and the communicator, once, of each wake on this board its lane has not acknowledged in ten minutes",
		Args:  cobra.NoArgs,
		RunE: func(c *cobra.Command, _ []string) error {
			board, done, err := cards()
			if err != nil {
				return err
			}
			defer done()
			return waker(c, board).Late(c.Context())
		},
	})
	return cmd
}
