package orca

import (
	"cmp"
	"context"
	"fmt"
	"io"
	"slices"
	"strings"
	"sync"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	graderCheckout  = "/home/sprite/monorepo"
	graderShipped   = "/tmp/lr-ship"
	mirrorGrader    = `set -e; if [ -L "$1" ]; then for held in "$2"/*/*.lock; do [ ! -e "$held" ] || flock -n -x "$held" true || { echo "a pass holds $held, so $1 stays a link to this Sprite's home until it ends" >&2; exit 1; }; done; rm "$1"; fi; clone=$3; shift 3; for checkout; do mkdir -p "$(dirname "$checkout")"; ln -sfn "$clone" "$checkout"; done`
	unshallowGrader = `set -eu; IFS= read -r GH_TOKEN; export GH_TOKEN GIT_TERMINAL_PROMPT=0; [ "$(git -C "$1" rev-parse --is-shallow-repository)" = false ] || git -C "$1" -c credential.helper= -c 'credential.helper=!gh auth git-credential' fetch --quiet --unshallow origin`
)

var (
	graderTools = []string{"ccx", "ccn", graderTool}
	graderNeeds = slices.Concat([]string{"lr", "codex", "git"}, graderTools)
)

type GraderMaker struct {
	Runner shell.Runner
	Store  *store.Store
	Home   string
	Now    func() time.Time
	Out    io.Writer

	said sync.Mutex
}

type graderPlan struct {
	pins      spritePins
	checkouts []string
	token     string
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
	var plan graderPlan
	if plan.pins, err = readPins(env); err != nil {
		return err
	}
	plan.pins = plan.pins.forGrader(graderTools...)
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
	for _, warning := range installWarnings(installed.Stdout) {
		g.say("%s: %s", box, warning)
	}
	if recorded := g.Store.RecordSpriteTools(box, installedTools(installed.Stdout), g.Now()); recorded != nil {
		return recorded
	}
	if err != nil {
		return err
	}
	if _, err := g.step(ctx, box, "mirror", shell.Command{Argv: slices.Concat([]string{"sprite", "exec", "-s", box, "--no-stdin", "--", "sh", "-c", mirrorGrader, "sh", g.Home, graderShipped, graderCheckout}, plan.checkouts)}); err != nil {
		return err
	}
	if _, err := g.step(ctx, box, "whole history", shell.Command{Argv: []string{"sprite", "exec", "-s", box, "--", "sh", "-c", unshallowGrader, "sh", graderCheckout}, Stdin: plan.token}); err != nil {
		return err
	}
	return nil
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
