package grading

import (
	"cmp"
	"context"
	"errors"
	"fmt"
	"os"
	"slices"
	"strconv"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/grading/grader"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const kindFirst = "lr.first"

func firstDraft(bus, writer string, number int) client.Draft {
	return client.Draft{Drive: bus, Lane: writer, Kind: kindFirst, Text: fmt.Sprintf("first: #%d", number)}
}

func (p *JobPool) Firsts(snapshot map[int]machine.Facts) []machine.Facts {
	asked := make([]machine.Facts, 0, len(p.firsts))
	for number := range p.firsts {
		if facts, open := snapshot[number]; open && !facts.Closed && !facts.Merged && !facts.Undriven() {
			asked = append(asked, facts)
		}
	}
	slices.SortFunc(asked, func(a, b machine.Facts) int { return cmp.Compare(p.firsts[a.Number], p.firsts[b.Number]) })
	return asked
}

func (p *JobPool) again(ctx context.Context, grading string, d desk, facts machine.Facts) (string, error) {
	asked := p.regradeKey(grading, facts)
	graded, err := p.gradedSince(ctx, grading, facts, asked)
	if err != nil || graded {
		return "", cmp.Or(err, ErrAnswered)
	}
	if grading == GraderFidelity {
		return p.Regrade(ctx, facts, nil)
	}
	ask := wholeAsk{PR: strconv.Itoa(facts.Number), Lane: facts.Lane, Key: strconv.FormatInt(asked, 10), Head: facts.Head}
	if voidOf(grading, facts) > 0 {
		ask.Head = ""
	}
	if err := p.queueWhole(ask); err != nil {
		return "", err
	}
	name, err := p.startWhole(ctx, d, ask, map[int]machine.Facts{facts.Number: facts})
	if err == nil && name == "" {
		return "", ErrAnswered
	}
	return name, err
}

func (p *JobPool) passBase(grading string, facts machine.Facts) string {
	pr := strconv.Itoa(facts.Number)
	switch {
	case slices.Contains(Missing(facts), grading) && voidOf(grading, facts) == 0:
		return pr + "-" + short(facts.Head)
	case grading == GraderFidelity:
		return pr + "-" + short(facts.Head) + regradeMark
	}
	return pr + wholeMark + strconv.FormatInt(p.regradeKey(grading, facts), 10)
}

func (p *JobPool) regradeKey(grading string, facts machine.Facts) int64 {
	return max(p.firsts[facts.Number], voidOf(grading, facts))
}

func voidOf(grading string, facts machine.Facts) int64 {
	if grading == GraderFinder {
		return facts.FinderVoid
	}
	return facts.FidelityVoid
}

func (p *JobPool) gradedSince(ctx context.Context, grading string, facts machine.Facts, asked int64) (bool, error) {
	pr := strconv.Itoa(facts.Number)
	found, err := client.Tail(ctx, client.Query{Drive: p.drive, Kinds: []string{kindReview}, PR: facts.Repo + "#" + pr, After: asked})
	if err != nil {
		return false, err
	}
	if grading == GraderFinder {
		return slices.ContainsFunc(gradedReviews(finderReviews(found, facts.Repo, pr)), func(r review) bool { return grader.NamesHead(r.head, facts.Head) }), nil
	}
	grades := fidelityGrades(found, withSuccessors(bus.FidelityLanes(), found), facts.Repo, pr)
	return anyNaming(grades, facts.Head) || anyNaming(readerSends(found), facts.Head), nil
}

func firstAsker(ctx context.Context, runner shell.Runner, named string) (string, error) {
	if named != "" {
		return named, nil
	}
	bound, err := lanes.BoundLane(ctx, runner)
	if err != nil || bound != "" {
		return bound, err
	}
	return "", errors.New("this session is bound to no lane, so the record could not say who moved the PR; pass --lane <your lane>")
}

func firstCommand(a *app.App) *cobra.Command {
	var driveName, asker string
	hours := int(rootFirstHorizon.Hours())
	cmd := &cobra.Command{
		Use:   "first N",
		Short: fmt.Sprintf("Grade PR N's head before any other work for %d hours, held or not: the pool starts each grade it lacks, and once regrades a finder or fidelity grade there that does not clear, with the slots it reserves for a first PR", hours),
		Args:  cobra.ExactArgs(1),
		RunE: func(c *cobra.Command, args []string) error {
			number, err := strconv.Atoi(args[0])
			if err != nil {
				return fmt.Errorf("%q is not a PR number", args[0])
			}
			entry, err := drive.Named(driveName)
			if err != nil {
				return err
			}
			writer, err := firstAsker(c.Context(), a.Runner, asker)
			if err != nil {
				return err
			}
			if _, err := client.Post(c.Context(), firstDraft(entry.Bus(), writer, number)); err != nil {
				return err
			}
			fmt.Fprintf(c.OutOrStdout(), "#%d goes first for %d hours: the next pool pass grades its head, or regrades a grade there that does not clear\n", number, hours)
			return nil
		},
	}
	cmd.Flags().StringVar(&driveName, "drive", os.Getenv("LR_DRIVE"), "the drive's bus name")
	cmd.Flags().StringVar(&asker, "lane", "", "the lane asking, which the record names; default: the lane this session is bound to, or root for the drive's root")
	return cmd
}
