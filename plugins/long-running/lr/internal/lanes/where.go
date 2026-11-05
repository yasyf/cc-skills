package lanes

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bkread"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	releaseReadLimit = 5 * time.Minute
	releaseRunning   = "running"
	releaseQueued    = "queued"
	releaseHeld      = "held"
	releaseConverged = "converged"
	releaseFailed    = "failed"
	waitingShown     = 3
)

var (
	migrationFile = regexp.MustCompile(`(?:^|/)migrations/[^/]+$`)
	buildTail     = regexp.MustCompile(`(\d+)/?$`)
	prArgument    = regexp.MustCompile(`^#?(\d+)$`)
)

type Deploy struct {
	Target  string    `json:"target"`
	Env     string    `json:"env"`
	Release string    `json:"release"`
	Name    string    `json:"name"`
	Build   string    `json:"build"`
	Commit  string    `json:"commit"`
	Since   time.Time `json:"since"`
}

type Cut struct {
	ID         string   `json:"id"`
	Name       string   `json:"name"`
	Mode       string   `json:"mode"`
	Status     string   `json:"status"`
	Build      string   `json:"build"`
	Commit     string   `json:"commit"`
	Components []string `json:"components"`
	Envs       []string `json:"envs"`
	Unchanged  []struct {
		Component string `json:"component"`
	} `json:"unchanged"`
	Timestamps struct {
		Running   time.Time `json:"running"`
		Converged time.Time `json:"converged"`
	} `json:"timestamps"`
}

type ReleaseView struct {
	Deploys    []Deploy
	Cuts       []Cut
	LiveUnread error
	CutsUnread error
}

type ReleaseRead struct {
	Runner   shell.Runner
	CLI      []string
	Token    bkread.Token
	Checkout string
	Now      func() time.Time
	holds    map[[2]string]bool
}

type Carrier struct {
	Number string    `json:"number"`
	Name   string    `json:"name"`
	ID     string    `json:"id"`
	Status string    `json:"status"`
	ETA    time.Time `json:"eta"`
	ETAWhy string    `json:"eta_unknown,omitempty"`
}

type EnvLive struct {
	Env   string    `json:"env"`
	Since time.Time `json:"since"`
}

type TargetState struct {
	Name   string    `json:"target"`
	Live   []EnvLive `json:"live"`
	Behind []string  `json:"behind"`
}

type Where struct {
	SHA        string        `json:"sha"`
	Release    *Carrier      `json:"release"`
	Targets    []TargetState `json:"targets"`
	Waiting    []string      `json:"waiting"`
	Outside    []string      `json:"outside,omitempty"`
	Failed     *Carrier      `json:"failed,omitempty"`
	LiveAt     time.Time     `json:"live_at"`
	LiveOn     int           `json:"live_on"`
	OfEnvs     int           `json:"of_envs"`
	Migrations []string      `json:"migrations"`
	Unknown    []string      `json:"unknown"`
}

func readDeploys(ctx context.Context, runner shell.Runner, token *bkread.Token, cli []string, checkout string) ([]Deploy, error) {
	result, err := token.Run(ctx, runner, shell.Command{Argv: append(slices.Clone(cli), "live", "--json"), Dir: checkout, Timeout: releaseReadLimit})
	if err != nil {
		return nil, fmt.Errorf("%w: %w", ErrUnread, err)
	}
	var deploys []Deploy
	if err := json.Unmarshal([]byte(result.Stdout), &deploys); err != nil {
		return nil, fmt.Errorf("%w: release live: %w", ErrUnread, err)
	}
	return deploys, nil
}

func changedPaths(ctx context.Context, runner shell.Runner, checkout, sha string) ([]string, error) {
	diff, err := runner.Run(ctx, shell.Command{Argv: []string{"git", "-C", checkout, "diff-tree", "--no-commit-id", "--name-only", "-r", sha}, Timeout: readTimeout})
	if err != nil {
		return nil, fmt.Errorf("%w: %w", ErrUnread, err)
	}
	return strings.FieldsFunc(diff.Stdout, func(r rune) bool { return r == '\n' }), nil
}

func touchedBy(ctx context.Context, runner shell.Runner, cli []string, checkout string, paths []string) ([]string, error) {
	request, err := json.Marshal(map[string]any{"body": "", "paths": paths})
	if err != nil {
		return nil, err
	}
	selected, err := runner.Run(ctx, shell.Command{Argv: append(slices.Clone(cli), "select", "--pr"), Stdin: string(request), Dir: checkout, Timeout: readTimeout})
	if err != nil {
		return nil, fmt.Errorf("%w: %w", ErrUnread, err)
	}
	var answer struct {
		Touched []string `json:"touched"`
	}
	if err := json.Unmarshal([]byte(selected.Stdout), &answer); err != nil {
		return nil, fmt.Errorf("%w: release select --pr: %w", ErrUnread, err)
	}
	return answer.Touched, nil
}

func (r *ReleaseRead) View(ctx context.Context) ReleaseView {
	var view ReleaseView
	view.Deploys, view.LiveUnread = readDeploys(ctx, r.Runner, &r.Token, r.CLI, r.Checkout)
	view.Cuts, view.CutsUnread = readCuts(ctx, r.Runner, &r.Token, r.CLI, r.Checkout)
	return view
}

func readCuts(ctx context.Context, runner shell.Runner, token *bkread.Token, cli []string, checkout string) ([]Cut, error) {
	result, err := token.Run(ctx, runner, shell.Command{Argv: append(slices.Clone(cli), "status", "--json"), Dir: checkout, Timeout: releaseReadLimit})
	if err != nil {
		return nil, fmt.Errorf("%w: %w", ErrUnread, err)
	}
	var cuts []Cut
	if err := json.Unmarshal([]byte(result.Stdout), &cuts); err != nil {
		return nil, fmt.Errorf("%w: release status: %w", ErrUnread, err)
	}
	return cuts, nil
}

func (r *ReleaseRead) present(ctx context.Context, sha string) error {
	if _, err := r.Runner.Run(ctx, shell.Command{Argv: []string{"git", "-C", r.Checkout, "cat-file", "-e", sha + "^{commit}"}, Timeout: readTimeout}); err == nil {
		return nil
	}
	_, err := r.Runner.Run(ctx, shell.Command{Argv: []string{"git", "-C", r.Checkout, "fetch", "--quiet", "origin", sha}, Timeout: releaseReadLimit})
	return err
}

func (r *ReleaseRead) carries(ctx context.Context, commit, sha string) (bool, error) {
	key := [2]string{commit, sha}
	if held, known := r.holds[key]; known {
		return held, nil
	}
	if err := r.present(ctx, commit); err != nil {
		return false, err
	}
	_, err := r.Runner.Run(ctx, shell.Command{Argv: []string{"git", "-C", r.Checkout, "merge-base", "--is-ancestor", sha, commit}, Timeout: readTimeout})
	var exit *shell.ExitError
	if err != nil && (!errors.As(err, &exit) || exit.Code != 1) {
		return false, err
	}
	if r.holds == nil {
		r.holds = map[[2]string]bool{}
	}
	r.holds[key] = err == nil
	return err == nil, nil
}

func (r *ReleaseRead) Locate(ctx context.Context, view ReleaseView, sha string) Where {
	w := Where{SHA: sha}
	unknown := func(fact string, err error) { w.Unknown = append(w.Unknown, fact+": "+err.Error()) }
	if err := r.present(ctx, sha); err != nil {
		unknown("the commit", err)
		return w
	}
	paths, err := changedPaths(ctx, r.Runner, r.Checkout, sha)
	if err != nil {
		unknown("what it changed", err)
		return w
	}
	w.Migrations = migrations(paths)
	touched, err := touchedBy(ctx, r.Runner, r.CLI, r.Checkout, paths)
	if err != nil {
		unknown("which targets it touches", err)
		return w
	}
	if view.LiveUnread != nil {
		unknown("what is live", view.LiveUnread)
	}
	if view.CutsUnread != nil {
		unknown("which releases are running", view.CutsUnread)
	}
	var first *Deploy
	for _, name := range touched {
		target := TargetState{Name: name}
		for _, deploy := range view.Deploys {
			if deploy.Target != name {
				continue
			}
			held, err := r.carries(ctx, deploy.Commit, sha)
			if err != nil {
				unknown(name+" on "+deploy.Env, err)
				continue
			}
			if !held {
				target.Behind = append(target.Behind, deploy.Env)
				continue
			}
			target.Live = append(target.Live, EnvLive{deploy.Env, deploy.Since})
			if deploy.Build != "" && (first == nil || deploy.Since.Before(first.Since)) {
				first = &deploy
			}
		}
		w.Targets = append(w.Targets, target)
	}
	var carrier *Cut
	if first != nil {
		if at := slices.IndexFunc(view.Cuts, func(cut Cut) bool { return cut.ID == first.Release }); at >= 0 {
			carrier = &view.Cuts[at]
		}
		w.settle(carrier)
		if len(w.Waiting) == 0 {
			w.Release = carrierOf(first, carrier)
			return w
		}
	}
	cut, failed, err := r.nextCut(ctx, view, sha, touched)
	if err != nil {
		unknown("which release carries it", err)
		return w
	}
	switch {
	case cut != nil && (cut.Status != releaseConverged || first == nil):
		w.settle(cut)
		w.Release = carrierOfCut(cut)
		if cut.Status == releaseRunning {
			w.Release.ETA, w.Release.ETAWhy = view.eta(*cut, r.Now())
		}
	case first != nil:
		w.Release = carrierOf(first, carrier)
	case failed != nil:
		w.Failed = carrierOfCut(failed)
	}
	return w
}

func (w *Where) settle(cut *Cut) {
	w.Waiting, w.Outside, w.LiveAt, w.LiveOn, w.OfEnvs = nil, nil, time.Time{}, 0, 0
	for _, target := range w.Targets {
		if cut != nil && !slices.Contains(cut.Components, target.Name) {
			w.Outside = append(w.Outside, target.Name)
			continue
		}
		for _, env := range target.Behind {
			switch {
			case cut == nil:
				w.OfEnvs++
			case slices.Contains(cut.Envs, env):
				w.Waiting = append(w.Waiting, target.Name+"/"+env)
			}
		}
		for _, env := range target.Live {
			if cut != nil && !slices.Contains(cut.Envs, env.Env) {
				continue
			}
			if env.Since.After(w.LiveAt) {
				w.LiveAt = env.Since
			}
			if cut == nil {
				w.OfEnvs++
				w.LiveOn++
			}
		}
	}
	if w.LiveOn == w.OfEnvs {
		w.LiveOn, w.OfEnvs = 0, 0
	}
}

func carrierOf(first *Deploy, cut *Cut) *Carrier {
	status := ""
	if cut != nil {
		status = cut.Status
	}
	return &Carrier{Number: releaseNumber(first.Build, first.Release), Name: first.Name, ID: first.Release, Status: status}
}

func carrierOfCut(cut *Cut) *Carrier {
	return &Carrier{Number: releaseNumber(cut.Build, cut.ID), Name: cut.Name, ID: cut.ID, Status: cut.Status}
}

func (r *ReleaseRead) nextCut(ctx context.Context, view ReleaseView, sha string, touched []string) (*Cut, *Cut, error) {
	var settled, failed *Cut
	for i := len(view.Cuts) - 1; i >= 0; i-- {
		cut := &view.Cuts[i]
		if cut.Mode == dryRunMode || !slices.Contains([]string{releaseRunning, releaseQueued, releaseHeld, releaseConverged, releaseFailed}, cut.Status) {
			continue
		}
		if !slices.ContainsFunc(cut.Components, func(component string) bool { return slices.Contains(touched, component) }) {
			continue
		}
		held, err := r.carries(ctx, cut.Commit, sha)
		if err != nil {
			return nil, nil, err
		}
		switch {
		case !held:
		case cut.Status == releaseFailed:
			failed = cut
		case cut.Status != releaseConverged:
			return cut, failed, nil
		case settled == nil:
			settled = cut
		}
	}
	return settled, failed, nil
}

func (v ReleaseView) eta(cut Cut, now time.Time) (time.Time, string) {
	var spans []time.Duration
	for _, other := range v.Cuts {
		if other.Mode == cut.Mode && other.Status == releaseConverged && !other.Timestamps.Running.IsZero() && !other.Timestamps.Converged.IsZero() {
			spans = append(spans, other.Timestamps.Converged.Sub(other.Timestamps.Running))
		}
	}
	if len(spans) == 0 {
		return time.Time{}, "no recent release converged to time it by"
	}
	slices.Sort(spans)
	typical := spans[len(spans)/2]
	at := cut.Timestamps.Running.Add(typical)
	if at.Before(now) {
		return time.Time{}, fmt.Sprintf("it has run past the %d minutes a recent release took", int(typical.Minutes()))
	}
	return at, ""
}

func releaseNumber(build, id string) string {
	if found := buildTail.FindStringSubmatch(build); found != nil {
		return found[1]
	}
	return id
}

func migrations(paths []string) []string {
	var names []string
	for _, changed := range paths {
		if migrationFile.MatchString(changed) {
			names = append(names, path.Base(changed))
		}
	}
	return names
}

func (w Where) Line() string {
	switch {
	case len(w.Unknown) > 0:
		return "unknown: " + w.Unknown[0]
	case len(w.Targets) == 0:
		return "touches no release target"
	case w.Release == nil && w.Failed != nil:
		return fmt.Sprintf("landed; release %s cut it and failed, so no release carries it now", w.Failed.Number)
	case w.Release == nil:
		return "landed, no release has cut it yet"
	}
	if len(w.Outside) == 0 {
		return w.carried()
	}
	return fmt.Sprintf("%s; it leaves out %s", w.carried(), strings.Join(w.Outside, ", "))
}

func (w Where) carried() string {
	switch {
	case (w.Release.Status == releaseQueued || w.Release.Status == releaseHeld) && (len(w.Waiting) > 0 || w.LiveAt.IsZero()):
		return fmt.Sprintf("release %s %s", w.Release.Number, w.Release.Status)
	case w.Release.Status == releaseRunning && (len(w.Waiting) > 0 || w.LiveAt.IsZero()):
		if w.Release.ETA.IsZero() {
			return fmt.Sprintf("release %s running, ETA unknown: %s", w.Release.Number, w.Release.ETAWhy)
		}
		return fmt.Sprintf("release %s running, ETA %s", w.Release.Number, pacific(w.Release.ETA))
	case len(w.Waiting) > 0:
		return fmt.Sprintf("in release %s, not live on %s", w.Release.Number, shownWaiting(w.Waiting))
	case w.LiveAt.IsZero():
		return fmt.Sprintf("in release %s, not live on any target it touches", w.Release.Number)
	case w.OfEnvs > 0:
		return fmt.Sprintf("in release %s, live at %s on %d of %d envs", w.Release.Number, pacific(w.LiveAt), w.LiveOn, w.OfEnvs)
	}
	return fmt.Sprintf("in release %s, live at %s", w.Release.Number, pacific(w.LiveAt))
}

func shownWaiting(waiting []string) string {
	if len(waiting) <= waitingShown {
		return strings.Join(waiting, ", ")
	}
	return fmt.Sprintf("%s and %d more", strings.Join(waiting[:waitingShown], ", "), len(waiting)-waitingShown)
}

func (w Where) Lines() []string {
	lines := []string{w.Line()}
	if w.Release != nil {
		lines = append(lines, fmt.Sprintf("release: %s %s (%s) %s", w.Release.Number, w.Release.Name, w.Release.ID, or(w.Release.Status, "past the recent releases read")))
	}
	for _, target := range w.Targets {
		lines = append(lines, target.line())
	}
	if len(w.Migrations) == 0 {
		lines = append(lines, "migrations: none")
	} else {
		lines = append(lines, "migrations: "+strings.Join(w.Migrations, ", "))
	}
	for _, why := range w.Unknown {
		lines = append(lines, "unknown: "+why)
	}
	return lines
}

func (t TargetState) line() string {
	if len(t.Live) == 0 && len(t.Behind) == 0 {
		return "target " + t.Name + ": unknown: the release ledger names no deploy of it"
	}
	var parts []string
	for _, env := range t.Live {
		parts = append(parts, fmt.Sprintf("live on %s since %s", env.Env, pacific(env.Since)))
	}
	if len(t.Behind) > 0 {
		parts = append(parts, "not on "+strings.Join(t.Behind, ", "))
	}
	return "target " + t.Name + ": " + strings.Join(parts, "; ")
}

func NewReleaseRead(a *app.App, entry drive.Entry) (*ReleaseRead, error) {
	landing, err := ReadRunnerLanding(entry.StateDir)
	if err != nil {
		return nil, err
	}
	cli := strings.Fields(landing.ReleaseCLI)
	if len(cli) == 0 {
		return nil, errors.New("runner.json names no landing.release_cli, so no release can be read")
	}
	return &ReleaseRead{Runner: a.Runner, CLI: cli, Token: bkread.Token{Command: landing.CITokenCommand}, Checkout: entry.Checkout, Now: a.Now}, nil
}

type mergedPR struct {
	State    string    `json:"state"`
	Title    string    `json:"title"`
	MergedAt time.Time `json:"mergedAt"`
	Commit   struct {
		OID string `json:"oid"`
	} `json:"mergeCommit"`
}

type located struct {
	PR       int       `json:"pr,omitempty"`
	Title    string    `json:"title,omitempty"`
	State    string    `json:"state,omitempty"`
	MergedAt time.Time `json:"merged_at"`
	Where
}

func WhereCommand(a *app.App) *cobra.Command {
	var driveName string
	var asJSON bool
	cmd := &cobra.Command{
		Use:   "where PR|SHA",
		Short: "Where a merged change is: its commit, the release carrying it, each target's state, and its migrations",
		Args:  cobra.ExactArgs(1),
		RunE: func(c *cobra.Command, args []string) error {
			entry, err := drive.Named(driveName)
			if err != nil {
				return err
			}
			found := located{}
			sha := args[0]
			if number := prArgument.FindStringSubmatch(args[0]); number != nil {
				if found.PR, err = strconv.Atoi(number[1]); err != nil {
					return err
				}
				out, err := a.CCX.GH(c.Context(), ccx.MaxAgeFresh, "pr", "view", number[1], "--repo", entry.Repo, "--json", "state,title,mergedAt,mergeCommit")
				if err != nil {
					return err
				}
				var pr mergedPR
				if err := json.Unmarshal([]byte(out), &pr); err != nil {
					return fmt.Errorf("the view of #%d: %w", found.PR, err)
				}
				found.Title, found.State, found.MergedAt, sha = pr.Title, strings.ToLower(pr.State), pr.MergedAt, pr.Commit.OID
			}
			if sha != "" {
				read, err := NewReleaseRead(a, entry)
				if err != nil {
					return err
				}
				found.Where = read.Locate(c.Context(), read.View(c.Context()), sha)
			}
			if asJSON {
				return json.NewEncoder(c.OutOrStdout()).Encode(found)
			}
			if found.PR != 0 {
				fmt.Fprintln(c.OutOrStdout(), found.head())
			}
			if sha == "" {
				return nil
			}
			fmt.Fprintln(c.OutOrStdout(), strings.Join(found.Lines(), "\n"))
			return nil
		},
	}
	cmd.Flags().StringVar(&driveName, "drive", os.Getenv("LR_DRIVE"), "the drive's bus name")
	cmd.Flags().BoolVar(&asJSON, "json", false, "print the answer as one JSON object")
	return cmd
}

func (l located) head() string {
	if l.SHA == "" {
		return fmt.Sprintf("#%d is %s, not merged: %s", l.PR, l.State, l.Title)
	}
	return fmt.Sprintf("#%d merged %s as %s: %s", l.PR, pacific(l.MergedAt), l.SHA[:min(12, len(l.SHA))], l.Title)
}
