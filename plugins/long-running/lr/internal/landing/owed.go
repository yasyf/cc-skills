package landing

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bkread"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	owedFact    = "release-owed"
	owedCursor  = "release-owed"
	owedClosed  = "closed"
	mergeFast   = "merge-fast"
	notLive     = "not live"
	releaseKind = "release"
	fixLiveKind = "fix-live"
)

var (
	releaseWith  = regexp.MustCompile(`(?m)^Release-With:[ \t]*(\S.*)$`)
	releaseNamed = regexp.MustCompile(`\breleases/(\d{4}-\d{2}-\d{2}/\d+)\b`)
	buildNumber  = regexp.MustCompile(`(\d+)/?$`)
)

type ReleaseOwed struct {
	Runner     shell.Runner
	CCX        ccx.Client
	Store      *store.Store
	Squash     *Squash
	Drive      string
	Repo       string
	Owner      string
	CLI        []string
	Token      bkread.Token
	Checkout   string
	Every      time.Duration
	Urgent     time.Duration
	Cut        time.Duration
	LiveClocks func() ([]string, error)
	Started    time.Time
	Now        func() time.Time
	Log        *slog.Logger

	done   map[int]bool
	holder string
}

type owedRelease struct {
	SHA        string           `json:"sha"`
	Components []string         `json:"components"`
	Landed     time.Time        `json:"landed"`
	Incident   bool             `json:"incident"`
	Posted     map[string]int64 `json:"posted"`
	Held       map[string]int64 `json:"held,omitempty"`
	Pressed    time.Time        `json:"pressed"`
	Answered   int64            `json:"answered,omitempty"`
	Cut        bool             `json:"cut,omitempty"`
	Settled    int64            `json:"settled,omitempty"`
	Builds     []string         `json:"builds,omitempty"`
	Targets    []string         `json:"targets,omitempty"`
	Release    string           `json:"release,omitempty"`
}

type owedLanding struct {
	pr   store.PR
	owed *owedRelease
}

func NewReleaseOwed(a *app.App, s *store.Store, cfg config.Config, runnerPath string, squash *Squash, liveClocks func() ([]string, error)) (*ReleaseOwed, error) {
	runner, err := config.LoadRunner(runnerPath)
	if err != nil {
		return nil, err
	}
	var landing struct {
		Owner        string   `json:"release_owner"`
		CLI          string   `json:"release_cli"`
		TokenCommand []string `json:"ci_token_command"`
	}
	if err := json.Unmarshal(runner.Sections["landing"], &landing); err != nil {
		return nil, fmt.Errorf("%s: landing: %w", runnerPath, err)
	}
	return &ReleaseOwed{
		Runner: a.Runner, CCX: a.CCX, Store: s, Squash: squash, Drive: cfg.Drive, Repo: cfg.Repo, Owner: landing.Owner, CLI: strings.Fields(landing.CLI), Token: bkread.Token{Command: landing.TokenCommand}, Checkout: cfg.Checkout,
		Every:      time.Duration(runner.Deadlines.ReleaseStartMinutes) * time.Minute,
		Urgent:     time.Duration(runner.Deadlines.IncidentReleaseMinutes) * time.Minute,
		Cut:        time.Duration(runner.Deadlines.CutMinutes) * time.Minute,
		LiveClocks: liveClocks, Started: a.Now(), Now: a.Now, Log: a.Log,
	}, nil
}

func (r *ReleaseOwed) Pass(ctx context.Context) error {
	start, err := r.Store.Cursor(owedCursor)
	if err != nil {
		return err
	}
	if start == 0 {
		return r.Store.Advance(owedCursor, r.Now().Unix())
	}
	open, err := r.landings(ctx, time.Unix(start, 0))
	if err != nil || len(open) == 0 {
		return err
	}
	since := open[0].owed.Landed
	for _, landing := range open {
		if landing.owed.Landed.Before(since) {
			since = landing.owed.Landed
		}
	}
	records, err := client.Tail(ctx, client.Query{Drive: r.Drive, Since: since})
	if err != nil {
		return err
	}
	clocks, err := r.LiveClocks()
	if err != nil {
		return err
	}
	if r.holder, err = lanes.Holder(r.Store, r.Drive, r.Owner); err != nil {
		return err
	}
	every := r.Every
	if len(clocks) > 0 {
		every = r.Urgent
	}
	for _, landing := range open {
		if err := r.tend(ctx, landing, records, every); err != nil {
			return err
		}
	}
	return nil
}

func (r *ReleaseOwed) landings(ctx context.Context, start time.Time) ([]owedLanding, error) {
	prs, err := r.Store.PRs(r.Repo)
	if err != nil {
		return nil, err
	}
	if r.done == nil {
		r.done = map[int]bool{}
	}
	var open []owedLanding
	for _, pr := range prs {
		if pr.State != machine.StateLanded || r.done[pr.Number] {
			continue
		}
		facts, err := r.Store.Facts(r.Repo, pr.Number, pr.Head)
		if err != nil {
			return nil, err
		}
		value := facts[owedFact]
		if value == "" && pr.ReadAt.Before(start) {
			r.done[pr.Number] = true
			continue
		}
		if value == "" {
			if value, err = r.judge(ctx, pr); err != nil {
				return nil, err
			}
		}
		if value == owedClosed {
			r.done[pr.Number] = true
			continue
		}
		var owed owedRelease
		if err := json.Unmarshal([]byte(value), &owed); err != nil {
			return nil, fmt.Errorf("the release #%d owes: %w", pr.Number, err)
		}
		open = append(open, owedLanding{pr, &owed})
	}
	return open, nil
}

func (r *ReleaseOwed) judge(ctx context.Context, pr store.PR) (string, error) {
	out, err := r.CCX.GH(ctx, ccx.MaxAgeFresh, "pr", "view", strconv.Itoa(pr.Number), "--repo", r.Repo, "--json", "body,labels,mergedAt,mergeCommit")
	if err != nil {
		return "", err
	}
	var view struct {
		Body   string `json:"body"`
		Labels []struct {
			Name string `json:"name"`
		} `json:"labels"`
		MergedAt    time.Time `json:"mergedAt"`
		MergeCommit struct {
			OID string `json:"oid"`
		} `json:"mergeCommit"`
	}
	if err := json.Unmarshal([]byte(out), &view); err != nil {
		return "", fmt.Errorf("the view of #%d: %w", pr.Number, err)
	}
	incident, err := r.incident(ctx, pr)
	if err != nil {
		return "", err
	}
	var components []string
	for _, line := range releaseWith.FindAllStringSubmatch(view.Body, -1) {
		for _, name := range strings.Fields(line[1]) {
			if !slices.Contains(components, name) {
				components = append(components, name)
			}
		}
	}
	fast := false
	for _, label := range view.Labels {
		fast = fast || label.Name == mergeFast
	}
	if !incident && (!fast || len(components) == 0) {
		return owedClosed, r.close(pr)
	}
	if view.MergedAt.Before(r.Started) {
		shipped, err := r.shipped(ctx, view.MergeCommit.OID, components)
		if err != nil {
			return "", err
		}
		if shipped {
			return owedClosed, r.close(pr)
		}
	}
	owed := &owedRelease{SHA: view.MergeCommit.OID, Components: components, Landed: view.MergedAt, Incident: incident, Posted: map[string]int64{}}
	value, err := json.Marshal(owed)
	if err != nil {
		return "", err
	}
	return string(value), r.save(pr, owed)
}

func (r *ReleaseOwed) incident(ctx context.Context, pr store.PR) (bool, error) {
	f := machine.Facts{Repo: pr.Repo, Number: pr.Number, Head: pr.Head, Base: pr.Base, Lane: pr.Lane}
	facts, err := r.Store.Facts(pr.Repo, pr.Number, pr.Head)
	if err != nil {
		return false, err
	}
	alerted, err := r.Squash.alerted(ctx, f, facts[squashedFact] == squashMerged)
	if err != nil || !alerted {
		return false, err
	}
	cleared, err := r.Squash.cleared(ctx, f)
	return !cleared, err
}

func (r *ReleaseOwed) shipped(ctx context.Context, sha string, components []string) (bool, error) {
	result, err := r.Token.Run(ctx, r.Runner, shell.Command{Argv: append(slices.Clone(r.CLI), "live", "--json"), Dir: r.Checkout, Timeout: trunkReadLimit})
	if err != nil {
		r.Log.Warn("what is live did not read", "err", err)
		return false, nil
	}
	var running []struct {
		Target string `json:"target"`
		Commit string `json:"commit"`
	}
	if err := json.Unmarshal([]byte(result.Stdout), &running); err != nil {
		return false, fmt.Errorf("what is live: %w", err)
	}
	read := map[string]bool{}
	for _, deploy := range running {
		if (len(components) > 0 && !slices.Contains(components, deploy.Target)) || read[deploy.Commit] {
			continue
		}
		read[deploy.Commit] = true
		if _, err := r.Runner.Run(ctx, shell.Command{Argv: []string{"git", "-C", r.Checkout, "merge-base", "--is-ancestor", sha, deploy.Commit}, Timeout: gitLimit}); err == nil {
			return true, nil
		}
	}
	return false, nil
}

func (r *ReleaseOwed) tend(ctx context.Context, landing owedLanding, records []client.Record, every time.Duration) error {
	pr, owed := landing.pr, landing.owed
	if owed.Settled != 0 {
		return r.publish(ctx, landing)
	}
	if owed.Incident {
		cleared, err := r.Squash.cleared(ctx, machine.Facts{Repo: pr.Repo, Number: pr.Number})
		if err != nil {
			return err
		}
		if cleared {
			return r.close(pr)
		}
	}
	if settled := r.settledBy(owed, pr.Number, records); settled != nil {
		owed.Settled, owed.Builds, owed.Targets = settled.Seq, settled.Refs.Builds, settled.Refs.Targets
		if found := releaseNamed.FindStringSubmatch(settled.Text); found != nil {
			owed.Release = found[1]
		}
		if string(settled.Kind) == fixLiveKind || len(owed.Builds) == 0 {
			return r.close(pr)
		}
		if err := r.save(pr, owed); err != nil {
			return err
		}
		return r.publish(ctx, landing)
	}
	if owed.Incident {
		if err := r.held(ctx, landing); err != nil {
			return err
		}
	}
	if err := r.press(ctx, landing, records, every); err != nil {
		return err
	}
	return r.cutSilent(ctx, landing, records)
}

func (r *ReleaseOwed) settledBy(owed *owedRelease, number int, records []client.Record) *client.Record {
	closing := map[int64]bool{}
	for _, seq := range owed.Posted {
		closing[seq] = true
	}
	for _, seq := range owed.Held {
		closing[seq] = true
	}
	tests := []func(client.Record) bool{
		func(record client.Record) bool { return r.started(record, owed, number) },
		func(record client.Record) bool { return r.owned(record, owed, number) },
		func(record client.Record) bool { return acknowledges(record, owed, closing) },
	}
	for _, settles := range tests {
		for i := range records {
			if !records[i].At.Before(owed.Landed) && settles(records[i]) {
				return &records[i]
			}
		}
	}
	return nil
}

func (r *ReleaseOwed) started(record client.Record, owed *owedRelease, number int) bool {
	kind := string(record.Kind)
	if (kind != releaseKind && kind != fixLiveKind) || record.Lane == self || record.Lane == deskRunnerLane {
		return false
	}
	targets := overlap(record.Refs.Targets, owed.Components)
	if !owed.Incident || kind != releaseKind {
		return targets || r.names(record, number)
	}
	if record.Lane == rootLane && r.names(record, number) {
		return true
	}
	if len(record.Refs.Builds) == 0 {
		return false
	}
	return targets || (len(owed.Components) == 0 && len(record.Refs.Targets) > 0)
}

func (r *ReleaseOwed) owned(record client.Record, owed *owedRelease, number int) bool {
	if record.Lane == self || record.Lane == deskRunnerLane || record.Lane == rootLane || !slices.ContainsFunc(record.To, r.holds) {
		return false
	}
	named := slices.Clone(record.Refs.Targets)
	for _, stack := range record.Refs.Stacks {
		head, _, _ := strings.Cut(stack, "/")
		named = append(named, head)
	}
	return overlap(named, owed.Components) || r.names(record, number) || strings.Contains(record.Text, owed.SHA[:min(7, len(owed.SHA))])
}

func acknowledges(record client.Record, owed *owedRelease, closing map[int64]bool) bool {
	if record.Lane != rootLane {
		return false
	}
	if closing[record.Resolves] || (!owed.Incident && closing[record.Re]) {
		return true
	}
	return slices.ContainsFunc(record.AlsoResolves, func(seq int64) bool { return closing[seq] })
}

func (r *ReleaseOwed) names(record client.Record, number int) bool {
	for _, pr := range record.Refs.PRs {
		if pr.Number == number && (pr.Repo == "" || strings.EqualFold(pr.Repo, r.Repo)) {
			return true
		}
	}
	return false
}

func (r *ReleaseOwed) holds(lane string) bool {
	lane = lanes.Canonical(r.Drive, lane)
	return lane == r.holder || lanes.Stem(lane) == lanes.Stem(lanes.Canonical(r.Drive, r.Owner))
}

func (r *ReleaseOwed) answer(owed *owedRelease, number int, records []client.Record) int64 {
	nags := map[int64]bool{}
	for _, seq := range owed.Posted {
		nags[seq] = true
	}
	for _, record := range records {
		replies := nags[record.Re] || nags[record.Resolves] || slices.ContainsFunc(record.AlsoResolves, func(seq int64) bool { return nags[seq] })
		if r.holds(record.Lane) && !record.At.Before(owed.Landed) && (replies || r.names(record, number)) {
			return record.Seq
		}
	}
	return 0
}

func overlap(named, wanted []string) bool {
	return slices.ContainsFunc(named, func(name string) bool { return slices.Contains(wanted, name) })
}

func (r *ReleaseOwed) press(ctx context.Context, landing owedLanding, records []client.Record, every time.Duration) error {
	pr, owed := landing.pr, landing.owed
	if owed.Answered != 0 {
		return nil
	}
	if owed.Answered = r.answer(owed, pr.Number, records); owed.Answered != 0 {
		return r.save(pr, owed)
	}
	whole := int(every.Minutes())
	minutes := int(r.Now().Sub(owed.Landed).Minutes())
	step := "0"
	if minutes >= whole {
		step = fmt.Sprintf("%d@%dm", minutes/whole, whole)
		if every == r.Every {
			step = strconv.Itoa(minutes / whole)
		}
	}
	if _, posted := owed.Posted[step]; posted {
		return nil
	}
	number := strconv.Itoa(pr.Number)
	kind, text := r.pressWords(pr.Number, owed, minutes, whole)
	running, err := lanes.Running(r.Store, r.Drive, r.holder)
	if err != nil {
		return err
	}
	if running == rootLane {
		text = "UNOWNED, " + r.holder + " is not running: " + text
	}
	posted, err := client.Post(ctx, client.Draft{Drive: r.Drive, Lane: self, Kind: kind, To: slices.Compact([]string{running, rootLane}), ID: "release:" + number + ":" + step, Topic: number, PRs: []string{r.Repo + "#" + number}, Text: text})
	if err != nil {
		return err
	}
	owed.Posted[step] = posted.Seq
	if owed.Pressed.IsZero() {
		owed.Pressed = r.Now()
	}
	return r.save(pr, owed)
}

func (r *ReleaseOwed) pressWords(number int, owed *owedRelease, minutes, every int) (kind, text string) {
	named := strings.Join(owed.Components, " ")
	how := "with " + mergeFast
	if owed.Incident {
		how = "as an incident fix"
	}
	start, targets := "Start the release its squash needs now", "targets a component"
	if named != "" {
		how += " and Release-With: " + named
		start, targets = "Start the "+named+" release now", "targets "+named
	}
	sha := owed.SHA[:min(10, len(owed.SHA))]
	if len(owed.Posted) > 0 {
		settles := fmt.Sprintf("post that record with --pr %d or --target <component>; your or the coordinator's reply to this record with --re also stops the blocker", number)
		if owed.Incident {
			settles = "post `release --build <url> --target <component>`; your reply to this record with --re also stops the blocker"
		}
		return "blocker", fmt.Sprintf("#%d %s: landed %d minutes ago %s, and no cci `release` record since %s or names #%d. %s and %s, which repeats every %d minutes.", number, sha, minutes, how, targets, number, start, settles, every)
	}
	build := ""
	if owed.Incident {
		build = " --build <url>"
	}
	return releaseKind, fmt.Sprintf("#%d landed as %s %s. %s, then run `cci post --drive %s --lane <lane> --kind release --pr %d --target <component>%s --text <build>`. With no such record %d minutes after the landing, the supervisor posts a blocker to you and the coordinator every %d minutes.", number, sha, how, start, r.Drive, number, build, every, every)
}

func (r *ReleaseOwed) held(ctx context.Context, landing owedLanding) error {
	pr, owed := landing.pr, landing.owed
	holds, err := client.Open(ctx, r.Drive, "hold")
	if err != nil {
		return err
	}
	for _, hold := range holds {
		key := strconv.FormatInt(hold.Seq, 10)
		_, told := owed.Held[key]
		if told || hold.Lane != rootLane || hold.At.Before(owed.Landed) || (!r.names(hold, pr.Number) && !slices.ContainsFunc(hold.To, r.holds)) {
			continue
		}
		posted, err := client.Post(ctx, client.Draft{
			Drive: r.Drive, Lane: self, Kind: "blocker", To: []string{rootLane}, ID: fmt.Sprintf("release-held:%d:%s", pr.Number, key), PRs: []string{fmt.Sprintf("%s#%d", r.Repo, pr.Number)},
			Text: fmt.Sprintf("RELEASE-HELD-AFTER-LANDING #%d: landed %s at %s; its cut is owed now, the other PR gets its own cut when it lands (coordinator #%d)", pr.Number, owed.SHA[:min(10, len(owed.SHA))], owed.Landed.In(pacificZone()).Format(time.Kitchen), hold.Seq),
		})
		if err != nil {
			return err
		}
		if owed.Held == nil {
			owed.Held = map[string]int64{}
		}
		owed.Held[key] = posted.Seq
		if err := r.save(pr, owed); err != nil {
			return err
		}
	}
	return nil
}

func (r *ReleaseOwed) cutSilent(ctx context.Context, landing owedLanding, records []client.Record) error {
	pr, owed := landing.pr, landing.owed
	if len(owed.Components) == 0 || owed.Cut || owed.Pressed.IsZero() || r.Now().Sub(owed.Pressed) < r.Cut {
		return nil
	}
	owed.Cut = true
	answered := slices.ContainsFunc(records, func(record client.Record) bool { return r.cutAnswer(record, owed, pr.Number) })
	if !answered {
		component := owed.Components[0]
		hand := fmt.Sprintf("%s start --manual %s --env <env> --commit %s --emergency", strings.Join(r.CLI, " "), component, owed.SHA)
		number := strconv.Itoa(pr.Number)
		text := fmt.Sprintf("CUT-SILENT %s %s: no CUT or refusal %d min after the release record for #%s; hand path: %s", r.holder, component, int(r.Now().Sub(owed.Pressed).Minutes()), number, hand)
		if _, err := client.Post(ctx, client.Draft{Drive: r.Drive, Lane: self, Kind: "blocker", To: []string{rootLane}, ID: "cut:release:" + number, PRs: []string{r.Repo + "#" + number}, Text: text}); err != nil {
			return err
		}
	}
	return r.save(pr, owed)
}

func (r *ReleaseOwed) cutAnswer(record client.Record, owed *owedRelease, number int) bool {
	kind := string(record.Kind)
	answers := kind == "refuse" || (kind == releaseKind && len(record.Refs.Builds) > 0)
	if !answers || record.At.Before(owed.Pressed) {
		return false
	}
	targets := overlap(record.Refs.Targets, owed.Components)
	if r.holds(record.Lane) {
		return len(record.Refs.Targets) == 0 || targets
	}
	return record.Lane != self && record.Lane != deskRunnerLane && (targets || r.names(record, number))
}

func (r *ReleaseOwed) publish(ctx context.Context, landing owedLanding) error {
	pr, owed := landing.pr, landing.owed
	components := owed.Components
	if len(components) == 0 {
		components = owed.Targets
	}
	if len(components) == 0 {
		return nil
	}
	build := owed.Builds[0]
	release := owed.Release
	if release == "" {
		found := buildNumber.FindStringSubmatch(build)
		if found == nil {
			return nil
		}
		release = found[1]
	}
	result, err := r.Token.Run(ctx, r.Runner, shell.Command{Argv: append(slices.Clone(r.CLI), "live", release, "--json"), Dir: r.Checkout, Timeout: trunkReadLimit})
	if err != nil {
		r.Log.Warn("a release's live state did not read", "release", release, "err", err)
		return nil
	}
	var live map[string]struct {
		Live string `json:"live"`
	}
	if err := json.Unmarshal([]byte(result.Stdout), &live); err != nil {
		return fmt.Errorf("the live state of %s: %w", release, err)
	}
	number := strconv.Itoa(pr.Number)
	argv := []string{"cci", "post", "--drive", r.Drive, "--lane", self, "--kind", fixLiveKind, "--to", rootLane, "--id", "release:" + number + ":live", "--topic", number, "--pr", r.Repo + "#" + number, "--build", build}
	versions := make([]string, 0, len(components))
	for _, component := range components {
		state, known := live[component]
		if !known || state.Live == "" || state.Live == notLive {
			return nil
		}
		versions = append(versions, component+" "+state.Live)
		argv = append(argv, "--target", component)
	}
	argv = append(argv, "--text", fmt.Sprintf("RELEASED %s (%s)", strings.Join(versions, ", "), build))
	if _, err := r.Runner.Run(ctx, shell.Command{Argv: argv, Timeout: gitLimit}); err != nil {
		return err
	}
	return r.close(pr)
}

func (r *ReleaseOwed) save(pr store.PR, owed *owedRelease) error {
	value, err := json.Marshal(owed)
	if err != nil {
		return err
	}
	_, err = r.Store.PutFact(pr.Repo, pr.Number, pr.Head, owedFact, string(value), r.Now())
	return err
}

func (r *ReleaseOwed) close(pr store.PR) error {
	if r.done == nil {
		r.done = map[int]bool{}
	}
	r.done[pr.Number] = true
	_, err := r.Store.PutFact(pr.Repo, pr.Number, pr.Head, owedFact, owedClosed, r.Now())
	return err
}
