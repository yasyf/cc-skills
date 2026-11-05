package explain

import (
	"context"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-inbox/client"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/board"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/orca"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	launched   = "LAUNCHED"
	launchWait = 10 * time.Minute
	designer   = "opus"
	effort     = "high"
)

var pullRequest = regexp.MustCompile(`^[\w.-]+/[\w.-]+#(\d+)$`)

type Retirer interface {
	Retire(ctx context.Context, lane, reason string, cards []string, out io.Writer) error
}

type Opener interface {
	Registered(lane string) (bool, error)
	Launch(ctx context.Context, order orca.Order) (string, error)
}

type Registry struct {
	Store  *store.Store
	Poster orca.Poster
}

func (r Registry) Registered(lane string) (bool, error) {
	_, found, err := r.Store.Lane(lane)
	return found, err
}

func (r Registry) Launch(ctx context.Context, order orca.Order) (string, error) {
	return r.Poster.Launch(ctx, order, launchWait)
}

type Redesign struct {
	Board    Retirer
	Checkout Checkout
	Host     Host
	Owner    Announcer
	Lanes    Opener
	Drive    string
	Lane     string
	StateDir string
	Out      io.Writer
}

func (r Redesign) Run(ctx context.Context, subsystem string, cards, prs []string, source []byte, now time.Time) error {
	slug := Slug(subsystem)
	if slug == "" {
		return fmt.Errorf("subsystem %q has no letter or digit to name its design lane", subsystem)
	}
	if claims := Uncited(source); len(claims) > 0 {
		return refuse(claims)
	}
	if err := refuseDiagrams(source); err != nil {
		return err
	}
	source = Visible(source)
	designLane := slug + "-design-1"
	if err := r.Board.Retire(ctx, r.Lane, "the owner sent "+subsystem+" back for a redesign from scratch", cards, r.Out); err != nil {
		return err
	}
	held, err := r.hold(ctx, slug, subsystem, designLane, prs)
	if err != nil {
		return err
	}
	link, err := r.host(ctx, subsystem, source, now)
	if err != nil {
		return err
	}
	return r.open(ctx, designLane, subsystem, cards, held, link)
}

func (r Redesign) host(ctx context.Context, subsystem string, source []byte, now time.Time) (string, error) {
	notice, changed, err := publish(ctx, r.Checkout, r.Host, subsystem+" redesign", source, now)
	if err != nil {
		return "", err
	}
	if !changed {
		fmt.Fprintf(r.Out, "UNCHANGED page %s\n", notice.Link)
		return notice.Link, nil
	}
	fmt.Fprintf(r.Out, "HOSTED %s\n", notice.Link)
	return notice.Link, r.Owner.Announce(ctx, notice)
}

func (r Redesign) hold(ctx context.Context, slug, subsystem, designLane string, prs []string) ([]string, error) {
	if len(prs) == 0 {
		return nil, nil
	}
	records, err := client.Tail(ctx, client.Query{Drive: r.Drive, Kinds: []string{"hold"}})
	if err != nil {
		return nil, err
	}
	byID := map[string]client.Record{}
	for _, record := range records {
		byID[record.ID] = record
	}
	held := make([]string, 0, len(prs))
	for _, pr := range prs {
		id := "redesign-hold/" + slug + "/" + pr
		record, found := byID[id]
		switch {
		case found && record.Status == "closed":
			fmt.Fprintf(r.Out, "UNCHANGED %s: its redesign hold %s was lifted\n", pr, record.Name())
			continue
		case found:
			fmt.Fprintf(r.Out, "UNCHANGED %s: held by %s\n", pr, record.Name())
		default:
			number := pullRequest.FindStringSubmatch(pr)[1]
			record, err = client.Post(ctx, client.Draft{Drive: r.Drive, Lane: r.Lane, Kind: "hold", ID: id, PRs: []string{pr}, Text: fmt.Sprintf("hold #%s: the owner sent %s back for a redesign from scratch and %s designs it; it stays until a lift or the coordinator's go names it", number, subsystem, designLane)})
			if err != nil {
				return nil, err
			}
			fmt.Fprintf(r.Out, "HELD %s by %s; lift it with cci post --kind lift --re %d\n", pr, record.Name(), record.Seq)
		}
		held = append(held, fmt.Sprintf("- %s, held by %s; lift it with `cci post --kind lift --re %d`", pr, record.Name(), record.Seq))
	}
	return held, nil
}

func (r Redesign) open(ctx context.Context, designLane, subsystem string, cards, held []string, link string) error {
	registered, err := r.Lanes.Registered(designLane)
	if err != nil {
		return err
	}
	if registered {
		fmt.Fprintf(r.Out, "UNCHANGED %s: the lane registry already has it\n", designLane)
		return nil
	}
	path := filepath.Join(r.StateDir, designLane, "brief.md")
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		return err
	}
	if err := os.WriteFile(path, []byte(designBrief(designLane, subsystem, cards, held, link)), 0o600); err != nil {
		return err
	}
	verdict, err := r.Lanes.Launch(ctx, orca.Order{Lane: designLane, Model: designer, Effort: effort, Brief: path})
	if err != nil {
		return err
	}
	if verdict != launched {
		return fmt.Errorf("the orca runner did not launch %s from %s: verdict %q", designLane, path, verdict)
	}
	fmt.Fprintf(r.Out, "OPENED %s from %s\n", designLane, path)
	return nil
}

func designBrief(designLane, subsystem string, cards, held []string, link string) string {
	withdrawn := "- none"
	if len(cards) > 0 {
		withdrawn = "- " + strings.Join(cards, "\n- ")
	}
	holds := "- none"
	if len(held) > 0 {
		holds = strings.Join(held, "\n")
	}
	return fmt.Sprintf(`# %[1]s: design %[2]s again from scratch

Register slice: lane=%[1]s step=all
Scale: the N %[2]s serves, which the design names

The owner said %[2]s is wrong at its root. The page on what he corrected: %[3]s

lr explain redesign withdrew these cards from his board:
%[4]s

It holds these PRs, and none lands until a lift or the coordinator's go names it. Nothing in this lane closes a PR or deletes a branch:
%[5]s

1. Design %[2]s from scratch. Read the held PRs as evidence, never as the starting point.
2. Write the new design as a one-pager and host it with lr explain, which serves it on the tailnet and sends its link to the owner as a Slack card and to the communicator, whose reply carries it to his window. Each sentence saying why something happened cites the record, PR, build, file:line, monitor, notebook or ccn id that proves it, in it or the sentence before, or opens with Unproven:; lr explain refuses the page otherwise.
3. Put each question only the owner can settle on his board as a card with an end.
4. Build each recommended option as a pushed branch, never a PR, until he approves the design.
5. Report each step to the coordinator.
`, designLane, subsystem, link, withdrawn, holds)
}

func redesignCommand(a *app.App) *cobra.Command {
	var from string
	var cards, prs []string
	cmd := &cobra.Command{
		Use:   "redesign <subsystem>",
		Short: "Withdraw a subsystem's board cards, hold its PRs, host the page on the owner's root correction and open its design lane; a rerun changes nothing",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			for _, pr := range prs {
				if !pullRequest.MatchString(pr) {
					return fmt.Errorf("--pr %q is not owner/repo#N", pr)
				}
			}
			cfg, err := a.Config()
			if err != nil {
				return err
			}
			source, err := read(cmd.Context(), a.Runner, from)
			if err != nil {
				return err
			}
			entry, err := drive.Named(cfg.Drive)
			if err != nil {
				return err
			}
			checkout, host, owner, err := publisher(cmd, a, cfg)
			if err != nil {
				return err
			}
			registry, err := store.Open(entry.Store())
			if err != nil {
				return err
			}
			defer func() { _ = registry.Close() }()
			lane := os.Getenv(lanes.LaneEnv)
			if lane == "" {
				return fmt.Errorf("%s names no lane; run lr explain redesign from the communicator's session", lanes.LaneEnv)
			}
			poster := orca.Poster{Bus: orca.CCI{}, Runner: a.Runner, Env: os.Getenv, Now: a.Now, Sleep: time.Sleep, Out: cmd.OutOrStdout(), Drive: entry.Bus(), Author: lane}
			return Redesign{
				Board: board.CardsOf(a, entry, entry.Root(), registry), Checkout: checkout, Host: host, Owner: owner,
				Lanes: Registry{Store: registry, Poster: poster}, Drive: entry.Bus(), Lane: lane, StateDir: entry.StateDir, Out: cmd.OutOrStdout(),
			}.Run(cmd.Context(), args[0], cards, prs, source, a.Now())
		},
	}
	cmd.Flags().StringVar(&from, "from", "", "the markdown file, or cci record such as #359589, saying what the owner corrected at the root")
	cmd.Flags().StringArrayVar(&cards, "card", nil, "an open question card on the subsystem, as the communicator names it, repeatable")
	cmd.Flags().StringArrayVar(&prs, "pr", nil, "an open PR on the subsystem as owner/repo#N, as the communicator names it, repeatable")
	_ = cmd.MarkFlagRequired("from")
	return cmd
}
