package orca

import (
	"cmp"
	"context"
	"fmt"
	"io"
	"os"
	"slices"
	"strings"
	"sync"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	graderHome      = "/home/sprite"
	graderCheckout  = "/home/sprite/monorepo"
	graderSeed      = "/tmp/store-seed.gz"
	linkGrader      = `set -e; sudo -n mkdir -p "$(dirname "$1")"; sudo -n ln -sfn ` + graderHome + ` "$1"; shift; for checkout; do mkdir -p "$(dirname "$checkout")"; ln -sfn ` + graderCheckout + ` "$checkout"; done`
	unshallowGrader = `set -eu; IFS= read -r GH_TOKEN; export GH_TOKEN GIT_TERMINAL_PROMPT=0; [ "$(git -C "$1" rev-parse --is-shallow-repository)" = false ] || git -C "$1" -c credential.helper= -c 'credential.helper=!gh auth git-credential' fetch --quiet --unshallow origin`
	seedGrader      = `trap 'rm -f "$2"' EXIT; ~/.local/bin/cci relay seed --drive "$1" < "$2"`
)

var (
	graderTools = []string{"ccx", "ccn", graderTool}
	graderNeeds = slices.Concat([]string{"lr", "cci", "codex", "git"}, graderTools)
)

type GraderMaker struct {
	Runner shell.Runner
	Store  *store.Store
	Home   string
	Sleep  func(time.Duration)
	Now    func() time.Time
	Out    io.Writer

	said sync.Mutex
}

type graderPlan struct {
	pins      spritePins
	checkouts []string
	token     string
	sponsor   string
	drive     string
	seed      string
}

func (g *GraderMaker) Make(ctx context.Context, runner string, boxes []string) error {
	file, err := readRunnerFile(runner)
	if err != nil {
		return err
	}
	env, err := runnerEnv(runner)
	if err != nil {
		return err
	}
	plan := graderPlan{sponsor: env(sponsorEnv), drive: file.Drive}
	if plan.sponsor == "" {
		return refuse("lr sprites grader: set orca.sprite.env.%s in %s to the Sprite holding the drive's enrolled cci service key; no grader was made", sponsorEnv, runner)
	}
	if plan.pins, err = readPins(env); err != nil {
		return err
	}
	plan.pins = plan.pins.only(graderTools...)
	for _, checkout := range []string{file.Landing.Checkout, file.Finder.Checkout, file.Fidelity.Checkout} {
		if checkout != "" && !slices.Contains(plan.checkouts, checkout) {
			plan.checkouts = append(plan.checkouts, checkout)
		}
	}
	token, err := g.Runner.Run(ctx, shell.Command{Argv: []string{"ccx", "vcs", "auth", "token", file.Landing.Repo}})
	if err != nil {
		return refuse("lr sprites grader: no token for %s to fetch the checkout's history: %s; no grader was made", file.Landing.Repo, cmp.Or(lastSaid(token), err.Error()))
	}
	plan.token = strings.TrimSpace(token.Stdout) + "\n"
	seed, err := os.CreateTemp("", "grader-seed-*.gz")
	if err != nil {
		return err
	}
	defer func() { _ = os.Remove(seed.Name()) }()
	start := g.Now()
	exported, err := g.Runner.Run(ctx, shell.Command{Argv: []string{"cci", "relay", "export", "--drive", plan.drive}, Stdout: seed})
	if closed := seed.Close(); err == nil {
		err = closed
	}
	if err != nil {
		return refuse("lr sprites grader: cci relay export --drive %s failed: %s; no grader was made", plan.drive, cmp.Or(lastSaid(exported), err.Error()))
	}
	plan.seed = seed.Name()
	g.say("lr sprites grader: exported %s once for %d Sprites in %s", plan.drive, len(boxes), g.since(start))
	failed := make([]string, len(boxes))
	var made sync.WaitGroup
	for i, box := range boxes {
		made.Go(func() {
			if err := g.make(ctx, box, plan); err != nil {
				g.say("%s", err.Error())
				failed[i] = box
			}
		})
	}
	made.Wait()
	if failed = slices.DeleteFunc(failed, func(box string) bool { return box == "" }); len(failed) > 0 {
		return refuse("lr sprites grader: %d of %d Sprites were not made: %s", len(failed), len(boxes), strings.Join(failed, " "))
	}
	return nil
}

func (g *GraderMaker) make(ctx context.Context, box string, plan graderPlan) error {
	installed, err := g.step(ctx, box, "install pins", shell.Command{Argv: plan.pins.installArgv(box, graderNeeds), Timeout: spriteInstallTimeout})
	if recorded := g.Store.RecordSpriteTools(box, installedTools(installed.Stdout), g.Now()); recorded != nil {
		return recorded
	}
	if err != nil {
		return err
	}
	if _, err := g.step(ctx, box, "links", shell.Command{Argv: slices.Concat([]string{"sprite", "exec", "-s", box, "--no-stdin", "--", "sh", "-c", linkGrader, "sh", g.Home}, plan.checkouts)}); err != nil {
		return err
	}
	if _, err := g.step(ctx, box, "whole history", shell.Command{Argv: []string{"sprite", "exec", "-s", box, "--", "sh", "-c", unshallowGrader, "sh", graderCheckout}, Stdin: plan.token}); err != nil {
		return err
	}
	start := g.Now()
	w := &workerLaunch{WorkerStarter: &WorkerStarter{Runner: g.Runner, Sleep: g.Sleep}, ctx: ctx}
	if _, err := w.join(box, plan.sponsor); err != nil {
		return refuse("lr sprites grader: %s did not join the cci relay with %s's service key: %v", box, plan.sponsor, err)
	}
	g.say("%s: relay join in %s", box, g.since(start))
	_, err = g.step(ctx, box, "store seed", shell.Command{Argv: []string{"sprite", "exec", "-s", box, "--no-stdin", "--file", plan.seed + ":" + graderSeed, "--", "sh", "-c", seedGrader, "sh", plan.drive, graderSeed}})
	return err
}

func (g *GraderMaker) step(ctx context.Context, box, name string, c shell.Command) (shell.Result, error) {
	start := g.Now()
	result, err := g.Runner.Run(ctx, c)
	if err != nil {
		return result, refuse("lr sprites grader: %s stopped at %s: %s", box, name, cmp.Or(lastSaid(result), err.Error()))
	}
	g.say("%s: %s in %s", box, name, g.since(start))
	return result, nil
}

func (g *GraderMaker) since(start time.Time) time.Duration {
	return g.Now().Sub(start).Round(time.Second)
}

func (g *GraderMaker) say(format string, args ...any) {
	g.said.Lock()
	defer g.said.Unlock()
	fmt.Fprintf(g.Out, format+"\n", args...)
}
