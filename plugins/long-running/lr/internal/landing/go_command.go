package landing

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	kindGo      = "go"
	openPR      = "OPEN"
	mergedPR    = "MERGED"
	filesJQ     = ".[] | [.filename, .patch] | @json"
	goReader    = "desk-runner"
	goUnread    = "go-unread"
	sweepTip    = "refs/lr/sweep/"
	goProbe     = "go-refused:"
	sweepCursor = "go-sweep"
)

type goFlags struct {
	lane, head, owner, body string
	before                  int64
	sweep                   bool
}

func goCommand(a *app.App) *cobra.Command {
	var flags goFlags
	cmd := &cobra.Command{
		Use:   "go PR...",
		Short: "Gate an exempt go on each PR at its exact head and, with --lane, post the ones the gate admits; a refused PR prints every reason and posts nothing",
		Args:  cobra.MinimumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			numbers := make([]int, len(args))
			for i, arg := range args {
				number, err := strconv.Atoi(strings.TrimPrefix(arg, "#"))
				if err != nil {
					return err
				}
				numbers[i] = number
			}
			if (flags.before != 0) != (flags.head != "") || flags.before != 0 && (flags.lane != "" || len(numbers) != 1) {
				return errors.New("--before replays one PR at the --head it names and posts nothing, so it takes one PR, --head and no --lane")
			}
			return runGo(cmd.Context(), a, numbers, flags, cmd.OutOrStdout())
		},
	}
	cmd.Flags().StringVar(&flags.lane, "lane", "", "post each admitted go as this lane; without it the pass is dry")
	cmd.Flags().StringVar(&flags.head, "head", "", "the head a replayed go named, with --before")
	cmd.Flags().Int64Var(&flags.before, "before", 0, "replay one PR's past go: read only the records before this seq, at --head, posting nothing")
	return cmd
}

func runGo(ctx context.Context, a *app.App, numbers []int, flags goFlags, stdout io.Writer) error {
	cfg, err := a.Config()
	if err != nil {
		return err
	}
	inputs, err := readGateInputs(ctx, cfg.Drive, a.Now())
	if err != nil {
		return err
	}
	heads, err := currentHeads(ctx, a.CCXFrom(cfg.Checkout), cfg.Repo, numbers, flags.head)
	if err != nil {
		return err
	}
	s, err := store.Open(filepath.Join(filepath.Dir(a.ConfigPath), storeFile))
	if err != nil {
		return err
	}
	defer func() { _ = s.Close() }()
	gate := goGate{a: a, cfg: cfg, s: s, inputs: inputs, stdout: stdout}
	for _, number := range numbers {
		head, open := heads[number]
		if !open {
			fmt.Fprintf(stdout, "REFUSED #%d: it is not an open pull request\n", number)
			continue
		}
		if _, _, err := gate.one(ctx, number, head, flags); err != nil {
			return err
		}
	}
	return nil
}

type goGate struct {
	a      *app.App
	cfg    config.Config
	s      *store.Store
	inputs gateInputs
	stdout io.Writer
}

type headRead struct {
	claim   gates.Claim
	read    gates.Read
	records []client.Record
	port    gates.PortClaim
	claimed bool
	lapses  time.Time
}

func (g goGate) readHead(ctx context.Context, number int, head string, before int64) (headRead, error) {
	records, err := client.Tail(ctx, client.Query{Drive: g.cfg.Drive, PR: fmt.Sprintf("%s#%d", g.cfg.Repo, number)})
	if err != nil {
		return headRead{}, err
	}
	if before != 0 {
		records = slices.DeleteFunc(records, func(record client.Record) bool { return record.Seq >= before })
	}
	lifts, err := bus.HoldLifts(records, func(seqs []int64) ([]client.Record, error) { return client.Among(ctx, g.cfg.Drive, seqs) })
	if err != nil {
		return headRead{}, fmt.Errorf("read what its holds resolve: %w", err)
	}
	r := headRead{claim: gates.Claim{Repo: g.cfg.Repo, PR: number, Head: head}, records: records}
	r.read = gates.Read{Lifts: lifts, Records: records, GoLanes: g.inputs.GoLanes, Exempt: g.inputs.Exempt, TapRules: g.inputs.TapRules, Cards: g.inputs.Cards}
	r.port, r.claimed = gates.PortAt(r.claim, records)
	if r.claimed {
		r.read.PortSourceMerged, err = g.portLanded(ctx, r.claim, r.port.Source)
		if err != nil {
			return r, err
		}
	}
	if !r.claimed || r.read.PortSourceMerged {
		r.lapses = earliestExpiry(records)
	}
	return r, nil
}

func (g goGate) readDiff(ctx context.Context, number int, read *gates.Read) error {
	files, err := g.a.CCX.GH(ctx, ccx.MaxAgeFresh, "api", "--paginate", fmt.Sprintf("repos/%s/pulls/%d/files", g.cfg.Repo, number), "--jq", filesJQ)
	if err != nil {
		return err
	}
	read.Files, read.Patches, err = changedFiles(files)
	return err
}

func (g goGate) answer(ctx context.Context, f machine.Facts) ([]string, error) {
	r, err := g.readHead(ctx, f.Number, f.Head, 0)
	if err != nil {
		return nil, err
	}
	r.read.Body = f.Body
	if err := g.readDiff(ctx, f.Number, &r.read); err != nil {
		return nil, err
	}
	_, _, refusals := gates.ExemptGo(r.claim, r.read)
	unread, err := gates.RowReadAt(g.s, g.cfg.Repo, f.Number, f.Head)
	if err != nil {
		return nil, err
	}
	if unread != "" {
		refusals = append(refusals, unread)
	}
	if len(refusals) == 0 {
		return nil, nil
	}
	return append(refusals, uncountedGos(r.claim, r.records, g.inputs.GoLanes)...), nil
}

func uncountedGos(c gates.Claim, records []client.Record, goLanes []string) []string {
	var out []string
	for _, record := range records {
		if string(record.Kind) == kindGo && !countsAt(record, c, goLanes) {
			out = append(out, fmt.Sprintf("go %s by %s is not counted at this head", record.Name(), record.Lane))
		}
	}
	return out
}

func countsAt(record client.Record, c gates.Claim, goLanes []string) bool {
	return slices.ContainsFunc(bus.LegacyFacts(record, c.Repo, goLanes), func(fact bus.Fact) bool {
		return fact.Kind == "exempt" && fact.PR == c.PR && strings.HasPrefix(c.Head, fact.Head)
	})
}

func (g goGate) one(ctx context.Context, number int, head string, flags goFlags) (refused bool, lapses time.Time, err error) {
	a, cfg, s, inputs, stdout := g.a, g.cfg, g.s, g.inputs, g.stdout
	r, err := g.readHead(ctx, number, head, flags.before)
	if err != nil {
		return false, r.lapses, err
	}
	claim, read, records, claimed, port := r.claim, r.read, r.records, r.claimed, r.port
	lapses = r.lapses
	standing := claimed && !port.Interacts || gates.FlakeAt(claim, records)
	if flags.sweep {
		read.Body = flags.body
		if _, _, refusals := gates.ExemptGo(claim, read); len(refusals) > 0 {
			fmt.Fprintf(stdout, "REFUSED #%d at %s: %s\n", number, head[:7], strings.Join(refusals, "; "))
			if err := sayGoRefused(ctx, cfg, claim, flags.owner, standing, records, refusals); err != nil {
				return true, lapses, err
			}
			return true, lapses, noteStandingRefused(s, claim, standing, refusals, a.Now())
		}
	}
	if err := g.readDiff(ctx, number, &read); err != nil {
		return false, lapses, err
	}
	if claimed && flags.before == 0 {
		if err := notePortRead(s, claim, read, a.Now()); err != nil {
			return false, lapses, err
		}
	}
	if !flags.sweep {
		read.Body, err = a.CCX.GH(ctx, ccx.MaxAgeFresh, "pr", "view", strconv.Itoa(number), "--repo", cfg.Repo, "--json", "body", "--jq", ".body")
		if err != nil {
			fmt.Fprintf(stdout, "WARNING #%d: could not read its body, so no wait it names refuses it: %v\n", number, err)
		}
	}
	text, class, refusals := gates.ExemptGo(claim, read)
	if err := noteStandingRefused(s, claim, standing && flags.before == 0 && (flags.sweep || len(refusals) == 0), refusals, a.Now()); err != nil {
		return false, lapses, err
	}
	if flags.before == 0 {
		unread, err := gates.RowReadAt(s, cfg.Repo, number, head)
		if err != nil {
			return false, lapses, err
		}
		if unread != "" {
			refusals, lapses = append(refusals, unread), time.Time{}
		}
	}
	if len(refusals) > 0 {
		fmt.Fprintf(stdout, "REFUSED #%d at %s: %s\n", number, head[:7], strings.Join(refusals, "; "))
		if flags.sweep {
			return true, lapses, sayGoRefused(ctx, cfg, claim, flags.owner, standing, records, refusals)
		}
		return true, lapses, nil
	}
	if flags.lane == "" {
		fmt.Fprintf(stdout, "GO #%d at %s (dry): %s\n", number, head[:7], text)
		return false, time.Time{}, nil
	}
	posted, err := client.Post(ctx, client.Draft{Drive: cfg.Drive, Lane: flags.lane, Kind: kindGo, To: []string{goReader}, PRs: []string{strconv.Itoa(number)}, Attrs: map[string]string{bus.ExemptClassAttr: class}, Text: text})
	if err != nil {
		return false, time.Time{}, err
	}
	fmt.Fprintf(stdout, "GO #%d at %s: posted %s: %s\n", number, head[:7], posted.Name(), text)
	if !bus.GoCounts(flags.lane, inputs.GoLanes) {
		fmt.Fprintf(stdout, "NOTE #%d: the gate admits it, but %s is not in go_lanes, so this record does not move it; the supervisor's sweep posts the go that counts, or a go.refused record saying why not\n", number, flags.lane)
	}
	return false, time.Time{}, nil
}

func (g goGate) portLanded(ctx context.Context, claim gates.Claim, source int) (bool, error) {
	stored, err := g.s.Facts(claim.Repo, claim.PR, claim.Head)
	if err != nil || gates.PortSourceLanded(stored) {
		return err == nil, err
	}
	return sourceLanded(ctx, g.a, g.cfg, source)
}

func earliestExpiry(records []client.Record) time.Time {
	lapses := neverLapses
	for _, record := range records {
		if record.ExpiresAt != nil && record.ExpiresAt.Before(lapses) {
			lapses = *record.ExpiresAt
		}
	}
	return lapses
}

func changedFiles(lines string) ([]string, map[string]*string, error) {
	var files []string
	patches := map[string]*string{}
	for line := range strings.Lines(lines) {
		var entry [2]*string
		if err := json.Unmarshal([]byte(line), &entry); err != nil {
			return nil, nil, fmt.Errorf("changed file %q: %w", line, err)
		}
		files = append(files, *entry[0])
		patches[*entry[0]] = entry[1]
	}
	return files, patches, nil
}

func sayGoRefused(ctx context.Context, cfg config.Config, claim gates.Claim, owner string, standing bool, records []client.Record, refusals []string) error {
	gos := slices.DeleteFunc(slices.Clone(records), func(record client.Record) bool {
		return string(record.Kind) != kindGo || !bus.GoNamesHead(record, claim.PR, claim.Head)
	})
	if !standing && len(gos) == 0 {
		return nil
	}
	to := []string{owner}
	said := slices.Clone(refusals)
	for _, record := range gos {
		to = append(to, record.Lane)
		if bus.HandExempt(record) {
			said = append(said, fmt.Sprintf("go %s by %s names its exempt class by hand, and a class lands a PR only once the gate checks it at its head: until every reason above clears, this head lands on his tap from its row's card, and lr landing go %d prints what the gate reads", record.Name(), record.Lane, claim.PR))
		}
	}
	slices.Sort(to)
	to = slices.DeleteFunc(slices.Compact(to), func(lane string) bool { return lane == "" || lane == bus.SweepLane })
	return bus.Bus{Drive: cfg.Drive, Lane: bus.SweepLane}.PostGoRefused(ctx, claim.Repo, claim.PR, claim.Head, to, strings.Join(said, "; "))
}

func noteStandingRefused(s *store.Store, claim gates.Claim, standing bool, refusals []string, at time.Time) error {
	if !standing {
		return nil
	}
	return s.Update(func(t *store.Tx) error {
		return t.PutFact(claim.Repo, claim.PR, claim.Head, gates.StandingRefused, strings.Join(refusals, "; "), at)
	})
}

func sourceLanded(ctx context.Context, a *app.App, cfg config.Config, source int) (bool, error) {
	rows, err := a.CCXFrom(cfg.Checkout).PRStatus(ctx, cfg.Repo, []int{source})
	if err != nil {
		return false, err
	}
	if slices.ContainsFunc(rows, func(row ccx.PRStatus) bool { return row.State == mergedPR }) {
		return true, nil
	}
	tip := sweepTip + cfg.Trunk
	if _, err := a.Runner.Run(ctx, shell.Command{Argv: []string{"git", "-C", cfg.Checkout, "fetch", "-q", "--no-tags", "--no-write-fetch-head", "--refmap=", "origin", "+refs/heads/" + cfg.Trunk + ":" + tip}, Timeout: gitLimit}); err != nil {
		return false, err
	}
	named := fmt.Sprintf("(#%d)", source)
	log, err := a.Runner.Run(ctx, shell.Command{Argv: []string{"git", "-C", cfg.Checkout, "log", tip, "--format=%s", "--fixed-strings", "--grep=" + named}, Timeout: gitLimit})
	if err != nil {
		return false, err
	}
	return slices.ContainsFunc(strings.Split(log.Stdout, "\n"), func(subject string) bool { return strings.HasSuffix(subject, named) }), nil
}

func notePortRead(s *store.Store, claim gates.Claim, read gates.Read, at time.Time) error {
	return s.Update(func(t *store.Tx) error {
		for kind, value := range gates.PortEvidence(read.PortSourceMerged, read.Files) {
			if err := t.PutFact(claim.Repo, claim.PR, claim.Head, kind, value, at); err != nil {
				return err
			}
		}
		return nil
	})
}

var neverLapses = time.Date(9999, time.January, 1, 0, 0, 0, 0, time.UTC)

func goReady(f machine.Facts) bool {
	return f.Green() && f.Approved && !f.Exempt && f.Hold == "" && !f.Draft && !f.Closed && !f.Merged && f.Tap == ""
}

type refusedProbe struct {
	name   string
	print  string
	lapses time.Time
}

func SweepGos(ctx context.Context, a *app.App, cfg config.Config, s *store.Store, snapshot []machine.Facts, now time.Time, stdout io.Writer) ([]int, error) {
	if len(snapshot) == 0 {
		return nil, nil
	}
	ready := slices.DeleteFunc(slices.Clone(snapshot), func(f machine.Facts) bool { return !goReady(f) })
	inputs, err := readGateInputs(ctx, cfg.Drive, now)
	if err != nil {
		return nil, err
	}
	named, since, newest, err := namedSince(ctx, s, cfg, now)
	if err != nil {
		return nil, err
	}
	probed, err := refusedProbes(s)
	if err != nil {
		return nil, err
	}
	gate := goGate{a: a, cfg: cfg, s: s, inputs: inputs, stdout: stdout}
	var posted []int
	unchanged := 0
	for _, f := range ready {
		key, err := goPrint(f, inputs)
		if err != nil {
			return posted, err
		}
		if !named[f.Number] && slices.ContainsFunc(probed[f.Number], func(p refusedProbe) bool { return p.print == key && now.Before(p.lapses) }) {
			unchanged++
			continue
		}
		refused, lapses, err := gate.one(ctx, f.Number, f.Head, goFlags{lane: bus.SweepLane, owner: f.Lane, body: f.Body, sweep: true})
		if err != nil {
			if ctx.Err() != nil {
				return posted, err
			}
			if err := sayUnread(ctx, s, cfg.Drive, f, err, stdout); err != nil {
				return posted, err
			}
			continue
		}
		for _, stale := range probed[f.Number] {
			if err := s.Unprobe(stale.name); err != nil {
				return posted, err
			}
		}
		switch {
		case !refused:
			posted = append(posted, f.Number)
		case !lapses.IsZero():
			if err := s.Probe(fmt.Sprintf("%s%d:%s:%d", goProbe, f.Number, key, lapses.Unix()), now); err != nil {
				return posted, err
			}
		}
	}
	open := make(map[int]bool, len(snapshot))
	for _, f := range snapshot {
		open[f.Number] = true
	}
	for number, stale := range probed {
		if open[number] {
			continue
		}
		for _, p := range stale {
			if err := s.Unprobe(p.name); err != nil {
				return posted, err
			}
		}
	}
	if err := refuseUnready(ctx, cfg, snapshot, since, inputs.GoLanes); err != nil {
		return posted, err
	}
	fmt.Fprintf(stdout, "SWEPT %d ready heads: %d gated, %d unchanged since their refusal, %d posted\n", len(ready), len(ready)-unchanged, unchanged, len(posted))
	return posted, s.Advance(sweepCursor, newest)
}

func refuseUnready(ctx context.Context, cfg config.Config, snapshot []machine.Facts, since []client.Record, goLanes []string) error {
	for _, f := range snapshot {
		if goReady(f) || f.Exempt || f.Closed || f.Merged {
			continue
		}
		gos := slices.DeleteFunc(slices.Clone(since), func(record client.Record) bool {
			return string(record.Kind) != kindGo || bus.GoCounts(record.Lane, goLanes) && !bus.HandExempt(record) || !bus.GoNamesHead(record, f.Number, f.Head)
		})
		if len(gos) == 0 {
			continue
		}
		if err := sayGoRefused(ctx, cfg, gates.Claim{Repo: cfg.Repo, PR: f.Number, Head: f.Head}, f.Lane, false, gos, unready(f)); err != nil {
			return err
		}
	}
	return nil
}

func unready(f machine.Facts) []string {
	var why []string
	if !f.Green() {
		why = append(why, "CI is not green at this head")
	}
	if !f.Approved {
		why = append(why, "no approving review is on this head")
	}
	if f.Hold != "" {
		why = append(why, fmt.Sprintf("%s holds it (#%d)", f.Hold, f.HoldSeq))
	}
	if f.Draft {
		why = append(why, "it is a draft")
	}
	if f.Tap != "" {
		why = append(why, fmt.Sprintf("its tap reads %s, and a tap moves it instead of a go", f.Tap))
	}
	return why
}

func Exempted(snapshot []machine.Facts, posted []int) []machine.Facts {
	marked := slices.Clone(snapshot)
	for i := range marked {
		if slices.Contains(posted, marked[i].Number) {
			marked[i].Exempt, marked[i].Finder, marked[i].Fidelity = true, machine.FinderClear, machine.FidelityPass
		}
	}
	return marked
}

func goPrint(f machine.Facts, inputs gateInputs) (string, error) {
	read, err := json.Marshal([]any{f.Head, f.Base, f.Body, gates.OpenCards(f.Number, f.Body, inputs.Cards), inputs.GoLanes, inputs.Exempt, inputs.TapRules})
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(read)
	return hex.EncodeToString(sum[:8]), nil
}

func namedSince(ctx context.Context, s *store.Store, cfg config.Config, now time.Time) (map[int]bool, []client.Record, int64, error) {
	after, err := s.Cursor(sweepCursor)
	if err != nil {
		return nil, nil, 0, err
	}
	query := client.Query{Drive: cfg.Drive, After: after}
	if after == 0 {
		query.Since = now.Add(-inputsStale)
	}
	records, err := client.Tail(ctx, query)
	if err != nil {
		return nil, nil, 0, err
	}
	named := map[int]bool{}
	for _, record := range records {
		after = max(after, record.Seq)
		for _, pr := range record.Refs.PRs {
			if pr.Repo == "" || strings.EqualFold(pr.Repo, cfg.Repo) {
				named[pr.Number] = true
			}
		}
	}
	return named, records, after, nil
}

func refusedProbes(s *store.Store) (map[int][]refusedProbe, error) {
	probes, err := s.Probes()
	if err != nil {
		return nil, err
	}
	byPR := map[int][]refusedProbe{}
	for name := range probes {
		rest, ours := strings.CutPrefix(name, goProbe)
		if !ours {
			continue
		}
		parts := strings.Split(rest, ":")
		number, err := strconv.Atoi(parts[0])
		if err != nil {
			return nil, fmt.Errorf("probe %q: %w", name, err)
		}
		probe := refusedProbe{name: name}
		if len(parts) == 3 {
			lapses, err := strconv.ParseInt(parts[2], 10, 64)
			if err != nil {
				return nil, fmt.Errorf("probe %q: %w", name, err)
			}
			probe.print, probe.lapses = parts[1], time.Unix(lapses, 0)
		}
		byPR[number] = append(byPR[number], probe)
	}
	return byPR, nil
}

func sayUnread(ctx context.Context, s *store.Store, drive string, f machine.Facts, unread error, stdout io.Writer) error {
	fmt.Fprintf(stdout, "UNREAD #%d at %s: %v\n", f.Number, f.Head[:7], unread)
	holder, err := lanes.ChangeOwner(s, drive, f.Lane, f.Number)
	if err != nil {
		return err
	}
	_, err = client.Post(ctx, client.Draft{
		Drive: drive, Lane: bus.SweepLane, Kind: "note", To: []string{holder},
		ID:   fmt.Sprintf("%s:%d:%s", goUnread, f.Number, f.Head),
		PRs:  []string{pullRef(f)},
		Text: fmt.Sprintf("GO-UNREAD #%d at %s: the go sweep could not read it, so it posts no go for it this pass and reads it again next pass: %v", f.Number, f.Head[:7], unread),
	})
	return err
}

type landingConfig struct {
	ExemptGrades []string            `json:"exempt_grades"`
	GoClasses    []string            `json:"go_classes"`
	PortPhrase   string              `json:"port_phrase"`
	TapRules     map[string][]string `json:"tap_rules"`
}

func landingSection(path string) (landingConfig, error) {
	runner, err := config.LoadRunner(path)
	if err != nil {
		return landingConfig{}, err
	}
	var section landingConfig
	if err := json.Unmarshal(runner.Sections["landing"], &section); err != nil {
		return landingConfig{}, fmt.Errorf("%s: landing: %w", path, err)
	}
	section.ExemptGrades = gates.Grades(section.ExemptGrades, section.GoClasses, section.PortPhrase)
	return section, nil
}

func currentHeads(ctx context.Context, c ccx.Client, repo string, numbers []int, head string) (map[int]string, error) {
	if head != "" {
		return map[int]string{numbers[0]: strings.ToLower(head)}, nil
	}
	rows, err := c.PRStatus(ctx, repo, numbers)
	if err != nil {
		return nil, err
	}
	heads := make(map[int]string, len(rows))
	for _, row := range rows {
		if row.State == openPR && row.Head != "" {
			heads[row.Number] = row.Head
		}
	}
	return heads, nil
}
