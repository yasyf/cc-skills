package landing

import (
	"cmp"
	"context"
	"errors"
	"fmt"
	"log/slog"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	BumpedAtFact    = "mergeable-bumped-at"
	BumpReadFact    = "mergeable-bump-read"
	BumpMoveFact    = "mergeable-moved"
	BumpRefusedFact = "mergeable-bump-refused"

	bumpEvery       = 10 * time.Minute
	bumpsBeforeMove = 3
	alreadyOnBase   = "already-on-base"
	recordText      = 400
	stackRefusal    = "part of a stack"
)

type Unstick struct {
	Runner shell.Runner
	Read   func(ctx context.Context, numbers []int) (map[int]string, error)
	Move   func(ctx context.Context, number int) (string, error)
	Store  *store.Store
	Drive  string
	Trunk  string
	Now    func() time.Time
	Log    *slog.Logger
}

func (u Unstick) Pass(ctx context.Context, snapshot []machine.Facts) error {
	now := u.Now()
	byBranch := make(map[string]machine.Facts, len(snapshot))
	for _, f := range snapshot {
		if !f.Closed {
			byBranch[f.Branch] = f
		}
	}
	var failed []error
	var patched []machine.Facts
	moves := 0
	for _, f := range snapshot {
		if !f.Uncomputed || f.Closed {
			continue
		}
		facts, err := u.Store.Facts(f.Repo, f.Number, f.Head)
		if err != nil {
			failed = append(failed, err)
			continue
		}
		due, err := bumpDue(facts, now)
		if err != nil {
			failed = append(failed, fmt.Errorf("#%d: %w", f.Number, err))
			continue
		}
		if !due || facts[BumpMoveFact] != "" {
			continue
		}
		bumps, err := strconv.Atoi(cmp.Or(facts[prrow.FactBumps], "0"))
		if err != nil {
			failed = append(failed, fmt.Errorf("#%d: %w", f.Number, err))
			continue
		}
		if chain, movable := movableChain(f, byBranch, u.Trunk); bumps >= bumpsBeforeMove && movable {
			if moves < restacksPerPass {
				moves++
				failed = append(failed, u.move(ctx, f, chain, byBranch, now))
			}
			continue
		}
		if bumps >= bumpsBeforeMove && facts[BumpRefusedFact] != "" {
			continue
		}
		accepted, err := u.bump(ctx, f, bumps, now)
		failed = append(failed, err)
		if accepted {
			patched = append(patched, f)
		}
	}
	return errors.Join(append(failed, u.readBack(ctx, patched, now))...)
}

func bumpDue(facts map[string]string, now time.Time) (bool, error) {
	if facts[prrow.FactUnknownSince] == "" {
		return false, nil
	}
	for _, kind := range []string{prrow.FactUnknownSince, BumpedAtFact} {
		if facts[kind] == "" {
			continue
		}
		at, err := time.Parse(time.RFC3339, facts[kind])
		if err != nil {
			return false, fmt.Errorf("%s: %w", kind, err)
		}
		if now.Sub(at) < bumpEvery {
			return false, nil
		}
	}
	return true, nil
}

func movableChain(f machine.Facts, byBranch map[string]machine.Facts, trunk string) ([]machine.Facts, bool) {
	var chain []machine.Facts
	for range len(byBranch) + 1 {
		if !mayMove(f) {
			return nil, false
		}
		chain = append(chain, f)
		if f.Base == trunk {
			return chain, true
		}
		below, open := byBranch[f.Base]
		if !open {
			return nil, false
		}
		f = below
	}
	return nil, false
}

func mayMove(f machine.Facts) bool {
	return !f.Undriven() && f.Tap == "" && !f.Exempt && f.Hold == "" && f.Queue != machine.QueueQueued
}

func (u Unstick) bump(ctx context.Context, f machine.Facts, bumps int, now time.Time) (bool, error) {
	if _, err := u.Store.PutFact(f.Repo, f.Number, f.Head, BumpedAtFact, now.Format(time.RFC3339), now); err != nil {
		return false, err
	}
	done, err := u.Runner.Run(ctx, shell.Command{Argv: []string{"gh", "api", "-X", "PATCH", fmt.Sprintf("repos/%s/pulls/%d", f.Repo, f.Number), "-f", "base=" + f.Base}, Timeout: readTimeout})
	refused := err != nil && strings.Contains(done.Stdout+done.Stderr, stackRefusal)
	if err != nil && !refused {
		return false, fmt.Errorf("#%d base PATCH: %w", f.Number, err)
	}
	if refused {
		if _, err := u.Store.PutFact(f.Repo, f.Number, f.Head, BumpRefusedFact, "yes", now); err != nil {
			return false, err
		}
	}
	if _, err := u.Store.PutFact(f.Repo, f.Number, f.Head, prrow.FactBumps, strconv.Itoa(bumps+1), now); err != nil {
		return false, err
	}
	return !refused, nil
}

func (u Unstick) readBack(ctx context.Context, patched []machine.Facts, now time.Time) error {
	if len(patched) == 0 {
		return nil
	}
	numbers := make([]int, 0, len(patched))
	for _, f := range patched {
		numbers = append(numbers, f.Number)
	}
	read, err := u.Read(ctx, numbers)
	if err != nil {
		return fmt.Errorf("the read after a base PATCH: %w", err)
	}
	var failed []error
	for _, f := range patched {
		_, err := u.Store.PutFact(f.Repo, f.Number, f.Head, BumpReadFact, read[f.Number], now)
		failed = append(failed, err)
		u.Log.Info("patched a stuck pull request's base", "pr", f.Number, "before", f.Head, "after", read[f.Number])
	}
	return errors.Join(failed...)
}

func (u Unstick) move(ctx context.Context, f machine.Facts, chain []machine.Facts, byBranch map[string]machine.Facts, now time.Time) error {
	if _, err := u.Store.PutFact(f.Repo, f.Number, f.Head, BumpedAtFact, now.Format(time.RFC3339), now); err != nil {
		return err
	}
	stdout, err := u.Move(ctx, f.Number)
	if err != nil && !errors.Is(err, ErrConflict) && !errors.Is(err, ErrCarries) {
		return fmt.Errorf("#%d rebase: %w", f.Number, err)
	}
	outcome, said, touched := moveOutcome(f, byBranch, stdout, err)
	if _, err := u.Store.PutFact(f.Repo, f.Number, f.Head, BumpMoveFact, outcome, now); err != nil {
		return err
	}
	rows := slices.Clone(chain)
	for _, row := range touched {
		if !slices.ContainsFunc(rows, func(seen machine.Facts) bool { return seen.Number == row.Number }) {
			rows = append(rows, row)
		}
	}
	to := make([]string, 0, len(rows))
	prs := make([]string, 0, len(rows))
	unowned := ""
	for _, row := range rows {
		holder, stopped, err := reach(u.Store, u.Drive, row)
		if err != nil {
			return err
		}
		to, prs, unowned = append(to, holder), append(prs, pullRef(row)), cmp.Or(unowned, stopped)
	}
	slices.Sort(to)
	slices.Sort(prs)
	_, err = client.Post(ctx, client.Draft{
		Drive: u.Drive,
		Lane:  self,
		Kind:  "report",
		To:    slices.Compact(to),
		ID:    fmt.Sprintf("%s:%d:%s", BumpMoveFact, f.Number, f.Head),
		PRs:   slices.Compact(prs),
		Text:  clipped(fmt.Sprintf("%s#%d at %s still read mergeable unknown after %d base PATCHes, so the supervisor rebased it on %s: %s", unowned, f.Number, shortHead(f.Head), bumpsBeforeMove, f.Base, said)),
	})
	return err
}

func moveOutcome(f machine.Facts, byBranch map[string]machine.Facts, stdout string, err error) (string, string, []machine.Facts) {
	if errors.Is(err, ErrCarries) {
		return carriesValue, err.Error(), nil
	}
	if err != nil {
		return conflictValue, "that rebase conflicts and pushed nothing, so resolving it is yours: " + firstLine(stdout), nil
	}
	var touched []machine.Facts
	var moved, left, whys []string
	name := func(branch string) string {
		row, open := byBranch[branch]
		if !open {
			return branch
		}
		touched = append(touched, row)
		return "#" + strconv.Itoa(row.Number)
	}
	for line := range strings.Lines(stdout) {
		fields := strings.Fields(line)
		switch {
		case len(fields) == 4 && fields[0] == movedLine:
			moved = append(moved, name(fields[1]))
		case len(fields) > 2 && fields[0] == leftLine:
			left = append(left, name(fields[1]))
			if why := strings.Join(fields[2:], " "); len(whys) == 0 || whys[len(whys)-1] != why {
				whys = append(whys, why)
			}
		}
	}
	if len(moved) == 0 {
		return alreadyOnBase, "it already sits on " + f.Base + ", so nothing moved and it stays stuck", nil
	}
	said := "moved " + strings.Join(moved, ", ")
	if len(left) > 0 {
		said += "; left " + strings.Join(left, ", ") + " behind, which only a lane can resolve: " + strings.Join(whys, "; ")
	}
	return movedValue, said + "; CI and grades run again at the new heads", touched
}

func clipped(text string) string {
	runes := []rune(text)
	if len(runes) <= recordText {
		return text
	}
	return string(runes[:recordText-1]) + "…"
}
