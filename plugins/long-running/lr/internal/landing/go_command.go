package landing

import (
	"context"
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
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	kindGo   = "go"
	openPR   = "OPEN"
	mergedPR = "MERGED"
	filesJQ  = ".[] | [.filename, .patch] | @json"
	goReader = "desk-runner"
)

type goFlags struct {
	lane, head, owner string
	before            int64
	sweep             bool
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
		if _, err := gate.one(ctx, number, head, flags); err != nil {
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

func (g goGate) one(ctx context.Context, number int, head string, flags goFlags) (refused bool, err error) {
	a, cfg, s, inputs, stdout := g.a, g.cfg, g.s, g.inputs, g.stdout
	records, err := client.Tail(ctx, client.Query{Drive: cfg.Drive, PR: fmt.Sprintf("%s#%d", cfg.Repo, number)})
	if err != nil {
		return false, err
	}
	if flags.before != 0 {
		records = slices.DeleteFunc(records, func(record client.Record) bool { return record.Seq >= flags.before })
	}
	lifts, err := bus.HoldLifts(records, func(seqs []int64) ([]client.Record, error) { return client.Among(ctx, cfg.Drive, seqs) })
	if err != nil {
		fmt.Fprintf(stdout, "WARNING #%d: could not read what its holds resolve, so each counts as a lift: %v\n", number, err)
	}
	claim := gates.Claim{Repo: cfg.Repo, PR: number, Head: head}
	read := gates.Read{Lifts: lifts, Records: records, GoLanes: inputs.GoLanes, Exempt: inputs.Exempt, TapRules: inputs.TapRules, Cards: inputs.Cards}
	port, claimed := gates.PortAt(claim, records)
	if claimed {
		read.PortSourceMerged, err = sourceLanded(ctx, a, cfg, port.Source)
		if err != nil {
			return false, err
		}
	}
	standing := claimed && !port.Interacts || gates.FlakeAt(claim, records)
	if flags.sweep {
		if _, refusals := gates.ExemptGo(claim, read); len(refusals) > 0 {
			fmt.Fprintf(stdout, "REFUSED #%d at %s: %s\n", number, head[:7], strings.Join(refusals, "; "))
			if err := sayGoRefused(ctx, cfg, claim, flags.owner, standing, records, refusals); err != nil {
				return true, err
			}
			return true, noteStandingRefused(s, claim, standing, refusals, a.Now())
		}
	}
	files, err := a.CCX.GH(ctx, ccx.MaxAgeFresh, "api", "--paginate", fmt.Sprintf("repos/%s/pulls/%d/files", cfg.Repo, number), "--jq", filesJQ)
	if err != nil {
		return false, err
	}
	read.Files, read.Patches, err = changedFiles(files)
	if err != nil {
		return false, err
	}
	if claimed && flags.before == 0 {
		if err := notePortRead(s, claim, read, a.Now()); err != nil {
			return false, err
		}
	}
	read.Body, err = a.CCX.GH(ctx, ccx.MaxAgeFresh, "pr", "view", strconv.Itoa(number), "--repo", cfg.Repo, "--json", "body", "--jq", ".body")
	if err != nil {
		fmt.Fprintf(stdout, "WARNING #%d: could not read its body, so no wait it names refuses it: %v\n", number, err)
	}
	text, refusals := gates.ExemptGo(claim, read)
	if err := noteStandingRefused(s, claim, standing && flags.before == 0 && (flags.sweep || len(refusals) == 0), refusals, a.Now()); err != nil {
		return false, err
	}
	if flags.before == 0 {
		row, found, err := s.ReviewRow(cfg.Repo, number)
		if err != nil {
			return false, err
		}
		if unread := gates.RowRead(row, found, head); unread != "" {
			refusals = append(refusals, unread)
		}
	}
	if len(refusals) > 0 {
		fmt.Fprintf(stdout, "REFUSED #%d at %s: %s\n", number, head[:7], strings.Join(refusals, "; "))
		if flags.sweep {
			return true, sayGoRefused(ctx, cfg, claim, flags.owner, standing, records, refusals)
		}
		return true, nil
	}
	if flags.lane == "" {
		fmt.Fprintf(stdout, "GO #%d at %s (dry): %s\n", number, head[:7], text)
		return false, nil
	}
	posted, err := client.Post(ctx, client.Draft{Drive: cfg.Drive, Lane: flags.lane, Kind: kindGo, To: []string{goReader}, PRs: []string{strconv.Itoa(number)}, Text: text})
	if err != nil {
		return false, err
	}
	fmt.Fprintf(stdout, "GO #%d at %s: posted %s: %s\n", number, head[:7], posted.Name(), text)
	if !bus.GoCounts(flags.lane, inputs.GoLanes) {
		fmt.Fprintf(stdout, "NOTE #%d: the gate admits it, but %s is not in go_lanes, so this record does not move it; the supervisor's sweep posts the go that counts, or a go.refused record saying why not\n", number, flags.lane)
	}
	return false, nil
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
	for _, record := range gos {
		to = append(to, record.Lane)
	}
	slices.Sort(to)
	to = slices.DeleteFunc(slices.Compact(to), func(lane string) bool { return lane == "" || lane == bus.SweepLane })
	return bus.Bus{Drive: cfg.Drive, Lane: bus.SweepLane}.PostGoRefused(ctx, claim.Repo, claim.PR, claim.Head, to, strings.Join(refusals, "; "))
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
	if _, err := a.Runner.Run(ctx, shell.Command{Argv: []string{"git", "-C", cfg.Checkout, "fetch", "-q", "origin", cfg.Trunk}, Timeout: gitLimit}); err != nil {
		return false, err
	}
	named := fmt.Sprintf("(#%d)", source)
	log, err := a.Runner.Run(ctx, shell.Command{Argv: []string{"git", "-C", cfg.Checkout, "log", "origin/" + cfg.Trunk, "--format=%s", "--fixed-strings", "--grep=" + named}, Timeout: gitLimit})
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

const goRegate = 10 * time.Minute

func goReady(f machine.Facts) bool {
	return f.Green() && f.Approved && f.Graded() && !f.Exempt && f.Hold == "" && !f.Draft && !f.Closed && !f.Merged && f.Tap == ""
}

func goProbe(number int) string {
	return fmt.Sprintf("go-refused:%d", number)
}

func SweepGos(ctx context.Context, a *app.App, cfg config.Config, s *store.Store, snapshot []machine.Facts, now time.Time, stdout io.Writer) error {
	probes, err := s.Probes()
	if err != nil {
		return err
	}
	ready := slices.DeleteFunc(slices.Clone(snapshot), func(f machine.Facts) bool {
		refused, seen := probes[goProbe(f.Number)]
		return !goReady(f) || seen && now.Sub(refused.Asked) < goRegate
	})
	if len(ready) == 0 {
		return nil
	}
	inputs, err := readGateInputs(ctx, cfg.Drive, now)
	if err != nil {
		return err
	}
	gate := goGate{a: a, cfg: cfg, s: s, inputs: inputs, stdout: stdout}
	for _, f := range ready {
		refused, err := gate.one(ctx, f.Number, f.Head, goFlags{lane: bus.SweepLane, owner: f.Lane, sweep: true})
		if err != nil {
			return err
		}
		mark := s.Unprobe(goProbe(f.Number))
		if refused {
			mark = s.Probe(goProbe(f.Number), now)
		}
		if mark != nil {
			return mark
		}
	}
	return nil
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
