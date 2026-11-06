package landing

import (
	"context"
	"fmt"
	"log/slog"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/pass"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/watch"
)

const (
	squashedFact    = "incident-squash"
	rebasedFact     = "rebased-direct"
	incidentKind    = "incident"
	incidentHorizon = 14 * 24 * time.Hour
	mergeLimit      = 2 * time.Minute
	statusOpen      = "open"
	squashMerged    = "merged"
)

var (
	incidentLane    = regexp.MustCompile(`^(?:incident|outage)-`)
	incidentClose   = regexp.MustCompile(`\bINCIDENT (?:\S+ CLOSED|CLOSED \S+)\b|\bis a closed incident\b`)
	stackMember     = regexp.MustCompile(`asynchronous merge REST API`)
	incidentCleared = regexp.MustCompile(`(?i)not an incident fix|off the incident path|no release is owed`)
	clearingKinds   = []string{"go", "hold", "lift", "decide", "decision", "release"}
	incidentChecks  = []string{"RULINGS-UNCITED", "RESERVED-NO-ANSWER", "TEST-REMOVED"}
	activeSays      = regexp.MustCompile(`(?i)\bactive (yes|no)\b`)
)

type Squash struct {
	Runner     shell.Runner
	CCX        ccx.Client
	Store      *store.Store
	Drive      string
	Trunk      string
	Checkout   string
	Rulings    string
	FixLane    func(lane string) (bool, error)
	LiveClocks func() ([]string, error)
	Restack    func(ctx context.Context, number int) (string, error)
	Wait       time.Duration
	Now        func() time.Time
	Log        *slog.Logger
}

func NewSquash(a *app.App, s *store.Store, cfg config.Config, runnerPath string, remote Remote, fixLane func(string) (bool, error), liveClocks func() ([]string, error)) (*Squash, error) {
	claude, err := drive.ConfigDir()
	if err != nil {
		return nil, err
	}
	runner, err := config.LoadRunner(runnerPath)
	if err != nil {
		return nil, err
	}
	return &Squash{
		Runner: a.Runner, CCX: a.CCX, Store: s, Drive: cfg.Drive, Trunk: cfg.Trunk, Checkout: cfg.Checkout,
		Rulings: filepath.Join(claude, "long-running", "current", "bin", "rulings.py"),
		FixLane: fixLane, LiveClocks: liveClocks, Restack: remote.Run, Wait: time.Duration(runner.Deadlines.AlertWaitMinutes) * time.Minute, Now: a.Now, Log: a.Log,
	}, nil
}

func (q *Squash) Pass(ctx context.Context, snapshot []machine.Facts) error {
	_, err := q.pass(ctx, snapshot, true)
	return err
}

func (q *Squash) Admit(ctx context.Context, acts []pass.Act, snapshot []machine.Facts) []pass.Act {
	taken, err := q.pass(ctx, snapshot, false)
	if err != nil {
		q.Log.Warn("the incident squash pass did not read", "err", err)
	}
	return slices.DeleteFunc(slices.Clone(acts), func(act pass.Act) bool {
		return taken[act.Facts.Number] && (act.Verdict.Action == machine.ActionEnqueue || leftToSquash(act))
	})
}

func leftToSquash(act pass.Act) bool {
	f := act.Facts
	return act.Verdict.Action == machine.ActionDequeue && f.Queued() && f.Hold == "" && f.Fidelity == machine.FidelityPass
}

func (q *Squash) pass(ctx context.Context, snapshot []machine.Facts, lands bool) (map[int]bool, error) {
	named, err := q.tagged(snapshot)
	if err != nil || len(named) == 0 {
		return nil, err
	}
	since := q.Now().Add(-incidentHorizon)
	incidents, err := client.Tail(ctx, client.Query{Drive: q.Drive, Kinds: []string{incidentKind}, Since: since})
	if err != nil {
		return nil, err
	}
	decides, err := client.Tail(ctx, client.Query{Drive: q.Drive, Kinds: []string{"decide"}, Lanes: []string{rootLane}, Since: since})
	if err != nil {
		return nil, err
	}
	ended := endedLanes(slices.Concat(incidents, decides))
	clocks, err := q.LiveClocks()
	if err != nil {
		return nil, err
	}
	taken := map[int]bool{}
	for _, f := range named {
		if _, closed := ended[client.Stem(f.Lane)]; closed {
			continue
		}
		cleared, err := q.cleared(ctx, f)
		if err != nil {
			return taken, err
		}
		if cleared {
			if lands {
				q.tell(ctx, f, "report", "incident-tag-cleared:"+strconv.Itoa(f.Number), fmt.Sprintf("INCIDENT-TAG-CLEARED #%d: a coordinator record took it off the incident path, so the runner never squashes it and gates it as a normal row, landing it only through the queue once its go and the owner's tap name its head", f.Number))
			}
			continue
		}
		if (strings.HasPrefix(f.Lane, trunkTopic+"-") || scopedOut(f.Lane, incidents)) && !f.Landable() {
			continue
		}
		taken[f.Number] = true
		if !lands {
			continue
		}
		open := openIncident(f, incidents, ended)
		if open == nil && !slices.Contains(clocks, client.Stem(f.Lane)) && !f.Admitted() {
			due, err := q.due(f)
			if err != nil {
				return taken, err
			}
			if due.IsZero() || q.Now().Before(due) || f.Tap == machine.TapWithdrawn {
				continue
			}
		}
		if err := q.land(ctx, f, open); err != nil {
			return taken, err
		}
	}
	return taken, nil
}

func scopedOut(lane string, incidents []client.Record) bool {
	family := func(name string) bool { return name == lane || strings.HasPrefix(name, lane+"-") }
	said := ""
	for _, record := range incidents {
		if !family(record.Lane) && (record.Lane != rootLane || !slices.ContainsFunc(record.To, family)) {
			continue
		}
		if found := activeSays.FindStringSubmatch(record.Text); found != nil {
			said = strings.ToLower(found[1])
		}
	}
	return said == "no"
}

func (q *Squash) due(f machine.Facts) (time.Time, error) {
	facts, err := q.Store.Facts(f.Repo, f.Number, f.Head)
	if err != nil || facts[gates.ShownFact] == "" {
		return time.Time{}, err
	}
	shown, err := store.Parse(facts[gates.ShownFact])
	if err != nil {
		return time.Time{}, err
	}
	due := shown.Add(q.Wait)
	_, err = q.Store.PutFact(f.Repo, f.Number, f.Head, gates.MergesAtFact, store.Stamp(due), q.Now())
	return due, err
}

func (q *Squash) alerted(ctx context.Context, f machine.Facts, squashed bool) (bool, error) {
	named, err := q.tagged([]machine.Facts{f})
	if err != nil || (len(named) == 0 && !squashed) {
		return false, err
	}
	since := q.Now().Add(-incidentHorizon)
	incidents, err := client.Tail(ctx, client.Query{Drive: q.Drive, Kinds: []string{incidentKind}, Since: since})
	if err != nil {
		return false, err
	}
	decides, err := client.Tail(ctx, client.Query{Drive: q.Drive, Kinds: []string{"decide"}, Lanes: []string{rootLane}, Since: since})
	if err != nil {
		return false, err
	}
	return openIncident(f, incidents, endedLanes(slices.Concat(incidents, decides))) != nil, nil
}

func (q *Squash) tagged(snapshot []machine.Facts) ([]machine.Facts, error) {
	var named []machine.Facts
	for _, f := range snapshot {
		if f.Base != q.Trunk || f.Closed || f.Merged || f.Draft || f.Foreign() {
			continue
		}
		fix := incidentLane.MatchString(f.Lane)
		if !fix && f.Lane != "" {
			var err error
			if fix, err = q.FixLane(f.Lane); err != nil {
				return nil, err
			}
		}
		if fix {
			named = append(named, f)
		}
	}
	return named, nil
}

func endedLanes(records []client.Record) map[string]int64 {
	ended := map[string]int64{}
	for _, record := range records {
		if !closesIncident(record) {
			continue
		}
		for _, lane := range record.To {
			ended[client.Stem(lane)] = max(ended[client.Stem(lane)], record.Seq)
		}
	}
	return ended
}

func closesIncident(record client.Record) bool {
	return record.Lane == rootLane && incidentClose.MatchString(record.Text)
}

func openIncident(f machine.Facts, incidents []client.Record, ended map[string]int64) *client.Record {
	var newest *client.Record
	number := regexp.MustCompile(`#` + strconv.Itoa(f.Number) + `\b`)
	for i, record := range incidents {
		if record.Kind != incidentKind || record.Status != statusOpen || closesIncident(record) || supersededIncident(record, ended) {
			continue
		}
		if namesPull(record, f) || number.MatchString(record.Text) || record.Lane == f.Lane || lanes.Addresses(record.To, f.Lane) {
			newest = &incidents[i]
		}
	}
	return newest
}

func namesPull(record client.Record, f machine.Facts) bool {
	for _, pr := range record.Refs.PRs {
		if pr.Number == f.Number && pr.Repo == f.Repo {
			return true
		}
	}
	return false
}

func supersededIncident(record client.Record, ended map[string]int64) bool {
	lanes := slices.DeleteFunc(append([]string{record.Lane}, record.To...), func(lane string) bool { return lane == rootLane })
	return len(lanes) > 0 && !slices.ContainsFunc(lanes, func(lane string) bool { return ended[client.Stem(lane)] <= record.Seq })
}

func (q *Squash) cleared(ctx context.Context, f machine.Facts) (bool, error) {
	records, err := client.Tail(ctx, client.Query{Drive: q.Drive, Lanes: []string{rootLane}, PR: pullRef(f)})
	if err != nil {
		return false, err
	}
	return slices.ContainsFunc(records, func(record client.Record) bool {
		return slices.Contains(clearingKinds, string(record.Kind)) && incidentCleared.MatchString(record.Text)
	}), nil
}

func (q *Squash) land(ctx context.Context, f machine.Facts, open *client.Record) error {
	facts, err := q.Store.Facts(f.Repo, f.Number, f.Head)
	if err != nil {
		return err
	}
	rebased := facts[rebasedFact] != ""
	if facts[squashedFact] != "" || !f.Approved || !f.Green() {
		return nil
	}
	if (gates.Keeper{Store: q.Store, Now: q.Now, Log: q.Log, Tell: q.tell}).Refuses(ctx, f, gates.Gate{Name: "incident rulings", Kind: "blocker", Check: q.rulings}) {
		return nil
	}
	number := strconv.Itoa(f.Number)
	if !rebased && f.Fidelity != machine.FidelityPass && !f.Admitted() {
		return nil
	}
	if f.Hold != "" {
		if open == nil {
			return nil
		}
		q.tell(ctx, f, "report", "incident-hold:"+number+":"+f.Head, fmt.Sprintf("INCIDENT-HOLD-WARNED #%d: a hold names the approved, green incident fix at %s, and open incident %s names it, so the hold warns and it merges (owner, Nov 1)", f.Number, shortHead(f.Head), open.Name()))
	}
	moved, err := q.rebase(ctx, f, rebased)
	if err != nil {
		q.tell(ctx, f, "blocker", "rebase-direct:"+number+":"+f.Head, fmt.Sprintf("#%d %s: the approved, green incident fix is behind the latest %s, a merge outside the queue lands only rebased onto it (owner e7170fa), and the rebase did not finish: %v", f.Number, shortHead(f.Head), q.Trunk, err))
		return nil
	}
	if moved {
		return nil
	}
	argv := []string{"gh", "pr", "merge", number, "--repo", f.Repo, "--squash", "--match-head-commit", f.Head}
	merged, err := q.Runner.Run(ctx, shell.Command{Argv: argv, Timeout: mergeLimit})
	if err != nil && stackMember.MatchString(merged.Stderr+merged.Stdout) {
		argv = []string{"gh", "stack", "merge", number, "--yes", "--squash"}
		merged, err = q.Runner.Run(ctx, shell.Command{Argv: argv, Env: []string{"GH_REPO=" + f.Repo}, Timeout: mergeLimit})
	}
	if err != nil {
		if state, read := q.CCX.GH(ctx, ccx.MaxAgeFresh, "pr", "view", number, "--repo", f.Repo, "--json", "state", "--jq", ".state"); read == nil && strings.TrimSpace(state) == "MERGED" {
			_, err := q.Store.PutFact(f.Repo, f.Number, f.Head, squashedFact, squashMerged, q.Now())
			return err
		}
		outcome := strings.TrimSpace(merged.Stderr + merged.Stdout)
		q.tell(ctx, f, "blocker", "land:"+number+":"+f.Head, fmt.Sprintf("SQUASH-FAILED #%d: %s refused the approved, green incident fix at %s: %s", f.Number, strings.Join(argv[:3], " "), shortHead(f.Head), outcome))
		_, err := q.Store.PutFact(f.Repo, f.Number, f.Head, squashedFact, outcome, q.Now())
		return err
	}
	if _, err := q.Store.PutFact(f.Repo, f.Number, f.Head, squashedFact, squashMerged, q.Now()); err != nil {
		return err
	}
	route, err := watch.Desks(q.Store)
	if err != nil {
		return err
	}
	return postLanded(ctx, q.Store, q.Drive, q.Trunk, f, route)
}

func (q *Squash) tell(ctx context.Context, f machine.Facts, kind, id, text string) {
	if err := postToLane(ctx, q.Store, q.Drive, f, kind, id, text); err != nil {
		q.Log.Warn("the incident squash went unposted", "pr", f.Number, "err", err)
	}
}

func (q *Squash) rulings(ctx context.Context, f machine.Facts) (gates.Verdict, error) {
	out, err := checkPR(ctx, q.Runner, q.Rulings, q.Checkout, q.Drive, f, "--incident")
	if err != nil {
		return gates.Verdict{}, err
	}
	var refusals []string
	for _, line := range strings.Split(out, "\n") {
		if cause, _, _ := strings.Cut(line, " "); slices.Contains(incidentChecks, cause) {
			refusals = append(refusals, line)
		}
	}
	if len(refusals) == 0 {
		return gates.Verdict{}, nil
	}
	return gates.Verdict{Cause: "rulings", Refusal: fmt.Sprintf("#%d %s: RULINGS-REFUSED `rulings.py check-pr --incident` names these on the approved, green incident fix, and it does not merge until they clear (45200f2): %s", f.Number, shortHead(f.Head), strings.Join(refusals, "; "))}, nil
}

func (q *Squash) rebase(ctx context.Context, f machine.Facts, rebased bool) (bool, error) {
	behind, err := q.CCX.GH(ctx, ccx.MaxAgeFresh, "api", "repos/"+f.Repo+"/compare/"+q.Trunk+"..."+f.Head, "--jq", ".behind_by")
	if err == nil && strings.TrimSpace(behind) == "0" {
		return false, nil
	}
	if rebased {
		touches, err := q.trunkTouches(ctx, f.Head)
		if err != nil || !touches {
			return false, err
		}
	}
	out, err := q.Restack(ctx, f.Number)
	if err != nil {
		return false, err
	}
	for line := range strings.SplitSeq(out, "\n") {
		if fields := strings.Fields(line); len(fields) == 4 && fields[0] == movedLine {
			_, err := q.Store.PutFact(f.Repo, f.Number, fields[3], rebasedFact, f.Head, q.Now())
			return true, err
		}
	}
	return false, nil
}

func (q *Squash) trunkTouches(ctx context.Context, head string) (bool, error) {
	git := func(limit time.Duration, args ...string) (string, error) {
		result, err := q.Runner.Run(ctx, shell.Command{Argv: append([]string{"git", "-C", q.Checkout}, args...), Timeout: limit})
		return strings.TrimSpace(result.Stdout), err
	}
	listed, err := git(gitLimit, "ls-remote", "--heads", "origin", "refs/heads/"+q.Trunk)
	if err != nil {
		return false, err
	}
	tip, _, _ := strings.Cut(listed, "\t")
	if _, err := git(trunkReadLimit, "fetch", "-q", "--no-tags", "origin", tip, head, "--no-write-fetch-head"); err != nil {
		return false, err
	}
	base, err := git(gitLimit, "merge-base", tip, head)
	if err != nil {
		return false, err
	}
	landed, err := git(gitLimit, "diff", "--name-only", base, tip)
	if err != nil {
		return false, err
	}
	changed, err := git(gitLimit, "diff", "--name-only", base, head)
	if err != nil {
		return false, err
	}
	files := strings.Fields(changed)
	return slices.ContainsFunc(strings.Fields(landed), func(file string) bool { return slices.Contains(files, file) }), nil
}
