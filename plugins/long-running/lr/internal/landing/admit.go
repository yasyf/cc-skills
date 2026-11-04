package landing

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"html"
	"log/slog"
	"maps"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bkread"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/board"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/pass"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	checkPRLimit   = 180 * time.Second
	checkPRReread  = 5 * time.Minute
	gitLimit       = time.Minute
	rulingsClear   = "rulings-clear"
	buildClear     = "build-clear"
	reservedPrefix = "RESERVED-NO-ANSWER "
	unreadPrefix   = "BLOCKER"
)

var (
	listItem = regexp.MustCompile(`(?s)<li>(.*?)</li>`)
	htmlTag  = regexp.MustCompile(`<[^>]+>`)
)

type BuildFailures struct {
	Tree        string `json:"tree"`
	Context     string `json:"context"`
	Annotations string `json:"annotations"`
}

type Gates struct {
	Runner   shell.Runner
	CCX      ccx.Client
	Store    *store.Store
	Drive    string
	Repo     string
	Checkout string
	Rulings  string
	Session  func(ctx context.Context) (string, error)
	Build    BuildFailures
	Token    bkread.Token
	Now      func() time.Time
	Log      *slog.Logger

	held map[string]time.Time
}

func NewGates(a *app.App, s *store.Store, cfg config.Config, runnerPath string) (*Gates, error) {
	runner, err := config.LoadRunner(runnerPath)
	if err != nil {
		return nil, err
	}
	var landing struct {
		Build        BuildFailures `json:"build_failures"`
		TokenCommand []string      `json:"ci_token_command"`
	}
	if err := json.Unmarshal(runner.Sections["landing"], &landing); err != nil {
		return nil, fmt.Errorf("%s: landing: %w", runnerPath, err)
	}
	claude, err := drive.ConfigDir()
	if err != nil {
		return nil, err
	}
	return &Gates{
		Runner: a.Runner, CCX: a.CCX, Store: s, Drive: cfg.Drive, Repo: cfg.Repo, Checkout: cfg.Checkout,
		Rulings: filepath.Join(claude, "long-running", "current", "bin", "rulings.py"),
		Session: func(ctx context.Context) (string, error) {
			inputs, err := readGateInputs(ctx, cfg.Drive, a.Now())
			return inputs.Session, err
		},
		Build: landing.Build, Token: bkread.Token{Command: landing.TokenCommand}, Now: a.Now, Log: a.Log,
	}, nil
}

func (g *Gates) Admit(ctx context.Context, acts []pass.Act, snapshot []machine.Facts) []pass.Act {
	refused := map[string]bool{}
	for _, act := range acts {
		if act.Verdict.Action == machine.ActionEnqueue && g.refuses(ctx, act.Facts) {
			refused[act.Facts.Branch] = true
		}
	}
	if len(refused) == 0 {
		return acts
	}
	byBranch := make(map[string]machine.Facts, len(snapshot))
	for _, f := range snapshot {
		byBranch[f.Branch] = f
	}
	return slices.DeleteFunc(slices.Clone(acts), func(act pass.Act) bool {
		return act.Verdict.Action == machine.ActionEnqueue && downstackMeets(act.Facts, byBranch, refused)
	})
}

func downstackMeets(f machine.Facts, byBranch map[string]machine.Facts, branches map[string]bool) bool {
	for open := true; open; f, open = byBranch[f.Base] {
		if branches[f.Branch] {
			return true
		}
	}
	return false
}

func (g *Gates) refuses(ctx context.Context, f machine.Facts) bool {
	declared := []gates.Gate{{Name: "safe on dev", Kind: "blocker", Check: safeOnDev}, {Name: "rulings", Kind: "report", Clear: rulingsClear, Check: g.reserved}}
	if g.Build.Tree != "" {
		declared = append(declared, gates.Gate{Name: "build", Kind: "blocker", Clear: buildClear, Check: g.buildFailure})
	}
	return gates.Keeper{Store: g.Store, Now: g.Now, Log: g.Log, Tell: g.tell}.Refuses(ctx, f, declared...)
}

func (g *Gates) tell(ctx context.Context, f machine.Facts, kind, id, text string) {
	if err := postToLane(ctx, g.Store, g.Drive, f, kind, id, text); err != nil {
		g.Log.Warn("a gate's refusal went unposted", "pr", f.Number, "err", err)
	}
}

func postToLane(ctx context.Context, s *store.Store, drive string, f machine.Facts, kind, id, text string) error {
	to := []string{rootLane}
	if f.Lane != "" {
		holder, err := lanes.Running(s, drive, f.Lane)
		if err != nil {
			return err
		}
		if holder == rootLane {
			text = "UNOWNED, " + f.Lane + " is not running: " + text
		} else {
			to = []string{holder, rootLane}
		}
	}
	_, err := client.Post(ctx, client.Draft{Drive: drive, Lane: self, Kind: kind, To: to, ID: id, PRs: []string{pullRef(f)}, Text: text})
	return err
}

func laneHolder(s *store.Store, drive, lane string) (string, error) {
	if lane == "" {
		return rootLane, nil
	}
	return lanes.Holder(s, drive, lane)
}

func pullRef(f machine.Facts) string {
	return f.Repo + "#" + strconv.Itoa(f.Number)
}

func safeOnDev(_ context.Context, f machine.Facts) (gates.Verdict, error) {
	if f.UnsafeWait == "" {
		return gates.Verdict{}, nil
	}
	return gates.Verdict{Cause: "unsafe-on-dev", Refusal: fmt.Sprintf("UNSAFE-ON-DEV #%d at %s stays out of the queue: %s. Name that gate in the body, or drop a wait that no longer holds, and this gate admits it.", f.Number, shortHead(f.Head), gates.UnsafeOnDev(f.UnsafeWait))}, nil
}

func (g *Gates) reserved(ctx context.Context, f machine.Facts) (gates.Verdict, error) {
	if read, known := g.held[f.Head]; known && g.Now().Sub(read) < checkPRReread {
		return gates.Verdict{Held: true}, nil
	}
	delete(g.held, f.Head)
	rows, unread, err := g.reservedRows(ctx, f)
	if err == nil && len(rows) > 0 && g.authorize(ctx, f, rows) {
		rows, unread, err = g.reservedRows(ctx, f)
	}
	if err != nil || len(rows) == 0 {
		return gates.Verdict{Reread: unread}, err
	}
	if g.held == nil {
		g.held = map[string]time.Time{}
	}
	g.held[f.Head] = g.Now()
	return g.ask(ctx, f, rows), nil
}

func (g *Gates) reservedRows(ctx context.Context, f machine.Facts) (rows []board.ReservedRow, unread bool, err error) {
	out, err := checkPR(ctx, g.Runner, g.Rulings, g.Checkout, g.Drive, f, "--reserved-only")
	if err != nil {
		return nil, true, err
	}
	for _, line := range strings.Split(out, "\n") {
		unread = unread || strings.HasPrefix(line, unreadPrefix)
		if row, found := strings.CutPrefix(line, reservedPrefix); found {
			key, sentence, _ := strings.Cut(row, "\t")
			rows = append(rows, board.ReservedRow{Key: key, Sentence: sentence})
		}
	}
	return rows, unread, nil
}

func checkPR(ctx context.Context, runner shell.Runner, rulings, checkout, drive string, f machine.Facts, scope string) (string, error) {
	result, err := runner.Run(ctx, shell.Command{
		Argv:    []string{rulings, "check-pr", "--repo", checkout, "--drive", drive, "--pr", pullRef(f), "--head", f.Head, scope},
		Timeout: checkPRLimit,
	})
	var exit *shell.ExitError
	if err != nil && (!errors.As(err, &exit) || exit.Code != 1) {
		return "", err
	}
	return result.Stdout, nil
}

func (g *Gates) authorize(ctx context.Context, f machine.Facts, rows []board.ReservedRow) bool {
	block := board.AnswerBlock(board.MergeWaits(f.Number, f.Head))
	granted := false
	for _, row := range rows {
		_, err := g.Runner.Run(ctx, shell.Command{
			Argv:    []string{g.Rulings, "authorize", "--repo", g.Checkout, "--drive", g.Drive, "--row", row.Key, "--block", block, "--option", board.MergeOption},
			Timeout: checkPRLimit,
		})
		granted = granted || err == nil
	}
	return granted
}

func (g *Gates) ask(ctx context.Context, f machine.Facts, rows []board.ReservedRow) gates.Verdict {
	waits := board.MergeWaits(f.Number, f.Head)
	touched := make([]string, 0, len(rows))
	for _, row := range rows {
		touched = append(touched, fmt.Sprintf("%s (%q)", row.Key, row.Sentence))
	}
	cause := "reserved"
	next := "Merge on card " + waits + " on his board is the answer that authorizes it, and this gate then admits the head; Hold keeps it out."
	if err := g.drawCard(ctx, f, rows); err != nil {
		g.Log.Warn("a reserved row's card did not draw", "pr", f.Number, "err", err)
		cause = "reserved-undrawn"
		next = fmt.Sprintf("Card %s did not draw (%v), so no answer can authorize it yet; the gate draws again every %s.", waits, err, checkPRReread)
	}
	return gates.Verdict{Cause: cause, Refusal: fmt.Sprintf("RESERVED #%d at %s stays out of the queue: it touches %s, which no owner answer authorizes. %s", f.Number, shortHead(f.Head), strings.Join(touched, ", "), next)}
}

func (g *Gates) drawCard(ctx context.Context, f machine.Facts, rows []board.ReservedRow) error {
	stored, err := g.Store.Facts(f.Repo, f.Number, f.Head)
	if err != nil || stored[board.FactWithdrawn] != "" {
		return err
	}
	session, err := g.Session(ctx)
	if err != nil {
		return err
	}
	view, err := g.CCX.GH(ctx, ccx.MaxAgeCached, "pr", "view", strconv.Itoa(f.Number), "--repo", f.Repo, "--json", board.CardPRFields)
	if err != nil {
		return err
	}
	var pr board.CardPR
	if err := json.Unmarshal([]byte(view), &pr); err != nil {
		return fmt.Errorf("view of #%d: %w", f.Number, err)
	}
	card, err := board.ReservedCard(f.Repo, f.Number, f.Head, rows, pr)
	if err != nil {
		return err
	}
	waits := board.MergeWaits(f.Number, f.Head)
	keys := make([]string, 0, len(rows))
	for _, row := range rows {
		keys = append(keys, row.Key)
	}
	_, err = client.Post(ctx, client.Draft{
		Drive: g.Drive,
		Lane:  self,
		Kind:  "note",
		Topic: boardWriteTopic,
		ID:    "board-ask:" + session + ":" + waits + ":" + strings.Join(keys, ","),
		PRs:   []string{pullRef(f)},
		Attrs: map[string]string{"op": "ask", "session": session, "waits": waits, "card": card},
		Text:  "board write: ask the merge card " + waits,
	})
	return err
}

func (g *Gates) buildFailure(ctx context.Context, f machine.Facts) (gates.Verdict, error) {
	failure, settled, err := g.readBuildFailure(ctx, f)
	if err != nil {
		return gates.Verdict{}, err
	}
	if failure != "" {
		return gates.Verdict{Cause: "build-failed", Refusal: fmt.Sprintf("BUILD-FAILED #%d: %s", f.Number, failure)}, nil
	}
	return gates.Verdict{Reread: !settled}, nil
}

func (g *Gates) readBuildFailure(ctx context.Context, f machine.Facts) (failure string, settled bool, err error) {
	files, err := g.CCX.GH(ctx, ccx.MaxAgeCached, "api", "--paginate", "repos/"+f.Repo+"/pulls/"+strconv.Itoa(f.Number)+"/files", "--jq", ".[].filename")
	if err != nil {
		return "", false, err
	}
	if !slices.ContainsFunc(strings.Fields(files), func(file string) bool { return strings.HasPrefix(file, g.Build.Tree) }) {
		return "", true, nil
	}
	builds, err := g.finishedBuilds(ctx, f.Head)
	if err != nil {
		return "", false, err
	}
	for _, url := range builds {
		failures, err := g.annotated(ctx, url)
		if err != nil {
			return "", false, err
		}
		for _, name := range slices.Sorted(maps.Keys(failures)) {
			inherited, err := g.inherited(ctx, f.Head, url, name, failures[name])
			if err != nil {
				return "", false, err
			}
			said := fmt.Sprintf("%s fails to build at %s on %s: %s", name, shortHead(f.Head), url, failures[name])
			if !inherited {
				return said, true, nil
			}
			g.Log.Info("a head inherits a build failure", "pr", f.Number, "failure", said)
		}
	}
	return "", len(builds) > 0, nil
}

func (g *Gates) finishedBuilds(ctx context.Context, sha string) ([]string, error) {
	out, err := g.CCX.GH(ctx, ccx.MaxAgeCached, "api", "repos/"+g.Repo+"/commits/"+sha+"/status")
	if err != nil {
		return nil, err
	}
	var read struct {
		Statuses []struct {
			State     string `json:"state"`
			TargetURL string `json:"target_url"`
		} `json:"statuses"`
	}
	if err := json.Unmarshal([]byte(out), &read); err != nil {
		return nil, fmt.Errorf("status of %s: %w", sha, err)
	}
	var builds []string
	for _, status := range read.Statuses {
		if (status.State == "success" || status.State == "failure") && strings.Contains(status.TargetURL, "buildkite.com/") && buildURL.MatchString(status.TargetURL) {
			builds = append(builds, status.TargetURL)
		}
	}
	return builds, nil
}

func (g *Gates) annotated(ctx context.Context, url string) (map[string]string, error) {
	found := buildURL.FindStringSubmatch(url)
	words := strings.Fields(strings.NewReplacer("{pipeline}", found[1], "{number}", found[2]).Replace(g.Build.Annotations))
	words[0] = filepath.Join(g.Checkout, words[0])
	result, err := g.Token.Run(ctx, g.Runner, shell.Command{Argv: words, Dir: g.Checkout, Timeout: trunkReadLimit})
	if err != nil {
		return nil, err
	}
	var annotations []struct {
		Context  string `json:"context"`
		BodyHTML string `json:"body_html"`
	}
	if err := json.Unmarshal([]byte(result.Stdout), &annotations); err != nil {
		return nil, fmt.Errorf("annotations of %s: %w", url, err)
	}
	failures := map[string]string{}
	for _, annotation := range annotations {
		if annotation.Context != g.Build.Context {
			continue
		}
		for _, item := range listItem.FindAllStringSubmatch(annotation.BodyHTML, -1) {
			if name, compile, found := strings.Cut(strings.TrimSpace(html.UnescapeString(htmlTag.ReplaceAllString(item[1], ""))), " "); found {
				failures[name] = strings.TrimSpace(compile)
			}
		}
	}
	return failures, nil
}

func (g *Gates) inherited(ctx context.Context, sha, url, name, compile string) (bool, error) {
	base, err := g.mergeBase(ctx, sha)
	if err != nil {
		return false, err
	}
	builds, err := g.finishedBuilds(ctx, base)
	if err != nil {
		return false, err
	}
	pipeline := buildURL.FindStringSubmatch(url)[1]
	at := slices.IndexFunc(builds, func(built string) bool { return buildURL.FindStringSubmatch(built)[1] == pipeline })
	if at < 0 {
		return false, nil
	}
	failures, err := g.annotated(ctx, builds[at])
	if err != nil {
		return false, err
	}
	return failures[name] == compile, nil
}

func (g *Gates) mergeBase(ctx context.Context, sha string) (string, error) {
	if _, err := g.git(ctx, gitLimit, "cat-file", "-e", sha+"^{commit}"); err != nil {
		if _, err := g.git(ctx, trunkReadLimit, "fetch", "-q", "--no-tags", "origin", sha); err != nil {
			return "", err
		}
	}
	return g.git(ctx, gitLimit, "merge-base", "HEAD", sha)
}

func (g *Gates) git(ctx context.Context, limit time.Duration, args ...string) (string, error) {
	result, err := g.Runner.Run(ctx, shell.Command{Argv: append([]string{"git", "-C", g.Checkout}, args...), Timeout: limit})
	return strings.TrimSpace(result.Stdout), err
}

func shortHead(sha string) string {
	return sha[:min(10, len(sha))]
}
